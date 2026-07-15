"""stdio MCP server exposing read-only guru advisor tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError

from krw_ontology.guru.company_bridge import (
    GuruEvidenceAnalysisValidationError,
    validate_guru_agent_evidence_analysis,
)
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
    guru_review_company_evidence_tool,
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
    company_context: dict[str, Any] | None = None,
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
        company_context=company_context,
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
    company_context: dict[str, Any] | None = None,
    guru_query_context: dict[str, Any] | None = None,
    investigation_questions: list[dict[str, Any]] | None = None,
    intent_family: str | None = None,
    limit_lens: int = 4,
    limit_consultation: int = 3,
    limit_data_needs: int = 5,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> CallToolResult:
    """Return a company filing brief or an in-run key-question correction.

    The generated Guru workflow submits exactly one philosophy-shaped key
    question. Malformed input is returned as English structured JSON so the
    active SDK run can correct it without a restart or a server-authored
    replacement question.
    """
    if investigation_questions is not None and len(investigation_questions) != 1:
        return _guru_input_correction_result(
            code="exactly_one_key_question_required",
            message=(
                "The Guru company workflow accepts exactly one philosophy-shaped key question."
            ),
            required_change=(
                "Provide investigation_questions as an array with exactly one draft. "
                "Keep separate filing proof needs in that draft's evidence_needed field, "
                "not as additional questions."
            ),
            invalid_fields=["investigation_questions"],
            allowed_next_tools=["krw_guru_company_brief"],
        )
    try:
        result = await _run_tool(
            guru_company_brief_tool,
            question=question,
            root=Path(root).expanduser() if root else None,
            author_keys=author_keys,
            ticker=ticker,
            company_name=company_name,
            company_context=company_context,
            guru_query_context=guru_query_context,
            investigation_questions=investigation_questions,
            intent_family=intent_family,
            limit_lens=limit_lens,
            limit_consultation=limit_consultation,
            limit_data_needs=limit_data_needs,
            response_format=response_format,
        )
    except (ValidationError, ValueError) as exc:
        if investigation_questions is None:
            raise
        return _guru_input_correction_result(
            code="invalid_key_question",
            message=f"The key question is invalid: {exc}",
            required_change=(
                "Keep exactly one draft with decision_role=main_tension. Use only returned "
                "philosophy principle ids and trusted company context anchor ids, and provide "
                "all required draft fields."
            ),
            invalid_fields=["investigation_questions"],
            allowed_next_tools=["krw_guru_company_brief"],
        )
    structured_content: dict[str, Any] | None = None
    try:
        candidate = json.loads(result)
        if isinstance(candidate, dict):
            structured_content = candidate
    except json.JSONDecodeError:
        pass
    return CallToolResult(
        content=[TextContent(type="text", text=result)],
        structuredContent=structured_content,
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
    company_payload: dict[str, Any] | None = None,
    company_context: dict[str, Any] | None = None,
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
        company_payload=company_payload,
        company_context=company_context,
        intent_family=intent_family,
        response_format=response_format,
    )


@mcp.tool(
    name="krw_guru_review_company_evidence",
    title="Review company evidence through guru lenses",
    annotations=READ_ONLY,
)
async def krw_guru_review_company_evidence(
    question: str,
    root: str | None = None,
    author_keys: list[str] | None = None,
    ticker: str | None = None,
    company_name: str | None = None,
    company_payload: dict[str, Any] | None = None,
    company_research_context: dict[str, Any] | None = None,
    investigation_brief: dict[str, Any] | None = None,
    agent_analysis: dict[str, Any] | None = None,
    company_context: dict[str, Any] | None = None,
    intent_family: str | None = None,
    response_format: GuruResponseFormat = GuruResponseFormat.JSON,
) -> CallToolResult:
    """Return internal guru interpretation guidance after company evidence.

    When a sealed brief is used, malformed or incomplete ``agent_analysis``
    returns an English structured MCP error in the active SDK run. The server
    does not repair the analysis, restart the run, or generate a replacement
    answer.
    """
    if investigation_brief is not None or agent_analysis is not None:
        if investigation_brief is None:
            return _guru_input_correction_result(
                code="missing_investigation_brief",
                message=("agent_analysis was supplied without the sealed investigation_brief."),
                required_change=(
                    "Call krw_guru_review_company_evidence after the runtime has attached "
                    "the sealed brief, and provide agent_analysis only for that brief."
                ),
                invalid_fields=["investigation_brief", "agent_analysis"],
            )
        if agent_analysis is None:
            return _guru_input_correction_result(
                code="missing_agent_analysis",
                message=("agent_analysis is required for the sealed key question."),
                required_change=(
                    "Provide one assessment for the sealed key question and a complete "
                    "overall_judgment."
                ),
                invalid_fields=["agent_analysis"],
            )
        if company_research_context is None:
            return _guru_input_correction_result(
                code="missing_company_research_context",
                message=(
                    "company_research_context is required for the sealed key question."
                ),
                required_change=(
                    "Call krw_guru_review_company_evidence after the runtime has attached "
                    "the company research context, and provide agent_analysis only for "
                    "that sealed brief."
                ),
                invalid_fields=["company_research_context"],
            )
        try:
            validate_guru_agent_evidence_analysis(
                investigation_brief=investigation_brief,
                company_research_context=company_research_context or {},
                agent_analysis=agent_analysis,
            )
        except GuruEvidenceAnalysisValidationError as exc:
            return _guru_input_correction_result(
                code=exc.code,
                message=str(exc),
                required_change=exc.required_change,
                invalid_fields=exc.invalid_fields,
                violations=exc.violations,
            )
        except (ValidationError, ValueError) as exc:
            return _guru_input_correction_result(
                code="invalid_agent_analysis",
                message=f"agent_analysis is invalid: {exc}",
                required_change=(
                    "Correct agent_analysis so it assesses the sealed key question exactly "
                    "once, cites only source_object_ids from company_research_context, "
                    "and uses mixed or unresolved verdicts."
                ),
                invalid_fields=["agent_analysis", "company_research_context"],
            )
    result = await _run_tool(
        guru_review_company_evidence_tool,
        question=question,
        root=Path(root).expanduser() if root else None,
        author_keys=author_keys,
        ticker=ticker,
        company_name=company_name,
        company_payload=company_payload,
        company_research_context=company_research_context,
        investigation_brief=investigation_brief,
        agent_analysis=agent_analysis,
        company_context=company_context,
        intent_family=intent_family,
        response_format=response_format,
    )
    structured_content: dict[str, Any] | None = None
    try:
        candidate = json.loads(result)
        if isinstance(candidate, dict):
            structured_content = candidate
    except json.JSONDecodeError:
        pass
    return CallToolResult(
        content=[TextContent(type="text", text=result)],
        structuredContent=structured_content,
    )


def _guru_input_correction_result(
    *,
    code: str,
    message: str,
    required_change: str,
    invalid_fields: list[str],
    violations: list[dict[str, Any]] | None = None,
    allowed_next_tools: list[str] | None = None,
) -> CallToolResult:
    """Return a machine-readable correction without leaving the SDK run."""
    payload = {
        "status": "input_correction_required",
        "code": code,
        "message": message,
        "required_change": required_change,
        "invalid_fields": invalid_fields,
        "allowed_next_tools": allowed_next_tools
        or ["krw_guru_review_company_evidence"],
    }
    if violations:
        payload["violations"] = violations
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
    name="krw_guru_select_lenses",
    title="Select relevant guru ontology lenses",
    annotations=READ_ONLY,
)
async def krw_guru_select_lenses(
    question: str,
    root: str | None = None,
    author_keys: list[str] | None = None,
    ticker: str | None = None,
    company_context: dict[str, Any] | None = None,
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
        company_context=company_context,
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
