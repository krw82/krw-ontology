from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from krw_ontology.agent_index.router_sidecar import build_router_sidecar
from krw_ontology.agent_index.router_coherence import build_router_coherence
from krw_ontology.agent_index.metric_dictionary import metric_dictionary_binding
from krw_ontology.agent_index import spine_builder
from krw_ontology.agent_index.source_artifact_sqlite import (
    SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
    SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
)
from krw_ontology.agent_index.spine_schema import (
    GLOBAL_SPINE_TABLES,
    initialize_global_spine_database,
    verify_global_spine_schema,
    write_global_spine_metadata,
    write_spine_verification_seal,
)
from krw_ontology.release import (
    RELEASE_FORMAT_V3,
    verify_release_startup_v3,
    write_release_manifest_v3,
)


def _write_v3_release_files(root: Path) -> None:
    spine_path = initialize_global_spine_database(
        root / "indexes" / "global_spine.sqlite",
        metadata={
            "release_id": root.name,
            "spine_projection_version": spine_builder.SPINE_PROJECTION_VERSION,
            "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
            "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
            "company_shard_schema_version": spine_builder.COMPANY_SHARD_SCHEMA_VERSION,
        },
    )
    with sqlite3.connect(spine_path) as conn:
        conn.execute(
            """
            INSERT INTO global_object_locator(
                object_id,
                ticker,
                shard_id,
                shard_path
            )
            VALUES('obj:AAPL:1', 'AAPL', 'AAPL', 'indexes/companies/AAPL.sqlite')
            """
        )
        conn.execute(
            """
            INSERT INTO global_object_replica(
                object_id, ticker, document_id, document_type, period,
                shard_id, shard_path
            )
            VALUES(
                'obj:AAPL:1', 'AAPL', '', '', '',
                'AAPL', 'indexes/companies/AAPL.sqlite'
            )
            """
        )
        write_global_spine_metadata(
            conn,
            {
                "counts": {
                    table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    for table in GLOBAL_SPINE_TABLES
                    if table != "metadata"
                }
            },
        )
    spine_verification = verify_global_spine_schema(
        spine_path,
        deep=True,
        trust_seal=False,
    )
    write_spine_verification_seal(spine_path, spine_verification)
    shard_path = root / "indexes" / "companies" / "AAPL.sqlite"
    shard_path.parent.mkdir(parents=True, exist_ok=True)
    shard_path.write_text("placeholder shard", encoding="utf-8")
    shard_manifest = {
        "format": "krw-ontology-shard-manifest/v3",
        "company_shard_schema_version": spine_builder.COMPANY_SHARD_SCHEMA_VERSION,
        "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
        "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
        "metric_dictionary": metric_dictionary_binding(),
        "shards": {
            "AAPL": {
                "path": "companies/AAPL.sqlite",
                "schema_version": spine_builder.COMPANY_SHARD_SCHEMA_VERSION,
                "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
                "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
                "document_count": 1,
                "object_count": 1,
                "edge_count": 0,
                "quality_event_count": 0,
                "metric_dictionary": metric_dictionary_binding(),
            }
        },
    }
    (root / "indexes" / "shard_manifest.json").write_text(
        json.dumps(shard_manifest, sort_keys=True),
        encoding="utf-8",
    )
    build_router_sidecar(spine_path, release_id=root.name)
    build_router_coherence(spine_path, release_id=root.name)


def test_write_release_manifest_v3_uses_global_spine_without_monolith(tmp_path: Path) -> None:
    release_root = tmp_path / "dev" / "20260612_000000"
    _write_v3_release_files(release_root)

    manifest = write_release_manifest_v3(release_root, release_id=release_root.name, env="dev")

    assert manifest["format"] == RELEASE_FORMAT_V3
    assert manifest["index_layout"] == "global-spine-and-company-shards"
    assert manifest["monolith_required"] is False
    assert manifest["indexes"]["global_spine"]["required"] is True
    assert manifest["indexes"]["router_sidecar"]["required"] is True
    assert manifest["indexes"]["router_coherence"]["required"] is True
    assert manifest["indexes"]["router_coherence"]["verification_ok"] is True
    assert manifest["indexes"]["router_sidecar"]["verification_ok"] is True
    assert manifest["indexes"]["router_sidecar"]["ranking_profile_sha256"]
    assert manifest["indexes"]["router_sidecar"]["build_fingerprint_sha256"]
    assert manifest["indexes"]["company_shards"]["count"] == 1
    assert (
        manifest["builder"]["source_artifact_sqlite_builder_version"]
        == SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION
    )
    assert manifest["builder"]["spine_projection_version"] == spine_builder.SPINE_PROJECTION_VERSION
    assert (
        manifest["indexes"]["company_shards"]["schema_version"]
        == spine_builder.COMPANY_SHARD_SCHEMA_VERSION
    )
    assert manifest["metric_dictionary"] == metric_dictionary_binding()
    assert manifest["indexes"]["company_shards"]["metric_dictionary"] == metric_dictionary_binding()
    assert "debug_monolith" not in manifest["indexes"]
    assert "monolith" not in manifest["indexes"]


