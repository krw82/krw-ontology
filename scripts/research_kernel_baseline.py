#!/usr/bin/env python3
"""Run a small ResearchKernel baseline/smoke pack.

This is a deterministic MCP-tool harness, not an agent E2E runner. It records
the v1 response state and the additive `kernel` envelope so later refactors can
compare routing, status, latency, and result size without changing the public
tool surface.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Callable

from krw_ontology.config.paths import ONTOLOGY_GLOBAL_SPINE_PATH_ENV, ONTOLOGY_RELEASE_ROOT_ENV
from krw_ontology.mcp_server.tools import compare_tool, query_context_tool, retrieve_tool


SMOKE_CASES: list[dict[str, Any]] = [
    {
        "case_id": "metric_aapl_iphone_services",
        "tool": "query_context",
        "kwargs": {
            "question": "AAPL의 iPhone과 Services 매출 비중은 2021~2025년에 어떻게 달라졌는지 비교해줘.",
            "ticker": "AAPL",
            "periods": ["CY2021", "CY2022", "CY2023", "CY2024", "CY2025"],
            "limit_results": 10,
        },
    },
    {
        "case_id": "risk_amzn_cyber_growth",
        "tool": "query_context",
        "kwargs": {
            "question": "AMZN의 사이버 보안 관련 비용 증가가 매출성장률을 갉아먹는지 점검해줘.",
            "ticker": "AMZN",
            "limit_results": 8,
        },
    },
    {
        "case_id": "direct_aapl_lng_henry_hub",
        "tool": "query_context",
        "kwargs": {
            "question": "AAPL이 LNG/Henry Hub 가격에 직접 노출되어 있나?",
            "ticker": "AAPL",
            "limit_results": 5,
        },
    },
    {
        "case_id": "compare_msft_aapl_ai_infra",
        "tool": "compare",
        "kwargs": {
            "tickers": ["MSFT", "AAPL"],
            "topic": "AI infrastructure capex margin pressure",
            "limit_per_ticker": 5,
        },
    },
    {
        "case_id": "retrieve_jpm_bac_nii",
        "tool": "compare",
        "kwargs": {
            "tickers": ["JPM", "BAC"],
            "topic": "net interest income deposit cost funding cost NII sensitivity",
            "limit_per_ticker": 5,
        },
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", default=None, help="v3 release root to read.")
    parser.add_argument("--global-spine-path", default=None, help="Explicit v3 global_spine.sqlite path.")
    parser.add_argument("--output-prefix", default=None, help="Output path prefix without extension.")
    parser.add_argument("--limit-cases", type=int, default=None, help="Run only the first N cases.")
    args = parser.parse_args()
    _configure_runtime_env(release_root=args.release_root, global_spine_path=args.global_spine_path)

    cases = SMOKE_CASES[: args.limit_cases] if args.limit_cases else SMOKE_CASES
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_prefix = Path(args.output_prefix or f"/tmp/krw_research_kernel_baseline_{timestamp}")

    records: list[dict[str, Any]] = []
    for case in cases:
        tool_name = str(case["tool"])
        kwargs = dict(case["kwargs"])
        started = time.perf_counter()
        status = "ok"
        error: str | None = None
        payload: dict[str, Any] = {}
        try:
            raw = _tool(tool_name)(**kwargs)
            payload = json.loads(raw)
        except Exception as exc:  # pragma: no cover - harness should keep going
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
        elapsed_sec = round(time.perf_counter() - started, 3)
        kernel = payload.get("kernel") if isinstance(payload.get("kernel"), dict) else {}
        research_pack = payload.get("research_pack") if isinstance(payload.get("research_pack"), dict) else {}
        records.append(
            {
                "case_id": case["case_id"],
                "tool": tool_name,
                "status": status,
                "error": error,
                "elapsed_sec": elapsed_sec,
                "response_chars": len(json.dumps(payload, ensure_ascii=False, default=str)) if payload else 0,
                "research_status": payload.get("research_status"),
                "recommended_answer_mode": (payload.get("answerability") or {}).get("recommended_answer_mode")
                if isinstance(payload.get("answerability"), dict)
                else None,
                "kernel": {
                    "version": kernel.get("version"),
                    "intent": kernel.get("intent"),
                    "primary_context": kernel.get("primary_context"),
                    "status": kernel.get("status"),
                    "answer_mode": kernel.get("answer_mode"),
                    "route_confidence": kernel.get("confidence"),
                    "do_not_call": kernel.get("do_not_call"),
                    "allowed_next_tools": kernel.get("allowed_next_tools"),
                    "timing_ms": kernel.get("timing_ms"),
                    "budget": kernel.get("budget"),
                },
                "research_pack_keys": sorted(str(key) for key, value in research_pack.items() if value),
            }
        )

    summary = _summary(records)
    json_path = output_prefix.with_suffix(".json")
    md_path = output_prefix.with_suffix(".md")
    json_path.write_text(json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(summary, records), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "summary": summary}, ensure_ascii=False, indent=2))


def _tool(name: str) -> Callable[..., str]:
    if name == "query_context":
        return query_context_tool
    if name == "retrieve":
        return retrieve_tool
    if name == "compare":
        return compare_tool
    raise ValueError(f"Unsupported tool: {name}")


def _configure_runtime_env(*, release_root: str | None, global_spine_path: str | None) -> None:
    if release_root:
        os.environ[ONTOLOGY_RELEASE_ROOT_ENV] = str(Path(release_root).expanduser().resolve())
    if global_spine_path:
        os.environ[ONTOLOGY_GLOBAL_SPINE_PATH_ENV] = str(Path(global_spine_path).expanduser().resolve())


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = [float(record["elapsed_sec"]) for record in records]
    return {
        "case_count": len(records),
        "ok_count": sum(1 for record in records if record["status"] == "ok"),
        "error_count": sum(1 for record in records if record["status"] != "ok"),
        "avg_elapsed_sec": round(sum(elapsed) / len(elapsed), 3) if elapsed else 0,
        "max_elapsed_sec": max(elapsed) if elapsed else 0,
        "intents": sorted({(record.get("kernel") or {}).get("intent") for record in records if (record.get("kernel") or {}).get("intent")}),
    }


def _markdown(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    lines = [
        "# KRW ResearchKernel Baseline",
        "",
        f"- cases: {summary['case_count']}",
        f"- ok: {summary['ok_count']}",
        f"- errors: {summary['error_count']}",
        f"- avg_elapsed_sec: {summary['avg_elapsed_sec']}",
        f"- max_elapsed_sec: {summary['max_elapsed_sec']}",
        "",
        "| case | tool | status | sec | intent | context | research_status | response chars |",
        "|---|---|---:|---:|---|---|---|---:|",
    ]
    for record in records:
        kernel = record.get("kernel") or {}
        lines.append(
            "| {case_id} | {tool} | {status} | {elapsed_sec} | {intent} | {context} | {research_status} | {chars} |".format(
                case_id=record["case_id"],
                tool=record["tool"],
                status=record["status"],
                elapsed_sec=record["elapsed_sec"],
                intent=kernel.get("intent") or "",
                context=kernel.get("primary_context") or "",
                research_status=record.get("research_status") or kernel.get("status") or "",
                chars=record["response_chars"],
            )
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
