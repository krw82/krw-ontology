from __future__ import annotations

import json
from pathlib import Path

from krw_ontology.guru.workspace import (
    GURU_DATA_ROOT_ENV,
    GURU_ENV_ENV,
    GURU_RELEASE_ROOT_ENV,
    GURU_ROOT_ENV,
    GURU_RUNNING_ROOT_ENV,
    default_guru_root,
    default_guru_running_root,
    guru_release_status,
    promote_guru_release,
    rollback_guru_release,
)


def test_default_guru_roots_use_operational_release_layout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(GURU_ROOT_ENV, raising=False)
    monkeypatch.delenv(GURU_RELEASE_ROOT_ENV, raising=False)
    monkeypatch.delenv(GURU_RUNNING_ROOT_ENV, raising=False)
    monkeypatch.setenv(GURU_DATA_ROOT_ENV, str(tmp_path / "guru-data"))
    monkeypatch.setenv(GURU_ENV_ENV, "dev")

    assert default_guru_root() == tmp_path / "guru-data" / "releases" / "dev" / "current"
    assert default_guru_running_root() == tmp_path / "guru-data" / "runs" / "default"

    monkeypatch.setenv(GURU_RELEASE_ROOT_ENV, str(tmp_path / "explicit-release"))
    assert default_guru_root() == tmp_path / "explicit-release"

    monkeypatch.setenv(GURU_ROOT_ENV, str(tmp_path / "legacy-root"))
    assert default_guru_root() == tmp_path / "legacy-root"


def test_promote_guru_release_copies_reviewed_root_and_updates_current(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    reviewed = source / "reviewed"
    indexes = source / "indexes"
    reviewed.mkdir(parents=True)
    indexes.mkdir(parents=True)
    (reviewed / "guru_objects.jsonl").write_text(
        json.dumps({"reviewed_id": "guru:buffett:1", "author_key": "buffett"}) + "\n",
        encoding="utf-8",
    )
    (indexes / "guru_shard_manifest.json").write_text('{"ok": true}\n', encoding="utf-8")
    (source / "source_manifest.yaml").write_text("authors: []\n", encoding="utf-8")

    payload = promote_guru_release(
        source,
        data_root=tmp_path / "guru-data",
        env="prod",
        release_id="rel-001",
    )

    release_root = tmp_path / "guru-data" / "releases" / "prod" / "rel-001"
    current = tmp_path / "guru-data" / "releases" / "prod" / "current"
    assert current.is_symlink()
    assert current.resolve() == release_root
    assert payload["promoted_release_id"] == "rel-001"
    assert (release_root / "reviewed" / "guru_objects.jsonl").is_file()

    manifest = json.loads((release_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "krw-guru-release/v1"
    assert manifest["release_id"] == "rel-001"
    assert manifest["env"] == "prod"
    assert manifest["reviewed_files"]["guru_objects.jsonl"]["rows"] == 1

    status = guru_release_status(data_root=tmp_path / "guru-data", env="prod")
    assert status["current_release_id"] == "rel-001"
    assert status["manifest_exists"] is True


def test_rollback_guru_release_updates_current_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "reviewed").mkdir(parents=True)
    (source / "reviewed" / "guru_objects.jsonl").write_text("", encoding="utf-8")

    promote_guru_release(source, data_root=tmp_path / "guru-data", env="prod", release_id="rel-a")
    promote_guru_release(source, data_root=tmp_path / "guru-data", env="prod", release_id="rel-b")

    payload = rollback_guru_release(
        data_root=tmp_path / "guru-data",
        env="prod",
        release_id="rel-a",
    )

    assert payload["rolled_back_to_release_id"] == "rel-a"
    assert payload["current_release_id"] == "rel-a"
    assert (tmp_path / "guru-data" / "releases" / "prod" / "current").resolve() == (
        tmp_path / "guru-data" / "releases" / "prod" / "rel-a"
    )
