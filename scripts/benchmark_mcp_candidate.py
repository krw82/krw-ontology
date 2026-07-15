#!/usr/bin/env python3
"""Gate a built candidate through the real MCP ResearchState/trace/chain path."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Mapping, Sequence

from krw_ontology.config.paths import (
    ONTOLOGY_ENV_ENV,
    ONTOLOGY_GLOBAL_SPINE_PATH_ENV,
    ONTOLOGY_MANIFEST_PATH_ENV,
    ONTOLOGY_RELEASE_ROOT_ENV,
    ONTOLOGY_ROOT_ENV,
)
from krw_ontology.mcp_server import tools as mcp_tools
from krw_ontology.mcp_server.contracts import (
    MAX_RESEARCH_STATE_MODEL_BYTES,
    MAX_RESEARCH_STATE_WIRE_BYTES,
    MCP_CONTRACT_VERSION,
    research_state_model_bytes,
    research_state_wire_bytes,
)

try:
    from scripts.benchmark_router_sidecar import _validate_planned_gold
except ModuleNotFoundError:  # direct `python scripts/...` execution
    from benchmark_router_sidecar import _validate_planned_gold


REPORT_FORMAT = "krw-ontology-mcp-candidate-benchmark/v1"


class McpBenchmarkError(ValueError):
    """Raised when release/gold inputs are not compatible."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpBenchmarkError(f"invalid JSON {path}: {exc}") from exc


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _release_binding(release_root: Path, source_hash: str) -> dict[str, str]:
    manifest_path = release_root / "manifest.json"
    source_path = release_root / "source_manifest.json"
    manifest = _read_json(manifest_path)
    source = _read_json(source_path)
    if not isinstance(manifest, Mapping) or not isinstance(source, Mapping):
        raise McpBenchmarkError("release manifests must be objects")
    actual_hash = str(source.get("manifest_hash") or source.get("source_manifest_hash") or "")
    if actual_hash != source_hash:
        raise McpBenchmarkError(
            f"planned gold source hash mismatch: release={actual_hash!r} gold={source_hash!r}"
        )
    return {
        "release_id": str(manifest.get("release_id") or release_root.name),
        "source_manifest_hash": actual_hash,
    }


def _configure_release(release_root: Path) -> None:
    resolved = str(release_root.resolve())
    os.environ.update(
        {
            ONTOLOGY_ENV_ENV: "dev",
            ONTOLOGY_RELEASE_ROOT_ENV: resolved,
            ONTOLOGY_ROOT_ENV: resolved,
            ONTOLOGY_MANIFEST_PATH_ENV: str(release_root / "manifest.json"),
            ONTOLOGY_GLOBAL_SPINE_PATH_ENV: str(release_root / "indexes" / "global_spine.sqlite"),
            "KRW_MCP_EXPECTED_CONTRACT_VERSION": MCP_CONTRACT_VERSION,
        }
    )
    mcp_tools.reset_mcp_runtime_caches()


