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

from krw_ontology.agent_index.spine_schema import (
    GLOBAL_SPINE_LAYOUT,
    GLOBAL_SPINE_RELATIVE_PATH,
    GLOBAL_SPINE_SCHEMA_VERSION,
    read_global_spine_metadata,
)
from krw_ontology.agent_index.spine_verify import verify_spine_shard_release
from krw_ontology.config.paths import (
    ONTOLOGY_ENV_ENV,
    ONTOLOGY_MANIFEST_PATH_ENV,
)

RELEASE_FORMAT_V3 = "krw-ontology-release/v3"
RELEASE_FORMAT = RELEASE_FORMAT_V3
RELEASE_MANIFEST_FILENAME = "manifest.json"
RELEASE_VERIFY_REPORT_FORMAT = "krw-ontology-release-verify/v1"
RELEASE_EVENT_FORMAT = "krw-ontology-release-event/v1"
RELEASE_VERIFY_DIRNAME = "verify"
RELEASE_VERIFY_REPORT_FILENAME = "release_verify.json"
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
ALLOWED_ONTOLOGY_ENVS = {"dev", "staging", "prod"}


def normalize_ontology_env(env: str | None = None) -> str:
    """Return a normalized ontology env name."""
    raw = (env or os.environ.get(ONTOLOGY_ENV_ENV) or "dev").strip().lower()
    if raw not in ALLOWED_ONTOLOGY_ENVS:
        allowed = ", ".join(sorted(ALLOWED_ONTOLOGY_ENVS))
        raise ValueError(f"KRW ontology env must be one of {allowed}; got {raw!r}")
    return raw


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


