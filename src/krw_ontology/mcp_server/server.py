"""stdio MCP server exposing read-only krw-ontology retrieval tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from krw_ontology.mcp_server.tools import (
    ResponseDetail,
    ResponseFormat,
    catalog_tool,
    compare_tool,
    plan_query_tool,
    quality_tool,
    query_tool,
    retrieve_tool,
    trace_tool,
)

mcp = FastMCP("krw_ontology_mcp")

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


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
    name="krw_ontology_query",
    title="Search KRW ontology evidence",
    annotations=READ_ONLY,
)
async def krw_ontology_query(
    root: str | None = None,
    index_path: str | None = None,
    topic: str | None = None,
    tickers: list[str] | None = None,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    object_types: list[str] | None = None,
    include_rejected: bool = False,
    limit: int = 10,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.JSON,
    response_detail: ResponseDetail = ResponseDetail.COMPACT,
) -> str:
    """Search accepted ontology objects and return source-grounded evidence bundles."""
    return query_tool(
        root=root,
        index_path=index_path,
        topic=topic,
        tickers=tickers,
        document_types=document_types,
        periods=periods,
        object_types=object_types,
        include_rejected=include_rejected,
        limit=limit,
        offset=offset,
        response_format=response_format,
        response_detail=response_detail,
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
    tickers: list[str] | None = None,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    include_rejected: bool | None = None,
    limit: int = 10,
    response_format: ResponseFormat = ResponseFormat.JSON,
    response_detail: ResponseDetail = ResponseDetail.COMPACT,
) -> str:
    """Run the deterministic local planner for a natural-language ontology question."""
    return retrieve_tool(
        question=question,
        root=root,
        index_path=index_path,
        tickers=tickers,
        document_types=document_types,
        periods=periods,
        include_rejected=include_rejected,
        limit=limit,
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
    tickers: list[str],
    root: str | None = None,
    index_path: str | None = None,
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
