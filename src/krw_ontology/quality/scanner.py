"""SQLite-backed quality scanner for ontology releases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

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
QUALITY_SHARD_SUMMARY_FORMAT = "krw-ontology-shard-quality-summary/v1"
QUALITY_RELEASE_SCAN_MODES = {"bounded", "full"}


def _normalize_release_quality_scan_mode(mode: str) -> str:
    normalized = str(mode or "bounded").strip().lower()
    if normalized not in QUALITY_RELEASE_SCAN_MODES:
        raise ValueError(f"invalid quality scan mode: {mode}")
    return normalized


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
            raw_path = manifest.get("global_spine_path") if isinstance(manifest.get("global_spine_path"), str) else ""
        return self._resolve_release_path(raw_path or "indexes/global_spine.sqlite")

    @property
    def shard_manifest_path(self) -> Path:
        manifest = self._load_manifest(allow_missing=True)
        raw_path = ((manifest.get("indexes") or {}).get("shard_manifest") or {}).get("path")
        return self._resolve_release_path(raw_path if isinstance(raw_path, str) and raw_path else "indexes/shard_manifest.json")

    def scan(
        self,
        *,
        min_docs: int = 5,
        mode: str = "bounded",
        sample_limit: int = 20,
    ) -> dict[str, Any]:
        resolved_mode = _normalize_release_quality_scan_mode(mode)
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

        for ticker, shard_path, entry in shard_entries:
            if not shard_path.is_file():
                continue
            report = None if resolved_mode == "full" else self._manifest_quality_rollup(entry)
            if report is None:
                report = QualityShardScanner(shard_path).scan(min_docs=min_docs)
                opened_shards += 1
            else:
                manifest_rollup_shards += 1
            shard_totals = report.get("totals") or self._manifest_entry_totals(entry)
            totals["documents"] += int(shard_totals.get("documents") or 0)
            totals["tickers"] += int(shard_totals.get("tickers") or 0)
            totals["objects"] += int(shard_totals.get("objects") or 0)
            totals["quality_events"] += int(shard_totals.get("quality_events") or 0)
            section_status.update({str(key): int(value) for key, value in dict(report.get("section_status") or {}).items()})
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
            kind
            for ticker in tickers
            for kind in ticker.problem_kinds(min_docs=min_docs)
        )
        consistency = self.consistency_report(sample_limit=sample_limit, mode=resolved_mode)
        if consistency["errors"]:
            kind_counts["release_consistency"] = len(consistency["errors"])
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
                "mode": resolved_mode,
                "rollup_source": rollup_source,
                "manifest_rollup_shards": manifest_rollup_shards,
                "opened_shards": opened_shards,
                "declared_shards": shard_topology["declared_count"],
                "available_shards": shard_topology["available_count"],
                "full_consistency": resolved_mode == "full",
            },
            "shards": shard_topology,
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
                rows.append(_ticker_quality_from_rollup(ticker, rollup, rollup.get("totals") or self._manifest_entry_totals(entry)))
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
        payload = QualityShardScanner(shard_path).explain_ticker(normalized, min_docs=min_docs, limit=limit)
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
    ) -> list[RepairJob]:
        jobs: list[RepairJob] = []
        for _ticker, shard_path, _entry in self._shard_entries():
            if not shard_path.is_file():
                continue
            jobs.extend(
                QualityShardScanner(shard_path).build_repair_jobs(
                    plan_id=plan_id,
                    min_docs=min_docs,
                    kinds=kinds,
                    include_warn=include_warn,
                )
            )
        return QualityShardScanner._dedupe_jobs(jobs)

    def fingerprint(self) -> dict[str, Any]:
        manifest = self._load_manifest()
        source_manifest_path = self.release_root / "source_manifest.json"
        return {
            "release_root": str(self.release_root),
            "release_id": manifest.get("release_id"),
            "release_format": manifest.get("format"),
            "release_manifest_sha256": _file_sha256(self.manifest_path),
            "source_manifest_sha256": _file_sha256(source_manifest_path) if source_manifest_path.is_file() else None,
            "global_spine_sha256": _file_sha256(self.global_spine_path) if self.global_spine_path.is_file() else None,
            "shard_manifest_sha256": _file_sha256(self.shard_manifest_path) if self.shard_manifest_path.is_file() else None,
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
        if not self.global_spine_path.is_file():
            return {
                "ok": not errors,
                "mode": resolved_mode,
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

        with sqlite3.connect(self.global_spine_path) as spine_conn:
            spine_conn.row_factory = sqlite3.Row
            counts.update(
                {
                    "global_object_locator": self._safe_count(spine_conn, "global_object_locator"),
                    "global_document_catalog": self._safe_count(spine_conn, "global_document_catalog"),
                    "global_edge_spine": self._safe_count(spine_conn, "global_edge_spine"),
                    "global_chain_index": self._safe_count(spine_conn, "global_chain_index"),
                }
            )
            errors.extend(self._global_endpoint_errors(spine_conn, sample_limit=sample_limit))
            if resolved_mode == "bounded":
                for ticker, shard_path, _entry in shard_entries:
                    if not shard_path.is_file():
                        errors.append(f"shard_missing:{ticker}:{shard_path}")
                return {
                    "ok": not errors,
                    "mode": resolved_mode,
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
                        errors.append(f"shard_manifest_{count_key}_mismatch:{ticker}:{expected}!={shard_counts[count_key]}")
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
            "mode": resolved_mode,
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
            entries.append((str(ticker).upper(), self._resolve_shard_path(raw_path), dict(raw_entry)))
        return entries

    def _shard_path_for_ticker(self, ticker: str) -> Path | None:
        normalized = ticker.upper()
        for shard_ticker, shard_path, _entry in self._shard_entries():
            if shard_ticker == normalized:
                return shard_path
        return None

    def _resolve_release_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else (self.release_root / candidate).resolve()
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
                    "object_missing_locator",
                    f"""
                    SELECT objects.id
                    FROM {schema_name}.objects AS objects
                    LEFT JOIN global_object_locator AS locator
                      ON locator.object_id = objects.id
                    WHERE objects.ticker = ?
                      AND locator.object_id IS NULL
                    ORDER BY objects.id
                    LIMIT ?
                    """,
                ),
                (
                    "locator_missing_object",
                    f"""
                    SELECT locator.object_id
                    FROM global_object_locator AS locator
                    LEFT JOIN {schema_name}.objects AS objects
                      ON objects.id = locator.object_id
                    WHERE locator.ticker = ?
                      AND objects.id IS NULL
                    ORDER BY locator.object_id
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
                    rows = [str(row[0]) for row in conn.execute(sql, (ticker, sample_limit)).fetchall()]
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
