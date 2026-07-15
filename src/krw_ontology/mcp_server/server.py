"""stdio MCP server exposing read-only krw-ontology retrieval tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from krw_ontology.config.paths import (
    ONTOLOGY_ENV_ENV,
    ONTOLOGY_RELEASE_ROOT_ENV,
    ONTOLOGY_ROOT_ENV,
    resolve_ontology_root,
)
from krw_ontology.mcp_server.tools import (
    ResponseDetail,
    ResponseFormat,
    catalog_tool,
    chain_tool,
    company_context_tool,
    compare_tool,
    index_context_tool,
    plan_query_tool,
    quality_tool,
    query_context_tool,
    query_tool,
    retrieve_tool,
    mcp_runtime_cache_status,
    topic_map_tool,
    trace_tool,
    verify_evidence_tool,
)
from krw_ontology.mcp_server.contracts import (
    MCP_CONTRACT_VERSION,
    QueryContextInputCorrection,
    SearchPlan,
    validate_query_context_search_plan,
)
from krw_ontology.mcp_server.runtime import configured_mcp_root, prepare_mcp_runtime
from krw_ontology.release import (
    load_release_manifest,
    normalize_ontology_env,
    verify_release_startup_v3,
)

mcp = FastMCP("krw_ontology_mcp")

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

_T = TypeVar("_T")
_LANE_FAST = "fast"
_LANE_BROAD = "broad"
_LANE_DIAGNOSTIC = "diagnostic"
_LANE_CONCURRENCY_DEFAULTS = {
    _LANE_FAST: 8,
    _LANE_BROAD: 2,
    _LANE_DIAGNOSTIC: 1,
}
_LANE_CONCURRENCY_ENVS = {
    _LANE_FAST: "KRW_MCP_FAST_LANE_CONCURRENCY",
    _LANE_BROAD: "KRW_MCP_BROAD_LANE_CONCURRENCY",
    _LANE_DIAGNOSTIC: "KRW_MCP_DIAGNOSTIC_LANE_CONCURRENCY",
}
_LANE_SEMAPHORES: dict[tuple[int, str], asyncio.Semaphore] = {}
EXPECTED_TOOL_NAMES = (
    "krw_ontology_catalog",
    "krw_ontology_chain",
    "krw_ontology_company_context",
    "krw_ontology_compare",
    "krw_ontology_index_context",
    "krw_ontology_plan_query",
    "krw_ontology_quality",
    "krw_ontology_query",
    "krw_ontology_query_context",
    "krw_ontology_retrieve",
    "krw_ontology_topic_map",
    "krw_ontology_trace",
    "krw_ontology_verify_evidence",
)
_EXPECTED_FINGERPRINT_ENVS = {
    "mcp_contract_version": "KRW_MCP_EXPECTED_CONTRACT_VERSION",
    "tool_schema_sha256": "KRW_MCP_EXPECTED_TOOL_SCHEMA_SHA256",
    "build_id": "KRW_MCP_EXPECTED_BUILD_ID",
    "backend_git_sha": "KRW_MCP_EXPECTED_BACKEND_GIT_SHA",
    "build_fingerprint_sha256": "KRW_MCP_EXPECTED_BUILD_FINGERPRINT_SHA256",
    "release_manifest_sha256": "KRW_MCP_EXPECTED_RELEASE_MANIFEST_SHA256",
    "service_fingerprint_sha256": "KRW_MCP_EXPECTED_SERVICE_FINGERPRINT_SHA256",
}


def health_payload(
    *,
    root: str | None = None,
) -> tuple[dict, int]:
    """Return health metadata for the configured ontology release."""
    return _release_payload(root=root, include_runtime_cache=True)


def ready_payload(
    *,
    root: str | None = None,
) -> tuple[dict, int]:
    """Return lightweight worker-admission readiness metadata."""
    return _release_payload(root=root, include_runtime_cache=False)


def require_mcp_runtime_ready(*, root: Path | str) -> dict[str, Any]:
    """Fail before serving when release, artifact, tool, or build bindings mismatch."""
    payload, status_code = ready_payload(root=str(root))
    if status_code != 200 or not payload.get("ok"):
        startup = payload.get("startup") if isinstance(payload.get("startup"), dict) else {}
        errors = list(startup.get("errors") or [])
        mismatches = payload.get("fingerprint_mismatches") or []
        details = ", ".join(str(value) for value in [*errors, *mismatches])
        raise RuntimeError(details or str(payload.get("error") or "runtime_not_ready"))
    return payload


def live_payload() -> tuple[dict, int]:
    """Return process liveness without touching release files or SQLite."""
    return {
        "ok": True,
        "service": "krw_ontology_mcp",
    }, 200


async def _run_tool_in_lane(
    lane: str,
    func: Callable[..., _T],
    /,
    **kwargs: Any,
) -> _T:
    """Run blocking tool code off the event loop behind a lane semaphore."""
    semaphore = _lane_semaphore(lane)
    async with semaphore:
        return await asyncio.to_thread(func, **kwargs)


def _lane_semaphore(lane: str) -> asyncio.Semaphore:
    running_loop_id = id(asyncio.get_running_loop())
    key = (running_loop_id, lane)
    semaphore = _LANE_SEMAPHORES.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_lane_concurrency(lane))
        _LANE_SEMAPHORES[key] = semaphore
    return semaphore


def _lane_concurrency(lane: str) -> int:
    env_name = _LANE_CONCURRENCY_ENVS.get(lane)
    default = _LANE_CONCURRENCY_DEFAULTS.get(lane, 1)
    if not env_name:
        return default
    raw_value = os.environ.get(env_name)
    try:
        value = int(str(raw_value or "").strip())
    except ValueError:
        return default
    return max(1, value)


def _has_ticker_scope(*, ticker: str | None = None, tickers: list[str] | None = None) -> bool:
    if str(ticker or "").strip():
        return True
    return any(str(item or "").strip() for item in (tickers or []))


def _release_payload(
    *,
    root: str | None,
    include_runtime_cache: bool,
) -> tuple[dict, int]:
    """Return release-serving metadata without live SQLite row counts."""
    supplied_root_path = _supplied_root_path(root)
    root_path = supplied_root_path.resolve()
    release_manifest, release_manifest_path = load_release_manifest(
        root_path,
        manifest_path=root_path / "manifest.json",
    )
    configured_env = _configured_env_name()
    current_symlink = _current_symlink_metadata(supplied_root_path, root_path)
    resolved_global_spine_path = _health_global_spine_path(root_path, release_manifest)
    index_outputs = (
        release_manifest.get("indexes") if isinstance(release_manifest.get("indexes"), dict) else {}
    )
    global_spine_output = (
        index_outputs.get("global_spine")
        if isinstance(index_outputs.get("global_spine"), dict)
        else {}
    )
    global_spine_counts = (
        global_spine_output.get("counts")
        if isinstance(global_spine_output.get("counts"), dict)
        else {}
    )
    company_shards_output = (
        index_outputs.get("company_shards")
        if isinstance(index_outputs.get("company_shards"), dict)
        else {}
    )
    document_count = _manifest_non_negative_int(global_spine_counts.get("global_document_catalog"))
    object_count = _manifest_non_negative_int(
        release_manifest.get("global_object_count")
        if release_manifest.get("global_object_count") is not None
        else global_spine_counts.get("global_object_locator")
    )
    object_occurrence_count = _manifest_non_negative_int(
        global_spine_counts.get("global_object_replica")
    )
    edge_count = _manifest_non_negative_int(global_spine_counts.get("global_edge_spine"))
    edge_occurrence_count = _manifest_non_negative_int(
        global_spine_counts.get("global_edge_replica")
    )
    global_topic_spine_count = _manifest_non_negative_int(
        global_spine_counts.get("global_topic_spine")
    )
    company_shard_count = _manifest_non_negative_int(company_shards_output.get("count"))
    startup_verification = verify_release_startup_v3(
        supplied_root_path,
        env=configured_env,
        manifest_path=root_path / "manifest.json",
        require_current_symlink=configured_env == "prod",
        check_sqlite=False,
    )
    chart_series_verification = startup_verification.get("chart_series_verification")
    chart_series_verification_ok = (
        bool(chart_series_verification.get("ok"))
        if isinstance(chart_series_verification, dict)
        else None
    )
    fingerprints = _runtime_fingerprints(release_manifest_path)
    payload = {
        "ok": False,
        "root": str(root_path),
        "supplied_root": str(supplied_root_path.expanduser().absolute()),
        "release_id": release_manifest.get("release_id"),
        "env": release_manifest.get("env") or configured_env,
        "configured_env": configured_env,
        "manifest_path": str(release_manifest_path) if release_manifest_path else None,
        "manifest_valid": bool(release_manifest) and startup_verification["ok"],
        "startup_verification_ok": startup_verification["ok"],
        "startup_verification_errors": list(startup_verification.get("errors") or []),
        "current_symlink": current_symlink["current_symlink"],
        "current_symlink_path": current_symlink["current_symlink_path"],
        "current_symlink_target": current_symlink["current_symlink_target"],
        "current_release_id": current_symlink["current_release_id"],
        "root_is_current_symlink": current_symlink["root_is_current_symlink"],
        "index_layout": release_manifest.get("index_layout"),
        "global_spine_schema_version": global_spine_output.get("schema_version"),
        "global_spine_path": str(resolved_global_spine_path),
        "global_spine_manifest_path": global_spine_output.get("path"),
        "global_spine_present": resolved_global_spine_path.exists(),
        "chart_series_path": startup_verification.get("chart_series_path"),
        "chart_series_present": bool(startup_verification.get("chart_series_present")),
        "chart_series_verification_ok": chart_series_verification_ok,
        "company_shards_present": bool(company_shards_output.get("required")),
        "global_topic_spine_present": global_topic_spine_count is not None
        and global_topic_spine_count > 0,
        "global_topic_spine_count": global_topic_spine_count,
        "company_shards_dir": release_manifest.get("company_shards_dir")
        or company_shards_output.get("dir"),
        "company_shard_count": company_shard_count,
        "documents": document_count,
        "objects": object_count,
        "object_occurrences": object_occurrence_count,
        "edges": edge_count,
        "edge_occurrences": edge_occurrence_count,
        "sqlite_checked": False,
        "tools": fingerprints["tool_names"],
        **fingerprints,
    }
    if include_runtime_cache:
        cache_status = mcp_runtime_cache_status()
        store_status = (
            cache_status.get("store") if isinstance(cache_status.get("store"), dict) else {}
        )
        payload["cache"] = cache_status
        payload["mcp_store_hot_swap"] = {
            "mode": store_status.get("mode"),
            "rotations": store_status.get("rotations"),
            "active_stores": store_status.get("active_stores"),
            "retired_stores": store_status.get("retired_stores"),
            "leased": store_status.get("leased"),
            "retired_leased": store_status.get("retired_leased"),
            "rotation_pending": bool(store_status.get("rotation_pending")),
            "retired_oldest_age_sec": store_status.get("retired_oldest_age_sec"),
            "last_rotation": store_status.get("last_rotation"),
            "retired_global_spine_stores": store_status.get("retired_global_spine_stores") or [],
        }
    if configured_env == "prod" and not current_symlink["root_is_current_symlink"]:
        payload["error"] = "prod_current_symlink_required"
        return payload, 503
    if not resolved_global_spine_path.exists():
        payload["error"] = "global_spine_not_found"
        return payload, 503
    if not startup_verification["ok"]:
        payload["error"] = "release_startup_verification_failed"
        return payload, 503
    if not payload["fingerprint_match"]:
        payload["error"] = "mcp_fingerprint_mismatch"
        return payload, 503

    payload["ok"] = True
    return payload, 200


def _runtime_fingerprints(manifest_path: Path | None) -> dict[str, Any]:
    tool_contract = _tool_contract_fingerprint()
    source_sha256 = _source_build_sha256()
    git_sha = _backend_git_sha()
    try:
        distribution_version = package_version("krw-ontology")
    except PackageNotFoundError:
        distribution_version = "0+unknown"
    build_id = os.environ.get("KRW_MCP_BUILD_ID") or (
        f"krw-ontology-{distribution_version}+source.{source_sha256[:12]}"
    )
    build_fingerprint_sha256 = _sha256_json(
        {
            "mcp_contract_version": MCP_CONTRACT_VERSION,
            "build_id": build_id,
            "backend_git_sha": git_sha,
            "distribution_version": distribution_version,
            "source_sha256": source_sha256,
            "runtime_versions": {
                "python": sys.version.split()[0],
                "sqlite": sqlite3.sqlite_version,
                "mcp": _distribution_version("mcp"),
                "pydantic": _distribution_version("pydantic"),
            },
        }
    )
    release_manifest_sha256 = _file_sha256(manifest_path)
    service_fingerprint_sha256 = _sha256_json(
        {
            "mcp_contract_version": MCP_CONTRACT_VERSION,
            "tool_schema_sha256": tool_contract["tool_schema_sha256"],
            "build_fingerprint_sha256": build_fingerprint_sha256,
            "release_manifest_sha256": release_manifest_sha256,
        }
    )
    payload: dict[str, Any] = {
        "mcp_contract_version": MCP_CONTRACT_VERSION,
        "tool_count": tool_contract["tool_count"],
        "tool_names": tool_contract["tool_names"],
        "tool_names_match": tool_contract["tool_names_match"],
        "tool_schema_sha256": tool_contract["tool_schema_sha256"],
        "build_id": build_id,
        "backend_git_sha": git_sha,
        "build_fingerprint_sha256": build_fingerprint_sha256,
        "release_manifest_sha256": release_manifest_sha256,
        "service_fingerprint_sha256": service_fingerprint_sha256,
    }
    mismatches: list[dict[str, str | None]] = []
    if not tool_contract["tool_names_match"]:
        mismatches.append(
            {
                "field": "tool_names",
                "expected": ",".join(EXPECTED_TOOL_NAMES),
                "actual": ",".join(tool_contract["tool_names"]),
            }
        )
    for field, env_name in _EXPECTED_FINGERPRINT_ENVS.items():
        expected = os.environ.get(env_name)
        if (
            expected is not None
            and expected.strip()
            and str(payload.get(field) or "") != expected.strip()
        ):
            mismatches.append(
                {
                    "field": field,
                    "expected": expected.strip(),
                    "actual": str(payload.get(field)) if payload.get(field) is not None else None,
                }
            )
    payload["fingerprint_match"] = not mismatches
    payload["fingerprint_mismatches"] = mismatches
    return payload


def runtime_fingerprint_payload(*, root: str | Path | None = None) -> dict[str, Any]:
    """Return deploy-time MCP/build/release fingerprints without opening SQLite."""
    root_path = _supplied_root_path(str(root) if root is not None else None).resolve()
    _manifest, manifest_path = load_release_manifest(
        root_path,
        manifest_path=root_path / "manifest.json",
    )
    return _runtime_fingerprints(manifest_path or (root_path / "manifest.json"))


@lru_cache(maxsize=1)
def _tool_contract_fingerprint() -> dict[str, Any]:
    tools = sorted(mcp._tool_manager.list_tools(), key=lambda tool: tool.name)
    names = [tool.name for tool in tools]
    schemas = []
    for tool in tools:
        annotations = tool.annotations
        if hasattr(annotations, "model_dump"):
            annotations_payload = annotations.model_dump(mode="json")
        elif isinstance(annotations, dict):
            annotations_payload = dict(annotations)
        else:
            annotations_payload = None
        fn_metadata = getattr(tool, "fn_metadata", None)
        schemas.append(
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "input_schema": tool.parameters,
                "output_schema": getattr(fn_metadata, "output_schema", None),
                "annotations": annotations_payload,
            }
        )
    return {
        "tool_count": len(names),
        "tool_names": names,
        "tool_names_match": tuple(names) == EXPECTED_TOOL_NAMES,
        "tool_schema_sha256": _sha256_json(schemas),
    }


@lru_cache(maxsize=1)
def _source_build_sha256() -> str:
    package_dir = Path(__file__).resolve().parent.parent
    repository_root = package_dir.parent.parent
    files = sorted(
        path
        for path in package_dir.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix in {".json", ".py", ".yaml", ".yml"}
    )
    plugin_dir = repository_root / "plugins" / "krw-ontology"
    files.extend(
        path
        for path in plugin_dir.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix in {".json", ".md", ".yaml", ".yml"}
    )
    for relative_path in (
        "pyproject.toml",
        "uv.lock",
        "plugins/krw-ontology/.mcp.json",
        "plugins/krw-ontology/.claude-plugin/plugin.json",
        "plugins/krw-ontology/.codex-plugin/plugin.json",
        "Dockerfile",
        "docker-compose.yml",
    ):
        candidate = repository_root / relative_path
        if candidate.is_file():
            files.append(candidate)
    files = sorted(set(files))
    digest = hashlib.sha256()
    for path in files:
        try:
            relative = path.relative_to(repository_root)
        except ValueError:
            relative = path.relative_to(package_dir)
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _distribution_version(name: str) -> str:
    try:
        return package_version(name)
    except PackageNotFoundError:
        return "0+unknown"


@lru_cache(maxsize=1)
def _backend_git_sha() -> str | None:
    configured = os.environ.get("KRW_MCP_BACKEND_GIT_SHA")
    if configured and configured.strip():
        return configured.strip()
    repo_root = Path(__file__).resolve().parents[3]
    git_dir = repo_root / ".git"
    if git_dir.is_file():
        pointer = git_dir.read_text(encoding="utf-8").strip()
        if pointer.startswith("gitdir:"):
            candidate = Path(pointer.split(":", 1)[1].strip())
            git_dir = candidate if candidate.is_absolute() else (repo_root / candidate).resolve()
    head_path = git_dir / "HEAD"
    if not head_path.exists():
        return None
    head = head_path.read_text(encoding="utf-8").strip()
    if not head.startswith("ref:"):
        return head or None
    ref_path = git_dir / head.split(":", 1)[1].strip()
    if ref_path.exists():
        return ref_path.read_text(encoding="utf-8").strip() or None
    packed_refs = git_dir / "packed-refs"
    if packed_refs.exists():
        ref_name = head.split(":", 1)[1].strip()
        for line in packed_refs.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            sha, _, name = line.partition(" ")
            if name == ref_name:
                return sha or None
    return None


def _file_sha256(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _supplied_root_path(root: str | None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    raw_root = os.environ.get(ONTOLOGY_RELEASE_ROOT_ENV) or os.environ.get(ONTOLOGY_ROOT_ENV)
    if raw_root:
        return Path(raw_root).expanduser()
    return resolve_ontology_root(None, fallback_to_cwd=False)


def _configured_env_name() -> str | None:
    raw_env = os.environ.get(ONTOLOGY_ENV_ENV)
    if raw_env is None or raw_env.strip() == "":
        return None
    return normalize_ontology_env(raw_env)


def _health_global_spine_path(root_path: Path, release_manifest: dict[str, Any]) -> Path:
    raw_path = ((release_manifest.get("indexes") or {}).get("global_spine") or {}).get("path")
    if isinstance(raw_path, str) and raw_path:
        candidate = Path(raw_path).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (root_path / candidate).resolve()
    return (root_path / "indexes" / "global_spine.sqlite").resolve()


def _current_symlink_metadata(supplied_root_path: Path, root_path: Path) -> dict[str, Any]:
    supplied_absolute = supplied_root_path.expanduser().absolute()
    root_is_current_symlink = supplied_absolute.name == "current" and supplied_absolute.is_symlink()
    current_path = (
        supplied_absolute if supplied_absolute.name == "current" else root_path.parent / "current"
    )
    current_target = os.readlink(current_path) if current_path.is_symlink() else None
    return {
        "current_symlink": current_path.is_symlink(),
        "current_symlink_path": str(current_path),
        "current_symlink_target": current_target,
        "current_release_id": Path(current_target).name.rstrip("/") if current_target else None,
        "root_is_current_symlink": root_is_current_symlink,
    }


def metrics_payload(
    *,
    root: str | None = None,
) -> tuple[str, int]:
    """Return Prometheus-compatible text metrics for external monitors."""
    payload, status_code = health_payload(root=root)
    cache = payload.get("cache") if isinstance(payload.get("cache"), dict) else {}
    store = cache.get("store") if isinstance(cache.get("store"), dict) else {}
    labels = {
        "env": payload.get("env") or "unknown",
        "release_id": payload.get("release_id") or "unknown",
    }
    metrics: list[tuple[str, str, str, float | int | None, dict[str, Any] | None]] = [
        (
            "krw_ontology_mcp_health_ok",
            "MCP health status, 1 when the configured release is serveable.",
            "gauge",
            1 if payload.get("ok") else 0,
            labels,
        ),
        (
            "krw_ontology_mcp_global_spine_present",
            "MCP configured global spine presence.",
            "gauge",
            1 if status_code == 200 else 0,
            labels,
        ),
        (
            "krw_ontology_mcp_release_documents",
            "Document count from release manifest.",
            "gauge",
            payload.get("documents"),
            labels,
        ),
        (
            "krw_ontology_mcp_release_objects",
            "Object count from release manifest.",
            "gauge",
            payload.get("objects"),
            labels,
        ),
        (
            "krw_ontology_mcp_company_shards",
            "Company shard count from release manifest.",
            "gauge",
            payload.get("company_shard_count"),
            labels,
        ),
        (
            "krw_ontology_mcp_global_topic_spine_rows",
            "Global topic spine row count from release manifest.",
            "gauge",
            payload.get("global_topic_spine_count"),
            labels,
        ),
        (
            "krw_ontology_mcp_store_active_stores",
            "Active SQLite store handles in the MCP process.",
            "gauge",
            store.get("active_stores"),
            labels,
        ),
        (
            "krw_ontology_mcp_store_leased",
            "Currently leased active SQLite store handles.",
            "gauge",
            store.get("leased"),
            labels,
        ),
        (
            "krw_ontology_mcp_store_retired_leased",
            "Leased retired store handles pinned to a previous release.",
            "gauge",
            store.get("retired_leased"),
            labels,
        ),
        (
            "krw_ontology_mcp_store_rotation_pending",
            "Whether any in-flight lease is still pinned to a retired release.",
            "gauge",
            1 if store.get("rotation_pending") else 0,
            labels,
        ),
        (
            "krw_ontology_mcp_store_retired_oldest_age_seconds",
            "Oldest retired in-flight store age in seconds.",
            "gauge",
            store.get("retired_oldest_age_sec"),
            labels,
        ),
        (
            "krw_ontology_mcp_store_rotations_total",
            "Process-local count of MCP store rotations.",
            "counter",
            store.get("rotations"),
            labels,
        ),
        (
            "krw_ontology_mcp_store_opened_total",
            "Process-local count of opened MCP stores.",
            "counter",
            store.get("opened"),
            labels,
        ),
        (
            "krw_ontology_mcp_store_closed_total",
            "Process-local count of closed MCP stores.",
            "counter",
            store.get("closed"),
            labels,
        ),
    ]
    return _render_prometheus_metrics(metrics), status_code


def diagnostics_payload(
    *,
    root: str | None = None,
) -> tuple[dict, int]:
    """Return heavier diagnostics, including live SQLite count queries."""
    payload, status_code = health_payload(root=root)
    if status_code != 200:
        payload["sqlite_checked"] = False
        return payload, status_code

    resolved_global_spine_path = Path(str(payload["global_spine_path"]))
    try:
        with closing(sqlite3.connect(resolved_global_spine_path)) as conn:
            payload["documents"] = conn.execute(
                "SELECT COUNT(*) FROM global_document_catalog"
            ).fetchone()[0]
            payload["objects"] = conn.execute(
                "SELECT COUNT(*) FROM global_object_locator"
            ).fetchone()[0]
            payload["object_occurrences"] = conn.execute(
                "SELECT COUNT(*) FROM global_object_replica"
            ).fetchone()[0]
            payload["edges"] = conn.execute("SELECT COUNT(*) FROM global_edge_spine").fetchone()[0]
            payload["edge_occurrences"] = conn.execute(
                "SELECT COUNT(*) FROM global_edge_replica"
            ).fetchone()[0]
    except sqlite3.Error as exc:
        payload["error"] = f"sqlite_error: {exc}"
        payload["sqlite_checked"] = True
        payload["ok"] = False
        return payload, 503

    payload["sqlite_checked"] = True
    payload["ok"] = True
    return payload, 200


def _manifest_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _render_prometheus_metrics(
    metrics: list[tuple[str, str, str, float | int | None, dict[str, Any] | None]],
) -> str:
    lines: list[str] = []
    for name, help_text, metric_type, value, labels in metrics:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")
        lines.append(f"{name}{_prometheus_labels(labels or {})} {value}")
    return "\n".join(lines) + "\n"


def _prometheus_labels(labels: dict[str, Any]) -> str:
    if not labels:
        return ""
    parts = [f'{key}="{_prometheus_escape(str(value))}"' for key, value in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _prometheus_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


@mcp.custom_route("/livez", methods=["GET"], include_in_schema=False)
async def krw_ontology_livez(_request: Request) -> JSONResponse:
    """Process liveness endpoint for supervisors and service managers."""
    payload, status_code = live_payload()
    return JSONResponse(payload, status_code=status_code)


@mcp.custom_route("/readyz", methods=["GET"], include_in_schema=False)
async def krw_ontology_readyz(_request: Request) -> JSONResponse:
    """Readiness endpoint for worker admission checks."""
    payload, status_code = ready_payload()
    return JSONResponse(payload, status_code=status_code)


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def krw_ontology_health(_request: Request) -> JSONResponse:
    """Health endpoint for local web and agent clients."""
    payload, status_code = health_payload()
    return JSONResponse(payload, status_code=status_code)


@mcp.custom_route("/metrics", methods=["GET"], include_in_schema=False)
async def krw_ontology_metrics(_request: Request) -> PlainTextResponse:
    """Prometheus-style metrics endpoint for release and hot-swap monitoring."""
    payload, status_code = metrics_payload()
    return PlainTextResponse(
        payload, status_code=status_code, media_type="text/plain; version=0.0.4"
    )


@mcp.custom_route("/diagnostics", methods=["GET"], include_in_schema=False)
async def krw_ontology_diagnostics(_request: Request) -> JSONResponse:
    """Diagnostics endpoint for explicit operator checks."""
    payload, status_code = await _run_tool_in_lane(_LANE_DIAGNOSTIC, diagnostics_payload)
    return JSONResponse(payload, status_code=status_code)


@mcp.tool(
    name="krw_ontology_catalog",
    title="List KRW ontology companies and documents",
    annotations=READ_ONLY,
)
async def krw_ontology_catalog(
    ticker: str | None = None,
    document_types: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """List indexed companies, document types, periods, and document metadata."""
    return await _run_tool_in_lane(
        _LANE_FAST,
        catalog_tool,
        ticker=ticker,
        document_types=document_types,
        limit=limit,
        offset=offset,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_ontology_index_context",
    title="Return KRW ontology index context",
    annotations=READ_ONLY,
)
async def krw_ontology_index_context(
    include_counts: bool = False,
    include_capabilities: bool = True,
    include_quality_summary: bool = False,
    allow_expensive: bool = False,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return index schema, capabilities, coverage, and answerability policy."""
    lane = (
        _LANE_DIAGNOSTIC
        if allow_expensive or include_counts or include_quality_summary
        else _LANE_FAST
    )
    return await _run_tool_in_lane(
        lane,
        index_context_tool,
        include_counts=include_counts,
        include_capabilities=include_capabilities,
        include_quality_summary=include_quality_summary,
        allow_expensive=allow_expensive,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_ontology_company_context",
    title="Return KRW ontology company topic context",
    annotations=READ_ONLY,
)
async def krw_ontology_company_context(
    ticker: str,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    limit_topics: int = 12,
    include_internal_ids: bool = True,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return evidence-derived company topic profiles for search planning."""
    return await _run_tool_in_lane(
        _LANE_FAST,
        company_context_tool,
        ticker=ticker,
        document_types=document_types,
        periods=periods,
        limit_topics=limit_topics,
        include_internal_ids=include_internal_ids,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_ontology_query_context",
    title="Plan KRW ontology answer context",
    annotations=READ_ONLY,
)
async def krw_ontology_query_context(
    search_plan: SearchPlan | Mapping[str, Any],
) -> CallToolResult:
    """Execute one explicit agent search plan and return compact ResearchState v2.

    The search plan is mandatory. The server never infers or replaces its intent,
    clauses, ticker scope, document filters, or period filters from keywords.
    Invalid plans return one English structured correction result containing
    every deterministic violation MCP can identify to the active agent run.
    The server does not rewrite the plan or start another run.
    """
    validated_plan = validate_query_context_search_plan(search_plan)
    if isinstance(validated_plan, QueryContextInputCorrection):
        return _query_context_input_correction_result(validated_plan)

    lane = _LANE_FAST if bool(validated_plan.tickers) else _LANE_BROAD
    state = await _run_tool_in_lane(
        lane,
        query_context_tool,
        search_plan=validated_plan,
    )
    compact_json = state.model_dump_json()
    # Returning CallToolResult preserves compact JSON and lets semantic input
    # corrections use the same MCP result channel without output revalidation.
    return CallToolResult(
        content=[TextContent(type="text", text=compact_json)],
        structuredContent=state.model_dump(mode="json", by_alias=True),
    )


def _query_context_input_correction_result(
    correction: QueryContextInputCorrection,
) -> CallToolResult:
    """Return an actionable MCP tool error without a runner-side retry path."""
    payload = correction.model_dump(mode="json", exclude_none=True)
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        ],
        structuredContent=payload,
        isError=True,
    )


@mcp.tool(
    name="krw_ontology_query",
    title="Search KRW ontology evidence",
    annotations=READ_ONLY,
)
async def krw_ontology_query(
    topic: str | None = None,
    ticker: str | None = None,
    tickers: list[str] | None = None,
    document_type: str | None = None,
    document_types: list[str] | None = None,
    period: str | None = None,
    periods: list[str] | None = None,
    object_type: str | None = None,
    object_types: list[str] | None = None,
    include_rejected: bool = False,
    limit: int = 10,
    offset: int = 0,
    group_by: str | None = None,
    limit_groups: int = 10,
    limit_per_group: int = 3,
    answer_candidate_only: bool = False,
    response_format: ResponseFormat = ResponseFormat.JSON,
    response_detail: ResponseDetail = ResponseDetail.COMPACT,
) -> str:
    """Search accepted ontology objects and return source-grounded evidence bundles."""
    lane = _LANE_FAST if _has_ticker_scope(ticker=ticker, tickers=tickers) else _LANE_BROAD
    return await _run_tool_in_lane(
        lane,
        query_tool,
        topic=topic,
        ticker=ticker,
        tickers=tickers,
        document_type=document_type,
        document_types=document_types,
        period=period,
        periods=periods,
        object_type=object_type,
        object_types=object_types,
        include_rejected=include_rejected,
        limit=limit,
        offset=offset,
        group_by=group_by,
        limit_groups=limit_groups,
        limit_per_group=limit_per_group,
        answer_candidate_only=answer_candidate_only,
        response_format=response_format,
        response_detail=response_detail,
    )


@mcp.tool(
    name="krw_ontology_topic_map",
    title="Discover KRW ontology search topics",
    annotations=READ_ONLY,
)
async def krw_ontology_topic_map(
    ticker: str,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    limit: int = 10,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return company-specific vocabulary for planning ontology searches."""
    return await _run_tool_in_lane(
        _LANE_FAST,
        topic_map_tool,
        ticker=ticker,
        document_types=document_types,
        periods=periods,
        limit=limit,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_ontology_retrieve",
    title="Plan and retrieve KRW ontology evidence",
    annotations=READ_ONLY,
)
async def krw_ontology_retrieve(
    question: str,
    ticker: str | None = None,
    tickers: list[str] | None = None,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    include_rejected: bool | None = None,
    limit: int = 10,
    group_by: str | None = None,
    limit_groups: int = 10,
    limit_per_group: int = 3,
    answer_candidate_only: bool = False,
    response_format: ResponseFormat = ResponseFormat.JSON,
    response_detail: ResponseDetail = ResponseDetail.COMPACT,
    agent_context: dict[str, Any] | None = None,
) -> str:
    """Run the deterministic local planner for a natural-language ontology question."""
    lane = _LANE_FAST if _has_ticker_scope(ticker=ticker, tickers=tickers) else _LANE_BROAD
    return await _run_tool_in_lane(
        lane,
        retrieve_tool,
        question=question,
        ticker=ticker,
        tickers=tickers,
        document_types=document_types,
        periods=periods,
        include_rejected=include_rejected,
        limit=limit,
        group_by=group_by,
        limit_groups=limit_groups,
        limit_per_group=limit_per_group,
        answer_candidate_only=answer_candidate_only,
        response_format=response_format,
        response_detail=response_detail,
        agent_context=agent_context,
    )


@mcp.tool(
    name="krw_ontology_trace",
    title="Trace KRW ontology object evidence",
    annotations=READ_ONLY,
)
async def krw_ontology_trace(
    object_id: str,
    ticker: str | None = None,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Trace an object occurrence to its source document, quotes, spans, and quality."""
    return await _run_tool_in_lane(
        _LANE_FAST,
        trace_tool,
        object_id=object_id,
        ticker=ticker,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_ontology_verify_evidence",
    title="Verify KRW ontology evidence lineage",
    annotations=READ_ONLY,
)
async def krw_ontology_verify_evidence(
    ticker: str,
    questions: list[dict[str, Any]],
    brief_hash: str | None = None,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Verify exact object ids and return a bounded, hash-stable company evidence pack."""
    return await _run_tool_in_lane(
        _LANE_FAST,
        verify_evidence_tool,
        ticker=ticker,
        questions=questions,
        brief_hash=brief_hash,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_ontology_chain",
    title="Trace KRW ontology object relationship chain",
    annotations=READ_ONLY,
)
async def krw_ontology_chain(
    object_id: str,
    ticker: str | None = None,
    max_depth: int = 2,
    direction: str = "both",
    include_quote_text: bool = False,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return evidence, semantic-neighbor, and temporal chains around an object occurrence."""
    return await _run_tool_in_lane(
        _LANE_FAST,
        chain_tool,
        object_id=object_id,
        ticker=ticker,
        max_depth=max_depth,
        direction=direction,
        include_quote_text=include_quote_text,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_ontology_quality",
    title="Inspect KRW ontology quality signals",
    annotations=READ_ONLY,
)
async def krw_ontology_quality(
    ticker: str | None = None,
    document_type: str | None = None,
    period: str | None = None,
    limit: int = 10,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return rejected-object, batch-failure, and section-quality events."""
    return await _run_tool_in_lane(
        _LANE_DIAGNOSTIC,
        quality_tool,
        ticker=ticker,
        document_type=document_type,
        period=period,
        limit=limit,
        offset=offset,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_ontology_compare",
    title="Compare companies in KRW ontology",
    annotations=READ_ONLY,
)
async def krw_ontology_compare(
    tickers: list[str] | None = None,
    ticker: str | None = None,
    ticker_a: str | None = None,
    ticker_b: str | None = None,
    topic: str | None = None,
    metric: str | None = None,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    limit_per_ticker: int = 5,
    response_format: ResponseFormat = ResponseFormat.JSON,
    response_detail: ResponseDetail = ResponseDetail.COMPACT,
) -> str:
    """Compare two or more companies by evidence topic or canonical metric."""
    return await _run_tool_in_lane(
        _LANE_BROAD,
        compare_tool,
        tickers=tickers,
        ticker=ticker,
        ticker_a=ticker_a,
        ticker_b=ticker_b,
        topic=topic,
        metric=metric,
        document_types=document_types,
        periods=periods,
        limit_per_ticker=limit_per_ticker,
        response_format=response_format,
        response_detail=response_detail,
    )


@mcp.tool(
    name="krw_ontology_plan_query",
    title="Validate KRW ontology SearchPlan v2",
    annotations=READ_ONLY,
)
async def krw_ontology_plan_query(
    search_plan: SearchPlan,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Validate and normalize an agent-authored SearchPlan without retrieval."""
    return await _run_tool_in_lane(
        _LANE_FAST,
        plan_query_tool,
        search_plan=search_plan,
        response_format=response_format,
    )


def main() -> None:
    """Run the local stdio MCP server."""
    root = configured_mcp_root()
    try:
        prepare_mcp_runtime(
            root=root,
            env=os.getenv(ONTOLOGY_ENV_ENV),
            expected_release_id=(
                os.getenv("KRW_MCP_EXPECTED_RELEASE_ID")
                or os.getenv("EXPECTED_RELEASE_ID")
                or os.getenv("KRW_ONTOLOGY_RELEASE_ID")
            ),
            store_mode=os.getenv("KRW_MCP_STORE_MODE", "persistent"),
        )
        require_mcp_runtime_ready(root=root)
    except RuntimeError as exc:
        raise SystemExit(f"KRW ontology MCP startup refused: {exc}") from exc
    mcp.run("stdio")


if __name__ == "__main__":
    main()
