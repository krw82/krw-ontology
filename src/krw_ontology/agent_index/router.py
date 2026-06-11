"""Shard-aware read router for agent index stores."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from krw_ontology.agent_index.store import OntologyStore


def open_ontology_store(
    index_path: Path | str,
    *,
    check_same_thread: bool = True,
    routing: str = "auto",
) -> OntologyStore | "OntologyStoreRouter":
    """Open a monolith store or shard-aware router for an agent index path."""
    resolved_routing = str(routing or "auto").strip().lower()
    if resolved_routing not in {"auto", "monolith", "shards"}:
        raise ValueError("routing must be one of: auto, monolith, shards")
    path = Path(index_path)
    if resolved_routing == "monolith":
        return OntologyStore(path, check_same_thread=check_same_thread)
    router = OntologyStoreRouter(path, check_same_thread=check_same_thread)
    if resolved_routing == "shards" and not router.shards_available:
        router.close()
        raise FileNotFoundError(f"shard layout not found for index: {path}")
    if resolved_routing == "auto" and not router.shards_available:
        router.close()
        return OntologyStore(path, check_same_thread=check_same_thread)
    return router


class OntologyStoreRouter:
    """Store-compatible facade that routes ticker-scoped reads to company shards."""

    def __init__(self, index_path: Path | str, *, check_same_thread: bool = True):
        self.index_path = Path(index_path).expanduser().resolve()
        self.index_dir = self.index_path.parent
        self.check_same_thread = check_same_thread
        self._manifest_path = self.index_dir / "shard_manifest.json"
        self._manifest = _read_json(self._manifest_path)
        self._catalog_path = self.index_dir / str(self._manifest.get("global_catalog") or "global_catalog.sqlite")
        self._global_topics_path = self.index_dir / str(self._manifest.get("global_topics") or "global_topics.sqlite")
        self._companies_dir = self.index_dir / str(self._manifest.get("companies_dir") or "companies")
        self._shard_paths = self._load_shard_paths()
        self._monolith: OntologyStore | None = None
        self._global_topics: OntologyStore | None = None
        self._shards: dict[str, OntologyStore] = {}
        self._closed = False

    @property
    def shards_available(self) -> bool:
        return bool(self._shard_paths) and self._catalog_path.exists() and self._companies_dir.exists()

    @property
    def global_topics_available(self) -> bool:
        return self._global_topics_path.exists()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._monolith_store().conn

    def close(self) -> None:
        if self._closed:
            return
        for store in self._shards.values():
            store.close()
        self._shards.clear()
        if self._monolith is not None:
            self._monolith.close()
            self._monolith = None
        if self._global_topics is not None:
            self._global_topics.close()
            self._global_topics = None
        self._closed = True

    def __enter__(self) -> "OntologyStoreRouter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def routing_status(self) -> dict[str, Any]:
        return {
            "mode": "shards" if self.shards_available else "monolith",
            "index_path": str(self.index_path),
            "global_catalog_path": str(self._catalog_path) if self._catalog_path.exists() else None,
            "global_topics_path": str(self._global_topics_path) if self._global_topics_path.exists() else None,
            "companies_dir": str(self._companies_dir) if self._companies_dir.exists() else None,
            "ticker_count": len(self._shard_paths),
            "open_shards": sorted(self._shards),
            "monolith_open": self._monolith is not None,
            "global_topics_open": self._global_topics is not None,
        }

    def list_companies(self) -> list[str]:
        if self.shards_available:
            return sorted(self._shard_paths)
        return self._monolith_store().list_companies()

    def _available_tickers(self, tickers: Sequence[str]) -> set[str]:
        normalized = {str(ticker).upper() for ticker in tickers if ticker}
        if self.shards_available:
            return {ticker for ticker in normalized if ticker in self._shard_paths}
        return self._monolith_store()._available_tickers(list(normalized))

    def list_documents(
        self,
        *,
        ticker: str | None = None,
        document_types: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        store = self._store_for_ticker(ticker) if ticker else self._monolith_store()
        return store.list_documents(ticker=ticker, document_types=document_types)

    def index_context(
        self,
        *,
        include_counts: bool = True,
        include_capabilities: bool = True,
        include_quality_summary: bool = True,
    ) -> dict[str, Any]:
        payload = self._monolith_store().index_context(
            include_counts=include_counts,
            include_capabilities=include_capabilities,
            include_quality_summary=include_quality_summary,
        )
        payload["routing"] = self.routing_status()
        return payload

    def company_context(self, *, ticker: str, **kwargs: Any) -> dict[str, Any]:
        return self._store_for_ticker(ticker).company_context(ticker=ticker, **kwargs)

    def query_context(self, *, question: str, ticker: str | None = None, tickers: Iterable[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        scoped_tickers = _normalize_tickers([ticker] if ticker else tickers)
        store = self._store_for_exact_tickers(scoped_tickers)
        return store.query_context(question=question, ticker=ticker, tickers=tickers, **kwargs)

    def get_object(self, object_id: str) -> dict[str, Any] | None:
        return self._store_for_object_id(object_id).get_object(object_id)

    def find_object_ids(self, prefix: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._store_for_object_id(prefix).find_object_ids(prefix, limit=limit)

    def query(self, **kwargs: Any) -> list[dict[str, Any]]:
        bundles, _diagnostics = self.query_with_diagnostics(**kwargs)
        return bundles

    def query_with_diagnostics(self, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        tickers = _normalize_tickers(kwargs.get("tickers"))
        if not tickers:
            return self._monolith_store().query_with_diagnostics(**kwargs)
        if len(tickers) == 1:
            store = self._store_for_ticker(tickers[0])
            bundles, diagnostics = store.query_with_diagnostics(**kwargs)
            route_mode = "company_shard" if tickers[0] in self._shard_paths else "monolith"
            diagnostics["routing"] = self._route_payload(route_mode, tickers)
            return bundles, diagnostics
        if not self._all_sharded(tickers):
            return self._monolith_store().query_with_diagnostics(**kwargs)
        return self._fanout_query_with_diagnostics(tickers, compact=False, **kwargs)

    def query_compact_with_diagnostics(self, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        tickers = _normalize_tickers(kwargs.get("tickers"))
        if not tickers:
            return self._monolith_store().query_compact_with_diagnostics(**kwargs)
        if len(tickers) == 1:
            store = self._store_for_ticker(tickers[0])
            rows, diagnostics = store.query_compact_with_diagnostics(**kwargs)
            route_mode = "company_shard" if tickers[0] in self._shard_paths else "monolith"
            diagnostics["routing"] = self._route_payload(route_mode, tickers)
            return rows, diagnostics
        if not self._all_sharded(tickers):
            return self._monolith_store().query_compact_with_diagnostics(**kwargs)
        return self._fanout_query_with_diagnostics(tickers, compact=True, **kwargs)

    def search_diagnostics(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._monolith_store().search_diagnostics(*args, **kwargs)

    def discover_company_topics(self, *, tickers: Iterable[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        scoped_tickers = _normalize_tickers(tickers)
        store = self._discovery_store_for_tickers(scoped_tickers)
        payload = store.discover_company_topics(tickers=tickers, **kwargs)
        diagnostics = payload.setdefault("search_diagnostics", {})
        if isinstance(diagnostics, dict):
            if store is self._global_topics:
                diagnostics["routing"] = self._route_payload("global_topics", scoped_tickers or [])
            elif scoped_tickers and len(scoped_tickers) == 1 and scoped_tickers[0] in self._shard_paths:
                diagnostics["routing"] = self._route_payload("company_shard", scoped_tickers)
        return payload

    def topic_map(self, *, ticker: str, **kwargs: Any) -> dict[str, Any]:
        return self._store_for_ticker(ticker).topic_map(ticker=ticker, **kwargs)

    def trace(self, object_id: str) -> dict[str, Any] | None:
        return self._store_for_object_id(object_id).trace(object_id)

    def chain(self, object_id: str, **kwargs: Any) -> dict[str, Any] | None:
        return self._store_for_object_id(object_id).chain(object_id, **kwargs)

    def bundle(self, object_id: str) -> dict[str, Any]:
        return self._store_for_object_id(object_id).bundle(object_id)

    def compare(self, *, tickers: Iterable[str], **kwargs: Any) -> dict[str, Any]:
        return self._fanout_compare(tickers=tickers, compact=False, **kwargs)

    def compare_compact(self, *, tickers: Iterable[str], **kwargs: Any) -> dict[str, Any]:
        return self._fanout_compare(tickers=tickers, compact=True, **kwargs)

    def quality(self, *, ticker: str | None = None, **kwargs: Any) -> dict[str, Any]:
        store = self._store_for_ticker(ticker) if ticker else self._monolith_store()
        return store.quality(ticker=ticker, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._monolith_store(), name)

    def _monolith_store(self) -> OntologyStore:
        if self._monolith is None:
            self._monolith = OntologyStore(self.index_path, check_same_thread=self.check_same_thread)
        return self._monolith

    def _global_topics_store(self) -> OntologyStore:
        if self._global_topics is None:
            self._global_topics = OntologyStore(self._global_topics_path, check_same_thread=self.check_same_thread)
        return self._global_topics

    def _store_for_ticker(self, ticker: str | None) -> OntologyStore:
        normalized = str(ticker or "").upper()
        if not normalized or normalized not in self._shard_paths:
            return self._monolith_store()
        store = self._shards.get(normalized)
        if store is None:
            store = OntologyStore(self._shard_paths[normalized], check_same_thread=self.check_same_thread)
            self._shards[normalized] = store
        return store

    def _store_for_exact_tickers(self, tickers: Sequence[str] | None) -> OntologyStore:
        if tickers and len(tickers) == 1:
            return self._store_for_ticker(tickers[0])
        return self._monolith_store()

    def _discovery_store_for_tickers(self, tickers: Sequence[str] | None) -> OntologyStore:
        if tickers and len(tickers) == 1:
            return self._store_for_ticker(tickers[0])
        if self.global_topics_available:
            return self._global_topics_store()
        return self._monolith_store()

    def _store_for_object_id(self, object_id: str) -> OntologyStore:
        ticker = _ticker_from_object_id(object_id, self._shard_paths.keys())
        return self._store_for_ticker(ticker) if ticker else self._monolith_store()

    def _all_sharded(self, tickers: Sequence[str]) -> bool:
        return bool(tickers) and all(ticker in self._shard_paths for ticker in tickers)

    def _fanout_query_with_diagnostics(
        self,
        tickers: Sequence[str],
        *,
        compact: bool,
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        limit = max(1, int(kwargs.get("limit") or 20))
        results: list[dict[str, Any]] = []
        child_diagnostics: dict[str, Any] = {}
        for ticker in tickers:
            ticker_kwargs = {**kwargs, "tickers": [ticker], "limit": limit}
            if compact:
                rows, diagnostics = self._store_for_ticker(ticker).query_compact_with_diagnostics(**ticker_kwargs)
            else:
                rows, diagnostics = self._store_for_ticker(ticker).query_with_diagnostics(**ticker_kwargs)
            child_diagnostics[ticker] = diagnostics
            results.extend(rows)
        diagnostics = {
            "result_count": min(len(results), limit),
            "routing": self._route_payload("company_shards", tickers),
            "shard_diagnostics": child_diagnostics,
        }
        return results[:limit], diagnostics

    def _fanout_compare(self, *, tickers: Iterable[str], compact: bool, **kwargs: Any) -> dict[str, Any]:
        ticker_list = _normalize_tickers(tickers) or []
        if not ticker_list or not self._all_sharded(ticker_list):
            compare_fn = self._monolith_store().compare_compact if compact else self._monolith_store().compare
            payload = compare_fn(tickers=ticker_list, **kwargs)
            payload["routing"] = self._route_payload("monolith", ticker_list)
            return payload
        results: dict[str, list[dict[str, Any]]] = {}
        evaluations: dict[str, dict[str, Any]] = {}
        contexts: dict[str, dict[str, Any]] = {}
        for ticker in ticker_list:
            compare_fn = self._store_for_ticker(ticker).compare_compact if compact else self._store_for_ticker(ticker).compare
            payload = compare_fn(tickers=[ticker], **kwargs)
            results[ticker] = list((payload.get("results") or {}).get(ticker) or [])
            evaluations[ticker] = dict((payload.get("comparison_evaluations") or {}).get(ticker) or {})
            contexts[ticker] = dict((payload.get("comparison_contexts") or {}).get(ticker) or {})
        topic = kwargs.get("topic")
        metric = kwargs.get("metric")
        payload = {
            "mode": "metric" if metric else "topic",
            "topic": topic,
            "metric": metric,
            "tickers": ticker_list,
            "results": results,
            "comparison_evaluations": evaluations,
            "comparison_contexts": contexts,
            "compact_fast_path": compact,
            "routing": self._route_payload("company_shards", ticker_list),
        }
        payload["kernel"] = self._monolith_store()._comparison_kernel_envelope(
            topic=topic,
            metric=metric,
            ticker_list=ticker_list,
            document_types=kwargs.get("document_types"),
            periods=kwargs.get("periods"),
            limit_per_ticker=int(kwargs.get("limit_per_ticker") or 5),
            results=results,
            comparison_contexts=contexts,
        )
        return payload

    def _route_payload(self, mode: str, tickers: Sequence[str]) -> dict[str, Any]:
        return {
            "mode": mode,
            "tickers": list(tickers),
            "global_catalog_path": str(self._catalog_path) if self._catalog_path.exists() else None,
            "global_topics_path": str(self._global_topics_path) if self._global_topics_path.exists() else None,
            "companies_dir": str(self._companies_dir) if self._companies_dir.exists() else None,
            "fallback": mode == "monolith",
        }

    def _load_shard_paths(self) -> dict[str, Path]:
        shard_rows = self._manifest.get("shards")
        paths: dict[str, Path] = {}
        if isinstance(shard_rows, Mapping):
            for ticker, entry in shard_rows.items():
                if not isinstance(entry, Mapping):
                    continue
                rel_path = entry.get("path")
                if not isinstance(rel_path, str) or not rel_path:
                    continue
                path = Path(rel_path)
                paths[str(ticker).upper()] = path if path.is_absolute() else self.index_dir / path
        return {ticker: path for ticker, path in paths.items() if path.exists()}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_tickers(tickers: Iterable[str] | None) -> list[str] | None:
    if tickers is None:
        return None
    normalized = [str(ticker).upper() for ticker in tickers if str(ticker or "").strip()]
    return normalized or None


def _ticker_from_object_id(object_id: str, known_tickers: Iterable[str]) -> str | None:
    known = {ticker.upper() for ticker in known_tickers}
    parts = re_split_object_id(object_id)
    for part in parts:
        candidate = part.upper()
        if candidate in known:
            return candidate
    return None


def re_split_object_id(object_id: str) -> list[str]:
    return [part for part in str(object_id or "").replace("/", ":").split(":") if part]
