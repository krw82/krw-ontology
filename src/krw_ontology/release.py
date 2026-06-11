"""Release manifest and local release-directory helpers."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import hashlib
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krw_ontology.agent_index.builder import (
    DEFAULT_INDEX_RELATIVE_PATH,
    verify_agent_index,
    verify_index_shards,
)
from krw_ontology.config.paths import (
    ONTOLOGY_ENV_ENV,
    ONTOLOGY_MANIFEST_PATH_ENV,
    resolve_agent_index_path,
)

RELEASE_FORMAT = "krw-ontology-release/v2"
RELEASE_MANIFEST_FILENAME = "manifest.json"
RELEASE_VERIFY_REPORT_FORMAT = "krw-ontology-release-verify/v1"
RELEASE_SMOKE_QUERIES_FORMAT = "krw-ontology-release-smoke-queries/v1"
RELEASE_SMOKE_BASELINE_FORMAT = "krw-ontology-release-smoke-baseline/v1"
RELEASE_RANKING_QUALITY_FORMAT = "krw-ontology-ranking-quality/v1"
RELEASE_RANKING_THRESHOLD_CALIBRATION_FORMAT = "krw-ontology-ranking-threshold-calibration/v1"
RELEASE_EVENT_FORMAT = "krw-ontology-release-event/v1"
RELEASE_VERIFY_DIRNAME = "verify"
RELEASE_VERIFY_REPORT_FILENAME = "release_verify.json"
RELEASE_SMOKE_QUERIES_FILENAME = "smoke_queries.json"
RELEASE_SMOKE_BASELINE_FILENAME = "smoke_queries.baseline.json"
RELEASE_RANKING_QUALITY_FILENAME = "ranking_quality.json"
RELEASE_EVENTS_FILENAME = "release_events.jsonl"
RELEASE_EVENTS_DIRNAME = "events"
FAILED_RELEASE_DIRNAME = "failed"
FAILED_RELEASE_METADATA_FILENAME = "failure.json"
FAILED_RELEASE_FORMAT = "krw-ontology-failed-release/v1"
RELEASE_ENV_RESERVED_DIRNAMES = frozenset(
    {
        "current",
        "candidates",
        FAILED_RELEASE_DIRNAME,
        "incoming",
        "locks",
        RELEASE_EVENTS_DIRNAME,
    }
)
GLOBAL_TOPIC_RANKING_TOP_K = 5
GLOBAL_TOPIC_RANKING_MIN_OVERLAP_RATIO = 0.5
GLOBAL_TOPIC_RANKING_MIN_OVERLAP_COUNT = 1
GLOBAL_TOPIC_RANKING_MAX_RANK_DELTA = 3
GLOBAL_TOPIC_RANKING_SAMPLE_LIMIT = 5
GLOBAL_TOPIC_RANKING_TOP_K_ENV = "KRW_RELEASE_SMOKE_GLOBAL_TOPIC_TOP_K"
GLOBAL_TOPIC_RANKING_MIN_OVERLAP_RATIO_ENV = "KRW_RELEASE_SMOKE_GLOBAL_TOPIC_MIN_OVERLAP_RATIO"
GLOBAL_TOPIC_RANKING_MIN_OVERLAP_COUNT_ENV = "KRW_RELEASE_SMOKE_GLOBAL_TOPIC_MIN_OVERLAP_COUNT"
GLOBAL_TOPIC_RANKING_MAX_RANK_DELTA_ENV = "KRW_RELEASE_SMOKE_GLOBAL_TOPIC_MAX_RANK_DELTA"
GLOBAL_TOPIC_RANKING_SAMPLE_LIMIT_ENV = "KRW_RELEASE_SMOKE_GLOBAL_TOPIC_SAMPLE_LIMIT"
ALLOWED_ONTOLOGY_ENVS = {"dev", "staging", "prod"}


def normalize_ontology_env(env: str | None = None) -> str:
    """Return a normalized ontology env name."""
    raw = (env or os.environ.get(ONTOLOGY_ENV_ENV) or "dev").strip().lower()
    if raw not in ALLOWED_ONTOLOGY_ENVS:
        allowed = ", ".join(sorted(ALLOWED_ONTOLOGY_ENVS))
        raise ValueError(f"KRW ontology env must be one of {allowed}; got {raw!r}")
    return raw


def _env_int(name: str, default: int, *, min_value: int, max_value: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < min_value:
        return min_value
    if max_value is not None and value > max_value:
        return max_value
    return value


def _env_float(name: str, default: float, *, min_value: float, max_value: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < min_value:
        return min_value
    if max_value is not None and value > max_value:
        return max_value
    return value


def _global_topic_ranking_thresholds(
    overrides: Mapping[str, int | float | None] | None = None,
) -> dict[str, int | float]:
    """Return runtime-tunable global topic ranking smoke thresholds."""
    return resolve_global_topic_ranking_threshold_metadata(overrides)["thresholds"]


def resolve_global_topic_ranking_threshold_metadata(
    overrides: Mapping[str, int | float | None] | None = None,
) -> dict[str, dict[str, int | float] | dict[str, str]]:
    """Resolve ranking thresholds and record where each value came from."""
    specs: dict[str, tuple[str, str, int | float, int | float, int | float]] = {
        "top_k": ("int", GLOBAL_TOPIC_RANKING_TOP_K_ENV, GLOBAL_TOPIC_RANKING_TOP_K, 1, 50),
        "min_overlap_ratio": (
            "float",
            GLOBAL_TOPIC_RANKING_MIN_OVERLAP_RATIO_ENV,
            GLOBAL_TOPIC_RANKING_MIN_OVERLAP_RATIO,
            0.0,
            1.0,
        ),
        "min_overlap_count": (
            "int",
            GLOBAL_TOPIC_RANKING_MIN_OVERLAP_COUNT_ENV,
            GLOBAL_TOPIC_RANKING_MIN_OVERLAP_COUNT,
            0,
            50,
        ),
        "max_rank_delta": (
            "int",
            GLOBAL_TOPIC_RANKING_MAX_RANK_DELTA_ENV,
            GLOBAL_TOPIC_RANKING_MAX_RANK_DELTA,
            0,
            50,
        ),
        "sample_limit": ("int", GLOBAL_TOPIC_RANKING_SAMPLE_LIMIT_ENV, GLOBAL_TOPIC_RANKING_SAMPLE_LIMIT, 1, 100),
    }
    provided = overrides or {}
    unknown = sorted(set(provided) - set(specs))
    if unknown:
        raise ValueError(f"Unknown global topic ranking threshold(s): {', '.join(unknown)}")

    thresholds: dict[str, int | float] = {}
    sources: dict[str, str] = {}
    for key, (value_type, env_name, default, min_value, max_value) in specs.items():
        override_value = provided.get(key)
        if override_value is not None:
            thresholds[key] = (
                _explicit_float_threshold(key, override_value, min_value=float(min_value), max_value=float(max_value))
                if value_type == "float"
                else _explicit_int_threshold(key, override_value, min_value=int(min_value), max_value=int(max_value))
            )
            sources[key] = "argument"
            continue
        value, source = _env_or_default_threshold(
            env_name=env_name,
            default=default,
            value_type=value_type,
            min_value=min_value,
            max_value=max_value,
        )
        thresholds[key] = value
        sources[key] = source
    return {"thresholds": thresholds, "sources": sources}


def resolve_global_topic_ranking_thresholds(
    overrides: Mapping[str, int | float | None] | None = None,
) -> dict[str, int | float]:
    """Resolve and validate global topic ranking smoke thresholds."""
    return _global_topic_ranking_thresholds(overrides)


def _env_or_default_threshold(
    *,
    env_name: str,
    default: int | float,
    value_type: str,
    min_value: int | float,
    max_value: int | float,
) -> tuple[int | float, str]:
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return default, "default"
    try:
        parsed: int | float = float(raw) if value_type == "float" else int(raw)
    except ValueError:
        return default, "default"
    if parsed < min_value:
        parsed = min_value
    if parsed > max_value:
        parsed = max_value
    if value_type == "int":
        parsed = int(parsed)
    return parsed, f"env:{env_name}"


def _explicit_int_threshold(
    name: str,
    value: int | float,
    *,
    min_value: int,
    max_value: int,
) -> int:
    parsed = int(value)
    if parsed != value:
        raise ValueError(f"{name} must be an integer")
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"{name} must be between {min_value} and {max_value}")
    return parsed


def _explicit_float_threshold(
    name: str,
    value: int | float,
    *,
    min_value: float,
    max_value: float,
) -> float:
    parsed = float(value)
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"{name} must be between {min_value:g} and {max_value:g}")
    return parsed


def find_release_manifest_path(root: Path, manifest_path: Path | str | None = None) -> Path | None:
    """Find the canonical release manifest for a root."""
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


def _read_index_metadata(index_path: Path, *, root: Path | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "index_present": index_path.exists(),
        "document_count": 0,
        "object_count": 0,
        "agent_index_schema_version": None,
        "index_generated_at": None,
        "index_layout": None,
        "index_layout_version": None,
        "company_shards_enabled": False,
        "index_shards_present": False,
        "shard_manifest_path": None,
        "global_catalog_path": None,
        "global_topics_path": None,
        "global_topics_present": False,
        "global_topic_count": 0,
        "company_shards_dir": None,
        "company_shard_count": 0,
        "index_shard_verification_ok": None,
        "artifact_manifest_path": None,
        "artifact_manifest_present": False,
        "artifact_manifest_sha256": None,
    }
    if not index_path.exists():
        return metadata
    metadata["index_sha256"] = _file_sha256(index_path)
    artifact_manifest_path = index_path.parent / "artifact_manifest.json"
    metadata["artifact_manifest_path"] = (
        _relative_or_absolute(artifact_manifest_path, root.resolve())
        if root is not None
        else str(artifact_manifest_path)
    )
    metadata["artifact_manifest_present"] = artifact_manifest_path.exists()
    if artifact_manifest_path.exists():
        metadata["artifact_manifest_sha256"] = _file_sha256(artifact_manifest_path)
    metadata["index_generated_at"] = datetime.fromtimestamp(
        index_path.stat().st_mtime,
        tz=timezone.utc,
    ).isoformat()
    try:
        with closing(sqlite3.connect(index_path)) as conn:
            metadata["document_count"] = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            metadata["object_count"] = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
            try:
                row = conn.execute(
                    "SELECT value FROM metadata WHERE key = 'build'"
                ).fetchone()
                if row:
                    build_metadata = json.loads(row[0])
                    metadata["agent_index_schema_version"] = (
                        build_metadata.get("agent_index_schema_version")
                        or build_metadata.get("schema_version")
                    )
                    metadata["index_layout"] = build_metadata.get("index_layout")
                    metadata["index_layout_version"] = build_metadata.get("index_layout_version")
                    metadata["company_shards_enabled"] = bool(build_metadata.get("company_shards_enabled"))
            except (json.JSONDecodeError, sqlite3.Error):
                pass
    except sqlite3.Error as exc:
        metadata["index_error"] = str(exc)
    shard_manifest_path = index_path.parent / "shard_manifest.json"
    if shard_manifest_path.exists():
        try:
            shard_manifest = json.loads(shard_manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            metadata["index_shard_error"] = str(exc)
        else:
            root_path = root.resolve() if root is not None else None
            global_catalog_path = index_path.parent / str(shard_manifest.get("global_catalog") or "global_catalog.sqlite")
            global_topics_path = index_path.parent / str(shard_manifest.get("global_topics") or "global_topics.sqlite")
            company_shards_dir = index_path.parent / str(shard_manifest.get("companies_dir") or "companies")
            metadata["index_shards_present"] = True
            metadata["shard_manifest_path"] = (
                _relative_or_absolute(shard_manifest_path, root_path)
                if root_path is not None
                else str(shard_manifest_path)
            )
            metadata["global_catalog_path"] = (
                _relative_or_absolute(global_catalog_path, root_path)
                if root_path is not None
                else str(global_catalog_path)
            )
            metadata["global_topics_path"] = (
                _relative_or_absolute(global_topics_path, root_path)
                if root_path is not None
                else str(global_topics_path)
            )
            metadata["global_topics_present"] = global_topics_path.exists()
            global_topic_counts = shard_manifest.get("global_topics_counts")
            if isinstance(global_topic_counts, dict):
                metadata["global_topic_count"] = int(global_topic_counts.get("company_topic_index") or 0)
            metadata["company_shards_dir"] = (
                _relative_or_absolute(company_shards_dir, root_path)
                if root_path is not None
                else str(company_shards_dir)
            )
            metadata["company_shard_count"] = int(shard_manifest.get("ticker_count") or 0)
            metadata["index_layout_version"] = shard_manifest.get("index_layout_version") or metadata["index_layout_version"]
            verification = shard_manifest.get("verification")
            if isinstance(verification, dict):
                metadata["index_shard_verification_ok"] = verification.get("ok")
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
    index_metadata = _read_index_metadata(resolved_index_path, root=root_path)
    index_outputs = _build_release_index_outputs(
        root_path,
        resolved_index_path,
        index_metadata=index_metadata,
    )
    manifest: dict[str, Any] = {
        "format": RELEASE_FORMAT,
        "release_id": release_id,
        "env": resolved_env,
        "status": "ready",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root_path),
        "source_root": str(Path(source_root).expanduser().resolve()) if source_root else str(root_path),
        "index_path": _relative_or_absolute(resolved_index_path, root_path),
        "index_abs_path": str(resolved_index_path),
        "indexes": index_outputs,
        "verification": {
            "status": "required",
            "path": f"{RELEASE_VERIFY_DIRNAME}/{RELEASE_VERIFY_REPORT_FILENAME}",
            "smoke_queries": f"{RELEASE_VERIFY_DIRNAME}/{RELEASE_SMOKE_QUERIES_FILENAME}",
            "ranking_quality": f"{RELEASE_VERIFY_DIRNAME}/{RELEASE_RANKING_QUALITY_FILENAME}",
        },
        **index_metadata,
    }
    return manifest


def _build_release_index_outputs(
    root_path: Path,
    index_path: Path,
    *,
    index_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    outputs: dict[str, Any] = {
        "monolith": {
            "path": _relative_or_absolute(index_path, root_path),
            "sha256": index_metadata.get("index_sha256"),
            "schema_version": index_metadata.get("agent_index_schema_version"),
            "required": True,
        },
    }
    if not index_metadata.get("index_shards_present"):
        return outputs

    shard_manifest_path = index_path.parent / "shard_manifest.json"
    try:
        shard_manifest = json.loads(shard_manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        shard_manifest = {}

    for role, metadata_key in (
        ("global_catalog", "global_catalog_path"),
        ("global_topics", "global_topics_path"),
    ):
        raw_path = index_metadata.get(metadata_key)
        if not isinstance(raw_path, str) or not raw_path:
            continue
        output_path = _resolve_release_relative_path(root_path, raw_path)
        outputs[role] = {
            "path": _relative_or_absolute(output_path, root_path),
            "sha256": _file_sha256(output_path) if output_path.is_file() else None,
            "required": True,
        }

    shard_entries = shard_manifest.get("shards")
    tickers: dict[str, Any] = {}
    if isinstance(shard_entries, Mapping):
        for ticker, raw_entry in sorted(shard_entries.items()):
            if not isinstance(raw_entry, Mapping):
                continue
            raw_path = raw_entry.get("path")
            if not isinstance(raw_path, str) or not raw_path:
                continue
            shard_path = index_path.parent / raw_path
            tickers[str(ticker)] = {
                "path": _relative_or_absolute(shard_path.resolve(), root_path),
                "sha256": raw_entry.get("sha256") or (_file_sha256(shard_path) if shard_path.is_file() else None),
                "document_count": raw_entry.get("document_count"),
                "object_count": raw_entry.get("object_count"),
                "edge_count": raw_entry.get("edge_count"),
                "quality_event_count": raw_entry.get("quality_event_count"),
            }
    outputs["company_shards"] = {
        "dir": index_metadata.get("company_shards_dir"),
        "count": len(tickers),
        "tickers": tickers,
        "required": True,
    }
    outputs["shard_manifest"] = {
        "path": _relative_or_absolute(shard_manifest_path, root_path),
        "sha256": _file_sha256(shard_manifest_path) if shard_manifest_path.is_file() else None,
        "required": True,
    }
    return outputs


def _resolve_release_relative_path(root_path: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    return candidate.expanduser().resolve() if candidate.is_absolute() else (root_path / candidate).resolve()


def write_release_manifest(
    root: Path | str,
    *,
    release_id: str,
    env: str | None = None,
    source_root: Path | str | None = None,
    index_path: Path | str | None = None,
) -> dict[str, Any]:
    """Write manifest.json for a release root."""
    root_path = Path(root).expanduser().resolve()
    _assert_not_active_release_path(root_path, "root")
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
    run_smoke: bool = False,
    ranking_thresholds: Mapping[str, int | float | None] | None = None,
) -> dict[str, Any]:
    """Verify release root invariants for worker/MCP use."""
    supplied_root = Path(root).expanduser()
    root_path = supplied_root.resolve()
    errors: list[str] = []
    if require_current_symlink and not _is_current_symlink_path(supplied_root):
        errors.append("current_symlink_required")
    errors.extend(_release_filesystem_errors(root_path))

    manifest, found_manifest_path = load_release_manifest(root_path, manifest_path=manifest_path)
    if not manifest:
        errors.append("manifest_missing")
    else:
        errors.extend(_release_manifest_errors(root_path, manifest))
        errors.extend(_release_manifest_index_output_errors(root_path, manifest))

    expected_env = normalize_ontology_env(env) if env is not None else None
    manifest_env = manifest.get("env")
    if expected_env is not None and manifest_env != expected_env:
        errors.append("manifest_env_mismatch")

    manifest_release_id = manifest.get("release_id")
    if manifest and not manifest_release_id:
        errors.append("manifest_release_id_missing")

    resolved_index_path = resolve_manifest_index_path(root_path, manifest, index_path=index_path)
    errors.extend(_release_manifest_index_digest_errors(manifest, resolved_index_path))
    errors.extend(_release_manifest_artifact_errors(root_path, manifest))
    index_verification: dict[str, Any] | None = None
    index_shard_verification: dict[str, Any] | None = None
    smoke_verification: dict[str, Any] | None = None
    if not resolved_index_path.exists():
        errors.append("index_missing")
    else:
        index_verification = verify_agent_index(resolved_index_path)
        errors.extend(index_verification["errors"])
        shard_layout_present = (
            bool(manifest.get("index_shards_present"))
            or (resolved_index_path.parent / "shard_manifest.json").exists()
            or (resolved_index_path.parent / "global_catalog.sqlite").exists()
            or (resolved_index_path.parent / "companies").exists()
        )
        if shard_layout_present:
            index_shard_verification = verify_index_shards(
                resolved_index_path.parent,
                monolith_index_path=resolved_index_path,
            )
            errors.extend(f"shards:{error}" for error in index_shard_verification["errors"])
        if run_smoke:
            if errors:
                smoke_verification = {
                    "ok": False,
                    "skipped": True,
                    "errors": [],
                    "warnings": ["verification_errors_before_smoke"],
                    "checks": [],
                }
            else:
                smoke_verification = _run_release_smoke_checks(
                    resolved_index_path,
                    ranking_thresholds=ranking_thresholds,
                )
                errors.extend(f"smoke:{error}" for error in smoke_verification["errors"])

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
        "index_verification": index_verification,
        "index_shard_verification": index_shard_verification,
        "smoke_verification": smoke_verification,
        "current_symlink": _is_current_symlink_path(supplied_root),
    }


def verify_release_startup(
    root: Path | str,
    *,
    env: str | None = None,
    manifest_path: Path | str | None = None,
    index_path: Path | str | None = None,
    require_current_symlink: bool = False,
    check_sqlite: bool = True,
) -> dict[str, Any]:
    """Verify only the cheap runtime contract needed before MCP startup.

    Deep release verification intentionally remains in ``verify_release_root``.
    Startup must not hash large SQLite files or run PRAGMA integrity_check.
    """
    supplied_root = Path(root).expanduser()
    root_path = supplied_root.resolve()
    errors: list[str] = []
    if require_current_symlink and not _is_current_symlink_path(supplied_root):
        errors.append("current_symlink_required")
    if not root_path.exists():
        errors.append("release_root_missing")
    elif not root_path.is_dir():
        errors.append("release_root_not_directory")

    manifest, found_manifest_path = load_release_manifest(root_path, manifest_path=manifest_path)
    if not manifest:
        errors.append("manifest_missing")
    else:
        errors.extend(_release_manifest_startup_errors(root_path, manifest))

    expected_env = normalize_ontology_env(env) if env is not None else None
    manifest_env = manifest.get("env")
    if expected_env is not None and manifest_env != expected_env:
        errors.append("manifest_env_mismatch")

    manifest_release_id = manifest.get("release_id")
    if manifest and not manifest_release_id:
        errors.append("manifest_release_id_missing")

    resolved_index_path = resolve_manifest_index_path(root_path, manifest, index_path=index_path)
    try:
        resolved_index_path.relative_to(root_path)
    except ValueError:
        errors.append("index_path_outside_root")
    index_verification: dict[str, Any] | None = None
    if not resolved_index_path.exists():
        errors.append("index_missing")
    elif not resolved_index_path.is_file():
        errors.append("index_not_file")
    elif check_sqlite and not errors:
        index_verification = _verify_agent_index_startup(resolved_index_path)
        errors.extend(index_verification["errors"])
    else:
        index_verification = {
            "ok": not errors,
            "errors": [],
            "index_path": str(resolved_index_path),
            "integrity_check": None,
            "counts": {},
            "verification_mode": "startup",
            "sqlite_checked": False,
            "skipped": bool(errors),
        }

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
        "index_verification": index_verification,
        "index_shard_verification": None,
        "smoke_verification": None,
        "current_symlink": _is_current_symlink_path(supplied_root),
        "verification_mode": "startup",
    }


def _release_manifest_startup_errors(root_path: Path, manifest: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("format") != RELEASE_FORMAT:
        errors.append("manifest_format_unsupported")
    if manifest.get("status") != "ready":
        errors.append("manifest_status_not_ready")
    release_id = manifest.get("release_id")
    if (
        isinstance(release_id, str)
        and root_path.parent.name in ALLOWED_ONTOLOGY_ENVS
        and root_path.name != release_id
    ):
        errors.append("manifest_release_id_directory_mismatch")
    if not manifest.get("agent_index_schema_version"):
        errors.append("manifest_agent_index_schema_version_missing")
    manifest_index_path = manifest.get("index_path")
    if not isinstance(manifest_index_path, str) or not manifest_index_path:
        errors.append("manifest_index_path_missing")
        return errors
    candidate = Path(manifest_index_path)
    if candidate.is_absolute():
        errors.append("manifest_index_path_not_relative")
        resolved = candidate.expanduser().resolve()
    else:
        resolved = (root_path / candidate).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError:
        errors.append("manifest_index_path_outside_root")
    outputs = manifest.get("indexes")
    if manifest.get("format") == RELEASE_FORMAT and not isinstance(outputs, Mapping):
        errors.append("manifest_indexes_missing")
    elif isinstance(outputs, Mapping):
        monolith = outputs.get("monolith")
        if not isinstance(monolith, Mapping):
            errors.append("manifest_indexes_monolith_missing")
        else:
            output_path = monolith.get("path")
            if not isinstance(output_path, str) or not output_path:
                errors.append("manifest_index_output_path_missing:monolith")
            elif output_path != manifest_index_path:
                errors.append("manifest_indexes_monolith_path_mismatch")
    return errors


def _verify_agent_index_startup(index_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    required_tables = {
        "metadata",
        "documents",
        "objects",
        "edges",
        "quality_events",
        "object_fts",
        "object_text",
        "object_search_text",
        "object_traceability",
        "metric_lookup",
        "metric_dimension_lookup",
        "company_dimension_catalog",
        "exposure_lookup",
        "agreement_lookup",
        "event_lookup",
        "factor_lookup",
        "company_topic_index",
        "company_topic_fts",
        "company_topic_source_objects",
    }
    try:
        with sqlite3.connect(f"{index_path.resolve().as_uri()}?mode=ro", uri=True) as conn:
            table_rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type IN ('table', 'view')
                """
            ).fetchall()
            existing_tables = {str(row[0]) for row in table_rows}
            for table_name in sorted(required_tables - existing_tables):
                errors.append(f"table_missing:{table_name}")
            if "metadata" in existing_tables:
                build_row = conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()
                if build_row is None:
                    errors.append("metadata_build_missing")
                else:
                    try:
                        build_metadata = json.loads(build_row[0])
                    except json.JSONDecodeError:
                        errors.append("metadata_build_invalid_json")
                    else:
                        schema_version = (
                            build_metadata.get("agent_index_schema_version")
                            or build_metadata.get("schema_version")
                        )
                        if not schema_version:
                            errors.append("agent_index_schema_version_missing")
    except sqlite3.Error as exc:
        errors.append(f"sqlite_error:{exc}")
    return {
        "ok": not errors,
        "errors": errors,
        "index_path": str(index_path),
        "integrity_check": None,
        "counts": {},
        "verification_mode": "startup",
    }


