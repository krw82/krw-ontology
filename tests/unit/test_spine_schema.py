from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest

import krw_ontology.agent_index.spine_schema as spine_schema
from krw_ontology.agent_index.spine_schema import (
    GLOBAL_SPINE_ALLOWED_SECONDARY_INDEXES,
    GLOBAL_SPINE_LAYOUT,
    GLOBAL_SPINE_REPLICA_INVARIANT_VERSION,
    GLOBAL_SPINE_ROUTING_MODE,
    GLOBAL_SPINE_SCHEMA_VERSION,
    GLOBAL_SPINE_TABLES,
    SPINE_FRAGMENT_SCHEMA_VERSION,
    SPINE_FRAGMENT_TABLES,
    create_global_spine_schema,
    create_spine_fragment_schema,
    initialize_global_spine_database,
    inherit_spine_verification_seal,
    read_global_spine_metadata,
    read_spine_verification_sha256,
    record_spine_verification_sha256,
    spine_verification_seal_path,
    verify_global_spine_schema,
    verify_spine_fragment_schema,
    write_global_spine_metadata,
    write_spine_verification_seal,
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
    assert {"global_object_replica", "global_edge_replica"}.issubset(tables)
    assert metadata["schema_version"] == GLOBAL_SPINE_SCHEMA_VERSION
    assert metadata["index_layout"] == GLOBAL_SPINE_LAYOUT
    assert metadata["routing_mode"] == GLOBAL_SPINE_ROUTING_MODE
    assert metadata["replica_invariant_version"] == GLOBAL_SPINE_REPLICA_INVARIANT_VERSION
    assert metadata["release_id"] == "test-release"


def test_global_spine_schema_uses_primary_keys_without_duplicate_unique_indexes() -> None:
    duplicate_names = {
        "idx_global_object_locator_object_id",
        "idx_global_document_catalog_document_id",
        "idx_global_edge_spine_edge_id",
        "idx_global_topic_spine_topic_id",
        "idx_global_chain_index_link_id",
    }
    with sqlite3.connect(":memory:") as conn:
        create_global_spine_schema(conn)
        indexes = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        primary_origins = {
            str(row[3])
            for table in (
                "global_object_locator",
                "global_document_catalog",
                "global_edge_spine",
                "global_topic_spine",
                "global_chain_index",
            )
            for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
        }

    assert indexes.isdisjoint(duplicate_names)
    assert "pk" in primary_origins


def test_ticker_aware_graph_indexes_are_required_and_selected() -> None:
    expected_indexes = {
        "idx_global_object_replica_ticker_local_key",
        "idx_global_edge_replica_ticker_from_confidence_to",
        "idx_global_edge_replica_ticker_to_confidence_from",
        "idx_global_chain_index_from_ticker_object_weight",
        "idx_global_chain_index_to_ticker_object_weight",
    }
    with sqlite3.connect(":memory:") as conn:
        create_global_spine_schema(conn)
        plans = {
            "idx_global_object_replica_ticker_local_key": _query_plan(
                conn,
                """
                SELECT object_id
                FROM global_object_replica
                WHERE ticker = ? AND local_object_key = ?
                LIMIT 1
                """,
                ("AAPL", "validation_report:UNKNOWN:UNKNOWN:UNKNOWN:with_edges"),
            ),
            "idx_global_edge_replica_ticker_from_confidence_to": _query_plan(
                conn,
                """
                SELECT edge_id, to_object_id
                FROM global_edge_replica
                WHERE ticker = ? AND from_object_id = ?
                ORDER BY confidence DESC, to_object_id
                LIMIT 24
                """,
                ("AAPL", "term:factor:shared"),
            ),
            "idx_global_edge_replica_ticker_to_confidence_from": _query_plan(
                conn,
                """
                SELECT edge_id, from_object_id
                FROM global_edge_replica
                WHERE ticker = ? AND to_object_id = ?
                ORDER BY confidence DESC, from_object_id
                LIMIT 24
                """,
                ("AAPL", "term:metric:shared"),
            ),
            "idx_global_chain_index_from_ticker_object_weight": _query_plan(
                conn,
                """
                SELECT link_id, to_ticker, to_object_id
                FROM global_chain_index
                WHERE from_ticker = ? AND from_object_id = ?
                ORDER BY weight DESC, to_ticker, to_object_id
                LIMIT 24
                """,
                ("AAPL", "term:factor:shared"),
            ),
            "idx_global_chain_index_to_ticker_object_weight": _query_plan(
                conn,
                """
                SELECT link_id, from_ticker, from_object_id
                FROM global_chain_index
                WHERE to_ticker = ? AND to_object_id = ?
                ORDER BY weight DESC, from_ticker, from_object_id
                LIMIT 24
                """,
                ("MSFT", "term:metric:shared"),
            ),
        }

    assert expected_indexes.issubset(set(GLOBAL_SPINE_ALLOWED_SECONDARY_INDEXES))
    for index_name, plan in plans.items():
        assert "USING" in plan
        assert index_name in plan


def test_global_spine_schema_omits_legacy_fts_storage() -> None:
    with sqlite3.connect(":memory:") as conn:
        create_global_spine_schema(conn)
        legacy_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'global_search_fts%'"
            ).fetchall()
        }

    assert legacy_tables == set()


