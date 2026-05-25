"""stdio MCP server exposing read-only krw-ontology retrieval tools."""

from __future__ import annotations

import sqlite3

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from krw_ontology.config.paths import resolve_agent_index_path, resolve_ontology_root
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
    topic_map_tool,
    trace_tool,
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
    """Return health metadata for the configured read-only ontology index."""
    root_path = resolve_ontology_root(root, fallback_to_cwd=False)
    resolved_index_path = resolve_agent_index_path(
        root_path,
        index_path,
        fallback_to_cwd=False,
    )
    payload = {
        "ok": False,
        "root": str(root_path),
        "index_path": str(resolved_index_path),
        "documents": 0,
        "objects": 0,
        "tools": sorted(tool.name for tool in mcp._tool_manager.list_tools()),
    }
    if not resolved_index_path.exists():
        payload["error"] = "agent_index_not_found"
        return payload, 503

    try:
        with sqlite3.connect(resolved_index_path) as conn:
            payload["documents"] = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            payload["objects"] = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    except sqlite3.Error as exc:
        payload["error"] = f"sqlite_error: {exc}"
        return payload, 503

    payload["ok"] = True
    return payload, 200


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def krw_ontology_health(_request: Request) -> JSONResponse:
    """Health endpoint for local web and agent clients."""
    payload, status_code = health_payload()
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