def _is_current_symlink_path(path: Path) -> bool:
    return path.name == "current" and path.is_symlink()


def _release_filesystem_errors(root_path: Path) -> list[str]:
    errors: list[str] = []
    if not root_path.exists():
        return ["release_root_missing"]
    if not root_path.is_dir():
        return ["release_root_not_directory"]
    if not (root_path / "companies").is_dir():
        errors.append("companies_dir_missing")
    if not (root_path / "indexes").is_dir():
        errors.append("indexes_dir_missing")
    for path in sorted(root_path.rglob("*"), key=lambda item: item.relative_to(root_path).as_posix()):
        relative = path.relative_to(root_path).as_posix()
        if path.is_symlink() and not path.exists():
            errors.append(f"broken_symlink:{relative}")
        if _is_release_temp_path(path):
            errors.append(f"release_temp_artifact:{relative}")
    return errors


def _is_release_temp_path(path: Path) -> bool:
    name = path.name
    if name == ".build" or name.endswith(".tmp") or name.endswith(".building"):
        return True
    return name.startswith(".") and ".tmp" in name


def _release_manifest_errors(root_path: Path, manifest: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("format") != RELEASE_FORMAT:
        errors.append("manifest_format_unsupported")
    if manifest.get("status") != "ready":
        errors.append("manifest_status_not_ready")
    release_id = manifest.get("release_id")
    if (
        isinstance(release_id, str)
        and root_path.parent.name in ALLOWED_ONTOLOGY_ENVS
        and root_path.name != release_id
    ):
        errors.append("manifest_release_id_directory_mismatch")
    if not manifest.get("agent_index_schema_version"):
        errors.append("manifest_agent_index_schema_version_missing")
    manifest_index_path = manifest.get("index_path")
    if not isinstance(manifest_index_path, str) or not manifest_index_path:
        errors.append("manifest_index_path_missing")
        return errors
    candidate = Path(manifest_index_path)
    if candidate.is_absolute():
        errors.append("manifest_index_path_not_relative")
        resolved = candidate.expanduser().resolve()
    else:
        resolved = (root_path / candidate).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError:
        errors.append("manifest_index_path_outside_root")
    return errors


def _release_manifest_index_output_errors(
    root_path: Path,
    manifest: Mapping[str, Any],
) -> list[str]:
    if manifest.get("format") != RELEASE_FORMAT:
        return []
    outputs = manifest.get("indexes")
    if not isinstance(outputs, Mapping):
        return ["manifest_indexes_missing"]

    errors: list[str] = []
    monolith = outputs.get("monolith")
    if not isinstance(monolith, Mapping):
        errors.append("manifest_indexes_monolith_missing")
    else:
        errors.extend(
            _release_manifest_output_file_errors(
                root_path,
                role="monolith",
                output=monolith,
                required=True,
            )
        )
        if monolith.get("path") != manifest.get("index_path"):
            errors.append("manifest_indexes_monolith_path_mismatch")
        if monolith.get("sha256") != manifest.get("index_sha256"):
            errors.append("manifest_indexes_monolith_sha256_mismatch")

    shard_layout = bool(manifest.get("index_shards_present"))
    for role in ("global_catalog", "global_topics", "shard_manifest"):
        output = outputs.get(role)
        if not isinstance(output, Mapping):
            if shard_layout:
                errors.append(f"manifest_indexes_{role}_missing")
            continue
        errors.extend(
            _release_manifest_output_file_errors(
                root_path,
                role=role,
                output=output,
                required=shard_layout,
            )
        )

    company_shards = outputs.get("company_shards")
    if shard_layout and not isinstance(company_shards, Mapping):
        errors.append("manifest_indexes_company_shards_missing")
        return errors
    if not isinstance(company_shards, Mapping):
        return errors
    tickers = company_shards.get("tickers")
    if not isinstance(tickers, Mapping):
        errors.append("manifest_indexes_company_shards_tickers_missing")
        return errors
    expected_count = company_shards.get("count")
    if not isinstance(expected_count, int) or expected_count != len(tickers):
        errors.append("manifest_indexes_company_shards_count_mismatch")
    flat_count = manifest.get("company_shard_count")
    if isinstance(flat_count, int) and flat_count != len(tickers):
        errors.append("manifest_company_shard_count_mismatch")
    raw_dir = company_shards.get("dir")
    if not isinstance(raw_dir, str) or not raw_dir:
        errors.append("manifest_indexes_company_shards_dir_missing")
    else:
        errors.extend(_release_manifest_relative_directory_errors(root_path, "company_shards", raw_dir))
    for ticker, output in sorted(tickers.items()):
        if not isinstance(output, Mapping):
            errors.append(f"manifest_indexes_company_shard_invalid:{ticker}")
            continue
        errors.extend(
            _release_manifest_output_file_errors(
                root_path,
                role=f"company_shard:{ticker}",
                output=output,
                required=True,
            )
        )
    return errors


def _release_manifest_output_file_errors(
    root_path: Path,
    *,
    role: str,
    output: Mapping[str, Any],
    required: bool,
) -> list[str]:
    errors: list[str] = []
    raw_path = output.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return [f"manifest_index_output_path_missing:{role}"] if required else []
    candidate = Path(raw_path)
    if candidate.is_absolute():
        errors.append(f"manifest_index_output_path_not_relative:{role}")
        resolved = candidate.expanduser().resolve()
    else:
        resolved = (root_path / candidate).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError:
        errors.append(f"manifest_index_output_path_outside_root:{role}")
    if not resolved.is_file():
        if required:
            errors.append(f"manifest_index_output_missing:{role}")
        return errors
    expected_sha256 = output.get("sha256")
    if not isinstance(expected_sha256, str) or not expected_sha256:
        errors.append(f"manifest_index_output_sha256_missing:{role}")
    elif _file_sha256(resolved) != expected_sha256:
        errors.append(f"manifest_index_output_sha256_mismatch:{role}")
    return errors


def _release_manifest_relative_directory_errors(
    root_path: Path,
    role: str,
    raw_path: str,
) -> list[str]:
    candidate = Path(raw_path)
    errors: list[str] = []
    if candidate.is_absolute():
        errors.append(f"manifest_index_output_path_not_relative:{role}")
        resolved = candidate.expanduser().resolve()
    else:
        resolved = (root_path / candidate).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError:
        errors.append(f"manifest_index_output_path_outside_root:{role}")
    if not resolved.is_dir():
        errors.append(f"manifest_index_output_missing:{role}")
    return errors


def _release_manifest_index_digest_errors(
    manifest: Mapping[str, Any],
    resolved_index_path: Path,
) -> list[str]:
    if not manifest:
        return []
    expected_sha256 = manifest.get("index_sha256")
    if not isinstance(expected_sha256, str) or not expected_sha256:
        return ["manifest_index_sha256_missing"]
    if not resolved_index_path.exists():
        return []
    actual_sha256 = _file_sha256(resolved_index_path)
    if actual_sha256 != expected_sha256:
        return ["manifest_index_sha256_mismatch"]
    return []


def _release_manifest_artifact_errors(
    root_path: Path,
    manifest: Mapping[str, Any],
) -> list[str]:
    if not manifest:
        return []
    raw_path = manifest.get("artifact_manifest_path")
    if not isinstance(raw_path, str) or not raw_path:
        return ["manifest_artifact_manifest_path_missing"]
    artifact_path = Path(raw_path)
    if artifact_path.is_absolute():
        errors = ["manifest_artifact_manifest_path_not_relative"]
        resolved = artifact_path.expanduser().resolve()
    else:
        errors = []
        resolved = (root_path / artifact_path).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError:
        errors.append("manifest_artifact_manifest_path_outside_root")
    if not resolved.exists():
        errors.append("artifact_manifest_missing")
        return errors
    expected_sha256 = manifest.get("artifact_manifest_sha256")
    if not isinstance(expected_sha256, str) or not expected_sha256:
        errors.append("manifest_artifact_manifest_sha256_missing")
    elif _file_sha256(resolved) != expected_sha256:
        errors.append("manifest_artifact_manifest_sha256_mismatch")
    return errors


def _run_release_smoke_checks(
    index_path: Path,
    *,
    ranking_thresholds: Mapping[str, int | float | None] | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    sample: dict[str, Any] = {}

    def record(name: str, status: str, **fields: Any) -> None:
        checks.append({"name": name, "status": status, **fields})

    def fail(name: str, code: str, exc: Exception | None = None, **fields: Any) -> None:
        detail = f"{code}:{exc}" if exc is not None else code
        errors.append(detail)
        record(name, "failed", error=detail, **fields)

    try:
        from krw_ontology.agent_index import open_ontology_store
    except Exception as exc:  # pragma: no cover - import failures are environment-specific
        fail("import_store_router", "store_router_import_failed", exc)
        return _smoke_payload(started_at, errors=errors, warnings=warnings, checks=checks, sample=sample)

    store = None
    try:
        store = open_ontology_store(index_path)
        record("open_store", "passed", store_type=type(store).__name__)
        routing_status = getattr(store, "routing_status", None)
        if callable(routing_status):
            try:
                sample["routing"] = routing_status()
                record("routing_status", "passed", mode=sample["routing"].get("mode"))
            except Exception as exc:
                fail("routing_status", "routing_status_failed", exc)

        try:
            context = store.index_context(
                include_counts=True,
                include_capabilities=True,
                include_quality_summary=False,
            )
            sample["serving_counts"] = context.get("serving_counts") or {}
            record("index_context", "passed")
        except Exception as exc:
            fail("index_context", "index_context_failed", exc)
            context = {}

        try:
            companies = store.list_companies()
            sample["companies"] = companies[:5]
            record("list_companies", "passed", count=len(companies))
        except Exception as exc:
            fail("list_companies", "list_companies_failed", exc)
            companies = []

        try:
            documents = store.list_documents()
            sample["document_count"] = len(documents)
            record("list_documents", "passed", count=len(documents))
        except Exception as exc:
            fail("list_documents", "list_documents_failed", exc)
            documents = []

        sample_ticker = _sample_ticker(companies, documents)
        if sample_ticker is None:
            warnings.append("no_ticker_documents_for_scoped_smoke")
            record("ticker_scoped_smoke", "skipped", warning="no_ticker_documents_for_scoped_smoke")
        else:
            sample["ticker"] = sample_ticker
            _run_ticker_smoke_checks(store, index_path, sample_ticker, checks, errors)

        compare_tickers = [ticker for ticker in companies[:2] if ticker]
        if len(compare_tickers) >= 2:
            try:
                payload = store.compare_compact(tickers=compare_tickers, topic="risk", limit_per_ticker=1)
                results = payload.get("results") if isinstance(payload.get("results"), dict) else {}
                record(
                    "compare_compact",
                    "passed",
                    tickers=compare_tickers,
                    mode=payload.get("mode"),
                    result_counts={
                        ticker: len(results.get(ticker) or [])
                        for ticker in compare_tickers
                    },
                )
            except Exception as exc:
                fail("compare_compact", "compare_compact_failed", exc)
        else:
            record("compare_compact", "skipped", warning="fewer_than_two_companies")
    except Exception as exc:
        fail("open_store", "open_store_failed", exc)
    finally:
        if store is not None:
            try:
                store.close()
            except Exception as exc:
                fail("close_store", "close_store_failed", exc)

    _run_global_topic_ranking_smoke_check(
        index_path,
        checks=checks,
        errors=errors,
        warnings=warnings,
        ranking_thresholds=ranking_thresholds,
    )
    _run_mcp_tool_smoke_checks(
        index_path,
        ticker=sample.get("ticker"),
        compare_tickers=compare_tickers if "compare_tickers" in locals() else [],
        object_id=_sample_object_id(index_path, str(sample.get("ticker"))) if sample.get("ticker") else None,
        checks=checks,
        errors=errors,
        warnings=warnings,
    )
    return _smoke_payload(started_at, errors=errors, warnings=warnings, checks=checks, sample=sample)


def _run_ticker_smoke_checks(
    store: Any,
    index_path: Path,
    ticker: str,
    checks: list[dict[str, Any]],
    errors: list[str],
) -> None:
    def record(name: str, status: str, **fields: Any) -> None:
        checks.append({"name": name, "status": status, **fields})

    def fail(name: str, code: str, exc: Exception) -> None:
        detail = f"{code}:{exc}"
        errors.append(detail)
        record(name, "failed", error=detail)

    try:
        documents = store.list_documents(ticker=ticker)
        record("list_documents_for_ticker", "passed", ticker=ticker, count=len(documents))
    except Exception as exc:
        fail("list_documents_for_ticker", "list_documents_for_ticker_failed", exc)

    try:
        rows, diagnostics = store.query_compact_with_diagnostics(tickers=[ticker], limit=1)
        record(
            "query_compact",
            "passed",
            ticker=ticker,
            result_count=len(rows),
            diagnostic_result_count=diagnostics.get("result_count"),
            top_ids=_row_ids(rows),
        )
    except Exception as exc:
        fail("query_compact", "query_compact_failed", exc)

    try:
        discovery = store.discover_company_topics(
            question="risk",
            tickers=[ticker],
            limit_groups=1,
            limit_per_group=1,
            limit=5,
        )
        record(
            "discover_company_topics",
            "passed",
            ticker=ticker,
            candidate_count=len(discovery.get("ticker_candidates") or []),
            top_candidates=[
                {
                    "ticker": candidate.get("ticker"),
                    "tier": candidate.get("tier"),
                    "matched_topic_count": candidate.get("matched_topic_count"),
                }
                for candidate in list(discovery.get("ticker_candidates") or [])[:5]
                if isinstance(candidate, dict)
            ],
        )
    except Exception as exc:
        fail("discover_company_topics", "discover_company_topics_failed", exc)

    object_id = _sample_object_id(index_path, ticker)
    if object_id is None:
        record("trace", "skipped", warning="no_object_for_trace_smoke", ticker=ticker)
        return
    try:
        trace_payload = store.trace(object_id)
        record("trace", "passed", ticker=ticker, object_id=object_id, found=trace_payload is not None)
    except Exception as exc:
        fail("trace", "trace_failed", exc)


def _sample_ticker(companies: list[str], documents: list[dict[str, Any]]) -> str | None:
    for document in documents:
        ticker = str(document.get("ticker") or "").upper()
        if ticker:
            return ticker
    for ticker in companies:
        normalized = str(ticker or "").upper()
        if normalized:
            return normalized
    return None


def _sample_object_id(index_path: Path, ticker: str) -> str | None:
    try:
        with closing(sqlite3.connect(index_path)) as conn:
            row = conn.execute(
                """
                SELECT id
                FROM objects
                WHERE ticker = ?
                ORDER BY id
                LIMIT 1
                """,
                (ticker,),
            ).fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row is not None and row[0] else None


def _row_ids(rows: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        object_id = row.get("id") or row.get("object_id")
        if object_id:
            ids.append(str(object_id))
    return ids


def _run_global_topic_ranking_smoke_check(
    index_path: Path,
    *,
    checks: list[dict[str, Any]],
    errors: list[str],
    warnings: list[str],
    ranking_thresholds: Mapping[str, int | float | None] | None = None,
) -> None:
    shard_manifest_path = index_path.parent / "shard_manifest.json"
    global_topics_path = index_path.parent / "global_topics.sqlite"

    def record(name: str, status: str, **fields: Any) -> None:
        checks.append({"name": name, "status": status, **fields})

    def fail(name: str, code: str, exc: Exception | None = None, **fields: Any) -> None:
        detail = f"{code}:{exc}" if exc is not None else code
        errors.append(detail)
        record(name, "failed", error=detail, **fields)

    if not shard_manifest_path.exists() or not global_topics_path.exists():
        record("global_topic_ranking", "skipped", warning="shard_global_topics_not_present")
        return

    threshold_metadata = resolve_global_topic_ranking_threshold_metadata(ranking_thresholds)
    thresholds = threshold_metadata["thresholds"]
    threshold_sources = threshold_metadata["sources"]
    samples = _sample_global_topic_questions(index_path, limit=int(thresholds["sample_limit"]))
    if not samples:
        warning = "no_global_topic_sample_question"
        warnings.append(warning)
        record("global_topic_ranking", "skipped", warning=warning)
        return

    try:
        from krw_ontology.agent_index import open_ontology_store
    except Exception as exc:  # pragma: no cover - import failures are environment-specific
        fail("global_topic_ranking", "store_router_import_failed", exc)
        return

    router_store = None
    monolith_store = None
    try:
        router_store = open_ontology_store(index_path, routing="auto")
        monolith_store = open_ontology_store(index_path, routing="monolith")
        sample_results = [
            _global_topic_ranking_sample_result(
                router_store,
                monolith_store,
                sample=sample,
                sample_index=sample_index,
                thresholds=thresholds,
            )
            for sample_index, sample in enumerate(samples)
        ]
        aggregate_payload = _aggregate_global_topic_ranking_samples(
            sample_results,
            thresholds=thresholds,
            threshold_sources=threshold_sources,
        )
        sample_errors = [
            str(sample.get("error"))
            for sample in sample_results
            if sample.get("status") == "failed" and sample.get("error")
        ]
        if sample_errors:
            error = sample_errors[0]
            errors.append(error)
            record("global_topic_ranking", "failed", error=error, **aggregate_payload)
            return
        record("global_topic_ranking", "passed", **aggregate_payload)
    except Exception as exc:
        fail("global_topic_ranking", "global_topic_ranking_failed", exc)
    finally:
        for store in (router_store, monolith_store):
            if store is None:
                continue
            try:
                store.close()
            except Exception:
                pass


def _global_topic_ranking_sample_result(
    router_store: Any,
    monolith_store: Any,
    *,
    sample: Mapping[str, Any],
    sample_index: int,
    thresholds: Mapping[str, int | float],
) -> dict[str, Any]:
    top_k = int(thresholds["top_k"])
    min_overlap_ratio = float(thresholds["min_overlap_ratio"])
    min_overlap_count = int(thresholds["min_overlap_count"])
    max_allowed_rank_delta = int(thresholds["max_rank_delta"])
    question = str(sample.get("question") or "").strip()
    try:
        router_payload = router_store.discover_company_topics(
            question=question,
            limit_groups=5,
            limit_per_group=2,
            limit=25,
        )
        monolith_payload = monolith_store.discover_company_topics(
            question=question,
            limit_groups=5,
            limit_per_group=2,
            limit=25,
        )
    except Exception as exc:
        return {
            "sample_index": sample_index,
            "sample_topic_id": sample.get("topic_id"),
            "sample_ticker": sample.get("ticker"),
            "question": question,
            "status": "failed",
            "error": f"global_topic_ranking_failed:{exc}",
            "top_k": top_k,
            "min_overlap_ratio": min_overlap_ratio,
            "max_allowed_rank_delta": max_allowed_rank_delta,
        }

    router_ids = _top_topic_ids(router_payload)[:top_k]
    monolith_ids = _top_topic_ids(monolith_payload)[:top_k]
    router_diagnostics = (
        router_payload.get("search_diagnostics")
        if isinstance(router_payload.get("search_diagnostics"), Mapping)
        else {}
    )
    monolith_diagnostics = (
        monolith_payload.get("search_diagnostics")
        if isinstance(monolith_payload.get("search_diagnostics"), Mapping)
        else {}
    )
    router_route = router_diagnostics.get("routing") if isinstance(router_diagnostics, Mapping) else None
    route_mode = router_route.get("mode") if isinstance(router_route, Mapping) else None
    router_fallback_used = _discovery_object_fallback_used(router_payload)
    monolith_fallback_used = _discovery_object_fallback_used(monolith_payload)
    overlap = [topic_id for topic_id in router_ids if topic_id in set(monolith_ids)]
    overlap_denominator = min(len(monolith_ids), top_k)
    overlap_ratio = (len(overlap) / overlap_denominator) if overlap_denominator else 1.0
    rank_deltas = _topic_rank_deltas(router_ids, monolith_ids, overlap)
    max_rank_delta = max(rank_deltas) if rank_deltas else 0
    required_overlap_count = min(min_overlap_count, overlap_denominator)
    payload = {
        "sample_index": sample_index,
        "sample_topic_id": sample.get("topic_id"),
        "sample_ticker": sample.get("ticker"),
        "question": question,
        "route_mode": route_mode,
        "top_k": top_k,
        "router_fts_strategy": router_diagnostics.get("fts_strategy"),
        "monolith_fts_strategy": monolith_diagnostics.get("fts_strategy"),
        "router_fallback_used": router_fallback_used,
        "monolith_fallback_used": monolith_fallback_used,
        "monolith_top_topic_ids": monolith_ids,
        "router_top_topic_ids": router_ids,
        "top_topic_ids": router_ids,
        "overlap_topic_ids": overlap,
        "overlap_count": len(overlap),
        "overlap_ratio": round(overlap_ratio, 4),
        "min_overlap_count": required_overlap_count,
        "min_overlap_ratio": min_overlap_ratio,
        "rank_deltas": rank_deltas,
        "max_rank_delta": max_rank_delta,
        "max_allowed_rank_delta": max_allowed_rank_delta,
        "router_candidate_count": len(router_payload.get("ticker_candidates") or []),
        "monolith_candidate_count": len(monolith_payload.get("ticker_candidates") or []),
    }
    error: str | None = None
    if route_mode != "global_topics":
        error = f"global_topic_route_unexpected:{route_mode}"
    elif router_fallback_used:
        error = "global_topic_router_used_object_fallback"
    elif monolith_ids and not router_ids:
        error = "global_topic_router_returned_no_candidates"
    elif monolith_ids and (len(overlap) < required_overlap_count or overlap_ratio < min_overlap_ratio):
        error = "global_topic_topk_overlap_below_threshold"
    elif max_rank_delta > max_allowed_rank_delta:
        error = "global_topic_rank_delta_above_threshold"
    if error:
        return {"status": "failed", "error": error, **payload}
    return {"status": "passed", **payload}


def _aggregate_global_topic_ranking_samples(
    samples: list[dict[str, Any]],
    *,
    thresholds: Mapping[str, int | float],
    threshold_sources: Mapping[str, str],
) -> dict[str, Any]:
    primary = samples[0] if samples else {}
    route_modes = sorted({str(sample["route_mode"]) for sample in samples if sample.get("route_mode")})
    overlap_ratios = [
        float(sample["overlap_ratio"])
        for sample in samples
        if isinstance(sample.get("overlap_ratio"), int | float)
    ]
    overlap_counts = [
        int(sample["overlap_count"])
        for sample in samples
        if isinstance(sample.get("overlap_count"), int)
    ]
    rank_deltas = [
        int(delta)
        for sample in samples
        for delta in sample.get("rank_deltas", [])
        if isinstance(delta, int)
    ]
    sample_errors = [
        str(sample.get("error"))
        for sample in samples
        if sample.get("status") == "failed" and sample.get("error")
    ]
    return {
        "question": primary.get("question"),
        "route_mode": route_modes[0] if len(route_modes) == 1 else "mixed",
        "top_k": int(thresholds["top_k"]),
        "threshold_sources": dict(sorted(threshold_sources.items())),
        "sample_limit": int(thresholds["sample_limit"]),
        "sample_count": len(samples),
        "passed_sample_count": sum(1 for sample in samples if sample.get("status") == "passed"),
        "failed_sample_count": len(sample_errors),
        "sample_errors": sample_errors,
        "router_fts_strategy": primary.get("router_fts_strategy"),
        "monolith_fts_strategy": primary.get("monolith_fts_strategy"),
        "router_fallback_used": any(bool(sample.get("router_fallback_used")) for sample in samples),
        "monolith_fallback_used": any(bool(sample.get("monolith_fallback_used")) for sample in samples),
        "monolith_top_topic_ids": primary.get("monolith_top_topic_ids") or [],
        "router_top_topic_ids": primary.get("router_top_topic_ids") or [],
        "top_topic_ids": primary.get("top_topic_ids") or [],
        "overlap_topic_ids": primary.get("overlap_topic_ids") or [],
        "overlap_count": min(overlap_counts) if overlap_counts else 0,
        "overlap_ratio": min(overlap_ratios) if overlap_ratios else None,
        "min_overlap_count": primary.get("min_overlap_count", int(thresholds["min_overlap_count"])),
        "min_overlap_ratio": float(thresholds["min_overlap_ratio"]),
        "rank_deltas": rank_deltas,
        "max_rank_delta": max(rank_deltas) if rank_deltas else 0,
        "max_allowed_rank_delta": int(thresholds["max_rank_delta"]),
        "router_candidate_count": primary.get("router_candidate_count", 0),
        "monolith_candidate_count": primary.get("monolith_candidate_count", 0),
        "min_overlap_ratio_observed": min(overlap_ratios) if overlap_ratios else None,
        "max_rank_delta_observed": max(rank_deltas) if rank_deltas else None,
        "samples": samples,
    }


def _sample_global_topic_question(index_path: Path) -> str | None:
    samples = _sample_global_topic_questions(index_path, limit=1)
    if not samples:
        return None
    return str(samples[0].get("question") or "")


def _sample_global_topic_questions(index_path: Path, *, limit: int) -> list[dict[str, Any]]:
    try:
        with closing(sqlite3.connect(index_path)) as conn:
            rows = conn.execute(
                """
                SELECT topic_id, topic_label, topic_summary, ticker
                FROM company_topic_index
                WHERE COALESCE(boilerplate_score, 0) < 1
                ORDER BY ticker, topic_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    except sqlite3.Error:
        return []
    samples: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    for topic_id, topic_label, topic_summary, ticker in rows:
        question = ""
        for value in (topic_label, topic_summary, ticker):
            text = str(value or "").strip()
            if text:
                question = text
                break
        if not question or question in seen_questions:
            continue
        seen_questions.add(question)
        samples.append(
            {
                "topic_id": str(topic_id or ""),
                "ticker": str(ticker or "").upper(),
                "question": question,
            }
        )
    return samples


def _top_topic_ids(payload: Mapping[str, Any]) -> list[str]:
    topic_ids: list[str] = []
    for candidate in payload.get("ticker_candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        for topic in candidate.get("matched_topics") or []:
            if not isinstance(topic, Mapping):
                continue
            topic_id = topic.get("topic_id")
            if topic_id and str(topic_id) not in topic_ids:
                topic_ids.append(str(topic_id))
    if topic_ids:
        return topic_ids
    results_by_ticker = payload.get("results_by_ticker")
    if isinstance(results_by_ticker, Mapping):
        for rows in results_by_ticker.values():
            if not isinstance(rows, list):
                continue
            for topic in rows:
                if not isinstance(topic, Mapping):
                    continue
                topic_id = topic.get("topic_id")
                if topic_id and str(topic_id) not in topic_ids:
                    topic_ids.append(str(topic_id))
    return topic_ids


def _discovery_object_fallback_used(payload: Mapping[str, Any]) -> bool:
    diagnostics = payload.get("search_diagnostics")
    return isinstance(diagnostics, Mapping) and bool(diagnostics.get("object_fallback"))


def _topic_rank_deltas(
    router_ids: Sequence[str],
    monolith_ids: Sequence[str],
    overlap_ids: Sequence[str],
) -> list[int]:
    router_rank = {topic_id: rank for rank, topic_id in enumerate(router_ids)}
    monolith_rank = {topic_id: rank for rank, topic_id in enumerate(monolith_ids)}
    return [
        abs(router_rank[topic_id] - monolith_rank[topic_id])
        for topic_id in overlap_ids
        if topic_id in router_rank and topic_id in monolith_rank
    ]


def _run_mcp_tool_smoke_checks(
    index_path: Path,
    *,
    ticker: str | None,
    compare_tickers: list[str],
    object_id: str | None,
    checks: list[dict[str, Any]],
    errors: list[str],
    warnings: list[str],
) -> None:
    """Run MCP tool wrapper smoke checks, not just SDK/store calls."""
    release_root = index_path.parent.parent

    def record(name: str, status: str, **fields: Any) -> None:
        checks.append({"name": name, "status": status, **fields})

    def fail(name: str, code: str, exc: Exception | None = None) -> None:
        detail = f"{code}:{exc}" if exc is not None else code
        errors.append(detail)
        record(name, "failed", error=detail)

    def call_tool(name: str, func: Any, *, required_keys: Sequence[str] = (), **kwargs: Any) -> dict[str, Any] | None:
        try:
            raw = func(
                root=str(release_root),
                index_path=str(index_path),
                **kwargs,
            )
            payload = json.loads(raw)
        except Exception as exc:
            fail(name, f"{name}_failed", exc)
            return None
        if not isinstance(payload, dict):
            fail(name, f"{name}_non_object_response")
            return None
        missing_keys = [key for key in required_keys if key not in payload]
        if missing_keys:
            fail(name, f"{name}_missing_keys:{','.join(missing_keys)}")
            return None
        record(
            name,
            "passed",
            tool=name.removeprefix("mcp_"),
            response_keys=sorted(str(key) for key in payload.keys())[:20],
            result_count=_mcp_payload_result_count(payload),
            company_count=_mcp_payload_company_count(payload),
            document_count=_mcp_payload_document_count(payload),
        )
        return payload

    try:
        from krw_ontology.mcp_server import tools as mcp_tools
    except Exception as exc:  # pragma: no cover - import failures are environment-specific
        fail("mcp_import_tools", "mcp_tools_import_failed", exc)
        return

    try:
        mcp_tools.reset_mcp_runtime_caches()
        call_tool(
            "mcp_index_context",
            mcp_tools.index_context_tool,
            required_keys=("index_status", "index_context_guard"),
            include_counts=False,
            include_capabilities=True,
            include_quality_summary=False,
            allow_expensive=False,
            response_format=mcp_tools.ResponseFormat.JSON,
        )
        call_tool(
            "mcp_catalog",
            mcp_tools.catalog_tool,
            required_keys=("companies", "documents", "pagination"),
            limit=5,
            response_format=mcp_tools.ResponseFormat.JSON,
        )

        if not ticker:
            warning = "no_ticker_documents_for_mcp_tool_smoke"
            warnings.append(warning)
            record("mcp_ticker_tools", "skipped", warning=warning)
            return

        call_tool(
            "mcp_query",
            mcp_tools.query_tool,
            required_keys=("query", "pagination"),
            topic="risk",
            tickers=[ticker],
            limit=1,
            response_format=mcp_tools.ResponseFormat.JSON,
            response_detail=mcp_tools.ResponseDetail.COMPACT,
        )
        call_tool(
            "mcp_query_context",
            mcp_tools.query_context_tool,
            required_keys=("research_status", "answerability"),
            question=f"{ticker} risk",
            ticker=ticker,
            limit_results=1,
            limit_tickers=5,
            response_format=mcp_tools.ResponseFormat.JSON,
        )
        call_tool(
            "mcp_topic_map",
            mcp_tools.topic_map_tool,
            required_keys=("ticker",),
            ticker=ticker,
            limit=5,
            response_format=mcp_tools.ResponseFormat.JSON,
        )
        call_tool(
            "mcp_quality",
            mcp_tools.quality_tool,
            required_keys=("summary", "events", "pagination"),
            ticker=ticker,
            limit=5,
            response_format=mcp_tools.ResponseFormat.JSON,
        )
        call_tool(
            "mcp_retrieve",
            mcp_tools.retrieve_tool,
            required_keys=("question", "query", "answerability"),
            question=f"{ticker} risk",
            ticker=ticker,
            limit=1,
            response_format=mcp_tools.ResponseFormat.JSON,
            response_detail=mcp_tools.ResponseDetail.COMPACT,
        )
        if object_id:
            call_tool(
                "mcp_trace",
                mcp_tools.trace_tool,
                required_keys=("object",),
                object_id=object_id,
                response_format=mcp_tools.ResponseFormat.JSON,
            )
        else:
            record("mcp_trace", "skipped", warning="no_object_for_mcp_trace_smoke", ticker=ticker)

        if len(compare_tickers) >= 2:
            call_tool(
                "mcp_compare",
                mcp_tools.compare_tool,
                required_keys=("results", "comparison_rows"),
                tickers=compare_tickers,
                topic="risk",
                limit_per_ticker=1,
                response_format=mcp_tools.ResponseFormat.JSON,
                response_detail=mcp_tools.ResponseDetail.COMPACT,
            )
        else:
            record("mcp_compare", "skipped", warning="fewer_than_two_companies")
    finally:
        mcp_tools.reset_mcp_runtime_caches()


def _mcp_payload_result_count(payload: Mapping[str, Any]) -> int | None:
    results = payload.get("results")
    if isinstance(results, list):
        return len(results)
    if isinstance(results, Mapping):
        return sum(len(rows) for rows in results.values() if isinstance(rows, list))
    candidates = payload.get("ticker_candidates")
    if isinstance(candidates, list):
        return len(candidates)
    events = payload.get("events")
    if isinstance(events, list):
        return len(events)
    return None


def _mcp_payload_company_count(payload: Mapping[str, Any]) -> int | None:
    companies = payload.get("companies")
    return len(companies) if isinstance(companies, list) else None


def _mcp_payload_document_count(payload: Mapping[str, Any]) -> int | None:
    documents = payload.get("documents")
    if isinstance(documents, list):
        return len(documents)
    count = payload.get("document_count")
    return int(count) if isinstance(count, int) else None


def _smoke_payload(
    started_at: datetime,
    *,
    errors: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
    sample: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": not errors,
        "skipped": False,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "sample": sample,
        "elapsed_ms": int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
    }


def write_release_verification_report(
    root: Path | str,
    *,
    env: str | None = None,
    manifest_path: Path | str | None = None,
    index_path: Path | str | None = None,
    require_current_symlink: bool = False,
    run_smoke: bool = True,
    smoke_baseline_path: Path | str | None = None,
    update_smoke_baseline: bool = False,
    ranking_thresholds: Mapping[str, int | float | None] | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a deterministic verification report for an immutable release root."""
    supplied_root = Path(root).expanduser()
    root_path = supplied_root.resolve()
    _assert_not_active_release_path(root_path, "root")
    if not root_path.is_dir():
        raise FileNotFoundError(f"Release root not found: {root_path}")
    if verification is None or (run_smoke and verification.get("smoke_verification") is None):
        verification_payload = verify_release_root(
            supplied_root,
            env=env,
            manifest_path=manifest_path,
            index_path=index_path,
            require_current_symlink=require_current_symlink,
            run_smoke=run_smoke,
            ranking_thresholds=ranking_thresholds,
        )
    else:
        verification_payload = verification
    verification_payload = {
        **verification_payload,
        "errors": list(verification_payload.get("errors") or []),
    }
    file_trace = _release_file_trace(root_path)
    reproducibility_hash = _release_reproducibility_hash(
        release_id=verification_payload.get("release_id"),
        env=verification_payload.get("env"),
        files=file_trace,
    )
    smoke_queries_report = _write_release_smoke_queries_report(root_path, verification_payload)
    smoke_baseline = _compare_or_update_smoke_baseline(
        root_path,
        smoke_queries_report=smoke_queries_report,
        smoke_baseline_path=smoke_baseline_path,
        update_smoke_baseline=update_smoke_baseline,
    )
    if smoke_baseline is not None and not smoke_baseline["ok"]:
        verification_payload["errors"].extend(smoke_baseline["errors"])
        verification_payload["ok"] = False
    ranking_quality_report = _write_release_ranking_quality_report(root_path, verification_payload)
    report_path = root_path / RELEASE_VERIFY_DIRNAME / RELEASE_VERIFY_REPORT_FILENAME
    report = {
        "format": RELEASE_VERIFY_REPORT_FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root_path),
        "supplied_root": str(supplied_root),
        "ok": bool(verification_payload.get("ok")),
        "errors": list(verification_payload.get("errors") or []),
        "env": verification_payload.get("env"),
        "release_id": verification_payload.get("release_id"),
        "manifest_path": _relative_or_absolute(
            Path(str(verification_payload["manifest_path"])).expanduser().resolve(),
            root_path,
        )
        if verification_payload.get("manifest_path")
        else None,
        "index_path": _relative_or_absolute(
            Path(str(verification_payload["index_path"])).expanduser().resolve(),
            root_path,
        )
        if verification_payload.get("index_path")
        else None,
        "reproducibility_hash": reproducibility_hash,
        "smoke_queries_path": _relative_or_absolute(
            Path(smoke_queries_report["path"]),
            root_path,
        )
        if smoke_queries_report is not None
        else None,
        "smoke_queries_hash": smoke_queries_report.get("smoke_hash") if smoke_queries_report else None,
        "ranking_quality_path": _relative_or_absolute(
            Path(ranking_quality_report["path"]),
            root_path,
        )
        if ranking_quality_report is not None
        else None,
        "ranking_quality_hash": ranking_quality_report.get("ranking_quality_hash")
        if ranking_quality_report
        else None,
        "smoke_baseline": smoke_baseline,
        "file_count": len(file_trace),
        "files": file_trace,
        "verification": verification_payload,
    }
    _write_json_atomic(report_path, report)
    return {**report, "path": str(report_path)}


def _write_release_smoke_queries_report(
    root_path: Path,
    verification_payload: dict[str, Any],
) -> dict[str, Any] | None:
    smoke = verification_payload.get("smoke_verification")
    if not isinstance(smoke, dict):
        return None
    payload = _release_smoke_queries_payload(verification_payload, smoke)
    path = root_path / RELEASE_VERIFY_DIRNAME / RELEASE_SMOKE_QUERIES_FILENAME
    _write_json_atomic(path, payload)
    return {**payload, "path": str(path)}


def _write_release_ranking_quality_report(
    root_path: Path,
    verification_payload: dict[str, Any],
) -> dict[str, Any] | None:
    smoke = verification_payload.get("smoke_verification")
    if not isinstance(smoke, dict):
        return None
    payload = _release_ranking_quality_payload(verification_payload, smoke)
    path = root_path / RELEASE_VERIFY_DIRNAME / RELEASE_RANKING_QUALITY_FILENAME
    _write_json_atomic(path, payload)
    return {**payload, "path": str(path)}


def _compare_or_update_smoke_baseline(
    root_path: Path,
    *,
    smoke_queries_report: dict[str, Any] | None,
    smoke_baseline_path: Path | str | None,
    update_smoke_baseline: bool,
) -> dict[str, Any] | None:
    if smoke_queries_report is None:
        return None
    baseline_path = _resolve_smoke_baseline_path(
        root_path,
        smoke_baseline_path=smoke_baseline_path,
        update_smoke_baseline=update_smoke_baseline,
    )
    if baseline_path is None:
        return None
    baseline_payload = _smoke_baseline_payload(smoke_queries_report)
    if update_smoke_baseline:
        _write_json_atomic(baseline_path, baseline_payload)
        return {
            "ok": True,
            "mode": "updated",
            "path": str(baseline_path),
            "expected_hash": baseline_payload["smoke_hash"],
            "actual_hash": smoke_queries_report["smoke_hash"],
            "errors": [],
        }
    if not baseline_path.exists():
        return {
            "ok": False,
            "mode": "missing",
            "path": str(baseline_path),
            "expected_hash": None,
            "actual_hash": smoke_queries_report["smoke_hash"],
            "errors": [f"smoke_baseline_missing:{baseline_path}"],
        }
    try:
        existing = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "mode": "invalid",
            "path": str(baseline_path),
            "expected_hash": None,
            "actual_hash": smoke_queries_report["smoke_hash"],
            "errors": [f"smoke_baseline_invalid:{exc}"],
        }
    expected_hash = existing.get("smoke_hash") if isinstance(existing, dict) else None
    actual_hash = smoke_queries_report["smoke_hash"]
    if expected_hash != actual_hash:
        return {
            "ok": False,
            "mode": "compared",
            "path": str(baseline_path),
            "expected_hash": expected_hash,
            "actual_hash": actual_hash,
            "errors": [f"smoke_baseline_mismatch:{expected_hash}!={actual_hash}"],
        }
    return {
        "ok": True,
        "mode": "compared",
        "path": str(baseline_path),
        "expected_hash": expected_hash,
        "actual_hash": actual_hash,
        "errors": [],
    }


def _resolve_smoke_baseline_path(
    root_path: Path,
    *,
    smoke_baseline_path: Path | str | None,
    update_smoke_baseline: bool,
) -> Path | None:
    if smoke_baseline_path is not None:
        candidate = Path(smoke_baseline_path).expanduser()
        return candidate if candidate.is_absolute() else root_path / candidate
    default_path = root_path / RELEASE_VERIFY_DIRNAME / RELEASE_SMOKE_BASELINE_FILENAME
    if update_smoke_baseline or default_path.exists():
        return default_path
    return None


def _smoke_baseline_payload(smoke_queries_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": RELEASE_SMOKE_BASELINE_FORMAT,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source_release_id": smoke_queries_report.get("release_id"),
        "source_env": smoke_queries_report.get("env"),
        "smoke_hash": smoke_queries_report["smoke_hash"],
        "ok": smoke_queries_report.get("ok"),
        "errors": list(smoke_queries_report.get("errors") or []),
        "warnings": list(smoke_queries_report.get("warnings") or []),
        "checks": smoke_queries_report.get("checks") or [],
        "sample": smoke_queries_report.get("sample") or {},
    }


def _release_smoke_queries_payload(
    verification_payload: dict[str, Any],
    smoke: dict[str, Any],
) -> dict[str, Any]:
    checks = [
        _normalize_smoke_check(check)
        for check in smoke.get("checks") or []
        if isinstance(check, dict)
    ]
    sample = _normalize_smoke_sample(smoke.get("sample") if isinstance(smoke.get("sample"), dict) else {})
    comparable = {
        "ok": bool(smoke.get("ok")),
        "errors": list(smoke.get("errors") or []),
        "warnings": list(smoke.get("warnings") or []),
        "checks": checks,
        "sample": sample,
    }
    smoke_hash = hashlib.sha256(
        json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "format": RELEASE_SMOKE_QUERIES_FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_id": verification_payload.get("release_id"),
        "env": verification_payload.get("env"),
        "index_path": verification_payload.get("index_path"),
        "smoke_hash": smoke_hash,
        **comparable,
    }


def _release_ranking_quality_payload(
    verification_payload: dict[str, Any],
    smoke: dict[str, Any],
) -> dict[str, Any]:
    checks = [
        _normalize_ranking_quality_check(check)
        for check in smoke.get("checks") or []
        if isinstance(check, dict) and check.get("name") == "global_topic_ranking"
    ]
    errors = [
        str(check.get("error"))
        for check in checks
        if check.get("status") == "failed" and check.get("error")
    ]
    warnings = [
        str(check.get("warning"))
        for check in checks
        if check.get("status") == "skipped" and check.get("warning")
    ]
    comparable = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": _ranking_quality_summary(checks),
        "checks": checks,
    }
    ranking_quality_hash = hashlib.sha256(
        json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "format": RELEASE_RANKING_QUALITY_FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_id": verification_payload.get("release_id"),
        "env": verification_payload.get("env"),
        "index_path": verification_payload.get("index_path"),
        "ranking_quality_hash": ranking_quality_hash,
        **comparable,
    }


def calibrate_global_topic_ranking_thresholds(
    report_paths: Sequence[Path | str],
    *,
    overlap_margin: float = 0.0,
    rank_delta_margin: int = 0,
    min_reports: int = 1,
) -> dict[str, Any]:
    """Build a threshold recommendation from ranking quality reports."""
    if min_reports < 1:
        raise ValueError("min_reports must be at least 1")
    if overlap_margin < 0 or overlap_margin > 1:
        raise ValueError("overlap_margin must be between 0 and 1")
    if rank_delta_margin < 0:
        raise ValueError("rank_delta_margin must be non-negative")

    inputs: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    for raw_path in report_paths:
        path = Path(raw_path).expanduser()
        input_info: dict[str, Any] = {
            "path": str(path.resolve()),
            "sha256": None,
            "release_id": None,
            "env": None,
            "ranking_quality_hash": None,
            "ok": None,
            "observation_count": 0,
        }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            input_info["sha256"] = _file_sha256(path)
        except FileNotFoundError:
            errors.append(f"ranking_quality_missing:{path}")
            inputs.append(input_info)
            continue
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"ranking_quality_invalid:{path}:{exc}")
            inputs.append(input_info)
            continue
        if payload.get("format") != RELEASE_RANKING_QUALITY_FORMAT:
            errors.append(f"ranking_quality_format_mismatch:{path}")
            inputs.append(input_info)
            continue
        input_info.update(
            {
                "release_id": payload.get("release_id"),
                "env": payload.get("env"),
                "ranking_quality_hash": payload.get("ranking_quality_hash"),
                "ok": bool(payload.get("ok")),
            }
        )
        if not payload.get("ok"):
            warnings.append(f"ranking_quality_not_ok:{path}")
        report_observations = _ranking_quality_observations(payload)
        input_info["observation_count"] = len(report_observations)
        observations.extend(report_observations)
        inputs.append(input_info)

    valid_report_count = sum(1 for item in inputs if item.get("sha256") and item.get("ok"))
    if valid_report_count < min_reports:
        errors.append(f"ranking_calibration_min_reports_not_met:{valid_report_count}<{min_reports}")
    if not observations:
        errors.append("ranking_calibration_no_observations")

    recommendation = _ranking_threshold_recommendation(
        observations,
        overlap_margin=overlap_margin,
        rank_delta_margin=rank_delta_margin,
        warnings=warnings,
    )
    comparable = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "inputs": inputs,
        "observation_count": len(observations),
        "observed": _ranking_observation_summary(observations),
        "recommendation": recommendation,
        "parameters": {
            "overlap_margin": overlap_margin,
            "rank_delta_margin": rank_delta_margin,
            "min_reports": min_reports,
        },
    }
    calibration_hash = hashlib.sha256(
        json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "format": RELEASE_RANKING_THRESHOLD_CALIBRATION_FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "calibration_hash": calibration_hash,
        **comparable,
    }


