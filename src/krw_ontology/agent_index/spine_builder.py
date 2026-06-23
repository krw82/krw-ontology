"""v3 global spine + company shard build primitives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krw_ontology.agent_index.cross_company_links import (
    CrossCompanyLinkGenerationResult,
    generate_cross_company_links,
)
from krw_ontology.agent_index.source_artifact_sqlite import (
    SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
    SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
    SourceArtifactSqlitePlan,
    SourceCompanyShardPlanItem,
    build_source_artifact_sqlite,
    cleanup_sqlite_database_files,
    plan_source_artifact_sqlite_inputs,
    replace_sqlite_database,
    temporary_sqlite_path,
    verify_source_artifact_sqlite,
    write_source_artifact_manifest,
)
from krw_ontology.agent_index.spine_schema import (
    GLOBAL_SPINE_BUILDER_VERSION,
    GLOBAL_SPINE_LAYOUT,
    GLOBAL_SPINE_SCHEMA_VERSION,
    GLOBAL_SPINE_TABLES,
    create_global_spine_schema,
    read_global_spine_metadata,
    verify_global_spine_schema,
    write_global_spine_metadata,
)

COMPANY_SHARD_SCHEMA_VERSION = "krw-ontology-company-shard/v1"
COMPANY_SHARD_CACHE_FORMAT_VERSION = "krw-ontology-company-shard-cache/v3"
SPINE_FRAGMENT_CACHE_FORMAT_VERSION = "krw-ontology-spine-fragment-cache/v3"
SPINE_FRAGMENT_FORMAT_VERSION = "krw-ontology-spine-fragment/v1"
SPINE_PROJECTION_VERSION = "spine-projection/v1"
V3_BUILD_SUMMARY_FORMAT_VERSION = "krw-ontology-v3-build-summary/v1"
V3_BUILD_PLAN_FORMAT_VERSION = "krw-ontology-v3-build-plan/v1"
V3_BUILD_PROGRESS_FORMAT_VERSION = "krw-ontology-v3-build-progress/v1"
V3_SHARD_MANIFEST_FORMAT_VERSION = "krw-ontology-shard-manifest/v3"
SHARD_QUALITY_SUMMARY_FORMAT_VERSION = "krw-ontology-shard-quality-summary/v1"


@dataclass(frozen=True)
class CompanyShardBuildResult:
    ticker: str
    shard_path: Path
    cache_hit: bool
    artifact_count: int
    totals: Mapping[str, int]
    verification: Mapping[str, Any]
    cache_key: str | None = None


@dataclass(frozen=True)
class SpineFragmentResult:
    ticker: str
    fragment_path: Path
    shard_path: Path
    counts: Mapping[str, int]
    cache_hit: bool = False
    cache_key: str | None = None


@dataclass(frozen=True)
class GlobalSpineMergeResult:
    global_spine_path: Path
    fragment_count: int
    counts: Mapping[str, int]
    chain_links: CrossCompanyLinkGenerationResult
    verification: Mapping[str, Any]


@dataclass(frozen=True)
class SpineShardReleaseBuildResult:
    release_root: Path
    release_id: str
    source_manifest_path: Path
    build_plan_path: Path
    shard_manifest_path: Path
    build_summary_path: Path
    global_spine_path: Path
    shard_results: tuple[CompanyShardBuildResult, ...]
    fragment_results: tuple[SpineFragmentResult, ...]
    merge_result: GlobalSpineMergeResult
    shard_manifest: Mapping[str, Any]
    build_summary: Mapping[str, Any]
    progress_path: Path | None = None


def build_spine_shard_release_outputs(
    root: Path,
    *,
    release_id: str,
    workers: int | None = None,
    cache_root: Path | None = None,
    source_manifest_path: Path | None = None,
    progress_path: Path | None = None,
    no_cache: bool = False,
    generate_links: bool = True,
) -> SpineShardReleaseBuildResult:
    """Build all v3 production index outputs for an already-materialized release root."""
    resolved_root = root.expanduser().resolve()
    if not resolved_root.is_dir():
        raise FileNotFoundError(f"Release root not found: {resolved_root}")
    indexes_dir = resolved_root / "indexes"
    companies_dir = indexes_dir / "companies"
    fragments_dir = indexes_dir / "fragments" / "spine"
    global_spine_path = indexes_dir / "global_spine.sqlite"
    shard_manifest_path = indexes_dir / "shard_manifest.json"
    build_plan_path = indexes_dir / "build_plan.json"
    build_summary_path = indexes_dir / "build_summary.json"
    resolved_source_manifest_path = (
        source_manifest_path.expanduser().resolve()
        if source_manifest_path is not None
        else resolved_root / "source_manifest.json"
    )
    resolved_progress_path = (
        progress_path.expanduser().resolve()
        if progress_path is not None
        else indexes_dir / "build_progress.jsonl"
    )

    indexes_dir.mkdir(parents=True, exist_ok=True)
    progress = _BuildProgressWriter(resolved_progress_path, release_id=release_id, release_root=resolved_root)
    build_started_at = time.perf_counter()
    progress.record(
        "build",
        "build",
        "started",
        details={
            "root": str(resolved_root),
            "index_layout": GLOBAL_SPINE_LAYOUT,
            "no_cache": no_cache,
        },
    )
    try:
        source_started_at = time.perf_counter()
        source_manifest = write_source_artifact_manifest(
            resolved_root,
            manifest_path=resolved_source_manifest_path,
        )
        progress.record(
            "source_manifest",
            "source_discovery",
            "complete",
            output=resolved_source_manifest_path,
            details={
                "artifact_count": source_manifest.get("artifact_count"),
                "manifest_hash": source_manifest.get("manifest_hash"),
            },
            started_at=source_started_at,
        )
        plan_started_at = time.perf_counter()
        progress.record(
            "build_plan",
            "build_plan",
            "started",
            output=build_plan_path,
        )
        plan = _plan_v3_artifact_inputs(
            resolved_root,
            sqlite_path=global_spine_path,
            cache_root=cache_root,
            workers=workers,
            source_manifest_path=resolved_source_manifest_path,
        )
        if not plan.company_items:
            raise ValueError("No company artifacts found for v3 release build")

        cache_probe_started_at = time.perf_counter()
        progress.record(
            "cache_probe",
            "cache_probe",
            "started",
            details={
                "company_count": len(plan.company_items),
                "worker_count": min(plan.workers, len(plan.company_items)),
            },
        )
        company_specs = _company_build_specs(
            plan,
            companies_dir=companies_dir,
            no_cache=no_cache,
        )
        build_plan = {
            "format": V3_BUILD_PLAN_FORMAT_VERSION,
            "release_id": release_id,
            "index_layout": GLOBAL_SPINE_LAYOUT,
            "source_manifest_path": _path_label(resolved_root, resolved_source_manifest_path),
            "source_manifest_hash": plan.source_manifest_hash,
            "progress_path": _path_label(resolved_root, resolved_progress_path),
            "no_cache": no_cache,
            "company_count": len(company_specs),
            "worker_count": min(plan.workers, len(company_specs)),
            "plan": plan.to_dict(include_items=True),
            "companies": [
                {
                    "ticker": spec["ticker"],
                    "artifact_count": len(spec["company_plan"].items),
                    "shard_path": _path_label(resolved_root, spec["shard_path"]),
                    "cache_hit": spec["company_plan"].company_items[0].cache_hit,
                    "cache_key": spec["company_plan"].company_items[0].cache_key,
                    "estimated_cost": spec["estimated_cost"],
                }
                for spec in company_specs
            ],
        }
        _write_json(build_plan_path, build_plan)
        progress.record(
            "cache_probe",
            "cache_probe",
            "complete",
            details={
                "company_count": len(company_specs),
                "company_cache_hits": sum(
                    1 for spec in company_specs if spec["company_plan"].company_items[0].cache_hit
                ),
                "spine_fragment_cache_hits": sum(
                    1
                    for spec in company_specs
                    if _plan_spine_fragment_cache_hit(
                        spec,
                        release_id=release_id,
                        source_manifest_hash=plan.source_manifest_hash,
                        cache_root=plan.cache_root,
                        no_cache=no_cache,
                    )
                ),
            },
            started_at=cache_probe_started_at,
        )
        progress.record(
            "build_plan",
            "build_plan",
            "complete",
            output=build_plan_path,
            details={
                "company_count": len(company_specs),
                "worker_count": min(plan.workers, len(company_specs)),
            },
            started_at=plan_started_at,
        )

        shard_stage_started_at = time.perf_counter()
        progress.record(
            "company_shards",
            "company_shard",
            "started",
            details={"total": len(company_specs), "workers": min(plan.workers, len(company_specs))},
        )
        shard_results = _build_company_shards_for_release(
            resolved_root,
            company_specs=company_specs,
            workers=plan.workers,
            no_cache=no_cache,
            progress=progress,
        )
        progress.record(
            "company_shards",
            "company_shard",
            "complete",
            details={
                "total": len(shard_results),
                "cache_hits": sum(1 for result in shard_results if result.cache_hit),
                "rebuilt": sum(1 for result in shard_results if not result.cache_hit),
            },
            started_at=shard_stage_started_at,
        )

        fragment_stage_started_at = time.perf_counter()
        progress.record(
            "spine_fragments",
            "spine_fragment",
            "started",
            details={"total": len(shard_results), "workers": min(plan.workers, len(shard_results))},
        )
        fragment_results = _emit_spine_fragments_for_release(
            shard_results,
            fragments_dir=fragments_dir,
            release_id=release_id,
            source_manifest_hash=plan.source_manifest_hash,
            cache_root=plan.cache_root,
            workers=plan.workers,
            no_cache=no_cache,
            progress=progress,
        )
        progress.record(
            "spine_fragments",
            "spine_fragment",
            "complete",
            details={
                "total": len(fragment_results),
                "cache_hits": sum(1 for result in fragment_results if result.cache_hit),
                "rebuilt": sum(1 for result in fragment_results if not result.cache_hit),
            },
            started_at=fragment_stage_started_at,
        )

        merge_started_at = time.perf_counter()
        progress.record(
            "global_spine_merge",
            "global_spine_merge",
            "started",
            output=global_spine_path,
            details={
                "fragment_count": len(fragment_results),
                "generate_links": generate_links,
            },
        )
        merge_result = merge_spine_fragments(
            [result.fragment_path for result in fragment_results],
            global_spine_path,
            release_id=release_id,
            source_manifest_hash=plan.source_manifest_hash,
            generate_links=generate_links,
            progress_callback=lambda event: progress.record(
                str(event.get("node_id") or "global_spine_merge"),
                str(event.get("stage") or "global_spine_merge"),
                str(event.get("status") or "progress"),
                output=event.get("output"),
                details=event.get("details") if isinstance(event.get("details"), Mapping) else None,
            ),
        )
        progress.record(
            "global_spine_merge",
            "global_spine_merge",
            "complete",
            output=global_spine_path,
            details={
                "fragment_count": merge_result.fragment_count,
                "counts": dict(merge_result.counts),
                "chain_links_inserted": merge_result.chain_links.inserted,
            },
            started_at=merge_started_at,
        )

        manifest_started_at = time.perf_counter()
        shard_manifest = _write_v3_shard_manifest(
            shard_manifest_path,
            release_root=resolved_root,
            release_id=release_id,
            source_manifest_hash=plan.source_manifest_hash,
            shard_results=shard_results,
        )
        progress.record(
            "shard_manifest",
            "manifest",
            "complete",
            output=shard_manifest_path,
            details={"ticker_count": shard_manifest.get("ticker_count")},
            started_at=manifest_started_at,
        )
        cleanup_started_at = time.perf_counter()
        fragment_cleanup = _cleanup_release_spine_fragments(
            fragments_dir,
            release_root=resolved_root,
        )
        progress.record(
            "spine_fragments",
            "artifact_cleanup",
            "complete",
            output=fragments_dir,
            details=fragment_cleanup,
            started_at=cleanup_started_at,
        )
        build_summary = {
            "format": V3_BUILD_SUMMARY_FORMAT_VERSION,
            "release_id": release_id,
            "index_layout": GLOBAL_SPINE_LAYOUT,
            "source_manifest_path": _path_label(resolved_root, resolved_source_manifest_path),
            "source_manifest_hash": plan.source_manifest_hash,
            "progress_path": _path_label(resolved_root, resolved_progress_path),
            "artifact_count": len(plan.items),
            "company_count": len(shard_results),
            "company_shard_cache": {
                "hits": sum(1 for result in shard_results if result.cache_hit),
                "misses": sum(1 for result in shard_results if not result.cache_hit),
            },
            "spine_fragment_cache": {
                "hits": sum(1 for result in fragment_results if result.cache_hit),
                "misses": sum(1 for result in fragment_results if not result.cache_hit),
            },
            "global_spine": {
                "path": _path_label(resolved_root, merge_result.global_spine_path),
                "counts": dict(merge_result.counts),
                "chain_links": {
                    "inserted": merge_result.chain_links.inserted,
                    "exact_links": merge_result.chain_links.exact_links,
                    "similarity_links": merge_result.chain_links.similarity_links,
                    "key_count": merge_result.chain_links.key_count,
                    "skipped_generic_keys": merge_result.chain_links.skipped_generic_keys,
                },
            },
            "artifact_cleanup": {
                "spine_fragments": fragment_cleanup,
            },
            "shards": dict(shard_manifest.get("shards") or {}),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        summary_started_at = time.perf_counter()
        _write_json(build_summary_path, build_summary)
        progress.record(
            "build_summary",
            "build_summary",
            "complete",
            output=build_summary_path,
            details={
                "artifact_count": len(plan.items),
                "company_count": len(shard_results),
            },
            started_at=summary_started_at,
        )
        progress.record(
            "build",
            "build",
            "complete",
            details={
                "artifact_count": len(plan.items),
                "company_count": len(shard_results),
                "global_spine": _path_label(resolved_root, global_spine_path),
            },
            started_at=build_started_at,
        )
        return SpineShardReleaseBuildResult(
            release_root=resolved_root,
            release_id=release_id,
            source_manifest_path=resolved_source_manifest_path,
            build_plan_path=build_plan_path,
            shard_manifest_path=shard_manifest_path,
            build_summary_path=build_summary_path,
            global_spine_path=global_spine_path,
            shard_results=tuple(sorted(shard_results, key=lambda result: result.ticker)),
            fragment_results=tuple(sorted(fragment_results, key=lambda result: result.ticker)),
            merge_result=merge_result,
            shard_manifest=shard_manifest,
            build_summary=build_summary,
            progress_path=resolved_progress_path,
        )
    except Exception as exc:
        progress.record("build", "build", "failed", error=str(exc), started_at=build_started_at)
        raise


def plan_spine_shard_release_outputs(
    root: Path,
    *,
    release_id: str | None = None,
    workers: int | None = None,
    cache_root: Path | None = None,
    source_manifest_path: Path | None = None,
    no_cache: bool = False,
) -> dict[str, Any]:
    """Preview the v3 release build DAG without writing release outputs."""
    resolved_root = root.expanduser().resolve()
    if not resolved_root.is_dir():
        raise FileNotFoundError(f"Release root not found: {resolved_root}")
    indexes_dir = resolved_root / "indexes"
    companies_dir = indexes_dir / "companies"
    fragments_dir = indexes_dir / "fragments" / "spine"
    global_spine_path = indexes_dir / "global_spine.sqlite"
    shard_manifest_path = indexes_dir / "shard_manifest.json"
    build_plan_path = indexes_dir / "build_plan.json"
    build_summary_path = indexes_dir / "build_summary.json"
    resolved_source_manifest_path = (
        source_manifest_path.expanduser().resolve()
        if source_manifest_path is not None
        else resolved_root / "source_manifest.json"
    )
    source_manifest = write_source_artifact_manifest(
        resolved_root,
        manifest_path=resolved_source_manifest_path,
    )
    plan = _plan_v3_artifact_inputs(
        resolved_root,
        sqlite_path=global_spine_path,
        cache_root=cache_root,
        workers=workers,
        source_manifest_path=resolved_source_manifest_path,
    )
    company_specs = _company_build_specs(
        plan,
        companies_dir=companies_dir,
        no_cache=no_cache,
    )

    companies: list[dict[str, Any]] = []
    dirty_tickers: set[str] = set()
    for spec in company_specs:
        ticker = str(spec["ticker"])
        company_plan = spec["company_plan"]
        company_item = company_plan.company_items[0]
        company_cache_hit = False if no_cache else bool(company_item.cache_hit)
        fragment_cache_key = _spine_fragment_cache_key(
            ticker=ticker,
            company_cache_key=company_item.cache_key,
            source_manifest_hash=plan.source_manifest_hash,
        )
        fragment_cache_path = _spine_fragment_cache_path(plan.cache_root, fragment_cache_key)
        fragment_cache_errors = (
            ("no_cache",)
            if no_cache
            else _verify_spine_fragment_cache(fragment_cache_path, ticker=ticker, cache_key=fragment_cache_key)
        )
        fragment_cache_hit = False if no_cache else not fragment_cache_errors
        if not company_cache_hit or not fragment_cache_hit:
            dirty_tickers.add(ticker)
        companies.append(
            {
                "ticker": ticker,
                "artifact_count": len(company_plan.items),
                "dirty_artifact_count": len(company_plan.dirty_items),
                "cached_artifact_count": len(company_plan.cached_items),
                "company_cache_hit": company_cache_hit,
                "company_cache_key": company_item.cache_key,
                "company_cache_errors": list(company_item.cache_errors if not no_cache else ("no_cache",)),
                "company_shard_path": _path_label(resolved_root, spec["shard_path"]),
                "spine_fragment_path": _path_label(resolved_root, fragments_dir / f"{_safe_ticker_filename(ticker)}.sqlite"),
                "spine_fragment_cache_hit": fragment_cache_hit,
                "spine_fragment_cache_key": fragment_cache_key,
                "spine_fragment_cache_errors": list(fragment_cache_errors),
                "estimated_cost": int(spec["estimated_cost"]),
            }
        )

    company_nodes = [
        {
            "id": f"company_shard:{row['ticker']}",
            "stage": "company_shard",
            "ticker": row["ticker"],
            "depends_on": ["source_manifest"],
            "output": row["company_shard_path"],
            "cache_key": row["company_cache_key"],
            "cache_hit": row["company_cache_hit"],
            "status": "cached" if row["company_cache_hit"] else "rebuild",
            "rebuild_reason": None if row["company_cache_hit"] else ",".join(row["company_cache_errors"] or ["cache_miss"]),
        }
        for row in companies
    ]
    fragment_nodes = [
        {
            "id": f"spine_fragment:{row['ticker']}",
            "stage": "spine_fragment",
            "ticker": row["ticker"],
            "depends_on": [f"company_shard:{row['ticker']}"],
            "output": row["spine_fragment_path"],
            "cache_key": row["spine_fragment_cache_key"],
            "cache_hit": row["spine_fragment_cache_hit"],
            "status": "cached" if row["spine_fragment_cache_hit"] else "rebuild",
            "rebuild_reason": None
            if row["spine_fragment_cache_hit"]
            else ",".join(row["spine_fragment_cache_errors"] or ["cache_miss"]),
        }
        for row in companies
    ]
    merge_dependencies = [node["id"] for node in fragment_nodes]
    nodes: list[dict[str, Any]] = [
        {
            "id": "source_manifest",
            "stage": "source_discovery",
            "depends_on": [],
            "output": _path_label(resolved_root, resolved_source_manifest_path),
            "cache_hit": False,
            "status": "planned",
        },
        *company_nodes,
        *fragment_nodes,
        {
            "id": "global_spine_merge",
            "stage": "global_spine_merge",
            "depends_on": merge_dependencies,
            "output": _path_label(resolved_root, global_spine_path),
            "cache_hit": False,
            "status": "rebuild",
        },
        {
            "id": "cross_company_links",
            "stage": "cross_company_links",
            "depends_on": ["global_spine_merge"],
            "output": _path_label(resolved_root, global_spine_path),
            "cache_hit": False,
            "status": "planned",
        },
        {
            "id": "shard_manifest",
            "stage": "manifest",
            "depends_on": [node["id"] for node in company_nodes],
            "output": _path_label(resolved_root, shard_manifest_path),
            "cache_hit": False,
            "status": "planned",
        },
        {
            "id": "release_manifest",
            "stage": "manifest",
            "depends_on": ["global_spine_merge", "shard_manifest"],
            "output": "manifest.json",
            "cache_hit": False,
            "status": "planned",
        },
        {
            "id": "verification",
            "stage": "verification",
            "depends_on": ["release_manifest", "cross_company_links"],
            "output": "verify/release_verify.json",
            "cache_hit": False,
            "status": "planned",
        },
        {
            "id": "promote_current",
            "stage": "promotion",
            "depends_on": ["verification"],
            "output": "current",
            "cache_hit": False,
            "status": "conditional",
        },
    ]
    return {
        "format": V3_BUILD_PLAN_FORMAT_VERSION,
        "release_id": release_id,
        "index_layout": GLOBAL_SPINE_LAYOUT,
        "source_manifest_path": _path_label(resolved_root, resolved_source_manifest_path),
        "source_manifest_hash": plan.source_manifest_hash,
        "source_manifest": {
            "manifest_hash": source_manifest.get("manifest_hash"),
            "artifact_count": source_manifest.get("artifact_count"),
        },
        "cache_root": str(plan.cache_root),
        "no_cache": no_cache,
        "artifact_count": len(plan.items),
        "company_count": len(companies),
        "worker_count": min(plan.workers, len(companies)) if companies else 0,
        "dirty_tickers": sorted(dirty_tickers),
        "dirty_company_count": sum(1 for row in companies if not row["company_cache_hit"]),
        "cached_company_count": sum(1 for row in companies if row["company_cache_hit"]),
        "dirty_spine_fragment_count": sum(1 for row in companies if not row["spine_fragment_cache_hit"]),
        "cached_spine_fragment_count": sum(1 for row in companies if row["spine_fragment_cache_hit"]),
        "outputs": {
            "build_plan": _path_label(resolved_root, build_plan_path),
            "build_summary": _path_label(resolved_root, build_summary_path),
            "global_spine": _path_label(resolved_root, global_spine_path),
            "shard_manifest": _path_label(resolved_root, shard_manifest_path),
            "company_shards_dir": _path_label(resolved_root, companies_dir),
            "spine_fragments_dir": _path_label(resolved_root, fragments_dir),
        },
        "nodes": nodes,
        "companies": companies,
        "plan": plan.to_dict(include_items=True),
    }


def build_company_shard_direct(
    root: Path,
    *,
    ticker: str,
    shard_path: Path,
    cache_root: Path | None = None,
    workers: int | None = None,
    source_manifest_path: Path | None = None,
    no_cache: bool = False,
) -> CompanyShardBuildResult:
    """Build one company shard directly from source artifacts without a monolith."""
    normalized_ticker = _normalize_ticker(ticker)
    resolved_root = root.expanduser().resolve()
    resolved_shard_path = shard_path.expanduser().resolve()
    full_plan = _plan_v3_artifact_inputs(
        resolved_root,
        sqlite_path=resolved_shard_path,
        cache_root=cache_root,
        workers=workers,
        source_manifest_path=source_manifest_path,
    )
    company_plan = _filter_plan_for_company(
        full_plan,
        ticker=normalized_ticker,
        shard_path=resolved_shard_path,
        no_cache=no_cache,
    )
    if not company_plan.items:
        raise ValueError(f"No artifact inputs found for ticker: {normalized_ticker}")

    return _build_company_shard_from_plan(
        resolved_root,
        ticker=normalized_ticker,
        shard_path=resolved_shard_path,
        company_plan=company_plan,
        no_cache=no_cache,
    )


def _build_company_shard_from_plan(
    root: Path,
    *,
    ticker: str,
    shard_path: Path,
    company_plan: SourceArtifactSqlitePlan,
    no_cache: bool,
) -> CompanyShardBuildResult:
    normalized_ticker = _normalize_ticker(ticker)
    resolved_root = root.expanduser().resolve()
    resolved_shard_path = shard_path.expanduser().resolve()
    if not company_plan.company_items:
        raise ValueError(f"company plan missing company item: {normalized_ticker}")
    company_item = company_plan.company_items[0]
    resolved_shard_path.parent.mkdir(parents=True, exist_ok=True)
    if not no_cache and company_item.cache_hit:
        try:
            _copy_sqlite_database(company_item.shard_cache_path, resolved_shard_path)
            _rebase_company_shard_release_paths(resolved_shard_path, release_root=resolved_root)
            verification = verify_source_artifact_sqlite(resolved_shard_path)
            if not verification["ok"]:
                errors = ", ".join(verification["errors"])
                raise RuntimeError(f"cached company shard failed verification: {normalized_ticker}: {errors}")
            return CompanyShardBuildResult(
                ticker=normalized_ticker,
                shard_path=resolved_shard_path,
                cache_hit=True,
                artifact_count=len(company_plan.items),
                totals=_company_shard_counts(resolved_shard_path),
                verification=verification,
                cache_key=company_item.cache_key,
            )
        except Exception:
            _quarantine_sqlite_cache(company_item.shard_cache_path)
            cleanup_sqlite_database_files(resolved_shard_path)

    if no_cache:
        for item in company_plan.items:
            cleanup_sqlite_database_files(item.fragment_path)

    tmp_path = temporary_sqlite_path(resolved_shard_path)
    cleanup_sqlite_database_files(tmp_path)
    try:
        result = build_source_artifact_sqlite(
            resolved_root,
            sqlite_path=tmp_path,
            published_sqlite_path=resolved_shard_path,
            plan=company_plan,
        )
        _write_company_shard_metadata(
            tmp_path,
            ticker=normalized_ticker,
            plan=company_plan,
        )
        verification = verify_source_artifact_sqlite(tmp_path)
        if not verification["ok"]:
            errors = ", ".join(verification["errors"])
            raise RuntimeError(f"company shard failed verification: {normalized_ticker}: {errors}")
        replace_sqlite_database(tmp_path, resolved_shard_path)
        _store_sqlite_database_cache(resolved_shard_path, company_item.shard_cache_path)
        return CompanyShardBuildResult(
            ticker=normalized_ticker,
            shard_path=resolved_shard_path,
            cache_hit=False,
            artifact_count=len(company_plan.items),
            totals=result.get("totals") or {},
            verification=verification,
            cache_key=company_item.cache_key,
        )
    finally:
        cleanup_sqlite_database_files(tmp_path)


def _company_build_specs(
    plan: SourceArtifactSqlitePlan,
    *,
    companies_dir: Path,
    no_cache: bool,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for company in plan.company_items:
        ticker = _normalize_ticker(company.ticker)
        shard_path = companies_dir / f"{_safe_ticker_filename(ticker)}.sqlite"
        company_plan = _filter_plan_for_company(
            plan,
            ticker=ticker,
            shard_path=shard_path,
            no_cache=no_cache,
        )
        specs.append(
            {
                "ticker": ticker,
                "shard_path": shard_path,
                "company_plan": company_plan,
                "estimated_cost": _company_build_cost(company_plan),
            }
        )
    return sorted(specs, key=lambda spec: (-int(spec["estimated_cost"]), str(spec["ticker"])))


def _plan_spine_fragment_cache_hit(
    spec: Mapping[str, Any],
    *,
    release_id: str,
    source_manifest_hash: str | None,
    cache_root: Path,
    no_cache: bool,
) -> bool:
    if no_cache:
        return False
    ticker = str(spec["ticker"])
    company_plan = spec["company_plan"]
    if not company_plan.company_items:
        return False
    company_item = company_plan.company_items[0]
    cache_key = _spine_fragment_cache_key(
        ticker=ticker,
        company_cache_key=company_item.cache_key,
        source_manifest_hash=source_manifest_hash,
    )
    return not _verify_spine_fragment_cache(
        _spine_fragment_cache_path(cache_root, cache_key),
        ticker=ticker,
        cache_key=cache_key,
    )


def _build_company_shards_for_release(
    root: Path,
    *,
    company_specs: Sequence[Mapping[str, Any]],
    workers: int,
    no_cache: bool,
    progress: _BuildProgressWriter | None = None,
) -> list[CompanyShardBuildResult]:
    if not company_specs:
        return []
    worker_count = max(1, min(int(workers), len(company_specs)))
    total = len(company_specs)
    if worker_count <= 1:
        results: list[CompanyShardBuildResult] = []
        for spec in company_specs:
            ticker = str(spec["ticker"])
            shard_path = Path(spec["shard_path"])
            company_plan = spec["company_plan"]
            started_at = time.perf_counter()
            if progress:
                progress.record(
                    f"company_shard:{ticker}",
                    "company_shard",
                    "started",
                    ticker=ticker,
                    output=shard_path,
                    cache_hit=False if no_cache else bool(company_plan.company_items[0].cache_hit),
                    details={"completed": len(results), "total": total},
                )
            try:
                result = _build_company_shard_from_plan(
                    root,
                    ticker=ticker,
                    shard_path=shard_path,
                    company_plan=company_plan,
                    no_cache=no_cache,
                )
            except Exception as exc:
                if progress:
                    progress.record(
                        f"company_shard:{ticker}",
                        "company_shard",
                        "failed",
                        ticker=ticker,
                        output=shard_path,
                        error=str(exc),
                        details={"completed": len(results), "total": total},
                        started_at=started_at,
                    )
                raise
            results.append(result)
            if progress:
                progress.record(
                    f"company_shard:{ticker}",
                    "company_shard",
                    "cached" if result.cache_hit else "rebuilt",
                    ticker=ticker,
                    output=result.shard_path,
                    cache_hit=result.cache_hit,
                    details={
                        "completed": len(results),
                        "total": total,
                        "artifact_count": result.artifact_count,
                        "cache_key": result.cache_key,
                    },
                    started_at=started_at,
                )
        return results
    results: list[CompanyShardBuildResult] = []
    try:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {}
            for spec in company_specs:
                ticker = str(spec["ticker"])
                shard_path = Path(spec["shard_path"])
                company_plan = spec["company_plan"]
                started_at = time.perf_counter()
                if progress:
                    progress.record(
                        f"company_shard:{ticker}",
                        "company_shard",
                        "started",
                        ticker=ticker,
                        output=shard_path,
                        cache_hit=False if no_cache else bool(company_plan.company_items[0].cache_hit),
                        details={"completed": len(results), "total": total},
                    )
                future = executor.submit(
                    _build_company_shard_from_plan,
                    root,
                    ticker=ticker,
                    shard_path=shard_path,
                    company_plan=company_plan,
                    no_cache=no_cache,
                )
                futures[future] = (ticker, shard_path, started_at)
            for future in as_completed(futures):
                ticker, shard_path, started_at = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    if progress:
                        progress.record(
                            f"company_shard:{ticker}",
                            "company_shard",
                            "failed",
                            ticker=ticker,
                            output=shard_path,
                            error=str(exc),
                            details={"completed": len(results), "total": total},
                            started_at=started_at,
                        )
                    raise
                results.append(result)
                if progress:
                    progress.record(
                        f"company_shard:{ticker}",
                        "company_shard",
                        "cached" if result.cache_hit else "rebuilt",
                        ticker=ticker,
                        output=result.shard_path,
                        cache_hit=result.cache_hit,
                        details={
                            "completed": len(results),
                            "total": total,
                            "artifact_count": result.artifact_count,
                            "cache_key": result.cache_key,
                        },
                        started_at=started_at,
                    )
    except BrokenProcessPool as exc:
        if progress:
            progress.record(
                "company_shards",
                "company_shard",
                "fallback_sequential",
                error=str(exc),
                details={"workers": worker_count, "total": total},
            )
        return _build_company_shards_for_release(
            root,
            company_specs=company_specs,
            workers=1,
            no_cache=no_cache,
            progress=progress,
        )
    return sorted(results, key=lambda result: result.ticker)


def _emit_spine_fragments_for_release(
    shard_results: Sequence[CompanyShardBuildResult],
    *,
    fragments_dir: Path,
    release_id: str,
    source_manifest_hash: str | None,
    cache_root: Path,
    workers: int,
    no_cache: bool,
    progress: _BuildProgressWriter | None = None,
) -> list[SpineFragmentResult]:
    if not shard_results:
        return []
    worker_count = max(1, min(int(workers), len(shard_results)))
    tasks = [
        {
            "shard_result": result,
            "fragment_path": fragments_dir / f"{_safe_ticker_filename(result.ticker)}.sqlite",
            "release_root": fragments_dir.parent.parent.parent,
            "release_id": release_id,
            "shard_path_in_release": f"indexes/companies/{_safe_ticker_filename(result.ticker)}.sqlite",
            "source_manifest_hash": source_manifest_hash,
            "cache_root": cache_root,
            "no_cache": no_cache,
        }
        for result in sorted(shard_results, key=lambda item: item.ticker)
    ]
    total = len(tasks)
    if worker_count <= 1:
        results: list[SpineFragmentResult] = []
        for task in tasks:
            shard_result = task["shard_result"]
            ticker = shard_result.ticker
            fragment_path = Path(task["fragment_path"])
            started_at = time.perf_counter()
            if progress:
                progress.record(
                    f"spine_fragment:{ticker}",
                    "spine_fragment",
                    "started",
                    ticker=ticker,
                    output=fragment_path,
                    cache_hit=False if no_cache else None,
                    details={"completed": len(results), "total": total},
                )
            try:
                result = _emit_spine_fragment_cached(**task)
            except Exception as exc:
                if progress:
                    progress.record(
                        f"spine_fragment:{ticker}",
                        "spine_fragment",
                        "failed",
                        ticker=ticker,
                        output=fragment_path,
                        error=str(exc),
                        details={"completed": len(results), "total": total},
                        started_at=started_at,
                    )
                raise
            results.append(result)
            if progress:
                progress.record(
                    f"spine_fragment:{ticker}",
                    "spine_fragment",
                    "cached" if result.cache_hit else "rebuilt",
                    ticker=ticker,
                    output=result.fragment_path,
                    cache_hit=result.cache_hit,
                    details={
                        "completed": len(results),
                        "total": total,
                        "cache_key": result.cache_key,
                        "counts": dict(result.counts),
                    },
                    started_at=started_at,
                )
        return results
    results: list[SpineFragmentResult] = []
    try:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {}
            for task in tasks:
                shard_result = task["shard_result"]
                ticker = shard_result.ticker
                fragment_path = Path(task["fragment_path"])
                started_at = time.perf_counter()
                if progress:
                    progress.record(
                        f"spine_fragment:{ticker}",
                        "spine_fragment",
                        "started",
                        ticker=ticker,
                        output=fragment_path,
                        cache_hit=False if no_cache else None,
                        details={"completed": len(results), "total": total},
                    )
                futures[executor.submit(_emit_spine_fragment_cached, **task)] = (ticker, fragment_path, started_at)
            for future in as_completed(futures):
                ticker, fragment_path, started_at = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    if progress:
                        progress.record(
                            f"spine_fragment:{ticker}",
                            "spine_fragment",
                            "failed",
                            ticker=ticker,
                            output=fragment_path,
                            error=str(exc),
                            details={"completed": len(results), "total": total},
                            started_at=started_at,
                        )
                    raise
                results.append(result)
                if progress:
                    progress.record(
                        f"spine_fragment:{ticker}",
                        "spine_fragment",
                        "cached" if result.cache_hit else "rebuilt",
                        ticker=ticker,
                        output=result.fragment_path,
                        cache_hit=result.cache_hit,
                        details={
                            "completed": len(results),
                            "total": total,
                            "cache_key": result.cache_key,
                            "counts": dict(result.counts),
                        },
                        started_at=started_at,
                    )
    except BrokenProcessPool as exc:
        if progress:
            progress.record(
                "spine_fragments",
                "spine_fragment",
                "fallback_sequential",
                error=str(exc),
                details={"workers": worker_count, "total": total},
            )
        return _emit_spine_fragments_for_release(
            shard_results,
            fragments_dir=fragments_dir,
            release_id=release_id,
            source_manifest_hash=source_manifest_hash,
            cache_root=cache_root,
            workers=1,
            no_cache=no_cache,
            progress=progress,
        )
    return sorted(results, key=lambda result: result.ticker)


def _write_v3_shard_manifest(
    path: Path,
    *,
    release_root: Path,
    release_id: str,
    source_manifest_hash: str | None,
    shard_results: Sequence[CompanyShardBuildResult],
) -> dict[str, Any]:
    shards: dict[str, Any] = {}
    for result in sorted(shard_results, key=lambda item: item.ticker):
        counts = _company_shard_counts(result.shard_path)
        shards[result.ticker] = {
            "ticker": result.ticker,
            "path": _path_label(release_root / "indexes", result.shard_path),
            "schema_version": COMPANY_SHARD_SCHEMA_VERSION,
            "document_count": counts.get("documents", 0),
            "object_count": counts.get("objects", 0),
            "edge_count": counts.get("edges", 0),
            "quality_event_count": counts.get("quality_events", 0),
            "row_counts": counts,
            "quality_summary": _company_shard_quality_summary(result.shard_path),
            "sha256": _file_sha256(result.shard_path),
            "cache_hit": result.cache_hit,
            "cache_key": result.cache_key,
        }
    payload = {
        "format": V3_SHARD_MANIFEST_FORMAT_VERSION,
        "index_layout": GLOBAL_SPINE_LAYOUT,
        "release_id": release_id,
        "source_manifest_hash": source_manifest_hash,
        "companies_dir": "companies",
        "ticker_count": len(shards),
        "shards": shards,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(path, payload)
    return payload


def merge_spine_fragments(
    fragments: Sequence[Path],
    global_spine_path: Path,
    *,
    release_id: str | None = None,
    source_manifest_hash: str | None = None,
    replace: bool = True,
    generate_links: bool = True,
    created_at: str | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> GlobalSpineMergeResult:
    """Merge per-company spine fragments into one deterministic global spine."""
    resolved_fragments = tuple(sorted((path.expanduser().resolve() for path in fragments), key=lambda path: path.name))
    if not resolved_fragments:
        raise ValueError("at least one spine fragment is required")
    resolved_output = global_spine_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = resolved_output.parent / f".{resolved_output.name}.{os.getpid()}.{time.time_ns()}.tmp"
    cleanup_sqlite_database_files(tmp_path)
    if replace:
        cleanup_sqlite_database_files(resolved_output)
    try:
        with sqlite3.connect(tmp_path) as conn:
            conn.row_factory = sqlite3.Row
            create_global_spine_schema(conn)
            write_global_spine_metadata(
                conn,
                {
                    "builder_version": GLOBAL_SPINE_BUILDER_VERSION,
                    "index_layout": GLOBAL_SPINE_LAYOUT,
                    "schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
                    "release_id": release_id,
                    "source_manifest_hash": source_manifest_hash,
                    "spine_projection_version": SPINE_PROJECTION_VERSION,
                    "fragment_count": len(resolved_fragments),
                    "created_at": created_at or datetime.now(timezone.utc).isoformat(),
                },
            )
            for index, fragment in enumerate(resolved_fragments, start=1):
                if progress_callback:
                    progress_callback(
                        {
                            "node_id": f"global_spine_merge:{fragment.stem}",
                            "stage": "global_spine_merge",
                            "status": "started",
                            "output": resolved_output,
                            "details": {
                                "completed": index - 1,
                                "total": len(resolved_fragments),
                                "fragment": fragment.name,
                            },
                        }
                    )
                verification = verify_global_spine_schema(fragment)
                if not verification["ok"]:
                    errors = ", ".join(verification["errors"])
                    raise RuntimeError(f"spine fragment failed verification: {fragment}: {errors}")
                schema_name = f"frag_{index}"
                conn.execute(f"ATTACH DATABASE ? AS {schema_name}", (str(fragment),))
                try:
                    for table_name in _SPINE_FRAGMENT_MERGE_TABLES:
                        _merge_spine_table(conn, schema_name, table_name)
                    conn.commit()
                finally:
                    conn.execute(f"DETACH DATABASE {schema_name}")
                if progress_callback:
                    progress_callback(
                        {
                            "node_id": f"global_spine_merge:{fragment.stem}",
                            "stage": "global_spine_merge",
                            "status": "merged",
                            "output": resolved_output,
                            "details": {
                                "completed": index,
                                "total": len(resolved_fragments),
                                "fragment": fragment.name,
                            },
                        }
                    )
            if progress_callback:
                progress_callback(
                    {
                        "node_id": "global_chain_links",
                        "stage": "global_spine_merge",
                        "status": "started",
                        "output": resolved_output,
                        "details": {"generate_links": generate_links},
                    }
                )
            chain_result = (
                generate_cross_company_links(conn, replace=True)
                if generate_links
                else CrossCompanyLinkGenerationResult(
                    inserted=0,
                    exact_links=0,
                    similarity_links=0,
                    key_count=_count_table(conn, "global_key_stats"),
                    skipped_generic_keys=0,
                )
            )
            if progress_callback:
                progress_callback(
                    {
                        "node_id": "global_chain_links",
                        "stage": "global_spine_merge",
                        "status": "complete",
                        "output": resolved_output,
                        "details": {
                            "inserted": chain_result.inserted,
                            "exact_links": chain_result.exact_links,
                            "similarity_links": chain_result.similarity_links,
                        },
                    }
                )
            counts = {
                table_name: _count_table(conn, table_name)
                for table_name in GLOBAL_SPINE_TABLES
                if table_name != "metadata"
            }
            write_global_spine_metadata(
                conn,
                {
                    "builder_version": GLOBAL_SPINE_BUILDER_VERSION,
                    "index_layout": GLOBAL_SPINE_LAYOUT,
                    "schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
                    "release_id": release_id,
                    "source_manifest_hash": source_manifest_hash,
                    "spine_projection_version": SPINE_PROJECTION_VERSION,
                    "fragment_count": len(resolved_fragments),
                    "counts": counts,
                    "chain_links": {
                        "inserted": chain_result.inserted,
                        "exact_links": chain_result.exact_links,
                        "similarity_links": chain_result.similarity_links,
                        "key_count": chain_result.key_count,
                        "skipped_generic_keys": chain_result.skipped_generic_keys,
                    },
                    "created_at": created_at or datetime.now(timezone.utc).isoformat(),
                },
            )
            conn.commit()
            conn.execute("PRAGMA optimize")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            conn.execute("PRAGMA journal_mode=DELETE").fetchall()
        replace_sqlite_database(tmp_path, resolved_output)
        verification = verify_global_spine_schema(resolved_output)
        if not verification["ok"]:
            errors = ", ".join(verification["errors"])
            raise RuntimeError(f"merged global spine failed verification: {errors}")
        return GlobalSpineMergeResult(
            global_spine_path=resolved_output,
            fragment_count=len(resolved_fragments),
            counts=counts,
            chain_links=chain_result,
            verification=verification,
        )
    finally:
        cleanup_sqlite_database_files(tmp_path)


def emit_spine_fragment_from_company_shard(
    shard_path: Path,
    fragment_path: Path,
    *,
    ticker: str,
    release_id: str | None = None,
    shard_path_in_release: str | None = None,
    cache_key: str | None = None,
    company_source_hash: str | None = None,
    source_manifest_hash: str | None = None,
    replace: bool = True,
) -> SpineFragmentResult:
    """Project one company shard into a compact global spine fragment."""
    normalized_ticker = _normalize_ticker(ticker)
    resolved_shard_path = shard_path.expanduser().resolve()
    resolved_fragment_path = fragment_path.expanduser().resolve()
    if not resolved_shard_path.is_file():
        raise FileNotFoundError(f"Company shard not found: {resolved_shard_path}")
    resolved_fragment_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = resolved_fragment_path.parent / f".{resolved_fragment_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    cleanup_sqlite_database_files(tmp_path)
    if replace:
        cleanup_sqlite_database_files(resolved_fragment_path)
    release_shard_path = shard_path_in_release or f"indexes/companies/{normalized_ticker}.sqlite"
    counts: dict[str, int] = defaultdict(int)
    try:
        with sqlite3.connect(resolved_shard_path) as source, sqlite3.connect(tmp_path) as target:
            source.row_factory = sqlite3.Row
            target.row_factory = sqlite3.Row
            create_global_spine_schema(target)
            write_global_spine_metadata(
                target,
                {
                    "format": SPINE_FRAGMENT_FORMAT_VERSION,
                    "builder_version": GLOBAL_SPINE_BUILDER_VERSION,
                    "index_layout": GLOBAL_SPINE_LAYOUT,
                    "schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
                    "spine_projection_version": SPINE_PROJECTION_VERSION,
                    "release_id": release_id,
                    "ticker": normalized_ticker,
                    "source_shard_path": str(resolved_shard_path),
                    "shard_path": release_shard_path,
                    "spine_fragment_cache_format_version": SPINE_FRAGMENT_CACHE_FORMAT_VERSION,
                    "spine_fragment_cache_key": cache_key,
                    "company_source_hash": company_source_hash,
                    "source_manifest_hash": source_manifest_hash,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            object_rows = _source_rows(source, "objects")
            object_ticker = {str(row["id"]): str(row["ticker"] or normalized_ticker).upper() for row in object_rows}
            search_rows = _rows_by_key(_source_rows(source, "object_search_text"), "object_id")
            document_rows = _source_rows(source, "documents")
            counts["global_document_catalog"] = _emit_document_catalog(
                target,
                document_rows,
                ticker=normalized_ticker,
                shard_path=release_shard_path,
            )
            counts["global_object_locator"] = _emit_object_locator_and_search(
                target,
                object_rows,
                search_rows=search_rows,
                ticker=normalized_ticker,
                shard_path=release_shard_path,
            )
            counts["global_edge_spine"] = _emit_edge_spine(
                target,
                _source_rows(source, "edges"),
                object_ticker=object_ticker,
                ticker=normalized_ticker,
                shard_path=release_shard_path,
            )
            counts["global_factor_spine"] = _emit_factor_spine(target, _source_rows(source, "factor_lookup"))
            counts["global_topic_spine"] = _emit_topic_spine(target, _source_rows(source, "company_topic_index"))
            counts["global_metric_spine"] = _emit_metric_spine(target, _source_rows(source, "metric_lookup"))
            counts["global_counterparty_spine"] = _emit_counterparty_spine(
                target,
                _source_rows(source, "agreement_lookup"),
            )
            counts["global_entity_spine"] = _emit_entity_spine_from_topics(
                target,
                _source_rows(source, "company_topic_index"),
            )
            counts["global_key_stats"] = _refresh_key_stats(target)
            target.execute("PRAGMA optimize")
        replace_sqlite_database(tmp_path, resolved_fragment_path)
        return SpineFragmentResult(
            ticker=normalized_ticker,
            fragment_path=resolved_fragment_path,
            shard_path=resolved_shard_path,
            counts=dict(counts),
            cache_hit=False,
            cache_key=cache_key,
        )
    finally:
        cleanup_sqlite_database_files(tmp_path)


def _emit_spine_fragment_cached(
    *,
    shard_result: CompanyShardBuildResult,
    fragment_path: Path,
    release_root: Path,
    release_id: str,
    shard_path_in_release: str,
    source_manifest_hash: str | None,
    cache_root: Path,
    no_cache: bool,
) -> SpineFragmentResult:
    cache_key = _spine_fragment_cache_key(
        ticker=shard_result.ticker,
        company_cache_key=shard_result.cache_key,
        source_manifest_hash=source_manifest_hash,
    )
    cache_path = _spine_fragment_cache_path(cache_root, cache_key)
    if not no_cache and not _verify_spine_fragment_cache(
        cache_path,
        ticker=shard_result.ticker,
        cache_key=cache_key,
    ):
        try:
            _copy_sqlite_database(cache_path, fragment_path)
            _rebase_spine_fragment_release_paths(
                fragment_path,
                release_root=release_root,
                release_id=release_id,
                source_shard_path=shard_result.shard_path,
                shard_path_in_release=shard_path_in_release,
            )
            verification = verify_global_spine_schema(fragment_path)
            if not verification["ok"]:
                errors = ", ".join(verification["errors"])
                raise RuntimeError(f"cached spine fragment failed verification: {shard_result.ticker}: {errors}")
            counts = _spine_counts(fragment_path)
            return SpineFragmentResult(
                ticker=shard_result.ticker,
                fragment_path=fragment_path.expanduser().resolve(),
                shard_path=shard_result.shard_path,
                counts=counts,
                cache_hit=True,
                cache_key=cache_key,
            )
        except Exception:
            _quarantine_sqlite_cache(cache_path)
            cleanup_sqlite_database_files(fragment_path)

    result = emit_spine_fragment_from_company_shard(
        shard_result.shard_path,
        fragment_path,
        ticker=shard_result.ticker,
        release_id=release_id,
        shard_path_in_release=shard_path_in_release,
        cache_key=cache_key,
        company_source_hash=shard_result.cache_key,
        source_manifest_hash=source_manifest_hash,
    )
    _store_sqlite_database_cache(result.fragment_path, cache_path)
    return result


def _spine_fragment_cache_key(
    *,
    ticker: str,
    company_cache_key: str | None,
    source_manifest_hash: str | None,
) -> str:
    return _stable_hash(
        {
            "spine_fragment_cache_format_version": SPINE_FRAGMENT_CACHE_FORMAT_VERSION,
            "spine_projection_version": SPINE_PROJECTION_VERSION,
            "global_spine_schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
            "global_spine_builder_version": GLOBAL_SPINE_BUILDER_VERSION,
            "ticker": ticker,
            "company_cache_key": company_cache_key,
            "source_manifest_hash": source_manifest_hash,
        }
    )


def _spine_fragment_cache_path(cache_root: Path, cache_key: str) -> Path:
    digest = cache_key.split(":", 1)[-1]
    return cache_root / "v3" / "spine_fragments" / digest[:2] / f"{digest}.sqlite"


def _verify_spine_fragment_cache(path: Path, *, ticker: str, cache_key: str) -> tuple[str, ...]:
    if not path.exists():
        return ("spine_fragment_cache_missing",)
    verification = verify_global_spine_schema(path)
    errors = [f"spine_fragment_cache:{error}" for error in verification.get("errors") or []]
    try:
        with sqlite3.connect(path) as conn:
            metadata = read_global_spine_metadata(conn)
    except sqlite3.Error as exc:
        errors.append(f"spine_fragment_cache_metadata_error:{exc}")
        metadata = {}
    if metadata.get("format") != SPINE_FRAGMENT_FORMAT_VERSION:
        errors.append("spine_fragment_cache_format_mismatch")
    if metadata.get("ticker") != ticker:
        errors.append("spine_fragment_cache_ticker_mismatch")
    if metadata.get("spine_fragment_cache_format_version") != SPINE_FRAGMENT_CACHE_FORMAT_VERSION:
        errors.append("spine_fragment_cache_metadata_format_mismatch")
    if metadata.get("spine_fragment_cache_key") != cache_key:
        errors.append("spine_fragment_cache_key_mismatch")
    return tuple(errors)


def _spine_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as conn:
        return {
            table_name: _count_table(conn, table_name)
            for table_name in GLOBAL_SPINE_TABLES
            if table_name != "metadata"
        }


_SPINE_FRAGMENT_MERGE_TABLES = tuple(
    table
    for table in GLOBAL_SPINE_TABLES
    if table not in {"metadata", "global_key_stats", "global_chain_index"}
)

_SPINE_REPLACE_TABLES = frozenset(
    {
        "global_object_locator",
        "global_document_catalog",
        "global_edge_spine",
        "global_topic_spine",
    }
)


def _merge_spine_table(conn: sqlite3.Connection, schema_name: str, table_name: str) -> None:
    columns = _spine_table_columns(conn, "main", table_name)
    if not columns:
        return
    column_sql = ", ".join(columns)
    verb = "INSERT OR REPLACE" if table_name in _SPINE_REPLACE_TABLES else "INSERT"
    conn.execute(
        f"""
        {verb} INTO {table_name}({column_sql})
        SELECT {column_sql}
        FROM {schema_name}.{table_name}
        """
    )


def _spine_table_columns(conn: sqlite3.Connection, schema_name: str, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA {schema_name}.table_info({table_name})").fetchall()
    return [str(row[1]) for row in rows]


def _count_table(conn: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _plan_v3_artifact_inputs(
    root: Path,
    *,
    sqlite_path: Path,
    cache_root: Path | None,
    workers: int | None,
    source_manifest_path: Path | None,
) -> SourceArtifactSqlitePlan:
    """Build the source-artifact plan used to materialize v3 shards.

    The lower-level planner still owns artifact discovery, content hashing, and
    fragment cache keys. v3 always requests shard layout and supplies an
    explicit output SQLite path so the production builder never depends on the
    legacy monolith default path.
    """
    return plan_source_artifact_sqlite_inputs(
        root,
        sqlite_path=sqlite_path,
        cache_root=cache_root,
        workers=workers,
        source_manifest_path=source_manifest_path,
    )


def _filter_plan_for_company(
    plan: SourceArtifactSqlitePlan,
    *,
    ticker: str,
    shard_path: Path,
    no_cache: bool,
) -> SourceArtifactSqlitePlan:
    items = tuple(item for item in plan.items if item.ticker == ticker)
    dirty_items = items if no_cache else tuple(item for item in items if not item.cache_hit)
    cached_items = () if no_cache else tuple(item for item in items if item.cache_hit)
    artifact_cache_keys = tuple(
        f"{item.relative_path}={item.cache_key}" for item in sorted(items, key=lambda item: item.relative_path)
    )
    input_hash = _stable_hash(
        {
            "ticker": ticker,
            "artifact_cache_keys": artifact_cache_keys,
            "company_shard_schema_version": COMPANY_SHARD_SCHEMA_VERSION,
            "company_shard_cache_format_version": COMPANY_SHARD_CACHE_FORMAT_VERSION,
            "index_layout": GLOBAL_SPINE_LAYOUT,
        }
    )
    cache_key = _stable_hash(
        {
            "company_shard_cache_format_version": COMPANY_SHARD_CACHE_FORMAT_VERSION,
            "input_hash": input_hash,
        }
    )
    cache_path = _v3_company_shard_cache_path(plan.cache_root, cache_key)
    cache_errors = _verify_company_shard_cache_v3(cache_path, ticker=ticker, cache_key=cache_key)
    build_settings = dict(plan.build_settings)
    build_settings["progress_log_path"] = str(
        plan.root
        / "indexes"
        / "progress"
        / "source_artifact_sqlite"
        / f"{_safe_ticker_filename(ticker)}.jsonl"
    )
    company_item = SourceCompanyShardPlanItem(
        ticker=ticker,
        artifact_count=len(items),
        artifact_cache_keys=artifact_cache_keys,
        input_hash=input_hash,
        cache_key=cache_key,
        shard_cache_path=cache_path,
        cache_hit=False if no_cache else not cache_errors,
        cache_errors=() if no_cache else cache_errors,
    )
    company_cache_hit = False if no_cache else not cache_errors
    return SourceArtifactSqlitePlan(
        root=plan.root,
        index_path=shard_path,
        cache_root=plan.cache_root,
        artifact_manifest_path=shard_path.parent / "artifact_manifest.json",
        source_manifest_path=plan.source_manifest_path,
        source_manifest_hash=plan.source_manifest_hash,
        discovery_mode=plan.discovery_mode,
        items=items,
        dirty_items=dirty_items,
        cached_items=cached_items,
        company_items=(company_item,),
        dirty_company_items=(company_item,) if not company_cache_hit else (),
        cached_company_items=(company_item,) if company_cache_hit else (),
        dirty_tickers=() if company_cache_hit else (ticker,),
        workers=plan.workers,
        layout="shards",
        build_settings=build_settings,
        builder_code_version=plan.builder_code_version,
        plan_format_version=plan.plan_format_version,
    )


def _write_company_shard_metadata(path: Path, *, ticker: str, plan: SourceArtifactSqlitePlan) -> None:
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()
        metadata: dict[str, Any] = {}
        if row is not None:
            try:
                metadata = json.loads(str(row[0]))
            except json.JSONDecodeError:
                metadata = {}
        metadata.update(
            {
                "schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
                "agent_index_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
                "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
                "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
                "company_shard_schema_version": COMPANY_SHARD_SCHEMA_VERSION,
                "index_layout": GLOBAL_SPINE_LAYOUT,
                "index_role": "company_shard",
                "shard_ticker": ticker,
                "company_cache_format_version": COMPANY_SHARD_CACHE_FORMAT_VERSION,
                "company_cache_key": plan.company_items[0].cache_key if plan.company_items else None,
                "company_source_hash": _stable_hash(
                    {
                        "ticker": ticker,
                        "artifact_cache_keys": [
                            f"{item.relative_path}={item.cache_key}"
                            for item in sorted(plan.items, key=lambda item: item.relative_path)
                        ],
                    }
                ),
                "spine_projection_version": SPINE_PROJECTION_VERSION,
            }
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('build', ?)",
            (json.dumps(metadata, sort_keys=True),),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        conn.execute("PRAGMA journal_mode=DELETE").fetchall()


def _v3_company_shard_cache_path(cache_root: Path, cache_key: str) -> Path:
    digest = cache_key.split(":", 1)[-1]
    return cache_root / "v3" / "company_shards" / digest[:2] / f"{digest}.sqlite"


def _verify_company_shard_cache_v3(
    shard_path: Path,
    *,
    ticker: str,
    cache_key: str,
) -> tuple[str, ...]:
    if not shard_path.exists():
        return ("company_cache_missing",)
    verification = verify_source_artifact_sqlite(shard_path)
    errors = [f"company_cache:{error}" for error in verification.get("errors") or []]
    try:
        with sqlite3.connect(shard_path) as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()
            metadata = json.loads(str(row[0])) if row is not None else {}
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        errors.append(f"company_cache_metadata_error:{exc}")
        metadata = {}
    if metadata.get("index_role") != "company_shard":
        errors.append("company_cache_metadata_role_mismatch")
    if str(metadata.get("shard_ticker") or "") != ticker:
        errors.append("company_cache_metadata_ticker_mismatch")
    if metadata.get("index_layout") != GLOBAL_SPINE_LAYOUT:
        errors.append("company_cache_metadata_layout_mismatch")
    if metadata.get("company_shard_schema_version") != COMPANY_SHARD_SCHEMA_VERSION:
        errors.append("company_cache_metadata_schema_mismatch")
    if metadata.get("company_cache_format_version") != COMPANY_SHARD_CACHE_FORMAT_VERSION:
        errors.append("company_cache_metadata_format_mismatch")
    if metadata.get("company_cache_key") != cache_key:
        errors.append("company_cache_metadata_key_mismatch")
    return tuple(errors)


def _copy_sqlite_database(source_path: Path, target_path: Path) -> None:
    cleanup_sqlite_database_files(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)


def _rebase_company_shard_release_paths(sqlite_path: Path, *, release_root: Path) -> None:
    resolved_root = release_root.expanduser().resolve()
    with sqlite3.connect(sqlite_path) as conn:
        _rebase_table_path_columns(
            conn,
            resolved_root,
            {
                "documents": ("artifact_index_path", "ontology_dir"),
                "objects": ("artifact_path",),
                "edges": ("artifact_path",),
            },
        )
        _rebase_metadata_json_values(conn, resolved_root)
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        conn.execute("PRAGMA journal_mode=DELETE").fetchall()


def _rebase_spine_fragment_release_paths(
    sqlite_path: Path,
    *,
    release_root: Path,
    release_id: str,
    source_shard_path: Path,
    shard_path_in_release: str,
) -> None:
    resolved_root = release_root.expanduser().resolve()
    with sqlite3.connect(sqlite_path) as conn:
        _rebase_table_path_columns(
            conn,
            resolved_root,
            {
                "global_document_catalog": ("source_path",),
            },
        )
        _rebase_metadata_json_values(
            conn,
            resolved_root,
            overrides={
                "release_id": release_id,
                "source_shard_path": str(source_shard_path.expanduser().resolve()),
                "shard_path": shard_path_in_release,
            },
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        conn.execute("PRAGMA journal_mode=DELETE").fetchall()


def _rebase_table_path_columns(
    conn: sqlite3.Connection,
    release_root: Path,
    table_columns: Mapping[str, Sequence[str]],
) -> None:
    for table_name, column_names in table_columns.items():
        if not _table_exists(conn, table_name):
            continue
        columns = _table_columns(conn, table_name)
        for column_name in column_names:
            if column_name not in columns:
                continue
            quoted_table = _quote_identifier(table_name)
            quoted_column = _quote_identifier(column_name)
            rows = conn.execute(
                f"SELECT rowid, {quoted_column} AS value FROM {quoted_table} WHERE {quoted_column} IS NOT NULL"
            ).fetchall()
            updates = []
            for rowid, raw_value in rows:
                if not isinstance(raw_value, str):
                    continue
                rebased = _rebase_release_path_string(raw_value, release_root)
                if rebased != raw_value:
                    updates.append((rebased, rowid))
            if updates:
                conn.executemany(
                    f"UPDATE {quoted_table} SET {quoted_column} = ? WHERE rowid = ?",
                    updates,
                )


def _rebase_metadata_json_values(
    conn: sqlite3.Connection,
    release_root: Path,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> None:
    if not _table_exists(conn, "metadata"):
        return
    columns = _table_columns(conn, "metadata")
    value_column = "value_json" if "value_json" in columns else "value" if "value" in columns else None
    if value_column is None:
        return
    key_column = "key" if "key" in columns else None
    quoted_value_column = _quote_identifier(value_column)
    if key_column is None:
        rows = [(row[0], None, row[1]) for row in conn.execute(f"SELECT rowid, {quoted_value_column} FROM metadata")]
    else:
        quoted_key_column = _quote_identifier(key_column)
        rows = conn.execute(
            f"SELECT rowid, {quoted_key_column}, {quoted_value_column} FROM metadata"
        ).fetchall()
    updates = []
    for rowid, key, raw_value in rows:
        if not isinstance(raw_value, str):
            continue
        override_value = overrides.get(str(key)) if overrides and key is not None else None
        if override_value is not None:
            encoded = json.dumps(override_value, sort_keys=True)
            if encoded != raw_value:
                updates.append((encoded, rowid))
            continue
        try:
            decoded = json.loads(raw_value)
        except json.JSONDecodeError:
            rebased_text = _rebase_release_path_string(raw_value, release_root)
            if rebased_text != raw_value:
                updates.append((rebased_text, rowid))
            continue
        rebased = _rebase_release_paths(decoded, release_root)
        if isinstance(rebased, dict) and overrides:
            rebased.update(dict(overrides))
        encoded = json.dumps(rebased, sort_keys=True)
        if encoded != raw_value:
            updates.append((encoded, rowid))
    if updates:
        conn.executemany(
            f"UPDATE metadata SET {quoted_value_column} = ? WHERE rowid = ?",
            updates,
        )


def _rebase_release_paths(value: Any, release_root: Path) -> Any:
    if isinstance(value, str):
        return _rebase_release_path_string(value, release_root)
    if isinstance(value, list):
        return [_rebase_release_paths(item, release_root) for item in value]
    if isinstance(value, dict):
        return {key: _rebase_release_paths(item, release_root) for key, item in value.items()}
    return value


def _rebase_release_path_string(value: str, release_root: Path) -> str:
    if not value or not Path(value).is_absolute():
        return value
    path = Path(value)
    parts = path.parts
    for index in range(len(parts) - 2):
        if parts[index] != "releases":
            continue
        suffix = parts[index + 3 :]
        return str(release_root.joinpath(*suffix)) if suffix else str(release_root)
    for marker in ("companies", "indexes"):
        if marker in parts:
            index = parts.index(marker)
            return str(release_root.joinpath(*parts[index:]))
    return value


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _store_sqlite_database_cache(source_path: Path, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.parent / f".{cache_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    cleanup_sqlite_database_files(tmp_path)
    try:
        shutil.copy2(source_path, tmp_path)
        os.replace(tmp_path, cache_path)
    finally:
        cleanup_sqlite_database_files(tmp_path)


def _quarantine_sqlite_cache(cache_path: Path) -> Path | None:
    if not cache_path.exists():
        return None
    target = cache_path.with_name(f".corrupt-{cache_path.name}.{os.getpid()}.{time.time_ns()}")
    cleanup_sqlite_database_files(target)
    try:
        os.replace(cache_path, target)
    except OSError:
        cleanup_sqlite_database_files(cache_path)
        return None
    return target


def _company_shard_counts(path: Path) -> dict[str, int]:
    tables = ("documents", "objects", "edges", "quality_events")
    with sqlite3.connect(path) as conn:
        return {table: _count_table(conn, table) for table in tables}


def _company_shard_quality_summary(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        totals = {
            "documents": _count_table(conn, "documents"),
            "tickers": _count_distinct(conn, "documents", "ticker"),
            "objects": _count_table(conn, "objects"),
            "quality_events": _count_table(conn, "quality_events"),
        }
        section_status = (
            {
                str(row["section_quality_status"] or "unknown"): int(row["cnt"] or 0)
                for row in conn.execute(
                    """
                    SELECT section_quality_status, COUNT(*) AS cnt
                    FROM documents
                    GROUP BY section_quality_status
                    """
                )
            }
            if _table_exists(conn, "documents")
            else {}
        )
        event_counts = (
            [
                {
                    "category": str(row["category"] or ""),
                    "severity": str(row["severity"] or ""),
                    "stage": str(row["stage"] or ""),
                    "count": int(row["count"] or 0),
                }
                for row in conn.execute(
                    """
                    SELECT category, severity, COALESCE(stage, '') AS stage, COUNT(*) AS count
                    FROM quality_events
                    GROUP BY category, severity, stage
                    ORDER BY count DESC, category, severity, stage
                    """
                )
            ]
            if _table_exists(conn, "quality_events")
            else []
        )
        ticker_quality = _company_shard_ticker_quality_summary(conn)
        rejected_reasons = (
            [
                {"reason": str(row["reason"] or ""), "count": int(row["count"] or 0)}
                for row in conn.execute(
                    """
                    SELECT
                        CASE
                            WHEN message LIKE 'Unsupported numeric values:%'
                                THEN 'Unsupported numeric values'
                            WHEN message LIKE 'Dangling references:%'
                                THEN 'Dangling references'
                            ELSE message
                        END AS reason,
                        COUNT(*) AS count
                    FROM quality_events
                    WHERE category='rejected_object'
                    GROUP BY reason
                    ORDER BY count DESC, reason
                    LIMIT 20
                    """
                )
            ]
            if _table_exists(conn, "quality_events")
            else []
        )
    return {
        "format": SHARD_QUALITY_SUMMARY_FORMAT_VERSION,
        "totals": totals,
        "section_status": section_status,
        "event_counts": event_counts,
        "ticker_quality": ticker_quality,
        "rejected_reasons": rejected_reasons,
    }


def _company_shard_ticker_quality_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "documents"):
        return []
    if not _table_exists(conn, "quality_events"):
        sql = """
            SELECT ticker,
                   COUNT(*) AS docs,
                   SUM(CASE WHEN section_quality_status='fail' THEN 1 ELSE 0 END) AS section_fail,
                   SUM(CASE WHEN section_quality_status='warn' THEN 1 ELSE 0 END) AS section_warn,
                   0 AS batch_failure,
                   0 AS coverage_gap,
                   0 AS rejected_object
            FROM documents
            GROUP BY ticker
            ORDER BY ticker
        """
    else:
        sql = """
            WITH d AS (
                SELECT ticker,
                       COUNT(*) AS docs,
                       SUM(CASE WHEN section_quality_status='fail' THEN 1 ELSE 0 END) AS section_fail,
                       SUM(CASE WHEN section_quality_status='warn' THEN 1 ELSE 0 END) AS section_warn
                FROM documents
                GROUP BY ticker
            ),
            e AS (
                SELECT ticker,
                       SUM(CASE WHEN category='batch_failure' THEN 1 ELSE 0 END) AS batch_failure,
                       SUM(CASE WHEN category='coverage_gap' THEN 1 ELSE 0 END) AS coverage_gap,
                       SUM(CASE WHEN category='rejected_object' THEN 1 ELSE 0 END) AS rejected_object
                FROM quality_events
                GROUP BY ticker
            )
            SELECT d.ticker, d.docs, d.section_fail, d.section_warn,
                   COALESCE(e.batch_failure, 0) AS batch_failure,
                   COALESCE(e.coverage_gap, 0) AS coverage_gap,
                   COALESCE(e.rejected_object, 0) AS rejected_object
            FROM d
            LEFT JOIN e ON d.ticker=e.ticker
            ORDER BY d.ticker
        """
    return [
        {
            "ticker": str(row["ticker"] or ""),
            "docs": int(row["docs"] or 0),
            "section_fail": int(row["section_fail"] or 0),
            "section_warn": int(row["section_warn"] or 0),
            "batch_failure": int(row["batch_failure"] or 0),
            "coverage_gap": int(row["coverage_gap"] or 0),
            "rejected_object": int(row["rejected_object"] or 0),
        }
        for row in conn.execute(sql)
    ]


def _count_distinct(conn: sqlite3.Connection, table_name: str, column_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT COUNT(DISTINCT {column_name}) FROM {table_name}").fetchone()[0])


def _company_build_cost(plan: SourceArtifactSqlitePlan) -> int:
    return sum(max(int(item.estimated_bytes or 0), 1) for item in plan.items) + len(plan.items)


def _safe_ticker_filename(ticker: str) -> str:
    return re.sub(r"[^A-Z0-9._-]+", "_", _normalize_ticker(ticker))


class _BuildProgressWriter:
    def __init__(self, path: Path, *, release_id: str, release_root: Path) -> None:
        self.path = path.expanduser().resolve()
        self.release_id = release_id
        self.release_root = release_root.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def record(
        self,
        node_id: str,
        stage: str,
        status: str,
        *,
        event: str = "node_status",
        ticker: str | None = None,
        output: Path | str | None = None,
        cache_hit: bool | None = None,
        details: Mapping[str, Any] | None = None,
        error: str | None = None,
        started_at: float | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "format": V3_BUILD_PROGRESS_FORMAT_VERSION,
            "release_id": self.release_id,
            "event": event,
            "node_id": node_id,
            "stage": stage,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if ticker:
            payload["ticker"] = ticker
        if output is not None:
            payload["output"] = _progress_output_label(self.release_root, output)
        if cache_hit is not None:
            payload["cache_hit"] = bool(cache_hit)
        if details:
            payload["details"] = dict(details)
        if error:
            payload["error"] = error
        if started_at is not None:
            payload["duration_ms"] = max(0, int((time.perf_counter() - started_at) * 1000))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _progress_output_label(root: Path, output: Path | str) -> str:
    if isinstance(output, Path):
        return _path_label(root, output)
    return str(output)


def _path_label(root: Path, path: Path) -> str:
    resolved_root = root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    try:
        return str(resolved_path.relative_to(resolved_root))
    except ValueError:
        return str(resolved_path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_release_spine_fragments(fragments_dir: Path, *, release_root: Path) -> dict[str, Any]:
    resolved_root = release_root.expanduser().resolve()
    resolved_fragments = fragments_dir.expanduser().resolve()
    try:
        resolved_fragments.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Refusing to clean spine fragments outside release root: {resolved_fragments}") from exc

    details: dict[str, Any] = {
        "path": _path_label(resolved_root, resolved_fragments),
        "removed": False,
        "file_count": 0,
        "size_bytes": 0,
    }
    if not resolved_fragments.exists():
        return details

    file_count = 0
    size_bytes = 0
    for path in resolved_fragments.rglob("*"):
        if not path.is_file():
            continue
        file_count += 1
        try:
            size_bytes += path.stat().st_size
        except OSError:
            continue
    shutil.rmtree(resolved_fragments)
    details.update(
        {
            "removed": True,
            "file_count": file_count,
            "size_bytes": size_bytes,
        }
    )

    parent = resolved_fragments.parent
    try:
        if parent != resolved_root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            details["parent_removed"] = _path_label(resolved_root, parent)
    except OSError:
        details["parent_removed"] = None
    return details


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _source_rows(conn: sqlite3.Connection, table_name: str) -> list[sqlite3.Row]:
    if not _table_exists(conn, table_name):
        return []
    return list(conn.execute(f"SELECT * FROM {table_name}").fetchall())


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _rows_by_key(rows: Sequence[sqlite3.Row], key: str) -> dict[str, sqlite3.Row]:
    result: dict[str, sqlite3.Row] = {}
    for row in rows:
        value = row[key]
        if value is not None:
            result[str(value)] = row
    return result


def _emit_document_catalog(
    conn: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    *,
    ticker: str,
    shard_path: str,
) -> int:
    payload: list[tuple[Any, ...]] = []
    for row in rows:
        document_id = _document_id(row, ticker=ticker)
        payload.append(
            (
                document_id,
                str(row["ticker"] or ticker).upper(),
                None,
                row["document_type"],
                row["period"],
                _fiscal_year(row["period"]),
                _fiscal_quarter(row["period"]),
                row["generated_at"],
                row["artifact_index_path"],
                ticker,
                shard_path,
                _stable_hash(
                    {
                        "artifact_index_path": row["artifact_index_path"],
                        "counts_json": row["counts_json"],
                        "section_quality_json": row["section_quality_json"],
                    }
                ),
                _json_count(row["counts_json"], default_key="objects"),
                _json_count(row["counts_json"], default_key="edges"),
                _json_count(row["counts_json"], default_key="quality_events"),
                row["section_quality_status"],
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO global_document_catalog(
            document_id, ticker, company_name, document_type, period,
            fiscal_year, fiscal_quarter, filing_date, source_path, shard_id,
            shard_path, document_hash, object_count, edge_count,
            quality_event_count, quality_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _document_source_metadata(row: sqlite3.Row) -> dict[str, Any]:
    artifact_index_path = str(row["artifact_index_path"] or "")
    if not artifact_index_path:
        return {}
    try:
        doc_root = Path(artifact_index_path).parent
    except TypeError:
        return {}

    document_type = str(row["document_type"] or "")
    doc_type_key = document_type.replace("-", "").upper()
    period = str(row["period"] or "")
    candidates: list[tuple[Path, str]] = [
        (doc_root / "source_documents.jsonl", "jsonl"),
        (doc_root / "metadata.json", "json"),
    ]
    if len(doc_root.parents) >= 3:
        company_root = doc_root.parents[2]
        candidates.append((company_root / "sources" / doc_type_key / period / "metadata.json", "json"))

    metadata: dict[str, Any] = {}
    for path, file_type in candidates:
        if not path.exists() or path.stat().st_size <= 0:
            continue
        try:
            if file_type == "jsonl":
                line = path.read_text(encoding="utf-8").splitlines()[0]
                data = _json_loads(line)
            else:
                data = _json_loads(path.read_text(encoding="utf-8"))
        except (OSError, IndexError, ValueError, TypeError):
            continue
        if isinstance(data, dict):
            for key, value in data.items():
                if value not in (None, ""):
                    metadata[key] = value
    return metadata


def _metadata_value(metadata: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _emit_object_locator_and_search(
    conn: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    *,
    search_rows: Mapping[str, sqlite3.Row],
    ticker: str,
    shard_path: str,
) -> int:
    locator_rows: list[tuple[Any, ...]] = []
    search_payload: list[tuple[Any, ...]] = []
    for row in rows:
        object_id = str(row["id"])
        search = search_rows.get(object_id)
        obj = _json_loads(row["json"])
        compact_text = _row_value(search, "compact_text") or row["text"]
        compact_label = _compact_label(row, obj)
        locator_rows.append(
            (
                object_id,
                str(row["ticker"] or ticker).upper(),
                None,
                _object_document_id(row, ticker=ticker),
                row["document_type"],
                row["period"],
                None,
                row["type"],
                ticker,
                shard_path,
                object_id,
                _stable_hash(row["json"]),
                compact_label,
                _truncate(compact_text, 700),
                row["review_status"] or row["confidence"],
            )
        )
        search_payload.append(
            (
                object_id,
                str(row["ticker"] or ticker).upper(),
                row["type"],
                _truncate(compact_text, 1200),
                _row_value(search, "text_related") or "",
                _row_value(search, "text_support") or "",
                _row_value(search, "text_self") or "",
                _row_value(search, "text_entities") or "",
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO global_object_locator(
            object_id, ticker, company_name, document_id, document_type, period,
            filing_date, object_type, shard_id, shard_path, local_object_key,
            object_hash, compact_label, compact_summary, quality_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        locator_rows,
    )
    conn.executemany(
        """
        INSERT INTO global_search_fts(
            object_id, ticker, object_type, compact_text, topic_terms,
            factor_terms, metric_terms, entity_terms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        search_payload,
    )
    return len(locator_rows)