def _payload(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise McpBenchmarkError("MCP trace/chain response is not an object")
    return parsed


def _has_error(payload: Mapping[str, Any]) -> bool:
    if payload.get("ok") is False:
        return True
    return bool(payload.get("error") or payload.get("error_code"))


def _contains_text(payload: Any, value: str) -> bool:
    needle = value.casefold()
    if isinstance(payload, str):
        return needle in payload.casefold()
    if isinstance(payload, Mapping):
        return any(_contains_text(item, value) for item in payload.values())
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return any(_contains_text(item, value) for item in payload)
    return False


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return round(ordered[index], 3)


def _model_payload_bytes(payload: Any) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _run_case(row: Mapping[str, Any], plan: Any) -> dict[str, Any]:
    expected = set(row["expected_tickers"])
    started = time.perf_counter()
    state = mcp_tools.query_context_tool(search_plan=plan)
    query_ms = round((time.perf_counter() - started) * 1000, 3)
    expected_units = [unit for unit in state.evidence_units if unit.ticker in expected]
    traceable = next(
        (unit for unit in expected_units if unit.object_id and unit.ticker),
        None,
    )
    trace_payload: dict[str, Any] = {}
    chain_payload: dict[str, Any] = {}
    trace_ms: float | None = None
    chain_ms: float | None = None
    if traceable is not None:
        started = time.perf_counter()
        trace_payload = _payload(
            mcp_tools.trace_tool(object_id=traceable.object_id, ticker=traceable.ticker)
        )
        trace_ms = round((time.perf_counter() - started) * 1000, 3)
        started = time.perf_counter()
        chain_payload = _payload(
            mcp_tools.chain_tool(
                object_id=traceable.object_id,
                ticker=traceable.ticker,
                max_depth=2,
                include_quote_text=False,
            )
        )
        chain_ms = round((time.perf_counter() - started) * 1000, 3)
    required_coverage = [coverage for coverage in state.clause_coverage if coverage.required]
    chain_model_bytes = _model_payload_bytes(chain_payload) if chain_payload else None
    checks = {
        "contract_version": state.contract_version == "research-state/v2",
        "resolved_expected_ticker": bool(
            expected.intersection(state.resolved_scope.resolved_tickers)
        ),
        "expected_ticker_evidence": bool(expected_units),
        "required_clauses_not_missing": bool(required_coverage)
        and all(coverage.status != "missing" for coverage in required_coverage),
        "trace_root_available": traceable is not None,
        "trace_ok": bool(trace_payload) and not _has_error(trace_payload),
        "trace_ticker_preserved": bool(traceable)
        and _contains_text(trace_payload, str(traceable.ticker)),
        "chain_ok": bool(chain_payload) and not _has_error(chain_payload),
        "chain_identity_preserved": bool(traceable)
        and _contains_text(chain_payload, str(traceable.object_id))
        and _contains_text(chain_payload, str(traceable.ticker)),
        "chain_payload_bounded": chain_model_bytes is not None
        and chain_model_bytes <= mcp_tools.MAX_CHAIN_RESPONSE_MODEL_BYTES
        and bool((chain_payload.get("response_budget") or {}).get("within_budget")),
        "model_payload_bounded": research_state_model_bytes(state)
        <= MAX_RESEARCH_STATE_MODEL_BYTES,
        "wire_payload_bounded": research_state_wire_bytes(state) <= MAX_RESEARCH_STATE_WIRE_BYTES,
    }
    state_payload = state.model_dump(mode="json", by_alias=True)
    return {
        "id": row["id"],
        "expected_tickers": sorted(expected),
        "resolved_tickers": state.resolved_scope.resolved_tickers,
        "answerability": state.answerability.model_dump(mode="json"),
        "clause_coverage": [item.model_dump(mode="json") for item in state.clause_coverage],
        "evidence_unit_count": len(state.evidence_units),
        "expected_evidence_unit_count": len(expected_units),
        "selected_trace": (
            {
                "object_id": traceable.object_id,
                "ticker": traceable.ticker,
                "directness": traceable.directness,
                "evidence_grade": traceable.evidence_grade,
            }
            if traceable
            else None
        ),
        "latency_ms": {"query_context": query_ms, "trace": trace_ms, "chain": chain_ms},
        "payload_bytes": {
            "model": research_state_model_bytes(state),
            "wire": research_state_wire_bytes(state),
            "chain_model": chain_model_bytes,
        },
        "state_sha256": _sha256_json(state_payload),
        "trace_sha256": _sha256_json(trace_payload) if trace_payload else None,
        "chain_sha256": _sha256_json(chain_payload) if chain_payload else None,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _run_missing_object_probe(
    *,
    ticker: str | None,
    max_latency_ms: float,
) -> dict[str, Any]:
    object_id = "__krw_missing_object_id_latency_probe__"
    started = time.perf_counter()
    payload = _payload(mcp_tools.trace_tool(object_id=object_id, ticker=ticker))
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
    checks = {
        "not_found": error.get("code") == "not_found",
        "latency_bounded": latency_ms <= max_latency_ms,
    }
    return {
        "object_id": object_id,
        "ticker": ticker,
        "latency_ms": latency_ms,
        "maximum_allowed_ms": max_latency_ms,
        "checks": checks,
        "passed": all(checks.values()),
    }


def benchmark(args: argparse.Namespace) -> int:
    release_root = args.release_root.expanduser().resolve()
    planned_payload = _read_json(args.planned_gold)
    rows, source = _validate_planned_gold(planned_payload)
    release = _release_binding(release_root, source["source_manifest_hash"])
    _configure_release(release_root)
    try:
        records = [_run_case(row, plan) for row, plan in rows]
        missing_object_probe = _run_missing_object_probe(
            ticker=None,
            max_latency_ms=args.max_missing_object_ms,
        )
    finally:
        mcp_tools.reset_mcp_runtime_caches()
    query_latencies = [float(row["latency_ms"]["query_context"]) for row in records]
    passed = all(row["passed"] for row in records) and missing_object_probe["passed"]
    p95 = _percentile(query_latencies, 0.95)
    latency_passed = p95 is not None and p95 <= args.max_query_p95_ms
    report = {
        "format": REPORT_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release": release,
        "planned_gold": {
            "path": str(args.planned_gold.resolve()),
            "sha256": hashlib.sha256(args.planned_gold.read_bytes()).hexdigest(),
            "case_count": len(records),
        },
        "summary": {
            "case_count": len(records),
            "passed": sum(bool(row["passed"]) for row in records),
            "failed": sum(not bool(row["passed"]) for row in records),
            "query_context_latency_ms": {
                "p50": round(statistics.median(query_latencies), 3),
                "p95": p95,
                "maximum_allowed_p95": args.max_query_p95_ms,
            },
        },
        "missing_object_probe": missing_object_probe,
        "gate": {"passed": bool(passed and latency_passed)},
        "records": records,
    }
    _atomic_write_json(args.output, report)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "summary": report["summary"],
                "gate": report["gate"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["gate"]["passed"] else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument(
        "--planned-gold",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmarks" / "router_planned_gold_v2.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-query-p95-ms", type=float, default=2_500.0)
    parser.add_argument("--max-missing-object-ms", type=float, default=750.0)
    args = parser.parse_args()
    if args.max_query_p95_ms <= 0:
        raise SystemExit("--max-query-p95-ms must be positive")
    if args.max_missing_object_ms <= 0:
        raise SystemExit("--max-missing-object-ms must be positive")
    try:
        raise SystemExit(benchmark(args))
    except (McpBenchmarkError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"FAILED MCP candidate benchmark: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
