"""Claude Agent SDK adapters for agent retrieval planning and reranking."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from typing import Any, Callable

from claude_agent_sdk import ClaudeAgentOptions, query

from krw_ontology.agent_index.retriever import QueryPlan
from krw_ontology.agent_index.store import DEFAULT_QUERY_TYPES

StructuredRunner = Callable[[str, dict[str, Any]], dict[str, Any]]

_ALLOWED_INTENTS = {"evidence_search", "compare", "quality_check"}
_ALLOWED_DOC_TYPES = {"10-K", "10-Q"}
_ALLOWED_OBJECT_TYPES = set(DEFAULT_QUERY_TYPES)
_ALLOWED_PERIOD_POLICIES = {"all", "latest"}


class ClaudeAgentQueryPlanner:
    """Use Claude Agent SDK to convert a question into a constrained QueryPlan."""

    def __init__(
        self,
        *,
        model: str | None = None,
        cwd: Path | str | None = None,
        max_turns: int = 3,
        timeout_seconds: float = 90,
        structured_runner: StructuredRunner | None = None,
    ):
        self.model = model
        self.cwd = Path(cwd) if cwd is not None else None
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self._structured_runner = structured_runner

    def plan(self, question: str, catalog: dict[str, Any]) -> QueryPlan:
        prompt = _planner_prompt(question, catalog)
        data = self._run_structured(prompt, _query_plan_schema())
        return _coerce_query_plan(question, catalog, data)

    def _run_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self._structured_runner:
            return self._structured_runner(prompt, schema)
        return _run_async(
            _query_structured(
                prompt,
                schema,
                model=self.model,
                cwd=self.cwd,
                max_turns=self.max_turns,
                timeout_seconds=self.timeout_seconds,
                system_prompt=(
                    "You are an ontology retrieval planner. Return only the requested "
                    "structured output. Do not use tools. Do not invent tickers, periods, "
                    "or document types outside the provided catalog."
                ),
            )
        )


class ClaudeAgentReranker:
    """Use Claude Agent SDK to filter/rerank candidate evidence bundles."""

    def __init__(
        self,
        *,
        model: str | None = None,
        cwd: Path | str | None = None,
        max_turns: int = 3,
        timeout_seconds: float = 90,
        max_candidates: int = 30,
        structured_runner: StructuredRunner | None = None,
    ):
        self.model = model
        self.cwd = Path(cwd) if cwd is not None else None
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self.max_candidates = max_candidates
        self._structured_runner = structured_runner

    def __call__(self, candidates: list[dict[str, Any]], plan: QueryPlan) -> list[dict[str, Any]]:
        if not candidates:
            return []
        limited = candidates[: self.max_candidates]
        prompt = _reranker_prompt(limited, plan)
        data = self._run_structured(prompt, _rerank_schema())
        selected_ids = [
            item.get("id")
            for item in data.get("selected", [])
            if isinstance(item, dict) and item.get("id")
        ]
        if not selected_ids:
            return limited
        by_id = {candidate["id"]: candidate for candidate in candidates if candidate.get("id")}
        return [by_id[candidate_id] for candidate_id in selected_ids if candidate_id in by_id]

    def _run_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self._structured_runner:
            return self._structured_runner(prompt, schema)
        return _run_async(
            _query_structured(
                prompt,
                schema,
                model=self.model,
                cwd=self.cwd,
                max_turns=self.max_turns,
                timeout_seconds=self.timeout_seconds,
                system_prompt=(
                    "You are an ontology evidence reranker. Return only the requested "
                    "structured output. Do not use tools. Prefer direct, source-grounded "
                    "evidence over loosely related text."
                ),
            )
        )


async def _query_structured(
    prompt: str,
    schema: dict[str, Any],
    *,
    model: str | None,
    cwd: Path | None,
    max_turns: int,
    timeout_seconds: float,
    system_prompt: str,
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            _query_structured_inner(
                prompt,
                schema,
                model=model,
                cwd=cwd,
                max_turns=max_turns,
                system_prompt=system_prompt,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise TimeoutError(
            f"Claude Agent SDK call exceeded {timeout_seconds:g}s"
        ) from exc


async def _query_structured_inner(
    prompt: str,
    schema: dict[str, Any],
    *,
    model: str | None,
    cwd: Path | None,
    max_turns: int,
    system_prompt: str,
) -> dict[str, Any]:
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        tools=[],
        allowed_tools=[],
        max_turns=max_turns,
        model=model,
        cwd=str(cwd) if cwd else None,
        output_format={"type": "json_schema", "schema": schema},
    )
    result_payload: dict[str, Any] | None = None
    async for message in query(prompt=prompt, options=options):
        if type(message).__name__ != "ResultMessage":
            continue
        if getattr(message, "is_error", False):
            errors = getattr(message, "errors", None)
            raise RuntimeError(f"Claude Agent SDK returned an error: {errors or message}")
        structured_output = getattr(message, "structured_output", None)
        if structured_output is not None:
            result_payload = structured_output
        elif getattr(message, "result", None):
            result_payload = json.loads(message.result)
    if result_payload is None:
        raise RuntimeError("Claude Agent SDK returned no structured output")
    if not isinstance(result_payload, dict):
        raise RuntimeError("Claude Agent SDK structured output was not an object")
    return result_payload


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            error["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error["error"]
    return result.get("value")


def _planner_prompt(question: str, catalog: dict[str, Any]) -> str:
    compact_docs = [
        {
            "ticker": doc["ticker"],
            "document_type": doc["document_type"],
            "period": doc["period"],
            "section_quality": doc.get("section_quality_status"),
        }
        for doc in catalog.get("documents", [])[:300]
    ]
    payload = {
        "question": question,
        "catalog": {
            "companies": catalog.get("companies", []),
            "document_types": catalog.get("document_types", []),
            "documents": compact_docs,
        },
        "rules": [
            "Use only tickers that appear in catalog.companies.",
            "Use only document_types that appear in catalog.document_types.",
            "If the user asks for recent/latest/current, set period_policy to latest.",
            "Use include_rejected only when the user explicitly asks for rejected objects.",
            "Choose compare only for multi-company or explicit comparison questions.",
            "Choose quality_check for quality, rejected, failure, or section-quality questions.",
            "Return topics as concrete English search phrases.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _reranker_prompt(candidates: list[dict[str, Any]], plan: QueryPlan) -> str:
    compact_candidates = [
        {
            "id": candidate.get("id"),
            "type": candidate.get("type"),
            "ticker": candidate.get("ticker"),
            "document_type": candidate.get("document_type"),
            "period": candidate.get("period"),
            "text": candidate.get("text"),
            "quote_texts": [
                quote.get("text")
                for quote in candidate.get("evidence", {}).get("quotes", [])[:3]
            ],
            "quality": candidate.get("quality"),
        }
        for candidate in candidates
    ]
    payload = {
        "query_plan": plan.to_dict(),
        "candidates": compact_candidates,
        "instructions": [
            "Select only candidates directly relevant to the query plan.",
            "Keep source-grounded candidates with supporting quotes.",
            "Drop loosely related or wrong-period candidates.",
            "Return selected candidates in descending relevance order.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _query_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": sorted(_ALLOWED_INTENTS)},
            "tickers": {"type": "array", "items": {"type": "string"}},
            "document_types": {"type": "array", "items": {"type": "string", "enum": sorted(_ALLOWED_DOC_TYPES)}},
            "periods": {"type": "array", "items": {"type": "string"}},
            "period_policy": {"type": "string", "enum": sorted(_ALLOWED_PERIOD_POLICIES)},
            "topics": {"type": "array", "items": {"type": "string"}},
            "metric": {"type": ["string", "null"]},
            "object_types": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(_ALLOWED_OBJECT_TYPES)},
            },
            "include_rejected": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": [
            "intent",
            "tickers",
            "document_types",
            "periods",
            "period_policy",
            "topics",
            "metric",
            "object_types",
            "include_rejected",
            "limit",
        ],
        "additionalProperties": False,
    }


def _rerank_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "selected": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["selected"],
        "additionalProperties": False,
    }


def _coerce_query_plan(
    question: str,
    catalog: dict[str, Any],
    data: dict[str, Any],
) -> QueryPlan:
    catalog_companies = set(catalog.get("companies", []))
    catalog_doc_types = set(catalog.get("document_types", [])) or _ALLOWED_DOC_TYPES
    intent = data.get("intent") if data.get("intent") in _ALLOWED_INTENTS else "evidence_search"
    period_policy = (
        data.get("period_policy")
        if data.get("period_policy") in _ALLOWED_PERIOD_POLICIES
        else "all"
    )
    tickers = [
        ticker.upper()
        for ticker in data.get("tickers", [])
        if isinstance(ticker, str) and ticker.upper() in catalog_companies
    ]
    document_types = [
        doc_type
        for doc_type in data.get("document_types", [])
        if isinstance(doc_type, str) and doc_type in catalog_doc_types
    ]
    object_types = [
        object_type
        for object_type in data.get("object_types", [])
        if isinstance(object_type, str) and object_type in _ALLOWED_OBJECT_TYPES
    ] or list(DEFAULT_QUERY_TYPES)
    topics = [
        topic.strip()
        for topic in data.get("topics", [])
        if isinstance(topic, str) and topic.strip()
    ]
    periods = [
        period.upper()
        for period in data.get("periods", [])
        if isinstance(period, str) and period.strip()
    ]
    metric = data.get("metric")
    if not isinstance(metric, str) or not metric.strip():
        metric = None
    limit = data.get("limit", 20)
    if not isinstance(limit, int):
        limit = 20
    return QueryPlan(
        question=question,
        intent=intent,
        tickers=tickers,
        document_types=document_types,
        periods=periods,
        period_policy=period_policy,
        topics=topics,
        metric=metric,
        object_types=object_types,
        include_rejected=bool(data.get("include_rejected", False)),
        limit=max(1, min(limit, 50)),
    )
