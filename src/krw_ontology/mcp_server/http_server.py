"""HTTP entrypoint for the read-only KRW ontology MCP server."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from krw_ontology.config.paths import DEFAULT_ONTOLOGY_ROOT, ONTOLOGY_ROOT_ENV
from krw_ontology.mcp_server.server import mcp


def serve(
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
    mcp_path: str = typer.Option(
        "/mcp",
        "--mcp-path",
        help="Streamable HTTP MCP endpoint path.",
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
) -> None:
    """Run the KRW ontology read-only MCP server over local HTTP."""
    root = root.expanduser().resolve()
    os.environ[ONTOLOGY_ROOT_ENV] = str(root)
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = mcp_path
    mcp.settings.stateless_http = stateless
    mcp.settings.json_response = json_response
    mcp.settings.log_level = log_level
    mcp.run("streamable-http")


def main() -> None:
    """CLI entrypoint."""
    typer.run(serve)


if __name__ == "__main__":
    main()
