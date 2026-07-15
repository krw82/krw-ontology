"""SQLite-backed quality scanner for ontology releases."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.config.settings import PipelineConfig
from krw_ontology.agent_index.spine_schema import verify_global_spine_schema
from krw_ontology.agent_index.spine_verify import global_replica_consistency_errors
from krw_ontology.pipeline.queue import FAILED as QUEUE_FAILED
from krw_ontology.pipeline.queue import PipelineQueue
from krw_ontology.pipeline.research_plan import discover_research_filing_targets
from krw_ontology.quality.models import (
    BATCH_FAILURE,
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
DOCUMENT_CLEAN_RERUN_STAGES = {
    "extract_evidence_quotes",
    "extract_research_claims",
    "extract_assumption_candidates",
}
DOCUMENT_CLEAN_RERUN_FAILURE_RATE_THRESHOLD = 0.20
QUALITY_SHARD_SUMMARY_FORMAT = "krw-ontology-shard-quality-summary/v1"
QUALITY_RELEASE_FULL_MODE = "full-release-diagnostic"
QUALITY_RELEASE_SCAN_MODES = {"bounded", "full", QUALITY_RELEASE_FULL_MODE}
DOCS_MISSING_TARGETED_ACTION = "targeted_filing_update"
DOCS_MISSING_FALLBACK_ACTION = "full_refresh_fallback"
DOCS_MISSING_NO_REPAIR_ACTION = "expected_filing_coverage_complete"
DOCS_MISSING_REPAIRABILITY = "source_repair"
DOCS_MISSING_DISCOVERY_YEARS = 3
_PERIOD_RE = re.compile(
    r"^(?P<prefix>CY|FY)?(?P<year>\d{4})(?:Q(?P<quarter>[1-4]))?$", re.IGNORECASE
)
_QUEUE_FINAL_SPLIT_FAILURE_RE = re.compile(
    r"(?P<stage>extract_evidence_quotes|extract_research_claims|extract_assumption_candidates):\s*"
    r"(?P<failed>\d+)\s*/\s*(?P<total>\d+)\s+"
    r"(?P<unit>spans|quotes|batches|claims)\s+failed after split retry",
    re.IGNORECASE,
)
ExpectedFilingProvider = Callable[[str], tuple[list[dict[str, str]], str | None]]


def _normalize_release_quality_scan_mode(mode: str) -> str:
    normalized = str(mode or "full").strip().lower()
    if normalized == QUALITY_RELEASE_FULL_MODE:
        return "full"
    if normalized not in QUALITY_RELEASE_SCAN_MODES:
        raise ValueError(f"invalid quality scan mode: {mode}")
    return normalized


def _release_scan_mode_label(mode: str) -> str:
    return QUALITY_RELEASE_FULL_MODE if mode == "full" else mode


def _ticker_quality_payload(ticker: TickerQuality) -> dict[str, Any]:
    return {
        "ticker": ticker.ticker,
        "docs": ticker.docs,
        "section_fail": ticker.section_fail,
        "section_warn": ticker.section_warn,
        "batch_failure": ticker.batch_failure,
        "coverage_gap": ticker.coverage_gap,
        "rejected_object": ticker.rejected_object,
    }


def _ticker_quality_from_payload(payload: Mapping[str, Any]) -> TickerQuality:
    return TickerQuality(
        ticker=str(payload.get("ticker") or "").upper(),
        docs=int(payload.get("docs") or 0),
        section_fail=int(payload.get("section_fail") or 0),
        section_warn=int(payload.get("section_warn") or 0),
        batch_failure=int(payload.get("batch_failure") or 0),
        coverage_gap=int(payload.get("coverage_gap") or 0),
        rejected_object=int(payload.get("rejected_object") or 0),
    )


def _ticker_quality_from_rollup(
    ticker: str,
    report: Mapping[str, Any],
    totals: Mapping[str, Any],
) -> TickerQuality:
    section_status = dict(report.get("section_status") or {})
    batch_failure = 0
    coverage_gap = 0
    rejected_object = 0
    for event in report.get("event_counts") or []:
        if not isinstance(event, Mapping):
            continue
        category = str(event.get("category") or "")
        count = int(event.get("count") or 0)
        if category == "batch_failure":
            batch_failure += count
        elif category == "coverage_gap":
            coverage_gap += count
        elif category == "rejected_object":
            rejected_object += count
    return TickerQuality(
        ticker=ticker.upper(),
        docs=int(totals.get("documents") or 0),
        section_fail=int(section_status.get("fail") or 0),
        section_warn=int(section_status.get("warn") or 0),
        batch_failure=batch_failure,
        coverage_gap=coverage_gap,
        rejected_object=rejected_object,
    )


class QualityShardScanner:
    """Read quality signals from one shard-local ontology SQLite database."""

    def __init__(self, shard_path: Path | str):
        self.shard_path = Path(shard_path).expanduser().resolve()

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
                    ORDER BY count DESC, category, severity, stage
                    """
                )
            ]
            tickers = self.ticker_quality(min_docs=min_docs)
            problem_tickers = [
                ticker for ticker in tickers if ticker.problem_kinds(min_docs=min_docs)
            ]
            severity_counts = Counter(
                ticker.severity(min_docs=min_docs)
                for ticker in tickers
                if ticker.problem_kinds(min_docs=min_docs)
            )
            kind_counts = Counter(
                kind for ticker in tickers for kind in ticker.problem_kinds(min_docs=min_docs)
            )
            rejected_reasons = self.rejected_reason_summary(limit=20)
        return {
            "shard_path": str(self.shard_path),
            "metadata": metadata,
            "totals": totals,
            "section_status": section_status,
            "event_counts": event_counts,
            "problem_ticker_count": len(problem_tickers),
            "severity_counts": dict(severity_counts),
            "kind_counts": dict(kind_counts),
            "rejected_reasons": rejected_reasons,
            "ticker_quality": [_ticker_quality_payload(ticker) for ticker in tickers],
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
                    ORDER BY count DESC, category, severity, stage
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
                    ORDER BY count DESC, reason
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
                    ORDER BY count DESC, reason
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
        expected_filing_provider: ExpectedFilingProvider | None = None,
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
                    actual_documents = self._actual_documents(conn, ticker)
                    expected_documents: list[dict[str, str]] | None = None
                    expected_discovery_error: str | None = None
                    if expected_filing_provider is not None:
                        try:
                            expected_documents, expected_discovery_error = expected_filing_provider(
                                ticker
                            )
                        except Exception as exc:
                            expected_documents = []
                            expected_discovery_error = str(exc)
                    payload = self._docs_missing_repair_payload(
                        ticker=ticker,
                        actual_documents=actual_documents,
                        actual_count=docs,
                        min_docs=min_docs,
                        expected_documents=expected_documents,
                        expected_discovery_error=expected_discovery_error,
                    )
                    if payload.get("action") == DOCS_MISSING_NO_REPAIR_ACTION:
                        continue
                    jobs.append(
                        self._job(
                            plan_id=plan_id,
                            kind=DOCS_MISSING,
                            ticker=ticker,
                            reason=str(
                                payload.get("reason")
                                or f"ticker has {docs} documents; expected at least {min_docs}"
                            ),
                            count=max(1, min_docs - docs),
                            payload=payload,
                        )
                    )

            if False and (enabled(SECTION_FAIL) or (include_warn and enabled(SECTION_WARN))):
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
                            reason=", ".join(
                                quality.get("fail_reasons") or quality.get("warn_reasons") or []
                            ),
                            payload={
                                "section_quality": quality,
                                "suggested_executor": "resection_document",
                            },
                        )
                    )

            if enabled(BATCH_FAILURE):
                document_rerun_jobs: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
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
                    stage = str(row["stage"] or "")
                    if self._batch_failure_uses_document_clean_rerun(stage, payload):
                        ontology_dir = self._document_ontology_dir(
                            conn,
                            row["ticker"],
                            row["doc_type_key"],
                            row["period"],
                        )
                        key = (
                            str(row["ticker"] or ""),
                            str(row["document_type"] or ""),
                            str(row["doc_type_key"] or ""),
                            str(row["period"] or ""),
                            stage,
                        )
                        grouped = document_rerun_jobs.setdefault(
                            key,
                            {
                                "row": row,
                                "payload": {
                                    "repair_strategy": "document_clean_rerun",
                                    "replace_partial_on_rate_limit": True,
                                    "stage": stage,
                                    "batch_indices": [],
                                    "source_event_ids": [],
                                    "input_span_ids": [],
                                    "failed_unit_ids": [],
                                    "error_messages": [],
                                    "error_types": [],
                                    "provider_error_statuses": [],
                                },
                                "count": 0,
                                "failed_count_without_ids": 0,
                                "fallback_rows": [],
                            },
                        )
                        grouped["count"] += 1
                        grouped["fallback_rows"].append((row, payload, ontology_dir))
                        grouped_payload = grouped["payload"]
                        if payload.get("batch_index") is not None:
                            grouped_payload["batch_indices"].append(payload.get("batch_index"))
                        grouped_payload["source_event_ids"].append(row["id"])
                        grouped_payload["input_span_ids"].extend(
                            payload.get("input_span_ids") or []
                        )
                        failed_unit_ids = self._batch_failure_failed_unit_ids(payload)
                        grouped_payload["failed_unit_ids"].extend(failed_unit_ids)
                        if not failed_unit_ids:
                            grouped["failed_count_without_ids"] += (
                                self._batch_failure_failed_unit_count(payload)
                            )
                        if payload.get("error_message"):
                            grouped_payload["error_messages"].append(payload.get("error_message"))
                        if payload.get("error_type"):
                            grouped_payload["error_types"].append(payload.get("error_type"))
                        if payload.get("provider_error_status") is not None:
                            grouped_payload["provider_error_statuses"].append(
                                payload.get("provider_error_status")
                            )
                        continue
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
                for grouped in document_rerun_jobs.values():
                    row = grouped["row"]
                    payload = grouped["payload"]
                    ontology_dir = self._document_ontology_dir(
                        conn,
                        row["ticker"],
                        row["doc_type_key"],
                        row["period"],
                    )
                    payload["batch_indices"] = sorted(
                        {int(value) for value in payload["batch_indices"]}
                    )
                    payload["source_event_ids"] = sorted(
                        {str(value) for value in payload["source_event_ids"] if value}
                    )
                    payload["input_span_ids"] = sorted(
                        {str(value) for value in payload["input_span_ids"] if value}
                    )
                    payload["failed_unit_ids"] = sorted(
                        {str(value) for value in payload["failed_unit_ids"] if value}
                    )
                    payload["error_messages"] = [
                        str(value) for value in payload["error_messages"][:5]
                    ]
                    payload["error_types"] = sorted(
                        {str(value) for value in payload["error_types"] if value}
                    )
                    payload["provider_error_statuses"] = sorted(
                        {
                            int(value)
                            for value in payload["provider_error_statuses"]
                            if value is not None
                        }
                    )
                    payload["source_batch_failure_count"] = int(grouped["count"])
                    failed_count = len(payload["failed_unit_ids"]) + int(
                        grouped["failed_count_without_ids"]
                    )
                    total_count, denominator_source = self._document_clean_rerun_denominator(
                        ontology_dir,
                        str(row["stage"] or ""),
                    )
                    failure_rate = (failed_count / total_count) if total_count else None
                    payload["document_clean_rerun_threshold"] = (
                        DOCUMENT_CLEAN_RERUN_FAILURE_RATE_THRESHOLD
                    )
                    payload["document_clean_rerun_failed_unit_count"] = failed_count
                    payload["document_clean_rerun_total_unit_count"] = total_count
                    payload["document_clean_rerun_failure_rate"] = failure_rate
                    payload["document_clean_rerun_denominator_source"] = denominator_source
                    if (
                        failure_rate is None
                        or failure_rate < DOCUMENT_CLEAN_RERUN_FAILURE_RATE_THRESHOLD
                    ):
                        for fallback_row, fallback_payload, fallback_ontology_dir in grouped[
                            "fallback_rows"
                        ]:
                            fallback_payload["document_clean_rerun_skipped"] = True
                            fallback_payload["document_clean_rerun_skip_reason"] = (
                                "failure_rate_below_threshold"
                            )
                            fallback_payload["document_clean_rerun_threshold"] = (
                                DOCUMENT_CLEAN_RERUN_FAILURE_RATE_THRESHOLD
                            )
                            fallback_payload["document_clean_rerun_failed_unit_count"] = (
                                failed_count
                            )
                            fallback_payload["document_clean_rerun_total_unit_count"] = total_count
                            fallback_payload["document_clean_rerun_failure_rate"] = failure_rate
                            fallback_payload["document_clean_rerun_denominator_source"] = (
                                denominator_source
                            )
                            jobs.append(
                                self._job(
                                    plan_id=plan_id,
                                    kind=BATCH_FAILURE,
                                    ticker=fallback_row["ticker"],
                                    document_type=fallback_row["document_type"],
                                    doc_type_key=fallback_row["doc_type_key"],
                                    period=fallback_row["period"],
                                    stage=fallback_row["stage"],
                                    batch_index=fallback_payload.get("batch_index"),
                                    source_event_id=fallback_row["id"],
                                    ontology_dir=fallback_ontology_dir,
                                    reason=fallback_payload.get("error_message") or "Batch failure",
                                    payload=fallback_payload,
                                )
                            )
                        continue
                    payload["suggested_executor"] = "document_clean_rerun"
                    jobs.append(
                        self._job(
                            plan_id=plan_id,
                            kind=BATCH_FAILURE,
                            ticker=row["ticker"],
                            document_type=row["document_type"],
                            doc_type_key=row["doc_type_key"],
                            period=row["period"],
                            stage=row["stage"],
                            source_event_id=None,
                            ontology_dir=ontology_dir,
                            reason=payload["error_messages"][0]
                            if payload["error_messages"]
                            else "Document clean rerun for transient batch failures",
                            count=int(grouped["count"]),
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

        return self._dedupe_jobs(jobs)

    def _connect(self) -> sqlite3.Connection:
        if not self.shard_path.exists():
            raise FileNotFoundError(f"quality shard not found: {self.shard_path}")
        conn = sqlite3.connect(self.shard_path)
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

    @staticmethod
    def _actual_documents(conn: sqlite3.Connection, ticker: str) -> list[dict[str, str]]:
        rows = conn.execute(
            """
            SELECT ticker, document_type, doc_type_key, period
            FROM documents
            WHERE ticker=?
            ORDER BY doc_type_key, period
            """,
            (ticker,),
        )
        return [
            {
                "ticker": str(row["ticker"] or "").upper(),
                "document_type": str(row["document_type"] or ""),
                "doc_type_key": str(row["doc_type_key"] or ""),
                "period": str(row["period"] or ""),
            }
            for row in rows
        ]

    @classmethod
    def _docs_missing_repair_payload(
        cls,
        *,
        ticker: str,
        actual_documents: list[dict[str, str]],
        actual_count: int,
        min_docs: int,
        expected_documents: list[dict[str, str]] | None = None,
        expected_discovery_error: str | None = None,
    ) -> dict[str, Any]:
        actual_filing_documents = cls._normalize_filing_documents(actual_documents)
        expected_filing_documents = (
            cls._normalize_filing_documents(expected_documents)
            if expected_documents is not None
            else []
        )
        deficit = max(1, int(min_docs) - int(actual_count))
        base_payload: dict[str, Any] = {
            "repairability": DOCS_MISSING_REPAIRABILITY,
            "actual_document_count": int(actual_count),
            "expected_document_count": int(min_docs),
            "document_deficit": deficit,
            "actual_documents": actual_documents,
            "actual_filing_documents": actual_filing_documents,
            "expected_filing_documents": expected_filing_documents,
            "expected_filing_count": len(expected_filing_documents),
            "expected_discovery_error": expected_discovery_error,
            "targeted_candidate_documents": [],
        }
        if expected_documents is not None and not expected_discovery_error:
            missing_from_expected = cls._missing_expected_documents(
                expected_filing_documents,
                actual_filing_documents,
            )
            base_payload["targeted_candidate_documents"] = missing_from_expected
            if missing_from_expected:
                label = ", ".join(
                    f"{doc['document_type']} {doc['period']}" for doc in missing_from_expected
                )
                return {
                    **base_payload,
                    "action": DOCS_MISSING_TARGETED_ACTION,
                    "missing_documents": missing_from_expected,
                    "targeted_update_count": len(missing_from_expected),
                    "inference_source": "expected_filing_diff",
                    "reason": f"missing {len(missing_from_expected)} expected filing(s): {label}",
                }
            return {
                **base_payload,
                "action": DOCS_MISSING_NO_REPAIR_ACTION,
                "repairability": "no_source_repair_required",
                "missing_documents": [],
                "targeted_update_count": 0,
                "inference_source": "expected_filing_diff",
                "reason": (
                    "expected filing discovery found no missing filings; "
                    "docs deficit is caused by the min_docs heuristic"
                ),
            }

        local_missing_documents = cls._infer_missing_documents(actual_documents)
        base_payload["local_gap_candidate_documents"] = local_missing_documents
        if expected_discovery_error:
            base_payload["targeted_candidate_documents"] = local_missing_documents
        if len(local_missing_documents) >= deficit:
            selected = local_missing_documents[:deficit]
            label = ", ".join(f"{doc['document_type']} {doc['period']}" for doc in selected)
            return {
                **base_payload,
                "action": DOCS_MISSING_TARGETED_ACTION,
                "missing_documents": selected,
                "targeted_update_count": len(selected),
                "inference_source": "local_period_gap",
                "reason": f"missing {len(selected)} expected filing(s): {label}",
            }
        fallback_reason = cls._docs_missing_fallback_reason(
            expected_documents=expected_documents,
            expected_discovery_error=expected_discovery_error,
            expected_filing_documents=expected_filing_documents,
            local_missing_documents=local_missing_documents,
            deficit=deficit,
        )
        return {
            **base_payload,
            "action": DOCS_MISSING_FALLBACK_ACTION,
            "missing_documents": [],
            "fallback_reason": fallback_reason,
            "years": DOCS_MISSING_DISCOVERY_YEARS,
            "reason": f"ticker has {actual_count} documents; expected at least {min_docs}; {fallback_reason}",
        }

    @classmethod
    def _docs_missing_fallback_reason(
        cls,
        *,
        expected_documents: list[dict[str, str]] | None,
        expected_discovery_error: str | None,
        expected_filing_documents: list[dict[str, str]],
        local_missing_documents: list[dict[str, str]],
        deficit: int,
    ) -> str:
        if expected_discovery_error:
            return f"expected filing discovery failed: {expected_discovery_error}"
        if expected_documents is not None and not expected_filing_documents:
            return "expected filing discovery returned no filing targets"
        if expected_documents is not None:
            return "expected filing discovery did not identify missing filings despite docs deficit"
        if not local_missing_documents:
            return "missing document periods could not be inferred from a complete local period sequence"
        return (
            f"inferred {len(local_missing_documents)} missing filing(s), "
            f"but {deficit} document(s) are required to satisfy min_docs"
        )

    @classmethod
    def _infer_missing_documents(
        cls, actual_documents: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
        for document in actual_documents:
            document_type = document.get("document_type") or _document_type_from_key(
                document.get("doc_type_key") or ""
            )
            doc_type_key = document.get("doc_type_key") or document_type.replace("-", "")
            if not document_type or not doc_type_key or not document.get("period"):
                continue
            grouped.setdefault((document_type, doc_type_key), []).append(document)

        missing: list[dict[str, str]] = []
        for (document_type, doc_type_key), documents in sorted(grouped.items()):
            missing.extend(
                cls._infer_missing_documents_for_group(
                    document_type=document_type,
                    doc_type_key=doc_type_key,
                    documents=documents,
                )
            )
        return sorted(
            missing,
            key=lambda doc: (_period_sort_key(doc.get("period")), doc.get("document_type") or ""),
        )

    @classmethod
    def _normalize_filing_documents(
        cls, documents: list[dict[str, str]] | None
    ) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for document in documents or []:
            if not isinstance(document, Mapping):
                continue
            payload = cls._normalize_filing_document(document)
            if payload is None:
                continue
            key = (payload["document_type"], payload["period"])
            if key in seen:
                continue
            seen.add(key)
            normalized.append(payload)
        return sorted(
            normalized,
            key=lambda doc: (_period_sort_key(doc.get("period")), doc.get("document_type") or ""),
        )

    @staticmethod
    def _normalize_filing_document(document: Mapping[str, Any]) -> dict[str, str] | None:
        raw_doc_type = str(document.get("document_type") or "")
        raw_doc_type_key = str(document.get("doc_type_key") or "")
        document_type = raw_doc_type.strip().upper()
        doc_type_key = raw_doc_type_key.strip().upper()
        if not document_type and doc_type_key:
            document_type = _document_type_from_key(doc_type_key)
        if not doc_type_key and document_type:
            doc_type_key = document_type.replace("-", "")
        if doc_type_key in {"10K", "10Q"}:
            document_type = _document_type_from_key(doc_type_key)
        if document_type not in {"10-K", "10-Q"}:
            return None
        period = str(document.get("period") or "").strip()
        if not period or period.upper() == "ALL":
            return None
        payload = {
            "ticker": str(document.get("ticker") or "").upper(),
            "document_type": document_type,
            "doc_type_key": doc_type_key or document_type.replace("-", ""),
            "period": period,
        }
        for key in ("accession_number", "filing_date", "report_date", "inference"):
            value = document.get(key)
            if value:
                payload[key] = str(value)
        return payload

    @classmethod
    def _missing_expected_documents(
        cls,
        expected_documents: list[dict[str, str]],
        actual_documents: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        actual_keys = {
            (document["document_type"], document["period"]) for document in actual_documents
        }
        return [
            {
                **document,
                "inference": document.get("inference") or "expected_filing_diff",
            }
            for document in expected_documents
            if (document["document_type"], document["period"]) not in actual_keys
        ]

    @staticmethod
    def _infer_missing_documents_for_group(
        *,
        document_type: str,
        doc_type_key: str,
        documents: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        parsed = [_parse_period(document.get("period")) for document in documents]
        parsed = [period for period in parsed if period is not None]
        if len(parsed) < 2:
            return []
        prefix_values = {period["prefix"] for period in parsed}
        if len(prefix_values) != 1:
            return []
        prefix = next(iter(prefix_values))
        quarters = {period["quarter"] for period in parsed}
        years = sorted({int(period["year"]) for period in parsed})
        missing: list[dict[str, str]] = []
        if quarters == {None}:
            if len(years) < 2:
                return []
            present = set(years)
            for year in range(min(years), max(years) + 1):
                if year not in present:
                    missing.append(
                        {
                            "document_type": document_type,
                            "doc_type_key": doc_type_key,
                            "period": f"{prefix}{year}",
                            "inference": "annual_period_gap",
                        }
                    )
            return missing
        quarter_by_year: dict[int, set[int]] = {}
        for period in parsed:
            quarter = period["quarter"]
            if quarter is None:
                continue
            quarter_by_year.setdefault(int(period["year"]), set()).add(int(quarter))
        for year, present_quarters in sorted(quarter_by_year.items()):
            if len(present_quarters) < 2:
                continue
            for quarter in range(min(present_quarters), max(present_quarters) + 1):
                if quarter not in present_quarters:
                    missing.append(
                        {
                            "document_type": document_type,
                            "doc_type_key": doc_type_key,
                            "period": f"{prefix}{year}Q{quarter}",
                            "inference": "quarterly_period_gap",
                        }
                    )
        return missing

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

    @staticmethod
    def _batch_failure_uses_document_clean_rerun(stage: str, payload: Mapping[str, Any]) -> bool:
        if stage not in DOCUMENT_CLEAN_RERUN_STAGES:
            return False
        error_message = str(payload.get("error_message") or payload.get("message") or "")
        error_type = str(payload.get("error_type") or "")
        provider_status = payload.get("provider_error_status")
        lower = error_message.lower()
        return (
            error_type == "RateLimitError"
            or provider_status in {429, 529}
            or "api error: 529" in lower
            or "api error: 429" in lower
            or "rate limited" in lower
            or "temporarily overloaded" in lower
            or "server-side issue" in lower
        )

    @staticmethod
    def _batch_failure_failed_unit_ids(payload: Mapping[str, Any]) -> list[str]:
        for key in (
            "input_span_ids",
            "span_ids",
            "input_quote_ids",
            "quote_ids",
            "input_claim_ids",
            "claim_ids",
        ):
            value = payload.get(key)
            if isinstance(value, list) and value:
                return [str(item) for item in value if item]
        return []

    @staticmethod
    def _batch_failure_failed_unit_count(payload: Mapping[str, Any]) -> int:
        for key in ("failed_spans", "failed_quotes", "failed_claims", "count"):
            value = payload.get(key)
            try:
                if value is not None:
                    return max(1, int(value))
            except (TypeError, ValueError):
                continue
        return 1

    @staticmethod
    def _document_clean_rerun_denominator(ontology_dir: str | None, stage: str) -> tuple[int, str]:
        if not ontology_dir:
            return 0, "missing_ontology_dir"
        path = Path(ontology_dir)
        if stage == "extract_evidence_quotes":
            audit_rows = QualityShardScanner._read_jsonl(path / "quote_span_audit.jsonl")
            keep_count = sum(1 for row in audit_rows if row.get("decision") == "keep")
            if keep_count:
                return keep_count, "quote_span_audit.keep"
            return len(QualityShardScanner._read_jsonl(path / "spans.jsonl")), "spans.jsonl"
        if stage == "extract_research_claims":
            quotes = [
                row
                for row in QualityShardScanner._read_jsonl(path / "evidence_quotes.jsonl")
                if row.get("id") and row.get("quote_text")
            ]
            return len(quotes), "evidence_quotes.jsonl"
        if stage == "extract_assumption_candidates":
            claims = [
                row
                for row in QualityShardScanner._read_jsonl(path / "claims.jsonl")
                if row.get("id")
            ]
            return len(claims), "claims.jsonl.all_claims"
        return 0, "unsupported_stage"

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return rows
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows


class QualityReleaseScanner:
    """Read quality signals from a v3 release root without a monolith index."""

    def __init__(self, release_root: Path | str):
        self.release_root = Path(release_root).expanduser().resolve()
        self.manifest_path = self.release_root / "manifest.json"
        self._manifest: dict[str, Any] | None = None
        self._shard_manifest: dict[str, Any] | None = None

    @property
    def global_spine_path(self) -> Path:
        manifest = self._load_manifest(allow_missing=True)
        raw_path = ((manifest.get("indexes") or {}).get("global_spine") or {}).get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raw_path = (
                manifest.get("global_spine_path")
                if isinstance(manifest.get("global_spine_path"), str)
                else ""
            )
        return self._resolve_release_path(raw_path or "indexes/global_spine.sqlite")

    @property
    def shard_manifest_path(self) -> Path:
        manifest = self._load_manifest(allow_missing=True)
        raw_path = ((manifest.get("indexes") or {}).get("shard_manifest") or {}).get("path")
        return self._resolve_release_path(
            raw_path if isinstance(raw_path, str) and raw_path else "indexes/shard_manifest.json"
        )

    def scan(
        self,
        *,
        min_docs: int = 5,
        mode: str = "full",
        sample_limit: int = 20,
    ) -> dict[str, Any]:
        resolved_mode = _normalize_release_quality_scan_mode(mode)
        mode_label = _release_scan_mode_label(resolved_mode)
        manifest = self._load_manifest()
        shard_entries = self._shard_entries()
        shard_topology = self.shard_topology()
        totals = Counter({"documents": 0, "tickers": 0, "objects": 0, "quality_events": 0})
        section_status: Counter[str] = Counter()
        event_counts: Counter[tuple[str, str, str]] = Counter()
        rejected_reasons: Counter[str] = Counter()
        ticker_rows: list[TickerQuality] = []
        opened_shards = 0
        manifest_rollup_shards = 0
        shard_diagnostics: dict[str, dict[str, Any]] = {}
        scan_errors: list[str] = []

        for ticker, shard_path, entry in shard_entries:
            if not shard_path.is_file():
                continue
            report = None if resolved_mode == "full" else self._manifest_quality_rollup(entry)
            if report is None:
                report = QualityShardScanner(shard_path).scan(min_docs=min_docs)
                opened_shards += 1
            else:
                manifest_rollup_shards += 1
            diagnostics = self._shard_manifest_diagnostics(
                ticker=ticker,
                shard_path=shard_path,
                shard_entry=entry,
                report=report,
                check_sha256=resolved_mode == "full",
            )
            shard_diagnostics[ticker] = diagnostics
            scan_errors.extend(f"shard:{ticker}:{error}" for error in diagnostics["errors"])
            shard_totals = report.get("totals") or self._manifest_entry_totals(entry)
            totals["documents"] += int(shard_totals.get("documents") or 0)
            totals["tickers"] += int(shard_totals.get("tickers") or 0)
            totals["objects"] += int(shard_totals.get("objects") or 0)
            totals["quality_events"] += int(shard_totals.get("quality_events") or 0)
            section_status.update(
                {
                    str(key): int(value)
                    for key, value in dict(report.get("section_status") or {}).items()
                }
            )
            for event in report.get("event_counts") or []:
                if not isinstance(event, Mapping):
                    continue
                key = (
                    str(event.get("category") or ""),
                    str(event.get("severity") or ""),
                    str(event.get("stage") or ""),
                )
                event_counts[key] += int(event.get("count") or 0)
            for reason in report.get("rejected_reasons") or []:
                if not isinstance(reason, Mapping):
                    continue
                rejected_reasons[str(reason.get("reason") or "")] += int(reason.get("count") or 0)
            ticker_rows.extend(
                _ticker_quality_from_payload(row)
                for row in report.get("ticker_quality") or []
                if isinstance(row, Mapping)
            )
            if not report.get("ticker_quality"):
                ticker_rows.append(_ticker_quality_from_rollup(ticker, report, shard_totals))

        tickers = sorted(ticker_rows, key=lambda item: item.ticker)
        problem_tickers = [ticker for ticker in tickers if ticker.problem_kinds(min_docs=min_docs)]
        severity_counts = Counter(
            ticker.severity(min_docs=min_docs)
            for ticker in tickers
            if ticker.problem_kinds(min_docs=min_docs)
        )
        kind_counts = Counter(
            kind for ticker in tickers for kind in ticker.problem_kinds(min_docs=min_docs)
        )
        consistency = self.consistency_report(sample_limit=sample_limit, mode=resolved_mode)
        consistency_errors = list(consistency["errors"]) + scan_errors
        consistency = {
            **consistency,
            "errors": consistency_errors,
            "ok": not consistency_errors,
        }
        if consistency_errors:
            kind_counts["release_consistency"] = len(consistency_errors)
            severity_counts["high"] += 1
        rollup_source = (
            "shard_scan"
            if resolved_mode == "full"
            else "manifest"
            if opened_shards == 0
            else "mixed"
        )
        return {
            "global_spine_path": str(self.global_spine_path),
            "release_root": str(self.release_root),
            "metadata": self._build_release_metadata(manifest),
            "scan": {
                "mode": mode_label,
                "rollup_source": rollup_source,
                "manifest_rollup_shards": manifest_rollup_shards,
                "opened_shards": opened_shards,
                "declared_shards": shard_topology["declared_count"],
                "available_shards": shard_topology["available_count"],
                "full_consistency": resolved_mode == "full",
            },
            "shards": shard_topology,
            "shard_diagnostics": shard_diagnostics,
            "totals": dict(totals),
            "section_status": dict(section_status),
            "event_counts": [
                {"category": category, "severity": severity, "stage": stage, "count": count}
                for (category, severity, stage), count in sorted(
                    event_counts.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ],
            "problem_ticker_count": len(problem_tickers),
            "severity_counts": dict(severity_counts),
            "kind_counts": dict(kind_counts),
            "rejected_reasons": [
                {"reason": reason, "count": count}
                for reason, count in rejected_reasons.most_common(20)
            ],
            "consistency": consistency,
            "min_docs": min_docs,
        }

    def ticker_quality(self, *, min_docs: int = 5, mode: str = "bounded") -> list[TickerQuality]:
        resolved_mode = _normalize_release_quality_scan_mode(mode)
        rows: list[TickerQuality] = []
        for ticker, shard_path, entry in self._shard_entries():
            if not shard_path.is_file():
                continue
            rollup = None if resolved_mode == "full" else self._manifest_quality_rollup(entry)
            if rollup is not None:
                ticker_payloads = [
                    _ticker_quality_from_payload(row)
                    for row in rollup.get("ticker_quality") or []
                    if isinstance(row, Mapping)
                ]
                if ticker_payloads:
                    rows.extend(ticker_payloads)
                    continue
                rows.append(
                    _ticker_quality_from_rollup(
                        ticker, rollup, rollup.get("totals") or self._manifest_entry_totals(entry)
                    )
                )
                continue
            rows.extend(QualityShardScanner(shard_path).ticker_quality())
        return sorted(rows, key=lambda item: item.ticker)

    def explain_ticker(self, ticker: str, *, min_docs: int = 5, limit: int = 20) -> dict[str, Any]:
        normalized = ticker.upper()
        shard_path = self._shard_path_for_ticker(normalized)
        if shard_path is None or not shard_path.is_file():
            return {
                "ticker": normalized,
                "summary": None,
                "documents": [],
                "event_summary": [],
                "events": [],
                "rejected_reasons": [],
                "min_docs": min_docs,
            }
        payload = QualityShardScanner(shard_path).explain_ticker(
            normalized, min_docs=min_docs, limit=limit
        )
        payload["release_root"] = str(self.release_root)
        payload["shard_path"] = str(shard_path)
        return payload

    def events(
        self,
        *,
        ticker: str | None = None,
        category: str | None = None,
        stage: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        shard_entries = self._shard_entries()
        if ticker:
            normalized = ticker.upper()
            shard_entries = [entry for entry in shard_entries if entry[0] == normalized]
        events: list[dict[str, Any]] = []
        for _ticker, shard_path, _entry in shard_entries:
            if not shard_path.is_file():
                continue
            events.extend(
                QualityShardScanner(shard_path).events(
                    ticker=ticker,
                    category=category,
                    stage=stage,
                    limit=limit,
                )
            )
        return events[:limit]

    def rejected_reason_summary(self, *, limit: int = 20) -> list[dict[str, Any]]:
        reasons: Counter[str] = Counter()
        for _ticker, shard_path, _entry in self._shard_entries():
            if not shard_path.is_file():
                continue
            for row in QualityShardScanner(shard_path).rejected_reason_summary(limit=limit):
                reasons[str(row.get("reason") or "")] += int(row.get("count") or 0)
        return [{"reason": reason, "count": count} for reason, count in reasons.most_common(limit)]

    def build_repair_jobs(
        self,
        *,
        plan_id: str,
        min_docs: int = 5,
        kinds: Iterable[str] | None = None,
        include_warn: bool = False,
        running_root: Path | str | None = None,
    ) -> list[RepairJob]:
        jobs: list[RepairJob] = []
        expected_filing_provider = self._expected_filing_provider()
        for _ticker, shard_path, _entry in self._shard_entries():
            if not shard_path.is_file():
                continue
            jobs.extend(
                QualityShardScanner(shard_path).build_repair_jobs(
                    plan_id=plan_id,
                    min_docs=min_docs,
                    kinds=kinds,
                    include_warn=include_warn,
                    expected_filing_provider=expected_filing_provider,
                )
            )
        jobs.extend(
            self._queue_failed_history_repair_jobs(
                plan_id=plan_id,
                running_root=Path(running_root).expanduser().resolve() if running_root else None,
                existing_jobs=jobs,
                kinds=kinds,
            )
        )
        return QualityShardScanner._dedupe_jobs(jobs)

    def _queue_failed_history_repair_jobs(
        self,
        *,
        plan_id: str,
        running_root: Path | None,
        existing_jobs: list[RepairJob],
        kinds: Iterable[str] | None,
    ) -> list[RepairJob]:
        if running_root is None:
            return []
        enabled_kinds = set(kinds) if kinds else None
        if enabled_kinds is not None and BATCH_FAILURE not in enabled_kinds:
            return []
        queue = PipelineQueue(running_root)
        if not queue.jobs_dir.exists():
            return []
        existing_document_reruns = {
            self._repair_document_stage_key(job)
            for job in existing_jobs
            if job.kind == BATCH_FAILURE
            and job.payload.get("repair_strategy") == "document_clean_rerun"
        }
        existing_document_reruns.discard(None)

        jobs: list[RepairJob] = []
        for queue_job in queue.list_jobs(statuses=[QUEUE_FAILED]):
            parsed = self._parse_queue_final_split_failure(queue_job.error)
            if parsed is None:
                continue
            if parsed["failure_rate"] < DOCUMENT_CLEAN_RERUN_FAILURE_RATE_THRESHOLD:
                continue
            document = self._resolve_queue_failed_document(
                running_root=running_root,
                ticker=queue_job.ticker,
                stage=parsed["stage"],
                queue_job_document_type=queue_job.document_type,
                queue_job_periods=queue_job.periods or [],
            )
            if document is None:
                continue
            key = (
                queue_job.ticker.upper(),
                document["doc_type_key"],
                document["period"],
                parsed["stage"],
            )
            if key in existing_document_reruns:
                continue
            existing_document_reruns.add(key)
            payload = {
                "repair_strategy": "document_clean_rerun",
                "stage": parsed["stage"],
                "suggested_executor": "document_clean_rerun",
                "source": "queue_failed_history",
                "queue_job_id": queue_job.job_id,
                "queue_job_type": queue_job.job_type,
                "queue_job_attempts": queue_job.attempts,
                "queue_job_error": queue_job.error,
                "queue_failure_failed_unit_count": parsed["failed"],
                "queue_failure_total_unit_count": parsed["total"],
                "queue_failure_rate": parsed["failure_rate"],
                "queue_failure_unit": parsed["unit"],
                "document_clean_rerun_threshold": DOCUMENT_CLEAN_RERUN_FAILURE_RATE_THRESHOLD,
                "document_clean_rerun_failed_unit_count": document["failed_count"],
                "document_clean_rerun_total_unit_count": document["total_count"],
                "document_clean_rerun_failure_rate": document["failure_rate"],
                "document_clean_rerun_denominator_source": document["denominator_source"],
                "source_batch_failure_count": document["batch_failure_count"],
                "batch_indices": document["batch_indices"],
                "failed_unit_ids": document["failed_unit_ids"],
                "error_messages": document["error_messages"],
            }
            jobs.append(
                self._queue_history_job(
                    plan_id=plan_id,
                    ticker=queue_job.ticker,
                    document_type=document["document_type"],
                    doc_type_key=document["doc_type_key"],
                    period=document["period"],
                    stage=parsed["stage"],
                    ontology_dir=document["ontology_dir"],
                    source_event_id=f"queue:{queue_job.job_id}",
                    reason=queue_job.error or "Queue final split failure",
                    count=document["batch_failure_count"],
                    payload=payload,
                )
            )
        return jobs

    @staticmethod
    def _parse_queue_final_split_failure(error: str | None) -> dict[str, Any] | None:
        match = _QUEUE_FINAL_SPLIT_FAILURE_RE.search(str(error or ""))
        if not match:
            return None
        failed = int(match.group("failed"))
        total = int(match.group("total"))
        if total <= 0:
            return None
        return {
            "stage": match.group("stage"),
            "failed": failed,
            "total": total,
            "unit": match.group("unit").lower(),
            "failure_rate": failed / total,
        }

    @staticmethod
    def _queue_history_job(
        *,
        plan_id: str,
        ticker: str,
        document_type: str,
        doc_type_key: str,
        period: str,
        stage: str,
        ontology_dir: str,
        source_event_id: str,
        reason: str,
        count: int,
        payload: dict[str, Any],
    ) -> RepairJob:
        dedupe_parts = [
            BATCH_FAILURE,
            ticker,
            document_type,
            doc_type_key,
            period,
            stage,
            source_event_id,
        ]
        digest = hashlib.sha256("|".join(dedupe_parts).encode()).hexdigest()[:12]
        return RepairJob(
            job_id=f"{plan_id}-{digest}",
            plan_id=plan_id,
            kind=BATCH_FAILURE,
            ticker=ticker,
            document_type=document_type,
            doc_type_key=doc_type_key,
            period=period,
            stage=stage,
            ontology_dir=ontology_dir,
            source_event_id=source_event_id,
            reason=reason,
            count=count,
            payload=payload,
        )

    @staticmethod
    def _repair_document_stage_key(job: RepairJob) -> tuple[str, str, str, str] | None:
        ticker = str(job.ticker or "").upper()
        doc_type_key = str(job.doc_type_key or "")
        if not doc_type_key and job.document_type:
            doc_type_key = normalize_doc_type(str(job.document_type))
        period = str(job.period or "")
        stage = str(job.stage or job.payload.get("stage") or "")
        if not ticker or not doc_type_key or not period or not stage:
            return None
        return ticker, doc_type_key, period, stage

    @staticmethod
    def _resolve_queue_failed_document(
        *,
        running_root: Path,
        ticker: str,
        stage: str,
        queue_job_document_type: str | None,
        queue_job_periods: list[str],
    ) -> dict[str, Any] | None:
        ticker_root = running_root / "companies" / ticker.upper() / "ontology"
        if not ticker_root.exists():
            return None
        preferred_doc_type_key = (
            normalize_doc_type(queue_job_document_type) if queue_job_document_type else None
        )
        preferred_periods = {str(period) for period in queue_job_periods if period}
        candidates: list[dict[str, Any]] = []
        for failures_path in ticker_root.glob("*/*/batch_failures.jsonl"):
            ontology_dir = failures_path.parent
            doc_type_key = ontology_dir.parent.name
            period = ontology_dir.name
            if preferred_doc_type_key and doc_type_key != preferred_doc_type_key:
                continue
            if preferred_periods and period not in preferred_periods:
                continue
            rows = [
                row
                for row in QualityShardScanner._read_jsonl(failures_path)
                if str(row.get("stage") or "") == stage
            ]
            if not rows:
                continue
            failed_ids: set[str] = set()
            failed_count_without_ids = 0
            batch_indices: list[int] = []
            error_messages: list[str] = []
            for row in rows:
                failed_unit_ids = QualityShardScanner._batch_failure_failed_unit_ids(row)
                if failed_unit_ids:
                    failed_ids.update(failed_unit_ids)
                else:
                    failed_count_without_ids += (
                        QualityShardScanner._batch_failure_failed_unit_count(row)
                    )
                if row.get("batch_index") is not None:
                    try:
                        batch_indices.append(int(row.get("batch_index")))
                    except (TypeError, ValueError):
                        pass
                if row.get("error_message") or row.get("error"):
                    error_messages.append(str(row.get("error_message") or row.get("error"))[:500])
            total_count, denominator_source = QualityShardScanner._document_clean_rerun_denominator(
                str(ontology_dir),
                stage,
            )
            failed_count = len(failed_ids) + failed_count_without_ids
            failure_rate = (failed_count / total_count) if total_count else 0.0
            candidates.append(
                {
                    "ticker": ticker.upper(),
                    "document_type": QualityReleaseScanner._document_type_from_key(doc_type_key),
                    "doc_type_key": doc_type_key,
                    "period": period,
                    "ontology_dir": str(ontology_dir),
                    "failed_count": failed_count,
                    "total_count": total_count,
                    "failure_rate": failure_rate,
                    "denominator_source": denominator_source,
                    "batch_failure_count": len(rows),
                    "batch_indices": sorted(set(batch_indices)),
                    "failed_unit_ids": sorted(failed_ids),
                    "error_messages": error_messages[:5],
                }
            )
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                item["failure_rate"],
                item["failed_count"],
                item["period"],
            ),
        )

    @staticmethod
    def _document_type_from_key(doc_type_key: str) -> str:
        normalized = str(doc_type_key or "").upper()
        if normalized == "10K":
            return "10-K"
        if normalized == "10Q":
            return "10-Q"
        return normalized or "UNKNOWN"

    def _expected_filing_provider(self) -> ExpectedFilingProvider:
        config = PipelineConfig.load()
        cache: dict[str, tuple[list[dict[str, str]], str | None]] = {}

        def provider(ticker: str) -> tuple[list[dict[str, str]], str | None]:
            normalized = ticker.upper()
            if normalized in cache:
                return cache[normalized]
            try:
                targets = discover_research_filing_targets(
                    normalized,
                    years=DOCS_MISSING_DISCOVERY_YEARS,
                    config=config,
                )
            except Exception as exc:
                result = ([], str(exc))
            else:
                result = (
                    [
                        {
                            "ticker": target.ticker,
                            "document_type": target.document_type,
                            "doc_type_key": target.document_type.replace("-", ""),
                            "period": target.period,
                            "accession_number": target.accession_number,
                            "filing_date": target.filing_date,
                            "report_date": target.report_date,
                        }
                        for target in targets
                    ],
                    None,
                )
            cache[normalized] = result
            return result

        return provider

    def fingerprint(self) -> dict[str, Any]:
        manifest = self._load_manifest()
        source_manifest_path = self.release_root / "source_manifest.json"
        return {
            "release_root": str(self.release_root),
            "release_id": manifest.get("release_id"),
            "release_format": manifest.get("format"),
            "release_manifest_sha256": _file_sha256(self.manifest_path),
            "source_manifest_sha256": _file_sha256(source_manifest_path)
            if source_manifest_path.is_file()
            else None,
            "global_spine_sha256": _file_sha256(self.global_spine_path)
            if self.global_spine_path.is_file()
            else None,
            "shard_manifest_sha256": _file_sha256(self.shard_manifest_path)
            if self.shard_manifest_path.is_file()
            else None,
        }

    def shard_topology(self) -> dict[str, Any]:
        available: list[dict[str, str]] = []
        missing: list[dict[str, str]] = []
        for ticker, shard_path, entry in self._shard_entries():
            payload = {
                "ticker": ticker,
                "path": str(shard_path),
                "manifest_path": str(entry.get("path") or entry.get("shard_path") or ""),
            }
            if shard_path.is_file():
                available.append(payload)
            else:
                missing.append(payload)
        return {
            "declared_count": len(available) + len(missing),
            "available_count": len(available),
            "missing_count": len(missing),
            "available": available,
            "missing": missing,
        }

    def consistency_report(self, *, sample_limit: int = 20, mode: str = "full") -> dict[str, Any]:
        resolved_mode = _normalize_release_quality_scan_mode(mode)
        mode_label = _release_scan_mode_label(resolved_mode)
        manifest = self._load_manifest()
        errors: list[str] = []
        warnings: list[str] = []
        counts: dict[str, int] = {}
        shard_topology = self.shard_topology()
        if manifest.get("format") != "krw-ontology-release/v3":
            errors.append("manifest_format_not_v3")
        if manifest.get("index_layout") != "global-spine-and-company-shards":
            errors.append("manifest_index_layout_not_v3")
        if manifest.get("monolith_required") is not False:
            errors.append("manifest_monolith_required_not_false")
        shard_entries = self._shard_entries()
        if not self.global_spine_path.is_file():
            errors.append("global_spine_missing")
        if not self.shard_manifest_path.is_file():
            errors.append("shard_manifest_missing")
        if not shard_entries:
            errors.append("shard_manifest_empty")
        errors.extend(
            self._manifest_file_digest_errors(manifest, "global_spine", self.global_spine_path)
        )
        errors.extend(
            self._manifest_file_digest_errors(manifest, "shard_manifest", self.shard_manifest_path)
        )
        if not self.global_spine_path.is_file():
            return {
                "ok": not errors,
                "mode": mode_label,
                "full_shard_checks": False,
                "errors": errors,
                "warnings": warnings,
                "counts": counts,
                "global_spine_path": str(self.global_spine_path),
                "shard_manifest_path": str(self.shard_manifest_path),
                "checked_shards": 0,
                "declared_shards": shard_topology["declared_count"],
                "available_shards": shard_topology["available_count"],
                "missing_shards": shard_topology["missing"],
            }

        schema_verification = verify_global_spine_schema(
            self.global_spine_path,
            deep=False,
            trust_seal=True,
        )
        if not schema_verification.get("ok"):
            errors.extend(
                f"global_spine:{error}" for error in schema_verification.get("errors") or []
            )
        with sqlite3.connect(self.global_spine_path) as spine_conn:
            spine_conn.row_factory = sqlite3.Row
            count_tables = (
                "global_object_locator",
                "global_object_replica",
                "global_document_catalog",
                "global_edge_spine",
                "global_edge_replica",
                "global_chain_index",
            )
            if resolved_mode == "full":
                counts.update(
                    {table: self._safe_count(spine_conn, table) for table in count_tables}
                )
                errors.extend(
                    self._global_endpoint_errors(
                        spine_conn,
                        sample_limit=sample_limit,
                    )
                )
                errors.extend(
                    global_replica_consistency_errors(
                        spine_conn,
                        sample_limit=sample_limit,
                    )
                )
            else:
                metadata_counts = (schema_verification.get("metadata") or {}).get("counts")
                if isinstance(metadata_counts, Mapping):
                    counts.update(
                        {
                            table: int(metadata_counts[table])
                            for table in count_tables
                            if isinstance(metadata_counts.get(table), int)
                        }
                    )
            if resolved_mode == "bounded":
                for ticker, shard_path, _entry in shard_entries:
                    if not shard_path.is_file():
                        errors.append(f"shard_missing:{ticker}:{shard_path}")
                return {
                    "ok": not errors,
                    "mode": mode_label,
                    "full_shard_checks": False,
                    "errors": errors,
                    "warnings": warnings,
                    "counts": counts,
                    "global_spine_path": str(self.global_spine_path),
                    "shard_manifest_path": str(self.shard_manifest_path),
                    "checked_shards": 0,
                    "declared_shards": shard_topology["declared_count"],
                    "available_shards": shard_topology["available_count"],
                    "missing_shards": shard_topology["missing"],
                }
            for index, (ticker, shard_path, entry) in enumerate(shard_entries, start=1):
                if not shard_path.is_file():
                    errors.append(f"shard_missing:{ticker}:{shard_path}")
                    continue
                shard_counts = self._shard_counts(shard_path)
                for key, count_key in (
                    ("document_count", "documents"),
                    ("object_count", "objects"),
                    ("edge_count", "edges"),
                    ("quality_event_count", "quality_events"),
                ):
                    expected = entry.get(key)
                    if expected is not None and int(expected) != shard_counts[count_key]:
                        errors.append(
                            f"shard_manifest_{count_key}_mismatch:{ticker}:{expected}!={shard_counts[count_key]}"
                        )
                counts[f"{ticker}.documents"] = shard_counts["documents"]
                counts[f"{ticker}.objects"] = shard_counts["objects"]
                errors.extend(
                    self._attached_shard_consistency_errors(
                        spine_conn,
                        shard_path,
                        ticker=ticker,
                        schema_name=f"qshard_{index}",
                        sample_limit=sample_limit,
                    )
                )
        return {
            "ok": not errors,
            "mode": mode_label,
            "full_shard_checks": True,
            "errors": errors,
            "warnings": warnings,
            "counts": counts,
            "global_spine_path": str(self.global_spine_path),
            "shard_manifest_path": str(self.shard_manifest_path),
            "checked_shards": len(shard_entries),
            "declared_shards": shard_topology["declared_count"],
            "available_shards": shard_topology["available_count"],
            "missing_shards": shard_topology["missing"],
        }

    @staticmethod
    def _manifest_quality_rollup(entry: Mapping[str, Any]) -> dict[str, Any] | None:
        summary = entry.get("quality_summary")
        if not isinstance(summary, Mapping):
            return None
        return dict(summary)

    @staticmethod
    def _manifest_entry_totals(entry: Mapping[str, Any]) -> dict[str, int]:
        return {
            "documents": int(entry.get("document_count") or 0),
            "tickers": 1 if int(entry.get("document_count") or 0) > 0 else 0,
            "objects": int(entry.get("object_count") or 0),
            "quality_events": int(entry.get("quality_event_count") or 0),
        }

    @classmethod
    def _shard_manifest_diagnostics(
        cls,
        *,
        ticker: str,
        shard_path: Path,
        shard_entry: Mapping[str, Any],
        report: Mapping[str, Any],
        check_sha256: bool,
    ) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        actual_summary = cls._quality_summary_from_shard_report(report)
        expected_summary = shard_entry.get("quality_summary")
        if not isinstance(expected_summary, Mapping):
            errors.append("quality_summary_missing")
        elif expected_summary.get("format") != QUALITY_SHARD_SUMMARY_FORMAT:
            errors.append("quality_summary_format_mismatch")
        elif _canonical_json(expected_summary) != _canonical_json(actual_summary):
            errors.append("quality_summary_mismatch")
        expected_counts = {
            "document_count": int((report.get("totals") or {}).get("documents") or 0),
            "object_count": int((report.get("totals") or {}).get("objects") or 0),
            "edge_count": cls._shard_edge_count(shard_path),
            "quality_event_count": int((report.get("totals") or {}).get("quality_events") or 0),
        }
        for key, actual in expected_counts.items():
            expected = shard_entry.get(key)
            if expected is None:
                errors.append(f"{key}_missing")
                continue
            if int(expected) != actual:
                errors.append(f"{key}_mismatch:{expected}!={actual}")
        sha256 = None
        expected_sha = shard_entry.get("sha256")
        if not isinstance(expected_sha, str) or not expected_sha:
            errors.append("sha256_missing")
        elif check_sha256:
            sha256 = _file_sha256(shard_path)
            if sha256 != expected_sha:
                errors.append("sha256_mismatch")
        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "path": str(shard_path),
            "counts": expected_counts,
            "sha256_checked": bool(check_sha256 and isinstance(expected_sha, str) and expected_sha),
            "sha256": sha256,
            "ticker": ticker,
        }

    @staticmethod
    def _quality_summary_from_shard_report(report: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "format": QUALITY_SHARD_SUMMARY_FORMAT,
            "totals": dict(report.get("totals") or {}),
            "section_status": dict(report.get("section_status") or {}),
            "event_counts": list(report.get("event_counts") or []),
            "ticker_quality": list(report.get("ticker_quality") or []),
            "rejected_reasons": list(report.get("rejected_reasons") or []),
        }

    @staticmethod
    def _shard_edge_count(shard_path: Path) -> int:
        with sqlite3.connect(shard_path) as conn:
            return QualityReleaseScanner._safe_count(conn, "edges")

    @staticmethod
    def _manifest_file_digest_errors(
        manifest: Mapping[str, Any], role: str, path: Path
    ) -> list[str]:
        output = ((manifest.get("indexes") or {}).get(role) or {}) if manifest else {}
        expected = output.get("sha256") if isinstance(output, Mapping) else None
        if not isinstance(expected, str) or not expected:
            return [f"{role}_sha256_missing"]
        if not path.is_file():
            return []
        return [f"{role}_sha256_mismatch"] if _file_sha256(path) != expected else []

    def _load_manifest(self, *, allow_missing: bool = False) -> dict[str, Any]:
        if self._manifest is not None:
            return self._manifest
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if allow_missing:
                return {}
            raise
        if not isinstance(payload, dict):
            raise ValueError(f"release manifest is not an object: {self.manifest_path}")
        self._manifest = payload
        return payload

    def _load_shard_manifest(self) -> dict[str, Any]:
        if self._shard_manifest is not None:
            return self._shard_manifest
        try:
            payload = json.loads(self.shard_manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._shard_manifest = {}
            return self._shard_manifest
        if not isinstance(payload, dict):
            raise ValueError(f"shard manifest is not an object: {self.shard_manifest_path}")
        self._shard_manifest = payload
        return payload

    def _shard_entries(self) -> list[tuple[str, Path, dict[str, Any]]]:
        payload = self._load_shard_manifest()
        raw_shards = payload.get("shards")
        if not isinstance(raw_shards, Mapping):
            return []
        entries: list[tuple[str, Path, dict[str, Any]]] = []
        for ticker, raw_entry in sorted(raw_shards.items()):
            if not isinstance(raw_entry, Mapping):
                continue
            raw_path = raw_entry.get("path") or raw_entry.get("shard_path")
            if not isinstance(raw_path, str) or not raw_path:
                continue
            entries.append(
                (str(ticker).upper(), self._resolve_shard_path(raw_path), dict(raw_entry))
            )
        return entries

    def _shard_path_for_ticker(self, ticker: str) -> Path | None:
        normalized = ticker.upper()
        for shard_ticker, shard_path, _entry in self._shard_entries():
            if shard_ticker == normalized:
                return shard_path
        return None

    def _resolve_release_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (self.release_root / candidate).resolve()
        )
        try:
            resolved.relative_to(self.release_root)
        except ValueError as exc:
            raise ValueError(f"release path escapes release root: {raw_path}") from exc
        return resolved

    def _resolve_shard_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
        elif candidate.parts and candidate.parts[0] == "indexes":
            resolved = (self.release_root / candidate).resolve()
        else:
            resolved = (self.release_root / "indexes" / candidate).resolve()
        try:
            resolved.relative_to(self.release_root)
        except ValueError as exc:
            raise ValueError(f"shard path escapes release root: {raw_path}") from exc
        return resolved

    @staticmethod
    def _build_release_metadata(manifest: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "release_id": manifest.get("release_id"),
            "env": manifest.get("env"),
            "format": manifest.get("format"),
            "index_layout": manifest.get("index_layout"),
            "monolith_required": manifest.get("monolith_required"),
            "builder": manifest.get("builder") or {},
        }

    @staticmethod
    def _safe_count(conn: sqlite3.Connection, table_name: str) -> int:
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
        except sqlite3.Error:
            return 0

    @staticmethod
    def _shard_counts(shard_path: Path) -> dict[str, int]:
        with sqlite3.connect(shard_path) as conn:
            return {
                "documents": QualityReleaseScanner._safe_count(conn, "documents"),
                "objects": QualityReleaseScanner._safe_count(conn, "objects"),
                "edges": QualityReleaseScanner._safe_count(conn, "edges"),
                "quality_events": QualityReleaseScanner._safe_count(conn, "quality_events"),
            }

    @staticmethod
    def _global_endpoint_errors(conn: sqlite3.Connection, *, sample_limit: int) -> list[str]:
        errors: list[str] = []
        checks = (
            (
                "edge_from_missing_locator",
                """
                SELECT edge_id
                FROM global_edge_spine AS edge
                LEFT JOIN global_object_locator AS locator
                  ON locator.object_id = edge.from_object_id
                WHERE edge.from_object_id IS NOT NULL
                  AND locator.object_id IS NULL
                ORDER BY edge.edge_id
                LIMIT ?
                """,
            ),
            (
                "edge_to_missing_locator",
                """
                SELECT edge_id
                FROM global_edge_spine AS edge
                LEFT JOIN global_object_locator AS locator
                  ON locator.object_id = edge.to_object_id
                WHERE edge.to_object_id IS NOT NULL
                  AND locator.object_id IS NULL
                ORDER BY edge.edge_id
                LIMIT ?
                """,
            ),
            (
                "chain_from_missing_locator",
                """
                SELECT link_id
                FROM global_chain_index AS chain
                LEFT JOIN global_object_locator AS locator
                  ON locator.object_id = chain.from_object_id
                WHERE chain.from_object_id IS NOT NULL
                  AND locator.object_id IS NULL
                ORDER BY chain.link_id
                LIMIT ?
                """,
            ),
            (
                "chain_to_missing_locator",
                """
                SELECT link_id
                FROM global_chain_index AS chain
                LEFT JOIN global_object_locator AS locator
                  ON locator.object_id = chain.to_object_id
                WHERE chain.to_object_id IS NOT NULL
                  AND locator.object_id IS NULL
                ORDER BY chain.link_id
                LIMIT ?
                """,
            ),
        )
        for label, sql in checks:
            try:
                rows = [str(row[0]) for row in conn.execute(sql, (sample_limit,)).fetchall()]
            except sqlite3.Error as exc:
                errors.append(f"{label}:query_failed:{exc}")
                continue
            errors.extend(f"{label}:{value}" for value in rows)
        return errors

    @staticmethod
    def _attached_shard_consistency_errors(
        conn: sqlite3.Connection,
        shard_path: Path,
        *,
        ticker: str,
        schema_name: str,
        sample_limit: int,
    ) -> list[str]:
        errors: list[str] = []
        conn.execute(f"ATTACH DATABASE ? AS {schema_name}", (str(shard_path),))
        try:
            checks = (
                (
                    "object_missing_replica",
                    f"""
                    SELECT objects.id
                    FROM {schema_name}.objects AS objects
                    LEFT JOIN global_object_replica AS replica
                      ON replica.object_id = objects.id
                     AND replica.ticker = objects.ticker
                     AND replica.document_id = COALESCE(
                            NULLIF(objects.source_document_id, ''),
                            UPPER(objects.ticker) || ':' || objects.doc_type_key || ':' || objects.period
                         )
                     AND replica.document_type = objects.document_type
                     AND replica.period = objects.period
                    WHERE objects.ticker = ?
                      AND replica.object_id IS NULL
                    ORDER BY objects.id
                    LIMIT ?
                    """,
                ),
                (
                    "replica_missing_object",
                    f"""
                    SELECT replica.object_id
                    FROM global_object_replica AS replica
                    LEFT JOIN {schema_name}.objects AS objects
                      ON objects.id = replica.object_id
                     AND objects.ticker = replica.ticker
                     AND COALESCE(
                            NULLIF(objects.source_document_id, ''),
                            UPPER(objects.ticker) || ':' || objects.doc_type_key || ':' || objects.period
                         ) = replica.document_id
                     AND objects.document_type = replica.document_type
                     AND objects.period = replica.period
                    WHERE replica.ticker = ?
                      AND objects.id IS NULL
                    ORDER BY replica.object_id
                    LIMIT ?
                    """,
                ),
                (
                    "edge_missing_replica",
                    f"""
                    SELECT edges.id
                    FROM {schema_name}.edges AS edges
                    LEFT JOIN global_edge_replica AS replica
                      ON replica.edge_id = edges.id
                     AND replica.ticker = edges.ticker
                     AND replica.document_id = COALESCE(
                            NULLIF(edges.source_document_id, ''),
                            UPPER(edges.ticker) || ':' || edges.doc_type_key || ':' || edges.period
                         )
                     AND replica.document_type = edges.document_type
                     AND replica.period = edges.period
                    WHERE edges.ticker = ?
                      AND replica.edge_id IS NULL
                    ORDER BY edges.id
                    LIMIT ?
                    """,
                ),
                (
                    "replica_missing_edge",
                    f"""
                    SELECT replica.edge_id
                    FROM global_edge_replica AS replica
                    LEFT JOIN {schema_name}.edges AS edges
                      ON edges.id = replica.edge_id
                     AND edges.ticker = replica.ticker
                     AND COALESCE(
                            NULLIF(edges.source_document_id, ''),
                            UPPER(edges.ticker) || ':' || edges.doc_type_key || ':' || edges.period
                         ) = replica.document_id
                     AND edges.document_type = replica.document_type
                     AND edges.period = replica.period
                    WHERE replica.ticker = ?
                      AND edges.id IS NULL
                    ORDER BY replica.edge_id
                    LIMIT ?
                    """,
                ),
                (
                    "document_missing_catalog",
                    f"""
                    SELECT docs.ticker || ':' || docs.document_type || ':' || docs.period
                    FROM {schema_name}.documents AS docs
                    LEFT JOIN global_document_catalog AS catalog
                      ON catalog.ticker = docs.ticker
                     AND catalog.document_type = docs.document_type
                     AND catalog.period = docs.period
                    WHERE docs.ticker = ?
                      AND catalog.document_id IS NULL
                    ORDER BY docs.document_type, docs.period
                    LIMIT ?
                    """,
                ),
            )
            for label, sql in checks:
                try:
                    rows = [
                        str(row[0]) for row in conn.execute(sql, (ticker, sample_limit)).fetchall()
                    ]
                except sqlite3.Error as exc:
                    errors.append(f"{label}:{ticker}:query_failed:{exc}")
                    continue
                errors.extend(f"{label}:{ticker}:{value}" for value in rows)
        finally:
            conn.commit()
            conn.execute(f"DETACH DATABASE {schema_name}")
        return errors


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _document_type_from_key(doc_type_key: str) -> str:
    normalized = str(doc_type_key or "").upper()
    if normalized == "10K":
        return "10-K"
    if normalized == "10Q":
        return "10-Q"
    return normalized


def _parse_period(period: str | None) -> dict[str, Any] | None:
    if not period:
        return None
    match = _PERIOD_RE.match(str(period).strip())
    if not match:
        return None
    prefix = (match.group("prefix") or "").upper()
    quarter = match.group("quarter")
    return {
        "prefix": prefix,
        "year": int(match.group("year")),
        "quarter": int(quarter) if quarter else None,
    }


def _period_sort_key(period: str | None) -> tuple[int, int, str]:
    parsed = _parse_period(period)
    if parsed is None:
        return (0, 0, str(period or ""))
    return (
        int(parsed["year"]),
        int(parsed["quarter"] or 0),
        str(period or ""),
    )