def test_write_release_manifest_rejects_missing_replica_table(tmp_path: Path) -> None:
    release_root = tmp_path / "dev" / "20260612_000001"
    _write_v3_release_files(release_root)
    with sqlite3.connect(release_root / "indexes" / "global_spine.sqlite") as conn:
        conn.execute("DROP TABLE global_object_replica")

    with pytest.raises(ValueError, match="global_spine_table_missing:global_object_replica"):
        write_release_manifest_v3(
            release_root,
            release_id=release_root.name,
            env="dev",
        )


def test_release_startup_rejects_missing_required_replica_index(tmp_path: Path) -> None:
    release_root = tmp_path / "dev" / "20260612_000002"
    _write_v3_release_files(release_root)
    write_release_manifest_v3(release_root, release_id=release_root.name, env="dev")
    with sqlite3.connect(release_root / "indexes" / "global_spine.sqlite") as conn:
        conn.execute("DROP INDEX idx_global_object_replica_ticker_doc")

    verification = verify_release_startup_v3(
        release_root,
        env="dev",
        check_sqlite=True,
    )

    assert verification["ok"] is False
    assert (
        "global_spine_index_missing:idx_global_object_replica_ticker_doc"
        in (verification["errors"])
    )


def test_verify_release_startup_v3_accepts_v3_release_without_monolith(tmp_path: Path) -> None:
    release_root = tmp_path / "dev" / "20260612_000000"
    _write_v3_release_files(release_root)
    write_release_manifest_v3(release_root, release_id=release_root.name, env="dev")

    result = verify_release_startup_v3(release_root, env="dev")

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["global_spine_present"] is True
    assert result["global_spine_verification"]["ok"] is True
    assert result["router_sidecar_present"] is True
    assert result["router_sidecar_verification"]["ok"] is True


