"""HTTP entrypoint for the read-only KRW ontology MCP server."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import typer

from krw_ontology.config.paths import (
    DEFAULT_ONTOLOGY_ROOT,
    ONTOLOGY_ENV_ENV,
    ONTOLOGY_GLOBAL_SPINE_PATH_ENV,
    ONTOLOGY_MANIFEST_PATH_ENV,
    ONTOLOGY_RELEASE_ROOT_ENV,
    ONTOLOGY_ROOT_ENV,
    ONTOLOGY_SHARD_MANIFEST_PATH_ENV,
)
from krw_ontology.mcp_server.server import mcp
from krw_ontology.release import normalize_ontology_env, verify_release_startup_v3

LOGGER = logging.getLogger(__name__)


def _configure_logging(*, log_level: str, log_file: Path | None) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file = log_file.expanduser()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def prepare_mcp_runtime(
    *,
    root: Path,
    env: str | None,
    expected_release_id: str | None = None,
    require_current_symlink: bool = False,
    store_mode: str = "persistent",
) -> dict[str, Any]:
    """Validate and publish env vars for a long-running MCP runtime."""
    resolved_env = normalize_ontology_env(env)
    normalized_store_mode = store_mode.strip().lower()
    if normalized_store_mode not in {"persistent", "per_call"}:
        raise RuntimeError(f"store_mode must be persistent or per_call, got {store_mode!r}")
    root = root.expanduser()
    require_symlink = require_current_symlink or resolved_env == "prod"
    verification = verify_release_startup_v3(
        root,
        env=resolved_env,
        manifest_path=root / "manifest.json",
        require_current_symlink=require_symlink,
    )
    if not verification["ok"]:
        errors = ", ".join(str(error) for error in verification["errors"])
        raise RuntimeError(f"release verification failed: {errors}")
    release_id = verification.get("release_id")
    if expected_release_id and release_id != expected_release_id:
        raise RuntimeError(
            f"release_id mismatch: expected {expected_release_id}, got {release_id}"
        )
    manifest_path = verification.get("manifest_path")
    runtime_root = _absolute_without_resolving(root)
    runtime_global_spine_path = _runtime_global_spine_path(
        runtime_root=runtime_root,
        verification=verification,
    )
    shard_manifest_path = runtime_root / "indexes" / "shard_manifest.json"

    os.environ[ONTOLOGY_ENV_ENV] = resolved_env
    os.environ[ONTOLOGY_RELEASE_ROOT_ENV] = str(runtime_root)
    os.environ[ONTOLOGY_ROOT_ENV] = str(runtime_root)
    if manifest_path:
        os.environ[ONTOLOGY_MANIFEST_PATH_ENV] = str(manifest_path)
    os.environ[ONTOLOGY_GLOBAL_SPINE_PATH_ENV] = str(runtime_global_spine_path)
    os.environ[ONTOLOGY_SHARD_MANIFEST_PATH_ENV] = str(shard_manifest_path)
    os.environ["KRW_ONTOLOGY_INDEX_LAYOUT"] = str(verification["index_layout"])
    os.environ["KRW_MCP_STORE_MODE"] = normalized_store_mode
    verification["runtime_root"] = str(runtime_root)
    verification["runtime_global_spine_path"] = str(runtime_global_spine_path)
    verification["runtime_shard_manifest_path"] = str(shard_manifest_path)
    verification["runtime_store_opened"] = False
    return verification


def _absolute_without_resolving(path: Path) -> Path:
    return path.expanduser().absolute()


def _runtime_global_spine_path(
    *,
    runtime_root: Path,
    verification: dict[str, Any],
) -> Path:
    manifest = verification.get("manifest") if isinstance(verification.get("manifest"), dict) else {}
    raw_spine_path = manifest.get("global_spine_path")
    if not isinstance(raw_spine_path, str) or not raw_spine_path:
        raw_spine_path = (((manifest.get("indexes") or {}).get("global_spine") or {}).get("path"))
    if isinstance(raw_spine_path, str) and raw_spine_path:
        candidate = Path(raw_spine_path).expanduser()
        return candidate.absolute() if candidate.is_absolute() else runtime_root / candidate
    raise RuntimeError("verified v3 release manifest is missing global spine path")


def serve(
    env: str | None = typer.Option(
        None,
        "--env",
        help="Ontology environment served by this MCP process: dev, staging, or prod.",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Host interface for the local MCP HTTP server.",
    ),
    port: int = typer.Option(
        8000,
        "--port",
        help="Port for the local MCP HTTP server.",
    ),
    root: Path = typer.Option(
        DEFAULT_ONTOLOGY_ROOT,
        "--root",
        help="Stable ontology data root. The server reads only this root by default.",
    ),
    expected_release_id: str | None = typer.Option(
        None,
        "--expected-release-id",
        help="Fail startup unless manifest.release_id matches this value.",
    ),
    require_current_symlink: bool = typer.Option(
        False,
        "--require-current-symlink",
        help="Require the supplied root to be a symlink. Prod always requires this.",
    ),
    mcp_path: str = typer.Option(
        "/mcp",
        "--mcp-path",
        help="Streamable HTTP MCP endpoint path.",
    ),
    store_mode: str = typer.Option(
        "persistent",
        "--store-mode",
        help="SQLite store mode for MCP tools: persistent or per_call.",
    ),
    stateless: bool = typer.Option(
        True,
        "--stateless/--stateful",
        help="Run MCP streamable HTTP in stateless mode for local agent clients.",
    ),
    json_response: bool = typer.Option(
        False,
        "--json-response/--sse-response",
        help="Use JSON responses instead of the default streamable/SSE response behavior.",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        help="Uvicorn log level.",
    ),
    log_file: Path | None = typer.Option(
        None,
        "--log-file",
        help="Optional MCP application log file.",
    ),
) -> None:
    """Run the KRW ontology read-only MCP server over local HTTP."""
    _configure_logging(log_level=log_level, log_file=log_file)
    try:
        verification = prepare_mcp_runtime(
            root=root,
            env=env,
            expected_release_id=expected_release_id,
            require_current_symlink=require_current_symlink,
            store_mode=store_mode,
        )
    except RuntimeError as exc:
        typer.echo(f"KRW ontology MCP startup refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = mcp_path
    mcp.settings.stateless_http = stateless
    mcp.settings.json_response = json_response
    mcp.settings.log_level = log_level
    LOGGER.info(
        "mcp_server_start env=%s release_id=%s root=%s global_spine_path=%s host=%s port=%s path=%s store_mode=%s",
        verification.get("env"),
        verification.get("release_id"),
        verification.get("root"),
        verification.get("runtime_global_spine_path"),
        host,
        port,
        mcp_path,
        store_mode,
    )
    mcp.run("streamable-http")


def main() -> None:
    """CLI entrypoint."""
    typer.run(serve)


if __name__ == "__main__":
    main()