def test_spine_fragment_schema_is_lean_and_has_dedicated_verifier(tmp_path: Path) -> None:
    path = tmp_path / "fragment.sqlite"
    with sqlite3.connect(path) as conn:
        create_spine_fragment_schema(conn)
        write_global_spine_metadata(
            conn,
            {
                "format": "krw-ontology-spine-fragment/v3",
                "fragment_schema_version": SPINE_FRAGMENT_SCHEMA_VERSION,
                "ticker": "AAPL",
            },
        )

    result = verify_spine_fragment_schema(path)

    assert result["ok"] is True, result["errors"]
    assert set(result["tables"]) == set(SPINE_FRAGMENT_TABLES)
    assert "global_chain_index" not in result["tables"]
    assert "global_key_stats" not in result["tables"]
    assert "global_object_replica" not in result["tables"]
    assert "global_edge_replica" not in result["tables"]
    assert "global_search_fts" not in result["tables"]
    assert not any(name.startswith("idx_global_") for name in _index_names(path))


def test_global_verifier_rejects_legacy_fts_shadow_and_unplanned_index(tmp_path: Path) -> None:
    path = initialize_global_spine_database(tmp_path / "global.sqlite")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE global_search_fts_data(id INTEGER)")
        conn.execute(
            "CREATE INDEX idx_unplanned_locator_summary ON global_object_locator(compact_summary)"
        )

    result = verify_global_spine_schema(path, deep=False, trust_seal=False)

    assert result["ok"] is False
    assert "global_spine_forbidden_table:global_search_fts_data" in result["errors"]
    assert "global_spine_forbidden_index:idx_unplanned_locator_summary" in result["errors"]


