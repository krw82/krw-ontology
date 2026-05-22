"""Python SDK for agent-friendly ontology retrieval."""

from __future__ import annotations

from collections import deque
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from krw_ontology.agent_index.retrieval_text import format_metric_compact
from krw_ontology.agent_index.discovery import (
    build_evidence_frame,
    build_query_frame,
    classify_topic_match,
    tier_rank,
    topic_from_row,
)

DEFAULT_QUERY_TYPES = (
    "EvidenceQuote",
    "ResearchClaim",
    "MetricObservation",
    "Calculation",
    "BusinessFactor",
    "AgreementTerm",
    "BusinessEvent",
    "BusinessActivity",
    "ExternalFactorExposure",
    "CompanyBusinessProfile",
    "TemporalLink",
    "TrendObservation",
    "ChangeEvent",
    "AssumptionCandidate",
)

_TERM_RE = re.compile(r"[A-Za-z0-9_]+")
_STRICT_TOPIC_WARNING_TERM_COUNT = 5
_CHAIN_MAX_DEPTH = 4
_CHAIN_MAX_PATHS = 40
_CHAIN_MAX_TEMPORAL_CONTEXT = 12
_QUERY_EXPANSION_RULES = (
    ("유럽", "europe european"),
    ("가스", "natural_gas_price natural gas feed gas lng"),
    ("천연가스", "natural_gas_price natural gas feed gas lng"),
    ("비축", "storage inventory"),
    ("저장", "storage inventory"),
    ("부족", "shortage deficit supply_disruption demand"),
    ("수요", "demand customer_demand lng_demand"),
    ("가격", "price commodity_price natural_gas_price"),
    ("마진", "operating_margin cost_of_revenue margin"),
    ("계약", "contract agreement spa customer"),
    ("조건", "threshold covenant default termination"),
    ("종료", "termination terminate terminated cancellation cancel"),
    ("해지", "termination terminate terminated cancellation cancel"),
    ("가속화", "debt_acceleration accelerate accelerated acceleration"),
    ("담보", "collateral collateral_enforcement security lien"),
    ("큰일", "risk covenant default impairment liquidity threshold"),
    ("위험", "risk"),
    ("중국", "china"),
    ("수출", "export exports export_controls restrictions"),
    ("규제", "regulatory regulation controls restrictions"),
    ("제한", "restriction restrictions controls"),
    ("허가", "license licensing approval"),
    ("라이선스", "license licensing"),
    ("고객", "customer customers"),
    ("집중", "concentration concentrated"),
    ("주요", "key primary major"),
    ("이벤트", "event business_event change_event"),
    ("일정", "event milestone date"),
    ("변화", "change trend temporal"),
    ("바뀐", "change changed trend"),
    ("부채", "debt maturity obligation"),
    ("만기", "maturity"),
    ("ttf", "natural_gas_price international_lng_price global_lng_price europe"),
    ("jkm", "international_lng_price global_lng_price lng"),
    ("henry hub", "natural_gas_price feed_gas_cost"),
    ("storage", "storage inventory supply_disruption"),
    ("shortage", "shortage deficit supply_disruption demand"),
    ("lng", "lng_sales lng_demand international_lng_price"),
)
_SPLIT_TOPIC_STOP_TERMS = {
    "and",
    "are",
    "for",
    "from",
    "how",
    "the",
    "this",
    "what",
    "when",
    "with",
}
_SEMANTIC_NEIGHBOR_TYPES = {
    "BusinessFactor",
    "AgreementTerm",
    "BusinessEvent",
    "BusinessActivity",
    "ExternalFactorExposure",
    "AssumptionCandidate",
}


