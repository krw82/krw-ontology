"""Read-only MCP tool implementations over the ontology agent index."""

from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
from typing import Any

from krw_ontology.agent_index import AgentRetriever, OntologyStore, QueryPlan
from krw_ontology.agent_index.store import DEFAULT_QUERY_TYPES
from krw_ontology.config.paths import ONTOLOGY_ROOT_ENV, resolve_agent_index_path, resolve_ontology_root


class ResponseFormat(str, Enum):
    """Supported MCP response formats."""

    JSON = "json"
    MARKDOWN = "markdown"


class ResponseDetail(str, Enum):
    """How much object/evidence detail to return from search-style tools."""

    COMPACT = "compact"
    FULL = "full"


DEFAULT_LIMIT = 10
MAX_LIMIT = 50
MAX_OFFSET = 10_000
MAX_COMPARE_TICKERS = 20
MAX_COMPACT_CLAIMS = 3
MAX_COMPACT_QUOTES = 3
MAX_COMPACT_SPANS = 1
MAX_COMPACT_RELATED_OBJECTS = 5

_ALLOWED_OBJECT_TYPES = set(DEFAULT_QUERY_TYPES) | {
    "FinancialMetricValue",
    "DerivedMetricValue",
    "NumericEvidence",
    "CalculatedNumericSupport",
    "XBRLFact",
    "BusinessActivity",
    "ExternalFactorExposure",
    "CompanyBusinessProfile",
    "TemporalLink",
    "TrendObservation",
    "ChangeEvent",
}
_OBJECT_TYPE_ALIASES = {
    "quote": ("EvidenceQuote",),
    "quotes": ("EvidenceQuote",),
    "evidencequote": ("EvidenceQuote",),
    "evidence_quote": ("EvidenceQuote",),
    "claim": ("ResearchClaim",),
    "claims": ("ResearchClaim",),
    "researchclaim": ("ResearchClaim",),
    "research_claim": ("ResearchClaim",),
    "risk": ("RiskFactor",),
    "risks": ("RiskFactor",),
    "riskfactor": ("RiskFactor",),
    "risk_factor": ("RiskFactor",),
    "growth": ("GrowthDriver",),
    "driver": ("GrowthDriver",),
    "drivers": ("GrowthDriver",),
    "growthdriver": ("GrowthDriver",),
    "growth_driver": ("GrowthDriver",),
    "headwind": ("Headwind",),
    "headwinds": ("Headwind",),
    "assumption": ("AssumptionCandidate",),
    "assumptions": ("AssumptionCandidate",),
    "assumptioncandidate": ("AssumptionCandidate",),
    "assumption_candidate": ("AssumptionCandidate",),
    "activity": ("BusinessActivity",),
    "businessactivity": ("BusinessActivity",),
    "business_activity": ("BusinessActivity",),
    "business": ("BusinessActivity", "CompanyBusinessProfile"),
    "exposure": ("ExternalFactorExposure",),
    "exposures": ("ExternalFactorExposure",),
    "externalfactorexposure": ("ExternalFactorExposure",),
    "external_factor_exposure": ("ExternalFactorExposure",),
    "profile": ("CompanyBusinessProfile",),
    "companyprofile": ("CompanyBusinessProfile",),
    "company_profile": ("CompanyBusinessProfile",),
    "companybusinessprofile": ("CompanyBusinessProfile",),
    "company_business_profile": ("CompanyBusinessProfile",),
    "temporal": ("TemporalLink", "TrendObservation", "ChangeEvent"),
    "temporallink": ("TemporalLink",),
    "temporal_link": ("TemporalLink",),
    "trend": ("TrendObservation",),
    "trendobservation": ("TrendObservation",),
    "trend_observation": ("TrendObservation",),
    "change": ("ChangeEvent",),
    "event": ("ChangeEvent",),
    "changeevent": ("ChangeEvent",),
    "change_event": ("ChangeEvent",),
    "metric": ("FinancialMetricValue", "DerivedMetricValue"),
    "metrics": ("FinancialMetricValue", "DerivedMetricValue"),
    "financialmetric": ("FinancialMetricValue", "DerivedMetricValue"),
    "financial_metric": ("FinancialMetricValue", "DerivedMetricValue"),
    "financialmetrics": ("FinancialMetricValue", "DerivedMetricValue"),
    "financial_metrics": ("FinancialMetricValue", "DerivedMetricValue"),
    "financialmetricvalue": ("FinancialMetricValue",),
    "financial_metric_value": ("FinancialMetricValue",),
    "derivedmetricvalue": ("DerivedMetricValue",),
    "derived_metric_value": ("DerivedMetricValue",),
    "xbrl": ("XBRLFact",),
    "xbrlfact": ("XBRLFact",),
    "xbrl_fact": ("XBRLFact",),
}


