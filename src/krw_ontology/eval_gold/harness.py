"""Headless harness runner for the evidence-gold suite.

Runs every gold case through the real ``query_context_tool`` path (mirroring
``scripts/benchmark_mcp_candidate.py``), adapts the resulting
``ResearchState`` into the dict-shaped view the grader consumes, and produces
a JSON-serializable report. Baseline gating (``compare_to_baseline``) and
markdown rendering (``render_report_markdown``) live here so the CLI wrapper
stays thin and testable.

Determinism: the only timing-derived value in a report is ``elapsed_s`` inside
each case's ``detail`` block; pass/fail content never depends on timing.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from krw_ontology.eval_gold.grader import CaseResult, aggregate, grade_case
from krw_ontology.eval_gold.schema import EvidenceGold, load_evidence_gold
from krw_ontology.mcp_server import tools as mcp_tools

_UNIT_KEYS = (
    "ticker",
    "period",
    "document_type",
    "object_id",
    "evidence_id",
    "title",
    "summary",
)


def _query_case(case: Any) -> dict[str, Any]:
    """Execute one gold case headlessly and build the grader-shaped state.

    Mirrors ``scripts/benchmark_mcp_candidate.py:_run_case``: reset the MCP
    process-local caches, call ``query_context_tool`` with only
    ``search_plan`` (the runtime reads everything else from ``KRW_*`` env),
    and dump a pydantic result. The adapter then trims ``evidence_units`` to
    the fields the grader grades on so heavyweight payload blocks never reach
    grading or the report.
    """
    mcp_tools.reset_mcp_runtime_caches()
    result = mcp_tools.query_context_tool(search_plan=dict(case.search_plan))
    if hasattr(result, "model_dump"):
        state: dict[str, Any] = result.model_dump(mode="json")
    else:
        state = dict(result)
    state["evidence_units"] = [
        {key: unit.get(key) for key in _UNIT_KEYS}
        for unit in state.get("evidence_units") or []
    ]
    return state


def run_harness(gold_path: Path | str, *, label: str) -> dict[str, Any]:
    """Run every case in a gold file and return the full report dict."""
    gold: EvidenceGold = load_evidence_gold(Path(gold_path))
    case_results: list[CaseResult] = []
    per_case: dict[str, Any] = {}
    for case in gold.cases:
        started = time.perf_counter()
        state = _query_case(case)
        result = grade_case(case, state)
        result.detail["elapsed_s"] = round(time.perf_counter() - started, 3)
        case_results.append(result)
        per_case[case.id] = {
            "strata": list(case.strata),
            "passed": result.passed,
            "recall": result.recall,
            "zero_hit": result.zero_hit,
            "not_disclosed_violation": result.not_disclosed_violation,
            "matched_object_ids": result.matched_object_ids,
            "detail": result.detail,
        }
    summary = aggregate(case_results)
    return {
        "label": label,
        "gold_path": str(gold_path),
        "source_release": gold.source_release,
        "overall": summary["overall"],
        "strata": summary["strata"],
        "cases": per_case,
    }


def compare_to_baseline(
    report: dict[str, Any],
    baseline: dict[str, Any],
    *,
    tolerance: float = 0.0,
) -> tuple[bool, list[str]]:
    """Gate ``report`` against a previous report; regressions are drops only.

    A metric regresses when it falls more than ``tolerance`` below the
    baseline value. Strata present in the baseline but absent from the report
    compare against the baseline value itself, so removing a stratum is never
    silently tolerated unless its baseline metrics were already at the floor.
    """
    regressions: list[str] = []
    for key in ("pass_rate", "mean_recall"):
        a = baseline["overall"].get(key, 0.0)
        b = report["overall"].get(key, 0.0)
        if b < a - tolerance:
            regressions.append(f"overall.{key}: {b:.4f} < baseline {a:.4f}")
    for stratum, block in baseline.get("strata", {}).items():
        for key in ("pass_rate", "mean_recall"):
            a = block.get(key, 0.0)
            b = report.get("strata", {}).get(stratum, {}).get(key, 0.0)
            if b < a - tolerance:
                regressions.append(
                    f"strata[{stratum}].{key}: {b:.4f} < baseline {a:.4f}"
                )
    return (not regressions), regressions


def gate_decision(
    report: dict[str, Any],
    baseline: dict[str, Any],
    *,
    tolerance: float = 0.0,
    baseline_path: str = "",
) -> tuple[bool, list[str]]:
    """Decide the benchmark CLI's exit-2 gate for a finished report.

    Runs :func:`compare_to_baseline` and stamps the resulting
    ``baseline_gate`` block onto ``report`` (the block is part of the JSON
    artifact the CLI writes, so the gate decision itself stays auditable).
    Returns ``(ok, regressions)``; the CLI exits with code 2 exactly when
    ``ok`` is ``False`` and only then.
    """
    ok, regressions = compare_to_baseline(report, baseline, tolerance=tolerance)
    report["baseline_gate"] = {
        "baseline_path": baseline_path,
        "tolerance": tolerance,
        "ok": ok,
        "regressions": regressions,
    }
    return ok, regressions


def _fmt(value: Any) -> str:
    return f"{float(value):.4f}"


def render_report_markdown(report: dict[str, Any]) -> str:
    """Render the report as markdown: overall block, strata table, case table."""
    overall = report.get("overall") or {}
    release_id = (report.get("source_release") or {}).get("release_id", "")
    lines = [
        "# Evidence Gold Benchmark",
        "",
        f"- Label: {report.get('label', '')}",
        f"- Gold: {report.get('gold_path', '')}",
        f"- Source release: {release_id}",
        "",
        "## Overall",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| cases | {overall.get('cases', 0)} |",
        f"| pass_rate | {_fmt(overall.get('pass_rate', 0.0))} |",
        f"| mean_recall | {_fmt(overall.get('mean_recall', 0.0))} |",
        f"| zero_hit_rate | {_fmt(overall.get('zero_hit_rate', 0.0))} |",
        "",
        "## Strata",
        "",
        "| stratum | cases | pass_rate | mean_recall | zero_hit_rate |",
        "| --- | --- | --- | --- | --- |",
    ]
    for stratum, block in sorted((report.get("strata") or {}).items()):
        lines.append(
            f"| {stratum} | {block.get('cases', 0)} | "
            f"{_fmt(block.get('pass_rate', 0.0))} | "
            f"{_fmt(block.get('mean_recall', 0.0))} | "
            f"{_fmt(block.get('zero_hit_rate', 0.0))} |"
        )
    lines += [
        "",
        "## Cases",
        "",
        "| case | pass | recall | zero_hit |",
        "| --- | --- | --- | --- |",
    ]
    for case_id in sorted(report.get("cases") or {}):
        block = report["cases"][case_id]
        verdict = "PASS" if block.get("passed") else "FAIL"
        zero_hit = "yes" if block.get("zero_hit") else "no"
        lines.append(
            f"| {case_id} | {verdict} | {_fmt(block.get('recall', 0.0))} | {zero_hit} |"
        )
    return "\n".join(lines) + "\n"
