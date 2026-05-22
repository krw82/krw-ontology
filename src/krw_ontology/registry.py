"""Ontology registry loader for artifact, index, and trace contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REGISTRY_VERSION = "1.0.0-alpha"


@dataclass(frozen=True)
class OntologyRegistry:
    version: str
    object_files: dict[str, str]
    text_fields_by_type: dict[str, list[str]]
    path: Path

    @property
    def canonical_artifacts(self) -> dict[str, str]:
        return dict(self.object_files)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def registry_path() -> Path:
    return _registry_path()


def _registry_path(path: Path | str | None = None) -> Path:
    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    candidates.extend(
        [
            repo_root() / "ontology" / "registry.yaml",
            Path(__file__).resolve().parent / "resources" / "registry.yaml",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def load_ontology_registry(path: Path | str | None = None) -> OntologyRegistry:
    """Load the project ontology registry.

    The registry is intentionally small in v1.0-alpha. It centralizes artifact
    filenames and searchable text fields so index/query code does not grow a
    second, drifting schema map.
    """
    resolved = _registry_path(path)
    data: dict[str, Any] = yaml.safe_load(resolved.read_text()) or {}
    objects = data.get("objects") or {}
    object_files: dict[str, str] = {}
    text_fields_by_type: dict[str, list[str]] = {}
    for object_type, spec in objects.items():
        artifact_file = spec.get("artifact_file")
        if artifact_file:
            object_files[str(object_type)] = str(artifact_file)
        text_fields = [str(field) for field in spec.get("text_fields") or []]
        if text_fields:
            text_fields_by_type[str(object_type)] = text_fields
    return OntologyRegistry(
        version=str(data.get("registry_version") or REGISTRY_VERSION),
        object_files=object_files,
        text_fields_by_type=text_fields_by_type,
        path=resolved,
    )
