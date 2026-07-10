"""Schema primitives for the v3 global-spine-and-company-shards index."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GLOBAL_SPINE_SCHEMA_VERSION = "krw-ontology-global-spine/v2"
GLOBAL_SPINE_BUILDER_VERSION = "global-spine-builder/v2"
GLOBAL_SPINE_AGENT_GRAPH_INDEX_VERSION = "agent-graph-index/v1"
GLOBAL_SPINE_RELATIVE_PATH = Path("indexes") / "global_spine.sqlite"
GLOBAL_SPINE_LAYOUT = "global-spine-and-company-shards"

GLOBAL_SPINE_TABLES = (
    "metadata",
    "global_object_locator",
    "global_document_catalog",
    "global_edge_spine",
    "global_factor_spine",
    "global_topic_spine",
    "global_metric_spine",
    "global_entity_spine",
    "global_counterparty_spine",
    "global_chain_index",
    "global_key_stats",
    "global_search_fts",
)

GLOBAL_SPINE_REQUIRED_METADATA_KEYS = (
    "schema_version",
    "builder_version",
    "index_layout",
    "created_at",
)


def create_global_spine_schema(
    conn: sqlite3.Connection,
    *,
    include_secondary_indexes: bool = True,
) -> None:
    """Create the v3 global spine schema in an open SQLite connection."""
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS global_object_locator (
            object_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            company_name TEXT,
            document_id TEXT,
            document_type TEXT,
            period TEXT,
            filing_date TEXT,
            object_type TEXT,
            shard_id TEXT NOT NULL,
            shard_path TEXT NOT NULL,
            local_object_key TEXT,
            object_hash TEXT,
            compact_label TEXT,
            compact_summary TEXT,
            quality_status TEXT
        );

        CREATE TABLE IF NOT EXISTS global_document_catalog (
            document_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            company_name TEXT,
            document_type TEXT,
            period TEXT,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            filing_date TEXT,
            source_path TEXT,
            shard_id TEXT NOT NULL,
            shard_path TEXT NOT NULL,
            document_hash TEXT,
            object_count INTEGER NOT NULL DEFAULT 0,
            edge_count INTEGER NOT NULL DEFAULT 0,
            quality_event_count INTEGER NOT NULL DEFAULT 0,
            quality_status TEXT
        );

        CREATE TABLE IF NOT EXISTS global_edge_spine (
            edge_id TEXT PRIMARY KEY,
            from_object_id TEXT NOT NULL,
            to_object_id TEXT NOT NULL,
            from_ticker TEXT,
            to_ticker TEXT,
            relation_type TEXT NOT NULL,
            edge_scope TEXT NOT NULL,
            source_object_type TEXT,
            target_object_type TEXT,
            confidence REAL,
            evidence_grade TEXT,
            materiality REAL,
            recency_score REAL,
            shard_hint TEXT,
            compact_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS global_factor_spine (
            factor_key TEXT NOT NULL,
            factor_label TEXT,
            factor_family TEXT,
            benchmark TEXT,
            ticker TEXT NOT NULL,
            object_id TEXT NOT NULL,
            document_id TEXT,
            impact_channel TEXT,
            effect_direction TEXT,
            materiality REAL,
            evidence_grade TEXT,
            shard_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS global_topic_spine (
            topic_id TEXT PRIMARY KEY,
            topic_key TEXT NOT NULL,
            topic_label TEXT,
            topic_family TEXT,
            topic_summary TEXT,
            ticker TEXT NOT NULL,
            source_object_ids TEXT,
            factor_terms TEXT,
            metric_terms TEXT,
            entity_terms TEXT,
            mechanism_terms TEXT,
            impact_channels TEXT,
            evidence_grade TEXT,
            materiality REAL,
            shard_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS global_metric_spine (
            canonical_metric_key TEXT NOT NULL,
            metric_name TEXT,
            unit TEXT,
            dimensions_hash TEXT,
            ticker TEXT NOT NULL,
            object_id TEXT NOT NULL,
            document_id TEXT,
            period TEXT,
            document_type TEXT,
            value_normalized REAL,
            trend_direction TEXT,
            confidence REAL,
            shard_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS global_entity_spine (
            entity_key TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            canonical_name TEXT,
            aliases TEXT,
            ticker_scope TEXT,
            ticker TEXT NOT NULL,
            object_id TEXT NOT NULL,
            document_id TEXT,
            confidence REAL,
            shard_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS global_counterparty_spine (
            counterparty_key TEXT NOT NULL,
            counterparty_name TEXT,
            relationship_type TEXT,
            ticker TEXT NOT NULL,
            object_id TEXT NOT NULL,
            document_id TEXT,
            agreement_type TEXT,
            affected_channels TEXT,
            materiality REAL,
            evidence_grade TEXT,
            shard_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS global_chain_index (
            link_id TEXT PRIMARY KEY,
            link_type TEXT NOT NULL,
            from_ticker TEXT NOT NULL,
            to_ticker TEXT NOT NULL,
            shared_key TEXT NOT NULL,
            shared_key_type TEXT NOT NULL,
            from_object_id TEXT,
            to_object_id TEXT,
            weight REAL NOT NULL,
            confidence REAL,
            evidence_grade TEXT,
            materiality REAL,
            generic_penalty REAL,
            recency_score REAL,
            explanation_template TEXT
        );

        CREATE TABLE IF NOT EXISTS global_key_stats (
            key_type TEXT NOT NULL,
            key TEXT NOT NULL,
            ticker_count INTEGER NOT NULL DEFAULT 0,
            object_count INTEGER NOT NULL DEFAULT 0,
            document_count INTEGER NOT NULL DEFAULT 0,
            idf_score REAL,
            generic INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (key_type, key)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS global_search_fts USING fts5(
            object_id UNINDEXED,
            ticker UNINDEXED,
            object_type UNINDEXED,
            compact_text,
            topic_terms,
            factor_terms,
            metric_terms,
            entity_terms
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_global_object_locator_object_id
            ON global_object_locator(object_id);
        CREATE INDEX IF NOT EXISTS idx_global_object_locator_ticker
            ON global_object_locator(ticker);
        CREATE INDEX IF NOT EXISTS idx_global_object_locator_type
            ON global_object_locator(object_type);
        CREATE INDEX IF NOT EXISTS idx_global_object_locator_doc
            ON global_object_locator(ticker, document_type, period);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_global_document_catalog_document_id
            ON global_document_catalog(document_id);
        CREATE INDEX IF NOT EXISTS idx_global_document_catalog_ticker_period
            ON global_document_catalog(ticker, document_type, period);
        CREATE INDEX IF NOT EXISTS idx_global_document_catalog_filing_date
            ON global_document_catalog(filing_date);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_global_edge_spine_edge_id
            ON global_edge_spine(edge_id);
        CREATE INDEX IF NOT EXISTS idx_global_edge_spine_from
            ON global_edge_spine(from_object_id);
        CREATE INDEX IF NOT EXISTS idx_global_edge_spine_to
            ON global_edge_spine(to_object_id);
        CREATE INDEX IF NOT EXISTS idx_global_edge_spine_tickers
            ON global_edge_spine(from_ticker, to_ticker);
        CREATE INDEX IF NOT EXISTS idx_global_edge_spine_scope
            ON global_edge_spine(edge_scope, relation_type);
        CREATE INDEX IF NOT EXISTS idx_global_edge_spine_from_relation_to
            ON global_edge_spine(from_object_id, relation_type, to_object_id);
        CREATE INDEX IF NOT EXISTS idx_global_edge_spine_to_relation_from
            ON global_edge_spine(to_object_id, relation_type, from_object_id);

        CREATE INDEX IF NOT EXISTS idx_global_factor_spine_key
            ON global_factor_spine(factor_key);
        CREATE INDEX IF NOT EXISTS idx_global_factor_spine_ticker
            ON global_factor_spine(ticker);
        CREATE INDEX IF NOT EXISTS idx_global_factor_spine_family
            ON global_factor_spine(factor_family);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_global_topic_spine_topic_id
            ON global_topic_spine(topic_id);
        CREATE INDEX IF NOT EXISTS idx_global_topic_spine_key
            ON global_topic_spine(topic_key);
        CREATE INDEX IF NOT EXISTS idx_global_topic_spine_ticker
            ON global_topic_spine(ticker);

        CREATE INDEX IF NOT EXISTS idx_global_metric_spine_key
            ON global_metric_spine(canonical_metric_key);
        CREATE INDEX IF NOT EXISTS idx_global_metric_spine_ticker_period
            ON global_metric_spine(ticker, period);

        CREATE INDEX IF NOT EXISTS idx_global_entity_spine_key
            ON global_entity_spine(entity_key);
        CREATE INDEX IF NOT EXISTS idx_global_entity_spine_type
            ON global_entity_spine(entity_type);
        CREATE INDEX IF NOT EXISTS idx_global_entity_spine_ticker
            ON global_entity_spine(ticker);

        CREATE INDEX IF NOT EXISTS idx_global_counterparty_spine_key
            ON global_counterparty_spine(counterparty_key);
        CREATE INDEX IF NOT EXISTS idx_global_counterparty_spine_ticker
            ON global_counterparty_spine(ticker);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_global_chain_index_link_id
            ON global_chain_index(link_id);
        CREATE INDEX IF NOT EXISTS idx_global_chain_index_from
            ON global_chain_index(from_ticker);
        CREATE INDEX IF NOT EXISTS idx_global_chain_index_to
            ON global_chain_index(to_ticker);
        CREATE INDEX IF NOT EXISTS idx_global_chain_index_shared_key
            ON global_chain_index(shared_key_type, shared_key);
        CREATE INDEX IF NOT EXISTS idx_global_chain_index_weight
            ON global_chain_index(weight DESC);
        CREATE INDEX IF NOT EXISTS idx_global_chain_index_from_object_weight
            ON global_chain_index(from_object_id, weight DESC, to_object_id);
        CREATE INDEX IF NOT EXISTS idx_global_chain_index_to_object_weight
            ON global_chain_index(to_object_id, weight DESC, from_object_id);
        CREATE INDEX IF NOT EXISTS idx_global_chain_index_from_ticker_weight
            ON global_chain_index(from_ticker, weight DESC, to_ticker);
        CREATE INDEX IF NOT EXISTS idx_global_chain_index_to_ticker_weight
            ON global_chain_index(to_ticker, weight DESC, from_ticker);
        """
    )
    if not include_secondary_indexes:
        _drop_global_spine_secondary_indexes(conn)


