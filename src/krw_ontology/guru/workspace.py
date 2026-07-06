"""Workspace creation for the standalone guru ontology pipeline."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from krw_ontology.guru.planner import (
    build_collection_plan,
    build_ontology_manifest,
    build_source_manifest,
)

GURU_ROOT_ENV = "KRW_GURU_ROOT"
GURU_RUNNING_ROOT_ENV = "KRW_GURU_RUNNING_ROOT"
DEFAULT_GURU_ROOT_NAME = "krw-ontology-guru"
DEFAULT_GURU_RUNNING_ROOT_NAME = "krw-ontology-guru-running"


def default_guru_root() -> Path:
    value = os.environ.get(GURU_ROOT_ENV)
    return Path(value).expanduser().resolve() if value else Path.home() / DEFAULT_GURU_ROOT_NAME


def default_guru_running_root() -> Path:
    value = os.environ.get(GURU_RUNNING_ROOT_ENV)
    if value:
        return Path(value).expanduser().resolve()
    return Path.home() / DEFAULT_GURU_RUNNING_ROOT_NAME


def guru_root(root: Path | str | None = None) -> Path:
    return Path(root).expanduser().resolve() if root is not None else default_guru_root()


def guru_running_root(root: Path | str | None = None) -> Path:
    if root is None:
        return default_guru_running_root()
    return Path(root).expanduser().resolve()


def initialize_guru_workspace(
    root: Path | str | None = None,
    *,
    running_root: Path | str | None = None,
    author_keys: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create a guru workspace and write planning manifests.

    This function does not fetch, parse, or store full source text.
    """
    root_path = guru_root(root)
    running_path = guru_running_root(running_root)
    root_path.mkdir(parents=True, exist_ok=True)
    running_path.mkdir(parents=True, exist_ok=True)
    for relative in ("reviewed", "published"):
        (root_path / relative).mkdir(parents=True, exist_ok=True)
    for relative in (
        "raw",
        "parsed",
        "spans",
        "generated",
        "generated/requests",
        "generated/responses",
        "logs",
    ):
        (running_path / relative).mkdir(parents=True, exist_ok=True)

    source_manifest = build_source_manifest(author_keys)
    ontology_manifest = build_ontology_manifest(author_keys)
    collection_plan = build_collection_plan(root_path, author_keys, running_root=running_path)

    outputs = {
        "source_manifest": root_path / "source_manifest.yaml",
        "ontology_manifest": root_path / "ontology_manifest.json",
        "collection_plan": root_path / "collection_plan.json",
    }
    for path in outputs.values():
        if path.exists() and not force:
            raise FileExistsError(f"Guru workspace file already exists: {path}")

    _write_yaml_atomic(outputs["source_manifest"], source_manifest.model_dump(mode="json"))
    _write_json_atomic(outputs["ontology_manifest"], ontology_manifest.model_dump(mode="json"))
    _write_json_atomic(outputs["collection_plan"], collection_plan.model_dump(mode="json"))

    return {
        "root": str(root_path),
        "running_root": str(running_path),
        "collection_started": False,
        "extraction_started": False,
        "author_keys": collection_plan.author_keys,
        "files": {name: str(path) for name, path in outputs.items()},
    }


def guru_workspace_status(
    root: Path | str | None = None,
    *,
    running_root: Path | str | None = None,
) -> dict[str, Any]:
    """Return a cheap status summary for a guru workspace."""
    root_path = guru_root(root)
    running_path = guru_running_root(running_root)
    files = {
        "source_manifest": root_path / "source_manifest.yaml",
        "ontology_manifest": root_path / "ontology_manifest.json",
        "collection_plan": root_path / "collection_plan.json",
        "discovery_manifest": running_path / "discovery_manifest.json",
        "raw_manifest": running_path / "raw_manifest.json",
        "parsed_manifest": running_path / "parsed_manifest.json",
        "extraction_manifest": running_path / "generated" / "extraction_manifest.json",
    }
    return {
        "root": str(root_path),
        "running_root": str(running_path),
        "exists": root_path.exists(),
        "running_exists": running_path.exists(),
        "collection_started": (running_path / "raw_manifest.json").exists(),
        "extraction_started": (running_path / "generated" / "extraction_manifest.json").exists(),
        "files": {name: {"path": str(path), "exists": path.exists()} for name, path in files.items()},
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _write_yaml_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(
        yaml.safe_dump(payload, allow_unicode=False, sort_keys=False),
        encoding="utf-8",
    )
    tmp_path.replace(path)