def test_fragment_verifier_rejects_legacy_fts_shadow_and_serving_index(tmp_path: Path) -> None:
    path = tmp_path / "fragment.sqlite"
    _write_minimal_fragment(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE global_search_fts_segdir(id INTEGER)")
        conn.execute(
            "CREATE INDEX idx_global_object_locator_ticker ON global_object_locator(ticker)"
        )

    result = verify_spine_fragment_schema(path, deep=False, trust_seal=False)

    assert result["ok"] is False
    assert "spine_fragment_forbidden_table:global_search_fts_segdir" in result["errors"]
    assert "spine_fragment_forbidden_index:idx_global_object_locator_ticker" in result["errors"]


def test_spine_verifier_uses_deep_seal_and_falls_back_after_mutation(tmp_path: Path) -> None:
    path = tmp_path / "fragment.sqlite"
    _write_minimal_fragment(path)

    deep = verify_spine_fragment_schema(path, deep=True, trust_seal=False)
    seal_path = write_spine_verification_seal(path, deep)
    sealed = verify_spine_fragment_schema(path)
    quick = verify_spine_fragment_schema(path, deep=False, trust_seal=False)

    assert seal_path == spine_verification_seal_path(path)
    assert sealed["verification_mode"] == "deep-sealed"
    assert sealed["integrity_source"] == "immutable_seal"
    assert quick["verification_mode"] == "quick"
    assert quick["integrity_source"] == "sqlite_quick_check"
    assert read_spine_verification_sha256(path) is None

    digest = "a" * 64
    record_spine_verification_sha256(path, digest)
    assert read_spine_verification_sha256(path) == digest
    with pytest.raises(ValueError, match="lowercase 64-hex"):
        record_spine_verification_sha256(path, "not-a-digest")

    with sqlite3.connect(path) as conn:
        write_global_spine_metadata(conn, {"ticker": "AAPL", "mutation": True})

    fallback = verify_spine_fragment_schema(path)

    assert fallback["ok"] is True, fallback["errors"]
    assert fallback["seal_status"] == "identity_mismatch"
    assert fallback["integrity_source"] == "sqlite_integrity_check"
    assert read_spine_verification_sha256(path) is None


def test_global_spine_copy_inherits_deep_seal_without_identity_reuse(tmp_path: Path) -> None:
    source = tmp_path / "source" / "global_spine.sqlite"
    target = tmp_path / "target" / "global_spine.sqlite"
    initialize_global_spine_database(source, metadata={"release_id": "source"}, replace=True)
    deep = verify_global_spine_schema(source, deep=True, trust_seal=False)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    write_spine_verification_seal(source, deep, sha256=digest)
    target.parent.mkdir(parents=True)
    shutil.copy2(source, target)

    inherited = inherit_spine_verification_seal(source, target)

    assert inherited["ok"] is True, inherited["errors"]
    assert inherited["seal_status"] == "valid"
    assert source.stat().st_ino != target.stat().st_ino
    assert read_spine_verification_sha256(target) == digest


def test_spine_seal_survives_ctime_only_metadata_change(tmp_path: Path) -> None:
    path = tmp_path / "fragment.sqlite"
    _write_minimal_fragment(path)
    deep = verify_spine_fragment_schema(path, deep=True, trust_seal=False)
    write_spine_verification_seal(path, deep, sha256="a" * 64)
    before = path.stat()

    os.chmod(path, stat.S_IMODE(before.st_mode) ^ stat.S_IXUSR)
    after = path.stat()
    sealed = verify_spine_fragment_schema(
        path,
        deep=False,
        require_trusted_seal=True,
    )

    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns
    assert sealed["ok"] is True, sealed["errors"]
    assert sealed["seal_status"] == "valid"
    assert sealed["integrity_source"] == "immutable_seal"
    assert read_spine_verification_sha256(path) == "a" * 64


def test_spine_seal_survives_device_id_change_after_volume_remount(tmp_path: Path) -> None:
    path = tmp_path / "fragment.sqlite"
    _write_minimal_fragment(path)
    deep = verify_spine_fragment_schema(path, deep=True, trust_seal=False)
    seal_path = write_spine_verification_seal(path, deep, sha256="a" * 64)
    payload = json.loads(seal_path.read_text(encoding="utf-8"))
    payload["database_identity"]["device"] += 1
    payload["sha256_database_identity"]["device"] += 1
    seal_path.write_text(json.dumps(payload), encoding="utf-8")

    sealed = verify_spine_fragment_schema(
        path,
        deep=False,
        require_trusted_seal=True,
    )

    assert sealed["ok"] is True, sealed["errors"]
    assert sealed["seal_status"] == "valid"
    assert read_spine_verification_sha256(path) == "a" * 64


def test_required_spine_seal_never_falls_back_to_sqlite_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "fragment.sqlite"
    _write_minimal_fragment(path)
    original_connect = sqlite3.connect

    class GuardedConnection:
        def __init__(self, *args, **kwargs):
            self._conn = original_connect(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self._conn.close()

        def execute(self, sql, *args, **kwargs):
            normalized = str(sql).strip().lower()
            if normalized.startswith(("pragma quick_check", "pragma integrity_check")):
                raise AssertionError("startup verification must not scan the database")
            return self._conn.execute(sql, *args, **kwargs)

    monkeypatch.setattr(spine_schema.sqlite3, "connect", GuardedConnection)
    result = verify_spine_fragment_schema(
        path,
        deep=False,
        require_trusted_seal=True,
    )

    assert result["ok"] is False
    assert "spine_fragment_verification_seal_required:missing" in result["errors"]
    assert result["integrity_check"] is None
    assert result["integrity_source"] == "none"


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
    assert result["replica_invariants"]["ok"] is True
    assert result["replica_invariants"]["source"] == "sqlite_query"


def test_global_replica_invariants_are_checked_and_reused_from_seal(tmp_path: Path) -> None:
    path = _write_valid_replica_global(tmp_path / "global.sqlite")

    deep = verify_global_spine_schema(path, deep=True, trust_seal=False)
    write_spine_verification_seal(path, deep)
    sealed = verify_global_spine_schema(path)

    assert deep["ok"] is True, deep["errors"]
    assert deep["replica_invariants"]["version"] == GLOBAL_SPINE_REPLICA_INVARIANT_VERSION
    assert deep["replica_invariants"]["source"] == "sqlite_query"
    assert deep["replica_invariants"]["checks"]["objects"]["ok"] is True
    assert deep["replica_invariants"]["checks"]["edges"]["ok"] is True
    assert sealed["ok"] is True, sealed["errors"]
    assert sealed["integrity_source"] == "immutable_seal"
    assert sealed["replica_invariants"]["source"] == "immutable_seal"

    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE global_object_locator SET occurrence_count = 7 "
            "WHERE object_id = 'term:factor:shared'"
        )
    mutated = verify_global_spine_schema(path)

    assert mutated["ok"] is False
    assert mutated["seal_status"] == "identity_mismatch"
    assert mutated["replica_invariants"]["source"] == "sqlite_query"
    assert any(
        error.startswith("global_spine_objects_replica_invariant_failed:")
        for error in mutated["errors"]
    )


def test_global_verifier_uses_single_validated_seal_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_valid_replica_global(tmp_path / "global.sqlite")
    deep = verify_global_spine_schema(path, deep=True, trust_seal=False)
    write_spine_verification_seal(path, deep)
    original = spine_schema._read_matching_spine_verification_seal
    calls = 0

    def drifting_identity(*args, **kwargs):
        nonlocal calls
        calls += 1
        payload, status = original(*args, **kwargs)
        if calls > 1:
            return payload, "identity_mismatch"
        return payload, status

    monkeypatch.setattr(
        spine_schema,
        "_read_matching_spine_verification_seal",
        drifting_identity,
    )

    result = verify_global_spine_schema(path)

    assert result["ok"] is True, result["errors"]
    assert result["replica_invariants"]["source"] == "immutable_seal"
    assert calls == 1


@pytest.mark.parametrize(
    ("mutation_sql", "error_prefix"),
    (
        (
            "UPDATE global_object_locator SET occurrence_count = 3 "
            "WHERE object_id = 'term:factor:shared'",
            "global_spine_objects_replica_invariant_failed:",
        ),
        (
            "DELETE FROM global_edge_replica WHERE edge_id = 'edge:shared'",
            "global_spine_edges_replica_invariant_failed:",
        ),
        (
            "UPDATE global_edge_replica SET semantic_hash = 'semantic:conflict' "
            "WHERE edge_id = 'edge:shared'",
            "global_spine_edges_replica_invariant_failed:",
        ),
    ),
)
def test_global_replica_invariant_corruption_fails_deep_verification(
    tmp_path: Path,
    mutation_sql: str,
    error_prefix: str,
) -> None:
    path = _write_valid_replica_global(tmp_path / "global.sqlite")
    with sqlite3.connect(path) as conn:
        conn.execute(mutation_sql)

    result = verify_global_spine_schema(path, deep=True, trust_seal=False)

    assert result["ok"] is False
    assert any(error.startswith(error_prefix) for error in result["errors"])
    assert result["replica_invariants"]["ok"] is False


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


def _index_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }


