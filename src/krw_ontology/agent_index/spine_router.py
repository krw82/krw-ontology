"""Monolith-free v3 router backed by global spine and company shards."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from krw_ontology.agent_index.store import OntologyStore
from krw_ontology.agent_index.spine_schema import GLOBAL_SPINE_LAYOUT, verify_global_spine_schema


class OntologySpineRouter:
    """Store-compatible facade for v3 global spine + company shard releases."""

    def __init__(self, global_spine_path: Path | str, *, check_same_thread: bool = True):
        self.index_path = Path(global_spine_path).expanduser().resolve()
        self.global_spine_path = self.index_path
        self.index_dir = self.global_spine_path.parent
        self.release_root = self.index_dir.parent
        self.check_same_thread = check_same_thread
        self._shard_manifest_path = self.index_dir / "shard_manifest.json"
        self._shard_manifest = _read_json(self._shard_manifest_path)
        self._declared_shard_paths, self._shard_paths, self._missing_shard_paths = self._load_shard_paths()
        self._spine_conn: sqlite3.Connection | None = None
        self._shards: dict[str, OntologyStore] = {}
        self._closed = False

    @classmethod
    def can_open(cls, path: Path | str) -> bool:
        candidate = Path(path).expanduser()
        if candidate.name == "global_spine.sqlite" and candidate.is_file():
            return verify_global_spine_schema(candidate)["ok"]
        return False

    @property
    def conn(self) -> sqlite3.Connection:
        return self._spine_connection()

    @property
    def shards_available(self) -> bool:
        return bool(self._shard_paths)

    def __enter__(self) -> "OntologySpineRouter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        for store in self._shards.values():
            store.close()
        self._shards.clear()
        if self._spine_conn is not None:
            self._spine_conn.close()
            self._spine_conn = None
        self._closed = True

    def routing_status(self) -> dict[str, Any]:
        return {
            "mode": "global_spine",
            "index_layout": GLOBAL_SPINE_LAYOUT,
            "global_spine_path": str(self.global_spine_path),
            "shard_manifest_path": str(self._shard_manifest_path),
            "release_root": str(self.release_root),
            "ticker_count": len(self._declared_shard_paths),
            "available_ticker_count": len(self._shard_paths),
            "missing_shard_count": len(self._missing_shard_paths),
            "missing_shards": {
                ticker: str(path) for ticker, path in sorted(self._missing_shard_paths.items())
            },
            "open_shards": sorted(self._shards),
            "fallback_enabled": False,
            "fallback": False,
        }

    def list_companies(self) -> list[str]:
        if self._declared_shard_paths:
            return sorted(self._declared_shard_paths)
        rows = self.conn.execute(
            "SELECT DISTINCT ticker FROM global_document_catalog ORDER BY ticker"
        ).fetchall()
        return [str(row[0]).upper() for row in rows]

    def _available_tickers(self, tickers: Sequence[str]) -> set[str]:
        requested = {str(ticker).upper() for ticker in tickers if str(ticker or "").strip()}
        return {ticker for ticker in requested if ticker in self._shard_paths}

    def list_documents(
        self,
        *,
        ticker: str | None = None,
        document_types: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        ticker_filter = str(ticker).upper() if ticker else None
        document_type_values = [str(value) for value in (document_types or []) if str(value or "").strip()]
        clauses: list[str] = []
        params: list[Any] = []
        if ticker_filter:
            clauses.append("ticker = ?")
            params.append(ticker_filter)
        if document_type_values:
            placeholders = ", ".join("?" for _ in document_type_values)
            clauses.append(f"document_type IN ({placeholders})")
            params.extend(document_type_values)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT ticker, document_type, period, source_path, object_count,
                   edge_count, quality_event_count, quality_status
            FROM global_document_catalog
            {where}
            ORDER BY ticker, document_type, period
            """,
            params,
        ).fetchall()
        return [
            {
                "ticker": row["ticker"],
                "document_type": row["document_type"],
                "doc_type_key": row["document_type"],
                "period": row["period"],
                "artifact_index_path": row["source_path"],
                "ontology_dir": None,
                "counts": {
                    "objects": int(row["object_count"] or 0),
                    "edges": int(row["edge_count"] or 0),
                    "quality_events": int(row["quality_event_count"] or 0),
                },
                "section_quality_status": row["quality_status"],
                "section_quality": {},
            }
            for row in rows
        ]

    def index_context(
        self,
        *,
        include_counts: bool = True,
        include_capabilities: bool = True,
        include_quality_summary: bool = True,
    ) -> dict[str, Any]:
        documents = self.list_documents()
        metadata = self._global_metadata()
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
            "index_path": str(self.global_spine_path),
            "index_layout": GLOBAL_SPINE_LAYOUT,
            "schema_version": metadata.get("schema_version"),
            "builder_version": metadata.get("builder_version"),
            "available_tickers": self.list_companies(),
            "available_document_types": sorted(document_types),
            "available_periods_by_ticker": {
                ticker: sorted(periods) for ticker, periods in periods_by_ticker.items()
            },
            "routing": self.routing_status(),
        }
        if include_counts:
            payload["object_counts"] = self._global_object_counts()
            payload["serving_counts"] = self._global_serving_counts()
            payload["serving_counts"]["available_company_shards"] = len(self._shard_paths)
            payload["serving_counts"]["missing_company_shards"] = len(self._missing_shard_paths)
        if include_capabilities:
            payload["capabilities"] = {
                "query": True,
                "retrieve": True,
                "trace": True,
                "chain": True,
                "compare": True,
                "quality": True,
                "company_context": True,
                "query_context": True,
                "global_spine": True,
                "company_shards": True,
                "fallback": False,
            }
        if include_quality_summary:
            payload["quality_summary"] = self._quality_summary()
        return payload

    def _global_metadata(self) -> dict[str, Any]:
        rows = self.conn.execute("SELECT key, value_json FROM metadata").fetchall()
        metadata: dict[str, Any] = {}
        for row in rows:
            try:
                metadata[str(row["key"])] = json.loads(str(row["value_json"]))
            except (json.JSONDecodeError, TypeError):
                metadata[str(row["key"])] = row["value_json"]
        return metadata

    def company_context(self, *, ticker: str, **kwargs: Any) -> dict[str, Any]:
        normalized = str(ticker or "").upper()
        if normalized in self._missing_shard_paths:
            return self._missing_shard_payload(ticker, operation="company_context")
        payload = self._store_for_ticker(ticker).company_context(ticker=ticker, **kwargs)
        payload.setdefault("routing", self._route_payload("company_shard", [normalized]))
        return payload

    def topic_map(self, *, ticker: str, **kwargs: Any) -> dict[str, Any]:
        normalized = str(ticker or "").upper()
        if normalized in self._missing_shard_paths:
            payload = self._missing_shard_payload(ticker, operation="topic_map")
            payload.setdefault("ticker", normalized)
            payload.setdefault("topics", [])
            return payload
        payload = self._store_for_ticker(ticker).topic_map(ticker=ticker, **kwargs)
        payload.setdefault("routing", self._route_payload("company_shard", [normalized]))
        return payload

    def query(self, **kwargs: Any) -> list[dict[str, Any]]:
        bundles, _diagnostics = self.query_with_diagnostics(**kwargs)
        return bundles

    def query_with_diagnostics(self, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return self._fanout_query(compact=False, **kwargs)

    def query_compact_with_diagnostics(self, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return self._fanout_query(compact=True, **kwargs)

    def query_context(
        self,
        *,
        question: str,
        ticker: str | None = None,
        tickers: Iterable[str] | None = None,
        limit_tickers: int = 5,
        **kwargs: Any,
    ) -> dict[str, Any]:
        scoped_tickers = _normalize_tickers([ticker] if ticker else tickers)
        candidate_tickers = self._candidate_tickers(
            question,
            explicit_tickers=scoped_tickers,
            limit=max(1, int(limit_tickers or 5)),
        )
        route_tickers = scoped_tickers or candidate_tickers
        contexts: list[dict[str, Any]] = []
        for candidate in candidate_tickers:
            context = self._store_for_ticker(candidate).query_context(
                question=question,
                tickers=[candidate],
                **kwargs,
            )
            context.setdefault("routing", self._route_payload("company_shard", [candidate]))
            contexts.append(context)
        if len(contexts) == 1:
            payload = contexts[0]
            payload["routing"] = self._route_payload("company_shard", route_tickers)
            self._attach_missing_release_parts(payload, route_tickers)
            _attach_spine_cross_company_pack(
                payload,
                question=question,
                requested_tickers=route_tickers,
                available_tickers=candidate_tickers,
                documents=self.list_documents(),
            )
            return payload
        payload = _merge_query_contexts(
            contexts,
            question=question,
            routing=self._route_payload("global_spine_fanout", route_tickers),
        )
        self._attach_missing_release_parts(payload, route_tickers)
        _attach_spine_cross_company_pack(
            payload,
            question=question,
            requested_tickers=route_tickers,
            available_tickers=candidate_tickers,
            documents=self.list_documents(),
        )
        return payload

    def discover_company_topics(
        self,
        *,
        question: str | None = None,
        tickers: Iterable[str] | None = None,
        limit_groups: int = 10,
        limit_per_group: int = 3,
        limit: int = 50,
        **kwargs: Any,
    ) -> dict[str, Any]:
        requested_tickers = _normalize_tickers(tickers)
        candidate_tickers = self._candidate_tickers(
            question or "",
            explicit_tickers=requested_tickers,
            limit=max(1, int(limit_groups or 10)),
        )
        route_tickers = requested_tickers or candidate_tickers
        shard_payloads: list[dict[str, Any]] = []
        for ticker in candidate_tickers:
            store = self._store_for_ticker(ticker)
            payload = store.discover_company_topics(
                question=question,
                tickers=[ticker],
                limit_groups=1,
                limit_per_group=limit_per_group,
                limit=limit,
                **kwargs,
            )
            if isinstance(payload, Mapping):
                shard_payloads.append(dict(payload))
        if shard_payloads:
            return _merge_discovery_payloads(
                shard_payloads,
                question=question,
                candidate_tickers=candidate_tickers,
                routing=self._route_payload("global_spine", route_tickers),
                limit=limit,
            )

        candidates: list[dict[str, Any]] = []
        for ticker in candidate_tickers:
            rows = self._topic_rows_for_ticker(ticker, question=question, limit=limit_per_group)
            candidates.append(
                {
                    "ticker": ticker,
                    "matched_topics": rows,
                    "top_topic": rows[0] if rows else None,
                    "score": rows[0].get("score") if rows else 0,
                    "routing": self._route_payload("global_spine", [ticker]),
                }
            )
        return {
            "question": question,
            "ticker_candidates": candidates[:limit],
            "search_diagnostics": {
                "routing": self._route_payload("global_spine", route_tickers),
                "candidate_tickers": candidate_tickers,
                "missing_shards": self._missing_shards_for_tickers(route_tickers),
                "unknown_tickers": self._unknown_tickers(route_tickers),
                "fallback_used": False,
            },
        }

    def get_object(self, object_id: str) -> dict[str, Any] | None:
        ticker = self._ticker_for_object(object_id)
        if ticker is None:
            return None
        if ticker in self._missing_shard_paths:
            return None
        return self._store_for_ticker(ticker).get_object(object_id)

    def find_object_ids(self, prefix: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT object_id AS id, ticker, object_type AS type, compact_label AS label,
                   compact_summary AS text
            FROM global_object_locator
            WHERE object_id LIKE ?
            ORDER BY object_id
            LIMIT ?
            """,
            (f"{prefix}%", max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def trace(self, object_id: str) -> dict[str, Any] | None:
        ticker = self._ticker_for_object(object_id)
        if ticker is None:
            return None
        locator = self._object_locator_payload(object_id, ticker)
        if ticker in self._missing_shard_paths:
            payload = self._missing_shard_payload(ticker, operation="trace")
            payload["object_id"] = object_id
            payload["object_locator"] = locator
            return payload
        payload = self._store_for_ticker(ticker).trace(object_id)
        if payload is not None:
            payload.setdefault("routing", self._route_payload("object_locator", [ticker]))
            payload.setdefault("object_locator", locator)
        return payload

    def chain(self, object_id: str, **kwargs: Any) -> dict[str, Any] | None:
        ticker = self._ticker_for_object(object_id)
        if ticker is None:
            return None
        locator = self._object_locator_payload(object_id, ticker)
        if ticker in self._missing_shard_paths:
            payload = self._missing_shard_payload(ticker, operation="chain")
            payload["object_id"] = object_id
            payload["object_locator"] = locator
            return payload
        chain = self._store_for_ticker(ticker).chain(object_id, **kwargs)
        if chain is None:
            return None
        chain["global_spine_neighbors"] = self._chain_neighbors(object_id)
        chain["routing"] = self._route_payload("object_locator", [ticker])
        chain["object_locator"] = locator
        return chain

    def bundle(self, object_id: str) -> dict[str, Any]:
        return self._store_for_object_id(object_id).bundle(object_id)

    def compare(self, *, tickers: Iterable[str], **kwargs: Any) -> dict[str, Any]:
        return self._fanout_compare(tickers=tickers, compact=False, **kwargs)

    def compare_compact(self, *, tickers: Iterable[str], **kwargs: Any) -> dict[str, Any]:
        return self._fanout_compare(tickers=tickers, compact=True, **kwargs)

    def quality(self, *, ticker: str | None = None, **kwargs: Any) -> dict[str, Any]:
        if ticker:
            if str(ticker or "").upper() in self._missing_shard_paths:
                payload = self._missing_shard_payload(ticker, operation="quality")
                return {
                    "documents": [],
                    "events": [],
                    "summary": {
                        "documents": 0,
                        "events": 0,
                        "rejected_objects": 0,
                        "batch_failures": 0,
                        "section_warnings": 0,
                        "status": "not_answerable_from_current_release",
                        "missing_parts": ["ticker_shard_missing"],
                        "missing_shards": payload["missing_shards"],
                    },
                    "routing": payload["routing"],
                }
            payload = self._store_for_ticker(ticker).quality(ticker=ticker, **kwargs)
            payload["routing"] = self._route_payload("company_shard", [ticker.upper()])
            return payload
        documents = self.list_documents()
        events: list[dict[str, Any]] = []
        for shard_ticker in sorted(self._shard_paths):
            shard_quality = self._store_for_ticker(shard_ticker).quality(ticker=shard_ticker, **kwargs)
            events.extend(shard_quality.get("events") or [])
        routing = self._route_payload("quality_release_scan", self.list_companies())
        return {
            "documents": documents,
            "events": events,
            "summary": {
                "documents": len(documents),
                "events": len(events),
                "rejected_objects": sum(1 for event in events if event.get("category") == "rejected_object"),
                "batch_failures": sum(1 for event in events if event.get("category") == "batch_failure"),
                "section_warnings": sum(1 for event in events if event.get("category") == "section_quality"),
                "missing_company_shards": len(self._missing_shard_paths),
                "missing_shards": {
                    ticker: str(path) for ticker, path in sorted(self._missing_shard_paths.items())
                },
            },
            "topology": self.routing_status(),
            "routing": routing,
        }

    def search_diagnostics(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "routing": self.routing_status(),
            "fallback_used": False,
            "args": len(args),
            "kwargs": sorted(kwargs),
        }

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"{type(self).__name__} has no fallback for {name!r}")

    def _fanout_query(self, *, compact: bool, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        explicit_tickers = _normalize_tickers(kwargs.get("tickers"))
        topic = str(kwargs.get("topic") or kwargs.get("question") or "")
        limit = max(1, int(kwargs.get("limit") or 20))
        candidate_tickers = self._candidate_tickers(topic, explicit_tickers=explicit_tickers, limit=min(limit, 10))
        route_tickers = explicit_tickers or candidate_tickers
        results: list[dict[str, Any]] = []
        shard_diagnostics: dict[str, Any] = {}
        primary_diagnostics: dict[str, Any] = {}
        for ticker in candidate_tickers:
            ticker_kwargs = {**kwargs, "tickers": [ticker], "limit": limit}
            store = self._store_for_ticker(ticker)
            if compact:
                rows, diagnostics = store.query_compact_with_diagnostics(**ticker_kwargs)
            else:
                rows, diagnostics = store.query_with_diagnostics(**ticker_kwargs)
            shard_diagnostics[ticker] = diagnostics
            if not primary_diagnostics and isinstance(diagnostics, Mapping):
                primary_diagnostics = dict(diagnostics)
            results.extend(rows)
            if len(results) >= limit:
                break
        diagnostics = dict(primary_diagnostics)
        diagnostics.update(
            {
            "result_count": min(len(results), limit),
            "routing": self._route_payload("global_spine_fanout", route_tickers),
            "shard_diagnostics": shard_diagnostics,
            "missing_shards": self._missing_shards_for_tickers(route_tickers),
            "unknown_tickers": self._unknown_tickers(route_tickers),
            "fallback_used": False,
            }
        )
        return results[:limit], diagnostics

    def _fanout_compare(self, *, tickers: Iterable[str], compact: bool, **kwargs: Any) -> dict[str, Any]:
        ticker_list = _normalize_tickers(tickers) or []
        results: dict[str, list[dict[str, Any]]] = {}
        evaluations: dict[str, dict[str, Any]] = {}
        contexts: dict[str, dict[str, Any]] = {}
        for ticker in ticker_list:
            if ticker not in self._shard_paths:
                reason = "ticker_shard_missing" if ticker in self._missing_shard_paths else "ticker_shard_not_found"
                results[ticker] = []
                evaluations[ticker] = {
                    "status": "missing",
                    "reason": reason,
                }
                contexts[ticker] = {
                    "research_status": "not_answerable_from_current_release",
                    "missing_parts": [reason],
                    "routing": self._route_payload("compare_fanout", [ticker]),
                    "missing_shards": self._missing_shards_for_tickers([ticker]),
                    "unknown_tickers": self._unknown_tickers([ticker]),
                }
                continue
            store = self._store_for_ticker(ticker)
            compare_fn = store.compare_compact if compact else store.compare
            payload = compare_fn(tickers=[ticker], **kwargs)
            results[ticker] = list((payload.get("results") or {}).get(ticker) or [])
            evaluations[ticker] = dict((payload.get("comparison_evaluations") or {}).get(ticker) or {})
            contexts[ticker] = dict((payload.get("comparison_contexts") or {}).get(ticker) or {})
            contexts[ticker].setdefault("routing", self._route_payload("company_shard", [ticker]))
        return {
            "mode": "metric" if kwargs.get("metric") else "topic",
            "topic": kwargs.get("topic"),
            "metric": kwargs.get("metric"),
            "tickers": ticker_list,
            "results": results,
            "comparison_evaluations": evaluations,
            "comparison_contexts": contexts,
            "compact_fast_path": compact,
            "missing_shards": self._missing_shards_for_tickers(ticker_list),
            "unknown_tickers": self._unknown_tickers(ticker_list),
            "fallback_used": False,
            "routing": self._route_payload("compare_fanout", ticker_list),
        }

    def _candidate_tickers(
        self,
        question: str,
        *,
        explicit_tickers: Sequence[str] | None,
        limit: int,
    ) -> list[str]:
        if explicit_tickers:
            return [ticker for ticker in explicit_tickers if ticker in self._shard_paths]
        terms = _fts_query(question)
        if terms:
            rows = self.conn.execute(
                """
                SELECT ticker, COUNT(*) AS hits
                FROM global_search_fts
                WHERE global_search_fts MATCH ?
                GROUP BY ticker
                ORDER BY hits DESC, ticker
                LIMIT ?
                """,
                (terms, max(1, int(limit))),
            ).fetchall()
            tickers = [str(row["ticker"]).upper() for row in rows if str(row["ticker"]).upper() in self._shard_paths]
            if tickers:
                return tickers
        rows = self.conn.execute(
            """
            SELECT ticker, COUNT(*) AS objects
            FROM global_object_locator
            GROUP BY ticker
            ORDER BY objects DESC, ticker
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [str(row["ticker"]).upper() for row in rows if str(row["ticker"]).upper() in self._shard_paths]

    def _store_for_ticker(self, ticker: str | None) -> OntologyStore:
        normalized = str(ticker or "").upper()
        if normalized in self._missing_shard_paths:
            raise FileNotFoundError(f"ticker shard missing: {normalized}: {self._missing_shard_paths[normalized]}")
        if normalized not in self._shard_paths:
            raise KeyError(f"ticker shard not found: {normalized or '<missing>'}")
        store = self._shards.get(normalized)
        if store is None:
            store = OntologyStore(self._shard_paths[normalized], check_same_thread=self.check_same_thread)
            self._shards[normalized] = store
        return store

    def _store_for_object_id(self, object_id: str) -> OntologyStore:
        ticker = self._ticker_for_object(object_id)
        if ticker is None:
            raise KeyError(f"object not found in global locator: {object_id}")
        return self._store_for_ticker(ticker)

    def _ticker_for_object(self, object_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT ticker FROM global_object_locator WHERE object_id = ?",
            (object_id,),
        ).fetchone()
        return str(row["ticker"]).upper() if row else None

    def _object_locator_payload(self, object_id: str, ticker: str | None = None) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT object_id, ticker, company_name, document_id, document_type, period,
                   filing_date, object_type, shard_id, shard_path, local_object_key,
                   object_hash, compact_label, compact_summary, quality_status
            FROM global_object_locator
            WHERE object_id = ?
            """,
            (object_id,),
        ).fetchone()
        normalized = str(ticker or (row["ticker"] if row else "") or "").upper()
        declared_path = self._declared_shard_paths.get(normalized)
        available_path = self._shard_paths.get(normalized)
        missing_path = self._missing_shard_paths.get(normalized)
        payload = dict(row) if row else {"object_id": object_id, "ticker": normalized}
        payload.update(
            {
                "ticker": normalized,
                "resolved_shard_path": str(available_path or declared_path or missing_path or ""),
                "shard_available": normalized in self._shard_paths,
                "shard_missing": normalized in self._missing_shard_paths,
                "fallback_used": False,
            }
        )
        return payload

    def _topic_rows_for_ticker(self, ticker: str, *, question: str | None, limit: int) -> list[dict[str, Any]]:
        params: list[Any] = [ticker]
        match_sql = ""
        terms = _like_terms(question or "")
        if terms:
            match_sql = "AND (" + " OR ".join("topic_label LIKE ? OR topic_summary LIKE ?" for _ in terms) + ")"
            params.extend(value for term in terms for value in (f"%{term}%", f"%{term}%"))
        params.append(max(1, int(limit)))
        rows = self.conn.execute(
            f"""
            SELECT topic_id, topic_key, topic_label, topic_summary, ticker,
                   evidence_grade, materiality
            FROM global_topic_spine
            WHERE ticker = ?
            {match_sql}
            ORDER BY COALESCE(materiality, 0) DESC, topic_label
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            {
                "topic_id": row["topic_id"],
                "topic_key": row["topic_key"],
                "topic_label": row["topic_label"],
                "topic_summary": row["topic_summary"],
                "ticker": row["ticker"],
                "evidence_grade": row["evidence_grade"],
                "materiality": row["materiality"],
                "score": row["materiality"] or 0,
            }
            for row in rows
        ]

    def _chain_neighbors(self, object_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT link_id, link_type, from_ticker, to_ticker, shared_key,
                   shared_key_type, from_object_id, to_object_id, weight,
                   confidence, explanation_template
            FROM global_chain_index
            WHERE from_object_id = ? OR to_object_id = ?
            ORDER BY COALESCE(weight, 0) DESC
            LIMIT 20
            """,
            (object_id, object_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def _global_object_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT object_type, COUNT(*) AS count
            FROM global_object_locator
            GROUP BY object_type
            ORDER BY object_type
            """
        ).fetchall()
        return {str(row["object_type"]): int(row["count"]) for row in rows}

    def _global_serving_counts(self) -> dict[str, int]:
        tables = (
            "global_object_locator",
            "global_document_catalog",
            "global_edge_spine",
            "global_factor_spine",
            "global_topic_spine",
            "global_metric_spine",
            "global_entity_spine",
            "global_counterparty_spine",
            "global_chain_index",
        )
        counts = {table: _count_table(self.conn, table) for table in tables}
        counts["objects"] = counts["global_object_locator"]
        counts["documents"] = counts["global_document_catalog"]
        counts["edges"] = counts["global_edge_spine"]
        counts["quality_events"] = sum(
            int(document["counts"].get("quality_events") or 0)
            for document in self.list_documents()
            if isinstance(document.get("counts"), Mapping)
        )
        return counts

    def _quality_summary(self) -> dict[str, Any]:
        documents = self.list_documents()
        statuses = Counter(str(document.get("section_quality_status") or "unknown") for document in documents)
        return {
            "document_quality_status": dict(statuses),
            "critical_errors": 0,
            "batch_failures": 0,
        }

    def _route_payload(self, mode: str, tickers: Sequence[str]) -> dict[str, Any]:
        normalized_tickers = [ticker for ticker in tickers if ticker]
        return {
            "mode": mode,
            "index_layout": GLOBAL_SPINE_LAYOUT,
            "tickers": normalized_tickers,
            "route_tickers": normalized_tickers,
            "global_spine_path": str(self.global_spine_path),
            "shard_manifest_path": str(self._shard_manifest_path),
            "companies_dir": str(self.index_dir / "companies"),
            "missing_shards": self._missing_shards_for_tickers(tickers),
            "unknown_tickers": self._unknown_tickers(tickers),
            "fallback": False,
            "fallback_used": False,
        }

    def _spine_connection(self) -> sqlite3.Connection:
        if self._spine_conn is None:
            self._spine_conn = sqlite3.connect(self.global_spine_path, check_same_thread=self.check_same_thread)
            self._spine_conn.row_factory = sqlite3.Row
        return self._spine_conn

    def _load_shard_paths(self) -> tuple[dict[str, Path], dict[str, Path], dict[str, Path]]:
        raw_shards = self._shard_manifest.get("shards")
        declared_paths: dict[str, Path] = {}
        paths: dict[str, Path] = {}
        missing_paths: dict[str, Path] = {}
        if isinstance(raw_shards, Mapping):
            for ticker, raw_entry in raw_shards.items():
                if not isinstance(raw_entry, Mapping):
                    continue
                raw_path = raw_entry.get("path") or raw_entry.get("shard_path")
                if not isinstance(raw_path, str) or not raw_path:
                    continue
                path = Path(raw_path)
                if path.is_absolute():
                    resolved = path.expanduser().resolve()
                elif path.parts and path.parts[0] == "indexes":
                    resolved = (self.release_root / path).resolve()
                else:
                    resolved = (self.index_dir / path).resolve()
                normalized = str(ticker).upper()
                declared_paths[normalized] = resolved
                if resolved.exists():
                    paths[normalized] = resolved
                else:
                    missing_paths[normalized] = resolved
        return declared_paths, paths, missing_paths

    def _missing_shards_for_tickers(self, tickers: Iterable[str] | None) -> dict[str, str]:
        normalized = _normalize_tickers(tickers) or []
        return {
            ticker: str(self._missing_shard_paths[ticker])
            for ticker in normalized
            if ticker in self._missing_shard_paths
        }

    def _unknown_tickers(self, tickers: Iterable[str] | None) -> list[str]:
        normalized = _normalize_tickers(tickers) or []
        known = set(self._declared_shard_paths) | set(self._shard_paths)
        return [ticker for ticker in normalized if ticker not in known]

    def _missing_shard_payload(self, ticker: str | None, *, operation: str) -> dict[str, Any]:
        normalized = str(ticker or "").upper()
        return {
            "error": {
                "code": "ticker_shard_missing",
                "message": f"Ticker shard is declared in shard_manifest but missing on disk: {normalized}",
                "details": {
                    "ticker": normalized,
                    "expected_shard_path": str(self._missing_shard_paths.get(normalized, "")),
                    "operation": operation,
                },
            },
            "ticker": normalized,
            "missing_parts": ["ticker_shard_missing"],
            "missing_shards": self._missing_shards_for_tickers([normalized]),
            "fallback_used": False,
            "routing": self._route_payload("company_shard_missing", [normalized]),
        }

    def _attach_missing_release_parts(self, payload: dict[str, Any], tickers: Iterable[str] | None) -> None:
        missing_shards = self._missing_shards_for_tickers(tickers)
        unknown_tickers = self._unknown_tickers(tickers)
        if not missing_shards and not unknown_tickers:
            return
        payload["missing_shards"] = missing_shards
        payload["unknown_tickers"] = unknown_tickers
        missing_parts = list(payload.get("missing_parts") or [])
        if missing_shards and "ticker_shard_missing" not in missing_parts:
            missing_parts.append("ticker_shard_missing")
        if unknown_tickers and "ticker_shard_not_found" not in missing_parts:
            missing_parts.append("ticker_shard_not_found")
        payload["missing_parts"] = missing_parts
        payload.setdefault("research_status", "not_answerable_from_current_release")
        payload.setdefault(
            "answerability",
            {
                "direct_answerable": False,
                "related_context_available": False,
                "negative_answer_supported": False,
                "needs_user_clarification": False,
                "recommended_answer_mode": "not_answerable_from_current_release",
            },
        )
        payload.setdefault("recommended_tools", [])
        payload.setdefault("do_not_call", ["broad_retrieve", "krw_ontology_retrieve"])
        payload.setdefault(
            "agent_autonomy",
            {
                "mode": "blocked_by_release_integrity",
                "allowed_next_tools": [],
                "max_additional_tool_calls": 0,
            },
        )


def _merge_query_contexts(
    contexts: Sequence[Mapping[str, Any]],
    *,
    question: str,
    routing: Mapping[str, Any],
) -> dict[str, Any]:
    documents: list[Any] = []
    objects: list[Any] = []
    bundles: list[Any] = []
    warnings: list[str] = []
    for context in contexts:
        documents.extend(context.get("documents") or [])
        objects.extend(context.get("objects") or [])
        bundles.extend(context.get("bundles") or context.get("evidence") or [])
        quality = context.get("quality") if isinstance(context.get("quality"), Mapping) else {}
        warnings.extend(str(item) for item in quality.get("warnings") or [])
    return {
        "question": question,
        "documents": documents,
        "objects": objects,
        "bundles": bundles,
        "quality": {"warnings": warnings},
        "routing": dict(routing),
    }


def _attach_spine_cross_company_pack(
    payload: dict[str, Any],
    *,
    question: str,
    requested_tickers: Sequence[str] | None,
    available_tickers: Sequence[str],
    documents: Sequence[Mapping[str, Any]],
) -> None:
    requested = [ticker for ticker in (requested_tickers or []) if ticker]
    if len(requested) < 2:
        return
    research_pack = payload.setdefault("research_pack", {})
    if not isinstance(research_pack, dict):
        return
    existing = research_pack.get("cross_company_signal_pack")
    if isinstance(existing, Mapping) and existing:
        return
    available = [ticker for ticker in available_tickers if ticker]
    document_by_ticker: dict[str, Mapping[str, Any]] = {}
    for document in documents:
        ticker = str(document.get("ticker") or "").upper()
        if ticker and ticker not in document_by_ticker:
            document_by_ticker[ticker] = document
    evidence_rows: list[dict[str, Any]] = []
    for ticker in available:
        document = document_by_ticker.get(ticker, {})
        evidence_rows.append(
            {
                "ticker": ticker,
                "period": document.get("period"),
                "document_type": document.get("document_type"),
                "signal": "filing_commentary",
                "evidence_strength": "medium",
                "commentary_summary": (
                    f"{ticker} current release contains filing evidence related to "
                    f"{_compact_text(question, max_chars=160)}"
                ),
            }
        )
    research_pack["cross_company_signal_pack"] = {
        "mode": "cross_company_signal_synthesis",
        "answer_policy": "Use this as compact cross-company evidence; cite shard evidence before strong claims.",
        "ticker_basket": requested,
        "available_tickers": available,
        "missing_tickers": [ticker for ticker in requested if ticker not in available],
        "company_evidence_rows": evidence_rows,
        "signals": [
            {
                "signal": "filing_evidence_available",
                "companies_supporting": available,
                "strength": "medium" if evidence_rows else "weak",
            }
        ],
        "quality": {
            "missing_parts": [],
            "fallback_used": False,
        },
    }


def _merge_discovery_payloads(
    payloads: Sequence[Mapping[str, Any]],
    *,
    question: str | None,
    candidate_tickers: Sequence[str],
    routing: Mapping[str, Any],
    limit: int,
) -> dict[str, Any]:
    ticker_candidates: list[dict[str, Any]] = []
    results_by_ticker: dict[str, Any] = {}
    query_frame: dict[str, Any] = {}
    diagnostics_by_ticker: dict[str, Any] = {}
    for payload in payloads:
        if not query_frame and isinstance(payload.get("query_frame"), Mapping):
            query_frame = dict(payload["query_frame"])
        if isinstance(payload.get("results_by_ticker"), Mapping):
            results_by_ticker.update(dict(payload["results_by_ticker"]))
        for candidate in payload.get("ticker_candidates") or []:
            if isinstance(candidate, Mapping):
                ticker_candidates.append(dict(candidate))
        diagnostics = payload.get("search_diagnostics")
        if isinstance(diagnostics, Mapping):
            for ticker in candidate_tickers:
                if ticker not in diagnostics_by_ticker:
                    diagnostics_by_ticker[ticker] = diagnostics
                    break
    return {
        "question": question,
        "query_frame": query_frame,
        "ticker_candidates": ticker_candidates[:limit],
        "results_by_ticker": results_by_ticker,
        "search_diagnostics": {
            "routing": dict(routing),
            "candidate_tickers": list(candidate_tickers),
            "shard_diagnostics": diagnostics_by_ticker,
            "fallback_used": False,
        },
    }


def _normalize_tickers(tickers: Iterable[str] | None) -> list[str] | None:
    if tickers is None:
        return None
    normalized = [str(ticker).upper() for ticker in tickers if str(ticker or "").strip()]
    return normalized or None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _count_table(conn: sqlite3.Connection, table_name: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _fts_query(text: str) -> str:
    terms = _like_terms(text)
    return " OR ".join(terms[:8])


def _like_terms(text: str) -> list[str]:
    terms: list[str] = []
    for raw in str(text or "").replace("-", " ").replace("_", " ").split():
        cleaned = "".join(ch for ch in raw.lower() if ch.isalnum())
        if len(cleaned) >= 3 and cleaned not in terms:
            terms.append(cleaned)
    return terms


def _compact_text(text: str, *, max_chars: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 3)].rstrip() + "..."
