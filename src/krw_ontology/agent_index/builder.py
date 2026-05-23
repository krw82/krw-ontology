"""Build a global SQLite index for agent retrieval.

The JSONL ontology artifacts remain canonical. This module builds a
regenerable read index that lets agents retrieve evidence bundles without
reasoning over the filesystem layout.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import hashlib
import time
from collections import defaultdict
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
BULK_INSERT_CHUNK_SIZE = 5_000
COMPANY_TOPIC_BATCH_SIZE = 500
SQLITE_CACHE_SIZE_KIB = 200_000
SQLITE_MMAP_SIZE_BYTES = 256 * 1024 * 1024
COMPANY_TOPIC_TEXT_CHAR_LIMIT = 2_500
COMPANY_TOPIC_FIELD_CHAR_LIMIT = 700
COMPANY_TOPIC_FTS_CHAR_LIMIT = 1_800
COMPANY_TOPIC_BUILDER_VERSION = "0.3.0-rich-hardened"


def _elapsed(started_at: float) -> float:
    return round(time.perf_counter() - started_at, 3)


def _log_build_phase(phase: str, **fields: Any) -> None:
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    message = f"build_agent_index: {phase}"
    if rendered:
        message = f"{message} {rendered}"
    logger.info(message, extra={"stage": "build_agent_index"})


def _compact_space(value: str) -> str:
    return _NON_WORD_RE.sub(" ", str(value or "")).strip()


def build_agent_index(
    root: Path,
    *,
    index_path: Path | None = None,
    force: bool = True,
) -> dict[str, Any]:
    """Build a global SQLite agent index from all discovered artifact indexes."""
    root = root.resolve()
    index_path = (index_path or root / DEFAULT_INDEX_RELATIVE_PATH).resolve()
    if force and index_path.exists():
        index_path.unlink()
    index_path.parent.mkdir(parents=True, exist_ok=True)

    artifact_indexes = discover_artifact_indexes(root)
    conn = sqlite3.connect(index_path, timeout=60)
    build_started_at = time.perf_counter()
    try:
        conn.row_factory = sqlite3.Row
        _configure_connection(conn)
        _create_schema(conn)
        _log_build_phase(
            "start",
            root=str(root),
            index_path=str(index_path),
            force=force,
            artifact_indexes=len(artifact_indexes),
        )
        totals = {
            "documents": 0,
            "objects": 0,
            "edges": 0,
            "quality_events": 0,
            "object_traceability": 0,
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
            _checkpoint_wal(conn)
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
                            "company_topic_fts_enabled": True,
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
        }
    finally:
        conn.close()


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


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_SIZE_KIB}")
    conn.execute(f"PRAGMA mmap_size={SQLITE_MMAP_SIZE_BYTES}")
    conn.execute("PRAGMA wal_autocheckpoint=10000")


def _checkpoint_wal(conn: sqlite3.Connection, *, truncate: bool = False) -> None:
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
