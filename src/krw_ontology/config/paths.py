"""Filesystem path helpers for ontology data roots and agent indexes."""

from __future__ import annotations

import os
from pathlib import Path

from krw_ontology.agent_index.builder import DEFAULT_INDEX_RELATIVE_PATH

ONTOLOGY_ROOT_ENV = "KRW_ONTOLOGY_ROOT"
DEFAULT_ONTOLOGY_ROOT = Path.home() / "krw-ontology-data"


def configured_ontology_root(*, fallback_to_cwd: bool = True) -> Path:
    """Return the configured ontology root.

    `KRW_ONTOLOGY_ROOT` is the stable production data root. CLI commands keep
    current-working-directory fallback for backwards compatibility with tests
    and older local workflows.
    """
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
    return resolve_ontology_root(root, fallback_to_cwd=fallback_to_cwd) / DEFAULT_INDEX_RELATIVE_PATH
