"""Python SDK for agent-friendly ontology retrieval."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

DEFAULT_QUERY_TYPES = (
    "EvidenceQuote",
    "ResearchClaim",
    "RiskFactor",
    "GrowthDriver",
    "Headwind",
    "BusinessActivity",
    "ExternalFactorExposure",
    "CompanyBusinessProfile",
    "TrendObservation",
    "ChangeEvent",
    "AssumptionCandidate",
)

_TERM_RE = re.compile(r"[A-Za-z0-9_]+")
_STRICT_TOPIC_WARNING_TERM_COUNT = 5


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
        selected_types = tuple(object_types or DEFAULT_QUERY_TYPES)
        if topic:
            rows = self._query_fts(
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
        return [self.bundle(row["id"]) for row in rows if row["id"]]

    def search_diagnostics(
        self,
        topic: str | None,
        *,
        result_count: int | None = None,
    ) -> dict[str, Any]:
        """Return deterministic diagnostics for the FTS topic query."""
        return _search_diagnostics(topic, result_count=result_count)

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
            object_types=("FinancialMetricValue", "DerivedMetricValue"),
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
            else:
                results[ticker] = self.query(
                    topic=topic,
                    tickers=[ticker],
                    document_types=document_types,
                    periods=periods,
                    limit=limit_per_ticker,
                )
        return {
            "mode": "metric" if metric else "topic",
            "topic": topic,
            "metric": metric,
            "tickers": ticker_list,
            "results": results,
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
        fts_query = _fts_query(topic, operator="AND")
        if not fts_query:
            return []
        return self._execute_fts(
            fts_query,
            tickers=tickers,
            document_types=document_types,
            periods=periods,
            object_types=object_types,
            include_rejected=include_rejected,
            limit=limit,
        )

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
            object_types=("FinancialMetricValue", "DerivedMetricValue"),
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

    def _expand_evidence(self, obj: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        claims: list[dict[str, Any]] = []
        quotes: list[dict[str, Any]] = []
        spans: list[dict[str, Any]] = []

        obj_type = obj.get("type")
        if obj_type == "ResearchClaim":
            claims = [obj]
            quotes = self._objects_by_ids(obj.get("supported_by_quotes") or [])
        elif obj_type == "EvidenceQuote":
            quotes = [obj]
            claims = self._claims_supported_by_quote(obj["id"])
        elif obj_type in {"RiskFactor", "GrowthDriver", "Headwind", "BusinessActivity", "ExternalFactorExposure"}:
            claims = self._objects_by_ids(obj.get("supported_by_claims") or [])
            quote_ids = _unique(
                quote_id
                for claim in claims
                for quote_id in claim.get("supported_by_quotes") or []
            )
            quote_ids = _unique([*quote_ids, *(obj.get("supported_by_quotes") or [])])
            quotes = self._objects_by_ids(quote_ids)
        elif obj_type == "AssumptionCandidate":
            quotes = self._objects_by_ids(obj.get("supported_by_quotes") or [])
            claims = self._objects_by_ids(obj.get("supported_by_claims") or [])
        elif obj_type == "ChangeEvent":
            quotes = self._objects_by_ids(obj.get("supported_by_quotes") or [])
            claims = self._objects_by_ids(obj.get("supported_by_claims") or [])
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
        return {
            "claims": [_compact_object(claim) for claim in claims],
            "quotes": [_compact_object(quote) for quote in quotes],
            "spans": [_compact_object(span) for span in spans],
            "related_objects": [_compact_object(related) for related in related_objects],
        }

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
        return [_object_from_row(row) for row in rows]

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
                'RiskFactor',
                'GrowthDriver',
                'Headwind',
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
    terms = [term.lower() for term in _TERM_RE.findall(topic) if len(term) > 1]
    terms = _unique(terms)
    if not terms:
        return ""
    if operator == "OR":
        return " OR ".join(f"{term}*" for term in terms)
    return " ".join(f"{term}*" for term in terms)


def _search_diagnostics(topic: str | None, *, result_count: int | None) -> dict[str, Any]:
    original_topic = topic or None
    terms = _unique(term.lower() for term in _TERM_RE.findall(topic or "") if len(term) > 1)
    fts_query = _fts_query(topic or "", operator="AND") if topic else None
    warnings: list[str] = []
    suggestions: list[str] = []

    if topic and not terms:
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
        "fts_query": fts_query,
        "operator": "AND" if topic else None,
        "result_count": result_count,
        "warnings": warnings,
        "suggestions": _unique(suggestions),
    }


def _object_from_row(row: sqlite3.Row) -> dict[str, Any]:
    obj = json.loads(row["json"])
    if row["review_status"]:
        obj["review_status"] = row["review_status"]
    return obj


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
                "object_types": ["ExternalFactorExposure", "RiskFactor", "ResearchClaim"],
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