def catalog_tool(
    *,
    root: str | None = None,
    index_path: str | None = None,
    ticker: str | None = None,
    document_types: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return available companies, documents, periods, and index metadata."""
    root_path = _root(root)
    index = _index(root, index_path)
    limit = _bounded_limit(limit)
    offset = _bounded_offset(offset)

    with _store(index) as store:
        documents = store.list_documents(ticker=ticker, document_types=document_types)
        companies = store.list_companies()

    total = len(documents)
    page = documents[offset : offset + limit]
    payload = {
        "root": str(root_path),
        "index_path": str(index),
        "companies": companies,
        "document_types": sorted({doc["document_type"] for doc in documents}),
        "periods": sorted({doc["period"] for doc in documents}),
        "documents": page,
        "pagination": _pagination(total, offset, len(page), limit),
    }
    return _format_response(payload, response_format, _markdown_catalog)


def query_tool(
    *,
    root: str | None = None,
    index_path: str | None = None,
    topic: str | None = None,
    tickers: list[str] | None = None,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    object_types: list[str] | None = None,
    include_rejected: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.JSON,
    response_detail: ResponseDetail = ResponseDetail.COMPACT,
) -> str:
    """Search accepted ontology objects and return evidence bundles."""
    index = _index(root, index_path)
    limit = _bounded_limit(limit)
    offset = _bounded_offset(offset)
    fetch_limit = min(MAX_LIMIT + offset + 1, offset + limit + 1)
    normalized_object_types, invalid_object_types = _normalize_object_types(object_types)
    if invalid_object_types:
        payload = _error_payload(
            "invalid_object_type",
            f"Unsupported object_types: {', '.join(invalid_object_types)}",
            "Use canonical types or aliases. Supported canonical types: "
            + ", ".join(sorted(_ALLOWED_OBJECT_TYPES)),
        )
        return _format_response(payload, response_format, _markdown_error)

    with _store(index) as store:
        bundles, search_diagnostics = store.query_with_diagnostics(
            topic=topic,
            tickers=tickers,
            document_types=document_types,
            periods=periods,
            object_types=normalized_object_types,
            include_rejected=include_rejected,
            limit=fetch_limit,
        )

    page = bundles[offset : offset + limit]
    detail = _coerce_response_detail(response_detail)
    results = page if detail == ResponseDetail.FULL else [_compact_bundle(bundle) for bundle in page]
    payload = {
        "query": {
            "topic": topic,
            "tickers": _upper_list(tickers),
            "document_types": document_types or [],
            "periods": _upper_list(periods),
            "object_types": normalized_object_types or [],
            "object_types_requested": object_types or [],
            "include_rejected": include_rejected,
        },
        "response_detail": detail.value,
        "search_diagnostics": search_diagnostics,
        "results": results,
        "pagination": _pagination(len(bundles), offset, len(page), limit),
    }
    return _format_response(payload, response_format, _markdown_bundles)


def topic_map_tool(
    *,
    root: str | None = None,
    index_path: str | None = None,
    ticker: str,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return company-specific search vocabulary for broad research questions."""
    index = _index(root, index_path)
    limit = _bounded_limit(limit)
    with _store(index) as store:
        payload = store.topic_map(
            ticker=ticker,
            document_types=document_types,
            periods=periods,
            limit=limit,
        )
    return _format_response(payload, response_format, _markdown_topic_map)


def retrieve_tool(
    *,
    question: str,
    root: str | None = None,
    index_path: str | None = None,
    tickers: list[str] | None = None,
    document_types: list[str] | None = None,
    periods: list[str] | None = None,
    include_rejected: bool | None = None,
    limit: int = DEFAULT_LIMIT,
    response_format: ResponseFormat = ResponseFormat.JSON,
    response_detail: ResponseDetail = ResponseDetail.COMPACT,
) -> str:
    """Use the deterministic local planner, then retrieve evidence bundles."""
    index = _index(root, index_path)
    limit = _bounded_limit(limit)
    with _store(index) as store:
        result = AgentRetriever(store).retrieve(
            question,
            tickers=tickers,
            document_types=document_types,
            periods=periods,
            include_rejected=include_rejected,
            limit=limit,
        )
    detail = _coerce_response_detail(response_detail)
    payload = result if detail == ResponseDetail.FULL else _compact_retrieval(result)
    payload["response_detail"] = detail.value
    return _format_response(payload, response_format, _markdown_retrieve)


def trace_tool(
    *,
    object_id: str,
    root: str | None = None,
    index_path: str | None = None,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Trace one ontology object to its source document, quotes, spans, and quality."""
    index = _index(root, index_path)
    with _store(index) as store:
        trace = store.trace(object_id)
        resolved_from_prefix = None
        candidates = []
        if trace is None:
            candidates = store.find_object_ids(object_id, limit=11)
            if len(candidates) == 1:
                resolved_from_prefix = object_id
                object_id = candidates[0]["id"]
                trace = store.trace(object_id)
    if trace is None:
        if candidates:
            payload = _error_payload(
                "ambiguous_object_id",
                f"Object id prefix is ambiguous: {object_id}",
                "Pass one exact candidate id from candidates.",
            )
            payload["candidates"] = candidates[:10]
        else:
            payload = _error_payload(
                "not_found",
                f"Object not found: {object_id}",
                "Call krw_ontology_query first, then pass one returned id or a unique id prefix to trace.",
            )
    else:
        payload = trace
        if resolved_from_prefix:
            payload["resolved_from_prefix"] = resolved_from_prefix
    return _format_response(payload, response_format, _markdown_trace)


def chain_tool(
    *,
    object_id: str,
    root: str | None = None,
    index_path: str | None = None,
    max_depth: int = 2,
    direction: str = "both",
    include_quote_text: bool = False,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return compact evidence, semantic, and temporal chains around one object."""
    index = _index(root, index_path)
    max_depth = max(0, min(int(max_depth), 4))
    with _store(index) as store:
        chain = store.chain(
            object_id,
            max_depth=max_depth,
            direction=direction,
            include_quote_text=include_quote_text,
        )
        resolved_from_prefix = None
        candidates = []
        if chain is None:
            candidates = store.find_object_ids(object_id, limit=11)
            if len(candidates) == 1:
                resolved_from_prefix = object_id
                object_id = candidates[0]["id"]
                chain = store.chain(
                    object_id,
                    max_depth=max_depth,
                    direction=direction,
                    include_quote_text=include_quote_text,
                )
    if chain is None:
        if candidates:
            payload = _error_payload(
                "ambiguous_object_id",
                f"Object id prefix is ambiguous: {object_id}",
                "Pass one exact candidate id from candidates.",
            )
            payload["candidates"] = candidates[:10]
        else:
            payload = _error_payload(
                "not_found",
                f"Object not found: {object_id}",
                "Call krw_ontology_query first, then pass one returned id or a unique id prefix to chain.",
            )
    else:
        payload = chain
        if resolved_from_prefix:
            payload["resolved_from_prefix"] = resolved_from_prefix
    return _format_response(payload, response_format, _markdown_chain)


def quality_tool(
    *,
    root: str | None = None,
    index_path: str | None = None,
    ticker: str | None = None,
    document_type: str | None = None,
    period: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return section-quality, rejected-object, and batch-failure signals."""
    index = _index(root, index_path)
    limit = _bounded_limit(limit)
    offset = _bounded_offset(offset)
    with _store(index) as store:
        quality = store.quality(ticker=ticker, document_type=document_type, period=period)

    events = quality["events"]
    page = events[offset : offset + limit]
    payload = {
        "documents": quality["documents"],
        "events": page,
        "summary": quality["summary"],
        "pagination": _pagination(len(events), offset, len(page), limit),
    }
    return _format_response(payload, response_format, _markdown_quality)


def compare_tool(
    *,
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
    """Compare companies by a topic search or canonical metric."""
    period_compare = len(tickers) == 1 and len(periods or []) >= 2
    if len(tickers) < 2 and not period_compare:
        payload = _error_payload(
            "invalid_request",
            "Compare requires at least two tickers or one ticker with at least two periods.",
            "Pass tickers like ['VG', 'XOM'], or tickers=['VG'] with periods like ['FY2024', 'FY2025'].",
        )
        return _format_response(payload, response_format, _markdown_error)
    if len(tickers) > MAX_COMPARE_TICKERS:
        payload = _error_payload(
            "invalid_request",
            f"Compare supports at most {MAX_COMPARE_TICKERS} tickers per call.",
            "Split the comparison into smaller batches.",
        )
        return _format_response(payload, response_format, _markdown_error)

    index = _index(root, index_path)
    limit_per_ticker = max(1, min(int(limit_per_ticker), 10))
    with _store(index) as store:
        if period_compare:
            result = _compare_periods(
                store,
                ticker=tickers[0],
                topic=topic,
                metric=metric,
                document_types=document_types,
                periods=periods or [],
                limit_per_period=limit_per_ticker,
            )
        else:
            result = store.compare(
                tickers=tickers,
                topic=topic,
                metric=metric,
                document_types=document_types,
                periods=periods,
                limit_per_ticker=limit_per_ticker,
            )
    result["comparison_rows"] = _comparison_rows(result)
    detail = _coerce_response_detail(response_detail)
    payload = result if detail == ResponseDetail.FULL else _compact_compare(result)
    payload["response_detail"] = detail.value
    return _format_response(payload, response_format, _markdown_compare)


def plan_query_tool(
    *,
    question: str,
    root: str | None = None,
    index_path: str | None = None,
    response_format: ResponseFormat = ResponseFormat.JSON,
) -> str:
    """Return the deterministic QueryPlan for a natural-language question."""
    index = _index(root, index_path)
    with _store(index) as store:
        plan = AgentRetriever(store).plan(question)
    payload = {"plan": plan.to_dict()}
    return _format_response(payload, response_format, _markdown_plan)


def _compare_periods(
    store: OntologyStore,
    *,
    ticker: str,
    topic: str | None,
    metric: str | None,
    document_types: list[str] | None,
    periods: list[str],
    limit_per_period: int,
) -> dict[str, Any]:
    ticker = ticker.upper()
    results: dict[str, list[dict[str, Any]]] = {}
    for period in _upper_list(periods):
        if metric:
            period_result = store.compare(
                tickers=[ticker],
                metric=metric,
                document_types=document_types,
                periods=[period],
                limit_per_ticker=limit_per_period,
            )
            results[period] = period_result["results"].get(ticker, [])
        else:
            results[period] = store.query(
                topic=topic,
                tickers=[ticker],
                document_types=document_types,
                periods=[period],
                limit=limit_per_period,
            )
    return {
        "mode": "period_metric" if metric else "period_topic",
        "ticker": ticker,
        "topic": topic,
        "metric": metric,
        "periods": _upper_list(periods),
        "results": results,
    }


def _root(root: str | None) -> Path:
    return resolve_ontology_root(root, fallback_to_cwd=False)


def _index(root: str | None, index_path: str | None) -> Path:
    return resolve_agent_index_path(root, index_path, fallback_to_cwd=False)


def _store(index_path: Path) -> OntologyStore:
    if not index_path.exists():
        raise FileNotFoundError(
            "Ontology agent index not found at "
            f"{index_path}. Build it with: uv run krw-ontology build-agent-index "
            f"--root ${ONTOLOGY_ROOT_ENV}"
        )
    return OntologyStore(index_path)


def _coerce_response_detail(response_detail: ResponseDetail | str) -> ResponseDetail:
    try:
        return ResponseDetail(response_detail)
    except ValueError:
        return ResponseDetail.COMPACT


def _normalize_object_types(
    object_types: list[str] | None,
) -> tuple[list[str] | None, list[str]]:
    if not object_types:
        return None, []
    normalized: list[str] = []
    invalid: list[str] = []
    for raw_value in object_types:
        value = str(raw_value).strip()
        if not value:
            continue
        if value in _ALLOWED_OBJECT_TYPES:
            normalized.append(value)
            continue
        alias_key = value.lower().replace("-", "_").replace(" ", "_")
        mapped = _OBJECT_TYPE_ALIASES.get(alias_key)
        if mapped:
            normalized.extend(mapped)
            continue
        invalid.append(value)
    return _unique(normalized), invalid


def _compact_retrieval(result: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in result.items()
        if key not in {"results", "compare", "quality"}
    }
    results = result.get("results")
    if isinstance(results, list):
        payload["results"] = [_compact_bundle(item) for item in results]
    elif isinstance(results, dict):
        payload["results"] = {
            ticker: [_compact_bundle(item) for item in items]
            for ticker, items in results.items()
        }
    else:
        payload["results"] = results
    if "compare" in result:
        payload["compare"] = _compact_compare(result["compare"])
    if "quality" in result:
        payload["quality"] = _compact_quality_payload(result["quality"])
    return payload


def _compact_compare(result: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: value for key, value in result.items() if key != "results"},
        "results": {
            ticker: [_compact_bundle(item) for item in items]
            for ticker, items in result.get("results", {}).items()
        },
    }


def _comparison_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    results = result.get("results") or {}
    mode = result.get("mode")
    if mode in {"period_topic", "period_metric"}:
        ticker = result.get("ticker")
        for period, items in results.items():
            rows.append(
                _comparison_row(
                    comparison_key=str(period),
                    ticker=ticker,
                    period=str(period),
                    items=items,
                    topic=result.get("topic"),
                    metric=result.get("metric"),
                )
            )
        return rows

    for ticker, items in results.items():
        rows.append(
            _comparison_row(
                comparison_key=str(ticker),
                ticker=str(ticker),
                period=None,
                items=items,
                topic=result.get("topic"),
                metric=result.get("metric"),
            )
        )
    return rows


def _comparison_row(
    *,
    comparison_key: str,
    ticker: str | None,
    period: str | None,
    items: list[dict[str, Any]],
    topic: str | None,
    metric: str | None,
) -> dict[str, Any]:
    if not items:
        return {
            "comparison_key": comparison_key,
            "ticker": ticker,
            "period": period,
            "document_type": None,
            "topic": topic,
            "metric": metric,
            "source_label": None,
            "summary": "",
            "object_id": None,
            "object_type": None,
            "confidence": "unsupported",
            "evidence_grade": None,
            "evidence_counts": {"claims": 0, "quotes": 0, "spans": 0},
            "caveats": ["No matching ontology objects were returned for this comparison key."],
            "missing": True,
            "missing_reason": "no_matching_ontology_objects",
        }

    item = _best_comparison_item(items)
    obj = item.get("object") or {}
    evidence = item.get("evidence") or {}
    quality = item.get("quality") or {}
    row_period = period or item.get("period")
    document_type = item.get("document_type")
    row_ticker = ticker or item.get("ticker")
    return {
        "comparison_key": comparison_key,
        "ticker": row_ticker,
        "period": row_period,
        "document_type": document_type,
        "topic": topic,
        "metric": metric,
        "source_label": _source_label(row_ticker, row_period, document_type),
        "summary": _short_text(item.get("text"), 500),
        "object_id": item.get("id"),
        "object_type": item.get("type"),
        "confidence": obj.get("confidence") or "unknown",
        "evidence_grade": obj.get("evidence_grade") or quality.get("evidence_grade"),
        "evidence_counts": {
            "claims": len(evidence.get("claims") or []),
            "quotes": len(evidence.get("quotes") or []),
            "spans": len(evidence.get("spans") or []),
        },
        "caveats": _comparison_caveats(item),
        "missing": False,
        "missing_reason": None,
    }


def _best_comparison_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    return max(items, key=_comparison_item_score)


def _comparison_item_score(item: dict[str, Any]) -> tuple[int, int, int, str]:
    obj = item.get("object") or {}
    evidence = item.get("evidence") or {}
    grade_score = {
        "direct": 4,
        "indirect": 3,
        "derived": 2,
        "unknown": 1,
        "unsupported": 0,
    }.get(str(obj.get("evidence_grade") or "").lower(), 1)
    type_score = {
        "EvidenceQuote": 5,
        "ResearchClaim": 4,
        "ExternalFactorExposure": 3,
        "BusinessActivity": 3,
        "FinancialMetricValue": 3,
        "DerivedMetricValue": 3,
        "RiskFactor": 2,
        "GrowthDriver": 2,
        "Headwind": 2,
    }.get(str(item.get("type") or ""), 1)
    support_count = len(evidence.get("claims") or []) + len(evidence.get("quotes") or [])
    return (grade_score, type_score, support_count, str(item.get("id") or ""))


def _comparison_caveats(item: dict[str, Any]) -> list[str]:
    caveats: list[str] = []
    obj = item.get("object") or {}
    quality = item.get("quality") or {}
    evidence_grade = obj.get("evidence_grade") or quality.get("evidence_grade")
    if evidence_grade in {"derived", "unsupported"}:
        caveats.append(f"Evidence grade is {evidence_grade}.")
    if quality.get("section_quality") in {"warn", "fail"}:
        caveats.append(f"Section quality is {quality.get('section_quality')}.")
    if quality.get("events"):
        caveats.append("Quality events are present for the selected source.")
    return caveats


def _source_label(ticker: Any, period: Any, document_type: Any) -> str | None:
    parts = [str(value) for value in (ticker, period, document_type) if value]
    return " ".join(parts) if parts else None


def _compact_quality_payload(quality: dict[str, Any]) -> dict[str, Any]:
    documents = quality.get("documents", [])
    return {
        "documents": [_compact_document(doc) for doc in documents],
        "events": quality.get("events", [])[:10],
        "summary": quality.get("summary", {}),
    }


def _compact_bundle(item: dict[str, Any]) -> dict[str, Any]:
    evidence = item.get("evidence") or {}
    document = item.get("document") or {}
    quality = item.get("quality") or {}
    return {
        "id": item.get("id"),
        "type": item.get("type"),
        "ticker": item.get("ticker"),
        "document_type": item.get("document_type"),
        "period": item.get("period"),
        "section": item.get("section"),
        "text": _short_text(item.get("text"), 700),
        "trace_id": item.get("id"),
        "evidence": {
            "claims": [
                _compact_evidence_object(claim, max_chars=320)
                for claim in evidence.get("claims", [])[:MAX_COMPACT_CLAIMS]
            ],
            "quotes": [
                _compact_evidence_object(quote, max_chars=420)
                for quote in evidence.get("quotes", [])[:MAX_COMPACT_QUOTES]
            ],
            "spans": [
                _compact_evidence_object(span, max_chars=260)
                for span in evidence.get("spans", [])[:MAX_COMPACT_SPANS]
            ],
            "related_objects": [
                _compact_evidence_object(related, max_chars=320)
                for related in evidence.get("related_objects", [])[:MAX_COMPACT_RELATED_OBJECTS]
            ],
        },
        "quality": {
            "object_status": quality.get("object_status"),
            "section_quality": quality.get("section_quality"),
            "evidence_grade": (item.get("object") or {}).get("evidence_grade"),
            "quality_event_count": len(quality.get("events") or []),
            "events": [
                _compact_quality_event(event)
                for event in (quality.get("events") or [])[:3]
            ],
            "batch_failures": (document.get("counts") or {}).get("batch_failures", 0),
            "rejected_objects": (document.get("counts") or {}).get("rejected_objects", 0),
        },
    }


def _compact_evidence_object(obj: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    return {
        "id": obj.get("id"),
        "type": obj.get("type"),
        "ticker": obj.get("ticker"),
        "document_type": obj.get("document_type"),
        "period": obj.get("period"),
        "section": obj.get("section"),
        "text": _short_text(obj.get("text"), max_chars),
    }


def _compact_document(doc: dict[str, Any]) -> dict[str, Any]:
    counts = doc.get("counts") or {}
    return {
        "ticker": doc.get("ticker"),
        "document_type": doc.get("document_type"),
        "period": doc.get("period"),
        "section_quality_status": doc.get("section_quality_status"),
        "batch_failures": counts.get("batch_failures", 0),
        "rejected_objects": counts.get("rejected_objects", 0),
    }


def _compact_quality_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event.get("id"),
        "severity": event.get("severity"),
        "category": event.get("category"),
        "stage": event.get("stage"),
        "message": _short_text(event.get("message"), 260),
    }


def _bounded_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _bounded_offset(offset: int) -> int:
    return max(0, min(int(offset), MAX_OFFSET))


def _upper_list(values: list[str] | None) -> list[str]:
    return [value.upper() for value in values or []]


def _pagination(total_count: int, offset: int, count: int, limit: int) -> dict[str, Any]:
    next_offset = offset + count if total_count > offset + count else None
    return {
        "total_count": total_count,
        "count": count,
        "offset": offset,
        "limit": limit,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
    }


def _error_payload(code: str, message: str, suggestion: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "suggestion": suggestion,
        }
    }


def _format_response(
    payload: dict[str, Any],
    response_format: ResponseFormat,
    markdown_formatter: Any,
) -> str:
    if response_format == ResponseFormat.MARKDOWN:
        return markdown_formatter(payload)
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _markdown_catalog(payload: dict[str, Any]) -> str:
    lines = [
        "# Ontology Catalog",
        f"- Root: `{payload['root']}`",
        f"- Index: `{payload['index_path']}`",
        f"- Companies: {', '.join(payload['companies']) or '(none)'}",
        f"- Documents returned: {payload['pagination']['count']}",
    ]
    for doc in payload["documents"]:
        lines.append(
            f"- `{doc['ticker']}` `{doc['document_type']}` `{doc['period']}` "
            f"section_quality={doc.get('section_quality_status')}"
        )
    return "\n".join(lines)


def _markdown_bundles(payload: dict[str, Any]) -> str:
    lines = ["# Ontology Query Results", f"- Results: {payload['pagination']['count']}"]
    diagnostics = payload.get("search_diagnostics") or {}
    warnings = diagnostics.get("warnings") or []
    if warnings:
        lines.append(f"- Search warnings: {', '.join(warnings)}")
    if diagnostics.get("fts_query"):
        lines.append(f"- FTS query: `{diagnostics['fts_query']}`")
    for item in payload["results"]:
        lines.extend(_bundle_lines(item))
    return "\n".join(lines)


def _markdown_topic_map(payload: dict[str, Any]) -> str:
    lines = [
        "# Ontology Topic Map",
        f"- Ticker: `{payload.get('ticker')}`",
        f"- Profile IDs: {', '.join(payload.get('source', {}).get('company_business_profile_ids') or []) or '(none)'}",
        f"- Fallback used: {payload.get('source', {}).get('fallback_used')}",
    ]
    topics = payload.get("topics") or {}
    for section in ("external_factors", "business_activities", "metrics", "projects_assets"):
        entries = topics.get(section) or []
        lines.append(f"## {section}")
        if not entries:
            lines.append("- No entries")
            continue
        for entry in entries[:10]:
            search_terms = ", ".join(entry.get("search_terms") or [])
            lines.append(f"- `{entry.get('term')}` search_terms={search_terms}")
    suggestions = payload.get("suggested_first_queries") or []
    if suggestions:
        lines.append("## Suggested First Queries")
        for suggestion in suggestions[:10]:
            lines.append(
                f"- topic=`{suggestion.get('topic')}` object_types={suggestion.get('object_types')}"
            )
    return "\n".join(lines)


def _markdown_retrieve(payload: dict[str, Any]) -> str:
    lines = [
        "# Ontology Retrieval",
        f"- Answerable: {payload.get('answerable')}",
        f"- Plan: `{json.dumps(payload.get('plan', {}), ensure_ascii=False)}`",
    ]
    results = payload.get("results")
    if isinstance(results, list):
        for item in results:
            lines.extend(_bundle_lines(item))
    elif results:
        lines.append(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    return "\n".join(lines)


def _markdown_trace(payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return _markdown_error(payload)
    obj = payload.get("object", {})
    lines = [
        "# Ontology Trace",
        f"- Object: `{obj.get('id')}` ({obj.get('type')})",
        f"- Scope: `{obj.get('ticker')}` `{obj.get('document_type')}` `{obj.get('period')}`",
        f"- Text: {_short_text(_display_text(obj))}",
    ]
    for quote in payload.get("evidence", {}).get("quotes", [])[:5]:
        lines.append(f"- Quote `{quote.get('id')}`: {_short_text(quote.get('text'))}")
    for span in payload.get("evidence", {}).get("spans", [])[:3]:
        lines.append(f"- Span `{span.get('id')}`: {_short_text(span.get('text'))}")
    return "\n".join(lines)


def _markdown_chain(payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return _markdown_error(payload)
    obj = payload.get("object", {})
    chain = payload.get("chain") or {}
    evidence_chain = chain.get("evidence_chain") or {}
    lines = [
        "# Ontology Chain",
        f"- Object: `{obj.get('id')}` ({obj.get('type')})",
        f"- Scope: `{obj.get('ticker')}` `{obj.get('document_type')}` `{obj.get('period')}`",
        f"- Text: {_short_text(obj.get('text'))}",
        f"- Direction: {chain.get('direction')} max_depth={chain.get('max_depth')}",
        f"- Evidence: claims={len(evidence_chain.get('claims') or [])}, "
        f"quotes={len(evidence_chain.get('quotes') or [])}, "
        f"spans={len(evidence_chain.get('spans') or [])}",
        f"- Semantic neighbors: {len(chain.get('semantic_neighbors') or [])}",
        f"- Temporal context: {len(chain.get('temporal_context') or [])}",
    ]
    warnings = (payload.get("quality") or {}).get("warnings") or []
    if warnings:
        lines.append(f"- Warnings: {', '.join(warnings)}")
    for neighbor in (chain.get("semantic_neighbors") or [])[:5]:
        neighbor_obj = neighbor.get("object") or {}
        lines.append(
            f"- Neighbor `{neighbor_obj.get('id')}` ({neighbor_obj.get('type')}): "
            f"{_short_text(neighbor_obj.get('text'))}"
        )
    for temporal in (chain.get("temporal_context") or [])[:5]:
        lines.append(
            f"- Temporal `{temporal.get('id')}` ({temporal.get('type')}): "
            f"{_short_text(temporal.get('text'))}"
        )
    return "\n".join(lines)


def _markdown_quality(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Ontology Quality",
        f"- Documents: {summary.get('documents', 0)}",
        f"- Events: {summary.get('events', 0)}",
        f"- Rejected objects: {summary.get('rejected_objects', 0)}",
        f"- Batch failures: {summary.get('batch_failures', 0)}",
        f"- Section warnings: {summary.get('section_warnings', 0)}",
    ]
    for event in payload.get("events", [])[:10]:
        lines.append(
            f"- `{event.get('category')}` `{event.get('severity')}` "
            f"{event.get('ticker')} {event.get('document_type')} {event.get('period')}: "
            f"{_short_text(event.get('message'))}"
        )
    return "\n".join(lines)


def _markdown_compare(payload: dict[str, Any]) -> str:
    lines = [
        "# Ontology Compare",
        f"- Mode: {payload.get('mode')}",
        f"- Topic: {payload.get('topic')}",
        f"- Metric: {payload.get('metric')}",
    ]
    comparison_rows = payload.get("comparison_rows") or []
    if comparison_rows:
        lines.append("## Comparison Rows")
        for row in comparison_rows:
            status = "missing" if row.get("missing") else "matched"
            lines.append(
                f"- `{row.get('comparison_key')}` {status}: "
                f"{_short_text(row.get('summary') or row.get('missing_reason'))}"
            )
    for ticker, items in payload.get("results", {}).items():
        lines.append(f"## {ticker}")
        if not items:
            lines.append("- No results")
            continue
        for item in items:
            lines.extend(_bundle_lines(item, prefix="- "))
    return "\n".join(lines)


def _markdown_plan(payload: dict[str, Any]) -> str:
    plan = payload.get("plan") or {}
    return "# Ontology Query Plan\n" + json.dumps(plan, ensure_ascii=False, indent=2, default=str)


def _markdown_error(payload: dict[str, Any]) -> str:
    error = payload.get("error", {})
    return (
        f"# Error\n- Code: `{error.get('code')}`\n"
        f"- Message: {error.get('message')}\n"
        f"- Suggestion: {error.get('suggestion')}"
    )


def _bundle_lines(item: dict[str, Any], *, prefix: str = "## ") -> list[str]:
    lines = [
        f"{prefix}`{item.get('id')}` ({item.get('type')})",
        f"- Scope: `{item.get('ticker')}` `{item.get('document_type')}` `{item.get('period')}`",
        f"- Text: {_short_text(item.get('text'))}",
    ]
    quotes = item.get("evidence", {}).get("quotes", [])
    if quotes:
        lines.append(f"- Quote `{quotes[0].get('id')}`: {_short_text(quotes[0].get('text'))}")
    return lines


def _display_text(obj: dict[str, Any]) -> str:
    for key in (
        "claim_text",
        "quote_text",
        "description",
        "mechanism",
        "business_model_summary",
        "interpretation",
        "assumption_text",
        "text",
        "raw_text",
    ):
        if obj.get(key):
            return str(obj[key])
    if obj.get("metric_name"):
        return f"{obj['metric_name']}: {obj.get('value')} {obj.get('unit')}"
    return str(obj.get("name") or obj.get("id") or "")


def _short_text(value: Any, max_chars: int = 320) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _unique(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def query_plan_from_payload(payload: dict[str, Any]) -> QueryPlan:
    """Helper for tests and future adapters."""
    return QueryPlan(**payload)
