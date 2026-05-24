"""Build a global SQLite index for agent retrieval.

The JSONL ontology artifacts remain canonical. This module builds a
regenerable read index that lets agents retrieve evidence bundles without
reasoning over the filesystem layout.
"""

from __future__ import annotations

import json
import logging
import os
import re
import resource
import sqlite3
import hashlib
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.agent_index.retrieval_text import (
    ObjectLookup,
    RETRIEVAL_TEXT_BUILDER_VERSION,
    RetrievalText,
    build_retrieval_text,
    fallback_retrieval_text,
)
from krw_ontology.agent_index.discovery import (
    COMPANY_TOPIC_OBJECT_TYPES,
    build_company_topic_profile,
)
from krw_ontology.utils.io import read_jsonl

logger = logging.getLogger("krw_ontology")

AGENT_INDEX_SCHEMA_VERSION = "1.0.0-alpha.3"
DEFAULT_INDEX_RELATIVE_PATH = Path("indexes") / "agent_index.sqlite"

OBJECT_FILE_KEYS = {
    "run_manifests",
    "ontology_registry_snapshots",
    "validation_reports",
    "taxonomy_terms",
    "source_documents",
    "source_locations",
    "source_tables",
    "source_table_cells",
    "spans",
    "evidence_quotes",
    "language_signals",
    "support_links",
    "canonical_entities",
    "entity_mentions",
    "claims",
    "metric_observations",
    "calculations",
    "business_factors",
    "agreement_terms",
    "business_events",
    "business_activities",
    "external_factor_exposures",
    "assumption_candidates",
    "xbrl_facts",
    "company_business_profiles",
    "temporal_links",
    "trend_observations",
    "change_events",
}

ANSWER_CANDIDATE_TYPES = {
    "ResearchClaim",
    "EvidenceQuote",
    "MetricObservation",
    "Calculation",
    "BusinessFactor",
    "AgreementTerm",
    "BusinessEvent",
    "BusinessActivity",
    "ExternalFactorExposure",
    "AssumptionCandidate",
    "TrendObservation",
    "ChangeEvent",
}

SEMANTIC_SUPPORT_TYPES = {
    "BusinessFactor",
    "AgreementTerm",
    "BusinessEvent",
    "BusinessActivity",
    "ExternalFactorExposure",
    "AssumptionCandidate",
    "ChangeEvent",
}

TEXT_KEYS_BY_TYPE = {
    "RunManifest": (
        "run_id",
        "pipeline_version",
        "ontology_schema_version",
        "ontology_registry_version",
        "model",
    ),
    "OntologyRegistrySnapshot": (
        "registry_version",
        "canonical_artifacts",
        "text_fields_by_type",
    ),
    "ValidationReport": ("validation_scope", "summary"),
    "TaxonomyTerm": ("taxonomy", "term_type", "canonical_name", "display_name", "aliases"),
    "SourceDocument": ("ticker", "company_name", "accession_number", "source_url"),
    "SourceLocation": ("source_boundary", "section_name", "section_path", "source_span_id"),
    "SourceTable": ("section_name", "caption"),
    "SourceTableCell": ("raw_text", "normalized_text"),
    "SourceSpan": ("text",),
    "EvidenceQuote": ("quote_text",),
    "LanguageSignal": ("signal_text",),
    "SupportLink": (
        "support_type",
        "support_role",
        "stance",
        "evidence_strength",
        "support_strength",
        "from_id",
        "to_id",
        "explanation",
    ),
    "CanonicalEntity": ("entity_type", "canonical_name", "aliases", "ticker_scope"),
    "EntityMention": ("mention_text", "source_object_type", "canonical_entity_id"),
    "ResearchClaim": (
        "claim_text",
        "theme_hint",
        "factor_hint",
        "activity_hint",
        "benchmark_hint",
        "impact_channels",
        "effect_direction",
        "materiality_hint",
        "time_horizon",
        "sector_hint",
        "object_type_hints",
    ),
    "MetricObservation": ("metric_name", "unit", "source_type", "normalization", "dimensions"),
    "Calculation": ("calculation_type", "formula", "calculation_method", "validation_status"),
    "BusinessFactor": ("name", "description", "factor_roles", "category", "affected_channels", "materiality_basis"),
    "AgreementTerm": ("name", "agreement_type", "agreement_subtype", "economic_role", "affected_channels"),
    "BusinessEvent": ("name", "event_type", "event_subtype", "event_status", "date_expression", "affected_channels"),
    "BusinessActivity": (
        "name",
        "activity_type",
        "description",
        "revenue_relevance",
        "cost_relevance",
    ),
    "ExternalFactorExposure": (
        "factor",
        "factor_category",
        "benchmark",
        "direction",
        "impact_channel",
        "effect_direction",
        "mechanism",
        "evidence_grade",
        "materiality",
    ),
    "AssumptionCandidate": ("name", "assumption_text", "value_hint", "assumption_type"),
    "XBRLFact": ("taxonomy_tag", "safe_taxonomy_tag", "context_ref"),
    "CompanyBusinessProfile": (
        "sector",
        "business_model_summary",
        "primary_business_activities",
        "primary_revenue_sources",
        "primary_cost_sources",
        "key_external_factors",
        "key_uncertainties",
    ),
    "TemporalLink": (
        "from_object_id",
        "to_object_id",
        "relation",
        "rationale",
        "from_period",
        "to_period",
    ),
    "TrendObservation": (
        "subject",
        "metric_or_factor",
        "direction",
        "magnitude_text",
        "interpretation",
    ),
    "ChangeEvent": ("event_type", "event_date", "description"),
}

_NON_WORD_RE = re.compile(r"\s+")
DEFAULT_BUILD_RESOURCE_PROFILE = "max-local"
DEFAULT_BULK_INSERT_CHUNK_SIZE = 20_000
DEFAULT_COMPANY_TOPIC_BATCH_SIZE = 2_000
DEFAULT_SQLITE_CACHE_SIZE_KIB = 4_096 * 1024
DEFAULT_SQLITE_MMAP_SIZE_BYTES = 16 * 1024 * 1024 * 1024
BULK_INSERT_CHUNK_SIZE = DEFAULT_BULK_INSERT_CHUNK_SIZE
COMPANY_TOPIC_BATCH_SIZE = DEFAULT_COMPANY_TOPIC_BATCH_SIZE
SQLITE_CACHE_SIZE_KIB = DEFAULT_SQLITE_CACHE_SIZE_KIB
SQLITE_MMAP_SIZE_BYTES = DEFAULT_SQLITE_MMAP_SIZE_BYTES
COMPANY_TOPIC_TEXT_CHAR_LIMIT = 2_500
COMPANY_TOPIC_FIELD_CHAR_LIMIT = 700
COMPANY_TOPIC_FTS_CHAR_LIMIT = 1_800
COMPANY_TOPIC_BUILDER_VERSION = "0.3.0-rich-hardened"
TYPED_PROJECTION_BUILDER_VERSION = "0.1.0-serving-projections"
TYPED_PROJECTION_TABLES = (
    "exposure_lookup",
    "agreement_lookup",
    "event_lookup",
    "factor_lookup",
)
TYPED_PROJECTION_OBJECT_TYPES = {
    "exposure_lookup": ("ExternalFactorExposure",),
    "agreement_lookup": ("AgreementTerm",),
    "event_lookup": ("BusinessEvent", "ChangeEvent", "TrendObservation", "TemporalLink"),
    "factor_lookup": ("BusinessFactor",),
}

_SQLITE_SYNCHRONOUS_VALUES = {"OFF", "NORMAL", "FULL", "EXTRA"}
_ACTIVE_BUILD_PROGRESS_LOGGER: _BuildProgressLogger | None = None