def _emit_edge_spine(
    conn: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    *,
    object_ticker: Mapping[str, str],
    ticker: str,
    shard_path: str,
) -> int:
    payload: list[tuple[Any, ...]] = []
    for row in rows:
        from_id = str(row["from_id"])
        to_id = str(row["to_id"])
        if from_id not in object_ticker or to_id not in object_ticker:
            continue
        from_ticker = object_ticker.get(from_id, str(row["ticker"] or ticker).upper())
        to_ticker = object_ticker.get(to_id, str(row["ticker"] or ticker).upper())
        payload.append(
            (
                row["id"],
                from_id,
                to_id,
                from_ticker,
                to_ticker,
                row["relation_name"] or row["relation_id"],
                "intra_company" if from_ticker == to_ticker else "cross_company",
                None,
                None,
                _confidence_score(row["confidence"]),
                row["review_status"],
                None,
                None,
                shard_path,
                row["relation_name"],
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO global_edge_spine(
            edge_id, from_object_id, to_object_id, from_ticker, to_ticker,
            relation_type, edge_scope, source_object_type, target_object_type,
            confidence, evidence_grade, materiality, recency_score,
            shard_hint, compact_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _emit_factor_spine(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> int:
    payload: list[tuple[Any, ...]] = []
    for row in rows:
        factor_key = _normalize_key(row["risk_or_driver"] or row["topic_label"] or row["factor_type"])
        if not factor_key:
            continue
        payload.append(
            (
                factor_key,
                row["topic_label"] or row["risk_or_driver"] or row["factor_type"],
                row["topic_family"] or row["factor_type"],
                None,
                row["ticker"],
                row["object_id"],
                _lookup_document_id(row),
                row["impact_channel"],
                None,
                row["specificity_score"],
                row["evidence_strength"],
                row["ticker"],
            )
        )
    conn.executemany(
        """
        INSERT INTO global_factor_spine(
            factor_key, factor_label, factor_family, benchmark, ticker,
            object_id, document_id, impact_channel, effect_direction,
            materiality, evidence_grade, shard_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _emit_topic_spine(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> int:
    payload: list[tuple[Any, ...]] = []
    for row in rows:
        topic_key = _normalize_key(row["topic_label"] or row["topic_family"] or row["topic_id"])
        payload.append(
            (
                row["topic_id"],
                topic_key,
                row["topic_label"],
                row["topic_family"],
                row["topic_summary"],
                row["ticker"],
                row["source_object_ids"],
                row["factor_terms"],
                row["metric_terms"],
                row["entity_terms"],
                row["mechanism_terms"],
                row["impact_channels"],
                row["evidence_strength"],
                row["materiality_score"],
                row["ticker"],
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO global_topic_spine(
            topic_id, topic_key, topic_label, topic_family, topic_summary,
            ticker, source_object_ids, factor_terms, metric_terms, entity_terms,
            mechanism_terms, impact_channels, evidence_grade, materiality, shard_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _emit_metric_spine(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> int:
    payload: list[tuple[Any, ...]] = []
    for row in rows:
        metric_key = _normalize_key(row["canonical_metric"] or row["metric_name"])
        if not metric_key:
            continue
        payload.append(
            (
                metric_key,
                row["metric_name"],
                row["unit"],
                _stable_hash(row["dimensions_json"] or ""),
                row["ticker"],
                row["object_id"],
                _lookup_document_id(row),
                row["period"],
                row["document_type"],
                _parse_float(row["value_text"]),
                None,
                None,
                row["ticker"],
            )
        )
    conn.executemany(
        """
        INSERT INTO global_metric_spine(
            canonical_metric_key, metric_name, unit, dimensions_hash, ticker,
            object_id, document_id, period, document_type, value_normalized,
            trend_direction, confidence, shard_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _emit_counterparty_spine(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> int:
    payload: list[tuple[Any, ...]] = []
    for row in rows:
        key = _normalize_key(row["counterparty"])
        if not key:
            continue
        payload.append(
            (
                key,
                row["counterparty"],
                row["agreement_subtype"] or row["agreement_type"],
                row["ticker"],
                row["object_id"],
                _lookup_document_id(row),
                row["agreement_type"],
                row["affected_channels"],
                row["specificity_score"],
                row["evidence_strength"],
                row["ticker"],
            )
        )
    conn.executemany(
        """
        INSERT INTO global_counterparty_spine(
            counterparty_key, counterparty_name, relationship_type, ticker,
            object_id, document_id, agreement_type, affected_channels,
            materiality, evidence_grade, shard_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _emit_entity_spine_from_topics(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> int:
    payload: list[tuple[Any, ...]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        for term in _json_list(row["entity_terms"]):
            key = _normalize_key(term)
            if not key:
                continue
            marker = (key, str(row["ticker"]), str(row["primary_object_id"]))
            if marker in seen:
                continue
            seen.add(marker)
            payload.append(
                (
                    key,
                    "unknown",
                    term,
                    json.dumps([term], sort_keys=True),
                    row["ticker"],
                    row["ticker"],
                    row["primary_object_id"],
                    None,
                    None,
                    row["ticker"],
                )
            )
    conn.executemany(
        """
        INSERT INTO global_entity_spine(
            entity_key, entity_type, canonical_name, aliases, ticker_scope,
            ticker, object_id, document_id, confidence, shard_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _refresh_key_stats(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM global_key_stats")
    sources = (
        ("factor", "global_factor_spine", "factor_key", "ticker", "object_id", "document_id"),
        ("topic", "global_topic_spine", "topic_key", "ticker", "topic_id", "topic_id"),
        ("metric", "global_metric_spine", "canonical_metric_key", "ticker", "object_id", "document_id"),
        ("entity", "global_entity_spine", "entity_key", "ticker", "object_id", "document_id"),
        ("counterparty", "global_counterparty_spine", "counterparty_key", "ticker", "object_id", "document_id"),
    )
    rows: list[tuple[Any, ...]] = []
    for key_type, table, key_col, ticker_col, object_col, document_col in sources:
        if not _table_exists(conn, table):
            continue
        for row in conn.execute(
            f"""
            SELECT
                {key_col} AS key,
                COUNT(DISTINCT {ticker_col}) AS ticker_count,
                COUNT(DISTINCT {object_col}) AS object_count,
                COUNT(DISTINCT {document_col}) AS document_count
            FROM {table}
            WHERE {key_col} IS NOT NULL AND {key_col} != ''
            GROUP BY {key_col}
            """
        ):
            ticker_count = int(row["ticker_count"] or 0)
            object_count = int(row["object_count"] or 0)
            idf_score = 1.0 / max(ticker_count, 1)
            rows.append(
                (
                    key_type,
                    row["key"],
                    ticker_count,
                    object_count,
                    int(row["document_count"] or 0),
                    idf_score,
                    1 if ticker_count >= 25 else 0,
                )
            )
    conn.executemany(
        """
        INSERT OR REPLACE INTO global_key_stats(
            key_type, key, ticker_count, object_count, document_count,
            idf_score, generic
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _document_id(row: sqlite3.Row, *, ticker: str) -> str:
    return f"{str(row['ticker'] or ticker).upper()}:{row['doc_type_key']}:{row['period']}"


def _object_document_id(row: sqlite3.Row, *, ticker: str) -> str:
    source_document_id = row["source_document_id"]
    if source_document_id:
        return str(source_document_id)
    return f"{str(row['ticker'] or ticker).upper()}:{row['doc_type_key']}:{row['period']}"


def _lookup_document_id(row: sqlite3.Row) -> str:
    return f"{str(row['ticker']).upper()}:{row['doc_type_key']}:{row['period']}"


def _compact_label(row: sqlite3.Row, obj: Mapping[str, Any]) -> str:
    for key in ("metric_name", "label", "name", "topic_label", "title", "section_name"):
        value = row[key] if key in row.keys() else obj.get(key)
        if value:
            return _truncate(str(value), 180)
    return str(row["id"])


def _row_value(row: sqlite3.Row | None, key: str) -> Any:
    if row is None:
        return None
    return row[key] if key in row.keys() else None


def _json_loads(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[str]:
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        parsed = value
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item).strip()]
    if isinstance(parsed, str) and parsed.strip():
        return [parsed.strip()]
    return []


def _json_count(value: Any, *, default_key: str) -> int:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return 0
    if isinstance(parsed, Mapping):
        raw = parsed.get(default_key)
        return int(raw) if isinstance(raw, (int, float)) else 0
    return 0


def _normalize_key(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return re.sub(r"_+", "_", text)


def _normalize_ticker(ticker: str) -> str:
    value = str(ticker or "").strip().upper()
    if not value:
        raise ValueError("ticker is required")
    return value


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit]


def _confidence_score(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    mapping = {"high": 1.0, "medium": 0.6, "low": 0.3}
    if text in mapping:
        return mapping[text]
    return _parse_float(value)


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _fiscal_year(period: Any) -> int | None:
    match = re.search(r"(20\d{2}|19\d{2})", str(period or ""))
    return int(match.group(1)) if match else None


def _fiscal_quarter(period: Any) -> int | None:
    match = re.search(r"Q([1-4])", str(period or ""), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None