def test_verify_release_startup_v3_rejects_metric_dictionary_mismatch(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "dev" / "20260612_dictionary_mismatch"
    _write_v3_release_files(release_root)
    manifest = write_release_manifest_v3(
        release_root,
        release_id=release_root.name,
        env="dev",
    )
    manifest["metric_dictionary"]["sha256"] = "0" * 64
    (release_root / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )

    result = verify_release_startup_v3(release_root, env="dev")

    assert result["ok"] is False
    assert "metric_dictionary_sha256_mismatch" in result["errors"]


@pytest.mark.parametrize(
    ("stale_kind", "expected_error"),
    (
        ("global_v1", "manifest_global_spine_schema_version_mismatch"),
        ("projection_v1", "manifest_builder_binding_mismatch:spine_projection_version"),
        (
            "source_builder_v1",
            "manifest_builder_binding_mismatch:source_artifact_sqlite_builder_version",
        ),
    ),
)
def test_verify_release_startup_v3_rejects_stale_serving_bindings(
    tmp_path: Path,
    stale_kind: str,
    expected_error: str,
) -> None:
    release_root = tmp_path / "dev" / f"20260612_{stale_kind}"
    _write_v3_release_files(release_root)
    manifest = write_release_manifest_v3(
        release_root,
        release_id=release_root.name,
        env="dev",
    )
    if stale_kind == "global_v1":
        manifest["builder"]["spine_schema_version"] = "krw-ontology-global-spine/v1"
        manifest["indexes"]["global_spine"]["schema_version"] = "krw-ontology-global-spine/v1"
        with sqlite3.connect(release_root / "indexes" / "global_spine.sqlite") as conn:
            conn.execute(
                "UPDATE metadata SET value_json = ? WHERE key = 'schema_version'",
                (json.dumps("krw-ontology-global-spine/v1"),),
            )
    elif stale_kind == "projection_v1":
        manifest["builder"]["spine_projection_version"] = "spine-projection/v1"
        manifest["indexes"]["global_spine"]["spine_projection_version"] = "spine-projection/v1"
    else:
        manifest["builder"]["source_artifact_sqlite_builder_version"] = (
            "source-artifact-sqlite-builder/v1"
        )
        manifest["indexes"]["company_shards"]["source_artifact_sqlite_builder_version"] = (
            "source-artifact-sqlite-builder/v1"
        )
    (release_root / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )

    result = verify_release_startup_v3(release_root, env="dev")

    assert result["ok"] is False
    assert expected_error in result["errors"]


def test_verify_release_startup_v3_rejects_v2_manifest(tmp_path: Path) -> None:
    release_root = tmp_path / "dev" / "20260612_legacy"
    release_root.mkdir(parents=True)
    (release_root / "manifest.json").write_text(
        json.dumps(
            {
                "format": "krw-ontology-release/v2",
                "release_id": release_root.name,
                "env": "dev",
                "status": "ready",
                "index_path": "indexes/agent_index.sqlite",
            }
        ),
        encoding="utf-8",
    )

    result = verify_release_startup_v3(release_root, env="dev")

    assert result["ok"] is False
    assert "manifest_format_unsupported" in result["errors"]
    assert "manifest_index_layout_unsupported" in result["errors"]


def test_verify_release_startup_v3_rejects_required_debug_monolith(tmp_path: Path) -> None:
    release_root = tmp_path / "dev" / "20260612_debug_monolith"
    _write_v3_release_files(release_root)
    manifest = write_release_manifest_v3(release_root, release_id=release_root.name, env="dev")
    manifest["indexes"]["debug_monolith"] = {
        "path": "debug/monolith.sqlite",
        "required": True,
    }
    (release_root / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )

    result = verify_release_startup_v3(release_root, env="dev")

    assert result["ok"] is False
    assert "manifest_debug_monolith_required" in result["errors"]


def test_write_release_manifest_v3_refuses_missing_router_sidecar(tmp_path: Path) -> None:
    release_root = tmp_path / "dev" / "20260612_missing_router"
    _write_v3_release_files(release_root)
    (release_root / "indexes" / "router_sidecar.sqlite").unlink()

    with pytest.raises(FileNotFoundError, match="Serving Index V2 sidecar"):
        write_release_manifest_v3(
            release_root,
            release_id=release_root.name,
            env="dev",
        )


def test_verify_release_startup_v3_rejects_appended_router_sidecar_bytes(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "dev" / "20260612_router_tampered"
    _write_v3_release_files(release_root)
    write_release_manifest_v3(release_root, release_id=release_root.name, env="dev")
    assert (
        verify_release_startup_v3(
            release_root,
            env="dev",
            check_sqlite=False,
        )["ok"]
        is True
    )

    with (release_root / "indexes" / "router_sidecar.sqlite").open("ab") as handle:
        handle.write(b"tampered")

    result = verify_release_startup_v3(
        release_root,
        env="dev",
        check_sqlite=False,
    )
    assert result["ok"] is False
    assert (
        "router_sidecar:router_sidecar_verification_seal_required:identity_mismatch"
        in result["errors"]
    )


def _write_observations_sidecar(root: Path) -> None:
    from krw_ontology.observation.builder import build_observations_store
    from krw_ontology.observation.ports import RawObservation, SeriesFetchResult
    from krw_ontology.observation.seed import load_series_seed

    seed = load_series_seed()
    rows = [
        RawObservation(
            "macro_cpi_yoy",
            "2026-05-01",
            296.1,
            "2026-06-10",
            "2026-06-10",
            {"endpoint": "series/observations", "params_hash": "t", "fetched_at": "t"},
        ),
        RawObservation(
            "macro_cpi_yoy",
            "2026-05-01",
            296.4,
            "2026-07-15",
            "2026-07-15",
            {"endpoint": "series/observations", "params_hash": "t", "fetched_at": "t"},
        ),
    ]
    result = SeriesFetchResult(
        series_key="macro_cpi_yoy",
        provider="fred",
        observations=tuple(rows),
        status="available",
    )
    build = build_observations_store(root / "indexes" / "observations.sqlite", seed, [result])
    assert build.verification["ok"] is True


def test_write_release_manifest_v3_includes_observations_sidecar(tmp_path: Path) -> None:
    from krw_ontology.observation.store import OBSERVATIONS_SCHEMA_VERSION

    release_root = tmp_path / "dev" / "20260630_observations"
    _write_v3_release_files(release_root)
    _write_observations_sidecar(release_root)

    manifest = write_release_manifest_v3(release_root, release_id=release_root.name, env="dev")

    observations = manifest["indexes"]["observations"]
    assert observations["schema_version"] == OBSERVATIONS_SCHEMA_VERSION
    assert observations["required"] is False
    assert observations["verification_ok"] is True
    assert observations["path"] == "indexes/observations.sqlite"
    assert observations["sha256"]
    assert observations["counts"]["observations"] == 2
    # Observation canonical metrics ride the bound metric dictionary (B2).
    assert observations["metric_dictionary"]["canonical_metric_count"] == 48
    assert observations["metric_dictionary"]["sha256"].startswith("ffc8f3c1")

    startup = verify_release_startup_v3(release_root, env="dev", check_sqlite=True)
    assert startup["ok"] is True, startup["errors"]
    assert startup["observations_present"] is True
    assert startup["observations_verification"]["ok"] is True


def test_write_release_manifest_v3_without_observations_sidecar(tmp_path: Path) -> None:
    release_root = tmp_path / "dev" / "20260630_no_observations"
    _write_v3_release_files(release_root)

    manifest = write_release_manifest_v3(release_root, release_id=release_root.name, env="dev")

    assert "observations" not in manifest["indexes"]
    startup = verify_release_startup_v3(release_root, env="dev", check_sqlite=False)
    assert startup["ok"] is True
    assert startup["observations_present"] is False