def write_ranking_threshold_calibration_report(
    path: Path | str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Write a ranking threshold calibration report."""
    output = Path(path).expanduser()
    _write_json_atomic(output, dict(payload))
    return {**payload, "path": str(output.resolve())}


def _ranking_quality_observations(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for check in payload.get("checks") or []:
        if not isinstance(check, Mapping) or check.get("name") != "global_topic_ranking":
            continue
        sample_items = check.get("samples") if isinstance(check.get("samples"), list) else [check]
        for sample in sample_items:
            if not isinstance(sample, Mapping) or sample.get("status") != "passed":
                continue
            overlap_ratio = sample.get("overlap_ratio")
            overlap_count = sample.get("overlap_count")
            max_rank_delta = sample.get("max_rank_delta")
            if not isinstance(overlap_ratio, int | float):
                continue
            if not isinstance(overlap_count, int):
                continue
            if not isinstance(max_rank_delta, int):
                continue
            observations.append(
                {
                    "release_id": payload.get("release_id"),
                    "env": payload.get("env"),
                    "question": sample.get("question"),
                    "sample_topic_id": sample.get("sample_topic_id"),
                    "sample_ticker": sample.get("sample_ticker"),
                    "overlap_ratio": float(overlap_ratio),
                    "overlap_count": overlap_count,
                    "max_rank_delta": max_rank_delta,
                    "top_k": sample.get("top_k") if isinstance(sample.get("top_k"), int) else check.get("top_k"),
                }
            )
    return observations


def _ranking_threshold_recommendation(
    observations: list[dict[str, Any]],
    *,
    overlap_margin: float,
    rank_delta_margin: int,
    warnings: list[str],
) -> dict[str, Any]:
    if not observations:
        return {
            "top_k": GLOBAL_TOPIC_RANKING_TOP_K,
            "min_overlap_ratio": GLOBAL_TOPIC_RANKING_MIN_OVERLAP_RATIO,
            "min_overlap_count": GLOBAL_TOPIC_RANKING_MIN_OVERLAP_COUNT,
            "max_rank_delta": GLOBAL_TOPIC_RANKING_MAX_RANK_DELTA,
            "sample_limit": GLOBAL_TOPIC_RANKING_SAMPLE_LIMIT,
        }
    overlap_ratios = [float(item["overlap_ratio"]) for item in observations]
    overlap_counts = [int(item["overlap_count"]) for item in observations]
    rank_deltas = [int(item["max_rank_delta"]) for item in observations]
    top_k_values = sorted({int(item["top_k"]) for item in observations if isinstance(item.get("top_k"), int)})
    if len(top_k_values) > 1:
        warnings.append(f"ranking_calibration_mixed_top_k:{','.join(str(value) for value in top_k_values)}")
    return {
        "top_k": top_k_values[-1] if top_k_values else GLOBAL_TOPIC_RANKING_TOP_K,
        "min_overlap_ratio": round(max(0.0, min(overlap_ratios) - overlap_margin), 4),
        "min_overlap_count": max(0, min(overlap_counts)),
        "max_rank_delta": min(50, max(rank_deltas) + rank_delta_margin),
        "sample_limit": len(observations),
    }


def _ranking_observation_summary(observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not observations:
        return {
            "min_overlap_ratio": None,
            "min_overlap_count": None,
            "max_rank_delta": None,
            "top_k_values": [],
        }
    return {
        "min_overlap_ratio": min(float(item["overlap_ratio"]) for item in observations),
        "min_overlap_count": min(int(item["overlap_count"]) for item in observations),
        "max_rank_delta": max(int(item["max_rank_delta"]) for item in observations),
        "top_k_values": sorted({int(item["top_k"]) for item in observations if isinstance(item.get("top_k"), int)}),
    }


def _normalize_ranking_quality_check(check: dict[str, Any]) -> dict[str, Any]:
    stable_keys = (
        "name",
        "status",
        "question",
        "sample_index",
        "sample_topic_id",
        "sample_ticker",
        "sample_limit",
        "sample_count",
        "passed_sample_count",
        "failed_sample_count",
        "sample_errors",
        "samples",
        "route_mode",
        "top_k",
        "threshold_sources",
        "router_fts_strategy",
        "monolith_fts_strategy",
        "router_fallback_used",
        "monolith_fallback_used",
        "monolith_top_topic_ids",
        "router_top_topic_ids",
        "top_topic_ids",
        "overlap_topic_ids",
        "overlap_count",
        "overlap_ratio",
        "min_overlap_ratio_observed",
        "min_overlap_count",
        "min_overlap_ratio",
        "rank_deltas",
        "max_rank_delta",
        "max_rank_delta_observed",
        "max_allowed_rank_delta",
        "router_candidate_count",
        "monolith_candidate_count",
        "warning",
        "error",
    )
    return {
        key: check[key]
        for key in stable_keys
        if key in check
    }


def _ranking_quality_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    route_modes: set[str] = set()
    overlap_ratios: list[float] = []
    rank_deltas: list[int] = []
    thresholds: dict[str, Any] = {}
    threshold_sources: dict[str, str] = {}
    router_fallback_count = 0
    monolith_fallback_count = 0
    sample_count = 0
    failed_sample_count = 0

    for check in checks:
        status = str(check.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if isinstance(check.get("sample_count"), int):
            sample_count += int(check["sample_count"])
        if isinstance(check.get("failed_sample_count"), int):
            failed_sample_count += int(check["failed_sample_count"])
        route_mode = check.get("route_mode")
        if route_mode:
            route_modes.add(str(route_mode))
        if check.get("router_fallback_used"):
            router_fallback_count += 1
        if check.get("monolith_fallback_used"):
            monolith_fallback_count += 1
        overlap_ratio = check.get("overlap_ratio")
        if isinstance(overlap_ratio, int | float):
            overlap_ratios.append(float(overlap_ratio))
        max_rank_delta = check.get("max_rank_delta")
        if isinstance(max_rank_delta, int):
            rank_deltas.append(max_rank_delta)
        for key in (
            "top_k",
            "min_overlap_count",
            "min_overlap_ratio",
            "max_allowed_rank_delta",
            "sample_limit",
        ):
            if key in check and key not in thresholds:
                thresholds[key] = check[key]
        if isinstance(check.get("threshold_sources"), Mapping):
            for key, source in check["threshold_sources"].items():
                if key not in threshold_sources:
                    threshold_sources[str(key)] = str(source)

    return {
        "check_count": len(checks),
        "sample_count": sample_count,
        "failed_sample_count": failed_sample_count,
        "status_counts": dict(sorted(status_counts.items())),
        "route_modes": sorted(route_modes),
        "router_fallback_count": router_fallback_count,
        "monolith_fallback_count": monolith_fallback_count,
        "min_overlap_ratio_observed": min(overlap_ratios) if overlap_ratios else None,
        "max_rank_delta_observed": max(rank_deltas) if rank_deltas else None,
        "thresholds": thresholds,
        "threshold_sources": dict(sorted(threshold_sources.items())),
    }


def _normalize_smoke_check(check: dict[str, Any]) -> dict[str, Any]:
    stable_keys = (
        "name",
        "status",
        "tool",
        "store_type",
        "mode",
        "route_mode",
        "top_k",
        "threshold_sources",
        "router_fts_strategy",
        "monolith_fts_strategy",
        "router_fallback_used",
        "monolith_fallback_used",
        "count",
        "company_count",
        "document_count",
        "question",
        "sample_index",
        "sample_topic_id",
        "sample_ticker",
        "sample_limit",
        "sample_count",
        "passed_sample_count",
        "failed_sample_count",
        "sample_errors",
        "samples",
        "ticker",
        "tickers",
        "result_count",
        "diagnostic_result_count",
        "candidate_count",
        "response_keys",
        "top_ids",
        "top_topic_ids",
        "monolith_top_topic_ids",
        "router_top_topic_ids",
        "overlap_count",
        "overlap_ratio",
        "min_overlap_ratio_observed",
        "overlap_topic_ids",
        "min_overlap_count",
        "min_overlap_ratio",
        "rank_deltas",
        "max_rank_delta",
        "max_rank_delta_observed",
        "max_allowed_rank_delta",
        "router_candidate_count",
        "monolith_candidate_count",
        "top_candidates",
        "result_counts",
        "object_id",
        "found",
        "warning",
        "error",
    )
    return {
        key: check[key]
        for key in stable_keys
        if key in check
    }


def _normalize_smoke_sample(sample: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in ("companies", "document_count", "ticker", "serving_counts"):
        if key in sample:
            normalized[key] = sample[key]
    routing = sample.get("routing")
    if isinstance(routing, dict):
        normalized["routing"] = {
            key: routing[key]
            for key in (
                "mode",
                "ticker_count",
                "monolith_open",
                "global_topics_open",
            )
            if key in routing
        }
    return normalized


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _release_file_trace(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not root.exists():
        return entries
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative_path = path.relative_to(root)
        if relative_path.parts and relative_path.parts[0] == RELEASE_VERIFY_DIRNAME:
            continue
        if path.is_symlink():
            entries.append(
                {
                    "path": relative_path.as_posix(),
                    "role": _release_file_role(relative_path),
                    "kind": "symlink",
                    "target": os.readlink(path),
                }
            )
            continue
        if not path.is_file():
            continue
        stat = path.stat()
        entries.append(
            {
                "path": relative_path.as_posix(),
                "role": _release_file_role(relative_path),
                "kind": "file",
                "size_bytes": stat.st_size,
                "sha256": _file_sha256(path),
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return entries


def _release_file_role(relative_path: Path) -> str:
    parts = relative_path.parts
    path_text = relative_path.as_posix()
    if path_text == RELEASE_MANIFEST_FILENAME:
        return "release_manifest"
    if path_text == DEFAULT_INDEX_RELATIVE_PATH.as_posix():
        return "agent_index"
    if path_text == "indexes/shard_manifest.json":
        return "shard_manifest"
    if path_text in {"indexes/global_catalog.sqlite", "indexes/global_topics.sqlite"}:
        return "global_index"
    if len(parts) >= 3 and parts[0] == "indexes" and parts[1] == "companies" and relative_path.suffix == ".sqlite":
        return "company_shard"
    if parts and parts[0] == "indexes":
        return "index_artifact"
    if parts and parts[0] == "companies":
        return "ontology_artifact"
    return "release_artifact"


def _release_reproducibility_hash(
    *,
    release_id: Any,
    env: Any,
    files: list[dict[str, Any]],
) -> str:
    deterministic_files: list[dict[str, Any]] = []
    for entry in files:
        deterministic_entry = {
            key: entry[key]
            for key in ("path", "role", "kind", "size_bytes", "sha256", "target")
            if key in entry
        }
        deterministic_files.append(deterministic_entry)
    payload = {
        "format": RELEASE_FORMAT,
        "release_id": str(release_id or ""),
        "env": str(env or ""),
        "files": deterministic_files,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_env_root(releases_root: Path | str, env: str | None = None) -> Path:
    """Return the env-specific release directory under a releases root."""
    resolved_env = normalize_ontology_env(env)
    root = Path(releases_root).expanduser()
    return root if root.name == resolved_env else root / resolved_env


def _assert_not_active_release_path(path: Path, label: str) -> None:
    if _path_points_at_active_release(path):
        raise ValueError(
            f"Refusing {label}={path}: current is an immutable release pointer. "
            "Build a candidate release and promote it instead."
        )


def _path_points_at_active_release(path: Path) -> bool:
    expanded = path.expanduser().absolute()
    for candidate in (expanded, *expanded.parents):
        if candidate.name == "current" and candidate.parent.name in ALLOWED_ONTOLOGY_ENVS:
            return True
        env_root = candidate.parent
        if env_root.name not in ALLOWED_ONTOLOGY_ENVS:
            continue
        current = env_root / "current"
        try:
            if current.is_symlink() and candidate.exists() and candidate.resolve() == current.resolve():
                return True
        except OSError:
            pass
    return False


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
    dirs = [
        path
        for path in env_root.iterdir()
        if path.is_dir()
        and path.name not in RELEASE_ENV_RESERVED_DIRNAMES
        and not path.name.startswith(".")
    ]
    return [path.name for path in sorted(dirs, key=lambda path: path.stat().st_mtime, reverse=True)]


def quarantine_local_release(
    releases_root: Path | str,
    *,
    env: str | None,
    release_path: Path | str,
    action: str,
    error: str,
) -> dict[str, Any] | None:
    """Move a failed local candidate out of the promotable release namespace."""
    source = Path(release_path).expanduser()
    if not source.exists() and not source.is_symlink():
        return None
    env_root = release_env_root(releases_root, env).expanduser()
    failed_root = env_root / FAILED_RELEASE_DIRNAME
    failed_root.mkdir(parents=True, exist_ok=True)
    target = failed_root / source.name
    if target.exists() or target.is_symlink():
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = failed_root / f"{source.name}-{suffix}"
    shutil.move(str(source), str(target))
    payload = {
        "format": FAILED_RELEASE_FORMAT,
        "env": normalize_ontology_env(env),
        "release_id": source.name,
        "action": action,
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "error": error,
        "original_path": str(source),
        "quarantine_path": str(target),
    }
    _write_json_atomic(target / FAILED_RELEASE_METADATA_FILENAME, payload)
    return {**payload, "path": str(target)}


def promote_local_release(
    releases_root: Path | str,
    *,
    env: str | None,
    release_id: str,
    action: str = "promote",
) -> dict[str, Any]:
    """Atomically point an env current symlink to a release id."""
    env_root = release_env_root(releases_root, env)
    previous_release_id = current_release_id(env_root)
    if previous_release_id == release_id:
        raise ValueError(f"Release {release_id} is already current")
    release_dir = env_root / release_id
    if not release_dir.is_dir():
        raise FileNotFoundError(f"Release directory not found: {release_dir}")
    event_log_paths = _release_event_log_paths(env_root=env_root, release_dir=release_dir)
    activated_before = event_log_paths["release_event_log"].exists()
    verification = verify_release_root(release_dir, env=env, run_smoke=True)
    if not verification["ok"]:
        if not activated_before:
            write_release_verification_report(
                release_dir,
                env=env,
                verification=verification,
            )
        raise ValueError(f"Release verification failed: {', '.join(verification['errors'])}")
    verify_report = (
        _load_existing_release_verification_report(release_dir)
        if activated_before
        else write_release_verification_report(
            release_dir,
            env=env,
            verification=verification,
        )
    )
    if not verify_report["ok"]:
        raise ValueError(f"Release verification report failed: {', '.join(verify_report['errors'])}")
    env_root.mkdir(parents=True, exist_ok=True)
    current = env_root / "current"
    _prepare_release_event_log_paths(event_log_paths)
    next_link = env_root / "current.next"
    if next_link.exists() or next_link.is_symlink():
        next_link.unlink()
    next_link.symlink_to(release_id)
    os.replace(next_link, current)
    event_logs = _write_release_changed_events(
        env_root=env_root,
        release_dir=release_dir,
        current=current,
        env=normalize_ontology_env(env),
        release_id=release_id,
        previous_release_id=previous_release_id,
        action=action,
        verification=verification,
        event_log_paths=event_log_paths,
    )
    return {
        "env_root": str(env_root.resolve()),
        "env": normalize_ontology_env(env),
        "release_id": release_id,
        "previous_release_id": previous_release_id,
        "current": str(current),
        "event_log": event_logs["env_event_log"],
        "release_event_log": event_logs["release_event_log"],
        "event_log_errors": event_logs["event_log_errors"],
        "verify_report": verify_report["path"],
    }


def _load_existing_release_verification_report(release_dir: Path) -> dict[str, Any]:
    required = {
        "release_verify": release_dir / RELEASE_VERIFY_DIRNAME / RELEASE_VERIFY_REPORT_FILENAME,
        "smoke_queries": release_dir / RELEASE_VERIFY_DIRNAME / RELEASE_SMOKE_QUERIES_FILENAME,
        "ranking_quality": release_dir / RELEASE_VERIFY_DIRNAME / RELEASE_RANKING_QUALITY_FILENAME,
    }
    payloads: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for label, path in required.items():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            errors.append(f"{label}_missing")
            continue
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{label}_invalid:{exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{label}_not_object")
            continue
        if payload.get("ok") is not True:
            errors.append(f"{label}_not_ok")
        payloads[label] = payload
    if errors:
        raise ValueError(
            "Previously activated release verification artifacts invalid: "
            + ", ".join(errors)
        )
    return {
        **payloads["release_verify"],
        "path": str(required["release_verify"]),
        "errors": [],
        "ok": True,
    }


def _release_event_log_paths(*, env_root: Path, release_dir: Path) -> dict[str, Path]:
    return {
        "env_event_log": env_root / RELEASE_EVENTS_FILENAME,
        "release_event_log": env_root / RELEASE_EVENTS_DIRNAME / f"{release_dir.name}.jsonl",
    }


def _prepare_release_event_log_paths(paths: Mapping[str, Path]) -> None:
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass


def _write_release_changed_events(
    *,
    env_root: Path,
    release_dir: Path,
    current: Path,
    env: str,
    release_id: str,
    previous_release_id: str | None,
    action: str,
    verification: Mapping[str, Any],
    event_log_paths: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    payload = {
        "format": RELEASE_EVENT_FORMAT,
        "event": "release_changed",
        "action": action,
        "env": env,
        "release_id": release_id,
        "previous_release_id": previous_release_id,
        "current": str(current),
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "verification_ok": bool(verification.get("ok")),
        "verification_errors": list(verification.get("errors") or []),
        "manifest_path": verification.get("manifest_path"),
        "index_path": verification.get("index_path"),
    }
    paths = event_log_paths or _release_event_log_paths(env_root=env_root, release_dir=release_dir)
    env_event_log = paths["env_event_log"]
    release_event_log = paths["release_event_log"]
    event_log_errors: list[str] = []
    for label, path in paths.items():
        try:
            _append_release_event(path, payload)
        except OSError as exc:
            event_log_errors.append(f"{label}:{path}:{exc}")
    return {
        "env_event_log": str(env_event_log),
        "release_event_log": str(release_event_log),
        "event_log_errors": event_log_errors,
    }


def _append_release_event(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), sort_keys=True) + "\n")


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
    return promote_local_release(env_root, env=env, release_id=target_id, action="rollback")
