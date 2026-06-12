from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from krw_ontology.agent_index.spine_schema import initialize_global_spine_database
from krw_ontology.release import (
    RELEASE_FORMAT_V3,
    verify_release_startup_v3,
    write_release_manifest_v3,
)


def _write_v3_release_files(root: Path) -> None:
    spine_path = initialize_global_spine_database(
        root / "indexes" / "global_spine.sqlite",
        metadata={"release_id": root.name},
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
    shard_path = root / "indexes" / "companies" / "AAPL.sqlite"
    shard_path.parent.mkdir(parents=True, exist_ok=True)
    shard_path.write_text("placeholder shard", encoding="utf-8")
    shard_manifest = {
        "format": "krw-ontology-shard-manifest/v3",
        "shards": {
            "AAPL": {
                "path": "companies/AAPL.sqlite",
                "schema_version": "krw-company-shard/v1",
                "document_count": 1,
                "object_count": 1,
                "edge_count": 0,
                "quality_event_count": 0,
            }
        },
    }
    (root / "indexes" / "shard_manifest.json").write_text(
        json.dumps(shard_manifest, sort_keys=True),
        encoding="utf-8",
    )


def test_write_release_manifest_v3_uses_global_spine_without_monolith(tmp_path: Path) -> None:
    release_root = tmp_path / "dev" / "20260612_000000"
    _write_v3_release_files(release_root)

    manifest = write_release_manifest_v3(release_root, release_id=release_root.name, env="dev")

    assert manifest["format"] == RELEASE_FORMAT_V3
    assert manifest["index_layout"] == "global-spine-and-company-shards"
    assert manifest["monolith_required"] is False
    assert manifest["indexes"]["global_spine"]["required"] is True
    assert manifest["indexes"]["company_shards"]["count"] == 1
    assert "debug_monolith" not in manifest["indexes"]
    assert "monolith" not in manifest["indexes"]


def test_verify_release_startup_v3_accepts_v3_release_without_monolith(tmp_path: Path) -> None:
    release_root = tmp_path / "dev" / "20260612_000000"
    _write_v3_release_files(release_root)
    write_release_manifest_v3(release_root, release_id=release_root.name, env="dev")

    result = verify_release_startup_v3(release_root, env="dev")

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["global_spine_present"] is True
    assert result["global_spine_verification"]["ok"] is True


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
