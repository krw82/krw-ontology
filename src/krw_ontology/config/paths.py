"""Filesystem path helpers for ontology data roots and agent indexes."""

from __future__ import annotations

import os
from pathlib import Path

from krw_ontology.agent_index.builder import DEFAULT_INDEX_RELATIVE_PATH

ONTOLOGY_ENV_ENV = "KRW_ONTOLOGY_ENV"
ONTOLOGY_ROOT_ENV = "KRW_ONTOLOGY_ROOT"
ONTOLOGY_RELEASE_ROOT_ENV = "KRW_ONTOLOGY_RELEASE_ROOT"
ONTOLOGY_MANIFEST_PATH_ENV = "KRW_ONTOLOGY_MANIFEST_PATH"
ONTOLOGY_INDEX_PATH_ENV = "KRW_ONTOLOGY_INDEX_PATH"
DEFAULT_ONTOLOGY_ROOT = Path.home() / "krw-ontology-data"


def configured_ontology_root(*, fallback_to_cwd: bool = True) -> Path:
    """Return the configured ontology root.

    `KRW_ONTOLOGY_RELEASE_ROOT` is the preferred immutable release root for
    workers/MCP. `KRW_ONTOLOGY_ROOT` remains supported for backwards
    compatibility with older local workflows and tests.
    """
    raw_release_root = os.environ.get(ONTOLOGY_RELEASE_ROOT_ENV)
    if raw_release_root:
        return Path(raw_release_root).expanduser().resolve()
    raw_root = os.environ.get(ONTOLOGY_ROOT_ENV)
    if raw_root:
        return Path(raw_root).expanduser().resolve()
    if fallback_to_cwd:
        return Path.cwd().resolve()
    return DEFAULT_ONTOLOGY_ROOT.expanduser().resolve()


def resolve_ontology_root(root: Path | str | None = None, *, fallback_to_cwd: bool = True) -> Path:
    """Resolve an explicit root or configured default root."""
    if root is not None:
        return Path(root).expanduser().resolve()
    return configured_ontology_root(fallback_to_cwd=fallback_to_cwd)


def resolve_agent_index_path(
    root: Path | str | None = None,
    index_path: Path | str | None = None,
    *,
    fallback_to_cwd: bool = True,
) -> Path:
    """Resolve the SQLite agent index path for an ontology root."""
    if index_path is not None:
        return Path(index_path).expanduser().resolve()
    if root is None:
        raw_index_path = os.environ.get(ONTOLOGY_INDEX_PATH_ENV)
        if raw_index_path:
            return Path(raw_index_path).expanduser().resolve()
    return resolve_ontology_root(root, fallback_to_cwd=fallback_to_cwd) / DEFAULT_INDEX_RELATIVE_PATH