def _query_plan(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[str, ...],
) -> str:
    return "\n".join(
        str(row[3]) for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    )


def _write_minimal_fragment(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        create_spine_fragment_schema(conn)
        write_global_spine_metadata(
            conn,
            {
                "format": "krw-ontology-spine-fragment/v3",
                "fragment_schema_version": SPINE_FRAGMENT_SCHEMA_VERSION,
                "ticker": "AAPL",
            },
        )


def _write_valid_replica_global(path: Path) -> Path:
    initialize_global_spine_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO global_object_locator(
                object_id, ticker, document_id, document_type, period,
                object_type, shard_id, shard_path, local_object_key,
                semantic_hash, occurrence_count
            )
            VALUES(
                'term:factor:shared', 'AAPL', 'AAPL:10K:FY2025', '10-K',
                'FY2025', 'CanonicalEntity', 'AAPL', 'AAPL.sqlite',
                'term:factor:shared', 'semantic:object', 2
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO global_object_replica(
                object_id, ticker, document_id, document_type, period,
                shard_id, shard_path, object_type, local_object_key,
                semantic_hash
            )
            VALUES(
                'term:factor:shared', ?, ?, '10-K', 'FY2025', ?, ?,
                'CanonicalEntity', 'term:factor:shared', 'semantic:object'
            )
            """,
            (
                ("AAPL", "AAPL:10K:FY2025", "AAPL", "AAPL.sqlite"),
                ("MSFT", "MSFT:10K:FY2025", "MSFT", "MSFT.sqlite"),
            ),
        )
        conn.execute(
            """
            INSERT INTO global_edge_spine(
                edge_id, ticker, document_id, document_type, period,
                shard_id, shard_path, from_object_id, to_object_id,
                relation_type, edge_scope, semantic_hash, occurrence_count
            )
            VALUES(
                'edge:shared', 'AAPL', 'AAPL:10K:FY2025', '10-K', 'FY2025',
                'AAPL', 'AAPL.sqlite', 'term:factor:shared',
                'term:factor:shared', 'supports', 'intra_company',
                'semantic:edge', 1
            )
            """
        )
        conn.execute(
            """
            INSERT INTO global_edge_replica(
                edge_id, ticker, document_id, document_type, period,
                shard_id, shard_path, from_object_id, to_object_id,
                relation_type, semantic_hash
            )
            VALUES(
                'edge:shared', 'AAPL', 'AAPL:10K:FY2025', '10-K', 'FY2025',
                'AAPL', 'AAPL.sqlite', 'term:factor:shared',
                'term:factor:shared', 'supports', 'semantic:edge'
            )
            """
        )
    return path