def _drop_global_spine_secondary_indexes(conn: sqlite3.Connection) -> None:
    """Drop regenerable global-spine indexes before a bulk merge."""
    names = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'index' AND name LIKE 'idx_global_%'
            ORDER BY name
            """
        )
    ]
    for name in names:
        conn.execute(f'DROP INDEX IF EXISTS "{name}"')


def write_global_spine_metadata(conn: sqlite3.Connection, metadata: Mapping[str, Any] | None = None) -> None:
    """Write required and caller-provided metadata rows."""
    payload: dict[str, Any] = {
        "schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
        "builder_version": GLOBAL_SPINE_BUILDER_VERSION,
        "index_layout": GLOBAL_SPINE_LAYOUT,
        "agent_graph_index_version": GLOBAL_SPINE_AGENT_GRAPH_INDEX_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        payload.update(dict(metadata))
    conn.executemany(
        "INSERT OR REPLACE INTO metadata(key, value_json) VALUES(?, ?)",
        ((key, json.dumps(value, sort_keys=True)) for key, value in sorted(payload.items())),
    )


def read_global_spine_metadata(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read metadata rows from an open global spine connection."""
    try:
        rows = conn.execute("SELECT key, value_json FROM metadata").fetchall()
    except sqlite3.Error:
        return {}
    metadata: dict[str, Any] = {}
    for key, value_json in rows:
        try:
            metadata[str(key)] = json.loads(str(value_json))
        except json.JSONDecodeError:
            metadata[str(key)] = str(value_json)
    return metadata


