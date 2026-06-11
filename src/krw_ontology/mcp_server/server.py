"""stdio MCP server exposing read-only krw-ontology retrieval tools."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from krw_ontology.config.paths import (
    ONTOLOGY_ENV_ENV,
    ONTOLOGY_RELEASE_ROOT_ENV,
    ONTOLOGY_ROOT_ENV,
    resolve_agent_index_path,
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
)
from krw_ontology.release import (
    load_release_manifest,
    normalize_ontology_env,
    resolve_manifest_index_path,
    verify_release_startup,
)

mcp = FastMCP("krw_ontology_mcp")

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def health_payload(
    *,
    root: str | None = None,
    index_path: str | None = None,
) -> tuple[dict, int]:
    """Return lightweight health metadata for the configured ontology release."""
    supplied_root_path = _supplied_root_path(root)
    root_path = supplied_root_path.resolve()
    release_manifest, release_manifest_path = load_release_manifest(root_path)
    configured_env = _configured_env_name()
    current_symlink = _current_symlink_metadata(supplied_root_path, root_path)
    resolved_index_path = (
        resolve_agent_index_path(root_path, index_path, fallback_to_cwd=False)
        if index_path is not None
        else resolve_manifest_index_path(root_path, release_manifest)
    )
    startup_verification = verify_release_startup(
        supplied_root_path,
        env=configured_env,
        index_path=Path(index_path) if index_path is not None else None,
        require_current_symlink=configured_env == "prod",
        check_sqlite=False,
    )
    cache_status = mcp_runtime_cache_status()
    store_status = cache_status.get("store") if isinstance(cache_status.get("store"), dict) else {}
    payload = {
        "ok": False,
        "root": str(root_path),
        "supplied_root": str(supplied_root_path.expanduser().absolute()),
        "index_path": str(resolved_index_path),
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
        "agent_index_schema_version": release_manifest.get("agent_index_schema_version"),
        "index_generated_at": release_manifest.get("index_generated_at"),
        "index_layout": release_manifest.get("index_layout"),
        "index_layout_version": release_manifest.get("index_layout_version"),
        "index_shards_present": bool(release_manifest.get("index_shards_present")),
        "global_catalog_path": release_manifest.get("global_catalog_path"),
        "global_topics_path": release_manifest.get("global_topics_path"),
        "global_topics_present": bool(release_manifest.get("global_topics_present")),
        "global_topic_count": _manifest_non_negative_int(release_manifest.get("global_topic_count")),
        "company_shards_dir": release_manifest.get("company_shards_dir"),
        "company_shard_count": _manifest_non_negative_int(release_manifest.get("company_shard_count")),
        "cache": cache_status,
        "mcp_store_hot_swap": {
            "mode": store_status.get("mode"),
            "rotations": store_status.get("rotations"),
            "active_stores": store_status.get("active_stores"),
            "retired_stores": store_status.get("retired_stores"),
            "leased": store_status.get("leased"),
            "retired_leased": store_status.get("retired_leased"),
            "rotation_pending": bool(store_status.get("rotation_pending")),
            "retired_oldest_age_sec": store_status.get("retired_oldest_age_sec"),
            "last_rotation": store_status.get("last_rotation"),
            "retired_indexes": store_status.get("retired_indexes") or [],
        },
        "documents": _manifest_non_negative_int(release_manifest.get("document_count")),
        "objects": _manifest_non_negative_int(release_manifest.get("object_count")),
        "sqlite_checked": False,
        "tools": sorted(tool.name for tool in mcp._tool_manager.list_tools()),
    }
    if configured_env == "prod" and not current_symlink["root_is_current_symlink"]:
        payload["error"] = "prod_current_symlink_required"
        return payload, 503
    if not resolved_index_path.exists():
        payload["error"] = "agent_index_not_found"
        return payload, 503
    if not startup_verification["ok"]:
        payload["error"] = "release_startup_verification_failed"
        return payload, 503

    payload["ok"] = True
    return payload, 200


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


def _current_symlink_metadata(supplied_root_path: Path, root_path: Path) -> dict[str, Any]:
    supplied_absolute = supplied_root_path.expanduser().absolute()
    root_is_current_symlink = supplied_absolute.name == "current" and supplied_absolute.is_symlink()
    current_path = supplied_absolute if supplied_absolute.name == "current" else root_path.parent / "current"
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
    index_path: str | None = None,
) -> tuple[str, int]:
    """Return Prometheus-compatible text metrics for external monitors."""
    payload, status_code = health_payload(root=root, index_path=index_path)
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
            "krw_ontology_mcp_index_present",
            "MCP configured agent index presence.",
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
            "krw_ontology_mcp_global_topics",
            "Global topic count from release manifest.",
            "gauge",
            payload.get("global_topic_count"),
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
    index_path: str | None = None,
) -> tuple[dict, int]:
    """Return heavier diagnostics, including live SQLite count queries."""
    payload, status_code = health_payload(root=root, index_path=index_path)
    if status_code != 200:
        payload["sqlite_checked"] = False
        return payload, status_code

    resolved_index_path = Path(str(payload["index_path"]))
    try:
        with closing(sqlite3.connect(resolved_index_path)) as conn:
            payload["documents"] = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            payload["objects"] = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
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
    parts = [
        f'{key}="{_prometheus_escape(str(value))}"'
        for key, value in sorted(labels.items())
    ]
    return "{" + ",".join(parts) + "}"


def _prometheus_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def krw_ontology_health(_request: Request) -> JSONResponse:
    """Health endpoint for local web and agent clients."""
    payload, status_code = health_payload()
    return JSONResponse(payload, status_code=status_code)


@mcp.custom_route("/metrics", methods=["GET"], include_in_schema=False)
async def krw_ontology_metrics(_request: Request) -> PlainTextResponse:
    """Prometheus-style metrics endpoint for release and hot-swap monitoring."""
    payload, status_code = metrics_payload()
    return PlainTextResponse(payload, status_code=status_code, media_type="text/plain; version=0.0.4")


@mcp.custom_route("/diagnostics", methods=["GET"], include_in_schema=False)
async def krw_ontology_diagnostics(_request: Request) -> JSONResponse:
    """Diagnostics endpoint for explicit operator checks."""
    payload, status_code = diagnostics_payload()
    return JSONResponse(payload, status_code=status_code)


@mcp.tool(
    name="krw_ontology_catalog",
    title="List KRW ontology companies and documents",
    annotations=READ_ONLY,
)
async def krw_ontology_catalog(
    root: str | None = None,
    index_path: str | None = None,
    ticker: str | None = None,
    document_types: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """List indexed companies, document types, periods, and document metadata."""
    return catalog_tool(
        root=root,
        index_path=index_path,
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
    root: str | None = None,
    index_path: str | None = None,
    include_counts: bool = False,
    include_capabilities: bool = True,
    include_quality_summary: bool = False,
    allow_expensive: bool = False,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return index schema, capabilities, coverage, and answerability policy."""
    return index_context_tool(
        root=root,
        index_path=index_path,
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
    root: str | None = None,
    index_path: str | None = None,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    limit_topics: int = 12,
    include_internal_ids: bool = True,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return evidence-derived company topic profiles for search planning."""
    return company_context_tool(
        ticker=ticker,
        root=root,
        index_path=index_path,
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
    question: str,
    root: str | None = None,
    index_path: str | None = None,
    ticker: str | None = None,
    tickers: list[str] | None = None,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    universe: str | None = None,
    limit_results: int = 10,
    limit_tickers: int = 20,
    include_internal_ids: bool = True,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return a compact query-specific context pack with answerability guidance."""
    return query_context_tool(
        question=question,
        root=root,
        index_path=index_path,
        ticker=ticker,
        tickers=tickers,
        document_types=document_types,
        periods=periods,
        universe=universe,
        limit_results=limit_results,
        limit_tickers=limit_tickers,
        include_internal_ids=include_internal_ids,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_ontology_query",
    title="Search KRW ontology evidence",
    annotations=READ_ONLY,
)
async def krw_ontology_query(
    root: str | None = None,
    index_path: str | None = None,
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
    return query_tool(
        root=root,
        index_path=index_path,
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
    root: str | None = None,
    index_path: str | None = None,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    limit: int = 10,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return company-specific vocabulary for planning ontology searches."""
    return topic_map_tool(
        root=root,
        index_path=index_path,
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
    root: str | None = None,
    index_path: str | None = None,
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
    return retrieve_tool(
        question=question,
        root=root,
        index_path=index_path,
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
    root: str | None = None,
    index_path: str | None = None,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Trace an object id to its source document, supporting quotes, spans, and quality."""
    return trace_tool(
        object_id=object_id,
        root=root,
        index_path=index_path,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_ontology_chain",
    title="Trace KRW ontology object relationship chain",
    annotations=READ_ONLY,
)
async def krw_ontology_chain(
    object_id: str,
    root: str | None = None,
    index_path: str | None = None,
    max_depth: int = 2,
    direction: str = "both",
    include_quote_text: bool = False,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return evidence, semantic-neighbor, and temporal-context chains around an object."""
    return chain_tool(
        object_id=object_id,
        root=root,
        index_path=index_path,
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
    root: str | None = None,
    index_path: str | None = None,
    ticker: str | None = None,
    document_type: str | None = None,
    period: str | None = None,
    limit: int = 10,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return rejected-object, batch-failure, and section-quality events."""
    return quality_tool(
        root=root,
        index_path=index_path,
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
    root: str | None = None,
    index_path: str | None = None,
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
    return compare_tool(
        tickers=tickers,
        root=root,
        index_path=index_path,
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
    title="Plan KRW ontology query",
    annotations=READ_ONLY,
)
async def krw_ontology_plan_query(
    question: str,
    root: str | None = None,
    index_path: str | None = None,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return the deterministic QueryPlan without executing a search."""
    return plan_query_tool(
        question=question,
        root=root,
        index_path=index_path,
        response_format=response_format,
    )


def main() -> None:
    """Run the local stdio MCP server."""
    mcp.run("stdio")


if __name__ == "__main__":
    main()
