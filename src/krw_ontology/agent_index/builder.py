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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import read_jsonl

logger = logging.getLogger("krw_ontology")

AGENT_INDEX_SCHEMA_VERSION = "0.1.0"
DEFAULT_INDEX_RELATIVE_PATH = Path("indexes") / "agent_index.sqlite"

OBJECT_FILE_KEYS = {
    "spans",
    "evidence_quotes",
    "language_signals",
    "claims",
    "risks",
    "growth_drivers",
    "headwinds",
    "business_activities",
    "external_factor_exposures",
    "assumption_candidates",
    "xbrl_facts",
    "financial_metric_values",
    "derived_metric_values",
    "numeric_evidence",
    "calculated_numeric_support",
    "company_business_profiles",
    "temporal_links",
    "trend_observations",
    "change_events",
}

TEXT_KEYS_BY_TYPE = {
    "SourceSpan": ("text",),
    "EvidenceQuote": ("quote_text",),
    "LanguageSignal": ("signal_text",),
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
    "RiskFactor": ("name", "category", "description", "qualitative_impact"),
    "GrowthDriver": ("name", "category", "description", "qualitative_impact"),
    "Headwind": ("name", "category", "description", "qualitative_impact"),
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
    "FinancialMetricValue": ("metric_name", "unit", "period_type"),
    "DerivedMetricValue": ("metric_name", "unit", "formula", "period_type"),
    "NumericEvidence": ("raw_text", "unit", "numeric_kind", "evidence_role"),
    "CalculatedNumericSupport": ("formula", "display_value", "unit", "calculation_type"),
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
    conn = sqlite3.connect(index_path)
    try:
        conn.row_factory = sqlite3.Row
        _create_schema(conn)
        totals = {
            "documents": 0,
            "objects": 0,
            "edges": 0,
            "quality_events": 0,
        }

        with conn:
            replace_fts_entries = not force
            for artifact_index_path in artifact_indexes:
                stats = _index_artifact(
                    conn,
                    root,
                    artifact_index_path,
                    replace_fts_entries=replace_fts_entries,
                )
                for key, value in stats.items():
                    totals[key] += value

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
                            "ontology_schema_version": SCHEMA_VERSION,
                            "root": str(root),
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "artifact_indexes": len(artifact_indexes),
                            "totals": totals,
                        },
                        ensure_ascii=False,
                    ),
                ),
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


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;

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

        CREATE INDEX IF NOT EXISTS idx_objects_scope
            ON objects(ticker, doc_type_key, period, type);
        CREATE INDEX IF NOT EXISTS idx_objects_metric
            ON objects(metric_name, ticker, doc_type_key, period);
        CREATE INDEX IF NOT EXISTS idx_edges_from
            ON edges(from_id, relation_id);
        CREATE INDEX IF NOT EXISTS idx_edges_to
            ON edges(to_id, relation_id);
        CREATE INDEX IF NOT EXISTS idx_quality_scope
            ON quality_events(ticker, doc_type_key, period, category);

        CREATE VIRTUAL TABLE IF NOT EXISTS object_fts USING fts5(
            object_id UNINDEXED,
            type UNINDEXED,
            ticker UNINDEXED,
            document_type UNINDEXED,
            period UNINDEXED,
            text,
            tokenize = 'unicode61'
        );
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
            stats["edges"] += _index_edges(conn, path, ticker, document_type, doc_type_key, period)
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
) -> int:
    if not path:
        return 0
    count = 0
    for obj in read_jsonl(path):
        if not obj.get("id") or not obj.get("type"):
            continue
        _insert_object(
            conn,
            obj,
            artifact_key=artifact_key,
            artifact_path=path,
            ticker=ticker,
            document_type=document_type,
            doc_type_key=doc_type_key,
            period=period,
            replace_fts_entries=replace_fts_entries,
        )
        count += 1
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
) -> None:
    text = _object_text(obj)
    review_status = forced_review_status or obj.get("review_status")
    conn.execute(
        """
        INSERT OR REPLACE INTO objects(
            id, type, ticker, document_type, doc_type_key, period,
            source_document_id, section_name, metric_name, review_status,
            confidence, text, json, artifact_key, artifact_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
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
            str(artifact_path),
        ),
    )
    if text:
        if replace_fts_entries:
            conn.execute("DELETE FROM object_fts WHERE object_id = ?", (obj["id"],))
        conn.execute(
            """
            INSERT INTO object_fts(object_id, type, ticker, document_type, period, text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                obj["id"],
                obj["type"],
                obj.get("ticker", ticker),
                obj.get("document_type", document_type),
                obj.get("period", period),
                text,
            ),
        )


def _index_edges(
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
    for edge in read_jsonl(path):
        if not edge.get("id") or edge.get("type") != "Edge":
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO edges(
                id, ticker, document_type, doc_type_key, period, source_document_id,
                from_id, to_id, relation_id, relation_name, confidence,
                review_status, json, artifact_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
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
                str(path),
            ),
        )
        count += 1
    return count


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
