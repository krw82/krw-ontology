from __future__ import annotations

import sqlite3
from pathlib import Path

from krw_ontology.agent_index.spine_schema import (
    GLOBAL_SPINE_LAYOUT,
    GLOBAL_SPINE_SCHEMA_VERSION,
    GLOBAL_SPINE_TABLES,
    create_global_spine_schema,
    initialize_global_spine_database,
    read_global_spine_metadata,
    verify_global_spine_schema,
    write_global_spine_metadata,
)


def test_create_global_spine_schema_creates_required_tables() -> None:
    with sqlite3.connect(":memory:") as conn:
        create_global_spine_schema(conn)
        write_global_spine_metadata(conn, {"release_id": "test-release"})

        tables = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type IN ('table', 'view')
                """
            ).fetchall()
        }
        metadata = read_global_spine_metadata(conn)

    assert set(GLOBAL_SPINE_TABLES).issubset(tables)
    assert metadata["schema_version"] == GLOBAL_SPINE_SCHEMA_VERSION
    assert metadata["index_layout"] == GLOBAL_SPINE_LAYOUT
    assert metadata["release_id"] == "test-release"


def test_initialize_and_verify_global_spine_database(tmp_path: Path) -> None:
    path = initialize_global_spine_database(
        tmp_path / "indexes" / "global_spine.sqlite",
        metadata={"release_id": "20260612_000000", "source_manifest_hash": "abc"},
    )

    result = verify_global_spine_schema(path)

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["metadata"]["schema_version"] == GLOBAL_SPINE_SCHEMA_VERSION
    assert result["metadata"]["release_id"] == "20260612_000000"
    assert result["metadata"]["source_manifest_hash"] == "abc"


def test_verify_global_spine_schema_reports_missing_tables(tmp_path: Path) -> None:
    path = tmp_path / "broken.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        write_global_spine_metadata(conn)

    result = verify_global_spine_schema(path)

    assert result["ok"] is False
    assert "global_spine_table_missing:global_object_locator" in result["errors"]


def test_verify_global_spine_schema_reports_missing_file(tmp_path: Path) -> None:
    result = verify_global_spine_schema(tmp_path / "missing.sqlite")

    assert result["ok"] is False
    assert result["errors"] == ["global_spine_missing"]