def initialize_global_spine_database(
    path: Path,
    *,
    metadata: Mapping[str, Any] | None = None,
    replace: bool = False,
) -> Path:
    """Create a global spine database at ``path`` and write required metadata."""
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if replace:
        resolved.unlink(missing_ok=True)
    with sqlite3.connect(resolved) as conn:
        create_global_spine_schema(conn)
        write_global_spine_metadata(conn, metadata)
    return resolved


def verify_global_spine_schema(path: Path) -> dict[str, Any]:
    """Verify that ``path`` is a readable v3 global spine SQLite database."""
    resolved = path.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    tables: list[str] = []
    metadata: dict[str, Any] = {}
    if not resolved.exists():
        return {
            "ok": False,
            "path": str(resolved),
            "errors": ["global_spine_missing"],
            "warnings": warnings,
            "tables": tables,
            "metadata": metadata,
        }
    try:
        with sqlite3.connect(resolved) as conn:
            integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
            if integrity_rows != [("ok",)]:
                errors.append(f"sqlite_integrity_check_failed:{integrity_rows!r}")
            tables = sorted(
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type IN ('table', 'view')
                    """
                ).fetchall()
            )
            metadata = read_global_spine_metadata(conn)
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "path": str(resolved),
            "errors": [f"sqlite_error:{exc}"],
            "warnings": warnings,
            "tables": tables,
            "metadata": metadata,
        }

    table_set = set(tables)
    for table in GLOBAL_SPINE_TABLES:
        if table not in table_set:
            errors.append(f"global_spine_table_missing:{table}")
    for key in GLOBAL_SPINE_REQUIRED_METADATA_KEYS:
        if key not in metadata:
            errors.append(f"global_spine_metadata_missing:{key}")
    if metadata.get("schema_version") != GLOBAL_SPINE_SCHEMA_VERSION:
        errors.append("global_spine_schema_version_mismatch")
    if metadata.get("builder_version") != GLOBAL_SPINE_BUILDER_VERSION:
        errors.append("global_spine_builder_version_mismatch")
    if metadata.get("index_layout") != GLOBAL_SPINE_LAYOUT:
        errors.append("global_spine_index_layout_mismatch")
    return {
        "ok": not errors,
        "path": str(resolved),
        "errors": errors,
        "warnings": warnings,
        "tables": tables,
        "metadata": metadata,
    }
