"""Release manifest and local release-directory helpers."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krw_ontology.agent_index.builder import DEFAULT_INDEX_RELATIVE_PATH
from krw_ontology.config.paths import (
    ONTOLOGY_ENV_ENV,
    ONTOLOGY_MANIFEST_PATH_ENV,
    resolve_agent_index_path,
)

RELEASE_FORMAT = "krw-ontology-release/v1"
RELEASE_MANIFEST_FILENAME = "manifest.json"
LEGACY_RELEASE_MANIFEST_FILENAME = "release_manifest.json"
ALLOWED_ONTOLOGY_ENVS = {"dev", "staging", "prod"}


def normalize_ontology_env(env: str | None = None) -> str:
    """Return a normalized ontology env name."""
    raw = (env or os.environ.get(ONTOLOGY_ENV_ENV) or "dev").strip().lower()
    if raw not in ALLOWED_ONTOLOGY_ENVS:
        allowed = ", ".join(sorted(ALLOWED_ONTOLOGY_ENVS))
        raise ValueError(f"KRW ontology env must be one of {allowed}; got {raw!r}")
    return raw


def find_release_manifest_path(root: Path, manifest_path: Path | str | None = None) -> Path | None:
    """Find the canonical or legacy release manifest for a root."""
    if manifest_path is not None:
        explicit = Path(manifest_path).expanduser()
        return explicit if explicit.exists() else None
    raw_manifest_path = os.environ.get(ONTOLOGY_MANIFEST_PATH_ENV)
    if raw_manifest_path:
        configured = Path(raw_manifest_path).expanduser()
        if configured.exists():
            return configured
    canonical = root / RELEASE_MANIFEST_FILENAME
    if canonical.exists():
        return canonical
    legacy = root / LEGACY_RELEASE_MANIFEST_FILENAME
    if legacy.exists():
        return legacy
    return None


def load_release_manifest(
    root: Path | str,
    *,
    manifest_path: Path | str | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Load a release manifest if present."""
    root_path = Path(root).expanduser()
    found_path = find_release_manifest_path(root_path, manifest_path)
    if found_path is None:
        return {}, None
    try:
        return json.loads(found_path.read_text(encoding="utf-8")), found_path
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid release manifest JSON: {found_path}") from exc


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _read_index_metadata(index_path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "index_present": index_path.exists(),
        "document_count": 0,
        "object_count": 0,
        "agent_index_schema_version": None,
        "index_generated_at": None,
    }
    if not index_path.exists():
        return metadata
    metadata["index_generated_at"] = datetime.fromtimestamp(
        index_path.stat().st_mtime,
        tz=timezone.utc,
    ).isoformat()
    try:
        with sqlite3.connect(index_path) as conn:
            metadata["document_count"] = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            metadata["object_count"] = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
            try:
                row = conn.execute(
                    "SELECT value FROM build_metadata WHERE key = 'agent_index_schema_version'"
                ).fetchone()
                if row:
                    metadata["agent_index_schema_version"] = row[0]
            except sqlite3.Error:
                pass
    except sqlite3.Error as exc:
        metadata["index_error"] = str(exc)
    return metadata


