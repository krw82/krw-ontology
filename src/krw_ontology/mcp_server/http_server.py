"""HTTP entrypoint for the read-only KRW ontology MCP server."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import typer

from krw_ontology.config.paths import (
    DEFAULT_ONTOLOGY_ROOT,
)
from krw_ontology.mcp_server.runtime import prepare_mcp_runtime
from krw_ontology.mcp_server.server import mcp, require_mcp_runtime_ready

LOGGER = logging.getLogger(__name__)


def _configure_logging(*, log_level: str, log_file: Path | None) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file = log_file.expanduser()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=int(os.getenv("KRW_MCP_LOG_MAX_BYTES", "10485760")),
                backupCount=int(os.getenv("KRW_MCP_LOG_BACKUP_COUNT", "3")),
                encoding="utf-8",
            )
        )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )
    protocol_level_name = os.getenv("KRW_MCP_PROTOCOL_LOG_LEVEL", "WARNING")
    protocol_level = getattr(logging, protocol_level_name.upper(), logging.WARNING)
    for logger_name in (
        "mcp.server.lowlevel.server",
        "mcp.server.streamable_http",
    ):
        logging.getLogger(logger_name).setLevel(protocol_level)


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
            expected_release_id=(
                expected_release_id
                or os.getenv("KRW_MCP_EXPECTED_RELEASE_ID")
                or os.getenv("EXPECTED_RELEASE_ID")
                or os.getenv("KRW_ONTOLOGY_RELEASE_ID")
            ),
            require_current_symlink=require_current_symlink,
            store_mode=store_mode,
        )
        require_mcp_runtime_ready(root=root)
    except RuntimeError as exc:
        typer.echo(f"KRW ontology MCP startup refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = mcp_path
    mcp.settings.stateless_http = stateless
    mcp.settings.json_response = json_response
    default_http_log_level = "WARNING" if verification.get("env") == "prod" else log_level
    mcp.settings.log_level = os.getenv(
        "KRW_MCP_HTTP_LOG_LEVEL", default_http_log_level
    )
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