def build_release_manifest_v3(
    root: Path | str,
    *,
    release_id: str,
    env: str | None = None,
    source_root: Path | str | None = None,
    global_spine_path: Path | str | None = None,
    shard_manifest_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build a v3 release manifest for global spine + company shards."""
    root_path = Path(root).expanduser().resolve()
    resolved_env = normalize_ontology_env(env)
    resolved_spine_path = (
        Path(global_spine_path).expanduser().resolve()
        if global_spine_path is not None
        else root_path / GLOBAL_SPINE_RELATIVE_PATH
    )
    resolved_shard_manifest_path = (
        Path(shard_manifest_path).expanduser().resolve()
        if shard_manifest_path is not None
        else root_path / "indexes" / "shard_manifest.json"
    )
    index_outputs = _build_release_index_outputs_v3(
        root_path,
        global_spine_path=resolved_spine_path,
        shard_manifest_path=resolved_shard_manifest_path,
    )
    spine_counts = index_outputs["global_spine"].get("counts") or {}
    return {
        "format": RELEASE_FORMAT_V3,
        "release_id": release_id,
        "env": resolved_env,
        "status": "ready",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root_path),
        "source_root": str(Path(source_root).expanduser().resolve()) if source_root else str(root_path),
        "index_layout": GLOBAL_SPINE_LAYOUT,
        "monolith_required": False,
        "global_spine_path": _relative_or_absolute(resolved_spine_path, root_path),
        "indexes": index_outputs,
        "verification": {
            "status": "required",
            "path": f"{RELEASE_VERIFY_DIRNAME}/{RELEASE_VERIFY_REPORT_FILENAME}",
            "consistency_report": f"{RELEASE_VERIFY_DIRNAME}/consistency_report.json",
            "chain_smoke": f"{RELEASE_VERIFY_DIRNAME}/chain_smoke.json",
            "quality_report": f"{RELEASE_VERIFY_DIRNAME}/quality_report.json",
            "routing_report": f"{RELEASE_VERIFY_DIRNAME}/routing_report.json",
        },
        "builder": {
            "release_builder_version": "release-builder/v3",
            "spine_schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
        },
        "global_object_count": spine_counts.get("global_object_locator", 0),
        "global_edge_count": spine_counts.get("global_edge_spine", 0),
        "global_chain_link_count": spine_counts.get("global_chain_index", 0),
        "company_shard_count": index_outputs["company_shards"].get("count", 0),
    }


def write_release_manifest_v3(
    root: Path | str,
    *,
    release_id: str,
    env: str | None = None,
    source_root: Path | str | None = None,
    global_spine_path: Path | str | None = None,
    shard_manifest_path: Path | str | None = None,
) -> dict[str, Any]:
    """Write a v3 manifest.json for a global spine + company shards release."""
    root_path = Path(root).expanduser().resolve()
    _assert_not_active_release_path(root_path, "root")
    root_path.mkdir(parents=True, exist_ok=True)
    manifest = build_release_manifest_v3(
        root_path,
        release_id=release_id,
        env=env,
        source_root=source_root,
        global_spine_path=global_spine_path,
        shard_manifest_path=shard_manifest_path,
    )
    _write_json_atomic(root_path / RELEASE_MANIFEST_FILENAME, manifest)
    return manifest


def _build_release_index_outputs_v3(
    root_path: Path,
    *,
    global_spine_path: Path,
    shard_manifest_path: Path,
) -> dict[str, Any]:
    spine_counts = _sqlite_counts_if_present(
        global_spine_path,
        (
            "global_object_locator",
            "global_document_catalog",
            "global_edge_spine",
            "global_factor_spine",
            "global_topic_spine",
            "global_metric_spine",
            "global_entity_spine",
            "global_counterparty_spine",
            "global_chain_index",
        ),
    )
    shard_entries = _read_v3_shard_entries(root_path, shard_manifest_path)
    return {
        "global_spine": {
            "path": _relative_or_absolute(global_spine_path, root_path),
            "sha256": _file_sha256(global_spine_path) if global_spine_path.is_file() else None,
            "schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
            "required": True,
            "counts": spine_counts,
        },
        "company_shards": {
            "dir": _relative_or_absolute(root_path / "indexes" / "companies", root_path),
            "required": True,
            "count": len(shard_entries),
            "tickers": shard_entries,
        },
        "shard_manifest": {
            "path": _relative_or_absolute(shard_manifest_path, root_path),
            "sha256": _file_sha256(shard_manifest_path) if shard_manifest_path.is_file() else None,
            "required": True,
        },
    }


def _read_v3_shard_entries(root_path: Path, shard_manifest_path: Path) -> dict[str, Any]:
    if not shard_manifest_path.is_file():
        return {}
    try:
        payload = json.loads(shard_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    raw_shards = payload.get("shards")
    if not isinstance(raw_shards, Mapping):
        return {}
    entries: dict[str, Any] = {}
    for ticker, raw_entry in sorted(raw_shards.items()):
        if not isinstance(raw_entry, Mapping):
            continue
        raw_path = raw_entry.get("path") or raw_entry.get("shard_path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        raw_candidate = Path(raw_path)
        if raw_candidate.is_absolute():
            shard_path = raw_candidate.expanduser().resolve()
        elif raw_candidate.parts and raw_candidate.parts[0] == "indexes":
            shard_path = (root_path / raw_candidate).resolve()
        else:
            shard_path = (root_path / "indexes" / raw_candidate).resolve()
        entries[str(ticker)] = {
            "path": _relative_or_absolute(shard_path, root_path),
            "sha256": raw_entry.get("sha256") or (_file_sha256(shard_path) if shard_path.is_file() else None),
            "schema_version": raw_entry.get("schema_version"),
            "document_count": raw_entry.get("document_count"),
            "object_count": raw_entry.get("object_count"),
            "edge_count": raw_entry.get("edge_count"),
            "quality_event_count": raw_entry.get("quality_event_count"),
        }
    return entries


def _sqlite_counts_if_present(path: Path, tables: Sequence[str]) -> dict[str, int]:
    if not path.is_file():
        return {table: 0 for table in tables}
    counts: dict[str, int] = {}
    try:
        with closing(sqlite3.connect(path)) as conn:
            existing = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                ).fetchall()
            }
            for table in tables:
                if table not in existing:
                    counts[table] = 0
                    continue
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return {table: 0 for table in tables}
    return counts


def verify_release_root(
    root: Path | str,
    *,
    env: str | None = None,
    manifest_path: Path | str | None = None,
    require_current_symlink: bool = False,
) -> dict[str, Any]:
    """Deep-verify a v3 global spine + company shard release."""
    supplied_root = Path(root).expanduser()
    root_path = supplied_root.resolve()
    manifest, found_manifest_path = load_release_manifest(root_path, manifest_path=manifest_path)
    if manifest.get("format") != RELEASE_FORMAT_V3:
        errors = _release_filesystem_errors(root_path)
        if require_current_symlink and not _is_current_symlink_path(supplied_root):
            errors.insert(0, "current_symlink_required")
        errors.append("manifest_missing" if not manifest else "manifest_format_unsupported")
        return {
            "ok": False,
            "errors": errors,
            "root": str(root_path),
            "supplied_root": str(supplied_root),
            "manifest_path": str(found_manifest_path) if found_manifest_path else None,
            "manifest": manifest,
            "env": manifest.get("env"),
            "release_id": manifest.get("release_id"),
            "index_layout": manifest.get("index_layout"),
            "global_spine_path": None,
            "global_spine_present": False,
            "global_spine_verification": None,
            "spine_shard_verification": None,
            "smoke_verification": None,
            "current_symlink": _is_current_symlink_path(supplied_root),
            "verification_mode": "release-root-v3",
        }
    return _verify_release_root_v3(
        supplied_root=supplied_root,
        root_path=root_path,
        manifest=manifest,
        found_manifest_path=found_manifest_path,
        env=env,
        require_current_symlink=require_current_symlink,
    )


def _verify_release_root_v3(
    *,
    supplied_root: Path,
    root_path: Path,
    manifest: dict[str, Any],
    found_manifest_path: Path | None,
    env: str | None,
    require_current_symlink: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    if require_current_symlink and not _is_current_symlink_path(supplied_root):
        errors.append("current_symlink_required")
    errors.extend(_release_filesystem_errors(root_path))
    errors.extend(_release_manifest_startup_errors_v3(root_path, manifest))
    expected_env = normalize_ontology_env(env) if env is not None else None
    manifest_env = manifest.get("env")
    if expected_env is not None and manifest_env != expected_env:
        errors.append("manifest_env_mismatch")
    spine_shard_verification = verify_spine_shard_release(
        root_path,
        manifest_path=found_manifest_path,
    )
    errors.extend(f"spine_shard:{error}" for error in spine_shard_verification["errors"])
    global_spine_path = _resolve_v3_global_spine_path(root_path, manifest)
    return {
        "ok": not errors,
        "errors": errors,
        "root": str(root_path),
        "supplied_root": str(supplied_root),
        "manifest_path": str(found_manifest_path) if found_manifest_path else None,
        "manifest": manifest,
        "env": manifest_env,
        "release_id": manifest.get("release_id"),
        "index_layout": manifest.get("index_layout"),
        "global_spine_path": str(global_spine_path),
        "global_spine_present": global_spine_path.exists(),
        "global_spine_verification": spine_shard_verification.get("global_spine_verification"),
        "spine_shard_verification": spine_shard_verification,
        "smoke_verification": None,
        "current_symlink": _is_current_symlink_path(supplied_root),
        "verification_mode": "release-root-v3",
    }


def verify_release_startup_v3(
    root: Path | str,
    *,
    env: str | None = None,
    manifest_path: Path | str | None = None,
    require_current_symlink: bool = False,
    check_sqlite: bool = True,
) -> dict[str, Any]:
    """Verify the cheap v3 runtime contract before MCP startup."""
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
        errors.extend(_release_manifest_startup_errors_v3(root_path, manifest))

    expected_env = normalize_ontology_env(env) if env is not None else None
    manifest_env = manifest.get("env")
    if expected_env is not None and manifest_env != expected_env:
        errors.append("manifest_env_mismatch")

    global_spine_path = _resolve_v3_global_spine_path(root_path, manifest)
    spine_verification: dict[str, Any] | None = None
    if not global_spine_path.exists():
        errors.append("global_spine_missing")
    elif not global_spine_path.is_file():
        errors.append("global_spine_not_file")
    elif check_sqlite:
        spine_verification = _verify_global_spine_startup(global_spine_path)
        errors.extend(spine_verification["errors"])
    else:
        spine_verification = {
            "ok": True,
            "errors": [],
            "path": str(global_spine_path),
            "sqlite_checked": False,
            "verification_mode": "startup",
        }

    return {
        "ok": not errors,
        "errors": errors,
        "root": str(root_path),
        "supplied_root": str(supplied_root),
        "manifest_path": str(found_manifest_path) if found_manifest_path else None,
        "manifest": manifest,
        "env": manifest_env,
        "release_id": manifest.get("release_id"),
        "index_layout": manifest.get("index_layout"),
        "global_spine_path": str(global_spine_path),
        "global_spine_present": global_spine_path.exists(),
        "global_spine_verification": spine_verification,
        "current_symlink": _is_current_symlink_path(supplied_root),
        "verification_mode": "startup-v3",
    }


def _release_manifest_startup_errors_v3(root_path: Path, manifest: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("format") != RELEASE_FORMAT_V3:
        errors.append("manifest_format_unsupported")
    if manifest.get("status") != "ready":
        errors.append("manifest_status_not_ready")
    if manifest.get("index_layout") != GLOBAL_SPINE_LAYOUT:
        errors.append("manifest_index_layout_unsupported")
    if manifest.get("monolith_required") is not False:
        errors.append("manifest_monolith_required_not_false")
    release_id = manifest.get("release_id")
    if not release_id:
        errors.append("manifest_release_id_missing")
    elif (
        isinstance(release_id, str)
        and root_path.parent.name in ALLOWED_ONTOLOGY_ENVS
        and root_path.name != release_id
    ):
        errors.append("manifest_release_id_directory_mismatch")
    outputs = manifest.get("indexes")
    if not isinstance(outputs, Mapping):
        errors.append("manifest_indexes_missing")
        return errors
    for role in ("global_spine", "company_shards", "shard_manifest"):
        output = outputs.get(role)
        if not isinstance(output, Mapping):
            errors.append(f"manifest_indexes_{role}_missing")
            continue
        if output.get("required") is not True:
            errors.append(f"manifest_indexes_{role}_not_required")
    global_spine = outputs.get("global_spine")
    if isinstance(global_spine, Mapping):
        raw_path = global_spine.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            errors.append("manifest_indexes_global_spine_path_missing")
        else:
            errors.extend(_release_manifest_relative_file_startup_errors(root_path, "global_spine", raw_path))
    company_shards = outputs.get("company_shards")
    if isinstance(company_shards, Mapping):
        raw_dir = company_shards.get("dir")
        if not isinstance(raw_dir, str) or not raw_dir:
            errors.append("manifest_indexes_company_shards_dir_missing")
        else:
            errors.extend(_release_manifest_relative_directory_errors(root_path, "company_shards", raw_dir))
    shard_manifest = outputs.get("shard_manifest")
    if isinstance(shard_manifest, Mapping):
        raw_path = shard_manifest.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            errors.append("manifest_indexes_shard_manifest_path_missing")
        else:
            errors.extend(_release_manifest_relative_file_startup_errors(root_path, "shard_manifest", raw_path))
    debug_monolith = outputs.get("debug_monolith")
    if isinstance(debug_monolith, Mapping) and debug_monolith.get("required") is True:
        errors.append("manifest_debug_monolith_required")
    return errors


def _release_manifest_relative_file_startup_errors(root_path: Path, role: str, raw_path: str) -> list[str]:
    errors: list[str] = []
    candidate = Path(raw_path)
    if candidate.is_absolute():
        errors.append(f"manifest_indexes_{role}_path_not_relative")
        resolved = candidate.expanduser().resolve()
    else:
        resolved = (root_path / candidate).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError:
        errors.append(f"manifest_indexes_{role}_path_outside_root")
    if not resolved.exists():
        errors.append(f"manifest_indexes_{role}_missing_file")
    elif not resolved.is_file():
        errors.append(f"manifest_indexes_{role}_not_file")
    return errors


def _resolve_v3_global_spine_path(root_path: Path, manifest: Mapping[str, Any]) -> Path:
    outputs = manifest.get("indexes")
    if isinstance(outputs, Mapping):
        global_spine = outputs.get("global_spine")
        if isinstance(global_spine, Mapping):
            raw_path = global_spine.get("path")
            if isinstance(raw_path, str) and raw_path:
                candidate = Path(raw_path)
                return candidate.expanduser().resolve() if candidate.is_absolute() else (root_path / candidate).resolve()
    raw_path = manifest.get("global_spine_path")
    if isinstance(raw_path, str) and raw_path:
        candidate = Path(raw_path)
        return candidate.expanduser().resolve() if candidate.is_absolute() else (root_path / candidate).resolve()
    return (root_path / GLOBAL_SPINE_RELATIVE_PATH).resolve()


def _verify_global_spine_startup(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    metadata: dict[str, Any] = {}
    try:
        with closing(sqlite3.connect(path)) as conn:
            metadata = read_global_spine_metadata(conn)
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "errors": [f"global_spine_sqlite_error:{exc}"],
            "path": str(path),
            "metadata": metadata,
            "sqlite_checked": True,
            "verification_mode": "startup",
        }
    if metadata.get("schema_version") != GLOBAL_SPINE_SCHEMA_VERSION:
        errors.append("global_spine_schema_version_mismatch")
    if metadata.get("index_layout") != GLOBAL_SPINE_LAYOUT:
        errors.append("global_spine_index_layout_mismatch")
    return {
        "ok": not errors,
        "errors": errors,
        "path": str(path),
        "metadata": metadata,
        "sqlite_checked": True,
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


def write_release_verification_report(
    root: Path | str,
    *,
    env: str | None = None,
    manifest_path: Path | str | None = None,
    require_current_symlink: bool = False,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a deterministic verification report for an immutable release root."""
    supplied_root = Path(root).expanduser()
    root_path = supplied_root.resolve()
    _assert_not_active_release_path(root_path, "root")
    if not root_path.is_dir():
        raise FileNotFoundError(f"Release root not found: {root_path}")
    if verification is None:
        verification_payload = verify_release_root(
            supplied_root,
            env=env,
            manifest_path=manifest_path,
            require_current_symlink=require_current_symlink,
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
        "global_spine_path": _relative_or_absolute(
            Path(str(verification_payload["global_spine_path"])).expanduser().resolve(),
            root_path,
        )
        if verification_payload.get("global_spine_path")
        else None,
        "reproducibility_hash": reproducibility_hash,
        "file_count": len(file_trace),
        "files": file_trace,
        "verification": verification_payload,
    }
    _write_json_atomic(report_path, report)
    return {**report, "path": str(report_path)}


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
    if path_text == GLOBAL_SPINE_RELATIVE_PATH.as_posix():
        return "global_spine"
    if path_text == "indexes/shard_manifest.json":
        return "shard_manifest"
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
    verification = verify_release_root(release_dir, env=env)
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
    verify_report_path = release_dir / RELEASE_VERIFY_DIRNAME / RELEASE_VERIFY_REPORT_FILENAME
    try:
        release_verify = json.loads(verify_report_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("Previously activated release verification artifacts invalid: release_verify_missing") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Previously activated release verification artifacts invalid: release_verify_invalid:{exc}") from exc
    if not isinstance(release_verify, dict):
        raise ValueError("Previously activated release verification artifacts invalid: release_verify_not_object")
    if release_verify.get("ok") is not True:
        raise ValueError("Previously activated release verification artifacts invalid: release_verify_not_ok")
    verification_payload = release_verify.get("verification")
    manifest_payload = release_verify.get("manifest")
    release_format = None
    if isinstance(verification_payload, Mapping):
        verification_manifest = verification_payload.get("manifest")
        if isinstance(verification_manifest, Mapping):
            release_format = verification_manifest.get("format")
    if release_format is None and isinstance(manifest_payload, Mapping):
        release_format = manifest_payload.get("format")
    if release_format == RELEASE_FORMAT_V3:
        return {
            **release_verify,
            "path": str(verify_report_path),
            "errors": [],
            "ok": True,
        }
    raise ValueError("Previously activated release verification artifacts invalid: release_format_not_v3")


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
        "global_spine_path": verification.get("global_spine_path"),
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