def build_release_manifest(
    root: Path | str,
    *,
    release_id: str,
    env: str | None = None,
    source_root: Path | str | None = None,
    index_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build a release manifest payload for an immutable ontology release root."""
    root_path = Path(root).expanduser().resolve()
    resolved_env = normalize_ontology_env(env)
    resolved_index_path = (
        Path(index_path).expanduser().resolve()
        if index_path is not None
        else root_path / DEFAULT_INDEX_RELATIVE_PATH
    )
    index_metadata = _read_index_metadata(resolved_index_path)
    manifest: dict[str, Any] = {
        "format": RELEASE_FORMAT,
        "release_id": release_id,
        "env": resolved_env,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root_path),
        "source_root": str(Path(source_root).expanduser().resolve()) if source_root else str(root_path),
        "index_path": _relative_or_absolute(resolved_index_path, root_path),
        "index_abs_path": str(resolved_index_path),
        **index_metadata,
    }
    return manifest


def write_release_manifest(
    root: Path | str,
    *,
    release_id: str,
    env: str | None = None,
    source_root: Path | str | None = None,
    index_path: Path | str | None = None,
    write_legacy: bool = False,
) -> dict[str, Any]:
    """Write manifest.json for a release root."""
    root_path = Path(root).expanduser().resolve()
    root_path.mkdir(parents=True, exist_ok=True)
    manifest = build_release_manifest(
        root_path,
        release_id=release_id,
        env=env,
        source_root=source_root,
        index_path=index_path,
    )
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (root_path / RELEASE_MANIFEST_FILENAME).write_text(payload, encoding="utf-8")
    if write_legacy:
        (root_path / LEGACY_RELEASE_MANIFEST_FILENAME).write_text(payload, encoding="utf-8")
    return manifest


def resolve_manifest_index_path(root: Path, manifest: dict[str, Any], index_path: Path | str | None = None) -> Path:
    """Resolve an index path using explicit input, manifest, env, or default root layout."""
    if index_path is not None:
        return Path(index_path).expanduser().resolve()
    manifest_index_path = manifest.get("index_path")
    if isinstance(manifest_index_path, str) and manifest_index_path:
        candidate = Path(manifest_index_path)
        return candidate.expanduser().resolve() if candidate.is_absolute() else (root / candidate).resolve()
    return resolve_agent_index_path(root, fallback_to_cwd=False)


def verify_release_root(
    root: Path | str,
    *,
    env: str | None = None,
    manifest_path: Path | str | None = None,
    index_path: Path | str | None = None,
    require_current_symlink: bool = False,
) -> dict[str, Any]:
    """Verify release root invariants for worker/MCP use."""
    supplied_root = Path(root).expanduser()
    root_path = supplied_root.resolve()
    errors: list[str] = []
    if require_current_symlink and not supplied_root.is_symlink():
        errors.append("current_symlink_required")

    manifest, found_manifest_path = load_release_manifest(root_path, manifest_path=manifest_path)
    if not manifest:
        errors.append("manifest_missing")

    expected_env = normalize_ontology_env(env) if env is not None else None
    manifest_env = manifest.get("env")
    if expected_env is not None and manifest_env != expected_env:
        errors.append("manifest_env_mismatch")

    manifest_release_id = manifest.get("release_id")
    if manifest and not manifest_release_id:
        errors.append("manifest_release_id_missing")

    resolved_index_path = resolve_manifest_index_path(root_path, manifest, index_path=index_path)
    if not resolved_index_path.exists():
        errors.append("index_missing")

    return {
        "ok": not errors,
        "errors": errors,
        "root": str(root_path),
        "supplied_root": str(supplied_root),
        "manifest_path": str(found_manifest_path) if found_manifest_path else None,
        "manifest": manifest,
        "env": manifest_env,
        "release_id": manifest_release_id,
        "index_path": str(resolved_index_path),
        "index_present": resolved_index_path.exists(),
        "current_symlink": supplied_root.is_symlink(),
    }


def release_env_root(releases_root: Path | str, env: str | None = None) -> Path:
    """Return the env-specific release directory under a releases root."""
    resolved_env = normalize_ontology_env(env)
    root = Path(releases_root).expanduser()
    return root if root.name == resolved_env else root / resolved_env


def current_release_id(env_root: Path) -> str | None:
    """Return the release id pointed to by current, if any."""
    current = env_root / "current"
    if not current.is_symlink():
        return None
    target = os.readlink(current)
    return Path(target).name.rstrip("/")


def list_release_ids(env_root: Path) -> list[str]:
    """List release directories newest first."""
    if not env_root.exists():
        return []
    dirs = [path for path in env_root.iterdir() if path.is_dir() and path.name != "current"]
    return [path.name for path in sorted(dirs, key=lambda path: path.stat().st_mtime, reverse=True)]


def promote_local_release(releases_root: Path | str, *, env: str | None, release_id: str) -> dict[str, Any]:
    """Atomically point an env current symlink to a release id."""
    env_root = release_env_root(releases_root, env)
    release_dir = env_root / release_id
    if not release_dir.is_dir():
        raise FileNotFoundError(f"Release directory not found: {release_dir}")
    verification = verify_release_root(release_dir, env=env)
    if not verification["ok"]:
        raise ValueError(f"Release verification failed: {', '.join(verification['errors'])}")
    env_root.mkdir(parents=True, exist_ok=True)
    current = env_root / "current"
    next_link = env_root / "current.next"
    if next_link.exists() or next_link.is_symlink():
        next_link.unlink()
    next_link.symlink_to(release_id)
    os.replace(next_link, current)
    return {
        "env_root": str(env_root.resolve()),
        "env": normalize_ontology_env(env),
        "release_id": release_id,
        "current": str(current),
    }


def rollback_local_release(
    releases_root: Path | str,
    *,
    env: str | None,
    release_id: str | None = None,
) -> dict[str, Any]:
    """Rollback current to a requested or previous release id."""
    env_root = release_env_root(releases_root, env)
    current_id = current_release_id(env_root)
    target_id = release_id
    if target_id is None:
        for candidate in list_release_ids(env_root):
            if candidate != current_id:
                target_id = candidate
                break
    if target_id is None:
        raise FileNotFoundError("No rollback target found")
    return promote_local_release(env_root, env=env, release_id=target_id)
