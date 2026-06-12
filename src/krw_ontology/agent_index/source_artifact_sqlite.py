"""Internal source-artifact SQLite materialization boundary for v3 builds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from krw_ontology.agent_index.builder import (
    AGENT_INDEX_SCHEMA_VERSION as SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
    CompanyPlanItem as SourceCompanyShardPlanItem,
    IndexBuildPlan as SourceArtifactSqlitePlan,
    SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
    _build_artifact_index_sqlite,
    _cleanup_sqlite_database_files,
    _plan_artifact_index_inputs,
    _replace_sqlite_database,
    _temporary_index_path,
    diff_source_artifact_manifests,
    verify_agent_index,
    verify_source_artifact_manifest,
    write_source_artifact_manifest,
)


def plan_source_artifact_sqlite_inputs(
    root: Path,
    *,
    sqlite_path: Path,
    cache_root: Path | None = None,
    workers: int | None = None,
    source_manifest_path: Path | None = None,
) -> SourceArtifactSqlitePlan:
    """Plan source artifact rows used to materialize v3 company shard SQLite files."""
    return _plan_artifact_index_inputs(
        root,
        index_path=sqlite_path,
        cache_root=cache_root,
        workers=workers,
        source_manifest_path=source_manifest_path,
    )


def build_source_artifact_sqlite(
    root: Path,
    *,
    sqlite_path: Path,
    published_sqlite_path: Path,
    plan: SourceArtifactSqlitePlan,
) -> dict[str, Any]:
    """Materialize one source-artifact SQLite database from a prepared v3 plan."""
    return _build_artifact_index_sqlite(
        root,
        index_path=sqlite_path,
        published_index_path=published_sqlite_path,
        plan=plan,
    )


def verify_source_artifact_sqlite(sqlite_path: Path) -> dict[str, Any]:
    return verify_agent_index(sqlite_path)


def cleanup_sqlite_database_files(sqlite_path: Path) -> None:
    _cleanup_sqlite_database_files(sqlite_path)


def temporary_sqlite_path(sqlite_path: Path) -> Path:
    return _temporary_index_path(sqlite_path)


def replace_sqlite_database(source_path: Path, target_path: Path) -> None:
    _replace_sqlite_database(source_path, target_path)
