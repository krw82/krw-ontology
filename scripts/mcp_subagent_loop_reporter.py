#!/usr/bin/env python3
"""Run KRW ontology MCP calls in repeated rounds with isolated worker process calls."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from krw_ontology.mcp_server.tools import (
    catalog_tool,
    index_context_tool,
    company_context_tool,
    query_context_tool,
    query_tool,
    retrieve_tool,
    quality_tool,
    compare_tool,
    plan_query_tool,
    trace_tool,
    chain_tool,
)

ROOT_DEFAULT = "~/krw-ontology-data"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z")


@dataclass
class QueryCase:
    name: str
    fn_name: str
    kwargs: dict[str, Any]


def _build_cases(root: str) -> list[QueryCase]:
    return [
        QueryCase(
            name="catalog_tool",
            fn_name="catalog_tool",
            kwargs={"root": root, "response_format": "json"},
        ),
        QueryCase(
            name="index_context_tool",
            fn_name="index_context_tool",
            kwargs={
                "root": root,
                "include_counts": True,
                "include_capabilities": True,
                "include_quality_summary": True,
                "response_format": "json",
            },
        ),
        QueryCase(
            name="company_context_tool",
            fn_name="company_context_tool",
            kwargs={"ticker": "AAPL", "root": root, "response_format": "json", "limit_topics": 40},
        ),
        QueryCase(
            name="query_context_tool",
            fn_name="query_context_tool",
            kwargs={
                "question": "AAPL AI demand growth and margin risk",
                "ticker": "AAPL",
                "root": root,
                "response_format": "json",
                "limit_results": 20,
                "limit_tickers": 20,
            },
        ),
        QueryCase(
            name="query_tool.focused_compact",
            fn_name="query_tool",
            kwargs={
                "topic": "cloud",
                "tickers": ["AAPL"],
                "document_types": ["10-K"],
                "object_types": ["ResearchClaim", "BusinessFactor"],
                "limit": 20,
                "root": root,
                "response_format": "json",
                "response_detail": "compact",
            },
        ),
        QueryCase(
            name="query_tool.broad_full",
            fn_name="query_tool",
            kwargs={
                "topic": "market risk margin growth",
                "tickers": ["AAPL", "AMZN", "GOOGL"],
                "document_types": ["10-K", "10-Q"],
                "object_types": [
                    "ResearchClaim",
                    "BusinessFactor",
                    "ExternalFactorExposure",
                    "MetricObservation",
                ],
                "limit": 80,
                "include_rejected": True,
                "root": root,
                "response_format": "json",
                "response_detail": "full",
            },
        ),
        QueryCase(
            name="retrieve_tool.full",
            fn_name="retrieve_tool",
            kwargs={
                "question": "AAPL AI and cloud related margin trend and risks",
                "tickers": ["AAPL"],
                "document_types": ["10-K"],
                "limit": 20,
                "root": root,
                "response_format": "json",
                "response_detail": "full",
            },
        ),
        QueryCase(
            name="quality_tool",
            fn_name="quality_tool",
            kwargs={
                "root": root,
                "ticker": "AAPL",
                "document_type": "10-K",
                "response_format": "json",
                "limit": 20,
            },
        ),
        QueryCase(
            name="compare_tool",
            fn_name="compare_tool",
            kwargs={
                "tickers": ["AAPL", "AMZN", "AMD"],
                "root": root,
                "topic": "revenue growth",
                "document_types": ["10-K"],
                "periods": ["CY2025"],
                "limit_per_ticker": 5,
                "response_format": "json",
            },
        ),
        QueryCase(
            name="plan_query_tool",
            fn_name="plan_query_tool",
            kwargs={
                "question": "AAPL exposure to cloud and AI infrastructure demand changes",
                "root": root,
                "response_format": "json",
            },
        ),
    ]


def _tool_by_name(name: str) -> Callable[..., str]:
    return {
        "catalog_tool": catalog_tool,
        "index_context_tool": index_context_tool,
        "company_context_tool": company_context_tool,
        "query_context_tool": query_context_tool,
        "query_tool": query_tool,
        "retrieve_tool": retrieve_tool,
        "quality_tool": quality_tool,
        "compare_tool": compare_tool,
        "plan_query_tool": plan_query_tool,
        "trace_tool": trace_tool,
        "chain_tool": chain_tool,
    }[name]


def _run_one_case(spec: QueryCase) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        tool_fn = _tool_by_name(spec.fn_name)
        response = tool_fn(**spec.kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "name": spec.name,
            "ok": True,
            "ms": round(elapsed_ms, 3),
            "bytes": len(response),
            "error": None,
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "name": spec.name,
            "ok": False,
            "ms": round(elapsed_ms, 3),
            "bytes": 0,
            "error": str(exc),
        }


def _median(values: list[float] | None) -> float | None:
    if not values:
        return None
    return round(statistics.median(values), 3)


def _resolve_seed_id(root: str) -> str | None:
    resp = json.loads(query_tool(
        topic="risk",
        tickers=["AAPL"],
        document_types=["10-K"],
        object_types=["ResearchClaim", "BusinessFactor", "EvidenceQuote"],
        limit=1,
        root=root,
        response_format="json",
        response_detail="compact",
    ))
    results = resp.get("results") or []
    if results:
        return results[0].get("id")
    return None


def _append_markdown_line(path: Path, round_no: int, result_rows: list[dict[str, Any]]) -> None:
    lines = [
        f"## Round {round_no} ({_utc_now()})",
        "| tool | median(ms) | p95(ms) | success/attempt | sample | status | bytes |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: |",
    ]

    grouped = {}
    for row in result_rows:
        grouped.setdefault(row["name"], []).append(row)

    for name, samples in grouped.items():
        attempts = len(samples)
        successes = sum(1 for s in samples if s["ok"])
        ms_values = [s["ms"] for s in samples if s["ok"]]
        median = _median(ms_values)
        p95 = None
        if ms_values:
            sorted_ms = sorted(ms_values)
            idx = max(0, int(len(sorted_ms) * 0.95) - 1)
            p95 = round(sorted_ms[min(idx, len(sorted_ms) - 1)], 3)
        status = "ok" if successes == attempts else "partial" if successes else "fail"
        sample = f"{','.join(str(s['ms']) for s in ms_values[:5])}"
        lines.append(
            f"| {name} | {median if median is not None else 'NA'} | {p95 if p95 is not None else 'NA'} | {successes}/{attempts} | {sample} | {status} | {samples[-1]['bytes'] if samples else 0} |"
        )

    path.write_text(path.read_text() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def run_round(root: str, repeat_each: int, parallel_workers: int, out_path: Path) -> int:
    cases = _build_cases(root)
    seed_id = _resolve_seed_id(root)
    trace_case = []
    if seed_id:
        trace_case = [
            QueryCase(
                name="trace_tool",
                fn_name="trace_tool",
                kwargs={"object_id": seed_id, "root": root, "response_format": "json"},
            ),
            QueryCase(
                name="chain_tool",
                fn_name="chain_tool",
                kwargs={
                    "object_id": seed_id,
                    "root": root,
                    "response_format": "json",
                    "max_depth": 2,
                    "direction": "both",
                    "include_quote_text": False,
                },
            ),
        ]

    work = cases + trace_case
    all_samples: list[dict[str, Any]] = []

    with ProcessPoolExecutor(max_workers=parallel_workers) as pool:
        for _ in range(repeat_each):
            futures = [pool.submit(_run_one_case, spec) for spec in work]
            for f in futures:
                all_samples.append(f.result())

    if not out_path.exists():
        out_path.write_text(
            "# KRW Ontology MCP Sub-Agent Emulation Report\n\n"
            "- generated: auto\n\n",
            encoding="utf-8",
        )

    round_no = 0
    existing = out_path.read_text(encoding="utf-8")
    for line in reversed(existing.splitlines()):
        if line.startswith("## Round "):
            try:
                round_no = int(line.split("Round ")[1].split("(")[0].strip())
            except Exception:
                round_no = 0
            break

    _append_markdown_line(out_path, round_no + 1, all_samples)
    return round_no + 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=ROOT_DEFAULT)
    parser.add_argument("--report", default="~/krw-ontology/mcp_subagent_loop_report.md")
    parser.add_argument("--repeat-each", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-rounds", type=int, default=0, help="0이면 무한반복")
    parser.add_argument("--sleep", type=float, default=30.0)
    args = parser.parse_args()

    report_path = Path(args.report)
    rounds = 0
    try:
        while True:
            rounds += 1
            run_no = run_round(args.root, args.repeat_each, args.workers, report_path)
            print(f"[{_utc_now()}] round={run_no} done")
            if args.max_rounds and rounds >= args.max_rounds:
                break
            time.sleep(args.sleep)
    except KeyboardInterrupt:
        print(f"[{_utc_now()}] stopped by user at round={rounds}")


if __name__ == "__main__":
    main()
