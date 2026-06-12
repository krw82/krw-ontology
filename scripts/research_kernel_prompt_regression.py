#!/usr/bin/env python3
"""Run ResearchKernel regression over suggested company prompts.

This is a deterministic MCP-tool regression harness, not an agent E2E runner.
It parses the company prompt markdown used by the frontend, calls
`krw_ontology_query_context`, and records latency, routing, budget, and compact
research-state signals.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import statistics
import time
from typing import Any

from krw_ontology.config.paths import ONTOLOGY_GLOBAL_SPINE_PATH_ENV, ONTOLOGY_RELEASE_ROOT_ENV
from krw_ontology.mcp_server.tools import query_context_tool


HEADER_RE = re.compile(r"^##\s+(?P<ticker>[A-Z][A-Z0-9.\-]*)\s+-\s+(?P<label>.+?)\s*$")
QUESTION_RE = re.compile(r"^(?P<index>\d+)\.\s+(?P<question>.+?)\s*$")


@dataclass(frozen=True)
class PromptCase:
    case_id: str
    ticker: str
    company_name: str
    question_index: int
    question: str


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompts",
        default="/tmp/krw_company_suggested_prompts_by_ticker.md",
        help="Markdown prompt source.",
    )
    parser.add_argument("--release-root", default=None, help="v3 release root to read.")
    parser.add_argument("--global-spine-path", default=None, help="Explicit v3 global_spine.sqlite path.")
    parser.add_argument("--output-prefix", default=None, help="Output path prefix without extension.")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N parsed cases.")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N parsed cases.")
    parser.add_argument("--limit-results", type=int, default=10, help="query_context limit_results.")
    parser.add_argument("--stop-on-error", action="store_true", help="Abort after first failed case.")
    args = parser.parse_args()
    configure_runtime_env(release_root=args.release_root, global_spine_path=args.global_spine_path)

    prompt_path = Path(args.prompts)
    all_cases = parse_prompt_cases(prompt_path)
    selected = all_cases[args.offset :]
    if args.limit is not None:
        selected = selected[: args.limit]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_prefix = Path(args.output_prefix or f"/tmp/krw_research_kernel_prompt_regression_{timestamp}")
    jsonl_path = output_prefix.with_suffix(".jsonl")
    summary_path = output_prefix.with_suffix(".summary.json")
    md_path = output_prefix.with_suffix(".md")
    full_json_path = output_prefix.with_suffix(".json")

    records: list[dict[str, Any]] = []
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for ordinal, case in enumerate(selected, start=1):
            record = run_case(
                case,
                ordinal=ordinal,
                total=len(selected),
                limit_results=args.limit_results,
            )
            records.append(record)
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            if args.stop_on_error and record["status"] != "ok":
                break

    summary = summarize(records, total_available=len(all_cases), prompt_path=str(prompt_path))
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    full_json_path.write_text(
        json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    md_path.write_text(to_markdown(summary, records), encoding="utf-8")

    print(
        json.dumps(
            {
                "jsonl": str(jsonl_path),
                "summary": str(summary_path),
                "json": str(full_json_path),
                "markdown": str(md_path),
                "summary_data": summary,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def parse_prompt_cases(path: Path) -> list[PromptCase]:
    cases: list[PromptCase] = []
    ticker: str | None = None
    company_name: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = HEADER_RE.match(line)
        if header:
            ticker = header.group("ticker").strip()
            company_name = header.group("label").strip()
            continue
        question = QUESTION_RE.match(line)
        if question and ticker:
            question_index = int(question.group("index"))
            cases.append(
                PromptCase(
                    case_id=f"{ticker}-{question_index}",
                    ticker=ticker,
                    company_name=company_name or ticker,
                    question_index=question_index,
                    question=question.group("question").strip(),
                )
            )
    return cases


def configure_runtime_env(*, release_root: str | None, global_spine_path: str | None) -> None:
    if release_root:
        os.environ[ONTOLOGY_RELEASE_ROOT_ENV] = str(Path(release_root).expanduser().resolve())
    if global_spine_path:
        os.environ[ONTOLOGY_GLOBAL_SPINE_PATH_ENV] = str(Path(global_spine_path).expanduser().resolve())


def run_case(
    case: PromptCase,
    *,
    ordinal: int,
    total: int,
    limit_results: int,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "question": case.question,
        "ticker": case.ticker,
        "limit_results": limit_results,
    }

    started = time.perf_counter()
    status = "ok"
    error: str | None = None
    payload: dict[str, Any] = {}
    try:
        raw = query_context_tool(**kwargs)
        payload = json.loads(raw)
    except Exception as exc:  # pragma: no cover - regression should keep going
        status = "error"
        error = f"{type(exc).__name__}: {exc}"

    elapsed_sec = round(time.perf_counter() - started, 3)
    response_json = json.dumps(payload, ensure_ascii=False, default=str) if payload else ""
    kernel = payload.get("kernel") if isinstance(payload.get("kernel"), dict) else {}
    answerability = payload.get("answerability") if isinstance(payload.get("answerability"), dict) else {}
    research_pack = payload.get("research_pack") if isinstance(payload.get("research_pack"), dict) else {}
    diagnostics = payload.get("search_diagnostics") if isinstance(payload.get("search_diagnostics"), dict) else {}
    candidate_tickers = extract_candidate_tickers(payload)

    return {
        "ordinal": ordinal,
        "total": total,
        "case_id": case.case_id,
        "ticker": case.ticker,
        "company_name": case.company_name,
        "question_index": case.question_index,
        "question": case.question,
        "status": status,
        "error": error,
        "elapsed_sec": elapsed_sec,
        "response_chars": len(response_json),
        "research_status": payload.get("research_status"),
        "answer_mode": answerability.get("recommended_answer_mode") or kernel.get("answer_mode"),
        "answerability": {
            "direct_answerable": answerability.get("direct_answerable"),
            "related_context_available": answerability.get("related_context_available"),
            "negative_answer_supported": answerability.get("negative_answer_supported"),
            "recommended_answer_mode": answerability.get("recommended_answer_mode"),
        },
        "kernel": {
            "version": kernel.get("version"),
            "contract_version": kernel.get("contract_version"),
            "enabled": kernel.get("enabled"),
            "shadow": kernel.get("shadow"),
            "intent": kernel.get("intent"),
            "primary_context": kernel.get("primary_context"),
            "status": kernel.get("status"),
            "answer_mode": kernel.get("answer_mode"),
            "confidence": kernel.get("confidence"),
            "allowed_next_tools": kernel.get("allowed_next_tools"),
            "do_not_call": kernel.get("do_not_call"),
            "max_additional_tool_calls": kernel.get("max_additional_tool_calls"),
            "missing_parts": kernel.get("missing_parts"),
            "recommended_trace_count": kernel.get("recommended_trace_count"),
            "timing_ms": kernel.get("timing_ms"),
            "budget": kernel.get("budget"),
            "packs_present": kernel.get("packs_present"),
        },
        "candidate_tickers": candidate_tickers,
        "target_ticker_present": case.ticker in candidate_tickers if candidate_tickers else None,
        "non_target_candidate_tickers": sorted(ticker for ticker in candidate_tickers if ticker != case.ticker),
        "research_pack_keys": sorted(str(key) for key, value in research_pack.items() if value),
        "diagnostic_keys": sorted(str(key) for key in diagnostics.keys()),
        "slow_bucket": slow_bucket(elapsed_sec),
        "note": "raw_mcp_payload_not_user_final_answer",
    }


def extract_candidate_tickers(payload: dict[str, Any]) -> list[str]:
    tickers: set[str] = set()
    for key in ("ticker_candidates", "candidates"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    ticker = item.get("ticker")
                    if isinstance(ticker, str) and ticker:
                        tickers.add(ticker)
    research_pack = payload.get("research_pack")
    if isinstance(research_pack, dict):
        for value in research_pack.values():
            if isinstance(value, dict):
                ticker = value.get("ticker")
                if isinstance(ticker, str) and ticker:
                    tickers.add(ticker)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        ticker = item.get("ticker")
                        if isinstance(ticker, str) and ticker:
                            tickers.add(ticker)
    return sorted(tickers)


def slow_bucket(elapsed_sec: float) -> str:
    if elapsed_sec >= 30:
        return "prod_unusable_30s_plus"
    if elapsed_sec >= 20:
        return "sync_ux_bad_20s_plus"
    if elapsed_sec >= 15:
        return "warning_15s_plus"
    if elapsed_sec >= 8:
        return "watch_8s_plus"
    return "ok_under_8s"


def summarize(records: list[dict[str, Any]], *, total_available: int, prompt_path: str) -> dict[str, Any]:
    elapsed = [float(record["elapsed_sec"]) for record in records]
    ok_records = [record for record in records if record["status"] == "ok"]
    intent_counts = Counter((record.get("kernel") or {}).get("intent") or "missing" for record in records)
    context_counts = Counter((record.get("kernel") or {}).get("primary_context") or "missing" for record in records)
    status_counts = Counter(record.get("research_status") or (record.get("kernel") or {}).get("status") or "missing" for record in records)
    bucket_counts = Counter(record.get("slow_bucket") or "missing" for record in records)
    question_index_counts = Counter(str(record.get("question_index")) for record in records)
    by_intent = summarize_groups(records, lambda record: (record.get("kernel") or {}).get("intent") or "missing")
    by_question_index = summarize_groups(records, lambda record: str(record.get("question_index")))
    return {
        "prompt_path": prompt_path,
        "total_available_cases": total_available,
        "case_count": len(records),
        "ok_count": len(ok_records),
        "error_count": len(records) - len(ok_records),
        "avg_elapsed_sec": round(sum(elapsed) / len(elapsed), 3) if elapsed else 0,
        "median_elapsed_sec": round(statistics.median(elapsed), 3) if elapsed else 0,
        "p90_elapsed_sec": percentile(elapsed, 90),
        "p95_elapsed_sec": percentile(elapsed, 95),
        "max_elapsed_sec": round(max(elapsed), 3) if elapsed else 0,
        "intent_counts": dict(sorted(intent_counts.items())),
        "context_counts": dict(sorted(context_counts.items())),
        "research_status_counts": dict(sorted(status_counts.items())),
        "slow_bucket_counts": dict(sorted(bucket_counts.items())),
        "question_index_counts": dict(sorted(question_index_counts.items())),
        "by_intent": by_intent,
        "by_question_index": by_question_index,
        "slowest_cases": [
            slim_record(record)
            for record in sorted(records, key=lambda item: float(item["elapsed_sec"]), reverse=True)[:20]
        ],
        "error_cases": [slim_record(record) for record in records if record["status"] != "ok"],
        "non_target_candidate_cases": [
            slim_record(record)
            for record in records
            if record.get("non_target_candidate_tickers")
        ][:50],
    }


def summarize_groups(records: list[dict[str, Any]], key_fn: Any) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for record in records:
        key = str(key_fn(record))
        groups[key].append(float(record["elapsed_sec"]))
        counts[key] += 1
    return {
        key: {
            "count": counts[key],
            "avg_elapsed_sec": round(sum(values) / len(values), 3) if values else 0,
            "p95_elapsed_sec": percentile(values, 95),
            "max_elapsed_sec": round(max(values), 3) if values else 0,
        }
        for key, values in sorted(groups.items())
    }


def percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return round(sorted_values[0], 3)
    rank = (len(sorted_values) - 1) * (pct / 100)
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    value = sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
    return round(value, 3)


def slim_record(record: dict[str, Any]) -> dict[str, Any]:
    kernel = record.get("kernel") or {}
    return {
        "case_id": record.get("case_id"),
        "ticker": record.get("ticker"),
        "question_index": record.get("question_index"),
        "elapsed_sec": record.get("elapsed_sec"),
        "status": record.get("status"),
        "error": record.get("error"),
        "intent": kernel.get("intent"),
        "primary_context": kernel.get("primary_context"),
        "research_status": record.get("research_status") or kernel.get("status"),
        "slow_bucket": record.get("slow_bucket"),
        "response_chars": record.get("response_chars"),
        "non_target_candidate_tickers": record.get("non_target_candidate_tickers"),
        "question": record.get("question"),
    }


def to_markdown(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    lines = [
        "# KRW ResearchKernel Prompt Regression",
        "",
        f"- prompt_path: {summary['prompt_path']}",
        f"- total_available_cases: {summary['total_available_cases']}",
        f"- cases: {summary['case_count']}",
        f"- ok: {summary['ok_count']}",
        f"- errors: {summary['error_count']}",
        f"- avg_elapsed_sec: {summary['avg_elapsed_sec']}",
        f"- median_elapsed_sec: {summary['median_elapsed_sec']}",
        f"- p90_elapsed_sec: {summary['p90_elapsed_sec']}",
        f"- p95_elapsed_sec: {summary['p95_elapsed_sec']}",
        f"- max_elapsed_sec: {summary['max_elapsed_sec']}",
        "",
        "## Counts",
        "",
        f"- intents: `{json.dumps(summary['intent_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- contexts: `{json.dumps(summary['context_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- research_status: `{json.dumps(summary['research_status_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- slow_buckets: `{json.dumps(summary['slow_bucket_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Slowest cases",
        "",
        "| case | qidx | sec | bucket | intent | context | status | response chars | question |",
        "|---|---:|---:|---|---|---|---|---:|---|",
    ]
    for record in summary["slowest_cases"][:20]:
        lines.append(
            "| {case_id} | {qidx} | {sec} | {bucket} | {intent} | {context} | {status} | {chars} | {question} |".format(
                case_id=record.get("case_id") or "",
                qidx=record.get("question_index") or "",
                sec=record.get("elapsed_sec") or "",
                bucket=record.get("slow_bucket") or "",
                intent=record.get("intent") or "",
                context=record.get("primary_context") or "",
                status=record.get("research_status") or "",
                chars=record.get("response_chars") or "",
                question=escape_table(str(record.get("question") or "")),
            )
        )
    lines.extend(
        [
            "",
            "## All cases",
            "",
            "| case | qidx | sec | intent | context | status | bucket |",
            "|---|---:|---:|---|---|---|---|",
        ]
    )
    for record in records:
        kernel = record.get("kernel") or {}
        lines.append(
            "| {case_id} | {qidx} | {sec} | {intent} | {context} | {status} | {bucket} |".format(
                case_id=record.get("case_id") or "",
                qidx=record.get("question_index") or "",
                sec=record.get("elapsed_sec") or "",
                intent=kernel.get("intent") or "",
                context=kernel.get("primary_context") or "",
                status=record.get("research_status") or kernel.get("status") or "",
                bucket=record.get("slow_bucket") or "",
            )
        )
    lines.append("")
    return "\n".join(lines)


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
