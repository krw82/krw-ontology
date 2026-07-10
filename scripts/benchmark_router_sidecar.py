#!/usr/bin/env python3
"""Benchmark Serving Index V4 against optional global-spine routing.

The query set is caller supplied.  Each JSON row must contain ``query`` and may
contain ``id`` plus ``expected_tickers`` for Recall@K/NDCG evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from krw_ontology.agent_index.router_sidecar import (
    RouterSidecar,
    build_router_micro_derivative,
    load_router_ranking_profile,
)
from krw_ontology.agent_index.spine_router import OntologySpineRouter


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100.0) * len(ordered)) - 1)
    return ordered[min(index, len(ordered) - 1)]


def _ranking_metrics(
    candidates: Sequence[str],
    expected: Sequence[str],
) -> dict[str, float] | None:
    gold = {str(ticker).strip().upper() for ticker in expected if str(ticker).strip()}
    if not gold:
        return None
    ranked = [str(ticker).strip().upper() for ticker in candidates]
    hits = [1 if ticker in gold else 0 for ticker in ranked]
    recall = len(gold.intersection(ranked)) / len(gold)
    dcg = sum(hit / math.log2(rank + 2) for rank, hit in enumerate(hits))
    ideal_hits = [1] * min(len(gold), len(ranked))
    idcg = sum(hit / math.log2(rank + 2) for rank, hit in enumerate(ideal_hits))
    return {
        "recall_at_k": recall,
        "ndcg_at_k": dcg / idcg if idcg else 0.0,
    }


def _measure(
    fn: Callable[[str, int], Sequence[str]],
    *,
    query: str,
    limit: int,
    iterations: int,
) -> tuple[list[str], list[float]]:
    durations: list[float] = []
    candidates: list[str] = []
    for _ in range(iterations):
        started = time.perf_counter()
        candidates = list(fn(query, limit))
        durations.append((time.perf_counter() - started) * 1_000)
    return candidates, durations


def _summary(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "min_ms": min(values) if values else None,
        "p50_ms": statistics.median(values) if values else None,
        "p95_ms": _percentile(values, 95),
        "max_ms": max(values) if values else None,
    }


def _json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _search_tickers(
    sidecar: RouterSidecar,
    query: str,
    limit: int,
    *,
    explicit_entity_scope: bool,
) -> list[str]:
    return [
        str(row.get("ticker") or "")
        for row in sidecar.search(
            query,
            limit=limit,
            explicit_entity_scope=explicit_entity_scope,
        ).get("ticker_candidates")
        or []
        if isinstance(row, dict) and row.get("ticker")
    ]


def _planned_search_tickers(
    sidecar: RouterSidecar,
    plan: Mapping[str, Any],
    limit: int,
) -> list[str]:
    clauses = [
        clause
        for clause in plan.get("clauses") or []
        if isinstance(clause, Mapping)
        and str(clause.get("retrieval_query") or clause.get("query") or "").strip()
    ]
    rrf_k = max(1, int(sidecar.ranking_profile.get("rrf_k") or 60))
    scores: defaultdict[str, float] = defaultdict(float)
    clause_hits: Counter[str] = Counter()
    required_hits: Counter[str] = Counter()
    best_rank: dict[str, int] = {}
    required_rankings: list[list[str]] = []
    for clause in clauses:
        query = str(clause.get("retrieval_query") or clause.get("query") or "").strip()
        required = bool(clause.get("required", True))
        ranking = _search_tickers(
            sidecar,
            query,
            limit,
            explicit_entity_scope=False,
        )
        seen: set[str] = set()
        for rank, ticker in enumerate(ranking, start=1):
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            scores[ticker] += (2.0 if required else 1.0) / (rrf_k + rank)
            clause_hits[ticker] += 1
            if required:
                required_hits[ticker] += 1
            best_rank[ticker] = min(best_rank.get(ticker, rank), rank)
        if required:
            required_rankings.append(ranking)
    aggregate = sorted(
        scores,
        key=lambda ticker: (
            -required_hits[ticker],
            -scores[ticker],
            -clause_hits[ticker],
            best_rank.get(ticker, limit + 1),
            ticker,
        ),
    )
    ranked: list[str] = []
    for clause_ranking in required_rankings:
        top = next((ticker for ticker in clause_ranking if ticker not in ranked), None)
        if top:
            ranked.append(top)
        if len(ranked) >= limit:
            return ranked
    for ticker in aggregate:
        if ticker not in ranked:
            ranked.append(ticker)
        if len(ranked) >= limit:
            break
    return ranked


def _ablation_report(
    sidecar: RouterSidecar,
    *,
    query: str,
    limit: int,
    expected: Sequence[str],
    explicit_entity_scope: bool,
    iterations: int,
) -> dict[str, Any]:
    original = deepcopy(sidecar._profile)
    weights = dict(original["channel_weights"])
    variants = {
        "full": weights,
        "primary_only": {
            "exact_alias": 0.0,
            "facet": 0.0,
            "fielded_fts": weights["fielded_fts"],
            "short_token": 0.0,
        },
        "without_facet": {**weights, "facet": 0.0},
        "without_short_token": {**weights, "short_token": 0.0},
    }
    report: dict[str, Any] = {}
    try:
        for name, channel_weights in variants.items():
            sidecar._profile["channel_weights"] = channel_weights
            candidates = _search_tickers(
                sidecar,
                query,
                limit,
                explicit_entity_scope=explicit_entity_scope,
            )
            report[name] = {
                "candidates": candidates,
                "quality": _ranking_metrics(candidates, expected),
            }
        sidecar._profile = deepcopy(original)
        sidecar._profile["micro_rerank"]["enabled"] = False
        candidates, durations = _measure(
            lambda text, top_k: _search_tickers(
                sidecar,
                text,
                top_k,
                explicit_entity_scope=explicit_entity_scope,
            ),
            query=query,
            limit=limit,
            iterations=iterations,
        )
        report["without_micro_rerank"] = {
            **_summary(durations),
            "candidates": candidates,
            "quality": _ranking_metrics(candidates, expected),
        }
    finally:
        sidecar._profile = original
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--global-spine", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--planned-queries", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--include-spine-baseline", action="store_true")
    parser.add_argument("--include-score-breakdown", action="store_true")
    parser.add_argument("--include-ablations", action="store_true")
    parser.add_argument("--ranking-profile", type=Path)
    parser.add_argument(
        "--build-micro-derivative-from",
        type=Path,
        help="Verified coarse sidecar copied and augmented before benchmarking",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

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

    query_rows = json.loads(args.queries.read_text(encoding="utf-8"))
    if not isinstance(query_rows, list) or not query_rows:
        raise SystemExit("--queries must be a non-empty JSON array")
    limit = max(1, int(args.limit))
    iterations = max(1, int(args.iterations))
    report: dict[str, Any] = {
        "format": "krw-ontology-router-benchmark/v2",
        "global_spine": str(args.global_spine.expanduser().resolve()),
        "sidecar": str(args.sidecar.expanduser().resolve()),
        "limit": limit,
        "iterations": iterations,
        "queries": [],
    }

    with RouterSidecar(args.sidecar) as sidecar:
        if args.ranking_profile:
            sidecar._profile = load_router_ranking_profile(args.ranking_profile)
        report["ranking_profile"] = {
            "id": sidecar.ranking_profile.get("profile_id"),
            "sha256": _json_sha256(sidecar.ranking_profile),
            "override": bool(args.ranking_profile),
            "path": str(args.ranking_profile.expanduser().resolve())
            if args.ranking_profile
            else None,
        }
        router = OntologySpineRouter(args.global_spine) if args.include_spine_baseline else None
        try:
            for index, raw_row in enumerate(query_rows, start=1):
                if not isinstance(raw_row, dict) or not str(raw_row.get("query") or "").strip():
                    raise SystemExit(f"query row {index} must be an object with non-empty query")
                query = str(raw_row["query"]).strip()
                expected = raw_row.get("expected_tickers") or []
                explicit_entity_scope = bool(raw_row.get("explicit_entity_scope", False))

                sidecar_candidates, sidecar_ms = _measure(
                    lambda text, top_k: _search_tickers(
                        sidecar,
                        text,
                        top_k,
                        explicit_entity_scope=explicit_entity_scope,
                    ),
                    query=query,
                    limit=limit,
                    iterations=iterations,
                )
                row_report: dict[str, Any] = {
                    "id": raw_row.get("id") or f"q{index}",
                    "query": query,
                    "expected_tickers": expected,
                    "sidecar": {
                        **_summary(sidecar_ms),
                        "candidates": sidecar_candidates,
                        "quality": _ranking_metrics(sidecar_candidates, expected),
                    },
                }
                if args.include_score_breakdown:
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
                        if isinstance(row, dict)
                    ]
                    row_report["sidecar"]["score_breakdown"] = {
                        "ticker_candidates": detailed.get("ticker_candidates") or [],
                        "routing_units": slim_units,
                        "query_term_stats": detailed.get("query_term_stats") or [],
                        "minimum_should_match": detailed.get("minimum_should_match"),
                        "micro_rerank": detailed.get("micro_rerank") or {},
                        "micro_routing_units": detailed.get("micro_routing_units") or [],
                        "graph_expansion_used": detailed.get("graph_expansion_used"),
                    }
                if args.include_ablations:
                    row_report["sidecar"]["ablations"] = _ablation_report(
                        sidecar,
                        query=query,
                        limit=limit,
                        expected=expected,
                        explicit_entity_scope=explicit_entity_scope,
                        iterations=iterations,
                    )
                if router is not None:
                    baseline_candidates, baseline_ms = _measure(
                        lambda text, top_k: router._rank_candidate_tickers(
                            text,
                            limit=top_k,
                        ),
                        query=query,
                        limit=limit,
                        iterations=iterations,
                    )
                    row_report["global_spine_baseline"] = {
                        **_summary(baseline_ms),
                        "candidates": baseline_candidates,
                        "quality": _ranking_metrics(baseline_candidates, expected),
                    }
                report["queries"].append(row_report)
            if args.planned_queries:
                planned_rows = json.loads(args.planned_queries.read_text(encoding="utf-8"))
                if not isinstance(planned_rows, list):
                    raise SystemExit("--planned-queries must be a JSON array")
                report["planned_queries"] = []
                for index, raw_row in enumerate(planned_rows, start=1):
                    if not isinstance(raw_row, Mapping) or not isinstance(
                        raw_row.get("plan"), Mapping
                    ):
                        raise SystemExit(f"planned query row {index} must contain a plan object")
                    expected = raw_row.get("gold") or raw_row.get("expected_tickers") or []
                    durations: list[float] = []
                    candidates: list[str] = []
                    for _ in range(iterations):
                        planned_started = time.perf_counter()
                        candidates = _planned_search_tickers(
                            sidecar,
                            raw_row["plan"],
                            limit,
                        )
                        durations.append((time.perf_counter() - planned_started) * 1_000)
                    original_profile = deepcopy(sidecar._profile)
                    try:
                        sidecar._profile["micro_rerank"]["enabled"] = False
                        without_micro_durations: list[float] = []
                        without_micro: list[str] = []
                        for _ in range(iterations):
                            without_micro_started = time.perf_counter()
                            without_micro = _planned_search_tickers(
                                sidecar,
                                raw_row["plan"],
                                limit,
                            )
                            without_micro_durations.append(
                                (time.perf_counter() - without_micro_started) * 1_000
                            )
                    finally:
                        sidecar._profile = original_profile
                    report["planned_queries"].append(
                        {
                            "id": raw_row.get("id") or f"planned-q{index}",
                            "expected_tickers": expected,
                            "sidecar": {
                                **_summary(durations),
                                "candidates": candidates,
                                "quality": _ranking_metrics(candidates, expected),
                            },
                            "without_micro_rerank": {
                                **_summary(without_micro_durations),
                                "candidates": without_micro,
                                "quality": _ranking_metrics(without_micro, expected),
                            },
                        }
                    )
        finally:
            if router is not None:
                router.close()

    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
