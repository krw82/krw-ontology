#!/usr/bin/env python3
"""Release-grade quality and latency gate for the immutable router sidecar.

The benchmark measures two explicit states:

* ``connection_cold`` opens a fresh sidecar connection for every sample.  The
  operating-system page cache is intentionally not flushed.
* ``query_warm`` opens one new persistent connection per query, executes the
  same query once without timing, and then records the requested samples.

Direct query gold contains only agent-authored English retrieval queries.
Raw-language user questions belong in the separate agent-to-plan E2E gold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import sqlite3
import statistics
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from krw_ontology.agent_index.router_sidecar import (
    ROUTER_SIDECAR_RELATIVE_PATH,
    RouterSidecar,
    build_router_micro_derivative,
    load_router_ranking_profile,
    verify_router_sidecar,
)
from krw_ontology.agent_index.spine_schema import read_spine_verification_sha256
from krw_ontology.agent_index.spine_router import OntologySpineRouter
from krw_ontology.mcp_server.contracts import SearchPlan
from krw_ontology.mcp_server.tools import clause_routing_query


BENCHMARK_FORMAT = "krw-ontology-router-benchmark/v4"
MEASUREMENT_PROTOCOL_FORMAT = "krw-ontology-router-measurement-protocol/v2"
QUERY_GOLD_FORMAT = "krw-ontology-router-query-gold/v2"
PLANNED_GOLD_FORMAT = "krw-ontology-router-planned-gold/v2"
BUDGET_FORMAT = "krw-ontology-router-release-budget/v2"
BASELINE_ACCEPTANCE_FORMAT = "krw-ontology-router-baseline-acceptance/v1"

QUERY_GOLD_CONTRACT = "agent-authored-english-retrieval-query"
PLANNED_GOLD_CONTRACT = "validated-search-plan-retrieval-query-routing"
_HANGUL_RE = re.compile(r"[가-힣]")

MEASUREMENT_PROTOCOL: dict[str, Any] = {
    "format": MEASUREMENT_PROTOCOL_FORMAT,
    "connection_cold": (
        "fresh RouterSidecar connection per sample; open, query, and close are timed"
    ),
    "query_warm": (
        "new persistent connection per query; identical untimed warmup once, then N timed calls"
    ),
    "os_page_cache": "not_flushed",
    "candidate_parity": "exact normalized ranking required across all cold and warm samples",
}

_BUDGET_BOOLEAN_KEYS = {
    "require_all_queries_gold",
    "require_baseline",
    "require_candidate_parity",
    "require_matching_environment",
    "require_matching_inputs",
    "require_production_layout",
    "require_row_constraints",
    "require_source_binding",
}
_BUDGET_NUMBER_KEYS = {
    "max_candidate_count",
    "max_connection_cold_p95_ms",
    "max_connection_cold_p95_regression_ratio",
    "max_distractor_hits_at_k",
    "max_expected_rank_at_k",
    "max_macro_ndcg_drop",
    "max_macro_recall_drop",
    "max_mean_expected_rank_at_k",
    "max_query_warm_p95_ms",
    "max_query_warm_p95_regression_ratio",
    "max_sidecar_size_regression_ratio",
    "min_expected_before_distractor_rate",
    "min_gold_queries",
    "min_joint_required_clause_coverage",
    "min_macro_hit_at_1",
    "min_macro_hit_at_5",
    "min_macro_mrr",
    "min_macro_ndcg_at_k",
    "min_macro_recall_at_k",
    "min_per_query_ndcg_at_k",
    "min_per_query_recall_at_k",
}
_BUDGET_ALLOWED_KEYS = {
    "format",
    "mode",
    *_BUDGET_BOOLEAN_KEYS,
    *_BUDGET_NUMBER_KEYS,
}
_RELATIVE_BUDGET_KEYS = {
    "max_connection_cold_p95_regression_ratio",
    "max_macro_ndcg_drop",
    "max_macro_recall_drop",
    "max_query_warm_p95_regression_ratio",
    "max_sidecar_size_regression_ratio",
}
_BOOTSTRAP_REQUIRED_KEYS = {
    "max_candidate_count",
    "max_connection_cold_p95_ms",
    "max_expected_rank_at_k",
    "max_mean_expected_rank_at_k",
    "max_query_warm_p95_ms",
    "min_expected_before_distractor_rate",
    "min_gold_queries",
    "min_joint_required_clause_coverage",
    "min_macro_hit_at_1",
    "min_macro_hit_at_5",
    "min_macro_mrr",
    "min_macro_ndcg_at_k",
    "min_macro_recall_at_k",
    "min_per_query_ndcg_at_k",
    "min_per_query_recall_at_k",
    "require_all_queries_gold",
    "require_candidate_parity",
    "require_production_layout",
    "require_row_constraints",
    "require_source_binding",
}
_ABSOLUTE_REQUIRED_TRUE_KEYS = {
    "require_all_queries_gold",
    "require_candidate_parity",
    "require_production_layout",
    "require_row_constraints",
    "require_source_binding",
}

_BASELINE_TOP_LEVEL_KEYS = {
    "ablations",
    "artifacts",
    "baseline",
    "baseline_acceptance",
    "cold_iterations",
    "environment",
    "format",
    "global_spine",
    "inputs",
    "iterations",
    "limit",
    "measurement_protocol",
    "planned_queries",
    "queries",
    "ranking_profile",
    "sidecar",
    "source_binding",
    "summary",
    "gate",
}


class BenchmarkSchemaError(ValueError):
    """Raised when benchmark inputs are not the declared versioned contract."""


def _json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_sha256(value: str, *, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise BenchmarkSchemaError(f"{label} must be a 64-character SHA-256 digest")
    return normalized


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkSchemaError(f"invalid JSON file {path}: {exc}") from exc


def _atomic_write_text(path: Path, payload: str) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.parent / f".{resolved.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, resolved)
    finally:
        temporary.unlink(missing_ok=True)


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BenchmarkSchemaError(f"{label} must be a JSON object")
    return value


def _reject_unknown_keys(
    payload: Mapping[str, Any],
    allowed: set[str],
    *,
    label: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise BenchmarkSchemaError(f"{label} contains unknown keys: {', '.join(unknown)}")


def _ticker_list(value: Any, *, label: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise BenchmarkSchemaError(f"{label} must be an array of ticker strings")
    normalized = [item.strip().upper() for item in value if item.strip()]
    if len(normalized) != len(set(normalized)):
        raise BenchmarkSchemaError(f"{label} must not contain duplicate tickers")
    if not normalized and not allow_empty:
        raise BenchmarkSchemaError(f"{label} must not be empty")
    return normalized


def _validate_source_release(value: Any, *, label: str) -> dict[str, str]:
    payload = _mapping(value, label=label)
    _reject_unknown_keys(
        payload,
        {"release_id", "source_manifest_hash"},
        label=label,
    )
    release_id = str(payload.get("release_id") or "").strip()
    source_manifest_hash = str(payload.get("source_manifest_hash") or "").strip()
    if not release_id or not source_manifest_hash:
        raise BenchmarkSchemaError(
            f"{label} requires non-empty release_id and source_manifest_hash"
        )
    return {
        "release_id": release_id,
        "source_manifest_hash": source_manifest_hash,
    }


def _validate_provenance(value: Any, *, label: str) -> dict[str, Any]:
    payload = _mapping(value, label=label)
    _reject_unknown_keys(
        payload,
        {"document_types", "periods", "source_note", "ticker"},
        label=label,
    )
    ticker = str(payload.get("ticker") or "").strip().upper()
    if not ticker:
        raise BenchmarkSchemaError(f"{label}.ticker is required")
    document_types = payload.get("document_types") or []
    periods = payload.get("periods") or []
    if not isinstance(document_types, list) or any(
        not isinstance(item, str) for item in document_types
    ):
        raise BenchmarkSchemaError(f"{label}.document_types must be an array of strings")
    if not isinstance(periods, list) or any(not isinstance(item, str) for item in periods):
        raise BenchmarkSchemaError(f"{label}.periods must be an array of strings")
    source_note = str(payload.get("source_note") or "").strip()
    if not source_note:
        raise BenchmarkSchemaError(f"{label}.source_note is required")
    return {
        "ticker": ticker,
        "document_types": [item.strip() for item in document_types if item.strip()],
        "periods": [item.strip() for item in periods if item.strip()],
        "source_note": source_note,
    }


def _validate_query_gold(payload: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    root = _mapping(payload, label="query gold")
    _reject_unknown_keys(
        root,
        {"contract", "format", "queries", "source_release"},
        label="query gold",
    )
    if root.get("format") != QUERY_GOLD_FORMAT:
        raise BenchmarkSchemaError(f"query gold format must be {QUERY_GOLD_FORMAT!r}")
    if root.get("contract") != QUERY_GOLD_CONTRACT:
        raise BenchmarkSchemaError(f"query gold contract must be {QUERY_GOLD_CONTRACT!r}")
    source_release = _validate_source_release(
        root.get("source_release"), label="query gold source_release"
    )
    raw_rows = root.get("queries")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise BenchmarkSchemaError("query gold queries must be a non-empty array")
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_rows, start=1):
        row = _mapping(raw, label=f"query gold row {index}")
        _reject_unknown_keys(
            row,
            {
                "distractor_tickers",
                "expected_tickers",
                "explicit_entity_scope",
                "id",
                "max_candidate_count",
                "provenance",
                "query",
            },
            label=f"query gold row {index}",
        )
        row_id = str(row.get("id") or "").strip()
        query = str(row.get("query") or "").strip()
        if not row_id or row_id in ids:
            raise BenchmarkSchemaError(f"query gold row {index} has missing/duplicate id")
        if not query:
            raise BenchmarkSchemaError(f"query gold row {index} query is required")
        if _HANGUL_RE.search(query):
            raise BenchmarkSchemaError(
                f"query gold row {row_id} contains Hangul; raw user language belongs in "
                "the agent-to-plan E2E gold"
            )
        expected = _ticker_list(
            row.get("expected_tickers"), label=f"query gold row {row_id}.expected_tickers"
        )
        distractors = _ticker_list(
            row.get("distractor_tickers") or [],
            label=f"query gold row {row_id}.distractor_tickers",
            allow_empty=True,
        )
        if set(expected).intersection(distractors):
            raise BenchmarkSchemaError(
                f"query gold row {row_id} expected/distractor tickers overlap"
            )
        max_candidates = row.get("max_candidate_count", 20)
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
            raise BenchmarkSchemaError(
                f"query gold row {row_id}.max_candidate_count must be an integer"
            )
        if max_candidates <= 0:
            raise BenchmarkSchemaError(
                f"query gold row {row_id}.max_candidate_count must be positive"
            )
        explicit_scope = row.get("explicit_entity_scope", False)
        if not isinstance(explicit_scope, bool):
            raise BenchmarkSchemaError(
                f"query gold row {row_id}.explicit_entity_scope must be boolean"
            )
        rows.append(
            {
                "id": row_id,
                "query": query,
                "expected_tickers": expected,
                "distractor_tickers": distractors,
                "max_candidate_count": max_candidates,
                "explicit_entity_scope": explicit_scope,
                "provenance": _validate_provenance(
                    row.get("provenance"), label=f"query gold row {row_id}.provenance"
                ),
            }
        )
        ids.add(row_id)
    return rows, source_release


def _validate_planned_gold(
    payload: Any,
) -> tuple[list[tuple[dict[str, Any], SearchPlan]], dict[str, str]]:
    root = _mapping(payload, label="planned gold")
    _reject_unknown_keys(
        root,
        {"contract", "format", "plans", "source_release"},
        label="planned gold",
    )
    if root.get("format") != PLANNED_GOLD_FORMAT:
        raise BenchmarkSchemaError(f"planned gold format must be {PLANNED_GOLD_FORMAT!r}")
    if root.get("contract") != PLANNED_GOLD_CONTRACT:
        raise BenchmarkSchemaError(f"planned gold contract must be {PLANNED_GOLD_CONTRACT!r}")
    source_release = _validate_source_release(
        root.get("source_release"), label="planned gold source_release"
    )
    raw_rows = root.get("plans")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise BenchmarkSchemaError("planned gold plans must be a non-empty array")
    rows: list[tuple[dict[str, Any], SearchPlan]] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_rows, start=1):
        row = _mapping(raw, label=f"planned gold row {index}")
        _reject_unknown_keys(
            row,
            {
                "distractor_tickers",
                "expected_tickers",
                "id",
                "max_candidate_count",
                "plan",
                "provenance",
            },
            label=f"planned gold row {index}",
        )
        row_id = str(row.get("id") or "").strip()
        if not row_id or row_id in ids:
            raise BenchmarkSchemaError(f"planned gold row {index} has missing/duplicate id")
        try:
            plan = SearchPlan.model_validate(row.get("plan"))
        except Exception as exc:
            raise BenchmarkSchemaError(
                f"planned gold row {row_id} invalid SearchPlan: {exc}"
            ) from exc
        expected = _ticker_list(
            row.get("expected_tickers"),
            label=f"planned gold row {row_id}.expected_tickers",
        )
        distractors = _ticker_list(
            row.get("distractor_tickers") or [],
            label=f"planned gold row {row_id}.distractor_tickers",
            allow_empty=True,
        )
        if set(expected).intersection(distractors):
            raise BenchmarkSchemaError(
                f"planned gold row {row_id} expected/distractor tickers overlap"
            )
        max_candidates = row.get("max_candidate_count", 20)
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
            raise BenchmarkSchemaError(
                f"planned gold row {row_id}.max_candidate_count must be an integer"
            )
        if max_candidates <= 0:
            raise BenchmarkSchemaError(
                f"planned gold row {row_id}.max_candidate_count must be positive"
            )
        rows.append(
            (
                {
                    "id": row_id,
                    "expected_tickers": expected,
                    "distractor_tickers": distractors,
                    "max_candidate_count": max_candidates,
                    "provenance": _validate_provenance(
                        row.get("provenance"),
                        label=f"planned gold row {row_id}.provenance",
                    ),
                },
                plan,
            )
        )
        ids.add(row_id)
    return rows, source_release


def _validate_budget(payload: Any) -> dict[str, Any]:
    budget = dict(_mapping(payload, label="budget"))
    _reject_unknown_keys(budget, _BUDGET_ALLOWED_KEYS, label="budget")
    if budget.get("format") != BUDGET_FORMAT:
        raise BenchmarkSchemaError(f"budget format must be {BUDGET_FORMAT!r}")
    mode = budget.get("mode")
    if mode not in {"bootstrap", "release"}:
        raise BenchmarkSchemaError("budget.mode must be 'bootstrap' or 'release'")
    for key in _BUDGET_BOOLEAN_KEYS.intersection(budget):
        if not isinstance(budget[key], bool):
            raise BenchmarkSchemaError(f"budget.{key} must be boolean")
    for key in _BUDGET_NUMBER_KEYS.intersection(budget):
        value = budget[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BenchmarkSchemaError(f"budget.{key} must be numeric")
        if not math.isfinite(float(value)):
            raise BenchmarkSchemaError(f"budget.{key} must be finite")
        if key.startswith("min_") and (
            key.endswith("_at_k")
            or key.endswith("_rate")
            or key.endswith("_coverage")
            or key in {"min_macro_mrr"}
        ):
            if not 0.0 <= float(value) <= 1.0:
                raise BenchmarkSchemaError(f"budget.{key} must be between 0 and 1")
        elif float(value) < 0.0:
            raise BenchmarkSchemaError(f"budget.{key} must be non-negative")
    require_baseline = budget.get("require_baseline")
    if not isinstance(require_baseline, bool):
        raise BenchmarkSchemaError("budget.require_baseline must be boolean")
    if mode == "bootstrap":
        if require_baseline:
            raise BenchmarkSchemaError("bootstrap budget must set require_baseline=false")
        forbidden = sorted(_RELATIVE_BUDGET_KEYS.intersection(budget))
        if forbidden:
            raise BenchmarkSchemaError(
                "bootstrap budget cannot contain relative keys: " + ", ".join(forbidden)
            )
        missing = sorted(_BOOTSTRAP_REQUIRED_KEYS - set(budget))
        if missing:
            raise BenchmarkSchemaError(
                "bootstrap budget is missing absolute checks: " + ", ".join(missing)
            )
    else:
        if not require_baseline:
            raise BenchmarkSchemaError("release budget must set require_baseline=true")
        for key in ("require_matching_environment", "require_matching_inputs"):
            if budget.get(key) is not True:
                raise BenchmarkSchemaError(f"release budget must set {key}=true")
        missing = sorted(_BOOTSTRAP_REQUIRED_KEYS - set(budget))
        if missing:
            raise BenchmarkSchemaError(
                "release budget is missing absolute checks: " + ", ".join(missing)
            )
    for key in _ABSOLUTE_REQUIRED_TRUE_KEYS:
        if budget.get(key) is not True:
            raise BenchmarkSchemaError(f"budget must set {key}=true")
    return budget


def _validate_baseline_report(payload: Any) -> Mapping[str, Any]:
    baseline = _mapping(payload, label="baseline")
    _reject_unknown_keys(baseline, _BASELINE_TOP_LEVEL_KEYS, label="baseline")
    if baseline.get("format") != BENCHMARK_FORMAT:
        raise BenchmarkSchemaError(f"baseline format must be {BENCHMARK_FORMAT!r}")
    if "baseline" in baseline:
        raise BenchmarkSchemaError("accepted baseline must not itself depend on another baseline")
    protocol = _mapping(baseline.get("measurement_protocol"), label="baseline protocol")
    if protocol != MEASUREMENT_PROTOCOL:
        raise BenchmarkSchemaError("baseline measurement protocol does not match current protocol")
    for key in ("inputs", "environment", "artifacts", "source_binding", "summary", "gate"):
        _mapping(baseline.get(key), label=f"baseline {key}")
    gate = _mapping(baseline["gate"], label="baseline gate")
    if gate.get("passed") is not True:
        raise BenchmarkSchemaError("baseline gate must have passed")
    accepted_budget = _validate_budget(gate.get("budget"))
    if accepted_budget.get("mode") != "bootstrap":
        raise BenchmarkSchemaError("baseline gate must use a bootstrap budget")
    source_binding = _mapping(baseline["source_binding"], label="baseline source_binding")
    if source_binding.get("ok") is not True:
        raise BenchmarkSchemaError("baseline source binding must pass")
    if source_binding.get("production_layout") is not True:
        raise BenchmarkSchemaError("baseline must use the production sidecar layout")
    summary = _mapping(baseline["summary"], label="baseline summary")
    summary_inputs = _mapping(summary.get("_inputs"), label="baseline summary _inputs")
    summary_environment = _mapping(
        summary.get("_environment"), label="baseline summary _environment"
    )
    inputs = _mapping(baseline["inputs"], label="baseline inputs")
    compatibility = _mapping(inputs.get("compatibility"), label="baseline inputs compatibility")
    if summary_inputs != compatibility:
        raise BenchmarkSchemaError("baseline summary _inputs does not match inputs.compatibility")
    if summary_environment != baseline["environment"]:
        raise BenchmarkSchemaError("baseline summary _environment does not match environment")
    acceptance = _mapping(baseline.get("baseline_acceptance"), label="baseline acceptance")
    _reject_unknown_keys(
        acceptance,
        {"accepted", "budget_mode", "budget_sha256", "format", "requested"},
        label="baseline acceptance",
    )
    if acceptance.get("format") != BASELINE_ACCEPTANCE_FORMAT:
        raise BenchmarkSchemaError("baseline acceptance format is invalid")
    if acceptance.get("requested") is not True or acceptance.get("accepted") is not True:
        raise BenchmarkSchemaError("baseline was not explicitly accepted")
    if acceptance.get("budget_mode") != "bootstrap":
        raise BenchmarkSchemaError("baseline must be accepted with a bootstrap budget")
    if not str(acceptance.get("budget_sha256") or "").strip():
        raise BenchmarkSchemaError("baseline acceptance budget_sha256 is required")
    if acceptance.get("budget_sha256") != gate.get("budget_sha256"):
        raise BenchmarkSchemaError(
            "baseline acceptance budget_sha256 does not match the gate budget"
        )
    return baseline


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100.0) * len(ordered)) - 1)
    return ordered[min(index, len(ordered) - 1)]


def _summary(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "min_ms": min(values) if values else None,
        "p50_ms": statistics.median(values) if values else None,
        "p95_ms": _percentile(values, 95),
        "max_ms": max(values) if values else None,
    }


def _normalized_candidates(candidates: Sequence[str]) -> list[str]:
    return list(
        dict.fromkeys(str(ticker).strip().upper() for ticker in candidates if str(ticker).strip())
    )


def _ranking_metrics(
    candidates: Sequence[str],
    expected: Sequence[str],
    *,
    distractors: Sequence[str] = (),
    evaluation_limit: int | None = None,
) -> dict[str, Any] | None:
    ranked = _normalized_candidates(candidates)
    gold = _normalized_candidates(expected)
    if not gold:
        return None
    gold_set = set(gold)
    rank_by_ticker = {ticker: rank for rank, ticker in enumerate(ranked, start=1)}
    hits = [1 if ticker in gold_set else 0 for ticker in ranked]
    recall = len(gold_set.intersection(ranked)) / len(gold_set)
    dcg = sum(hit / math.log2(rank + 2) for rank, hit in enumerate(hits))
    ideal_hits = [1] * min(len(gold_set), len(ranked))
    idcg = sum(hit / math.log2(rank + 2) for rank, hit in enumerate(ideal_hits))
    expected_ranks = {ticker: rank_by_ticker[ticker] for ticker in gold if ticker in rank_by_ticker}
    best_expected_rank = min(expected_ranks.values()) if expected_ranks else None
    resolved_limit = max(1, int(evaluation_limit or len(ranked) or 1))
    distractor_list = _normalized_candidates(distractors)
    distractor_ranks = {
        ticker: rank_by_ticker[ticker] for ticker in distractor_list if ticker in rank_by_ticker
    }
    best_distractor_rank = min(distractor_ranks.values()) if distractor_ranks else None
    expected_before_distractors: float | None = None
    if distractor_list:
        expected_before_distractors = float(
            best_expected_rank is not None
            and (best_distractor_rank is None or best_expected_rank < best_distractor_rank)
        )
    ndcg = dcg / idcg if idcg else 0.0
    if ndcg > 1.0 + 1e-12:
        raise AssertionError(f"NDCG invariant violated: {ndcg}")
    return {
        "recall_at_k": recall,
        "ndcg_at_k": min(1.0, ndcg),
        "hit_at_1": float(any(ticker in gold_set for ticker in ranked[:1])),
        "hit_at_5": float(any(ticker in gold_set for ticker in ranked[:5])),
        "mrr": 1.0 / best_expected_rank if best_expected_rank is not None else 0.0,
        "expected_rank": best_expected_rank,
        "expected_rank_penalized": best_expected_rank or (resolved_limit + 1),
        "expected_ranks": expected_ranks,
        "candidate_count": len(ranked),
        "distractor_count_at_k": len(distractor_ranks),
        "distractor_ranks": distractor_ranks,
        "best_distractor_rank": best_distractor_rank,
        "expected_before_distractors": expected_before_distractors,
    }


def _planned_joint_metrics(
    diagnostics: Mapping[str, Any], expected: Sequence[str]
) -> dict[str, Any]:
    gold = _normalized_candidates(expected)
    required_queries = [
        row
        for row in diagnostics.get("queries") or []
        if isinstance(row, Mapping) and bool(row.get("required"))
    ]
    required_count = len(required_queries)
    resolved_by_clause = [
        (
            str(row.get("clause_id") or ""),
            set(_normalized_candidates(row.get("resolved_tickers") or [])),
        )
        for row in required_queries
    ]
    coverage_by_ticker = {
        ticker: (
            sum(ticker in resolved for _clause_id, resolved in resolved_by_clause) / required_count
            if required_count
            else 0.0
        )
        for ticker in gold
    }
    best_ticker = (
        min(
            coverage_by_ticker,
            key=lambda ticker: (-coverage_by_ticker[ticker], ticker),
        )
        if coverage_by_ticker
        else None
    )
    coverage = coverage_by_ticker.get(best_ticker) if best_ticker else None
    covered_clause_ids = [
        clause_id
        for clause_id, resolved in resolved_by_clause
        if best_ticker is not None and best_ticker in resolved
    ]
    joint_expected = [
        ticker for ticker, ticker_coverage in coverage_by_ticker.items() if ticker_coverage == 1.0
    ]
    return {
        "required_clause_count": required_count,
        "covered_required_clause_ids": covered_clause_ids,
        "joint_required_clause_coverage": coverage,
        "expected_clause_coverage_by_ticker": coverage_by_ticker,
        "joint_expected_tickers": sorted(joint_expected) if required_count else [],
    }


def _assert_candidate_parity(
    label: str,
    samples: Sequence[Sequence[str]],
) -> list[str]:
    if not samples:
        raise AssertionError(f"{label}: candidate samples are missing")
    canonical = _normalized_candidates(samples[0])
    for index, sample in enumerate(samples[1:], start=2):
        normalized = _normalized_candidates(sample)
        if normalized != canonical:
            raise AssertionError(
                f"{label}: candidate parity failed at sample {index}: "
                f"{canonical!r} != {normalized!r}"
            )
    return canonical


def _search_tickers(
    sidecar: RouterSidecar,
    query: str,
    limit: int,
    *,
    explicit_entity_scope: bool,
    available_tickers: set[str] | None = None,
) -> list[str]:
    result = sidecar.search(
        query,
        limit=limit,
        explicit_entity_scope=explicit_entity_scope,
    )
    ranked = _normalized_candidates(
        [
            str(row.get("ticker") or "")
            for row in result.get("ticker_candidates") or []
            if isinstance(row, Mapping)
        ]
    )
    if available_tickers is None:
        return ranked
    return [ticker for ticker in ranked if ticker in available_tickers]


def _measure_connection_cold(
    sidecar_path: Path,
    *,
    iterations: int,
    ranking_profile: Mapping[str, Any],
    fn: Callable[[RouterSidecar], Sequence[str]],
) -> tuple[list[str], list[float]]:
    durations: list[float] = []
    candidate_samples: list[list[str]] = []
    for _ in range(iterations):
        started = time.perf_counter()
        with RouterSidecar(sidecar_path) as sidecar:
            sidecar._profile = deepcopy(ranking_profile)
            candidates = list(fn(sidecar))
        durations.append((time.perf_counter() - started) * 1_000)
        candidate_samples.append(candidates)
    return _assert_candidate_parity("connection_cold", candidate_samples), durations


def _measure_query_warm(
    sidecar_path: Path,
    *,
    iterations: int,
    ranking_profile: Mapping[str, Any],
    fn: Callable[[RouterSidecar], Sequence[str]],
) -> tuple[list[str], list[float]]:
    durations: list[float] = []
    candidate_samples: list[list[str]] = []
    with RouterSidecar(sidecar_path) as sidecar:
        sidecar._profile = deepcopy(ranking_profile)
        candidate_samples.append(list(fn(sidecar)))
        for _ in range(iterations):
            started = time.perf_counter()
            candidate_samples.append(list(fn(sidecar)))
            durations.append((time.perf_counter() - started) * 1_000)
    return _assert_candidate_parity("query_warm", candidate_samples), durations


def _measurement_summary(
    *,
    connection_cold_values: Sequence[float],
    query_warm_values: Sequence[float],
) -> dict[str, Any]:
    return {
        "connection_cold": {
            **_summary(connection_cold_values),
            "sample_count": len(connection_cold_values),
        },
        "query_warm": {
            **_summary(query_warm_values),
            "sample_count": len(query_warm_values),
            "untimed_warmup_count": 1,
        },
    }


def _routing_clauses(plan: SearchPlan) -> list[dict[str, Any]]:
    return [
        {
            "clause_id": clause.clause_id,
            "query": clause_routing_query(clause),
            "required": clause.required,
        }
        for clause in plan.clauses
    ]


def _new_production_router(
    global_spine_path: Path,
    sidecar_path: Path,
) -> OntologySpineRouter:
    router = OntologySpineRouter(global_spine_path)
    # route_planned_tickers is the production shared implementation.  The
    # override permits benchmarking a candidate before it is atomically placed
    # at the release path; the release budget separately requires layout parity.
    router._router_sidecar_path = sidecar_path.expanduser().resolve()
    return router


def _available_production_tickers(global_spine_path: Path) -> set[str]:
    router = OntologySpineRouter(global_spine_path)
    try:
        return set(router._shard_paths)
    finally:
        router.close()


def _route_planned_once(
    router: OntologySpineRouter,
    plan: SearchPlan,
    limit: int,
) -> tuple[list[str], dict[str, Any]]:
    candidates, diagnostics = router.route_planned_tickers(
        clauses=_routing_clauses(plan),
        explicit_tickers=plan.tickers or None,
        limit=limit,
    )
    return _normalized_candidates(candidates), dict(diagnostics)


def _measure_planned_connection_cold(
    global_spine_path: Path,
    sidecar_path: Path,
    *,
    plan: SearchPlan,
    limit: int,
    iterations: int,
    ranking_profile: Mapping[str, Any],
) -> tuple[list[str], list[float], dict[str, Any]]:
    durations: list[float] = []
    candidate_samples: list[list[str]] = []
    last_diagnostics: dict[str, Any] = {}
    for _ in range(iterations):
        router = _new_production_router(global_spine_path, sidecar_path)
        started = time.perf_counter()
        try:
            router._sidecar()._profile = deepcopy(ranking_profile)
            candidates, last_diagnostics = _route_planned_once(router, plan, limit)
        finally:
            router.close()
        durations.append((time.perf_counter() - started) * 1_000)
        candidate_samples.append(candidates)
    return (
        _assert_candidate_parity("planned_connection_cold", candidate_samples),
        durations,
        last_diagnostics,
    )


def _measure_planned_query_warm(
    global_spine_path: Path,
    sidecar_path: Path,
    *,
    plan: SearchPlan,
    limit: int,
    iterations: int,
    ranking_profile: Mapping[str, Any],
) -> tuple[list[str], list[float], dict[str, Any]]:
    router = _new_production_router(global_spine_path, sidecar_path)
    durations: list[float] = []
    candidate_samples: list[list[str]] = []
    last_diagnostics: dict[str, Any] = {}
    try:
        router._sidecar()._profile = deepcopy(ranking_profile)
        warmup_candidates, last_diagnostics = _route_planned_once(router, plan, limit)
        candidate_samples.append(warmup_candidates)
        for _ in range(iterations):
            started = time.perf_counter()
            candidates, last_diagnostics = _route_planned_once(router, plan, limit)
            durations.append((time.perf_counter() - started) * 1_000)
            candidate_samples.append(candidates)
    finally:
        router.close()
    return (
        _assert_candidate_parity("planned_query_warm", candidate_samples),
        durations,
        last_diagnostics,
    )


def _quality_for_row(
    candidates: Sequence[str],
    row: Mapping[str, Any],
    *,
    evaluation_limit: int,
    planned_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    quality = _ranking_metrics(
        candidates,
        row["expected_tickers"],
        distractors=row.get("distractor_tickers") or [],
        evaluation_limit=evaluation_limit,
    )
    if quality is None:
        raise AssertionError("validated gold row unexpectedly has no expected tickers")
    max_candidate_count = int(row.get("max_candidate_count") or evaluation_limit)
    quality["gold_max_candidate_count"] = max_candidate_count
    quality["row_constraints_passed"] = float(
        int(quality["candidate_count"]) <= max_candidate_count
    )
    if planned_diagnostics is not None:
        quality.update(_planned_joint_metrics(planned_diagnostics, row["expected_tickers"]))
    return quality


def _benchmark_summary(
    rows: Sequence[Mapping[str, Any]],
    query_warm_samples: Sequence[Sequence[float]],
    connection_cold_samples: Sequence[Sequence[float]],
) -> dict[str, Any]:
    if not (len(rows) == len(query_warm_samples) == len(connection_cold_samples)):
        raise AssertionError("benchmark rows and latency sample groups must align")
    qualities = [
        sidecar["quality"]
        for row in rows
        if isinstance((sidecar := row.get("sidecar")), Mapping)
        and isinstance(sidecar.get("quality"), Mapping)
    ]
    warm = [float(value) for values in query_warm_samples for value in values]
    cold = [float(value) for values in connection_cold_samples for value in values]

    def values(key: str) -> list[float]:
        return [
            float(quality[key])
            for quality in qualities
            if isinstance(quality.get(key), (int, float)) and not isinstance(quality.get(key), bool)
        ]

    recalls = values("recall_at_k")
    ndcgs = values("ndcg_at_k")
    hit_at_1 = values("hit_at_1")
    hit_at_5 = values("hit_at_5")
    mrr = values("mrr")
    expected_ranks = values("expected_rank_penalized")
    candidate_counts = values("candidate_count")
    distractor_counts = values("distractor_count_at_k")
    expected_before = values("expected_before_distractors")
    row_constraints = values("row_constraints_passed")
    joint_coverage = values("joint_required_clause_coverage")
    parity = all(
        bool(row.get("sidecar", {}).get("candidate_parity"))
        for row in rows
        if isinstance(row.get("sidecar"), Mapping)
    )
    return {
        "query_count": len(rows),
        "gold_query_count": len(qualities),
        "candidate_parity": parity,
        "quality": {
            "macro_recall_at_k": statistics.fmean(recalls) if recalls else None,
            "macro_ndcg_at_k": statistics.fmean(ndcgs) if ndcgs else None,
            "minimum_recall_at_k": min(recalls) if recalls else None,
            "minimum_ndcg_at_k": min(ndcgs) if ndcgs else None,
            "macro_hit_at_1": statistics.fmean(hit_at_1) if hit_at_1 else None,
            "macro_hit_at_5": statistics.fmean(hit_at_5) if hit_at_5 else None,
            "macro_mrr": statistics.fmean(mrr) if mrr else None,
            "mean_expected_rank_at_k": (
                statistics.fmean(expected_ranks) if expected_ranks else None
            ),
            "maximum_expected_rank_at_k": max(expected_ranks) if expected_ranks else None,
            "maximum_candidate_count": max(candidate_counts) if candidate_counts else None,
            "total_distractor_hits_at_k": sum(distractor_counts),
            "distractor_query_count": len(expected_before),
            "expected_before_distractor_rate": (
                statistics.fmean(expected_before) if expected_before else None
            ),
            "minimum_row_constraints_passed": (min(row_constraints) if row_constraints else None),
            "minimum_joint_required_clause_coverage": (
                min(joint_coverage) if joint_coverage else None
            ),
        },
        "latency": {
            "connection_cold": {**_summary(cold), "sample_count": len(cold)},
            "query_warm": {
                **_summary(warm),
                "sample_count": len(warm),
                "untimed_warmup_count_per_query": 1,
            },
        },
    }


def _nested_number(payload: Mapping[str, Any], *path: str) -> float | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return None
    value = float(current)
    return value if math.isfinite(value) else None


def _evaluate_gate(
    *,
    summary: Mapping[str, Any],
    artifacts: Mapping[str, Any],
    source_binding: Mapping[str, Any],
    budget: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    def record(check: dict[str, Any]) -> None:
        checks.append(check)
        if not check["passed"]:
            failures.append(check)

    def minimum_check(name: str, actual: float | None, minimum: float) -> None:
        record(
            {
                "name": name,
                "actual": actual,
                "minimum": minimum,
                "passed": actual is not None and actual >= minimum,
            }
        )

    def maximum_check(name: str, actual: float | None, maximum: float) -> None:
        record(
            {
                "name": name,
                "actual": actual,
                "maximum": maximum,
                "passed": actual is not None and actual <= maximum,
            }
        )

    minimum_paths = {
        "min_gold_queries": ("gold_query_count", ("combined", "gold_query_count")),
        "min_macro_recall_at_k": (
            "macro_recall_at_k",
            ("combined", "quality", "macro_recall_at_k"),
        ),
        "min_macro_ndcg_at_k": (
            "macro_ndcg_at_k",
            ("combined", "quality", "macro_ndcg_at_k"),
        ),
        "min_per_query_recall_at_k": (
            "minimum_recall_at_k",
            ("combined", "quality", "minimum_recall_at_k"),
        ),
        "min_per_query_ndcg_at_k": (
            "minimum_ndcg_at_k",
            ("combined", "quality", "minimum_ndcg_at_k"),
        ),
        "min_macro_hit_at_1": (
            "macro_hit_at_1",
            ("combined", "quality", "macro_hit_at_1"),
        ),
        "min_macro_hit_at_5": (
            "macro_hit_at_5",
            ("combined", "quality", "macro_hit_at_5"),
        ),
        "min_macro_mrr": ("macro_mrr", ("combined", "quality", "macro_mrr")),
        "min_expected_before_distractor_rate": (
            "expected_before_distractor_rate",
            ("combined", "quality", "expected_before_distractor_rate"),
        ),
        "min_joint_required_clause_coverage": (
            "minimum_joint_required_clause_coverage",
            ("planned_queries", "quality", "minimum_joint_required_clause_coverage"),
        ),
    }
    maximum_paths = {
        "max_connection_cold_p95_ms": (
            "connection_cold_p95_ms",
            ("combined", "latency", "connection_cold", "p95_ms"),
        ),
        "max_query_warm_p95_ms": (
            "query_warm_p95_ms",
            ("combined", "latency", "query_warm", "p95_ms"),
        ),
        "max_mean_expected_rank_at_k": (
            "mean_expected_rank_at_k",
            ("combined", "quality", "mean_expected_rank_at_k"),
        ),
        "max_expected_rank_at_k": (
            "maximum_expected_rank_at_k",
            ("combined", "quality", "maximum_expected_rank_at_k"),
        ),
        "max_candidate_count": (
            "maximum_candidate_count",
            ("combined", "quality", "maximum_candidate_count"),
        ),
        "max_distractor_hits_at_k": (
            "total_distractor_hits_at_k",
            ("combined", "quality", "total_distractor_hits_at_k"),
        ),
    }
    for budget_key, (name, path) in minimum_paths.items():
        if budget_key in budget:
            minimum_check(name, _nested_number(summary, *path), float(budget[budget_key]))
    for budget_key, (name, path) in maximum_paths.items():
        if budget_key in budget:
            maximum_check(name, _nested_number(summary, *path), float(budget[budget_key]))

    if budget.get("require_all_queries_gold"):
        query_count = _nested_number(summary, "combined", "query_count")
        gold_count = _nested_number(summary, "combined", "gold_query_count")
        record(
            {
                "name": "all_queries_have_gold",
                "query_count": query_count,
                "gold_query_count": gold_count,
                "passed": query_count is not None and query_count == gold_count,
            }
        )
    if budget.get("require_candidate_parity"):
        record(
            {
                "name": "candidate_parity",
                "actual": summary.get("combined", {}).get("candidate_parity"),
                "passed": summary.get("combined", {}).get("candidate_parity") is True,
            }
        )
    if budget.get("require_row_constraints"):
        actual = _nested_number(summary, "combined", "quality", "minimum_row_constraints_passed")
        minimum_check("row_constraints_passed", actual, 1.0)
    if budget.get("require_source_binding"):
        record(
            {
                "name": "source_binding",
                "errors": list(source_binding.get("errors") or []),
                "passed": source_binding.get("ok") is True,
            }
        )
    if budget.get("require_production_layout"):
        record(
            {
                "name": "production_layout",
                "actual": source_binding.get("production_layout"),
                "passed": source_binding.get("production_layout") is True,
            }
        )

    require_baseline = bool(budget.get("require_baseline"))
    if baseline is None:
        if require_baseline:
            record({"name": "accepted_baseline_present", "passed": False})
    else:
        baseline_summary_value = baseline.get("summary")
        baseline_summary = (
            baseline_summary_value if isinstance(baseline_summary_value, Mapping) else None
        )
        if baseline_summary is None:
            record({"name": "accepted_baseline_summary_valid", "passed": False})
        if budget.get("require_matching_inputs"):
            current_inputs = summary.get("_inputs")
            baseline_inputs = baseline_summary.get("_inputs") if baseline_summary else None
            record(
                {
                    "name": "accepted_baseline_inputs_match",
                    "actual": current_inputs,
                    "expected": baseline_inputs,
                    "passed": isinstance(current_inputs, Mapping)
                    and isinstance(baseline_inputs, Mapping)
                    and current_inputs == baseline_inputs,
                }
            )
        if budget.get("require_matching_environment"):
            current_environment = summary.get("_environment")
            baseline_environment = (
                baseline_summary.get("_environment") if baseline_summary else None
            )
            record(
                {
                    "name": "accepted_baseline_environment_matches",
                    "actual": current_environment,
                    "expected": baseline_environment,
                    "passed": isinstance(current_environment, Mapping)
                    and isinstance(baseline_environment, Mapping)
                    and current_environment == baseline_environment,
                }
            )

        def baseline_drop(
            budget_key: str,
            name: str,
            path: tuple[str, ...],
        ) -> None:
            if budget_key not in budget:
                return
            current = _nested_number(summary, *path)
            accepted = _nested_number(baseline_summary or {}, *path)
            actual = accepted - current if current is not None and accepted is not None else None
            maximum_check(name, actual, float(budget[budget_key]))

        baseline_drop(
            "max_macro_recall_drop",
            "macro_recall_drop",
            ("combined", "quality", "macro_recall_at_k"),
        )
        baseline_drop(
            "max_macro_ndcg_drop",
            "macro_ndcg_drop",
            ("combined", "quality", "macro_ndcg_at_k"),
        )

        def baseline_ratio(
            budget_key: str,
            name: str,
            current_value: float | None,
            baseline_value: float | None,
        ) -> None:
            if budget_key not in budget:
                return
            ratio = (
                current_value / baseline_value
                if current_value is not None and baseline_value not in (None, 0.0)
                else None
            )
            maximum_check(name, ratio, float(budget[budget_key]))

        baseline_ratio(
            "max_connection_cold_p95_regression_ratio",
            "connection_cold_p95_regression_ratio",
            _nested_number(summary, "combined", "latency", "connection_cold", "p95_ms"),
            _nested_number(
                baseline_summary or {},
                "combined",
                "latency",
                "connection_cold",
                "p95_ms",
            ),
        )
        baseline_ratio(
            "max_query_warm_p95_regression_ratio",
            "query_warm_p95_regression_ratio",
            _nested_number(summary, "combined", "latency", "query_warm", "p95_ms"),
            _nested_number(
                baseline_summary or {},
                "combined",
                "latency",
                "query_warm",
                "p95_ms",
            ),
        )
        baseline_artifacts = baseline.get("artifacts")
        baseline_ratio(
            "max_sidecar_size_regression_ratio",
            "sidecar_size_regression_ratio",
            _nested_number(artifacts, "sidecar_size_bytes"),
            _nested_number(
                baseline_artifacts if isinstance(baseline_artifacts, Mapping) else {},
                "sidecar_size_bytes",
            ),
        )

    return {
        "passed": not failures,
        "checks": checks,
        "failures": failures,
        "budget": dict(budget),
        "baseline_used": baseline is not None,
    }


def _read_global_metadata(path: Path) -> dict[str, Any]:
    uri = path.expanduser().resolve().as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute("SELECT key, value_json FROM metadata").fetchall()
    metadata: dict[str, Any] = {}
    for key, value_json in rows:
        try:
            metadata[str(key)] = json.loads(str(value_json))
        except (json.JSONDecodeError, TypeError):
            metadata[str(key)] = value_json
    return metadata


def _source_binding_report(
    global_spine_path: Path,
    sidecar_path: Path,
    *,
    expected_global_spine_sha256: str | None,
) -> dict[str, Any]:
    sealed_global_spine_sha256 = read_spine_verification_sha256(
        global_spine_path.expanduser().resolve()
    )
    trusted_global_spine_sha256 = expected_global_spine_sha256 or sealed_global_spine_sha256
    verification = verify_router_sidecar(
        sidecar_path,
        expected_global_spine_sha256=trusted_global_spine_sha256,
        deep=False,
    )
    sidecar_metadata = verification.get("metadata")
    sidecar_metadata = dict(sidecar_metadata) if isinstance(sidecar_metadata, Mapping) else {}
    global_metadata = _read_global_metadata(global_spine_path)
    errors = list(verification.get("errors") or [])
    if (
        expected_global_spine_sha256
        and sealed_global_spine_sha256
        and expected_global_spine_sha256 != sealed_global_spine_sha256
    ):
        errors.append("trusted_global_spine_sha256_seal_mismatch")
    comparisons = {
        "release_id": (
            sidecar_metadata.get("release_id"),
            global_metadata.get("release_id"),
        ),
        "source_manifest_hash": (
            sidecar_metadata.get("source_manifest_hash"),
            global_metadata.get("source_manifest_hash"),
        ),
        "source_created_at": (
            sidecar_metadata.get("source_created_at"),
            global_metadata.get("created_at"),
        ),
        "source_global_spine_schema_version": (
            sidecar_metadata.get("source_global_spine_schema_version"),
            global_metadata.get("schema_version"),
        ),
    }
    for key, (sidecar_value, global_value) in comparisons.items():
        if not sidecar_value or not global_value or sidecar_value != global_value:
            errors.append(f"router_sidecar_{key}_mismatch")
    expected_layout_path = (
        global_spine_path.expanduser().resolve().parent.parent / ROUTER_SIDECAR_RELATIVE_PATH
    ).resolve()
    production_layout = sidecar_path.expanduser().resolve() == expected_layout_path
    return {
        "ok": not errors,
        "verification_mode": (
            "light-sealed-sha256-no-rehash"
            if trusted_global_spine_sha256
            else "light-metadata-identity-no-global-rehash"
        ),
        "errors": sorted(set(errors)),
        "production_layout": production_layout,
        "expected_production_path": str(expected_layout_path),
        "source_global_spine_sha256": sidecar_metadata.get("source_global_spine_sha256"),
        "expected_global_spine_sha256": expected_global_spine_sha256,
        "sealed_global_spine_sha256": sealed_global_spine_sha256,
        "release_id": sidecar_metadata.get("release_id"),
        "source_manifest_hash": sidecar_metadata.get("source_manifest_hash"),
        "content_sha256": sidecar_metadata.get("content_sha256"),
        "build_fingerprint_sha256": sidecar_metadata.get("build_fingerprint_sha256"),
        "ranking_profile_sha256": sidecar_metadata.get("ranking_profile_sha256"),
    }


def _validate_gold_release(
    source_release: Mapping[str, str],
    global_metadata: Mapping[str, Any],
    *,
    label: str,
) -> None:
    # Gold describes source content, while a candidate release deliberately has
    # a new release id.  Requiring the provenance release id to equal every
    # candidate made a legitimate same-source A/B impossible and encouraged
    # untracked temporary gold rewrites.  The source-manifest hash is the actual
    # immutable data identity and remains a strict requirement.
    actual = str(global_metadata.get("source_manifest_hash") or "")
    expected = str(source_release.get("source_manifest_hash") or "")
    if actual != expected:
        raise BenchmarkSchemaError(
            f"{label} source_manifest_hash mismatch: expected {expected!r}, "
            f"global spine has {actual!r}"
        )


def _cpu_model() -> str:
    try:
        completed = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        value = completed.stdout.strip()
        if completed.returncode == 0 and value:
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.processor().strip() or platform.machine()


def _environment() -> dict[str, Any]:
    return {
        "architecture": platform.machine(),
        "cpu_model": _cpu_model(),
        "logical_cpu_count": int(os.cpu_count() or 1),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "system": platform.system(),
        "system_release": platform.release(),
    }


def _legacy_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    legacy = deepcopy(profile)
    legacy["fusion"]["primary_rrf_scale"] = float(legacy["rrf_k"]) + 1.0
    return legacy


def _score_breakdown(
    sidecar_path: Path,
    ranking_profile: Mapping[str, Any],
    *,
    query: str,
    limit: int,
    explicit_entity_scope: bool,
) -> dict[str, Any]:
    with RouterSidecar(sidecar_path) as sidecar:
        sidecar._profile = deepcopy(ranking_profile)
        detailed = sidecar.search(
            query,
            limit=limit,
            explicit_entity_scope=explicit_entity_scope,
        )
    slim_units = [
        {
            key: row.get(key)
            for key in (
                "routing_unit_id",
                "ticker",
                "topic_family_label",
                "score",
                "channel_scores",
                "lexical_score",
                "primary_score",
                "matched_terms",
                "term_coverage_count",
                "term_coverage_ratio",
                "information_coverage",
                "strict_match",
                "source_coherent_match",
                "coarse_rank",
                "coarse_score",
                "micro_score",
                "micro_unit_ids",
                "micro_matched_terms",
            )
            if key in row
        }
        for row in detailed.get("routing_units") or []
        if isinstance(row, Mapping)
    ]
    return {
        "ticker_candidates": detailed.get("ticker_candidates") or [],
        "routing_units": slim_units,
        "query_term_stats": detailed.get("query_term_stats") or [],
        "minimum_should_match": detailed.get("minimum_should_match"),
        "lexical_diagnostics": detailed.get("lexical_diagnostics") or {},
        "fusion_diagnostics": detailed.get("fusion_diagnostics") or {},
        "micro_rerank": detailed.get("micro_rerank") or {},
        "micro_routing_units": detailed.get("micro_routing_units") or [],
        "graph_expansion_used": detailed.get("graph_expansion_used"),
    }


def _ablation_delta(current: Mapping[str, Any], legacy: Mapping[str, Any]) -> dict[str, Any]:
    def delta(path: tuple[str, ...]) -> float | None:
        left = _nested_number(current, *path)
        right = _nested_number(legacy, *path)
        return right - left if left is not None and right is not None else None

    def ratio(path: tuple[str, ...]) -> float | None:
        left = _nested_number(current, *path)
        right = _nested_number(legacy, *path)
        return right / left if left not in (None, 0.0) and right is not None else None

    return {
        "macro_recall_delta": delta(("combined", "quality", "macro_recall_at_k")),
        "macro_ndcg_delta": delta(("combined", "quality", "macro_ndcg_at_k")),
        "macro_mrr_delta": delta(("combined", "quality", "macro_mrr")),
        "connection_cold_p95_ratio": ratio(("combined", "latency", "connection_cold", "p95_ms")),
        "query_warm_p95_ratio": ratio(("combined", "latency", "query_warm", "p95_ms")),
    }


def _positive_cli_int(value: int, *, label: str) -> int:
    if value <= 0:
        raise BenchmarkSchemaError(f"{label} must be positive")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--global-spine", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--planned-queries", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--cold-iterations", type=int, default=3)
    parser.add_argument("--include-score-breakdown", action="store_true")
    parser.add_argument("--include-ablations", action="store_true")
    parser.add_argument("--ranking-profile", type=Path)
    parser.add_argument(
        "--expected-global-spine-sha256",
        help=(
            "Optional trusted build-manifest hash. It is compared to sidecar metadata; "
            "the 59GiB global spine is never rehashed by this benchmark."
        ),
    )
    parser.add_argument("--budget", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--accept-baseline", type=Path)
    parser.add_argument("--build-micro-derivative-from", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        limit = _positive_cli_int(int(args.limit), label="--limit")
        iterations = _positive_cli_int(int(args.iterations), label="--iterations")
        cold_iterations = _positive_cli_int(int(args.cold_iterations), label="--cold-iterations")
        if args.accept_baseline and not args.budget:
            raise BenchmarkSchemaError("--accept-baseline requires --budget")
        if args.baseline and not args.budget:
            raise BenchmarkSchemaError("--baseline requires --budget")
        if args.accept_baseline and args.baseline:
            raise BenchmarkSchemaError(
                "bootstrap baseline acceptance cannot also consume --baseline"
            )
        budget = _validate_budget(_read_json(args.budget)) if args.budget else None
        if args.accept_baseline and budget and budget["mode"] != "bootstrap":
            raise BenchmarkSchemaError("--accept-baseline requires a bootstrap absolute budget")
        if args.baseline and budget and budget["mode"] != "release":
            raise BenchmarkSchemaError("--baseline requires a release budget")
        baseline: Mapping[str, Any] | None = None
        baseline_sha256: str | None = None
        if args.baseline:
            baseline = _validate_baseline_report(_read_json(args.baseline))
            baseline_sha256 = _file_sha256(args.baseline)

        if args.build_micro_derivative_from:
            build_result = build_router_micro_derivative(
                args.global_spine,
                args.build_micro_derivative_from,
                args.sidecar,
                ranking_profile_path=args.ranking_profile,
            )
            print(
                json.dumps(
                    {
                        "event": "micro_derivative_built",
                        "path": str(build_result.path),
                        "elapsed_ms": build_result.elapsed_ms,
                        "counts": build_result.counts,
                        "content_sha256": build_result.metadata.get("content_sha256"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )

        query_payload = _read_json(args.queries)
        query_rows, query_source_release = _validate_query_gold(query_payload)
        planned_rows: list[tuple[dict[str, Any], SearchPlan]] = []
        planned_source_release: dict[str, str] | None = None
        if args.planned_queries:
            planned_rows, planned_source_release = _validate_planned_gold(
                _read_json(args.planned_queries)
            )
        global_metadata = _read_global_metadata(args.global_spine)
        _validate_gold_release(
            query_source_release,
            global_metadata,
            label="query gold",
        )
        if planned_source_release:
            _validate_gold_release(
                planned_source_release,
                global_metadata,
                label="planned gold",
            )
        for row, plan in planned_rows:
            if plan.limit_tickers != limit:
                raise BenchmarkSchemaError(
                    f"planned gold row {row['id']} limit_tickers={plan.limit_tickers} "
                    f"does not match --limit={limit}"
                )

        source_binding = _source_binding_report(
            args.global_spine,
            args.sidecar,
            expected_global_spine_sha256=(
                _normalize_sha256(
                    args.expected_global_spine_sha256,
                    label="--expected-global-spine-sha256",
                )
                if args.expected_global_spine_sha256
                else None
            ),
        )
        with RouterSidecar(args.sidecar) as profile_sidecar:
            ranking_profile = (
                load_router_ranking_profile(args.ranking_profile)
                if args.ranking_profile
                else dict(profile_sidecar.ranking_profile)
            )
            sidecar_metadata = dict(profile_sidecar.metadata)
        available_tickers = _available_production_tickers(args.global_spine)
        missing_gold_tickers = sorted(
            {
                ticker
                for row in query_rows
                for ticker in row["expected_tickers"]
                if ticker not in available_tickers
            }
            | {
                ticker
                for row, _plan in planned_rows
                for ticker in row["expected_tickers"]
                if ticker not in available_tickers
            }
        )
        if missing_gold_tickers:
            raise BenchmarkSchemaError(
                "gold expected tickers do not have available production shards: "
                + ", ".join(missing_gold_tickers)
            )
        missing_distractor_tickers = sorted(
            {
                ticker
                for row in query_rows
                for ticker in row["distractor_tickers"]
                if ticker not in available_tickers
            }
            | {
                ticker
                for row, _plan in planned_rows
                for ticker in row["distractor_tickers"]
                if ticker not in available_tickers
            }
        )
        if missing_distractor_tickers:
            raise BenchmarkSchemaError(
                "gold distractor tickers do not have available production shards: "
                + ", ".join(missing_distractor_tickers)
            )

        environment = _environment()
        protocol_hash = _json_sha256(MEASUREMENT_PROTOCOL)
        input_compatibility = {
            "benchmark_implementation_sha256": _file_sha256(Path(__file__).resolve()),
            "query_gold_format": QUERY_GOLD_FORMAT,
            "queries_sha256": _file_sha256(args.queries),
            "planned_gold_format": PLANNED_GOLD_FORMAT if args.planned_queries else None,
            "planned_queries_sha256": (
                _file_sha256(args.planned_queries) if args.planned_queries else None
            ),
            "limit": limit,
            "iterations": iterations,
            "cold_iterations": cold_iterations,
            "measurement_protocol_sha256": protocol_hash,
            "include_score_breakdown": bool(args.include_score_breakdown),
            "include_ablations": bool(args.include_ablations),
        }
        report: dict[str, Any] = {
            "format": BENCHMARK_FORMAT,
            "measurement_protocol": dict(MEASUREMENT_PROTOCOL),
            "global_spine": str(args.global_spine.expanduser().resolve()),
            "sidecar": str(args.sidecar.expanduser().resolve()),
            "limit": limit,
            "iterations": iterations,
            "cold_iterations": cold_iterations,
            "inputs": {
                "queries_path": str(args.queries.expanduser().resolve()),
                "planned_queries_path": (
                    str(args.planned_queries.expanduser().resolve())
                    if args.planned_queries
                    else None
                ),
                "compatibility": input_compatibility,
            },
            "artifacts": {
                "global_spine_size_bytes": args.global_spine.stat().st_size,
                "sidecar_size_bytes": args.sidecar.stat().st_size,
                "available_company_shard_count": len(available_tickers),
            },
            "environment": environment,
            "source_binding": source_binding,
            "ranking_profile": {
                "id": ranking_profile.get("profile_id"),
                "sha256": _json_sha256(ranking_profile),
                "override": bool(args.ranking_profile),
                "path": (
                    str(args.ranking_profile.expanduser().resolve())
                    if args.ranking_profile
                    else None
                ),
                "fusion": deepcopy(ranking_profile.get("fusion") or {}),
                "metadata_sha256": sidecar_metadata.get("ranking_profile_sha256"),
            },
            "queries": [],
            "planned_queries": [],
            "ablations": {},
        }

        query_warm_samples: list[list[float]] = []
        query_cold_samples: list[list[float]] = []
        planned_warm_samples: list[list[float]] = []
        planned_cold_samples: list[list[float]] = []
        legacy_query_rows: list[dict[str, Any]] = []
        legacy_query_warm: list[list[float]] = []
        legacy_query_cold: list[list[float]] = []
        legacy_planned_rows: list[dict[str, Any]] = []
        legacy_planned_warm: list[list[float]] = []
        legacy_planned_cold: list[list[float]] = []
        legacy_profile = _legacy_profile(ranking_profile)

        for row in query_rows:
            query = row["query"]
            explicit_scope = bool(row["explicit_entity_scope"])

            def search(sidecar: RouterSidecar) -> list[str]:
                return _search_tickers(
                    sidecar,
                    query,
                    limit,
                    explicit_entity_scope=explicit_scope,
                    available_tickers=available_tickers,
                )

            cold_candidates, cold_durations = _measure_connection_cold(
                args.sidecar,
                iterations=cold_iterations,
                ranking_profile=ranking_profile,
                fn=search,
            )
            warm_candidates, warm_durations = _measure_query_warm(
                args.sidecar,
                iterations=iterations,
                ranking_profile=ranking_profile,
                fn=search,
            )
            candidates = _assert_candidate_parity(
                f"query {row['id']} cold/warm",
                [cold_candidates, warm_candidates],
            )
            sidecar_report: dict[str, Any] = {
                "latency": _measurement_summary(
                    connection_cold_values=cold_durations,
                    query_warm_values=warm_durations,
                ),
                "candidates": candidates,
                "candidate_sha256": _json_sha256(candidates),
                "candidate_parity": True,
                "quality": _quality_for_row(
                    candidates,
                    row,
                    evaluation_limit=limit,
                ),
            }
            if args.include_score_breakdown:
                sidecar_report["score_breakdown"] = _score_breakdown(
                    args.sidecar,
                    ranking_profile,
                    query=query,
                    limit=limit,
                    explicit_entity_scope=explicit_scope,
                )
            report_row = {
                "id": row["id"],
                "query": query,
                "expected_tickers": row["expected_tickers"],
                "distractor_tickers": row["distractor_tickers"],
                "provenance": row["provenance"],
                "sidecar": sidecar_report,
            }
            if args.include_ablations:
                legacy_cold_candidates, legacy_cold_durations = _measure_connection_cold(
                    args.sidecar,
                    iterations=cold_iterations,
                    ranking_profile=legacy_profile,
                    fn=search,
                )
                legacy_warm_candidates, legacy_warm_durations = _measure_query_warm(
                    args.sidecar,
                    iterations=iterations,
                    ranking_profile=legacy_profile,
                    fn=search,
                )
                legacy_candidates = _assert_candidate_parity(
                    f"legacy query {row['id']} cold/warm",
                    [legacy_cold_candidates, legacy_warm_candidates],
                )
                legacy_report = {
                    "primary_rrf_scale": legacy_profile["fusion"]["primary_rrf_scale"],
                    "latency": _measurement_summary(
                        connection_cold_values=legacy_cold_durations,
                        query_warm_values=legacy_warm_durations,
                    ),
                    "candidates": legacy_candidates,
                    "candidate_parity": True,
                    "quality": _quality_for_row(
                        legacy_candidates,
                        row,
                        evaluation_limit=limit,
                    ),
                }
                sidecar_report.setdefault("ablations", {})["legacy_primary_scale"] = legacy_report
                if ranking_profile.get("micro_rerank", {}).get("enabled"):
                    no_micro_profile = deepcopy(ranking_profile)
                    no_micro_profile["micro_rerank"]["enabled"] = False
                    no_micro_candidates, no_micro_durations = _measure_query_warm(
                        args.sidecar,
                        iterations=iterations,
                        ranking_profile=no_micro_profile,
                        fn=search,
                    )
                    sidecar_report["ablations"]["without_micro_rerank"] = {
                        "latency": {**_summary(no_micro_durations)},
                        "candidates": no_micro_candidates,
                        "quality": _quality_for_row(
                            no_micro_candidates,
                            row,
                            evaluation_limit=limit,
                        ),
                    }
                else:
                    sidecar_report.setdefault("ablations", {})["without_micro_rerank"] = {
                        "skipped": "active_profile_micro_rerank_disabled"
                    }
                legacy_query_rows.append({"sidecar": legacy_report})
                legacy_query_warm.append(legacy_warm_durations)
                legacy_query_cold.append(legacy_cold_durations)
            report["queries"].append(report_row)
            query_warm_samples.append(warm_durations)
            query_cold_samples.append(cold_durations)

        for row, plan in planned_rows:
            cold_candidates, cold_durations, _cold_diagnostics = _measure_planned_connection_cold(
                args.global_spine,
                args.sidecar,
                plan=plan,
                limit=limit,
                iterations=cold_iterations,
                ranking_profile=ranking_profile,
            )
            warm_candidates, warm_durations, diagnostics = _measure_planned_query_warm(
                args.global_spine,
                args.sidecar,
                plan=plan,
                limit=limit,
                iterations=iterations,
                ranking_profile=ranking_profile,
            )
            candidates = _assert_candidate_parity(
                f"planned query {row['id']} cold/warm",
                [cold_candidates, warm_candidates],
            )
            sidecar_report = {
                "latency": _measurement_summary(
                    connection_cold_values=cold_durations,
                    query_warm_values=warm_durations,
                ),
                "candidates": candidates,
                "candidate_sha256": _json_sha256(candidates),
                "candidate_parity": True,
                "quality": _quality_for_row(
                    candidates,
                    row,
                    evaluation_limit=limit,
                    planned_diagnostics=diagnostics,
                ),
                "routing_diagnostics": diagnostics,
            }
            if args.include_ablations:
                legacy_cold_candidates, legacy_cold_durations, _ = _measure_planned_connection_cold(
                    args.global_spine,
                    args.sidecar,
                    plan=plan,
                    limit=limit,
                    iterations=cold_iterations,
                    ranking_profile=legacy_profile,
                )
                (
                    legacy_warm_candidates,
                    legacy_warm_durations,
                    legacy_diagnostics,
                ) = _measure_planned_query_warm(
                    args.global_spine,
                    args.sidecar,
                    plan=plan,
                    limit=limit,
                    iterations=iterations,
                    ranking_profile=legacy_profile,
                )
                legacy_candidates = _assert_candidate_parity(
                    f"legacy planned query {row['id']} cold/warm",
                    [legacy_cold_candidates, legacy_warm_candidates],
                )
                legacy_report = {
                    "primary_rrf_scale": legacy_profile["fusion"]["primary_rrf_scale"],
                    "latency": _measurement_summary(
                        connection_cold_values=legacy_cold_durations,
                        query_warm_values=legacy_warm_durations,
                    ),
                    "candidates": legacy_candidates,
                    "candidate_parity": True,
                    "quality": _quality_for_row(
                        legacy_candidates,
                        row,
                        evaluation_limit=limit,
                        planned_diagnostics=legacy_diagnostics,
                    ),
                }
                sidecar_report.setdefault("ablations", {})["legacy_primary_scale"] = legacy_report
                legacy_planned_rows.append({"sidecar": legacy_report})
                legacy_planned_warm.append(legacy_warm_durations)
                legacy_planned_cold.append(legacy_cold_durations)
            report["planned_queries"].append(
                {
                    "id": row["id"],
                    "question": plan.question,
                    "expected_tickers": row["expected_tickers"],
                    "distractor_tickers": row["distractor_tickers"],
                    "provenance": row["provenance"],
                    "sidecar": sidecar_report,
                }
            )
            planned_warm_samples.append(warm_durations)
            planned_cold_samples.append(cold_durations)

        combined_rows = [*report["queries"], *report["planned_queries"]]
        combined_warm = [*query_warm_samples, *planned_warm_samples]
        combined_cold = [*query_cold_samples, *planned_cold_samples]
        report["summary"] = {
            "queries": _benchmark_summary(
                report["queries"], query_warm_samples, query_cold_samples
            ),
            "planned_queries": _benchmark_summary(
                report["planned_queries"], planned_warm_samples, planned_cold_samples
            ),
            "combined": _benchmark_summary(combined_rows, combined_warm, combined_cold),
            "_inputs": input_compatibility,
            "_environment": environment,
        }

        if args.include_ablations:
            legacy_combined_rows = [*legacy_query_rows, *legacy_planned_rows]
            legacy_combined_warm = [*legacy_query_warm, *legacy_planned_warm]
            legacy_combined_cold = [*legacy_query_cold, *legacy_planned_cold]
            legacy_summary = {
                "queries": _benchmark_summary(
                    legacy_query_rows, legacy_query_warm, legacy_query_cold
                ),
                "planned_queries": _benchmark_summary(
                    legacy_planned_rows, legacy_planned_warm, legacy_planned_cold
                ),
                "combined": _benchmark_summary(
                    legacy_combined_rows,
                    legacy_combined_warm,
                    legacy_combined_cold,
                ),
            }
            report["ablations"]["legacy_primary_scale"] = {
                "primary_rrf_scale": legacy_profile["fusion"]["primary_rrf_scale"],
                "summary": legacy_summary,
                "delta_vs_current": _ablation_delta(report["summary"], legacy_summary),
            }

        if args.baseline:
            report["baseline"] = {
                "path": str(args.baseline.expanduser().resolve()),
                "sha256": baseline_sha256,
            }

        if budget:
            report["gate"] = _evaluate_gate(
                summary=report["summary"],
                artifacts=report["artifacts"],
                source_binding=source_binding,
                budget=budget,
                baseline=baseline,
            )
            report["gate"]["budget_path"] = str(args.budget.expanduser().resolve())
            report["gate"]["budget_sha256"] = _file_sha256(args.budget)
        else:
            report["gate"] = {
                "passed": None,
                "checks": [],
                "failures": [],
                "budget": None,
                "baseline_used": baseline is not None,
            }

        gate_passed = report["gate"].get("passed") is True
        report["baseline_acceptance"] = {
            "format": BASELINE_ACCEPTANCE_FORMAT,
            "requested": bool(args.accept_baseline),
            "accepted": bool(args.accept_baseline and gate_passed),
            "budget_mode": budget.get("mode") if budget else None,
            "budget_sha256": _file_sha256(args.budget) if args.budget else None,
        }

        payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            _atomic_write_text(args.output, payload)
        if args.accept_baseline and gate_passed:
            _atomic_write_text(args.accept_baseline, payload)
        elif args.accept_baseline:
            print(
                "refusing to accept a benchmark baseline that failed its absolute gate",
                file=sys.stderr,
            )
        print(payload, end="")
        if budget and not gate_passed:
            raise SystemExit(2)
    except BenchmarkSchemaError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