class OntologyStore:
    """Read-only SDK over an agent index SQLite database."""

    def __init__(self, index_path: Path | str):
        self.index_path = Path(index_path)
        self.conn = sqlite3.connect(self.index_path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "OntologyStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def list_companies(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT ticker FROM documents ORDER BY ticker"
        ).fetchall()
        return [row["ticker"] for row in rows]

    def list_documents(
        self,
        *,
        ticker: str | None = None,
        document_types: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        where, params = _scope_where(ticker=ticker, document_types=document_types)
        rows = self.conn.execute(
            f"""
            SELECT ticker, document_type, doc_type_key, period, artifact_index_path,
                   ontology_dir, counts_json, section_quality_status, section_quality_json
            FROM documents
            {where}
            ORDER BY ticker, doc_type_key, period
            """,
            params,
        ).fetchall()
        return [_document_from_row(row) for row in rows]

    def index_context(
        self,
        *,
        include_counts: bool = True,
        include_capabilities: bool = True,
        include_quality_summary: bool = True,
    ) -> dict[str, Any]:
        """Return a compact capability card for the current serving index."""
        build_metadata = _metadata_json(self.conn, "build")
        documents = self.list_documents()
        periods_by_ticker: dict[str, list[str]] = {}
        document_types: set[str] = set()
        for document in documents:
            ticker = str(document.get("ticker") or "")
            period = str(document.get("period") or "")
            if ticker and period:
                periods_by_ticker.setdefault(ticker, [])
                if period not in periods_by_ticker[ticker]:
                    periods_by_ticker[ticker].append(period)
            if document.get("document_type"):
                document_types.add(str(document["document_type"]))

        payload: dict[str, Any] = {
            "index_status": "ready",
            "index_path": str(self.index_path),
            "schema_version": build_metadata.get("schema_version"),
            "agent_index_schema_version": build_metadata.get("agent_index_schema_version")
            or build_metadata.get("schema_version"),
            "ontology_schema_version": build_metadata.get("ontology_schema_version"),
            "ontology_registry_version": build_metadata.get("ontology_registry_version"),
            "retrieval_text_builder_version": build_metadata.get("retrieval_text_builder_version"),
            "company_topic_builder_version": build_metadata.get("company_topic_builder_version"),
            "company_topic_profile_mode": build_metadata.get("company_topic_profile_mode"),
            "available_tickers": self.list_companies(),
            "available_document_types": sorted(document_types),
            "available_periods_by_ticker": {ticker: sorted(periods) for ticker, periods in periods_by_ticker.items()},
            "ticker_coverage": _ticker_coverage(self.conn),
            "answer_candidate_object_types": list(DEFAULT_QUERY_TYPES),
            "trace_only_object_types": [
                "SupportLink",
                "Edge",
                "SourceDocument",
                "SourceLocation",
                "SourceSpan",
                "SourceTable",
                "SourceTableCell",
                "XBRLFact",
                "CanonicalEntity",
                "EntityMention",
            ],
            "answerability_policy": {
                "strong_claim_requires_traceable_direct": True,
                "metric_claim_requires_metric_lineage": True,
                "broad_related_must_not_be_presented_as_direct": True,
                "internal_ids_hidden_in_final_answer": True,
            },
        }
        if include_counts:
            payload["object_counts"] = _count_by(self.conn, "objects", "type")
            payload["serving_counts"] = {
                "documents": _table_count(self.conn, "documents"),
                "objects": _table_count(self.conn, "objects"),
                "object_search_text": _table_count(self.conn, "object_search_text"),
                "object_fts": _table_count(self.conn, "object_fts"),
                "object_traceability": _table_count(self.conn, "object_traceability"),
                "company_topic_index": _table_count(self.conn, "company_topic_index"),
                "company_topic_fts": _table_count(self.conn, "company_topic_fts"),
                "company_topic_source_objects": _table_count(self.conn, "company_topic_source_objects"),
            }
        if include_capabilities:
            payload["capabilities"] = {
                "query": True,
                "retrieve": True,
                "trace": True,
                "chain": True,
                "compare": True,
                "quality": True,
                "company_topic_index": _table_exists(self.conn, "company_topic_index"),
                "object_search_text": _table_exists(self.conn, "object_search_text"),
                "company_context": _table_exists(self.conn, "company_topic_index"),
                "query_context": _table_exists(self.conn, "company_topic_index"),
                "answerability_tiers": True,
                "metric_lineage_trace": True,
                "traceability_metadata": _table_exists(self.conn, "object_traceability"),
            }
        if include_quality_summary:
            severity_counts = _count_by(self.conn, "quality_events", "severity")
            payload["quality_summary"] = {
                "severity_counts": severity_counts,
                "critical_errors": int(severity_counts.get("critical") or severity_counts.get("error") or 0),
                "batch_failures": self.conn.execute(
                    "SELECT COUNT(*) FROM quality_events WHERE category = 'batch_failure'"
                ).fetchone()[0],
                "orphan_answer_object_count": self.conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM object_traceability
                    WHERE answer_candidate = 1
                      AND trace_status IN ('orphan', 'untraced', 'unknown')
                    """
                ).fetchone()[0],
            }
        return payload

    def company_context(
        self,
        *,
        ticker: str,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        limit_topics: int = 12,
        include_internal_ids: bool = True,
    ) -> dict[str, Any]:
        """Return a compressed evidence-derived company topic profile."""
        ticker = ticker.upper()
        where, params = _company_topic_filters(
            tickers=[ticker],
            document_types=document_types,
            periods=periods,
        )
        where = f"{where} AND COALESCE(company_topic_index.boilerplate_score, 0) < 1"
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM company_topic_index
            {where}
            ORDER BY
                CASE trace_status
                    WHEN 'traceable' THEN 3
                    WHEN 'traceable_metric_lineage' THEN 3
                    WHEN 'related' THEN 2
                    ELSE 1
                END DESC,
                COALESCE(specificity_score, 0) DESC,
                COALESCE(generic_score, 0) ASC,
                COALESCE(boilerplate_score, 0) ASC,
                evidence_chain_count DESC,
                support_quote_count + support_claim_count + support_metric_count DESC
            LIMIT ?
            """,
            [*params, max(1, min(int(limit_topics), 50))],
        ).fetchall()
        topics = [_company_topic_payload(topic_from_row(dict(row)), include_internal_ids=include_internal_ids) for row in rows]
        payload = {
            "ticker": ticker,
            "document_types": list(document_types or []),
            "periods": list(periods or []),
            "company_topics": topics,
            "internal_only_fields": ["topic_id", "source_object_ids", "top_traceable_object_ids", "untraced_object_ids"],
        }
        if not topics:
            payload["warnings"] = ["no_company_topics_found"]
        return payload

    def query_context(
        self,
        *,
        question: str,
        ticker: str | None = None,
        tickers: Iterable[str] | None = None,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        universe: str | None = None,
        limit_results: int = 10,
        limit_tickers: int = 20,
        include_internal_ids: bool = True,
    ) -> dict[str, Any]:
        """Return a deterministic answer-planning pack for one user question."""
        requested_tickers: list[str] | None
        if ticker:
            requested_tickers = [ticker.upper()]
        elif tickers:
            requested_tickers = [str(value).upper() for value in tickers]
        elif universe and universe != "all":
            requested_tickers = [str(universe).upper()]
        else:
            requested_tickers = None

        discovery = self.discover_company_topics(
            question=question,
            tickers=requested_tickers,
            document_types=document_types,
            periods=periods,
            limit_groups=max(1, min(int(limit_tickers), 50)),
            limit_per_group=3,
            limit=max(int(limit_results) * 10, 50),
        )
        candidates = list(discovery.get("ticker_candidates") or [])
        selected_candidates = candidates[: max(1, min(int(limit_tickers), 50))]
        query_frame = dict(discovery.get("query_frame") or {})
        if _question_requires_direct_match(question):
            query_frame["question_requires_direct_match"] = True
        answerability = _answerability_from_candidates(query_frame, selected_candidates)
        recommended_tools = _recommended_trace_tools(selected_candidates, limit=max(1, min(int(limit_results), 10)))
        payload = {
            "question": question,
            "query_frame": query_frame,
            "answerability": answerability,
            "ticker_candidates": selected_candidates,
            "recommended_tools": recommended_tools,
            "search_diagnostics": discovery.get("search_diagnostics"),
            "final_answer_guidance": _final_answer_guidance(answerability, selected_candidates),
            "internal_only_fields": ["topic_id", "primary_object_id", "source_object_ids", "top_traceable_object_ids", "recommended_tools.object_id"],
        }
        if include_internal_ids:
            payload["results_by_ticker"] = discovery.get("results_by_ticker") or {}
        return payload

    def get_object(self, object_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM objects WHERE id = ?",
            (object_id,),
        ).fetchone()
        return _object_from_row(row) if row else None

    def find_object_ids(self, prefix: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return object id candidates whose ids start with a caller-provided prefix."""
        rows = self.conn.execute(
            """
            SELECT id, type, ticker, document_type, period, review_status
            FROM objects
            WHERE id LIKE ? || '%'
            ORDER BY id
            LIMIT ?
            """,
            (prefix, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def query(
        self,
        *,
        topic: str | None = None,
        tickers: Iterable[str] | None = None,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        object_types: Iterable[str] | None = None,
        include_rejected: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return evidence bundles matching structured filters and optional FTS text."""
        bundles, _diagnostics = self.query_with_diagnostics(
            topic=topic,
            tickers=tickers,
            document_types=document_types,
            periods=periods,
            object_types=object_types,
            include_rejected=include_rejected,
            limit=limit,
        )
        return bundles

    def query_with_diagnostics(
        self,
        *,
        topic: str | None = None,
        tickers: Iterable[str] | None = None,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        object_types: Iterable[str] | None = None,
        include_rejected: bool = False,
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return evidence bundles plus deterministic search diagnostics."""
        selected_types = tuple(object_types or DEFAULT_QUERY_TYPES)
        search_strategy: dict[str, Any] | None = None
        if topic:
            rows, search_strategy = self._query_fts_with_strategy(
                topic,
                tickers=tickers,
                document_types=document_types,
                periods=periods,
                object_types=selected_types,
                include_rejected=include_rejected,
                limit=limit,
            )
        else:
            rows = self._query_objects(
                tickers=tickers,
                document_types=document_types,
                periods=periods,
                object_types=selected_types,
                include_rejected=include_rejected,
                limit=limit,
            )
        bundles = [self.bundle(row["id"]) for row in rows if row["id"]]
        diagnostics = _search_diagnostics(
            topic,
            result_count=len(bundles),
            search_strategy=search_strategy,
        )
        return bundles, diagnostics

    def query_compact_with_diagnostics(
        self,
        *,
        topic: str | None = None,
        tickers: Iterable[str] | None = None,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        object_types: Iterable[str] | None = None,
        include_rejected: bool = False,
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return row-level compact query results without eager evidence expansion."""
        selected_types = tuple(object_types or DEFAULT_QUERY_TYPES)
        search_strategy: dict[str, Any] | None = None
        if topic:
            rows, search_strategy = self._query_fts_with_strategy(
                topic,
                tickers=tickers,
                document_types=document_types,
                periods=periods,
                object_types=selected_types,
                include_rejected=include_rejected,
                limit=limit,
            )
        else:
            rows = self._query_objects(
                tickers=tickers,
                document_types=document_types,
                periods=periods,
                object_types=selected_types,
                include_rejected=include_rejected,
                limit=limit,
            )
        bundles = self._compact_bundles_from_rows(rows)
        diagnostics = _search_diagnostics(
            topic,
            result_count=len(bundles),
            search_strategy=search_strategy,
        )
        diagnostics["compact_fast_path"] = True
        return bundles, diagnostics

    def search_diagnostics(
        self,
        topic: str | None,
        *,
        result_count: int | None = None,
    ) -> dict[str, Any]:
        """Return deterministic diagnostics for the FTS topic query."""
        return _search_diagnostics(topic, result_count=result_count)

    def discover_company_topics(
        self,
        *,
        question: str,
        tickers: Iterable[str] | None = None,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        limit_groups: int = 20,
        limit_per_group: int = 3,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Discover ticker candidates by matching QueryFrame to evidence-derived topics."""
        query_frame = build_query_frame(question)
        fts_query = _fts_query(_expanded_topic(question), operator="OR")
        try:
            rows = self._query_company_topics(
                fts_query,
                tickers=tickers,
                document_types=document_types,
                periods=periods,
                limit=limit,
            ) if fts_query else []
        except sqlite3.OperationalError as exc:
            return {
                "query_frame": query_frame.as_dict(),
                "ticker_candidates": [],
                "results_by_ticker": {},
                "search_diagnostics": {
                    "topic": question,
                    "fts_query": fts_query,
                    "searched_company_topics": 0,
                    "matched_tickers": 0,
                    "warnings": ["company_topic_index_unavailable"],
                    "error": str(exc),
                },
            }

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            topic = topic_from_row(dict(row))
            evidence_frame = build_evidence_frame(topic)
            match = classify_topic_match(query_frame, evidence_frame)
            if match["tier"] == "insufficient":
                continue
            semantic_relevance = str(match.get("tier") or "insufficient")
            trace_status = str(topic.get("trace_status") or "unknown")
            final_tier = _combined_discovery_tier(semantic_relevance, trace_status)
            match = {
                **match,
                "semantic_relevance": semantic_relevance,
                "trace_status": trace_status,
                "tier": final_tier,
                "why_tier": _why_discovery_tier(semantic_relevance, trace_status, final_tier),
            }
            topic_payload = {
                "topic_id": topic.get("topic_id"),
                "ticker": topic.get("ticker"),
                "period": topic.get("period"),
                "document_type": topic.get("document_type"),
                "topic_label": topic.get("topic_label"),
                "topic_summary": topic.get("topic_summary"),
                "primary_object_id": topic.get("primary_object_id"),
                "primary_object_type": topic.get("primary_object_type"),
                "source_object_ids": topic.get("source_object_ids") or [],
                "dominant_object_types": topic.get("dominant_object_types") or [],
                "impact_channels": topic.get("impact_channels") or [],
                "evidence_strength": topic.get("evidence_strength"),
                "support_quote_count": topic.get("support_quote_count") or 0,
                "support_claim_count": topic.get("support_claim_count") or 0,
                "trace_status": trace_status,
                "evidence_chain_count": topic.get("evidence_chain_count") or 0,
                "support_depth": topic.get("support_depth"),
                "support_link_count": topic.get("support_link_count") or 0,
                "trace_method": topic.get("trace_method"),
                "metric_lineage_status": topic.get("metric_lineage_status"),
                "answer_candidate": bool(topic.get("answer_candidate")),
                "specificity_score": topic.get("specificity_score"),
                "generic_score": topic.get("generic_score"),
                "boilerplate_score": topic.get("boilerplate_score"),
                "materiality_hint": topic.get("materiality_hint"),
                "materiality_score": topic.get("materiality_score"),
                "topic_type": topic.get("topic_type"),
                "topic_family": topic.get("topic_family"),
                "factor_terms": topic.get("factor_terms") or [],
                "metric_terms": topic.get("metric_terms") or [],
                "entity_terms": topic.get("entity_terms") or [],
                "mechanism_terms": topic.get("mechanism_terms") or [],
                "scenario_terms": topic.get("scenario_terms") or [],
                "top_traceable_object_ids": topic.get("top_traceable_object_ids") or [],
                "untraced_object_ids": topic.get("untraced_object_ids") or [],
                "match": match,
            }
            grouped.setdefault(str(topic.get("ticker") or "UNKNOWN"), []).append(topic_payload)

        candidates: list[dict[str, Any]] = []
        results_by_ticker: dict[str, list[dict[str, Any]]] = {}
        for ticker, topics in grouped.items():
            topics.sort(
                key=lambda item: (
                    tier_rank(str((item.get("match") or {}).get("tier") or "")),
                    float((item.get("match") or {}).get("score") or 0.0),
                    float(item.get("specificity_score") or 0.0),
                    -float(item.get("generic_score") or 0.0),
                    -float(item.get("boilerplate_score") or 0.0),
                    int(item.get("support_quote_count") or 0) + int(item.get("support_claim_count") or 0),
                ),
                reverse=True,
            )
            selected = topics[:limit_per_group]
            if not selected:
                continue
            results_by_ticker[ticker] = selected
            best_tier = str((selected[0].get("match") or {}).get("tier") or "insufficient")
            top_object_ids: list[str] = []
            for topic in selected:
                for object_id in topic.get("source_object_ids") or []:
                    if object_id not in top_object_ids:
                        top_object_ids.append(object_id)
            candidates.append(
                {
                    "ticker": ticker,
                    "score": round(sum(float((topic.get("match") or {}).get("score") or 0.0) for topic in selected), 4),
                    "tier": best_tier,
                    "matched_topic_count": len(topics),
                    "matched_object_counts": _topic_object_counts(topics),
                    "evidence_counts": _topic_evidence_counts(topics),
                    "trace_status": _ticker_trace_status(selected),
                    "trace_counts": _topic_trace_counts(selected),
                    "top_traceable_object_ids": _traceable_object_ids(selected)[:10],
                    "untraced_object_ids": _untraced_object_ids(selected)[:10],
                    "top_reasons": [_topic_reason(topic) for topic in selected],
                    "top_object_ids": top_object_ids[:10],
                    "matched_topics": selected,
                }
            )

        candidates.sort(
            key=lambda item: (
                tier_rank(str(item.get("tier") or "")),
                float(item.get("score") or 0.0),
                int(item.get("matched_topic_count") or 0),
            ),
            reverse=True,
        )
        candidates = candidates[:limit_groups]
        allowed = {str(candidate.get("ticker")) for candidate in candidates}
        return {
            "query_frame": query_frame.as_dict(),
            "ticker_candidates": candidates,
            "results_by_ticker": {
                ticker: results_by_ticker[ticker]
                for ticker in sorted(allowed)
                if ticker in results_by_ticker
            },
            "search_diagnostics": {
                "topic": question,
                "fts_query": fts_query,
                "searched_company_topics": len(rows),
                "matched_tickers": len(candidates),
            },
        }

    def topic_map(
        self,
        *,
        ticker: str,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return a search-vocabulary map for a company from accepted objects.

        This is a retrieval helper, not a new ontology artifact. It repackages
        CompanyBusinessProfile and falls back to BusinessActivity and
        ExternalFactorExposure when a profile is unavailable.
        """
        ticker = ticker.upper()
        limit = max(1, int(limit))
        profiles = self._topic_map_objects(
            ticker=ticker,
            object_types=("CompanyBusinessProfile",),
            document_types=None,
            periods=None,
            limit=3,
        )
        activities = self._topic_map_objects(
            ticker=ticker,
            object_types=("BusinessActivity",),
            document_types=document_types,
            periods=periods,
            limit=limit,
        )
        exposures = self._topic_map_objects(
            ticker=ticker,
            object_types=("ExternalFactorExposure",),
            document_types=document_types,
            periods=periods,
            limit=limit,
        )
        metric_objects = self._topic_map_objects(
            ticker=ticker,
            object_types=("MetricObservation",),
            document_types=document_types,
            periods=periods,
            limit=limit * 2,
        )

        external_factors: dict[str, dict[str, Any]] = {}
        business_activities: dict[str, dict[str, Any]] = {}
        metrics: dict[str, dict[str, Any]] = {}
        projects_assets: dict[str, dict[str, Any]] = {}

        for profile in profiles:
            source_id = profile.get("id")
            for factor in _string_values(profile.get("key_external_factors")):
                _merge_topic_entry(external_factors, factor, source_id=source_id)
            for activity in _string_values(profile.get("primary_business_activities")):
                _merge_topic_entry(business_activities, activity, source_id=source_id)
            for metric in _string_values(profile.get("key_metrics")):
                _merge_topic_entry(metrics, metric, source_id=source_id)
            for value in _explicit_project_asset_values(profile):
                _merge_topic_entry(projects_assets, value, source_id=source_id)

        for activity in activities:
            source_id = activity.get("id")
            term = activity.get("activity_type") or activity.get("name")
            _merge_topic_entry(
                business_activities,
                term,
                source_id=source_id,
                related_metrics=_object_metrics(activity),
                search_terms=[activity.get("name"), activity.get("activity_type")],
            )
            for metric in _object_metrics(activity):
                _merge_topic_entry(metrics, metric, source_id=source_id)

        for exposure in exposures:
            source_id = exposure.get("id")
            term = exposure.get("factor")
            related_channels = _string_values(
                exposure.get("impact_channel")
                or exposure.get("impact_channels")
                or exposure.get("affects")
            )
            _merge_topic_entry(
                external_factors,
                term,
                source_id=source_id,
                related_channels=related_channels,
                search_terms=[exposure.get("factor"), exposure.get("benchmark")],
                evidence_grade=exposure.get("evidence_grade"),
            )
            for channel in related_channels:
                _merge_topic_entry(metrics, channel, source_id=source_id)

        for metric_obj in metric_objects:
            metric = metric_obj.get("metric_name")
            _merge_topic_entry(metrics, metric, source_id=metric_obj.get("id"))

        suggested_first_queries = _suggest_topic_queries(
            external_factors=external_factors,
            business_activities=business_activities,
        )

        return {
            "ticker": ticker,
            "query": {
                "document_types": list(document_types or []),
                "periods": list(periods or []),
                "limit": limit,
            },
            "source": {
                "company_business_profile_ids": [profile.get("id") for profile in profiles],
                "fallback_used": not bool(profiles),
                "business_activity_count": len(activities),
                "external_factor_exposure_count": len(exposures),
            },
            "topics": {
                "external_factors": _topic_entries(external_factors, limit=limit),
                "business_activities": _topic_entries(business_activities, limit=limit),
                "metrics": _topic_entries(metrics, limit=limit),
                "projects_assets": _topic_entries(projects_assets, limit=limit),
            },
            "suggested_first_queries": suggested_first_queries[: min(limit, 10)],
        }

    def trace(self, object_id: str) -> dict[str, Any] | None:
        """Trace an object back to supporting evidence and document metadata."""
        row = self.conn.execute("SELECT * FROM objects WHERE id = ?", (object_id,)).fetchone()
        if not row:
            return None
        obj = _object_from_row(row)
        evidence = self._expand_evidence(obj)
        return {
            "object": obj,
            "document": self._document_for(obj),
            "evidence": evidence,
            "quality": self._quality_for_object(obj),
        }

    def chain(
        self,
        object_id: str,
        *,
        max_depth: int = 2,
        direction: str = "both",
        include_quote_text: bool = False,
    ) -> dict[str, Any] | None:
        """Return a compact evidence and graph chain around one ontology object."""
        obj = self.get_object(object_id)
        if not obj:
            return None

        max_depth = max(0, min(int(max_depth), _CHAIN_MAX_DEPTH))
        direction = _normalize_chain_direction(direction)
        evidence = self._expand_evidence(obj)
        graph_paths = self._edge_paths(
            obj["id"],
            direction=direction,
            max_depth=max_depth,
            include_quote_text=include_quote_text,
        )
        temporal_context = self._temporal_context_for_object(
            obj,
            evidence=evidence,
            graph_paths=graph_paths,
            include_quote_text=include_quote_text,
        )
        quality = self._quality_for_object(obj)
        quality["evidence_grade"] = obj.get("evidence_grade")
        quality["warnings"] = _chain_warnings(obj, evidence, quality)

        return {
            "object": _chain_node(obj, include_quote_text=include_quote_text),
            "document": self._document_for(obj),
            "chain": {
                "max_depth": max_depth,
                "direction": direction,
                "include_quote_text": include_quote_text,
                "evidence_chain": {
                    "claims": [
                        _chain_node(claim, include_quote_text=include_quote_text)
                        for claim in evidence.get("claims", [])
                    ],
                    "quotes": [
                        _chain_node(quote, include_quote_text=include_quote_text)
                        for quote in evidence.get("quotes", [])
                    ],
                    "spans": [
                        _chain_node(span, include_quote_text=include_quote_text)
                        for span in evidence.get("spans", [])
                    ],
                },
                "semantic_neighbors": self._semantic_neighbors_for_chain(
                    obj,
                    evidence=evidence,
                    graph_paths=graph_paths,
                    include_quote_text=include_quote_text,
                ),
                "temporal_context": temporal_context,
                "edge_paths": graph_paths,
            },
            "quality": quality,
        }

    def bundle(self, object_id: str) -> dict[str, Any]:
        """Return a compact evidence bundle for query results."""
        trace = self.trace(object_id)
        if trace is None:
            return {"id": object_id, "missing": True}
        obj = trace["object"]
        return {
            "id": obj["id"],
            "type": obj["type"],
            "ticker": obj.get("ticker"),
            "document_type": obj.get("document_type"),
            "period": obj.get("period"),
            "section": obj.get("section_name") or obj.get("section_key"),
            "text": _display_text(obj),
            "object": obj,
            "evidence": trace["evidence"],
            "quality": trace["quality"],
            "document": trace["document"],
        }

    def compare(
        self,
        *,
        tickers: Iterable[str],
        topic: str | None = None,
        metric: str | None = None,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        limit_per_ticker: int = 5,
    ) -> dict[str, Any]:
        """Compare companies by topic or canonical metric at query time."""
        ticker_list = [ticker.upper() for ticker in tickers]
        results: dict[str, list[dict[str, Any]]] = {}
        comparison_evaluations: dict[str, dict[str, Any]] = {}
        comparison_contexts: dict[str, dict[str, Any]] = {}
        for ticker in ticker_list:
            if metric:
                rows = self._query_metric(
                    metric,
                    ticker=ticker,
                    document_types=document_types,
                    periods=periods,
                    limit=limit_per_ticker,
                )
                results[ticker] = [self.bundle(row["id"]) for row in rows]
                comparison_evaluations[ticker] = _comparison_evaluation_from_items(
                    results[ticker],
                    metric=metric,
                )
            else:
                context = self.query_context(
                    question=topic or "",
                    ticker=ticker,
                    document_types=document_types,
                    periods=periods,
                    limit_results=limit_per_ticker,
                    limit_tickers=1,
                    include_internal_ids=True,
                )
                comparison_contexts[ticker] = _compact_comparison_context(context)
                comparison_evaluations[ticker] = _comparison_evaluation_from_query_context(context)
                context_bundles = self._bundles_from_object_ids(
                    _object_ids_from_query_context(context),
                    limit=limit_per_ticker,
                )
                query_bundles = self.query(
                    topic=topic,
                    tickers=[ticker],
                    document_types=document_types,
                    periods=periods,
                    limit=limit_per_ticker,
                )
                results[ticker] = _merge_bundle_lists(
                    context_bundles,
                    query_bundles,
                    limit=max(limit_per_ticker * 2, limit_per_ticker + 2),
                )
                _apply_comparison_evaluation(results[ticker], comparison_evaluations[ticker])
        return {
            "mode": "metric" if metric else "topic",
            "topic": topic,
            "metric": metric,
            "tickers": ticker_list,
            "results": results,
            "comparison_evaluations": comparison_evaluations,
            "comparison_contexts": comparison_contexts,
        }

    def compare_compact(
        self,
        *,
        tickers: Iterable[str],
        topic: str | None = None,
        metric: str | None = None,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        limit_per_ticker: int = 5,
    ) -> dict[str, Any]:
        """Compare companies using compact row-level candidates without eager traces."""
        ticker_list = [ticker.upper() for ticker in tickers]
        results: dict[str, list[dict[str, Any]]] = {}
        comparison_evaluations: dict[str, dict[str, Any]] = {}
        comparison_contexts: dict[str, dict[str, Any]] = {}
        for ticker in ticker_list:
            if metric:
                rows = self._query_metric(
                    metric,
                    ticker=ticker,
                    document_types=document_types,
                    periods=periods,
                    limit=limit_per_ticker,
                )
                results[ticker] = self._compact_bundles_from_rows(rows)
                comparison_evaluations[ticker] = _comparison_evaluation_from_items(
                    results[ticker],
                    metric=metric,
                )
            else:
                context = self.query_context(
                    question=topic or "",
                    ticker=ticker,
                    document_types=document_types,
                    periods=periods,
                    limit_results=limit_per_ticker,
                    limit_tickers=1,
                    include_internal_ids=True,
                )
                comparison_contexts[ticker] = _compact_comparison_context(context)
                comparison_evaluations[ticker] = _comparison_evaluation_from_query_context(context)
                context_bundles = self._compact_bundles_from_object_ids(
                    _object_ids_from_query_context(context),
                    limit=limit_per_ticker,
                )
                query_bundles, _diagnostics = self.query_compact_with_diagnostics(
                    topic=topic,
                    tickers=[ticker],
                    document_types=document_types,
                    periods=periods,
                    limit=limit_per_ticker,
                )
                results[ticker] = _merge_bundle_lists(
                    context_bundles,
                    query_bundles,
                    limit=max(limit_per_ticker * 2, limit_per_ticker + 2),
                )
                _apply_comparison_evaluation(results[ticker], comparison_evaluations[ticker])
        return {
            "mode": "metric" if metric else "topic",
            "topic": topic,
            "metric": metric,
            "tickers": ticker_list,
            "results": results,
            "comparison_evaluations": comparison_evaluations,
            "comparison_contexts": comparison_contexts,
            "compact_fast_path": True,
        }

    def quality(
        self,
        *,
        ticker: str | None = None,
        document_type: str | None = None,
        period: str | None = None,
    ) -> dict[str, Any]:
        where_parts: list[str] = []
        params: list[Any] = []
        if ticker:
            where_parts.append("ticker = ?")
            params.append(ticker.upper())
        if document_type:
            where_parts.append("document_type = ?")
            params.append(document_type)
        if period:
            where_parts.append("period = ?")
            params.append(period)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        documents = [
            _document_from_row(row)
            for row in self.conn.execute(
                f"SELECT * FROM documents {where} ORDER BY ticker, doc_type_key, period",
                params,
            ).fetchall()
        ]
        events = [
            _quality_from_row(row)
            for row in self.conn.execute(
                f"SELECT * FROM quality_events {where} ORDER BY ticker, doc_type_key, period, category",
                params,
            ).fetchall()
        ]
        return {
            "documents": documents,
            "events": events,
            "summary": {
                "documents": len(documents),
                "events": len(events),
                "rejected_objects": sum(1 for event in events if event["category"] == "rejected_object"),
                "batch_failures": sum(1 for event in events if event["category"] == "batch_failure"),
                "section_warnings": sum(1 for event in events if event["category"] == "section_quality"),
            },
        }

    def _query_fts(
        self,
        topic: str,
        *,
        tickers: Iterable[str] | None,
        document_types: Iterable[str] | None,
        periods: Iterable[str] | None,
        object_types: Iterable[str],
        include_rejected: bool,
        limit: int,
    ) -> list[sqlite3.Row]:
        rows, _strategy = self._query_fts_with_strategy(
            topic,
            tickers=tickers,
            document_types=document_types,
            periods=periods,
            object_types=object_types,
            include_rejected=include_rejected,
            limit=limit,
        )
        return rows

    def _query_fts_with_strategy(
        self,
        topic: str,
        *,
        tickers: Iterable[str] | None,
        document_types: Iterable[str] | None,
        periods: Iterable[str] | None,
        object_types: Iterable[str],
        include_rejected: bool,
        limit: int,
    ) -> tuple[list[sqlite3.Row], dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        selected_rows: dict[str, sqlite3.Row] = {}
        expanded_topic = _expanded_topic(topic)
        original_terms = _query_terms(topic)
        should_relax = expanded_topic != topic or len(original_terms) >= 3

        def run_attempt(mode: str, query_topic: str, *, operator: str) -> None:
            if len(selected_rows) >= limit:
                return
            fts_query = _fts_query(query_topic, operator=operator)
            if not fts_query:
                attempts.append(
                    {
                        "mode": mode,
                        "topic": query_topic,
                        "operator": operator,
                        "fts_query": "",
                        "result_count": 0,
                        "added_count": 0,
                    }
                )
                return
            rows = self._execute_fts(
                fts_query,
                tickers=tickers,
                document_types=document_types,
                periods=periods,
                object_types=object_types,
                include_rejected=include_rejected,
                limit=limit,
            )
            added_count = 0
            for row in rows:
                row_id = row["id"]
                if row_id in selected_rows:
                    continue
                selected_rows[row_id] = row
                added_count += 1
                if len(selected_rows) >= limit:
                    break
            attempts.append(
                {
                    "mode": mode,
                    "topic": query_topic,
                    "operator": operator,
                    "fts_query": fts_query,
                    "result_count": len(rows),
                    "added_count": added_count,
                }
            )

        run_attempt("strict_and", topic, operator="AND")
        if len(selected_rows) < limit and expanded_topic != topic:
            run_attempt("expanded_and", expanded_topic, operator="AND")
        if should_relax and len(selected_rows) < limit:
            for split_topic in _split_topic_queries(expanded_topic):
                run_attempt("split_and", split_topic, operator="AND")
                if len(selected_rows) >= limit:
                    break
        if should_relax and len(selected_rows) < limit:
            run_attempt("relaxed_or", expanded_topic, operator="OR")

        selected_mode = next(
            (attempt["mode"] for attempt in attempts if attempt.get("added_count")),
            None,
        )
        strategy = {
            "original_topic": topic,
            "expanded_topic": expanded_topic,
            "expanded_terms": _query_terms(expanded_topic),
            "selected_mode": selected_mode,
            "attempts": attempts,
        }
        return list(selected_rows.values())[:limit], strategy

    def _execute_fts(
        self,
        fts_query: str,
        *,
        tickers: Iterable[str] | None,
        document_types: Iterable[str] | None,
        periods: Iterable[str] | None,
        object_types: Iterable[str],
        include_rejected: bool,
        limit: int,
    ) -> list[sqlite3.Row]:
        where, params = _object_filters(
            tickers=tickers,
            document_types=document_types,
            periods=periods,
            object_types=object_types,
            include_rejected=include_rejected,
        )
        return self.conn.execute(
            f"""
            SELECT objects.*
            FROM object_fts
            JOIN objects ON objects.id = object_fts.object_id
            {where} AND object_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            [*params, fts_query, limit],
        ).fetchall()

    def _query_company_topics(
        self,
        fts_query: str,
        *,
        tickers: Iterable[str] | None,
        document_types: Iterable[str] | None,
        periods: Iterable[str] | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        where, params = _company_topic_filters(
            tickers=tickers,
            document_types=document_types,
            periods=periods,
        )
        return self.conn.execute(
            f"""
            SELECT company_topic_index.*
            FROM company_topic_fts
            JOIN company_topic_index
              ON company_topic_index.topic_id = company_topic_fts.topic_id
            {where} AND company_topic_fts MATCH ?
            ORDER BY
                rank,
                CASE company_topic_index.trace_status
                    WHEN 'traceable' THEN 3
                    WHEN 'traceable_metric_lineage' THEN 3
                    WHEN 'related' THEN 2
                    ELSE 1
                END DESC,
                COALESCE(company_topic_index.specificity_score, 0) DESC,
                COALESCE(company_topic_index.generic_score, 0) ASC,
                COALESCE(company_topic_index.boilerplate_score, 0) ASC,
                company_topic_index.evidence_chain_count DESC,
                company_topic_index.support_quote_count + company_topic_index.support_claim_count + company_topic_index.support_metric_count DESC
            LIMIT ?
            """,
            [*params, fts_query, limit],
        ).fetchall()

    def _bundles_from_object_ids(self, object_ids: Iterable[str], *, limit: int) -> list[dict[str, Any]]:
        bundles: list[dict[str, Any]] = []
        seen: set[str] = set()
        for object_id in object_ids:
            if not object_id or object_id in seen:
                continue
            seen.add(object_id)
            bundle = self.bundle(object_id)
            if bundle.get("missing"):
                continue
            bundles.append(bundle)
            if len(bundles) >= max(1, int(limit)):
                break
        return bundles

    def _compact_bundles_from_object_ids(self, object_ids: Iterable[str], *, limit: int) -> list[dict[str, Any]]:
        ids: list[str] = []
        seen: set[str] = set()
        for object_id in object_ids:
            if not object_id or object_id in seen:
                continue
            seen.add(object_id)
            ids.append(object_id)
            if len(ids) >= max(1, int(limit)):
                break
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT * FROM objects WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        ordered_rows = [by_id[object_id] for object_id in ids if object_id in by_id]
        return self._compact_bundles_from_rows(ordered_rows)

    def _query_objects(
        self,
        *,
        tickers: Iterable[str] | None,
        document_types: Iterable[str] | None,
        periods: Iterable[str] | None,
        object_types: Iterable[str],
        include_rejected: bool,
        limit: int,
    ) -> list[sqlite3.Row]:
        where, params = _object_filters(
            tickers=tickers,
            document_types=document_types,
            periods=periods,
            object_types=object_types,
            include_rejected=include_rejected,
        )
        return self.conn.execute(
            f"SELECT * FROM objects {where} ORDER BY ticker, doc_type_key, period, type LIMIT ?",
            [*params, limit],
        ).fetchall()

    def _compact_bundles_from_rows(self, rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
        if not rows:
            return []
        object_ids = [row["id"] for row in rows if row["id"]]
        trace_by_id = self._traceability_by_id(object_ids)
        bundles: list[dict[str, Any]] = []
        for row in rows:
            obj = _object_from_row(row)
            trace = trace_by_id.get(row["id"], {})
            bundles.append(
                {
                    "id": row["id"],
                    "type": row["type"],
                    "ticker": row["ticker"],
                    "document_type": row["document_type"],
                    "period": row["period"],
                    "section": row["section_name"],
                    "text": row["text"] or _display_text(obj),
                    "object": obj,
                    "evidence": {
                        "claims": [],
                        "quotes": [],
                        "spans": [],
                        "related_objects": [],
                        "metric_lineage": None,
                    },
                    "quality": {
                        "object_status": obj.get("review_status"),
                        "section_quality": None,
                        "evidence_grade": obj.get("evidence_grade"),
                        "events": [],
                    },
                    "document": {},
                    "trace_status": trace.get("trace_status"),
                    "evidence_chain_count": trace.get("evidence_chain_count"),
                    "support_depth": trace.get("support_depth"),
                    "support_quote_count": trace.get("support_quote_count"),
                    "support_claim_count": trace.get("support_claim_count"),
                    "support_link_count": trace.get("support_link_count"),
                    "metric_lineage_status": trace.get("metric_lineage_status"),
                    "answer_candidate": bool(trace.get("answer_candidate")),
                    "compact_only": True,
                    "trace_id": row["id"],
                    "trace_required": False,
                    "trace_policy": {
                        "mode": "lazy",
                        "reason": "query_compact_fast_path_skips_evidence_bundle_expansion",
                    },
                }
            )
        return bundles

    def _traceability_by_id(self, object_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        ids = [object_id for object_id in object_ids if object_id]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT * FROM object_traceability WHERE object_id IN ({placeholders})",
            ids,
        ).fetchall()
        return {
            row["object_id"]: {
                "trace_status": row["trace_status"],
                "evidence_chain_count": row["evidence_chain_count"],
                "support_depth": row["support_depth"],
                "support_quote_count": row["support_quote_count"],
                "support_claim_count": row["support_claim_count"],
                "support_link_count": row["support_link_count"],
                "metric_lineage_status": row["metric_lineage_status"],
                "answer_candidate": row["answer_candidate"],
            }
            for row in rows
        }

    def _query_metric(
        self,
        metric: str,
        *,
        ticker: str,
        document_types: Iterable[str] | None,
        periods: Iterable[str] | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        where, params = _object_filters(
            tickers=[ticker],
            document_types=document_types,
            periods=periods,
            object_types=("MetricObservation",),
            include_rejected=False,
        )
        return self.conn.execute(
            f"""
            SELECT * FROM objects
            {where} AND metric_name = ?
            ORDER BY period DESC, type
            LIMIT ?
            """,
            [*params, metric, limit],
        ).fetchall()

    def _topic_map_objects(
        self,
        *,
        ticker: str,
        object_types: Iterable[str],
        document_types: Iterable[str] | None,
        periods: Iterable[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        where, params = _object_filters(
            tickers=[ticker],
            document_types=document_types,
            periods=periods,
            object_types=object_types,
            include_rejected=False,
        )
        rows = self.conn.execute(
            f"""
            SELECT * FROM objects
            {where}
            ORDER BY period DESC, type, id
            LIMIT ?
            """,
            [*params, max(1, int(limit))],
        ).fetchall()
        return [_object_from_row(row) for row in rows]

    def _edge_paths(
        self,
        object_id: str,
        *,
        direction: str,
        max_depth: int,
        include_quote_text: bool,
    ) -> list[dict[str, Any]]:
        if max_depth <= 0:
            return []

        paths: list[dict[str, Any]] = []
        queue: deque[tuple[str, list[dict[str, Any]], set[str]]] = deque()
        queue.append((object_id, [], {object_id}))
        while queue and len(paths) < _CHAIN_MAX_PATHS:
            current_id, current_steps, seen_ids = queue.popleft()
            if len(current_steps) >= max_depth:
                continue
            for edge, neighbor in self._direct_edge_neighbors(current_id, direction=direction):
                neighbor_id = neighbor.get("id")
                if not neighbor_id or neighbor_id in seen_ids:
                    continue
                step = {
                    "direction": edge.pop("_chain_direction"),
                    "edge": _compact_edge(edge),
                    "object": _chain_node(neighbor, include_quote_text=include_quote_text),
                }
                next_steps = [*current_steps, step]
                paths.append({"depth": len(next_steps), "steps": next_steps})
                queue.append((neighbor_id, next_steps, {*seen_ids, neighbor_id}))
                if len(paths) >= _CHAIN_MAX_PATHS:
                    break
        return paths

    def _direct_edge_neighbors(
        self,
        object_id: str,
        *,
        direction: str,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        if direction in {"both", "outgoing"}:
            rows.extend(self._edge_neighbor_rows(object_id, outgoing=True))
        if direction in {"both", "incoming"}:
            rows.extend(self._edge_neighbor_rows(object_id, outgoing=False))
        return rows

    def _edge_neighbor_rows(
        self,
        object_id: str,
        *,
        outgoing: bool,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        from_column, to_column = ("from_id", "to_id") if outgoing else ("to_id", "from_id")
        rows = self.conn.execute(
            f"""
            SELECT edges.*, objects.json AS object_json, objects.review_status AS object_review_status
            FROM edges
            JOIN objects ON objects.id = edges.{to_column}
            WHERE edges.{from_column} = ?
              AND (objects.review_status IS NULL OR objects.review_status != 'rejected')
            ORDER BY edges.relation_id, objects.type, objects.id
            LIMIT 20
            """,
            (object_id,),
        ).fetchall()
        neighbors: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in rows:
            edge = _edge_from_row(row)
            edge["_chain_direction"] = "outgoing" if outgoing else "incoming"
            neighbor = json.loads(row["object_json"])
            if row["object_review_status"]:
                neighbor["review_status"] = row["object_review_status"]
            neighbors.append((edge, neighbor))
        return neighbors

    def _semantic_neighbors_for_chain(
        self,
        obj: dict[str, Any],
        *,
        evidence: dict[str, list[dict[str, Any]]],
        graph_paths: list[dict[str, Any]],
        include_quote_text: bool,
    ) -> list[dict[str, Any]]:
        neighbors: dict[str, dict[str, Any]] = {}

        def add_neighbor(candidate: dict[str, Any], *, via: dict[str, Any]) -> None:
            candidate_id = candidate.get("id")
            if (
                not candidate_id
                or candidate_id == obj.get("id")
                or candidate.get("type") not in _SEMANTIC_NEIGHBOR_TYPES
            ):
                return
            entry = neighbors.setdefault(
                candidate_id,
                {
                    "object": _chain_node(candidate, include_quote_text=include_quote_text),
                    "via": [],
                },
            )
            entry["via"].append(via)

        for related in evidence.get("related_objects", []):
            add_neighbor(related, via={"source": "shared_claim"})

        for path in graph_paths:
            for step in path.get("steps", []):
                candidate = step.get("object") or {}
                add_neighbor(
                    candidate,
                    via={
                        "source": "edge",
                        "depth": path.get("depth"),
                        "direction": step.get("direction"),
                        "relation_id": (step.get("edge") or {}).get("relation_id"),
                    },
                )

        return list(neighbors.values())[:20]

    def _temporal_context_for_object(
        self,
        obj: dict[str, Any],
        *,
        evidence: dict[str, list[dict[str, Any]]],
        graph_paths: list[dict[str, Any]],
        include_quote_text: bool,
    ) -> list[dict[str, Any]]:
        object_ids = {obj.get("id")}
        object_ids.update(claim.get("id") for claim in evidence.get("claims", []) if claim.get("id"))
        object_ids.update(quote.get("id") for quote in evidence.get("quotes", []) if quote.get("id"))
        for path in graph_paths:
            for step in path.get("steps", []):
                step_object = step.get("object") or {}
                if step_object.get("id"):
                    object_ids.add(step_object["id"])

        rows = self.conn.execute(
            """
            SELECT *
            FROM objects
            WHERE ticker = ?
              AND type IN ('TemporalLink', 'TrendObservation', 'ChangeEvent')
              AND (review_status IS NULL OR review_status != 'rejected')
            ORDER BY period DESC, type, id
            LIMIT 200
            """,
            (obj.get("ticker"),),
        ).fetchall()
        temporal: list[dict[str, Any]] = []
        for row in rows:
            candidate = _object_from_row(row)
            if candidate.get("id") == obj.get("id") or _temporal_references_object(candidate, object_ids):
                temporal.append(_chain_node(candidate, include_quote_text=include_quote_text))
            if len(temporal) >= _CHAIN_MAX_TEMPORAL_CONTEXT:
                break
        return temporal

    def _expand_evidence(self, obj: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        claims: list[dict[str, Any]] = []
        quotes: list[dict[str, Any]] = []
        spans: list[dict[str, Any]] = []

        obj_type = obj.get("type")
        if obj_type == "ResearchClaim":
            claims = [obj]
            quotes = _dedupe_objects([
                *self._objects_by_ids(obj.get("supported_by_quotes") or []),
                *self._support_objects_for(obj["id"], support_types={"EvidenceQuote"}),
            ])
        elif obj_type == "EvidenceQuote":
            quotes = [obj]
            claims = self._claims_supported_by_quote(obj["id"])
        elif obj_type in {"BusinessFactor", "AgreementTerm", "BusinessEvent", "BusinessActivity", "ExternalFactorExposure"}:
            claims = _dedupe_objects([
                *self._objects_by_ids(obj.get("supported_by_claims") or []),
                *self._support_objects_for(obj["id"], support_types={"ResearchClaim"}),
            ])
            quote_ids = _unique(
                quote_id
                for claim in claims
                for quote_id in claim.get("supported_by_quotes") or []
            )
            quote_ids = _unique([*quote_ids, *(obj.get("supported_by_quotes") or [])])
            quotes = _dedupe_objects([
                *self._objects_by_ids(quote_ids),
                *self._support_objects_for(obj["id"], support_types={"EvidenceQuote"}),
            ])
        elif obj_type == "AssumptionCandidate":
            quotes = _dedupe_objects([
                *self._objects_by_ids(obj.get("supported_by_quotes") or []),
                *self._support_objects_for(obj["id"], support_types={"EvidenceQuote"}),
            ])
            claims = _dedupe_objects([
                *self._objects_by_ids(obj.get("supported_by_claims") or []),
                *self._support_objects_for(obj["id"], support_types={"ResearchClaim"}),
            ])
        elif obj_type == "ChangeEvent":
            quotes = _dedupe_objects([
                *self._objects_by_ids(obj.get("supported_by_quotes") or []),
                *self._support_objects_for(obj["id"], support_types={"EvidenceQuote"}),
            ])
            claims = _dedupe_objects([
                *self._objects_by_ids(obj.get("supported_by_claims") or []),
                *self._support_objects_for(obj["id"], support_types={"ResearchClaim"}),
            ])
        elif obj_type == "CompanyBusinessProfile":
            source_objects = self._objects_by_ids(obj.get("source_object_ids") or [])
            claims = self._claims_from_source_objects(source_objects)
            quote_ids = _unique(
                [
                    *[
                        quote_id
                        for claim in claims
                        for quote_id in claim.get("supported_by_quotes") or []
                    ],
                    *[
                        quote_id
                        for source in source_objects
                        for quote_id in source.get("supported_by_quotes") or []
                    ],
                ]
            )
            quotes = self._objects_by_ids(quote_ids)
        elif obj_type == "TrendObservation":
            source_objects = self._objects_by_ids(obj.get("supported_by_objects") or [])
            claims = self._claims_from_source_objects(source_objects)
            quote_ids = _unique(
                quote_id
                for claim in claims
                for quote_id in claim.get("supported_by_quotes") or []
            )
            quotes = self._objects_by_ids(quote_ids)

        span_ids = _unique(quote.get("source_span_id") for quote in quotes if quote.get("source_span_id"))
        spans = self._objects_by_ids(span_ids)
        related_objects = self._objects_sharing_claims(obj, claims)
        evidence: dict[str, Any] = {
            "claims": [_compact_object(claim) for claim in claims],
            "quotes": [_compact_object(quote) for quote in quotes],
            "spans": [_compact_object(span) for span in spans],
            "related_objects": [_compact_object(related) for related in related_objects],
        }
        if obj_type == "MetricObservation":
            evidence["metric_lineage"] = self._metric_lineage_for(obj)
        return evidence

    def _metric_lineage_for(self, metric: dict[str, Any]) -> dict[str, Any]:
        calculation = self._metric_calculation(metric)
        input_metrics: list[dict[str, Any]] = []
        if calculation:
            input_metrics.extend(
                self._objects_by_ids(
                    [
                        *list(calculation.get("input_metric_ids") or []),
                        *list(calculation.get("source_metric_ids") or []),
                    ]
                )
            )
        input_metrics.extend(self._objects_by_ids(metric.get("source_metric_ids") or []))
        input_metrics = _dedupe_objects(input_metrics)

        fact_ids = list(metric.get("source_fact_ids") or [])
        for input_metric in input_metrics:
            fact_ids.extend(input_metric.get("source_fact_ids") or [])
        xbrl_facts = self._objects_by_ids(_unique(fact_ids))
        source_document_ids = _unique(
            fact.get("source_document_id")
            for fact in xbrl_facts
            if fact.get("source_document_id")
        )
        return {
            "trace_type": "metric_lineage",
            "formatted_value": format_metric_compact(metric),
            "calculation": _compact_object(calculation) if calculation else None,
            "input_metrics": [_compact_object(input_metric) for input_metric in input_metrics],
            "xbrl_facts": [_compact_object(fact) for fact in xbrl_facts],
            "source_document_ids": source_document_ids,
        }

    def _metric_calculation(self, metric: dict[str, Any]) -> dict[str, Any] | None:
        calculation_id = metric.get("calculation_id")
        if calculation_id:
            calculations = self._objects_by_ids([calculation_id])
            if calculations:
                return calculations[0]
        rows = self.conn.execute(
            """
            SELECT *
            FROM objects
            WHERE type = 'Calculation'
              AND json_extract(json, '$.output_metric_id') = ?
              AND (review_status IS NULL OR review_status != 'rejected')
            ORDER BY id
            LIMIT 1
            """,
            (metric.get("id"),),
        ).fetchall()
        return _object_from_row(rows[0]) if rows else None

    def _objects_by_ids(self, object_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = [object_id for object_id in object_ids if object_id]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT * FROM objects WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        by_id = {row["id"]: _object_from_row(row) for row in rows}
        return [by_id[object_id] for object_id in ids if object_id in by_id]

    def _claims_supported_by_quote(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT objects.*
            FROM edges
            JOIN objects ON objects.id = edges.to_id
            WHERE edges.from_id = ? AND edges.relation_id = 'supports'
            ORDER BY objects.id
            """,
            (quote_id,),
        ).fetchall()
        return _dedupe_objects([
            *[_object_from_row(row) for row in rows],
            *self._support_targets_for(quote_id, target_types={"ResearchClaim"}),
        ])

    def _support_objects_for(
        self,
        object_id: str,
        *,
        support_types: set[str],
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT support.*
            FROM objects AS links
            JOIN objects AS support
              ON support.id = COALESCE(
                    json_extract(links.json, '$.support_object_id'),
                    json_extract(links.json, '$.from_id')
                 )
            WHERE links.type = 'SupportLink'
              AND (
                    json_extract(links.json, '$.target_object_id') = ?
                 OR json_extract(links.json, '$.to_id') = ?
              )
              AND (support.review_status IS NULL OR support.review_status != 'rejected')
            ORDER BY support.type, support.id
            """,
            (object_id, object_id),
        ).fetchall()
        return [
            obj
            for obj in (_object_from_row(row) for row in rows)
            if obj.get("type") in support_types
        ]

    def _support_targets_for(
        self,
        support_id: str,
        *,
        target_types: set[str],
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT target.*
            FROM objects AS links
            JOIN objects AS target
              ON target.id = COALESCE(
                    json_extract(links.json, '$.target_object_id'),
                    json_extract(links.json, '$.to_id')
                 )
            WHERE links.type = 'SupportLink'
              AND (
                    json_extract(links.json, '$.support_object_id') = ?
                 OR json_extract(links.json, '$.from_id') = ?
              )
              AND (target.review_status IS NULL OR target.review_status != 'rejected')
            ORDER BY target.type, target.id
            """,
            (support_id, support_id),
        ).fetchall()
        return [
            obj
            for obj in (_object_from_row(row) for row in rows)
            if obj.get("type") in target_types
        ]

    def _claims_from_source_objects(self, source_objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        claim_ids = _unique(
            [
                *[
                    obj["id"]
                    for obj in source_objects
                    if obj.get("type") == "ResearchClaim" and obj.get("id")
                ],
                *[
                    claim_id
                    for obj in source_objects
                    for claim_id in obj.get("supported_by_claims") or []
                ],
            ]
        )
        return self._objects_by_ids(claim_ids)

    def _objects_sharing_claims(
        self,
        obj: dict[str, Any],
        claims: list[dict[str, Any]],
        *,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        claim_ids = set(obj.get("supported_by_claims") or [])
        if obj.get("type") == "ResearchClaim" and obj.get("id"):
            claim_ids.add(obj["id"])
        claim_ids.update(claim["id"] for claim in claims if claim.get("id"))
        if not claim_ids:
            return []

        rows = self.conn.execute(
            """
            SELECT * FROM objects
            WHERE ticker = ?
              AND document_type = ?
              AND period = ?
              AND type IN (
                'BusinessFactor',
                'AgreementTerm',
                'BusinessEvent',
                'BusinessActivity',
                'ExternalFactorExposure',
                'ChangeEvent'
              )
            ORDER BY type, id
            """,
            (obj.get("ticker"), obj.get("document_type"), obj.get("period")),
        ).fetchall()
        related: list[dict[str, Any]] = []
        for row in rows:
            candidate = _object_from_row(row)
            if candidate.get("id") == obj.get("id"):
                continue
            if claim_ids.intersection(candidate.get("supported_by_claims") or []):
                related.append(candidate)
            if len(related) >= limit:
                break
        return related

    def _document_for(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM documents
            WHERE ticker = ? AND document_type = ? AND period = ?
            """,
            (obj.get("ticker"), obj.get("document_type"), obj.get("period")),
        ).fetchone()
        return _document_from_row(row) if row else None

    def _quality_for_object(self, obj: dict[str, Any]) -> dict[str, Any]:
        document = self._document_for(obj) or {}
        rows = self.conn.execute(
            """
            SELECT * FROM quality_events
            WHERE object_id = ?
               OR (
                    ticker = ?
                AND document_type = ?
                AND period = ?
                AND category IN ('section_quality', 'batch_failure')
               )
            ORDER BY category
            """,
            (obj["id"], obj.get("ticker"), obj.get("document_type"), obj.get("period")),
        ).fetchall()
        return {
            "object_status": obj.get("review_status") or "unknown",
            "section_quality": document.get("section_quality_status"),
            "events": [_quality_from_row(row) for row in rows],
        }


def _metadata_json(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    if not _table_exists(conn, "metadata"):
        return {}
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_count(conn: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _count_by(conn: sqlite3.Connection, table_name: str, column_name: str) -> dict[str, int]:
    if not _table_exists(conn, table_name):
        return {}
    rows = conn.execute(
        f"""
        SELECT {column_name} AS key, COUNT(*) AS value
        FROM {table_name}
        GROUP BY {column_name}
        ORDER BY value DESC, key
        """
    ).fetchall()
    return {str(row["key"] or "unknown"): int(row["value"] or 0) for row in rows}


def _company_topic_payload(topic: Mapping[str, Any], *, include_internal_ids: bool) -> dict[str, Any]:
    payload = {
        "ticker": topic.get("ticker"),
        "period": topic.get("period"),
        "document_type": topic.get("document_type"),
        "topic_label": topic.get("topic_label"),
        "topic_summary": topic.get("topic_summary"),
        "topic_type": topic.get("topic_type"),
        "topic_family": topic.get("topic_family"),
        "evidence_strength": topic.get("evidence_strength"),
        "trace_status": topic.get("trace_status"),
        "evidence_chain_count": topic.get("evidence_chain_count") or 0,
        "support_quote_count": topic.get("support_quote_count") or 0,
        "support_claim_count": topic.get("support_claim_count") or 0,
        "support_metric_count": topic.get("support_metric_count") or 0,
        "impact_channels": topic.get("impact_channels") or [],
        "factor_terms": topic.get("factor_terms") or [],
        "metric_terms": topic.get("metric_terms") or [],
        "entity_terms": topic.get("entity_terms") or [],
        "mechanism_terms": topic.get("mechanism_terms") or [],
        "scenario_terms": topic.get("scenario_terms") or [],
        "specificity_score": topic.get("specificity_score"),
        "generic_score": topic.get("generic_score"),
        "boilerplate_score": topic.get("boilerplate_score"),
        "materiality_hint": topic.get("materiality_hint"),
        "materiality_score": topic.get("materiality_score"),
    }
    if include_internal_ids:
        payload.update(
            {
                "topic_id": topic.get("topic_id"),
                "primary_object_id": topic.get("primary_object_id"),
                "primary_object_type": topic.get("primary_object_type"),
                "source_object_ids": topic.get("source_object_ids") or [],
                "top_traceable_object_ids": topic.get("top_traceable_object_ids") or [],
                "untraced_object_ids": topic.get("untraced_object_ids") or [],
                "internal_only": {
                    "topic_id": True,
                    "primary_object_id": True,
                    "source_object_ids": True,
                    "top_traceable_object_ids": True,
                    "untraced_object_ids": True,
                },
            }
        )
    return payload


def _answerability_from_candidates(query_frame: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tiers = _collect_values(candidates, "tier")
    has_direct = any(str(tier) in {"traceable_direct", "traceable_metric_lineage"} for tier in tiers)
    has_related = any(
        str(tier)
        in {
            "traceable_related",
            "untraced_related",
            "broad_related_candidate",
            "untraced_direct_candidate",
            "related",
        }
        for tier in tiers
    )
    requires_direct = bool(query_frame.get("question_requires_direct_match") or query_frame.get("requires_direct_match"))
    needs_clarification = not candidates
    return {
        "direct_answerable": has_direct,
        "related_context_available": has_related,
        "negative_answer_supported": bool(requires_direct and not has_direct and has_related),
        "needs_user_clarification": needs_clarification,
        "recommended_answer_mode": (
            "direct_answer"
            if has_direct
            else "no_direct_evidence_with_related_context"
            if requires_direct and has_related
            else "related_context_only"
            if has_related
            else "not_answerable"
        ),
    }


def _compact_comparison_context(context: Mapping[str, Any]) -> dict[str, Any]:
    candidates = list(context.get("ticker_candidates") or [])
    return {
        "answerability": context.get("answerability") or {},
        "query_frame": context.get("query_frame") or {},
        "top_candidate": _comparison_candidate_summary(candidates[0]) if candidates else None,
        "recommended_tools": list(context.get("recommended_tools") or [])[:3],
    }


def _comparison_candidate_summary(candidate: Mapping[str, Any]) -> dict[str, Any]:
    match = candidate.get("match") or {}
    return {
        "ticker": candidate.get("ticker"),
        "topic_id": candidate.get("topic_id"),
        "topic_label": candidate.get("topic_label"),
        "primary_object_id": candidate.get("primary_object_id"),
        "primary_object_type": candidate.get("primary_object_type"),
        "tier": candidate.get("tier") or match.get("tier"),
        "semantic_relevance": candidate.get("semantic_relevance") or match.get("semantic_relevance"),
        "trace_status": candidate.get("trace_status") or match.get("trace_status"),
        "matched_required_facets": candidate.get("matched_required_facets") or match.get("matched_required_facets") or [],
        "missing_required_facets": candidate.get("missing_required_facets") or match.get("missing_required_facets") or [],
        "why_tier": candidate.get("why_tier") or match.get("why_tier"),
    }


def _comparison_evaluation_from_query_context(context: Mapping[str, Any]) -> dict[str, Any]:
    answerability = context.get("answerability") or {}
    candidates = list(context.get("ticker_candidates") or [])
    top = candidates[0] if candidates else {}
    match = top.get("match") if isinstance(top, Mapping) else {}
    if not isinstance(match, Mapping):
        match = {}
    tier = top.get("tier") or match.get("tier")
    semantic_relevance = top.get("semantic_relevance") or match.get("semantic_relevance")
    trace_status = top.get("trace_status") or match.get("trace_status")
    matched_required = top.get("matched_required_facets") or match.get("matched_required_facets") or []
    missing_required = top.get("missing_required_facets") or match.get("missing_required_facets") or []
    why_tier = top.get("why_tier") or match.get("why_tier")
    return {
        "direct_answerable": bool(answerability.get("direct_answerable")),
        "related_context_available": bool(answerability.get("related_context_available")),
        "negative_answer_supported": bool(answerability.get("negative_answer_supported")),
        "recommended_answer_mode": answerability.get("recommended_answer_mode"),
        "semantic_relevance": semantic_relevance,
        "trace_status": trace_status,
        "tier": tier,
        "matched_required_facets": list(matched_required),
        "missing_required_facets": list(missing_required),
        "why_tier": why_tier,
        "evidence_chain_count": top.get("evidence_chain_count") or 0,
        "support_depth": top.get("support_depth"),
        "support_quote_count": top.get("support_quote_count") or 0,
        "support_claim_count": top.get("support_claim_count") or 0,
        "source_object_ids": _object_ids_from_query_context(context),
        "top_traceable_object_ids": top.get("top_traceable_object_ids") or [],
    }


def _object_ids_from_query_context(context: Mapping[str, Any]) -> list[str]:
    candidates = list(context.get("ticker_candidates") or [])
    ids: list[str] = []
    for candidate in candidates:
        for key in ("top_traceable_object_ids", "top_object_ids", "source_object_ids", "primary_object_id"):
            value = candidate.get(key)
            if isinstance(value, list):
                ids.extend(str(item) for item in value if item)
            elif value:
                ids.append(str(value))
        for topic in candidate.get("matched_topics") or []:
            for key in ("top_traceable_object_ids", "source_object_ids", "primary_object_id"):
                value = topic.get(key)
                if isinstance(value, list):
                    ids.extend(str(item) for item in value if item)
                elif value:
                    ids.append(str(value))
        for reason in candidate.get("top_reasons") or []:
            for key in ("source_object_ids", "object_id"):
                value = reason.get(key)
                if isinstance(value, list):
                    ids.extend(str(item) for item in value if item)
                elif value:
                    ids.append(str(value))
    deduped: list[str] = []
    seen: set[str] = set()
    for object_id in ids:
        if object_id and object_id not in seen:
            seen.add(object_id)
            deduped.append(object_id)
    return deduped


def _merge_bundle_lists(*bundle_lists: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bundles in bundle_lists:
        for bundle in bundles:
            object_id = str(bundle.get("id") or "")
            if not object_id or object_id in seen:
                continue
            seen.add(object_id)
            merged.append(dict(bundle))
            if len(merged) >= max(1, int(limit)):
                return merged
    return merged


def _comparison_evaluation_from_items(items: Sequence[Mapping[str, Any]], *, metric: str | None) -> dict[str, Any]:
    if not items:
        return {
            "direct_answerable": False,
            "related_context_available": False,
            "negative_answer_supported": False,
            "recommended_answer_mode": "not_answerable",
            "tier": "not_answerable",
            "trace_status": "untraced",
            "why_tier": "No matching ontology objects were returned for this comparison key.",
        }
    first = items[0]
    tier = "traceable_metric_lineage" if metric else None
    evidence = first.get("evidence") or {}
    if tier is None:
        if evidence.get("metric_lineage"):
            tier = "traceable_metric_lineage"
        elif first.get("type") in {"EvidenceQuote", "ResearchClaim"}:
            tier = "traceable_direct"
        elif evidence.get("claims") or evidence.get("quotes"):
            tier = "traceable_related"
        else:
            tier = "untraced_direct_candidate"
    trace_status = "traceable" if tier in {"traceable_direct", "traceable_metric_lineage", "traceable_related"} else "untraced"
    return {
        "direct_answerable": tier in {"traceable_direct", "traceable_metric_lineage"},
        "related_context_available": tier == "traceable_related",
        "negative_answer_supported": False,
        "recommended_answer_mode": "direct_answer" if tier in {"traceable_direct", "traceable_metric_lineage"} else "related_context_only",
        "semantic_relevance": "direct" if tier in {"traceable_direct", "traceable_metric_lineage"} else "related",
        "trace_status": trace_status,
        "tier": tier,
        "matched_required_facets": [],
        "missing_required_facets": [],
        "why_tier": "Metric comparison uses traceable metric lineage." if tier == "traceable_metric_lineage" else "Comparison candidate inferred from returned evidence support.",
        "evidence_chain_count": 1 if trace_status == "traceable" else 0,
        "support_quote_count": len(evidence.get("quotes") or []),
        "support_claim_count": len(evidence.get("claims") or []),
    }


def _apply_comparison_evaluation(items: Sequence[dict[str, Any]], evaluation: Mapping[str, Any]) -> None:
    for item in items:
        for key in (
            "semantic_relevance",
            "trace_status",
            "tier",
            "matched_required_facets",
            "missing_required_facets",
            "why_tier",
            "evidence_chain_count",
            "support_depth",
            "support_quote_count",
            "support_claim_count",
        ):
            if item.get(key) is None and evaluation.get(key) is not None:
                item[key] = evaluation.get(key)


def _question_requires_direct_match(question: str) -> bool:
    normalized = str(question or "").lower()
    return any(
        term in normalized
        for term in (
            "direct exposure",
            "directly exposed",
            "directly affect",
            "직접",
            "직접 노출",
            "직접 영향",
            "직접적으로",
        )
    )


def _recommended_trace_tools(candidates: Sequence[Mapping[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    object_ids: list[str] = []
    for key in ("top_traceable_object_ids", "source_object_ids", "primary_object_id"):
        for value in _collect_values(candidates, key):
            if isinstance(value, list):
                object_ids.extend(str(item) for item in value if item)
            elif value:
                object_ids.append(str(value))
    selected: list[str] = []
    for object_id in object_ids:
        if object_id not in selected:
            selected.append(object_id)
        if len(selected) >= limit:
            break
    return [
        {
            "tool": "krw_ontology_trace",
            "object_id": object_id,
            "purpose": "verify_final_or_related_context",
            "internal_only": True,
        }
        for object_id in selected
    ]


def _final_answer_guidance(answerability: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    missing: list[str] = []
    for value in _collect_values(candidates, "missing_required_facets"):
        if isinstance(value, list):
            missing.extend(str(item) for item in value if item)
        elif value:
            missing.append(str(value))
    guidance = {
        "use_direct_evidence_only_when": "tier is traceable_direct or traceable_metric_lineage",
        "do_not_expose_internal_ids": True,
    }
    if answerability.get("recommended_answer_mode") == "no_direct_evidence_with_related_context":
        guidance["safe_answer_pattern"] = (
            "공시자료에서 요청한 직접 노출은 확인되지 않습니다. "
            "다만 관련 맥락은 별도로 구분해 설명할 수 있습니다."
        )
        guidance["missing_required_facets"] = sorted(set(missing))
    elif answerability.get("direct_answerable"):
        guidance["safe_answer_pattern"] = "공시자료에서 직접 확인되는 내용과 영향을 받는 사업/재무 채널을 함께 설명합니다."
    else:
        guidance["safe_answer_pattern"] = "현재 검색 맥락만으로는 단정하지 말고 확인되는 관련 맥락만 제한적으로 설명합니다."
    return guidance


def _collect_values(value: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, Mapping):
        if key in value:
            found.append(value[key])
        for child in value.values():
            found.extend(_collect_values(child, key))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_values(child, key))
    return found


def _ticker_coverage(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    document_counts = {
        str(row["ticker"]): int(row["count"])
        for row in conn.execute(
            "SELECT ticker, COUNT(*) AS count FROM documents GROUP BY ticker"
        ).fetchall()
    }
    object_counts = {
        str(row["ticker"]): int(row["count"])
        for row in conn.execute(
            "SELECT ticker, COUNT(*) AS count FROM objects GROUP BY ticker"
        ).fetchall()
    }
    coverage: dict[str, dict[str, Any]] = {}
    for ticker in sorted(set(document_counts) | set(object_counts)):
        document_count = document_counts.get(ticker, 0)
        object_count = object_counts.get(ticker, 0)
        status = "ready"
        warnings: list[str] = []
        if object_count and not document_count:
            status = "inconsistent"
            warnings.append("objects_without_documents")
        elif document_count and not object_count:
            status = "inconsistent"
            warnings.append("documents_without_objects")
        elif not document_count and not object_count:
            status = "not_indexed"
        coverage[ticker] = {
            "has_documents": bool(document_count),
            "has_objects": bool(object_count),
            "document_count": document_count,
            "object_count": object_count,
            "status": status,
            "warnings": warnings,
        }
    return coverage


def _object_filters(
    *,
    tickers: Iterable[str] | None,
    document_types: Iterable[str] | None,
    periods: Iterable[str] | None,
    object_types: Iterable[str] | None,
    include_rejected: bool,
) -> tuple[str, list[Any]]:
    parts = ["1 = 1"]
    params: list[Any] = []
    _add_in_filter(parts, params, "objects.ticker", [t.upper() for t in tickers or []])
    _add_in_filter(parts, params, "objects.document_type", list(document_types or []))
    _add_in_filter(parts, params, "objects.period", list(periods or []))
    _add_in_filter(parts, params, "objects.type", list(object_types or []))
    if not include_rejected:
        parts.append("(objects.review_status IS NULL OR objects.review_status != 'rejected')")
    return f"WHERE {' AND '.join(parts)}", params


def _company_topic_filters(
    *,
    tickers: Iterable[str] | None,
    document_types: Iterable[str] | None,
    periods: Iterable[str] | None,
) -> tuple[str, list[Any]]:
    parts = ["1 = 1"]
    params: list[Any] = []
    _add_in_filter(parts, params, "company_topic_index.ticker", [t.upper() for t in tickers or []])
    _add_in_filter(parts, params, "company_topic_index.document_type", list(document_types or []))
    _add_in_filter(parts, params, "company_topic_index.period", list(periods or []))
    return f"WHERE {' AND '.join(parts)}", params


def _combined_discovery_tier(semantic_relevance: str, trace_status: str) -> str:
    if semantic_relevance == "insufficient":
        return "insufficient"
    if trace_status in {"traceable", "traceable_metric_lineage"}:
        return "traceable_direct" if semantic_relevance == "direct" else "traceable_related"
    if semantic_relevance == "direct":
        return "untraced_direct_candidate"
    if trace_status in {"orphan", "untraced", "untraced_metric_candidate", "unknown"}:
        return "untraced_related"
    return "untraced_related"


def _why_discovery_tier(semantic_relevance: str, trace_status: str, tier: str) -> str:
    if tier == "traceable_direct":
        return "Matched query core premise and has an explicit evidence or metric trace."
    if tier == "untraced_direct_candidate":
        return "Matched query core premise, but no explicit evidence trace was available in the serving index."
    if tier == "traceable_related":
        return "Related to the query and has an explicit evidence or metric trace."
    if tier == "untraced_related":
        return "Related to the query, but evidence traceability is missing or weak."
    return f"semantic_relevance={semantic_relevance}, trace_status={trace_status}"


def _is_traceable_status(trace_status: Any) -> bool:
    return str(trace_status or "") in {"traceable", "traceable_metric_lineage"}


def _ticker_trace_status(topics: Iterable[Mapping[str, Any]]) -> str:
    statuses = [str(topic.get("trace_status") or "unknown") for topic in topics]
    if any(_is_traceable_status(status) for status in statuses):
        return "traceable"
    if any(status == "orphan" for status in statuses):
        return "orphan"
    return statuses[0] if statuses else "unknown"


def _topic_trace_counts(topics: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"traceable": 0, "untraced": 0, "orphan": 0, "evidence_chains": 0}
    for topic in topics:
        status = str(topic.get("trace_status") or "unknown")
        if _is_traceable_status(status):
            counts["traceable"] += 1
        elif status == "orphan":
            counts["orphan"] += 1
        else:
            counts["untraced"] += 1
        counts["evidence_chains"] += int(topic.get("evidence_chain_count") or 0)
    return counts


def _traceable_object_ids(topics: Iterable[Mapping[str, Any]]) -> list[str]:
    ids: list[str] = []
    for topic in topics:
        if not _is_traceable_status(topic.get("trace_status")):
            continue
        object_id = topic.get("primary_object_id")
        if object_id and object_id not in ids:
            ids.append(str(object_id))
    return ids


def _untraced_object_ids(topics: Iterable[Mapping[str, Any]]) -> list[str]:
    ids: list[str] = []
    for topic in topics:
        if _is_traceable_status(topic.get("trace_status")):
            continue
        object_id = topic.get("primary_object_id")
        if object_id and object_id not in ids:
            ids.append(str(object_id))
    return ids


def _topic_object_counts(topics: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for topic in topics:
        object_types = topic.get("dominant_object_types") or [topic.get("primary_object_type")]
        for object_type in object_types:
            key = str(object_type or "Unknown")
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _topic_evidence_counts(topics: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"quotes": 0, "claims": 0, "topics": 0}
    for topic in topics:
        counts["topics"] += 1
        counts["quotes"] += int(topic.get("support_quote_count") or 0)
        counts["claims"] += int(topic.get("support_claim_count") or 0)
    return counts


def _topic_reason(topic: Mapping[str, Any]) -> dict[str, Any]:
    match = topic.get("match") or {}
    reason: dict[str, Any] = {
        "topic_id": topic.get("topic_id"),
        "topic_label": topic.get("topic_label"),
        "object_id": topic.get("primary_object_id"),
        "object_type": topic.get("primary_object_type"),
        "tier": match.get("tier"),
        "semantic_relevance": match.get("semantic_relevance"),
        "trace_status": topic.get("trace_status"),
        "trace_method": topic.get("trace_method"),
        "evidence_chain_count": topic.get("evidence_chain_count") or 0,
        "support_depth": topic.get("support_depth"),
        "score": match.get("score"),
        "matched_core_terms": match.get("matched_core_terms") or [],
        "matched_mechanisms": match.get("matched_mechanisms") or [],
        "matched_impact_channels": match.get("matched_impact_channels") or [],
        "matched_generic_terms": match.get("matched_generic_terms") or [],
        "missing_required_facets": match.get("missing_required_facets") or [],
        "evidence_strength": topic.get("evidence_strength"),
        "source_object_ids": topic.get("source_object_ids") or [],
    }
    if match.get("why_direct"):
        reason["why_direct"] = match.get("why_direct")
    if match.get("why_not_direct"):
        reason["why_not_direct"] = match.get("why_not_direct")
    if match.get("why_tier"):
        reason["why_tier"] = match.get("why_tier")
    return reason


def _scope_where(
    *,
    ticker: str | None,
    document_types: Iterable[str] | None,
) -> tuple[str, list[Any]]:
    parts: list[str] = []
    params: list[Any] = []
    if ticker:
        parts.append("ticker = ?")
        params.append(ticker.upper())
    if document_types:
        values = list(document_types)
        placeholders = ",".join("?" for _ in values)
        parts.append(f"document_type IN ({placeholders})")
        params.extend(values)
    return (f"WHERE {' AND '.join(parts)}" if parts else ""), params


def _add_in_filter(parts: list[str], params: list[Any], column: str, values: list[Any]) -> None:
    if not values:
        return
    placeholders = ",".join("?" for _ in values)
    parts.append(f"{column} IN ({placeholders})")
    params.extend(values)


def _fts_query(topic: str, *, operator: str) -> str:
    terms = _query_terms(topic)
    if not terms:
        return ""
    if operator == "OR":
        return " OR ".join(f"{term}*" for term in terms)
    return " ".join(f"{term}*" for term in terms)


def _query_terms(topic: str | None) -> list[str]:
    return _unique(term.lower() for term in _TERM_RE.findall(topic or "") if len(term) > 1)


def _expanded_topic(topic: str) -> str:
    topic = " ".join(str(topic or "").split())
    if not topic:
        return ""
    topic_lower = topic.lower()
    expansions = [
        expansion
        for needle, expansion in _QUERY_EXPANSION_RULES
        if needle in topic_lower
    ]
    if not expansions:
        return topic
    return " ".join(_unique([topic, *expansions]))


def _split_topic_queries(topic: str, *, limit: int = 12) -> list[str]:
    terms = [
        term
        for term in _query_terms(topic)
        if term not in _SPLIT_TOPIC_STOP_TERMS and len(term) > 2
    ]
    chunks: list[str] = []
    chunks.extend(term for term in terms if "_" in term)
    chunks.extend(
        f"{left} {right}"
        for left, right in zip(terms, terms[1:])
        if left != right
    )
    chunks.extend(terms)
    return _unique(chunks)[:limit]


def _search_diagnostics(
    topic: str | None,
    *,
    result_count: int | None,
    search_strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    original_topic = topic or None
    terms = _query_terms(topic)
    fts_query = _fts_query(topic or "", operator="AND") if topic else None
    warnings: list[str] = []
    suggestions: list[str] = []
    expanded_terms = (search_strategy or {}).get("expanded_terms") or terms

    if topic and not terms:
        if result_count:
            warnings.append("topic_rewritten_for_search")
        else:
            warnings.append("empty_topic_after_tokenization")
            suggestions.append(
                "Use English/canonical company exposure terms or call krw_ontology_topic_map first."
            )
    if topic and terms and result_count == 0 and len(terms) >= 3:
        warnings.append("strict_and_query_may_be_too_narrow")
        suggestions.append("Split the topic into shorter focused queries.")
    elif topic and len(terms) >= _STRICT_TOPIC_WARNING_TERM_COUNT:
        warnings.append("long_strict_topic")
        suggestions.append("Prefer several small topic queries over one long topic.")

    return {
        "original_topic": original_topic,
        "normalized_terms": terms,
        "expanded_terms": expanded_terms,
        "fts_query": fts_query,
        "operator": "AND" if topic else None,
        "result_count": result_count,
        "search_strategy": search_strategy or {},
        "warnings": warnings,
        "suggestions": _unique(suggestions),
    }


def _object_from_row(row: sqlite3.Row) -> dict[str, Any]:
    obj = json.loads(row["json"])
    if row["review_status"]:
        obj["review_status"] = row["review_status"]
    return obj


def _dedupe_objects(objects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for obj in objects:
        object_id = obj.get("id")
        if not object_id or object_id in seen:
            continue
        seen.add(object_id)
        output.append(obj)
    return output


def _document_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "ticker": row["ticker"],
        "document_type": row["document_type"],
        "doc_type_key": row["doc_type_key"],
        "period": row["period"],
        "artifact_index_path": row["artifact_index_path"],
        "ontology_dir": row["ontology_dir"],
        "counts": json.loads(row["counts_json"]),
        "section_quality_status": row["section_quality_status"],
        "section_quality": json.loads(row["section_quality_json"]),
    }


def _quality_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "document_type": row["document_type"],
        "period": row["period"],
        "severity": row["severity"],
        "category": row["category"],
        "object_id": row["object_id"],
        "stage": row["stage"],
        "message": row["message"],
        "payload": json.loads(row["json"]),
    }


def _edge_from_row(row: sqlite3.Row) -> dict[str, Any]:
    edge = json.loads(row["json"])
    for key in (
        "id",
        "ticker",
        "document_type",
        "period",
        "source_document_id",
        "from_id",
        "to_id",
        "relation_id",
        "relation_name",
        "confidence",
        "review_status",
    ):
        if row[key] is not None:
            edge[key] = row[key]
    return edge


def _compact_edge(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": edge.get("id"),
        "relation_id": edge.get("relation_id"),
        "relation_name": edge.get("relation_name"),
        "from_id": edge.get("from_id"),
        "to_id": edge.get("to_id"),
        "edge_class": edge.get("edge_class"),
        "evidence_level": edge.get("evidence_level"),
        "generation_method": edge.get("generation_method"),
        "confidence": edge.get("confidence"),
        "review_status": edge.get("review_status"),
        "rationale": edge.get("rationale"),
    }


def _chain_node(obj: dict[str, Any], *, include_quote_text: bool) -> dict[str, Any]:
    node = _compact_object(obj)
    if obj.get("type") in {"EvidenceQuote", "SourceSpan"} and not include_quote_text:
        node.pop("text", None)
        node["text_available"] = bool(_display_text(obj))
    return node


def _normalize_chain_direction(direction: str) -> str:
    value = str(direction or "both").strip().lower()
    if value in {"incoming", "in", "upstream", "up"}:
        return "incoming"
    if value in {"outgoing", "out", "downstream", "down"}:
        return "outgoing"
    return "both"


def _temporal_references_object(candidate: dict[str, Any], object_ids: set[str | None]) -> bool:
    if not object_ids:
        return False
    candidate_type = candidate.get("type")
    if candidate_type == "TemporalLink":
        return candidate.get("from_object_id") in object_ids or candidate.get("to_object_id") in object_ids
    if candidate_type == "TrendObservation":
        return bool(set(candidate.get("supported_by_objects") or []).intersection(object_ids))
    if candidate_type == "ChangeEvent":
        referenced_ids = {
            *(candidate.get("affected_objects") or []),
            *(candidate.get("supported_by_claims") or []),
            *(candidate.get("supported_by_quotes") or []),
        }
        return bool(referenced_ids.intersection(object_ids))
    return False


def _chain_warnings(
    obj: dict[str, Any],
    evidence: dict[str, list[dict[str, Any]]],
    quality: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    if obj.get("review_status") == "rejected":
        warnings.append("object_is_rejected")
    if not evidence.get("claims") and not evidence.get("quotes") and obj.get("type") not in {
        "EvidenceQuote",
        "ResearchClaim",
        "SourceSpan",
        "MetricObservation",
        "Calculation",
        "XBRLFact",
    }:
        warnings.append("no_supporting_evidence_found")
    evidence_grade = obj.get("evidence_grade")
    if evidence_grade in {"derived", "unsupported"}:
        warnings.append(f"weak_evidence_grade:{evidence_grade}")
    if quality.get("section_quality") in {"warn", "fail"}:
        warnings.append(f"section_quality:{quality.get('section_quality')}")
    if quality.get("events"):
        warnings.append("quality_events_present")
    return _unique(warnings)


def _display_text(obj: dict[str, Any]) -> str:
    if obj.get("type") == "MetricObservation" or obj.get("metric_name"):
        return format_metric_compact(obj)
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
        value = obj.get(key)
        if value:
            return str(value)
    if obj.get("metric_name"):
        return f"{obj['metric_name']}: {obj.get('value')} {obj.get('unit')}"
    return obj.get("name") or obj.get("id", "")


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        output: list[str] = []
        for item in value:
            output.extend(_string_values(item))
        return output
    return [str(value)] if str(value).strip() else []


def _object_metrics(obj: dict[str, Any]) -> list[str]:
    return _unique(
        [
            *_string_values(obj.get("related_metrics")),
            *_string_values(obj.get("affects")),
            *_string_values(obj.get("impact_channel")),
            *_string_values(obj.get("impact_channels")),
        ]
    )


def _explicit_project_asset_values(obj: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "projects",
        "key_projects",
        "major_projects",
        "project_names",
        "assets",
        "key_assets",
        "major_assets",
        "projects_assets",
    ):
        values.extend(_string_values(obj.get(key)))
    return _unique(values)


def _canonical_topic_term(value: Any) -> str | None:
    values = _string_values(value)
    if not values:
        return None
    term = values[0].strip()
    return term or None


def _search_terms_for(value: Any) -> list[str]:
    terms: list[str] = []
    for item in _string_values(value):
        cleaned = " ".join(item.replace("_", " ").replace("-", " ").split())
        if cleaned:
            terms.append(cleaned)
        if item and item != cleaned:
            terms.append(item)
    return _unique(terms)


def _merge_topic_entry(
    collection: dict[str, dict[str, Any]],
    term_value: Any,
    *,
    source_id: str | None = None,
    search_terms: Iterable[Any] | None = None,
    related_channels: Iterable[str] | None = None,
    related_metrics: Iterable[str] | None = None,
    evidence_grade: str | None = None,
) -> None:
    term = _canonical_topic_term(term_value)
    if not term:
        return
    key = term.lower().replace(" ", "_").replace("-", "_")
    entry = collection.setdefault(
        key,
        {
            "term": key,
            "label": term,
            "search_terms": [],
            "related_channels": [],
            "related_metrics": [],
            "source_object_ids": [],
            "evidence_grades": [],
        },
    )
    entry["search_terms"] = _unique(
        [
            *entry["search_terms"],
            *_search_terms_for(term),
            *[
                search_term
                for value in search_terms or []
                for search_term in _search_terms_for(value)
            ],
        ]
    )
    entry["related_channels"] = _unique(
        [*entry["related_channels"], *_string_values(list(related_channels or []))]
    )
    entry["related_metrics"] = _unique(
        [*entry["related_metrics"], *_string_values(list(related_metrics or []))]
    )
    if source_id:
        entry["source_object_ids"] = _unique([*entry["source_object_ids"], source_id])
    if evidence_grade:
        entry["evidence_grades"] = _unique([*entry["evidence_grades"], evidence_grade])


def _topic_entries(collection: dict[str, dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    entries = sorted(
        collection.values(),
        key=lambda item: (-len(item.get("source_object_ids") or []), item.get("term") or ""),
    )
    compacted: list[dict[str, Any]] = []
    for entry in entries[:limit]:
        compacted.append(
            {
                "term": entry["term"],
                "label": entry["label"],
                "search_terms": entry["search_terms"][:6],
                "related_channels": entry["related_channels"][:8],
                "related_metrics": entry["related_metrics"][:8],
                "source_object_ids": entry["source_object_ids"][:8],
                "evidence_grades": entry["evidence_grades"][:4],
            }
        )
    return compacted


def _suggest_topic_queries(
    *,
    external_factors: dict[str, dict[str, Any]],
    business_activities: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for entry in _topic_entries(external_factors, limit=5):
        topic = (entry.get("search_terms") or [entry["label"]])[0]
        suggestions.append(
            {
                "topic": topic,
                "object_types": ["ExternalFactorExposure", "BusinessFactor", "ResearchClaim"],
                "source_object_ids": entry.get("source_object_ids", [])[:3],
            }
        )
    for entry in _topic_entries(business_activities, limit=5):
        topic = (entry.get("search_terms") or [entry["label"]])[0]
        suggestions.append(
            {
                "topic": topic,
                "object_types": ["BusinessActivity", "ResearchClaim", "EvidenceQuote"],
                "source_object_ids": entry.get("source_object_ids", [])[:3],
            }
        )
    return suggestions


def _compact_object(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": obj.get("id"),
        "type": obj.get("type"),
        "ticker": obj.get("ticker"),
        "document_type": obj.get("document_type"),
        "period": obj.get("period"),
        "section": obj.get("section_name") or obj.get("section_key"),
        "text": _display_text(obj),
    }


def _unique(values: Iterable[Any]) -> list[Any]:
    seen = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