def _env_int(name: str, default: int, *, min_value: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        logger.warning(
            "build_agent_index: ignoring invalid integer env %s=%r",
            name,
            raw,
            extra={"stage": "build_agent_index"},
        )
        return default
    return max(min_value, value)


def _env_float(name: str, default: float, *, min_value: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(str(raw).strip())
    except ValueError:
        logger.warning(
            "build_agent_index: ignoring invalid float env %s=%r",
            name,
            raw,
            extra={"stage": "build_agent_index"},
        )
        return default
    return max(min_value, value)


def _default_progress_log_path(index_path: Path | None = None) -> str:
    if index_path is None:
        return ""
    return str(index_path.parent / "build_progress.jsonl")


def _build_resource_settings(index_path: Path | None = None) -> dict[str, Any]:
    profile = str(os.getenv("KRW_BUILD_RESOURCE_PROFILE") or DEFAULT_BUILD_RESOURCE_PROFILE).strip().lower() or DEFAULT_BUILD_RESOURCE_PROFILE
    max_local = profile == "max-local"
    default_cache_mib = max(1, DEFAULT_SQLITE_CACHE_SIZE_KIB // 1024)
    default_mmap_gib = DEFAULT_SQLITE_MMAP_SIZE_BYTES / float(1024**3)
    default_batch_size = DEFAULT_BULK_INSERT_CHUNK_SIZE
    default_company_topic_batch_size = DEFAULT_COMPANY_TOPIC_BATCH_SIZE
    default_checkpoint_every_artifacts = 10

    synchronous = str(os.getenv("KRW_SQLITE_SYNCHRONOUS") or "OFF").strip().upper()
    if synchronous not in _SQLITE_SYNCHRONOUS_VALUES:
        logger.warning(
            "build_agent_index: ignoring invalid KRW_SQLITE_SYNCHRONOUS=%r",
            synchronous,
            extra={"stage": "build_agent_index"},
        )
        synchronous = "OFF"

    cache_mib = _env_int("KRW_SQLITE_CACHE_MIB", default_cache_mib, min_value=1)
    mmap_gib = _env_float("KRW_SQLITE_MMAP_GIB", default_mmap_gib, min_value=0.0)
    return {
        "resource_profile": profile,
        "sqlite_synchronous": synchronous,
        "sqlite_cache_mib": cache_mib,
        "sqlite_cache_size_kib": cache_mib * 1024,
        "sqlite_mmap_gib": mmap_gib,
        "sqlite_mmap_size_bytes": int(mmap_gib * 1024**3),
        "sqlite_wal_autocheckpoint": _env_int("KRW_SQLITE_WAL_AUTOCHECKPOINT", 0, min_value=0),
        "bulk_insert_chunk_size": _env_int("KRW_BUILD_BATCH_SIZE", default_batch_size, min_value=1),
        "company_topic_batch_size": _env_int(
            "KRW_COMPANY_TOPIC_BATCH_SIZE",
            default_company_topic_batch_size,
            min_value=1,
        ),
        "checkpoint_every_artifacts": _env_int(
            "KRW_BUILD_CHECKPOINT_EVERY_ARTIFACTS",
            default_checkpoint_every_artifacts,
            min_value=0,
        ),
        "progress_log_path": os.getenv("KRW_BUILD_PROGRESS_LOG") or _default_progress_log_path(index_path),
        "progress_log_interval_sec": _env_float("KRW_BUILD_LOG_INTERVAL_SEC", 10.0, min_value=0.0),
    }


def _apply_build_resource_settings(settings: Mapping[str, Any]) -> None:
    global BULK_INSERT_CHUNK_SIZE, COMPANY_TOPIC_BATCH_SIZE, SQLITE_CACHE_SIZE_KIB, SQLITE_MMAP_SIZE_BYTES
    BULK_INSERT_CHUNK_SIZE = int(settings["bulk_insert_chunk_size"])
    COMPANY_TOPIC_BATCH_SIZE = int(settings["company_topic_batch_size"])
    SQLITE_CACHE_SIZE_KIB = int(settings["sqlite_cache_size_kib"])
    SQLITE_MMAP_SIZE_BYTES = int(settings["sqlite_mmap_size_bytes"])


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return value


def _rss_mb() -> float | None:
    try:
        max_rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(max_rss / divisor, 1)


class _BuildProgressLogger:
    def __init__(self, *, path: Path, index_path: Path, settings: Mapping[str, Any]) -> None:
        self.path = path
        self.index_path = index_path
        self.settings = dict(settings)
        self.started_at = time.perf_counter()
        self.last_write_at = 0.0
        self.interval_sec = float(settings.get("progress_log_interval_sec") or 0.0)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, phase: str, fields: Mapping[str, Any]) -> None:
        now = time.perf_counter()
        must_write = (
            self.last_write_at == 0.0
            or phase == "start"
            or phase.endswith("_start")
            or phase.endswith("_done")
            or phase == "finalize_done"
            or self.interval_sec == 0.0
            or now - self.last_write_at >= self.interval_sec
        )
        if not must_write:
            return
        self.last_write_at = now
        wal_path = Path(str(self.index_path) + "-wal")
        shm_path = Path(str(self.index_path) + "-shm")
        payload = {
            "event": "build_phase",
            "phase": phase,
            "ts": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "elapsed_sec": round(now - self.started_at, 3),
            "rss_mb": _rss_mb(),
            "db_size_mb": round(self.index_path.stat().st_size / 1024 / 1024, 1) if self.index_path.exists() else 0.0,
            "wal_size_mb": round(wal_path.stat().st_size / 1024 / 1024, 1) if wal_path.exists() else 0.0,
            "shm_size_mb": round(shm_path.stat().st_size / 1024 / 1024, 1) if shm_path.exists() else 0.0,
            "resource_profile": self.settings.get("resource_profile"),
            "sqlite_synchronous": self.settings.get("sqlite_synchronous"),
            "sqlite_cache_mib": self.settings.get("sqlite_cache_mib"),
            "sqlite_mmap_gib": self.settings.get("sqlite_mmap_gib"),
            "bulk_insert_chunk_size": self.settings.get("bulk_insert_chunk_size"),
            "checkpoint_every_artifacts": self.settings.get("checkpoint_every_artifacts"),
            "fields": _json_safe(dict(fields)),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _elapsed(started_at: float) -> float:
    return round(time.perf_counter() - started_at, 3)


def _log_build_phase(phase: str, **fields: Any) -> None:
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    message = f"build_agent_index: {phase}"
    if rendered:
        message = f"{message} {rendered}"
    logger.info(message, extra={"stage": "build_agent_index"})
    if _ACTIVE_BUILD_PROGRESS_LOGGER is not None:
        _ACTIVE_BUILD_PROGRESS_LOGGER.write(phase, fields)


def _compact_space(value: str) -> str:
    return _NON_WORD_RE.sub(" ", str(value or "")).strip()


def build_agent_index(
    root: Path,
    *,
    index_path: Path | None = None,
    force: bool = True,
) -> dict[str, Any]:
    """Build a global SQLite agent index from all discovered artifact indexes."""
    global _ACTIVE_BUILD_PROGRESS_LOGGER
    root = root.resolve()
    index_path = (index_path or root / DEFAULT_INDEX_RELATIVE_PATH).resolve()
    if force and index_path.exists():
        index_path.unlink()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    build_settings = _build_resource_settings(index_path)
    _apply_build_resource_settings(build_settings)

    artifact_indexes = discover_artifact_indexes(root)
    progress_log_path = str(build_settings.get("progress_log_path") or "").strip()
    previous_progress_logger = _ACTIVE_BUILD_PROGRESS_LOGGER
    _ACTIVE_BUILD_PROGRESS_LOGGER = (
        _BuildProgressLogger(path=Path(progress_log_path), index_path=index_path, settings=build_settings)
        if progress_log_path
        else None
    )
    conn = sqlite3.connect(index_path, timeout=60)
    build_started_at = time.perf_counter()
    try:
        conn.row_factory = sqlite3.Row
        _configure_connection(conn, build_settings)
        _create_schema(conn)
        _log_build_phase(
            "start",
            root=str(root),
            index_path=str(index_path),
            force=force,
            artifact_indexes=len(artifact_indexes),
            build_settings=build_settings,
        )
        totals = {
            "documents": 0,
            "objects": 0,
            "edges": 0,
            "quality_events": 0,
            "object_traceability": 0,
            "metric_lookup": 0,
            "metric_dimension_lookup": 0,
            "company_dimension_catalog": 0,
            "exposure_lookup": 0,
            "agreement_lookup": 0,
            "event_lookup": 0,
            "factor_lookup": 0,
            "company_topics": 0,
        }

        replace_fts_entries = not force
        for artifact_number, artifact_index_path in enumerate(artifact_indexes, start=1):
            artifact_started_at = time.perf_counter()
            _log_build_phase(
                "index_artifact_start",
                artifact_number=artifact_number,
                artifact_indexes=len(artifact_indexes),
                artifact_index=str(artifact_index_path),
            )
            with conn:
                stats = _index_artifact(
                    conn,
                    root,
                    artifact_index_path,
                    replace_fts_entries=replace_fts_entries,
                )
            for key, value in stats.items():
                totals[key] += value
            _checkpoint_wal(conn, settings=build_settings, artifact_number=artifact_number)
            _log_build_phase(
                "index_artifact_done",
                artifact_number=artifact_number,
                artifact_indexes=len(artifact_indexes),
                artifact_index=str(artifact_index_path),
                elapsed_seconds=_elapsed(artifact_started_at),
                stats=stats,
                totals=totals,
            )

        secondary_started_at = time.perf_counter()
        _log_build_phase("create_base_secondary_indexes_start", totals=totals)
        with conn:
            _create_base_secondary_indexes(conn)
        _checkpoint_wal(conn)
        _log_build_phase(
            "create_base_secondary_indexes_done",
            elapsed_seconds=_elapsed(secondary_started_at),
            totals=totals,
        )

        trace_started_at = time.perf_counter()
        _log_build_phase("object_traceability_start", objects=totals["objects"])
        with conn:
            totals["object_traceability"] = _rebuild_object_traceability(conn)
        _checkpoint_wal(conn)
        _log_build_phase(
            "object_traceability_done",
            elapsed_seconds=_elapsed(trace_started_at),
            object_traceability=totals["object_traceability"],
        )

        metric_started_at = time.perf_counter()
        _log_build_phase("metric_lookup_start", objects=totals["objects"])
        with conn:
            totals["metric_lookup"] = _rebuild_metric_lookup(conn)
            totals["metric_dimension_lookup"] = _rebuild_metric_dimension_lookup(conn)
            totals["company_dimension_catalog"] = _rebuild_company_dimension_catalog(conn)
        _checkpoint_wal(conn)
        _log_build_phase(
            "metric_lookup_done",
            elapsed_seconds=_elapsed(metric_started_at),
            metric_lookup=totals["metric_lookup"],
            metric_dimension_lookup=totals["metric_dimension_lookup"],
            company_dimension_catalog=totals["company_dimension_catalog"],
        )

        projection_started_at = time.perf_counter()
        _log_build_phase("typed_projection_lookup_start", objects=totals["objects"])
        with conn:
            for table_name in TYPED_PROJECTION_TABLES:
                totals[table_name] = _rebuild_typed_projection_lookup(conn, table_name)
        _checkpoint_wal(conn)
        _log_build_phase(
            "typed_projection_lookup_done",
            elapsed_seconds=_elapsed(projection_started_at),
            exposure_lookup=totals["exposure_lookup"],
            agreement_lookup=totals["agreement_lookup"],
            event_lookup=totals["event_lookup"],
            factor_lookup=totals["factor_lookup"],
        )

        topic_started_at = time.perf_counter()
        _log_build_phase("company_topic_index_start", objects=totals["objects"])
        totals["company_topics"] = _rebuild_company_topic_index(conn)
        _checkpoint_wal(conn)
        _log_build_phase(
            "company_topic_index_done",
            elapsed_seconds=_elapsed(topic_started_at),
            company_topics=totals["company_topics"],
        )

        finalize_started_at = time.perf_counter()
        _log_build_phase("finalize_start", totals=totals)
        with conn:
            _create_serving_secondary_indexes(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO metadata(key, value)
                VALUES (?, ?)
                """,
                (
                    "build",
                    json.dumps(
                        {
                            "schema_version": AGENT_INDEX_SCHEMA_VERSION,
                            "agent_index_schema_version": AGENT_INDEX_SCHEMA_VERSION,
                            "ontology_schema_version": SCHEMA_VERSION,
                            "ontology_registry_version": _registry_version(conn),
                            "retrieval_text_builder_version": RETRIEVAL_TEXT_BUILDER_VERSION,
                            "root": str(root),
                            "artifact_root": str(root),
                            "artifact_manifest_hash": _artifact_manifest_hash(artifact_indexes),
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "artifact_indexes": len(artifact_indexes),
                            "company_topic_builder_version": COMPANY_TOPIC_BUILDER_VERSION,
                            "company_topic_profile_mode": "rich_materialized",
                            "object_search_text_enabled": True,
                            "metric_lookup_enabled": True,
                            "metric_dimension_lookup_enabled": True,
                            "company_dimension_catalog_enabled": True,
                            "typed_projection_builder_version": TYPED_PROJECTION_BUILDER_VERSION,
                            "typed_projection_lookup_enabled": True,
                            "company_topic_fts_enabled": True,
                            "build_settings": build_settings,
                            "totals": totals,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        conn.execute("PRAGMA optimize")
        _checkpoint_wal(conn, truncate=True)
        _log_build_phase(
            "finalize_done",
            elapsed_seconds=_elapsed(finalize_started_at),
            total_elapsed_seconds=_elapsed(build_started_at),
            totals=totals,
        )

        logger.info(
            "build_agent_index: indexed %d documents, %d objects, %d edges",
            totals["documents"],
            totals["objects"],
            totals["edges"],
            extra={"stage": "build_agent_index"},
        )
        return {
            "index_path": index_path,
            "root": root,
            "artifact_indexes": len(artifact_indexes),
            "totals": totals,
            "build_settings": build_settings,
        }
    finally:
        conn.close()
        _ACTIVE_BUILD_PROGRESS_LOGGER = previous_progress_logger


def discover_artifact_indexes(root: Path) -> list[Path]:
    """Return artifact indexes known through company indexes or direct glob."""
    found: dict[Path, Path] = {}

    for company_index_path in sorted(root.glob("companies/*/indexes/company_artifact_index.json")):
        try:
            company_index = json.loads(company_index_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for doc in company_index.get("documents", {}).values():
            rel = doc.get("artifact_index")
            if not rel:
                continue
            path = (root / rel).resolve()
            if path.exists():
                found[path] = path

    for path in sorted(root.glob("companies/*/ontology/*/*/artifact_index.json")):
        found[path.resolve()] = path.resolve()

    for path in sorted(root.glob("companies/*/context/artifact_index.json")):
        found[path.resolve()] = path.resolve()

    return sorted(found)


def _configure_connection(conn: sqlite3.Connection, settings: Mapping[str, Any] | None = None) -> None:
    settings = dict(settings or _build_resource_settings())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA synchronous={settings['sqlite_synchronous']}")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute(f"PRAGMA cache_size=-{int(settings['sqlite_cache_size_kib'])}")
    conn.execute(f"PRAGMA mmap_size={int(settings['sqlite_mmap_size_bytes'])}")
    conn.execute(f"PRAGMA wal_autocheckpoint={int(settings['sqlite_wal_autocheckpoint'])}")


def _checkpoint_wal(
    conn: sqlite3.Connection,
    *,
    truncate: bool = False,
    settings: Mapping[str, Any] | None = None,
    artifact_number: int | None = None,
) -> None:
    if artifact_number is not None and settings is not None:
        checkpoint_every = int(settings.get("checkpoint_every_artifacts") or 0)
        if checkpoint_every <= 0 or artifact_number % checkpoint_every != 0:
            return
    mode = "TRUNCATE" if truncate else "PASSIVE"
    try:
        conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchall()
    except sqlite3.OperationalError as exc:
        logger.debug(
            "build_agent_index: WAL checkpoint skipped: %s",
            exc,
            extra={"stage": "build_agent_index"},
        )


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            artifact_index_path TEXT NOT NULL,
            ontology_dir TEXT NOT NULL,
            sources_json TEXT NOT NULL,
            reports_json TEXT NOT NULL,
            counts_json TEXT NOT NULL,
            section_quality_status TEXT,
            section_quality_json TEXT NOT NULL,
            generated_at TEXT,
            schema_version TEXT,
            PRIMARY KEY (ticker, doc_type_key, period)
        );

        CREATE TABLE IF NOT EXISTS objects (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            source_document_id TEXT,
            section_name TEXT,
            metric_name TEXT,
            review_status TEXT,
            confidence TEXT,
            text TEXT NOT NULL,
            json TEXT NOT NULL,
            artifact_key TEXT NOT NULL,
            artifact_path TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS edges (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            source_document_id TEXT,
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            relation_id TEXT NOT NULL,
            relation_name TEXT NOT NULL,
            confidence TEXT,
            review_status TEXT,
            json TEXT NOT NULL,
            artifact_path TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS quality_events (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            object_id TEXT,
            stage TEXT,
            message TEXT NOT NULL,
            json TEXT NOT NULL
        );

        DROP TABLE IF EXISTS object_fts;
        DROP TABLE IF EXISTS object_text;
        DROP TABLE IF EXISTS object_search_text;
        DROP TABLE IF EXISTS object_traceability;
        DROP TABLE IF EXISTS metric_lookup;
        DROP TABLE IF EXISTS metric_dimension_lookup;
        DROP TABLE IF EXISTS company_dimension_catalog;
        DROP TABLE IF EXISTS exposure_lookup;
        DROP TABLE IF EXISTS agreement_lookup;
        DROP TABLE IF EXISTS event_lookup;
        DROP TABLE IF EXISTS factor_lookup;
        DROP TABLE IF EXISTS company_topic_source_objects;
        DROP TABLE IF EXISTS company_topic_fts;
        DROP TABLE IF EXISTS company_topic_index;

        CREATE VIRTUAL TABLE object_fts USING fts5(
            object_id UNINDEXED,
            type UNINDEXED,
            ticker UNINDEXED,
            document_type UNINDEXED,
            period UNINDEXED,
            text_self,
            text_support,
            text_related,
            text_entities,
            text_aliases,
            compact_text,
            tokenize = 'unicode61'
        );

        CREATE TABLE IF NOT EXISTS object_search_text (
            object_id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            period TEXT NOT NULL,
            text_self TEXT,
            text_support TEXT,
            text_related TEXT,
            text_entities TEXT,
            text_aliases TEXT,
            compact_text TEXT
        );

        CREATE TABLE IF NOT EXISTS object_text (
            object_id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            period TEXT NOT NULL,
            text_self TEXT,
            text_support TEXT,
            text_related TEXT,
            text_entities TEXT,
            text_aliases TEXT,
            compact_text TEXT
        );

        CREATE TABLE IF NOT EXISTS object_traceability (
            object_id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            period TEXT NOT NULL,
            trace_status TEXT NOT NULL,
            evidence_chain_count INTEGER DEFAULT 0,
            support_depth INTEGER,
            support_quote_count INTEGER DEFAULT 0,
            support_claim_count INTEGER DEFAULT 0,
            support_link_count INTEGER DEFAULT 0,
            trace_method TEXT,
            metric_lineage_status TEXT,
            answer_candidate INTEGER DEFAULT 0,
            json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metric_lookup (
            object_id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            metric_name TEXT,
            canonical_metric TEXT,
            metric_alias_text TEXT,
            value_text TEXT,
            unit TEXT,
            dimensions_json TEXT NOT NULL,
            is_company_total INTEGER DEFAULT 0,
            segment_name TEXT,
            product_name TEXT,
            geography_name TEXT,
            trace_status TEXT,
            metric_lineage_status TEXT,
            text TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metric_dimension_lookup (
            object_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            period TEXT NOT NULL,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            canonical_metric TEXT NOT NULL,
            dimension_kind TEXT,
            dimension_key TEXT NOT NULL,
            dimension_label TEXT NOT NULL,
            axis_key TEXT,
            member_key TEXT,
            is_company_total INTEGER DEFAULT 0,
            trace_status TEXT,
            metric_lineage_status TEXT,
            PRIMARY KEY (object_id, dimension_key)
        );

        CREATE TABLE IF NOT EXISTS company_dimension_catalog (
            ticker TEXT NOT NULL,
            dimension_key TEXT NOT NULL,
            dimension_label TEXT NOT NULL,
            dimension_kind TEXT,
            source_metric_count INTEGER DEFAULT 0,
            periods_json TEXT NOT NULL,
            aliases_text TEXT,
            PRIMARY KEY (ticker, dimension_key)
        );

        CREATE TABLE IF NOT EXISTS exposure_lookup (
            object_id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            factor TEXT,
            benchmark TEXT,
            impact_channel TEXT,
            mechanism TEXT,
            scenario_terms TEXT,
            direct_anchor_text TEXT,
            related_context_text TEXT,
            negative_guard_terms TEXT,
            trace_status TEXT,
            evidence_strength TEXT,
            specificity_score REAL,
            generic_score REAL,
            boilerplate_score REAL,
            support_quote_count INTEGER DEFAULT 0,
            support_claim_count INTEGER DEFAULT 0,
            evidence_chain_count INTEGER DEFAULT 0,
            lookup_text TEXT NOT NULL,
            source_text TEXT
        );

        CREATE TABLE IF NOT EXISTS agreement_lookup (
            object_id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            agreement_type TEXT,
            agreement_subtype TEXT,
            counterparty TEXT,
            amount TEXT,
            maturity_date TEXT,
            termination_terms TEXT,
            covenant_terms TEXT,
            collateral_terms TEXT,
            affected_channels TEXT,
            trace_status TEXT,
            evidence_strength TEXT,
            specificity_score REAL,
            generic_score REAL,
            boilerplate_score REAL,
            support_quote_count INTEGER DEFAULT 0,
            support_claim_count INTEGER DEFAULT 0,
            evidence_chain_count INTEGER DEFAULT 0,
            lookup_text TEXT NOT NULL,
            source_text TEXT
        );

        CREATE TABLE IF NOT EXISTS event_lookup (
            object_id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            event_type TEXT,
            event_status TEXT,
            event_date TEXT,
            date_expression TEXT,
            date_sort_key TEXT,
            project_or_product TEXT,
            regulatory_body TEXT,
            affected_channels TEXT,
            trace_status TEXT,
            evidence_strength TEXT,
            specificity_score REAL,
            generic_score REAL,
            boilerplate_score REAL,
            support_quote_count INTEGER DEFAULT 0,
            support_claim_count INTEGER DEFAULT 0,
            evidence_chain_count INTEGER DEFAULT 0,
            lookup_text TEXT NOT NULL,
            source_text TEXT
        );

        CREATE TABLE IF NOT EXISTS factor_lookup (
            object_id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            factor_type TEXT,
            topic_family TEXT,
            impact_channel TEXT,
            business_area TEXT,
            risk_or_driver TEXT,
            topic_label TEXT,
            topic_summary TEXT,
            trace_status TEXT,
            evidence_strength TEXT,
            specificity_score REAL,
            generic_score REAL,
            boilerplate_score REAL,
            support_quote_count INTEGER DEFAULT 0,
            support_claim_count INTEGER DEFAULT 0,
            evidence_chain_count INTEGER DEFAULT 0,
            lookup_text TEXT NOT NULL,
            source_text TEXT
        );

        CREATE TABLE IF NOT EXISTS company_topic_index (
            topic_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            period TEXT,
            document_type TEXT,
            doc_type_key TEXT,
            filing_type TEXT,
            topic_label TEXT,
            topic_summary TEXT,
            topic_type TEXT,
            topic_family TEXT,
            topic_text TEXT,
            facet_text TEXT,
            primary_object_id TEXT NOT NULL,
            primary_object_type TEXT NOT NULL,
            source_object_ids TEXT NOT NULL,
            top_traceable_object_ids TEXT NOT NULL,
            untraced_object_ids TEXT NOT NULL,
            dominant_object_types TEXT NOT NULL,
            impact_channels TEXT NOT NULL,
            factor_terms TEXT NOT NULL,
            metric_terms TEXT NOT NULL,
            entity_terms TEXT NOT NULL,
            mechanism_terms TEXT NOT NULL,
            scenario_terms TEXT NOT NULL,
            evidence_strength TEXT,
            materiality_hint TEXT,
            materiality_score REAL,
            specificity_score REAL,
            generic_score REAL,
            boilerplate_score REAL,
            support_quote_count INTEGER DEFAULT 0,
            support_claim_count INTEGER DEFAULT 0,
            support_metric_count INTEGER DEFAULT 0,
            trace_status TEXT DEFAULT 'unknown',
            evidence_chain_count INTEGER DEFAULT 0,
            support_depth INTEGER,
            support_link_count INTEGER DEFAULT 0,
            trace_method TEXT,
            metric_lineage_status TEXT,
            answer_candidate INTEGER DEFAULT 0,
            created_from TEXT,
            builder_version TEXT
        );

        CREATE TABLE IF NOT EXISTS company_topic_source_objects (
            topic_id TEXT NOT NULL,
            object_id TEXT NOT NULL,
            object_type TEXT NOT NULL,
            role TEXT,
            rank INTEGER,
            trace_status TEXT,
            evidence_chain_count INTEGER DEFAULT 0,
            PRIMARY KEY (topic_id, object_id)
        );

        CREATE VIRTUAL TABLE company_topic_fts USING fts5(
            topic_id UNINDEXED,
            ticker UNINDEXED,
            period UNINDEXED,
            text_label,
            text_summary,
            text_evidence,
            text_entities,
            text_channels,
            text_aliases,
            compact_text,
            tokenize = 'unicode61'
        );
        """
    )


def _create_base_secondary_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_objects_scope
            ON objects(ticker, doc_type_key, period, type);
        CREATE INDEX IF NOT EXISTS idx_objects_metric
            ON objects(metric_name, ticker, doc_type_key, period);
        CREATE INDEX IF NOT EXISTS idx_objects_ticker_type_period
            ON objects(ticker, type, period);
        CREATE INDEX IF NOT EXISTS idx_objects_ticker_metric_period
            ON objects(ticker, metric_name, period);
        CREATE INDEX IF NOT EXISTS idx_edges_from
            ON edges(from_id, relation_id);
        CREATE INDEX IF NOT EXISTS idx_edges_to
            ON edges(to_id, relation_id);
        CREATE INDEX IF NOT EXISTS idx_quality_scope
            ON quality_events(ticker, doc_type_key, period, category);
        CREATE INDEX IF NOT EXISTS idx_object_search_text_ticker
            ON object_search_text(ticker);
        CREATE INDEX IF NOT EXISTS idx_object_search_text_type
            ON object_search_text(type);
        CREATE INDEX IF NOT EXISTS idx_object_search_text_ticker_type
            ON object_search_text(ticker, type);
        """
    )


def _create_serving_secondary_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_object_traceability_status
            ON object_traceability(trace_status, object_type);
        CREATE INDEX IF NOT EXISTS idx_object_traceability_ticker_type_status
            ON object_traceability(ticker, object_type, trace_status);
        CREATE INDEX IF NOT EXISTS idx_metric_lookup_ticker_period_metric
            ON metric_lookup(ticker, period, canonical_metric);
        CREATE INDEX IF NOT EXISTS idx_metric_lookup_ticker_year_metric
            ON metric_lookup(ticker, fiscal_year, canonical_metric);
        CREATE INDEX IF NOT EXISTS idx_metric_lookup_ticker_type_period
            ON metric_lookup(ticker, object_type, period);
        CREATE INDEX IF NOT EXISTS idx_metric_lookup_total
            ON metric_lookup(ticker, is_company_total, canonical_metric);
        CREATE INDEX IF NOT EXISTS idx_metric_lookup_ticker_metric_year
            ON metric_lookup(ticker, canonical_metric, fiscal_year, period);
        CREATE INDEX IF NOT EXISTS idx_metric_lookup_total_metric_year
            ON metric_lookup(ticker, canonical_metric, is_company_total, fiscal_year, period);
        CREATE INDEX IF NOT EXISTS idx_metric_dim_ticker_key_metric_year
            ON metric_dimension_lookup(ticker, dimension_key, canonical_metric, fiscal_year, period);
        CREATE INDEX IF NOT EXISTS idx_metric_dim_ticker_metric_year_key
            ON metric_dimension_lookup(ticker, canonical_metric, fiscal_year, dimension_key);
        CREATE INDEX IF NOT EXISTS idx_metric_dim_ticker_kind_key_metric_year
            ON metric_dimension_lookup(ticker, dimension_kind, dimension_key, canonical_metric, fiscal_year, period);
        CREATE INDEX IF NOT EXISTS idx_company_dimension_catalog_ticker
            ON company_dimension_catalog(ticker);
        CREATE INDEX IF NOT EXISTS idx_company_dimension_catalog_lookup
            ON company_dimension_catalog(ticker, dimension_key, dimension_kind);
        CREATE INDEX IF NOT EXISTS idx_exposure_lookup_scope
            ON exposure_lookup(ticker, period, object_type);
        CREATE INDEX IF NOT EXISTS idx_exposure_lookup_factor
            ON exposure_lookup(ticker, factor, benchmark);
        CREATE INDEX IF NOT EXISTS idx_agreement_lookup_scope
            ON agreement_lookup(ticker, period, object_type);
        CREATE INDEX IF NOT EXISTS idx_agreement_lookup_type
            ON agreement_lookup(ticker, agreement_type, maturity_date);
        CREATE INDEX IF NOT EXISTS idx_event_lookup_scope
            ON event_lookup(ticker, period, object_type);
        CREATE INDEX IF NOT EXISTS idx_event_lookup_type
            ON event_lookup(ticker, event_type, event_status, date_sort_key);
        CREATE INDEX IF NOT EXISTS idx_factor_lookup_scope
            ON factor_lookup(ticker, period, object_type);
        CREATE INDEX IF NOT EXISTS idx_factor_lookup_family
            ON factor_lookup(ticker, topic_family, risk_or_driver);
        CREATE INDEX IF NOT EXISTS idx_company_topic_ticker
            ON company_topic_index(ticker);
        CREATE INDEX IF NOT EXISTS idx_company_topic_scope
            ON company_topic_index(ticker, period, document_type, doc_type_key);
        CREATE INDEX IF NOT EXISTS idx_company_topic_ticker_family
            ON company_topic_index(ticker, topic_family);
        CREATE INDEX IF NOT EXISTS idx_company_topic_answerability
            ON company_topic_index(ticker, answer_candidate, trace_status, evidence_strength);
        CREATE INDEX IF NOT EXISTS idx_company_topic_source_object
            ON company_topic_source_objects(object_id);
        CREATE INDEX IF NOT EXISTS idx_company_topic_source_topic
            ON company_topic_source_objects(topic_id);
        CREATE INDEX IF NOT EXISTS idx_company_topic_source_topic_rank
            ON company_topic_source_objects(topic_id, rank);
        """
    )


def _index_artifact(
    conn: sqlite3.Connection,
    root: Path,
    artifact_index_path: Path,
    *,
    replace_fts_entries: bool,
) -> dict[str, int]:
    artifact_index = json.loads(artifact_index_path.read_text())
    ticker = artifact_index["ticker"].upper()
    document_type = artifact_index["document_type"]
    doc_type_key = artifact_index.get("doc_type_key") or normalize_doc_type(document_type)
    period = artifact_index["period"]
    ontology_dir = artifact_index_path.parent
    files = artifact_index.get("files", {})
    object_rows_by_key: dict[str, list[dict[str, Any]]] = {}
    for artifact_key, rel_path in files.items():
        if artifact_key not in OBJECT_FILE_KEYS:
            continue
        path = _resolve_artifact_path(root, rel_path)
        object_rows_by_key[artifact_key] = read_jsonl(path) if path else []

    edge_path = _resolve_artifact_path(root, files.get("edges"))
    edge_rows = read_jsonl(edge_path) if edge_path else []
    objects_by_id = {
        obj["id"]: obj
        for rows in object_rows_by_key.values()
        for obj in rows
        if obj.get("id")
    }
    retrieval_lookup = ObjectLookup(
        objects_by_id=objects_by_id,
        support_links=object_rows_by_key.get("support_links", []),
        edges=edge_rows,
    )
    taxonomy_by_id = _taxonomy_by_id(object_rows_by_key.get("taxonomy_terms", []))

    section_quality = _read_section_quality(ontology_dir)
    conn.execute(
        """
        INSERT OR REPLACE INTO documents(
            ticker, document_type, doc_type_key, period, artifact_index_path,
            ontology_dir, sources_json, reports_json, counts_json,
            section_quality_status, section_quality_json, generated_at, schema_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            document_type,
            doc_type_key,
            period,
            str(artifact_index_path),
            str(ontology_dir),
            json.dumps(artifact_index.get("sources", {}), ensure_ascii=False),
            json.dumps(artifact_index.get("reports", {}), ensure_ascii=False),
            json.dumps(artifact_index.get("counts", {}), ensure_ascii=False),
            section_quality.get("status"),
            json.dumps(section_quality, ensure_ascii=False),
            artifact_index.get("generated_at"),
            artifact_index.get("schema_version"),
        ),
    )

    stats = {"documents": 1, "objects": 0, "edges": 0, "quality_events": 0}
    if section_quality.get("status") in {"warn", "fail"}:
        _insert_quality_event(
            conn,
            ticker=ticker,
            document_type=document_type,
            doc_type_key=doc_type_key,
            period=period,
            severity=section_quality["status"],
            category="section_quality",
            object_id=None,
            stage="extract_sections",
            message=_section_quality_message(section_quality),
            payload=section_quality,
        )
        stats["quality_events"] += 1

    for artifact_key, rel_path in files.items():
        path = _resolve_artifact_path(root, rel_path)
        if artifact_key == "edges":
            stats["edges"] += _index_edges(
                conn,
                path,
                ticker,
                document_type,
                doc_type_key,
                period,
                rows=edge_rows,
            )
        elif artifact_key == "quality_events":
            stats["quality_events"] += _index_quality_events(
                conn, path, ticker, document_type, doc_type_key, period
            )
        elif artifact_key in OBJECT_FILE_KEYS:
            stats["objects"] += _index_objects(
                conn,
                path,
                artifact_key,
                ticker,
                document_type,
                doc_type_key,
                period,
                replace_fts_entries=replace_fts_entries,
                rows=object_rows_by_key.get(artifact_key),
                retrieval_lookup=retrieval_lookup,
                taxonomy_by_id=taxonomy_by_id,
            )

    rejected_path = _resolve_artifact_path(root, files.get("rejected_objects"))
    rejected_objects = read_jsonl(rejected_path) if rejected_path else []
    for obj in rejected_objects:
        _insert_object(
            conn,
            obj,
            artifact_key="rejected_objects",
            artifact_path=rejected_path or ontology_dir / "rejected_objects.jsonl",
            ticker=ticker,
            document_type=document_type,
            doc_type_key=doc_type_key,
            period=period,
            forced_review_status="rejected",
            replace_fts_entries=replace_fts_entries,
            retrieval_lookup=None,
            taxonomy_by_id=None,
        )
        stats["objects"] += 1
        _insert_quality_event(
            conn,
            ticker=ticker,
            document_type=document_type,
            doc_type_key=doc_type_key,
            period=period,
            severity="warn",
            category="rejected_object",
            object_id=obj.get("id"),
            stage=obj.get("rejection_stage") or obj.get("stage"),
            message=obj.get("rejection_reason") or obj.get("reason") or "Rejected object",
            payload=obj,
        )
        stats["quality_events"] += 1

    failures_path = _resolve_artifact_path(root, files.get("batch_failures"))
    failures = read_jsonl(failures_path) if failures_path else []
    for failure in failures:
        _insert_quality_event(
            conn,
            ticker=ticker,
            document_type=document_type,
            doc_type_key=doc_type_key,
            period=period,
            severity="error",
            category="batch_failure",
            object_id=failure.get("id"),
            stage=failure.get("stage"),
            message=failure.get("error") or failure.get("message") or "Batch failure",
            payload=failure,
        )
        stats["quality_events"] += 1

    return stats


def _index_objects(
    conn: sqlite3.Connection,
    path: Path | None,
    artifact_key: str,
    ticker: str,
    document_type: str,
    doc_type_key: str,
    period: str,
    *,
    replace_fts_entries: bool,
    rows: list[dict[str, Any]] | None = None,
    retrieval_lookup: ObjectLookup | None = None,
    taxonomy_by_id: dict[str, dict[str, Any]] | None = None,
) -> int:
    if not path and rows is None:
        return 0
    count = 0
    object_rows: list[tuple[Any, ...]] = []
    fts_rows: list[tuple[Any, ...]] = []
    for obj in rows if rows is not None else read_jsonl(path):
        if not obj.get("id") or not obj.get("type"):
            continue
        object_row, fts_row = _build_object_insert_rows(
            obj,
            artifact_key=artifact_key,
            artifact_path=path,
            ticker=ticker,
            document_type=document_type,
            doc_type_key=doc_type_key,
            period=period,
            retrieval_lookup=retrieval_lookup,
            taxonomy_by_id=taxonomy_by_id,
        )
        object_rows.append(object_row)
        if fts_row is not None:
            fts_rows.append(fts_row)
        count += 1
        if len(object_rows) >= BULK_INSERT_CHUNK_SIZE:
            _flush_object_insert_batch(
                conn,
                object_rows,
                fts_rows,
                replace_fts_entries=replace_fts_entries,
            )
    _flush_object_insert_batch(
        conn,
        object_rows,
        fts_rows,
        replace_fts_entries=replace_fts_entries,
    )
    return count


def _index_quality_events(
    conn: sqlite3.Connection,
    path: Path | None,
    ticker: str,
    document_type: str,
    doc_type_key: str,
    period: str,
) -> int:
    if not path:
        return 0
    count = 0
    for event in read_jsonl(path):
        _insert_quality_event(
            conn,
            ticker=str(event.get("ticker") or ticker).upper(),
            document_type=str(event.get("document_type") or document_type),
            doc_type_key=str(event.get("doc_type_key") or doc_type_key),
            period=str(event.get("period") or period),
            severity=str(event.get("severity") or "warn"),
            category=str(event.get("category") or "quality_event"),
            object_id=event.get("object_id"),
            stage=event.get("stage"),
            message=str(event.get("message") or "Quality event"),
            payload=event,
        )
        count += 1
    return count


def _rebuild_object_traceability(conn: sqlite3.Connection) -> int:
    """Compute serving-layer evidence traceability without mutating artifacts."""
    conn.execute("DELETE FROM object_traceability")
    rows = conn.execute(
        """
        SELECT id, type, ticker, document_type, period, json
        FROM objects
        WHERE review_status IS NULL OR review_status != 'rejected'
        """
    ).fetchall()
    objects: dict[str, dict[str, Any]] = {}
    for row in rows:
        obj = json.loads(row["json"])
        obj.setdefault("id", row["id"])
        obj.setdefault("type", row["type"])
        obj.setdefault("ticker", row["ticker"])
        obj.setdefault("document_type", row["document_type"])
        obj.setdefault("period", row["period"])
        objects[row["id"]] = obj

    incoming_support: dict[str, list[dict[str, Any]]] = defaultdict(list)
    calculations_by_output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for obj in objects.values():
        if obj.get("type") == "SupportLink":
            support_id = obj.get("support_object_id") or obj.get("from_id")
            target_id = obj.get("target_object_id") or obj.get("to_id")
            if support_id in objects and target_id in objects:
                incoming_support[str(target_id)].append(obj)
        if obj.get("type") == "Calculation" and obj.get("output_metric_id"):
            calculations_by_output[str(obj["output_metric_id"])].append(obj)

    count = 0
    trace_rows: list[tuple[Any, ...]] = []
    for obj in objects.values():
        info = _traceability_for_object(obj, objects, incoming_support, calculations_by_output)
        trace_rows.append(
            (
                obj["id"],
                obj.get("type") or "Unknown",
                obj.get("ticker") or "",
                obj.get("document_type") or "",
                obj.get("period") or "",
                info["trace_status"],
                info["evidence_chain_count"],
                info.get("support_depth"),
                info["support_quote_count"],
                info["support_claim_count"],
                info["support_link_count"],
                info.get("trace_method"),
                info.get("metric_lineage_status"),
                1 if info.get("answer_candidate") else 0,
                json.dumps(info, ensure_ascii=False),
            )
        )
        count += 1
        if len(trace_rows) >= BULK_INSERT_CHUNK_SIZE:
            _flush_traceability_batch(conn, trace_rows)
    _flush_traceability_batch(conn, trace_rows)
    return count


def _flush_traceability_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO object_traceability(
            object_id, object_type, ticker, document_type, period,
            trace_status, evidence_chain_count, support_depth,
            support_quote_count, support_claim_count, support_link_count,
            trace_method, metric_lineage_status, answer_candidate, json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    rows.clear()


def _traceability_for_object(
    obj: dict[str, Any],
    objects: dict[str, dict[str, Any]],
    incoming_support: dict[str, list[dict[str, Any]]],
    calculations_by_output: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    obj_id = str(obj.get("id") or "")
    obj_type = str(obj.get("type") or "")
    incoming = incoming_support.get(obj_id, [])
    answer_candidate = obj_type in ANSWER_CANDIDATE_TYPES

    if not answer_candidate:
        return {
            "trace_status": "index_only",
            "evidence_chain_count": 0,
            "support_depth": None,
            "support_quote_count": 0,
            "support_claim_count": 0,
            "support_link_count": len(incoming),
            "trace_method": "index_only",
            "metric_lineage_status": None,
            "answer_candidate": False,
        }

    if obj_type == "EvidenceQuote":
        span_id = obj.get("source_span_id")
        traceable = bool(span_id and span_id in objects)
        return {
            "trace_status": "traceable" if traceable else "untraced",
            "evidence_chain_count": 1 if traceable else 0,
            "support_depth": 1 if traceable else None,
            "support_quote_count": 1,
            "support_claim_count": 0,
            "support_link_count": len(incoming),
            "trace_method": "source_span" if traceable else "none",
            "metric_lineage_status": None,
            "answer_candidate": True,
        }

    if obj_type == "ResearchClaim":
        quote_ids = _support_ids(
            obj,
            objects,
            incoming,
            field_names=("supported_by_quotes",),
            support_types={"EvidenceQuote"},
        )
        return {
            "trace_status": "traceable" if quote_ids else "untraced",
            "evidence_chain_count": len(quote_ids),
            "support_depth": 1 if quote_ids else None,
            "support_quote_count": len(quote_ids),
            "support_claim_count": 1,
            "support_link_count": len(incoming),
            "trace_method": _trace_method(obj, incoming, quote_ids),
            "metric_lineage_status": None,
            "answer_candidate": True,
        }

    if obj_type == "MetricObservation":
        fact_ids = _support_ids(
            obj,
            objects,
            incoming,
            field_names=("source_fact_ids",),
            support_types={"XBRLFact"},
        )
        source_metric_ids = _support_ids(
            obj,
            objects,
            incoming,
            field_names=("source_metric_ids",),
            support_types={"MetricObservation"},
        )
        calculation_ids = _unique_list(
            [
                *([obj["calculation_id"]] if obj.get("calculation_id") else []),
                *[calc.get("id") for calc in calculations_by_output.get(obj_id, []) if calc.get("id")],
                *[
                    _support_object_id(link)
                    for link in incoming
                    if _object_type(objects, _support_object_id(link)) == "Calculation"
                ],
            ]
        )
        traceable = bool(fact_ids or source_metric_ids or calculation_ids)
        return {
            "trace_status": "traceable_metric_lineage" if traceable else "untraced_metric_candidate",
            "evidence_chain_count": len(fact_ids) + len(source_metric_ids) + len(calculation_ids),
            "support_depth": 1 if fact_ids else (2 if source_metric_ids or calculation_ids else None),
            "support_quote_count": 0,
            "support_claim_count": 0,
            "support_link_count": len(incoming),
            "trace_method": "metric_lineage" if traceable else "none",
            "metric_lineage_status": "traceable_metric_lineage" if traceable else "missing_metric_lineage",
            "answer_candidate": True,
        }

    if obj_type in SEMANTIC_SUPPORT_TYPES:
        claim_ids = _support_ids(
            obj,
            objects,
            incoming,
            field_names=("supported_by_claims",),
            support_types={"ResearchClaim"},
        )
        direct_quote_ids = _support_ids(
            obj,
            objects,
            incoming,
            field_names=("supported_by_quotes",),
            support_types={"EvidenceQuote"},
        )
        claim_quote_ids = _unique_list(
            quote_id
            for claim_id in claim_ids
            for quote_id in _quote_ids_for_claim(objects.get(claim_id), objects, incoming_support)
        )
        quote_ids = _unique_list([*direct_quote_ids, *claim_quote_ids])
        traceable = bool(claim_ids or quote_ids)
        return {
            "trace_status": "traceable" if traceable else "orphan",
            "evidence_chain_count": len(claim_ids) + len(quote_ids),
            "support_depth": 2 if claim_quote_ids else (1 if direct_quote_ids or claim_ids else None),
            "support_quote_count": len(quote_ids),
            "support_claim_count": len(claim_ids),
            "support_link_count": len(incoming),
            "trace_method": _trace_method(obj, incoming, [*claim_ids, *quote_ids]),
            "metric_lineage_status": None,
            "answer_candidate": True,
        }

    if obj_type == "Calculation":
        metric_ids = _unique_list([*(obj.get("input_metric_ids") or []), *(obj.get("source_metric_ids") or [])])
        output_id = obj.get("output_metric_id")
        traceable = bool(metric_ids or output_id)
        return {
            "trace_status": "traceable_metric_lineage" if traceable else "untraced_metric_candidate",
            "evidence_chain_count": len(metric_ids) + (1 if output_id else 0),
            "support_depth": 1 if traceable else None,
            "support_quote_count": 0,
            "support_claim_count": 0,
            "support_link_count": len(incoming),
            "trace_method": "metric_lineage" if traceable else "none",
            "metric_lineage_status": "traceable_metric_lineage" if traceable else "missing_metric_lineage",
            "answer_candidate": True,
        }

    return {
        "trace_status": "untraced",
        "evidence_chain_count": 0,
        "support_depth": None,
        "support_quote_count": 0,
        "support_claim_count": 0,
        "support_link_count": len(incoming),
        "trace_method": "none",
        "metric_lineage_status": None,
        "answer_candidate": True,
    }


def _support_ids(
    obj: dict[str, Any],
    objects: dict[str, dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    field_names: tuple[str, ...],
    support_types: set[str],
) -> list[str]:
    values: list[str] = []
    for field_name in field_names:
        values.extend(obj.get(field_name) or [])
    for link in incoming:
        support_id = _support_object_id(link)
        if _object_type(objects, support_id) in support_types:
            values.append(support_id)
    return _unique_list(value for value in values if value in objects)


def _support_object_id(link: dict[str, Any]) -> str:
    return str(link.get("support_object_id") or link.get("from_id") or "")


def _object_type(objects: dict[str, dict[str, Any]], object_id: str) -> str | None:
    return (objects.get(object_id) or {}).get("type")


def _quote_ids_for_claim(
    claim: dict[str, Any] | None,
    objects: dict[str, dict[str, Any]],
    incoming_support: dict[str, list[dict[str, Any]]],
) -> list[str]:
    if not claim:
        return []
    quote_ids = list(claim.get("supported_by_quotes") or [])
    for link in incoming_support.get(str(claim.get("id") or ""), []):
        support_id = _support_object_id(link)
        if _object_type(objects, support_id) == "EvidenceQuote":
            quote_ids.append(support_id)
    return _unique_list(quote_id for quote_id in quote_ids if quote_id in objects)


def _trace_method(obj: dict[str, Any], incoming: list[dict[str, Any]], support_ids: list[str]) -> str:
    if incoming and support_ids:
        return "explicit_support_link"
    if support_ids:
        return "reference_fields"
    return "none"


def _unique_list(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _insert_object(
    conn: sqlite3.Connection,
    obj: dict[str, Any],
    *,
    artifact_key: str,
    artifact_path: Path,
    ticker: str,
    document_type: str,
    doc_type_key: str,
    period: str,
    forced_review_status: str | None = None,
    replace_fts_entries: bool,
    retrieval_lookup: ObjectLookup | None,
    taxonomy_by_id: dict[str, dict[str, Any]] | None,
) -> None:
    object_row, fts_row = _build_object_insert_rows(
        obj,
        artifact_key=artifact_key,
        artifact_path=artifact_path,
        ticker=ticker,
        document_type=document_type,
        doc_type_key=doc_type_key,
        period=period,
        forced_review_status=forced_review_status,
        retrieval_lookup=retrieval_lookup,
        taxonomy_by_id=taxonomy_by_id,
    )
    _flush_object_insert_batch(
        conn,
        [object_row],
        [fts_row] if fts_row is not None else [],
        replace_fts_entries=replace_fts_entries,
    )


def _build_object_insert_rows(
    obj: dict[str, Any],
    *,
    artifact_key: str,
    artifact_path: Path | None,
    ticker: str,
    document_type: str,
    doc_type_key: str,
    period: str,
    forced_review_status: str | None = None,
    retrieval_lookup: ObjectLookup | None,
    taxonomy_by_id: dict[str, dict[str, Any]] | None,
) -> tuple[tuple[Any, ...], tuple[Any, ...] | None]:
    retrieval_text = (
        build_retrieval_text(obj, retrieval_lookup, taxonomy_by_id)
        if retrieval_lookup is not None
        else fallback_retrieval_text(obj)
    )
    text = retrieval_text.compact_text or retrieval_text.text_self or _object_text(obj)
    review_status = forced_review_status or obj.get("review_status")
    object_row = (
        obj["id"],
        obj["type"],
        obj.get("ticker", ticker),
        obj.get("document_type", document_type),
        doc_type_key,
        obj.get("period", period),
        obj.get("source_document_id"),
        obj.get("section_name") or obj.get("section_key"),
        obj.get("metric_name"),
        review_status,
        obj.get("confidence"),
        text,
        json.dumps(obj, ensure_ascii=False),
        artifact_key,
        str(artifact_path or ""),
    )
    fts_row = None
    if retrieval_text.joined and obj.get("type") != "SupportLink":
        fts_row = (
            obj["id"],
            obj["type"],
            obj.get("ticker", ticker),
            obj.get("document_type", document_type),
            obj.get("period", period),
            retrieval_text.text_self,
            retrieval_text.text_support,
            retrieval_text.text_related,
            retrieval_text.text_entities,
            retrieval_text.text_aliases,
            retrieval_text.compact_text,
        )
    return object_row, fts_row


def _flush_object_insert_batch(
    conn: sqlite3.Connection,
    object_rows: list[tuple[Any, ...]],
    fts_rows: list[tuple[Any, ...]],
    *,
    replace_fts_entries: bool,
) -> None:
    if object_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO objects(
                id, type, ticker, document_type, doc_type_key, period,
                source_document_id, section_name, metric_name, review_status,
                confidence, text, json, artifact_key, artifact_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            object_rows,
        )
    if fts_rows:
        fts_rows = [_object_fts_row_with_scope_tokens(row) for row in fts_rows]
        if replace_fts_entries:
            conn.executemany("DELETE FROM object_fts WHERE object_id = ?", ((row[0],) for row in fts_rows))
            conn.executemany("DELETE FROM object_search_text WHERE object_id = ?", ((row[0],) for row in fts_rows))
            conn.executemany("DELETE FROM object_text WHERE object_id = ?", ((row[0],) for row in fts_rows))
        conn.executemany(
            """
            INSERT INTO object_fts(
                object_id, type, ticker, document_type, period,
                text_self, text_support, text_related, text_entities,
                text_aliases, compact_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fts_rows,
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO object_search_text(
                object_id, type, ticker, document_type, period,
                text_self, text_support, text_related, text_entities,
                text_aliases, compact_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fts_rows,
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO object_text(
                object_id, type, ticker, document_type, period,
                text_self, text_support, text_related, text_entities,
                text_aliases, compact_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fts_rows,
        )
    object_rows.clear()
    fts_rows.clear()


def _rebuild_metric_lookup(conn: sqlite3.Connection) -> int:
    """Materialize metric-oriented objects for structured numeric lookup."""
    conn.execute("DELETE FROM metric_lookup")
    inserted_count = 0
    rows = conn.execute(
        """
        SELECT
            objects.id,
            objects.type,
            objects.ticker,
            objects.document_type,
            objects.doc_type_key,
            objects.period,
            objects.metric_name,
            objects.text,
            objects.json,
            object_traceability.trace_status,
            object_traceability.metric_lineage_status
        FROM objects
        LEFT JOIN object_traceability
          ON object_traceability.object_id = objects.id
        WHERE objects.type IN ('MetricObservation', 'Calculation')
          AND (objects.review_status IS NULL OR objects.review_status != 'rejected')
        """
    ).fetchall()
    lookup_rows: list[tuple[Any, ...]] = []
    for row in rows:
        try:
            obj = json.loads(row["json"] or "{}")
        except json.JSONDecodeError:
            obj = {}
        metric_name = str(row["metric_name"] or _metric_lookup_metric_name(obj, row["type"]) or "")
        metric_text = obj.get("text") or row["text"]
        canonical_metric = _metric_lookup_canonical(metric_name)
        dimensions = _metric_lookup_normalize_dimensions(obj.get("dimensions") or obj.get("dimension") or {})
        inferred_dimension = _metric_lookup_infer_dimension_from_text(
            metric_name=metric_name,
            text=metric_text,
            dimensions=dimensions,
        )
        if inferred_dimension and inferred_dimension[1] not in set(dimensions.values()):
            dimensions[f"inferred_{inferred_dimension[0]}"] = inferred_dimension[1]
        segment_name = _metric_lookup_dimension(dimensions, ("segment", "segment_name", "business_segment"))
        product_name = _metric_lookup_dimension(dimensions, ("product", "product_name", "product_line"))
        geography_name = _metric_lookup_dimension(dimensions, ("geography", "geography_name", "region", "country"))
        fiscal_year = _metric_lookup_year(obj, row["period"])
        fiscal_quarter = _metric_lookup_quarter(obj, row["period"])
        alias_text = _metric_lookup_alias_text(
            metric_name=metric_name,
            canonical_metric=canonical_metric,
            obj=obj,
            dimensions=dimensions,
            text=metric_text,
        )
        lookup_rows.append(
            (
                row["id"],
                row["type"],
                row["ticker"],
                row["document_type"],
                row["doc_type_key"],
                row["period"],
                fiscal_year,
                fiscal_quarter,
                metric_name or None,
                canonical_metric or None,
                alias_text,
                _metric_lookup_value_text(obj),
                _metric_lookup_unit(obj),
                json.dumps(dimensions, ensure_ascii=False, sort_keys=True),
                1
                if _metric_lookup_is_company_total(
                    metric_name=metric_name,
                    dimensions=dimensions,
                    text=metric_text,
                )
                else 0,
                segment_name,
                product_name,
                geography_name,
                row["trace_status"],
                row["metric_lineage_status"],
                metric_text,
            )
        )
        inserted_count += 1
        if len(lookup_rows) >= BULK_INSERT_CHUNK_SIZE:
            _flush_metric_lookup_batch(conn, lookup_rows)

    xbrl_rows = conn.execute(
        """
        SELECT
            objects.id,
            objects.type,
            objects.ticker,
            objects.document_type,
            objects.doc_type_key,
            objects.period,
            objects.text,
            objects.json,
            object_traceability.trace_status,
            object_traceability.metric_lineage_status
        FROM objects
        LEFT JOIN object_traceability
          ON object_traceability.object_id = objects.id
        WHERE objects.type = 'XBRLFact'
          AND (objects.review_status IS NULL OR objects.review_status != 'rejected')
        """
    ).fetchall()
    for row in xbrl_rows:
        try:
            obj = json.loads(row["json"] or "{}")
        except json.JSONDecodeError:
            obj = {}
        dimensions = _metric_lookup_xbrl_dimensions(obj)
        if not dimensions:
            continue
        metric_name = _metric_lookup_xbrl_metric_name(obj)
        if not metric_name:
            continue
        metric_text = _metric_lookup_xbrl_text(
            obj=obj,
            fallback_text=row["text"],
            metric_name=metric_name,
            period=row["period"],
        )
        canonical_metric = _metric_lookup_canonical(metric_name)
        segment_name = _metric_lookup_dimension(dimensions, ("segment", "segment_name", "business_segment"))
        product_name = _metric_lookup_dimension(dimensions, ("product", "product_name", "product_line"))
        geography_name = _metric_lookup_dimension(dimensions, ("geography", "geography_name", "region", "country"))
        fiscal_year = _metric_lookup_year(obj, row["period"])
        fiscal_quarter = _metric_lookup_quarter(obj, row["period"])
        alias_text = _metric_lookup_alias_text(
            metric_name=metric_name,
            canonical_metric=canonical_metric,
            obj=obj,
            dimensions=dimensions,
            text=metric_text,
        )
        lookup_rows.append(
            (
                row["id"],
                row["type"],
                row["ticker"],
                row["document_type"],
                row["doc_type_key"],
                row["period"],
                fiscal_year,
                fiscal_quarter,
                metric_name,
                canonical_metric or None,
                alias_text,
                _metric_lookup_value_text(obj),
                _metric_lookup_unit(obj),
                json.dumps(dimensions, ensure_ascii=False, sort_keys=True),
                1
                if _metric_lookup_is_company_total(
                    metric_name=metric_name,
                    dimensions=dimensions,
                    text=metric_text,
                )
                else 0,
                segment_name,
                product_name,
                geography_name,
                row["trace_status"],
                row["metric_lineage_status"],
                metric_text,
            )
        )
        inserted_count += 1
        if len(lookup_rows) >= BULK_INSERT_CHUNK_SIZE:
            _flush_metric_lookup_batch(conn, lookup_rows)
    _flush_metric_lookup_batch(conn, lookup_rows)
    return inserted_count


def _flush_metric_lookup_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO metric_lookup(
            object_id, object_type, ticker, document_type, doc_type_key,
            period, fiscal_year, fiscal_quarter, metric_name, canonical_metric,
            metric_alias_text, value_text, unit, dimensions_json,
            is_company_total, segment_name, product_name, geography_name,
            trace_status, metric_lineage_status, text
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    rows.clear()


def _rebuild_metric_dimension_lookup(conn: sqlite3.Connection) -> int:
    """Materialize normalized dimension keys for metric series lookup."""
    conn.execute("DELETE FROM metric_dimension_lookup")
    rows = conn.execute(
        """
        SELECT
            object_id,
            ticker,
            period,
            fiscal_year,
            fiscal_quarter,
            canonical_metric,
            dimensions_json,
            is_company_total,
            trace_status,
            metric_lineage_status
        FROM metric_lookup
        WHERE canonical_metric IS NOT NULL
        """
    ).fetchall()
    dimension_rows: list[tuple[Any, ...]] = []
    for row in rows:
        try:
            dimensions = json.loads(row["dimensions_json"] or "{}")
        except json.JSONDecodeError:
            dimensions = {}
        if not isinstance(dimensions, Mapping):
            dimensions = {}
        if row["is_company_total"]:
            dimension_rows.append(
                (
                    row["object_id"],
                    row["ticker"],
                    row["period"],
                    row["fiscal_year"],
                    row["fiscal_quarter"],
                    row["canonical_metric"],
                    "company_total",
                    "__company_total__",
                    "Company Total",
                    None,
                    None,
                    1,
                    row["trace_status"],
                    row["metric_lineage_status"],
                )
            )
        else:
            seen_keys: set[str] = set()
            for axis, label in dimensions.items():
                clean_label = _metric_lookup_clean_dimension_label(label)
                if not clean_label:
                    continue
                dimension_key = _metric_dimension_key(clean_label)
                if not dimension_key or dimension_key in seen_keys:
                    continue
                seen_keys.add(dimension_key)
                dimension_rows.append(
                    (
                        row["object_id"],
                        row["ticker"],
                        row["period"],
                        row["fiscal_year"],
                        row["fiscal_quarter"],
                        row["canonical_metric"],
                        _metric_dimension_kind(axis, clean_label),
                        dimension_key,
                        clean_label,
                        _metric_dimension_key(axis),
                        _metric_dimension_key(clean_label),
                        0,
                        row["trace_status"],
                        row["metric_lineage_status"],
                    )
                )
        if len(dimension_rows) >= BULK_INSERT_CHUNK_SIZE:
            _flush_metric_dimension_lookup_batch(conn, dimension_rows)
    _flush_metric_dimension_lookup_batch(conn, dimension_rows)
    return conn.execute("SELECT COUNT(*) FROM metric_dimension_lookup").fetchone()[0]


def _flush_metric_dimension_lookup_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO metric_dimension_lookup(
            object_id, ticker, period, fiscal_year, fiscal_quarter,
            canonical_metric, dimension_kind, dimension_key, dimension_label,
            axis_key, member_key, is_company_total, trace_status, metric_lineage_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    rows.clear()


def _rebuild_company_dimension_catalog(conn: sqlite3.Connection) -> int:
    """Build ticker-level dimension catalog from normalized metric dimensions."""
    conn.execute("DELETE FROM company_dimension_catalog")
    rows = conn.execute(
        """
        SELECT ticker, dimension_key, dimension_label, dimension_kind, period, object_id
        FROM metric_dimension_lookup
        WHERE is_company_total = 0
        """
    ).fetchall()
    catalog: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["ticker"], row["dimension_key"])
        entry = catalog.setdefault(
            key,
            {
                "ticker": row["ticker"],
                "dimension_key": row["dimension_key"],
                "labels": {},
                "kinds": {},
                "periods": set(),
                "object_ids": set(),
                "aliases": set(),
            },
        )
        label = row["dimension_label"] or row["dimension_key"]
        kind = row["dimension_kind"] or "unknown"
        entry["labels"][label] = entry["labels"].get(label, 0) + 1
        entry["kinds"][kind] = entry["kinds"].get(kind, 0) + 1
        if row["period"]:
            entry["periods"].add(row["period"])
        if row["object_id"]:
            entry["object_ids"].add(row["object_id"])
        entry["aliases"].update(_metric_dimension_aliases(label, row["dimension_key"]))
    catalog_rows: list[tuple[Any, ...]] = []
    for entry in catalog.values():
        label = max(entry["labels"].items(), key=lambda item: (item[1], len(item[0])))[0]
        kind = max(entry["kinds"].items(), key=lambda item: item[1])[0]
        catalog_rows.append(
            (
                entry["ticker"],
                entry["dimension_key"],
                label,
                kind,
                len(entry["object_ids"]),
                json.dumps(sorted(entry["periods"]), ensure_ascii=False),
                " ".join(sorted(str(alias) for alias in entry["aliases"] if alias)),
            )
        )
    if catalog_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO company_dimension_catalog(
                ticker, dimension_key, dimension_label, dimension_kind,
                source_metric_count, periods_json, aliases_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            catalog_rows,
        )
    return len(catalog_rows)


def _metric_lookup_metric_name(obj: Mapping[str, Any], object_type: str) -> str:
    if object_type == "Calculation":
        for key in ("output_metric_name", "output_metric_id", "calculation_type", "name"):
            if obj.get(key):
                return str(obj[key])
    for key in ("metric_name", "canonical_metric", "name", "label"):
        if obj.get(key):
            return str(obj[key])
    return ""


def _metric_lookup_canonical(metric_name: str | None) -> str:
    terms = re.findall(r"[A-Za-z0-9_]+", str(metric_name or "").lower())
    return "_".join(term for term in terms if term)


def _metric_lookup_year(obj: Mapping[str, Any], period: Any) -> int | None:
    for key in ("fiscal_year", "year", "calendar_year"):
        value = obj.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    context = obj.get("context")
    if isinstance(context, Mapping):
        value = context.get("fiscal_year") or context.get("year") or context.get("calendar_year")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    match = re.search(r"(?:CY|FY)?(20\d{2}|19\d{2})", str(period or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _metric_lookup_quarter(obj: Mapping[str, Any], period: Any) -> int | None:
    for key in ("fiscal_quarter", "quarter"):
        value = obj.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    match = re.search(r"Q([1-4])", str(period or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _metric_lookup_xbrl_metric_name(obj: Mapping[str, Any]) -> str | None:
    tag = str(obj.get("safe_taxonomy_tag") or obj.get("taxonomy_tag") or "").split(":")[-1]
    tag_key = _metric_dimension_key(tag)
    if not tag_key:
        return None
    if "remaining_performance_obligation" in tag_key or tag_key.endswith("percentage"):
        return None
    if (
        tag_key in {"revenue", "revenues"}
        or "revenue_from_contract" in tag_key
        or "sales_revenue_net" in tag_key
        or "net_sales" in tag_key
    ):
        return "revenue"
    if "cost_of_goods" in tag_key or "cost_of_revenue" in tag_key or "cost_of_goods_and_services_sold" in tag_key:
        return "cost_of_revenue"
    if "gross_profit" in tag_key:
        return "gross_profit"
    if "operating_income_loss" in tag_key or "operating_income" in tag_key:
        return "operating_income"
    if "net_income_loss" in tag_key or tag_key == "net_income":
        return "net_income"
    if tag_key in {"assets", "liabilities", "stockholders_equity"}:
        return tag_key
    if "cash_and_cash_equivalents" in tag_key:
        return "cash_and_cash_equivalents"
    if "capital_expenditure" in tag_key or "payments_to_acquire_property_plant_and_equipment" in tag_key:
        return "capex"
    return None


def _metric_lookup_xbrl_dimensions(obj: Mapping[str, Any]) -> dict[str, str]:
    context = obj.get("context") if isinstance(obj.get("context"), Mapping) else {}
    raw_dimensions = (
        obj.get("dimensions")
        or obj.get("dimension")
        or context.get("dimensions")
        or context.get("dimension")
        or {}
    )
    return _metric_lookup_normalize_dimensions(raw_dimensions)


def _metric_lookup_xbrl_text(
    *,
    obj: Mapping[str, Any],
    fallback_text: Any,
    metric_name: str,
    period: Any,
) -> str:
    dimensions = _metric_lookup_xbrl_dimensions(obj)
    dimension_text = ", ".join(_metric_lookup_clean_dimension_label(value) for value in dimensions.values() if value)
    role_text = dimension_text or "company total"
    value_text = _metric_lookup_value_text(obj)
    unit = _metric_lookup_unit(obj)
    tag = str(obj.get("safe_taxonomy_tag") or obj.get("taxonomy_tag") or "").split(":")[-1]
    parts = [str(period or "").strip(), role_text, metric_name]
    if value_text:
        parts.append(str(value_text))
    if unit:
        parts.append(str(unit))
    if tag:
        parts.append(tag)
    text = " ".join(part for part in parts if part)
    return text or str(fallback_text or "")


def _metric_lookup_value_text(obj: Mapping[str, Any]) -> str | None:
    for key in ("value", "numeric_value", "amount", "reported_value"):
        value = obj.get(key)
        if value is not None:
            return str(value)
    return None


def _metric_lookup_unit(obj: Mapping[str, Any]) -> str | None:
    for key in ("unit", "unit_ref", "currency", "scale"):
        value = obj.get(key)
        if value:
            return str(value)
    return None


def _metric_lookup_normalize_dimensions(raw_dimensions: Any) -> dict[str, str]:
    dimensions: dict[str, str] = {}

    def put(key: Any, value: Any) -> None:
        if key is None or value is None:
            return
        key_text = _metric_lookup_clean_dimension_label(key)
        value_text = _metric_lookup_clean_dimension_label(value)
        if key_text and value_text:
            dimensions[key_text] = value_text

    def walk(value: Any, fallback_key: Any = None) -> None:
        if isinstance(value, Mapping):
            axis = value.get("axis") or value.get("dimension") or value.get("key") or value.get("name")
            member = (
                value.get("member")
                or value.get("value")
                or value.get("label")
                or value.get("member_label")
                or value.get("dimension_value")
            )
            if axis and member:
                put(axis, member)
                return
            for key, child in value.items():
                if isinstance(child, (Mapping, list, tuple)):
                    walk(child, key)
                else:
                    put(key, child)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and fallback_key is None:
                    put(item, item)
                else:
                    walk(item, fallback_key)
            return
        if fallback_key is not None:
            put(fallback_key, value)

    walk(raw_dimensions)
    return dimensions


def _metric_lookup_clean_dimension_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("#")[-1].split("/")[-1].split(":")[-1]
    text = re.sub(r"(?:Member|Axis|Domain)$", "", text)
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _metric_dimension_key(value: Any) -> str:
    text = _metric_lookup_clean_dimension_label(value).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _metric_dimension_kind(axis: Any, label: Any) -> str:
    axis_key = _metric_dimension_key(axis)
    if any(term in axis_key for term in ("geograph", "region", "country", "area")):
        return "geography"
    if any(term in axis_key for term in ("product", "service", "brand")):
        return "product"
    if any(term in axis_key for term in ("segment", "business", "division")):
        return "segment"
    if any(term in axis_key for term in ("customer", "client")):
        return "customer"
    if any(term in axis_key for term in ("channel", "market")):
        return "channel"
    return "unknown"


def _metric_lookup_dimension(dimensions: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = dimensions.get(key)
        if value:
            return _metric_lookup_clean_dimension_label(value)
    for key, value in dimensions.items():
        key_lower = str(key).lower()
        if any(marker in key_lower for marker in keys) and value:
            return _metric_lookup_clean_dimension_label(value)
    return None


def _metric_lookup_infer_dimension_from_text(
    *,
    metric_name: str,
    text: Any,
    dimensions: Mapping[str, Any],
) -> tuple[str, str] | None:
    if any(value for value in dimensions.values()):
        return None
    haystacks = [str(text or ""), metric_name]
    metric_phrases = (
        "net sales",
        "operating income",
        "operating profit",
        "gross profit",
        "gross margin",
        "operating margin",
        "membership fee revenue",
        "subscription revenue",
        "service revenue",
        "services revenue",
        "product revenue",
        "revenue",
        "revenues",
        "sales",
        "income",
        "margin",
    )
    for haystack in haystacks:
        clean = re.sub(r"\b(?:CY|FY)?(?:19|20)\d{2}(?:Q[1-4])?\b", " ", haystack, flags=re.IGNORECASE)
        clean = re.sub(r"[$€£¥]?\d+(?:\.\d+)?\s*(?:billion|million|thousand|bn|mm|m|b)?", " ", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s+", " ", clean).strip()
        for phrase in metric_phrases:
            match = re.search(
                rf"\b(?P<label>[A-Za-z][A-Za-z0-9&/ .'-]{{1,80}}?)\s+{re.escape(phrase)}\b",
                clean,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            label = _metric_lookup_clean_inferred_dimension_label(match.group("label"))
            if not label:
                continue
            return (_metric_lookup_dimension_kind(label), label)
    return None


def _metric_lookup_clean_inferred_dimension_label(label: str) -> str:
    text = re.sub(r"\b(?:the|a|an|our|company|total|consolidated|net)\b", " ", label, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:was|were|is|are|for|of|and|from|in|to|by)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[^A-Za-z0-9&/ .'-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .:-")
    if not text:
        return ""
    generic = {
        "revenue",
        "revenues",
        "sales",
        "income",
        "margin",
        "profit",
        "cost",
        "expense",
        "expenses",
        "assets",
        "liabilities",
    }
    if text.lower() in generic:
        return ""
    tokens = text.split()
    if len(tokens) > 6:
        return ""
    return text


def _metric_lookup_dimension_kind(label: str) -> str:
    return "unknown"


def _metric_lookup_is_company_total(
    *,
    metric_name: str,
    dimensions: Mapping[str, Any],
    text: Any,
) -> bool:
    if not any(value for value in dimensions.values()):
        return True
    haystack = f"{metric_name} {text}".lower()
    segment_markers = ("segment", "product", "geograph", "region", "country", "customer", "axis", "member")
    if any(marker in str(key).lower() for key in dimensions for marker in segment_markers):
        return False
    for value in dimensions.values():
        value_text = str(value or "").lower()
        if value_text and value_text not in {"total", "consolidated", "company"} and value_text in haystack:
            return False
    if any(term in haystack for term in ("total", "consolidated", "company")):
        return True
    return False



def _metric_lookup_alias_text(
    *,
    metric_name: str,
    canonical_metric: str,
    obj: Mapping[str, Any],
    dimensions: Mapping[str, Any],
    text: Any,
) -> str:
    pieces: list[str] = [metric_name, canonical_metric, str(text or "")]
    for key in (
        "label",
        "description",
        "source_label",
        "xbrl_concept",
        "concept",
        "normalization",
        "source_type",
        "calculation_type",
        "formula",
    ):
        value = obj.get(key)
        if value:
            pieces.append(str(value))
    for key, value in dimensions.items():
        if key:
            pieces.append(str(key))
            pieces.extend(_metric_dimension_aliases(key, _metric_dimension_key(key)))
        if value:
            pieces.append(str(value))
            pieces.extend(_metric_dimension_aliases(value, _metric_dimension_key(value)))
    return " ".join(piece for piece in pieces if piece)[:4000]


def _metric_dimension_aliases(label: Any, dimension_key: Any) -> set[str]:
    aliases: set[str] = set()
    label_text = _metric_lookup_clean_dimension_label(label)
    key_text = str(dimension_key or "").strip()
    key_space = key_text.replace("_", " ")
    for value in (label_text, key_text, key_space):
        value = str(value or "").strip()
        if not value:
            continue
        aliases.add(value)
        aliases.add(value.lower())
        compact = re.sub(r"[^A-Za-z0-9]+", "", value)
        if compact:
            aliases.add(compact)
            aliases.add(compact.lower())
        if value.endswith("s") and len(value) > 3:
            aliases.add(value[:-1])
            aliases.add(value[:-1].lower())
        elif re.search(r"[A-Za-z]$", value):
            aliases.add(f"{value}s")
            aliases.add(f"{value.lower()}s")
    return aliases


def _rebuild_typed_projection_lookup(conn: sqlite3.Connection, table_name: str) -> int:
    """Materialize typed serving projections for cheap candidate lookup."""
    object_types = TYPED_PROJECTION_OBJECT_TYPES[table_name]
    conn.execute(f"DELETE FROM {table_name}")
    placeholders = ",".join("?" for _ in object_types)
    rows = conn.execute(
        f"""
        SELECT
            objects.id,
            objects.type,
            objects.ticker,
            objects.document_type,
            objects.doc_type_key,
            objects.period,
            objects.text,
            objects.json,
            object_traceability.trace_status,
            object_traceability.evidence_chain_count,
            object_traceability.support_quote_count,
            object_traceability.support_claim_count
        FROM objects
        LEFT JOIN object_traceability
          ON object_traceability.object_id = objects.id
        WHERE objects.type IN ({placeholders})
          AND (objects.review_status IS NULL OR objects.review_status != 'rejected')
        """,
        object_types,
    ).fetchall()
    projection_rows: list[tuple[Any, ...]] = []
    for row in rows:
        try:
            obj = json.loads(row["json"] or "{}")
        except json.JSONDecodeError:
            obj = {}
        topic = build_company_topic_profile(dict(row), obj, str(row["text"] or "")) or {}
        common = _typed_projection_common(row, obj, topic)
        if table_name == "exposure_lookup":
            projection_rows.append(_exposure_lookup_row(row, obj, common))
        elif table_name == "agreement_lookup":
            projection_rows.append(_agreement_lookup_row(row, obj, common))
        elif table_name == "event_lookup":
            projection_rows.append(_event_lookup_row(row, obj, common))
        elif table_name == "factor_lookup":
            projection_rows.append(_factor_lookup_row(row, obj, common))
        if len(projection_rows) >= BULK_INSERT_CHUNK_SIZE:
            _flush_typed_projection_batch(conn, table_name, projection_rows)
    _flush_typed_projection_batch(conn, table_name, projection_rows)
    return len(rows)


def _typed_projection_common(
    row: sqlite3.Row,
    obj: Mapping[str, Any],
    topic: Mapping[str, Any],
) -> dict[str, Any]:
    source_text = str(row["text"] or _projection_object_values_text(obj) or "")
    lookup_text = _compact_space(
        " ".join(
            str(value or "")
            for value in (
                source_text,
                topic.get("topic_label"),
                topic.get("topic_summary"),
                topic.get("facet_text"),
                _projection_object_values_text(obj),
            )
        )
    )[:4000]
    return {
        "object_id": row["id"],
        "object_type": row["type"],
        "ticker": row["ticker"],
        "document_type": row["document_type"],
        "doc_type_key": row["doc_type_key"],
        "period": row["period"],
        "trace_status": row["trace_status"],
        "evidence_strength": topic.get("evidence_strength") or obj.get("evidence_grade"),
        "specificity_score": topic.get("specificity_score"),
        "generic_score": topic.get("generic_score"),
        "boilerplate_score": topic.get("boilerplate_score"),
        "support_quote_count": max(int(topic.get("support_quote_count") or 0), int(row["support_quote_count"] or 0)),
        "support_claim_count": max(int(topic.get("support_claim_count") or 0), int(row["support_claim_count"] or 0)),
        "evidence_chain_count": int(row["evidence_chain_count"] or 0),
        "lookup_text": lookup_text,
        "source_text": source_text[:2000],
        "topic_family": topic.get("topic_family"),
        "topic_label": topic.get("topic_label"),
        "topic_summary": topic.get("topic_summary"),
        "impact_channels": " ".join(topic.get("impact_channels") or []),
    }


def _exposure_lookup_row(row: sqlite3.Row, obj: Mapping[str, Any], common: Mapping[str, Any]) -> tuple[Any, ...]:
    factor = _typed_projection_text(obj, ("factor", "external_factor", "factor_category", "name"))
    benchmark = _typed_projection_text(obj, ("benchmark", "benchmark_hint", "index", "price_benchmark"))
    impact_channel = _typed_projection_text(obj, ("impact_channel", "affected_channel", "affected_channels"))
    mechanism = _typed_projection_text(obj, ("mechanism", "description", "scenario_effects"))
    scenario_terms = _typed_projection_text(obj, ("scenario_terms", "scenario_effects", "effect_direction", "direction"))
    return (
        *(_common_projection_values(common)),
        factor,
        benchmark,
        impact_channel,
        mechanism,
        scenario_terms,
        _join_projection_parts(factor, benchmark, mechanism),
        _join_projection_parts(impact_channel, scenario_terms, common["lookup_text"]),
        _join_projection_parts(factor, benchmark),
        common["trace_status"],
        common["evidence_strength"],
        common["specificity_score"],
        common["generic_score"],
        common["boilerplate_score"],
        common["support_quote_count"],
        common["support_claim_count"],
        common["evidence_chain_count"],
        common["lookup_text"],
        common["source_text"],
    )


def _agreement_lookup_row(row: sqlite3.Row, obj: Mapping[str, Any], common: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        *(_common_projection_values(common)),
        _typed_projection_text(obj, ("agreement_type", "contract_type", "type_name")),
        _typed_projection_text(obj, ("agreement_subtype", "economic_role", "role")),
        _typed_projection_text(obj, ("counterparty", "counterparties", "customer", "supplier", "lender")),
        _typed_projection_text(obj, ("amount", "value", "notional_amount", "commitment_amount")),
        _typed_projection_text(obj, ("maturity_date", "expiration_date", "end_date", "termination_date")),
        _typed_projection_text(obj, ("termination_terms", "termination", "default_terms", "termination_rights")),
        _typed_projection_text(obj, ("covenant_terms", "covenants", "financial_covenants")),
        _typed_projection_text(obj, ("collateral_terms", "collateral", "security", "lien")),
        _typed_projection_text(obj, ("affected_channels", "impact_channel", "economic_role")),
        common["trace_status"],
        common["evidence_strength"],
        common["specificity_score"],
        common["generic_score"],
        common["boilerplate_score"],
        common["support_quote_count"],
        common["support_claim_count"],
        common["evidence_chain_count"],
        common["lookup_text"],
        common["source_text"],
    )


def _event_lookup_row(row: sqlite3.Row, obj: Mapping[str, Any], common: Mapping[str, Any]) -> tuple[Any, ...]:
    event_date = _typed_projection_text(obj, ("event_date", "date", "actual_date"))
    date_expression = _typed_projection_text(obj, ("date_expression", "target_date", "expected_date", "timing"))
    return (
        *(_common_projection_values(common)),
        _typed_projection_text(obj, ("event_type", "event_subtype", "change_type")),
        _typed_projection_text(obj, ("event_status", "status", "completion_status")),
        event_date,
        date_expression,
        _typed_projection_date_sort_key(event_date or date_expression or row["period"]),
        _typed_projection_text(obj, ("project_or_product", "project", "product", "asset", "subject")),
        _typed_projection_text(obj, ("regulatory_body", "agency", "regulator", "authority")),
        _typed_projection_text(obj, ("affected_channels", "impact_channel", "affected_objects")),
        common["trace_status"],
        common["evidence_strength"],
        common["specificity_score"],
        common["generic_score"],
        common["boilerplate_score"],
        common["support_quote_count"],
        common["support_claim_count"],
        common["evidence_chain_count"],
        common["lookup_text"],
        common["source_text"],
    )


def _factor_lookup_row(row: sqlite3.Row, obj: Mapping[str, Any], common: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        *(_common_projection_values(common)),
        _typed_projection_text(obj, ("factor_type", "factor_roles", "category")),
        common["topic_family"],
        _typed_projection_text(obj, ("impact_channel", "affected_channels", "primary_channel")),
        _typed_projection_text(obj, ("business_area", "segment", "product", "activity")),
        _typed_projection_text(obj, ("risk_or_driver", "factor_roles", "category", "name")),
        common["topic_label"],
        common["topic_summary"],
        common["trace_status"],
        common["evidence_strength"],
        common["specificity_score"],
        common["generic_score"],
        common["boilerplate_score"],
        common["support_quote_count"],
        common["support_claim_count"],
        common["evidence_chain_count"],
        common["lookup_text"],
        common["source_text"],
    )


def _common_projection_values(common: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        common["object_id"],
        common["object_type"],
        common["ticker"],
        common["document_type"],
        common["doc_type_key"],
        common["period"],
    )


def _flush_typed_projection_batch(
    conn: sqlite3.Connection,
    table_name: str,
    rows: list[tuple[Any, ...]],
) -> None:
    if not rows:
        return
    insert_sql = {
        "exposure_lookup": """
            INSERT OR REPLACE INTO exposure_lookup(
                object_id, object_type, ticker, document_type, doc_type_key, period,
                factor, benchmark, impact_channel, mechanism, scenario_terms,
                direct_anchor_text, related_context_text, negative_guard_terms,
                trace_status, evidence_strength, specificity_score, generic_score,
                boilerplate_score, support_quote_count, support_claim_count,
                evidence_chain_count, lookup_text, source_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        "agreement_lookup": """
            INSERT OR REPLACE INTO agreement_lookup(
                object_id, object_type, ticker, document_type, doc_type_key, period,
                agreement_type, agreement_subtype, counterparty, amount, maturity_date,
                termination_terms, covenant_terms, collateral_terms, affected_channels,
                trace_status, evidence_strength, specificity_score, generic_score,
                boilerplate_score, support_quote_count, support_claim_count,
                evidence_chain_count, lookup_text, source_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        "event_lookup": """
            INSERT OR REPLACE INTO event_lookup(
                object_id, object_type, ticker, document_type, doc_type_key, period,
                event_type, event_status, event_date, date_expression, date_sort_key,
                project_or_product, regulatory_body, affected_channels,
                trace_status, evidence_strength, specificity_score, generic_score,
                boilerplate_score, support_quote_count, support_claim_count,
                evidence_chain_count, lookup_text, source_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        "factor_lookup": """
            INSERT OR REPLACE INTO factor_lookup(
                object_id, object_type, ticker, document_type, doc_type_key, period,
                factor_type, topic_family, impact_channel, business_area, risk_or_driver,
                topic_label, topic_summary, trace_status, evidence_strength,
                specificity_score, generic_score, boilerplate_score,
                support_quote_count, support_claim_count, evidence_chain_count,
                lookup_text, source_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
    }[table_name]
    conn.executemany(insert_sql, rows)
    rows.clear()


def _typed_projection_text(obj: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    values: list[str] = []
    for key in keys:
        value = obj.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, Mapping):
            values.extend(str(item) for item in value.values() if item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            values.extend(str(item) for item in value if item)
        else:
            values.append(str(value))
    text = _compact_space(" ".join(values))
    return text[:1000] if text else None


def _join_projection_parts(*values: Any) -> str:
    return _compact_space(" ".join(str(value) for value in values if value))


def _projection_object_values_text(obj: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key, value in obj.items():
        if key in {"id", "type", "json"}:
            continue
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, Mapping):
            values.extend(str(item) for item in value.values() if isinstance(item, (str, int, float)))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            values.extend(str(item) for item in value if isinstance(item, (str, int, float)))
        elif isinstance(value, (int, float)):
            values.append(str(value))
    return _compact_space(" ".join(values))


def _typed_projection_date_sort_key(value: Any) -> str | None:
    text = str(value or "")
    match = re.search(r"(20\d{2}|19\d{2})(?:[-/ ]?(0[1-9]|1[0-2]))?(?:[-/ ]?([0-3]\d))?", text)
    if not match:
        return None
    year, month, day = match.group(1), match.group(2) or "00", match.group(3) or "00"
    return f"{year}-{month}-{day}"



def _rebuild_company_topic_index(conn: sqlite3.Connection) -> int:
    """Build serving-only company topic profiles from indexed evidence text."""
    conn.execute("DELETE FROM company_topic_index")
    conn.execute("DELETE FROM company_topic_fts")
    conn.execute("DELETE FROM company_topic_source_objects")
    placeholders = ",".join("?" for _ in COMPANY_TOPIC_OBJECT_TYPES)
    params = tuple(sorted(COMPANY_TOPIC_OBJECT_TYPES))
    candidate_count = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM objects
        WHERE objects.type IN ({placeholders})
        """,
        params,
    ).fetchone()[0]
    _log_build_phase("company_topic_candidates", candidates=candidate_count)
    rows = conn.execute(
        f"""
        SELECT
            objects.id,
            objects.type,
            objects.ticker,
            objects.period,
            objects.document_type,
            objects.doc_type_key,
            objects.json,
            object_search_text.text_self,
            object_search_text.text_support,
            object_search_text.text_related,
            object_search_text.text_entities,
            object_search_text.text_aliases,
            object_search_text.compact_text,
            object_traceability.trace_status,
            object_traceability.evidence_chain_count,
            object_traceability.support_depth,
            object_traceability.support_quote_count AS trace_support_quote_count,
            object_traceability.support_claim_count AS trace_support_claim_count,
            object_traceability.support_link_count,
            object_traceability.trace_method,
            object_traceability.metric_lineage_status,
            object_traceability.answer_candidate
        FROM objects
        LEFT JOIN object_search_text ON object_search_text.object_id = objects.id
        LEFT JOIN object_traceability ON object_traceability.object_id = objects.id
        WHERE objects.type IN ({placeholders})
        ORDER BY objects.ticker, objects.id
        """,
        params,
    )
    count = 0
    topic_rows: list[tuple[Any, ...]] = []
    topic_fts_rows: list[tuple[Any, ...]] = []
    topic_source_rows: list[tuple[Any, ...]] = []
    for row in rows:
        obj = json.loads(row["json"])
        retrieval_text = _topic_retrieval_text(row)
        topic = build_company_topic_profile(dict(row), obj, retrieval_text)
        if topic is None:
            continue
        source_object_ids = list(dict.fromkeys(topic.get("source_object_ids") or []))
        trace_status = row["trace_status"] or "unknown"
        if trace_status in {"traceable", "traceable_metric_lineage"}:
            top_traceable_object_ids = source_object_ids[:5]
            untraced_object_ids: list[str] = []
        else:
            top_traceable_object_ids = []
            untraced_object_ids = source_object_ids[:5]
        support_metric_count = int(topic.get("support_metric_count") or 0)
        if topic.get("primary_object_type") in {"MetricObservation", "Calculation"}:
            support_metric_count = max(support_metric_count, 1)
        topic_rows.append(
            (
                topic["topic_id"],
                topic["ticker"],
                topic.get("period"),
                topic.get("document_type"),
                topic.get("doc_type_key"),
                topic.get("document_type"),
                topic.get("topic_label"),
                topic.get("topic_summary"),
                topic.get("topic_type"),
                topic.get("topic_family"),
                topic.get("topic_text"),
                topic.get("facet_text"),
                topic.get("primary_object_id"),
                topic.get("primary_object_type"),
                json.dumps(source_object_ids, ensure_ascii=False),
                json.dumps(top_traceable_object_ids, ensure_ascii=False),
                json.dumps(untraced_object_ids, ensure_ascii=False),
                json.dumps(topic.get("dominant_object_types") or [], ensure_ascii=False),
                json.dumps(topic.get("impact_channels") or [], ensure_ascii=False),
                json.dumps(topic.get("factor_terms") or [], ensure_ascii=False),
                json.dumps(topic.get("metric_terms") or [], ensure_ascii=False),
                json.dumps(topic.get("entity_terms") or [], ensure_ascii=False),
                json.dumps(topic.get("mechanism_terms") or [], ensure_ascii=False),
                json.dumps(topic.get("scenario_terms") or [], ensure_ascii=False),
                topic.get("evidence_strength"),
                topic.get("materiality_hint"),
                topic.get("materiality_score"),
                topic.get("specificity_score"),
                topic.get("generic_score"),
                topic.get("boilerplate_score"),
                max(int(topic.get("support_quote_count") or 0), int(row["trace_support_quote_count"] or 0)),
                max(int(topic.get("support_claim_count") or 0), int(row["trace_support_claim_count"] or 0)),
                support_metric_count,
                trace_status,
                int(row["evidence_chain_count"] or 0),
                row["support_depth"],
                int(row["support_link_count"] or 0),
                row["trace_method"],
                row["metric_lineage_status"],
                int(row["answer_candidate"] or 0),
                "object_search_text",
                COMPANY_TOPIC_BUILDER_VERSION,
            )
        )
        for rank, source_id in enumerate(source_object_ids):
            role = _topic_source_role(source_id, topic.get("primary_object_id"))
            topic_source_rows.append(
                (
                    topic["topic_id"],
                    source_id,
                    _topic_source_object_type(source_id, topic.get("primary_object_type") if role == "primary" else None),
                    role,
                    rank,
                    trace_status if role == "primary" else None,
                    int(row["evidence_chain_count"] or 0) if role == "primary" else 0,
                )
            )
        topic_fts_rows.append(
            (
                topic["topic_id"],
                topic["ticker"],
                topic.get("period"),
                topic.get("topic_label"),
                topic.get("topic_summary"),
                _company_topic_evidence_text(topic, retrieval_text),
                _compact_space(
                    " ".join(
                        [
                            _topic_scope_token_text(topic),
                            " ".join(topic.get("entity_terms") or []),
                        ]
                    )
                ),
                " ".join(
                    list(topic.get("impact_channels") or [])
                    + list(topic.get("factor_terms") or [])
                    + list(topic.get("metric_terms") or [])
                    + list(topic.get("mechanism_terms") or [])
                    + list(topic.get("scenario_terms") or [])
                ),
                topic.get("facet_text"),
                _compact_space(
                    " ".join(
                        str(value or "")
                        for value in (
                            _topic_scope_token_text(topic),
                            topic.get("topic_label"),
                            topic.get("topic_summary"),
                            str(topic.get("topic_text") or "")[:COMPANY_TOPIC_FTS_CHAR_LIMIT],
                            topic.get("facet_text"),
                            topic.get("evidence_strength"),
                            topic.get("trace_status"),
                        )
                    )
                )[:COMPANY_TOPIC_FTS_CHAR_LIMIT],
            )
        )
        count += 1
        if len(topic_rows) >= COMPANY_TOPIC_BATCH_SIZE:
            _flush_company_topic_batch(conn, topic_rows, topic_fts_rows, topic_source_rows)
            conn.commit()
            _log_build_phase(
                "company_topic_batch_done",
                indexed=count,
                candidates=candidate_count,
            )
    _flush_company_topic_batch(conn, topic_rows, topic_fts_rows, topic_source_rows)
    conn.commit()
    return count


def _object_fts_row_with_scope_tokens(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """Add indexed scope tokens to FTS text while preserving normal columns.

    The ticker/document/period/type columns on FTS tables are UNINDEXED. These
    tokens let MATCH narrow candidates at the FTS stage, while the normal SQL
    filters still enforce correctness.
    """
    (
        object_id,
        object_type,
        ticker,
        document_type,
        period,
        text_self,
        text_support,
        text_related,
        text_entities,
        text_aliases,
        compact_text,
    ) = row
    scope_text = _scope_token_text(
        ticker=ticker,
        document_type=document_type,
        period=period,
        object_type=object_type,
    )
    return (
        object_id,
        object_type,
        ticker,
        document_type,
        period,
        text_self,
        text_support,
        text_related,
        _compact_space(" ".join([scope_text, str(text_entities or "")])),
        text_aliases,
        _compact_space(" ".join([scope_text, str(compact_text or "")])),
    )


def _topic_scope_token_text(topic: Mapping[str, Any]) -> str:
    return _scope_token_text(
        ticker=topic.get("ticker"),
        document_type=topic.get("document_type"),
        period=topic.get("period"),
        object_type=topic.get("primary_object_type"),
        topic_family=topic.get("topic_family"),
    )


def _scope_token_text(
    *,
    ticker: Any = None,
    document_type: Any = None,
    period: Any = None,
    object_type: Any = None,
    topic_family: Any = None,
) -> str:
    tokens = [
        _scope_token("ticker", ticker),
        _scope_token("doctype", document_type),
        _scope_token("period", period),
        _scope_token("otype", object_type),
        _scope_token("family", topic_family),
    ]
    return " ".join(token for token in tokens if token)


def _scope_token(prefix: str, value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    if not normalized:
        return ""
    return f"{prefix}_{normalized}"


def _topic_retrieval_text(row: sqlite3.Row) -> str:
    parts: list[str] = []
    for key in (
        "text_self",
        "text_support",
        "text_related",
        "text_entities",
        "text_aliases",
        "compact_text",
    ):
        value = str(row[key] or "")
        if value:
            parts.append(value[:COMPANY_TOPIC_FIELD_CHAR_LIMIT])
    return "\n".join(parts)[:COMPANY_TOPIC_TEXT_CHAR_LIMIT]


def _company_topic_evidence_text(topic: dict[str, Any], retrieval_text: str) -> str:
    return _compact_space(
        " ".join(
            str(value or "")
            for value in (
                topic.get("topic_label"),
                topic.get("topic_summary"),
                topic.get("facet_text"),
                retrieval_text,
                topic.get("evidence_strength"),
                topic.get("materiality_hint"),
                " ".join(topic.get("impact_channels") or []),
                " ".join(topic.get("factor_terms") or []),
                " ".join(topic.get("metric_terms") or []),
                " ".join(topic.get("entity_terms") or []),
                " ".join(topic.get("mechanism_terms") or []),
                " ".join(topic.get("scenario_terms") or []),
            )
        )
    )[:COMPANY_TOPIC_FTS_CHAR_LIMIT]


def _topic_source_role(source_id: str, primary_object_id: Any) -> str:
    if source_id == primary_object_id:
        return "primary"
    normalized = str(source_id or "").lower()
    if "quote" in normalized:
        return "support"
    if "claim" in normalized:
        return "support"
    if "metric" in normalized or "calculation" in normalized or "xbrl" in normalized:
        return "metric"
    if "agreement" in normalized or "contract" in normalized or "covenant" in normalized:
        return "agreement"
    if "event" in normalized or "milestone" in normalized:
        return "event"
    return "related"


def _topic_source_object_type(source_id: str, fallback: Any = None) -> str:
    if fallback:
        return str(fallback)
    normalized = str(source_id or "").lower()
    prefix_map = (
        ("evidence_quote", "EvidenceQuote"),
        ("quote", "EvidenceQuote"),
        ("research_claim", "ResearchClaim"),
        ("claim", "ResearchClaim"),
        ("metric_observation", "MetricObservation"),
        ("metric", "MetricObservation"),
        ("calculation", "Calculation"),
        ("xbrl", "XBRLFact"),
        ("agreement", "AgreementTerm"),
        ("contract", "AgreementTerm"),
        ("business_event", "BusinessEvent"),
        ("event", "BusinessEvent"),
        ("external_factor_exposure", "ExternalFactorExposure"),
        ("exposure", "ExternalFactorExposure"),
        ("business_factor", "BusinessFactor"),
        ("factor", "BusinessFactor"),
        ("business_activity", "BusinessActivity"),
        ("activity", "BusinessActivity"),
    )
    for marker, object_type in prefix_map:
        if marker in normalized:
            return object_type
    return "Unknown"


def _flush_company_topic_batch(
    conn: sqlite3.Connection,
    topic_rows: list[tuple[Any, ...]],
    topic_fts_rows: list[tuple[Any, ...]],
    topic_source_rows: list[tuple[Any, ...]],
) -> None:
    if topic_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO company_topic_index(
                topic_id, ticker, period, document_type, doc_type_key,
                filing_type, topic_label, topic_summary, topic_type, topic_family,
                topic_text, facet_text, primary_object_id, primary_object_type,
                source_object_ids, top_traceable_object_ids, untraced_object_ids,
                dominant_object_types, impact_channels, factor_terms, metric_terms,
                entity_terms, mechanism_terms, scenario_terms, evidence_strength,
                materiality_hint, materiality_score, specificity_score, generic_score,
                boilerplate_score, support_quote_count, support_claim_count,
                support_metric_count, trace_status, evidence_chain_count, support_depth,
                support_link_count, trace_method, metric_lineage_status,
                answer_candidate, created_from, builder_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            topic_rows,
        )
    if topic_source_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO company_topic_source_objects(
                topic_id, object_id, object_type, role, rank,
                trace_status, evidence_chain_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            topic_source_rows,
        )
    if topic_fts_rows:
        conn.executemany(
            """
            INSERT INTO company_topic_fts(
                topic_id, ticker, period, text_label, text_summary, text_evidence,
                text_entities, text_channels, text_aliases, compact_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            topic_fts_rows,
        )
    topic_rows.clear()
    topic_fts_rows.clear()
    topic_source_rows.clear()


def _index_edges(
    conn: sqlite3.Connection,
    path: Path | None,
    ticker: str,
    document_type: str,
    doc_type_key: str,
    period: str,
    *,
    rows: list[dict[str, Any]] | None = None,
) -> int:
    if not path and rows is None:
        return 0
    count = 0
    edge_rows_to_insert: list[tuple[Any, ...]] = []
    for edge in rows if rows is not None else read_jsonl(path):
        if not edge.get("id") or edge.get("type") != "Edge":
            continue
        edge_rows_to_insert.append(
            (
                edge["id"],
                edge.get("ticker", ticker),
                edge.get("document_type", document_type),
                doc_type_key,
                edge.get("period", period),
                edge.get("source_document_id"),
                edge.get("from_id"),
                edge.get("to_id"),
                edge.get("relation_id"),
                edge.get("relation_name"),
                edge.get("confidence"),
                edge.get("review_status"),
                json.dumps(edge, ensure_ascii=False),
                str(path or ""),
            )
        )
        count += 1
        if len(edge_rows_to_insert) >= BULK_INSERT_CHUNK_SIZE:
            _flush_edge_batch(conn, edge_rows_to_insert)
    _flush_edge_batch(conn, edge_rows_to_insert)
    return count


def _flush_edge_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO edges(
            id, ticker, document_type, doc_type_key, period, source_document_id,
            from_id, to_id, relation_id, relation_name, confidence,
            review_status, json, artifact_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    rows.clear()


def _insert_quality_event(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    document_type: str,
    doc_type_key: str,
    period: str,
    severity: str,
    category: str,
    object_id: str | None,
    stage: str | None,
    message: str,
    payload: dict[str, Any],
) -> None:
    event_id = ":".join(
        part
        for part in (
            "quality",
            ticker,
            period,
            doc_type_key,
            category,
            object_id or stage or message[:40],
        )
        if part
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO quality_events(
            id, ticker, document_type, doc_type_key, period, severity, category,
            object_id, stage, message, json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            ticker,
            document_type,
            doc_type_key,
            period,
            severity,
            category,
            object_id,
            stage,
            message,
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def _object_text(obj: dict[str, Any]) -> str:
    keys = TEXT_KEYS_BY_TYPE.get(obj.get("type"), ())
    parts: list[str] = []
    for key in keys:
        value = obj.get(key)
        if value is not None:
            parts.append(str(value))
    if obj.get("related_metrics"):
        parts.extend(str(metric) for metric in obj["related_metrics"])
    if obj.get("affects"):
        parts.extend(str(metric) for metric in obj["affects"])
    return _NON_WORD_RE.sub(" ", " ".join(parts)).strip()


def _taxonomy_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    taxonomy: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in (
            row.get("id"),
            row.get("term_id"),
            row.get("term_key"),
            row.get("canonical_name"),
        ):
            if key:
                taxonomy[str(key)] = row
    return taxonomy


def _registry_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT json
        FROM objects
        WHERE type = 'OntologyRegistrySnapshot'
        ORDER BY period DESC, id
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["json"]).get("registry_version")
    except (TypeError, json.JSONDecodeError):
        return None


def _artifact_manifest_hash(artifact_indexes: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in artifact_indexes:
        digest.update(str(path).encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return f"sha256:{digest.hexdigest()}"


def _read_section_quality(ontology_dir: Path) -> dict[str, Any]:
    path = ontology_dir / "section_quality.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"status": "unknown", "fail_reasons": ["unreadable_section_quality"]}


def _section_quality_message(section_quality: dict[str, Any]) -> str:
    status = section_quality.get("status", "unknown")
    fail_reasons = section_quality.get("fail_reasons") or []
    missing_core = section_quality.get("missing_core_sections") or []
    pieces = [f"section_quality={status}"]
    if fail_reasons:
        pieces.append(f"fail_reasons={','.join(fail_reasons)}")
    if missing_core:
        pieces.append(f"missing_core_sections={','.join(missing_core)}")
    return "; ".join(pieces)


def _resolve_artifact_path(root: Path, rel_path: str | None) -> Path | None:
    if not rel_path:
        return None
    path = Path(rel_path)
    if path.is_absolute():
        return path
    return root / path
