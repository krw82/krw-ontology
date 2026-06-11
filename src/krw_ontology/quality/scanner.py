"""SQLite-backed quality scanner for ontology releases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from krw_ontology.quality.models import (
    BATCH_FAILURE,
    COVERAGE_GAP,
    DOCS_MISSING,
    NORMALIZE_NUMERIC,
    REPAIR_REFERENCE,
    SECTION_FAIL,
    SECTION_WARN,
    RepairJob,
    TickerQuality,
)


REFERENCE_STAGES = {"reference_validation", "reference_alias_resolution", "relation_validation"}
NUMERIC_STAGES = {"numeric_guard"}


class QualityScanner:
    """Read quality signals from an agent_index.sqlite database."""

    def __init__(self, index_path: Path | str):
        self.index_path = Path(index_path).expanduser().resolve()

    def scan(self, *, min_docs: int = 5) -> dict[str, Any]:
        with self._connect() as conn:
            metadata = self._build_metadata(conn)
            totals = {
                "documents": self._scalar(conn, "SELECT COUNT(*) FROM documents"),
                "tickers": self._scalar(conn, "SELECT COUNT(DISTINCT ticker) FROM documents"),
                "objects": self._scalar(conn, "SELECT COUNT(*) FROM objects"),
                "quality_events": self._scalar(conn, "SELECT COUNT(*) FROM quality_events"),
            }
            section_status = {
                row["section_quality_status"] or "unknown": row["cnt"]
                for row in conn.execute(
                    """
                    SELECT section_quality_status, COUNT(*) AS cnt
                    FROM documents
                    GROUP BY section_quality_status
                    """
                )
            }
            event_counts = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT category, severity, COALESCE(stage, '') AS stage, COUNT(*) AS count
                    FROM quality_events
                    GROUP BY category, severity, stage
                    ORDER BY count DESC
                    """
                )
            ]
            tickers = self.ticker_quality(min_docs=min_docs)
            problem_tickers = [
                ticker
                for ticker in tickers
                if ticker.problem_kinds(min_docs=min_docs)
            ]
            severity_counts = Counter(
                ticker.severity(min_docs=min_docs)
                for ticker in tickers
                if ticker.problem_kinds(min_docs=min_docs)
            )
            kind_counts = Counter(
                kind
                for ticker in tickers
                for kind in ticker.problem_kinds(min_docs=min_docs)
            )
            rejected_reasons = self.rejected_reason_summary(limit=20)
        return {
            "index_path": str(self.index_path),
            "metadata": metadata,
            "totals": totals,
            "section_status": section_status,
            "event_counts": event_counts,
            "problem_ticker_count": len(problem_tickers),
            "severity_counts": dict(severity_counts),
            "kind_counts": dict(kind_counts),
            "rejected_reasons": rejected_reasons,
            "min_docs": min_docs,
        }

    def ticker_quality(self, *, min_docs: int = 5) -> list[TickerQuality]:
        del min_docs
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH d AS (
                    SELECT ticker,
                           COUNT(*) AS docs,
                           SUM(CASE WHEN section_quality_status='fail' THEN 1 ELSE 0 END) AS section_fail,
                           SUM(CASE WHEN section_quality_status='warn' THEN 1 ELSE 0 END) AS section_warn
                    FROM documents
                    GROUP BY ticker
                ),
                e AS (
                    SELECT ticker,
                           SUM(CASE WHEN category='batch_failure' THEN 1 ELSE 0 END) AS batch_failure,
                           SUM(CASE WHEN category='coverage_gap' THEN 1 ELSE 0 END) AS coverage_gap,
                           SUM(CASE WHEN category='rejected_object' THEN 1 ELSE 0 END) AS rejected_object
                    FROM quality_events
                    GROUP BY ticker
                )
                SELECT d.ticker, d.docs, d.section_fail, d.section_warn,
                       COALESCE(e.batch_failure, 0) AS batch_failure,
                       COALESCE(e.coverage_gap, 0) AS coverage_gap,
                       COALESCE(e.rejected_object, 0) AS rejected_object
                FROM d
                LEFT JOIN e ON d.ticker=e.ticker
                ORDER BY d.ticker
                """
            ).fetchall()
        return [
            TickerQuality(
                ticker=row["ticker"],
                docs=int(row["docs"]),
                section_fail=int(row["section_fail"] or 0),
                section_warn=int(row["section_warn"] or 0),
                batch_failure=int(row["batch_failure"] or 0),
                coverage_gap=int(row["coverage_gap"] or 0),
                rejected_object=int(row["rejected_object"] or 0),
            )
            for row in rows
        ]

    def explain_ticker(self, ticker: str, *, min_docs: int = 5, limit: int = 20) -> dict[str, Any]:
        normalized = ticker.upper()
        with self._connect() as conn:
            docs = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT ticker, document_type, doc_type_key, period,
                           section_quality_status, section_quality_json,
                           artifact_index_path, ontology_dir
                    FROM documents
                    WHERE ticker=?
                    ORDER BY doc_type_key, period
                    """,
                    (normalized,),
                )
            ]
            events = [
                self._event_row(row)
                for row in conn.execute(
                    """
                    SELECT *
                    FROM quality_events
                    WHERE ticker=?
                    ORDER BY severity, category, doc_type_key, period
                    LIMIT ?
                    """,
                    (normalized, limit),
                )
            ]
            event_summary = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT category, severity, COALESCE(stage, '') AS stage, COUNT(*) AS count
                    FROM quality_events
                    WHERE ticker=?
                    GROUP BY category, severity, stage
                    ORDER BY count DESC
                    """,
                    (normalized,),
                )
            ]
            rejected_reasons = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        CASE
                            WHEN message LIKE 'Unsupported numeric values:%'
                                THEN 'Unsupported numeric values'
                            WHEN message LIKE 'Dangling references:%'
                                THEN 'Dangling references'
                            ELSE message
                        END AS reason,
                        COUNT(*) AS count
                    FROM quality_events
                    WHERE ticker=? AND category='rejected_object'
                    GROUP BY reason
                    ORDER BY count DESC
                    LIMIT ?
                    """,
                    (normalized, limit),
                )
            ]
        ticker_summary = next(
            (item for item in self.ticker_quality(min_docs=min_docs) if item.ticker == normalized),
            None,
        )
        return {
            "ticker": normalized,
            "summary": ticker_summary.to_dict(min_docs=min_docs) if ticker_summary else None,
            "documents": [self._document_row(doc) for doc in docs],
            "event_summary": event_summary,
            "events": events,
            "rejected_reasons": rejected_reasons,
            "min_docs": min_docs,
        }

    def events(
        self,
        *,
        ticker: str | None = None,
        category: str | None = None,
        stage: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if ticker:
            where.append("ticker=?")
            params.append(ticker.upper())
        if category:
            where.append("category=?")
            params.append(category)
        if stage:
            where.append("stage=?")
            params.append(stage)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        sql = f"""
            SELECT *
            FROM quality_events
            {where_sql}
            ORDER BY ticker, doc_type_key, period, category
            LIMIT ?
        """
        params.append(limit)
        with self._connect() as conn:
            return [self._event_row(row) for row in conn.execute(sql, params)]

    def rejected_reason_summary(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        CASE
                            WHEN message LIKE 'Unsupported numeric values:%'
                                THEN 'Unsupported numeric values'
                            WHEN message LIKE 'Dangling references:%'
                                THEN 'Dangling references'
                            ELSE message
                        END AS reason,
                        COUNT(*) AS count
                    FROM quality_events
                    WHERE category='rejected_object'
                    GROUP BY reason
                    ORDER BY count DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            ]

    def build_repair_jobs(
        self,
        *,
        plan_id: str,
        min_docs: int = 5,
        kinds: Iterable[str] | None = None,
        include_warn: bool = False,
    ) -> list[RepairJob]:
        wanted = {kind for kind in kinds or []}

        def enabled(kind: str) -> bool:
            return not wanted or kind in wanted

        jobs: list[RepairJob] = []
        with self._connect() as conn:
            docs_by_ticker = {
                row["ticker"]: int(row["docs"])
                for row in conn.execute(
                    "SELECT ticker, COUNT(*) AS docs FROM documents GROUP BY ticker ORDER BY ticker"
                )
            }
            if enabled(DOCS_MISSING):
                for ticker, docs in docs_by_ticker.items():
                    if docs >= min_docs:
                        continue
                    jobs.append(
                        self._job(
                            plan_id=plan_id,
                            kind=DOCS_MISSING,
                            ticker=ticker,
                            reason=f"ticker has {docs} documents; expected at least {min_docs}",
                            count=max(1, min_docs - docs),
                        )
                    )

            if enabled(SECTION_FAIL) or (include_warn and enabled(SECTION_WARN)):
                statuses = ["fail"]
                if include_warn:
                    statuses.append("warn")
                placeholders = ",".join("?" for _ in statuses)
                for row in conn.execute(
                    f"""
                    SELECT ticker, document_type, doc_type_key, period,
                           section_quality_status, section_quality_json,
                           ontology_dir, artifact_index_path
                    FROM documents
                    WHERE section_quality_status IN ({placeholders})
                    ORDER BY ticker, doc_type_key, period
                    """,
                    statuses,
                ):
                    kind = SECTION_FAIL if row["section_quality_status"] == "fail" else SECTION_WARN
                    if not enabled(kind):
                        continue
                    quality = self._loads(row["section_quality_json"])
                    jobs.append(
                        self._job(
                            plan_id=plan_id,
                            kind=kind,
                            ticker=row["ticker"],
                            document_type=row["document_type"],
                            doc_type_key=row["doc_type_key"],
                            period=row["period"],
                            ontology_dir=row["ontology_dir"],
                            artifact_index_path=row["artifact_index_path"],
                            reason=", ".join(quality.get("fail_reasons") or quality.get("warn_reasons") or []),
                            payload={
                                "section_quality": quality,
                                "suggested_executor": "resection_document",
                            },
                        )
                    )

            if enabled(BATCH_FAILURE):
                for row in conn.execute(
                    """
                    SELECT id, ticker, document_type, doc_type_key, period,
                           stage, json, object_id
                    FROM quality_events
                    WHERE category='batch_failure'
                    ORDER BY ticker, stage, period
                    """
                ):
                    payload = self._loads(row["json"])
                    jobs.append(
                        self._job(
                            plan_id=plan_id,
                            kind=BATCH_FAILURE,
                            ticker=row["ticker"],
                            document_type=row["document_type"],
                            doc_type_key=row["doc_type_key"],
                            period=row["period"],
                            stage=row["stage"],
                            batch_index=payload.get("batch_index"),
                            source_event_id=row["id"],
                            ontology_dir=self._document_ontology_dir(
                                conn,
                                row["ticker"],
                                row["doc_type_key"],
                                row["period"],
                            ),
                            reason=payload.get("error_message") or "Batch failure",
                            payload=payload,
                        )
                    )

            if enabled(NORMALIZE_NUMERIC) or enabled(REPAIR_REFERENCE):
                for row in conn.execute(
                    """
                    SELECT ticker, document_type, doc_type_key, period, stage,
                           COUNT(*) AS count,
                           MIN(id) AS source_event_id,
                           MIN(message) AS sample_message
                    FROM quality_events
                    WHERE category='rejected_object'
                      AND stage IN ('numeric_guard', 'reference_validation',
                                    'reference_alias_resolution', 'relation_validation')
                    GROUP BY ticker, document_type, doc_type_key, period, stage
                    ORDER BY ticker, stage, period
                    """
                ):
                    stage = row["stage"] or ""
                    kind = NORMALIZE_NUMERIC if stage in NUMERIC_STAGES else REPAIR_REFERENCE
                    if not enabled(kind):
                        continue
                    jobs.append(
                        self._job(
                            plan_id=plan_id,
                            kind=kind,
                            ticker=row["ticker"],
                            document_type=row["document_type"],
                            doc_type_key=row["doc_type_key"],
                            period=row["period"],
                            stage=stage,
                            source_event_id=row["source_event_id"],
                            ontology_dir=self._document_ontology_dir(
                                conn,
                                row["ticker"],
                                row["doc_type_key"],
                                row["period"],
                            ),
                            reason=row["sample_message"],
                            count=int(row["count"]),
                        )
                    )

            if enabled(COVERAGE_GAP):
                for row in conn.execute(
                    """
                    SELECT id, ticker, document_type, doc_type_key, period,
                           stage, message, json
                    FROM quality_events
                    WHERE category='coverage_gap'
                    ORDER BY ticker
                    """
                ):
                    jobs.append(
                        self._job(
                            plan_id=plan_id,
                            kind=COVERAGE_GAP,
                            ticker=row["ticker"],
                            document_type=row["document_type"],
                            doc_type_key=row["doc_type_key"],
                            period=row["period"],
                            stage=row["stage"],
                            source_event_id=row["id"],
                            reason=row["message"],
                            payload=self._loads(row["json"]),
                        )
                    )
        return self._dedupe_jobs(jobs)

    def _connect(self) -> sqlite3.Connection:
        if not self.index_path.exists():
            raise FileNotFoundError(f"agent index not found: {self.index_path}")
        conn = sqlite3.connect(self.index_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _scalar(conn: sqlite3.Connection, sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    def _build_metadata(self, conn: sqlite3.Connection) -> dict[str, Any]:
        try:
            row = conn.execute("SELECT value FROM metadata WHERE key='build'").fetchone()
        except sqlite3.Error:
            return {}
        if not row:
            return {}
        return self._loads(row["value"])

    @staticmethod
    def _loads(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _document_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["section_quality"] = self._loads(payload.pop("section_quality_json", None))
        return payload

    def _event_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["payload"] = self._loads(payload.pop("json", None))
        return payload

    def _document_ontology_dir(
        self,
        conn: sqlite3.Connection,
        ticker: str,
        doc_type_key: str,
        period: str,
    ) -> str | None:
        row = conn.execute(
            """
            SELECT ontology_dir
            FROM documents
            WHERE ticker=? AND doc_type_key=? AND period=?
            """,
            (ticker, doc_type_key, period),
        ).fetchone()
        return row["ontology_dir"] if row else None

    def _job(
        self,
        *,
        plan_id: str,
        kind: str,
        ticker: str,
        document_type: str | None = None,
        doc_type_key: str | None = None,
        period: str | None = None,
        stage: str | None = None,
        batch_index: int | None = None,
        ontology_dir: str | None = None,
        artifact_index_path: str | None = None,
        source_event_id: str | None = None,
        reason: str | None = None,
        count: int = 1,
        payload: dict[str, Any] | None = None,
    ) -> RepairJob:
        dedupe_parts = [
            kind,
            ticker,
            document_type or "",
            doc_type_key or "",
            period or "",
            stage or "",
            "" if batch_index is None else str(batch_index),
            source_event_id or "",
        ]
        digest = hashlib.sha256("|".join(dedupe_parts).encode()).hexdigest()[:12]
        return RepairJob(
            job_id=f"{plan_id}-{digest}",
            plan_id=plan_id,
            kind=kind,
            ticker=ticker,
            document_type=document_type,
            doc_type_key=doc_type_key,
            period=period,
            stage=stage,
            batch_index=batch_index,
            ontology_dir=ontology_dir,
            artifact_index_path=artifact_index_path,
            source_event_id=source_event_id,
            reason=reason,
            count=count,
            payload=payload or {},
        )

    @staticmethod
    def _dedupe_jobs(jobs: list[RepairJob]) -> list[RepairJob]:
        seen: set[str] = set()
        result: list[RepairJob] = []
        for job in jobs:
            if job.job_id in seen:
                continue
            seen.add(job.job_id)
            result.append(job)
        return result
