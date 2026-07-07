"""stdio MCP server exposing read-only guru advisor tools."""

from __future__ import annotations

import asyncio
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from krw_ontology.guru.lens_selector import guru_select_lenses_tool
from krw_ontology.guru.mcp_tools import (
    GuruResponseFormat,
    guru_chain_tool,
    guru_company_brief_tool,
    guru_company_pack_tool,
    guru_context_tool,
    guru_data_needs_tool,
    guru_eval_questions_tool,
    guru_evidence_tool,
    guru_index_context_tool,
    guru_query_context_tool,
    guru_search_tool,
    guru_status_tool,
    guru_trace_tool,
)


mcp = FastMCP("krw_guru_advisor_mcp")

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


async def _run_tool(func, /, **kwargs):
    return await asyncio.to_thread(func, **kwargs)


@mcp.tool(
    name="krw_guru_status",
    title="Return KRW guru advisor ontology status",
    annotations=READ_ONLY,
)
async def krw_guru_status(
    root: str | None = None,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Return reviewed guru ontology counts, files, and curation quality metadata."""
    return await _run_tool(
        guru_status_tool,
        root=Path(root).expanduser() if root else None,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_index_context",
    title="Return KRW guru shard index status",
    annotations=READ_ONLY,
)
async def krw_guru_index_context(
    root: str | None = None,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Return author-shard index status and runtime policy for Guru MCP."""
    return await _run_tool(
        guru_index_context_tool,
        root=Path(root).expanduser() if root else None,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_search",
    title="Search reviewed KRW guru ontology",
    annotations=READ_ONLY,
)
async def krw_guru_search(
    query: str,
    root: str | None = None,
    author_keys: list[str] | None = None,
    object_types: list[str] | None = None,
    intent_family: str | None = None,
    decision_stage: str | None = None,
    requires_company_data: bool | None = None,
    limit: int = 10,
    offset: int = 0,
    include_relationships: bool = False,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Search guru lens, consultation, and data-need objects without company facts."""
    return await _run_tool(
        guru_search_tool,
        query=query,
        root=Path(root).expanduser() if root else None,
        author_keys=author_keys,
        object_types=object_types,
        intent_family=intent_family,
        decision_stage=decision_stage,
        requires_company_data=requires_company_data,
        limit=limit,
        offset=offset,
        include_relationships=include_relationships,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_query_context",
    title="Build guru advisor research pack",
    annotations=READ_ONLY,
)
async def krw_guru_query_context(
    question: str,
    root: str | None = None,
    author_keys: list[str] | None = None,
    ticker: str | None = None,
    company_context_json: str | None = None,
    intent_family: str | None = None,
    limit_lens: int = 4,
    limit_consultation: int = 3,
    limit_data_needs: int = 5,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Return a compact ResearchPack with answerability and allowed next tools."""
    return await _run_tool(
        guru_query_context_tool,
        question=question,
        root=Path(root).expanduser() if root else None,
        author_keys=author_keys,
        ticker=ticker,
        company_context_json=company_context_json,
        intent_family=intent_family,
        limit_lens=limit_lens,
        limit_consultation=limit_consultation,
        limit_data_needs=limit_data_needs,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_company_brief",
    title="Build KRW company filing brief from guru lenses",
    annotations=READ_ONLY,
)
async def krw_guru_company_brief(
    question: str,
    root: str | None = None,
    author_keys: list[str] | None = None,
    ticker: str | None = None,
    company_name: str | None = None,
    company_context_json: str | None = None,
    intent_family: str | None = None,
    limit_lens: int = 4,
    limit_consultation: int = 3,
    limit_data_needs: int = 5,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Return a company filing research brief derived from guru ontology needs."""
    return await _run_tool(
        guru_company_brief_tool,
        question=question,
        root=Path(root).expanduser() if root else None,
        author_keys=author_keys,
        ticker=ticker,
        company_name=company_name,
        company_context_json=company_context_json,
        intent_family=intent_family,
        limit_lens=limit_lens,
        limit_consultation=limit_consultation,
        limit_data_needs=limit_data_needs,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_company_pack",
    title="Build GuruCompanyResearchPack from guru lenses and company evidence",
    annotations=READ_ONLY,
)
async def krw_guru_company_pack(
    question: str,
    root: str | None = None,
    author_keys: list[str] | None = None,
    ticker: str | None = None,
    company_name: str | None = None,
    company_payload_json: str | None = None,
    company_context_json: str | None = None,
    intent_family: str | None = None,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Return internal company-aware guru answer pack plus render plan."""
    return await _run_tool(
        guru_company_pack_tool,
        question=question,
        root=Path(root).expanduser() if root else None,
        author_keys=author_keys,
        ticker=ticker,
        company_name=company_name,
        company_payload_json=company_payload_json,
        company_context_json=company_context_json,
        intent_family=intent_family,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_select_lenses",
    title="Select relevant guru ontology lenses",
    annotations=READ_ONLY,
)
async def krw_guru_select_lenses(
    question: str,
    root: str | None = None,
    author_keys: list[str] | None = None,
    ticker: str | None = None,
    company_context_json: str | None = None,
    intent_family: str | None = None,
    limit: int = 5,
    data_need_limit: int = 6,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Select guru lenses and evidence hooks before answer generation."""
    return await _run_tool(
        guru_select_lenses_tool,
        question=question,
        root=Path(root).expanduser() if root else None,
        author_keys=author_keys,
        ticker=ticker,
        company_context_json=company_context_json,
        intent_family=intent_family,
        limit=limit,
        data_need_limit=data_need_limit,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_context",
    title="Build guru advisor consultation context",
    annotations=READ_ONLY,
)
async def krw_guru_context(
    question: str,
    root: str | None = None,
    author_keys: list[str] | None = None,
    ticker: str | None = None,
    intent_family: str | None = None,
    limit_lens: int = 6,
    limit_consultation: int = 4,
    limit_data_needs: int = 8,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Build a compact pack of guru lenses, playbooks, and data needs for a question."""
    return await _run_tool(
        guru_context_tool,
        question=question,
        root=Path(root).expanduser() if root else None,
        author_keys=author_keys,
        ticker=ticker,
        intent_family=intent_family,
        limit_lens=limit_lens,
        limit_consultation=limit_consultation,
        limit_data_needs=limit_data_needs,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_trace",
    title="Trace one guru ontology object",
    annotations=READ_ONLY,
)
async def krw_guru_trace(
    reviewed_id: str,
    root: str | None = None,
    include_related: bool = True,
    include_span_metadata: bool = True,
    include_private_excerpt: bool = False,
    max_excerpt_words: int = 25,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Trace one selected guru object to source support and bounded neighbors."""
    return await _run_tool(
        guru_trace_tool,
        reviewed_id=reviewed_id,
        root=Path(root).expanduser() if root else None,
        include_related=include_related,
        include_span_metadata=include_span_metadata,
        include_private_excerpt=include_private_excerpt,
        max_excerpt_words=max_excerpt_words,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_chain",
    title="Return bounded guru ontology chain",
    annotations=READ_ONLY,
)
async def krw_guru_chain(
    reviewed_id: str,
    root: str | None = None,
    max_neighbors: int = 8,
    include_relationships: bool = False,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Return compact relationship neighbors around one selected guru object."""
    return await _run_tool(
        guru_chain_tool,
        reviewed_id=reviewed_id,
        root=Path(root).expanduser() if root else None,
        max_neighbors=max_neighbors,
        include_relationships=include_relationships,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_evidence",
    title="Return guru object source support",
    annotations=READ_ONLY,
)
async def krw_guru_evidence(
    reviewed_id: str,
    root: str | None = None,
    include_related: bool = True,
    include_span_metadata: bool = True,
    include_private_excerpt: bool = False,
    max_excerpt_words: int = 25,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Return one reviewed guru object with relationships and support-span metadata."""
    return await _run_tool(
        guru_evidence_tool,
        reviewed_id=reviewed_id,
        root=Path(root).expanduser() if root else None,
        include_related=include_related,
        include_span_metadata=include_span_metadata,
        include_private_excerpt=include_private_excerpt,
        max_excerpt_words=max_excerpt_words,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_data_needs",
    title="Return filing evidence needs for guru consultation",
    annotations=READ_ONLY,
)
async def krw_guru_data_needs(
    question: str,
    root: str | None = None,
    author_keys: list[str] | None = None,
    intent_family: str | None = None,
    limit: int = 10,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Return guru-derived evidence requirements for existing KRW Ontology filing research."""
    return await _run_tool(
        guru_data_needs_tool,
        question=question,
        root=Path(root).expanduser() if root else None,
        author_keys=author_keys,
        intent_family=intent_family,
        limit=limit,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_eval_questions",
    title="List KRW guru advisor eval questions",
    annotations=READ_ONLY,
)
async def krw_guru_eval_questions(
    lens: str | None = None,
    requires_company_evidence: bool | None = None,
    intent: str | None = None,
    limit: int = 25,
    offset: int = 0,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> str:
    """Return realistic investor consultation eval cases for guru advisor testing."""
    return await _run_tool(
        guru_eval_questions_tool,
        lens=lens,
        requires_company_evidence=requires_company_evidence,
        intent=intent,
        limit=limit,
        offset=offset,
        response_format=response_format,
    )


def main() -> None:
    """Run the guru advisor MCP server over stdio."""
    mcp.run("stdio")


if __name__ == "__main__":
    main()
