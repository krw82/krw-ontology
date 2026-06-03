"""Data models for release quality inspection and repair planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL_STATUSES = {SUCCEEDED, FAILED, CANCELLED}

DOCS_MISSING = "docs_missing"
SECTION_FAIL = "section_fail"
SECTION_WARN = "section_warn"
BATCH_FAILURE = "batch_failure"
COVERAGE_GAP = "coverage_gap"
REPAIR_REFERENCE = "repair_reference"
NORMALIZE_NUMERIC = "normalize_numeric"

EXECUTABLE_REPAIR_KINDS = {
    BATCH_FAILURE,
    DOCS_MISSING,
    NORMALIZE_NUMERIC,
    REPAIR_REFERENCE,
    SECTION_FAIL,
    SECTION_WARN,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class QualityIssue:
    kind: str
    severity: str
    count: int
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TickerQuality:
    ticker: str
    docs: int
    section_fail: int = 0
    section_warn: int = 0
    batch_failure: int = 0
    coverage_gap: int = 0
    rejected_object: int = 0

    @property
    def issue_count(self) -> int:
        return (
            self.section_fail
            + self.section_warn
            + self.batch_failure
            + self.coverage_gap
        )

    def problem_kinds(self, *, min_docs: int) -> list[str]:
        kinds: list[str] = []
        if self.docs < min_docs:
            kinds.append(DOCS_MISSING)
        if self.section_fail:
            kinds.append(SECTION_FAIL)
        if self.section_warn:
            kinds.append(SECTION_WARN)
        if self.batch_failure:
            kinds.append(BATCH_FAILURE)
        if self.coverage_gap:
            kinds.append(COVERAGE_GAP)
        return kinds

    def severity(self, *, min_docs: int) -> str:
        if self.docs < min_docs or self.section_fail:
            return "high"
        if self.section_warn or self.batch_failure or self.coverage_gap:
            return "medium"
        if self.rejected_object:
            return "low"
        return "ok"

    def to_dict(self, *, min_docs: int) -> dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity(min_docs=min_docs)
        payload["problem_kinds"] = self.problem_kinds(min_docs=min_docs)
        return payload


@dataclass
class RepairJob:
    job_id: str
    plan_id: str
    kind: str
    ticker: str
    status: str = PENDING
    document_type: str | None = None
    doc_type_key: str | None = None
    period: str | None = None
    stage: str | None = None
    batch_index: int | None = None
    ontology_dir: str | None = None
    artifact_index_path: str | None = None
    source_event_id: str | None = None
    reason: str | None = None
    count: int = 1
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RepairJob":
        return cls(
            job_id=payload["job_id"],
            plan_id=payload["plan_id"],
            kind=payload["kind"],
            ticker=payload["ticker"],
            status=payload.get("status", PENDING),
            document_type=payload.get("document_type"),
            doc_type_key=payload.get("doc_type_key"),
            period=payload.get("period"),
            stage=payload.get("stage"),
            batch_index=payload.get("batch_index"),
            ontology_dir=payload.get("ontology_dir"),
            artifact_index_path=payload.get("artifact_index_path"),
            source_event_id=payload.get("source_event_id"),
            reason=payload.get("reason"),
            count=int(payload.get("count", 1)),
            payload=dict(payload.get("payload") or {}),
            attempts=int(payload.get("attempts", 0)),
            created_at=payload.get("created_at") or utc_now(),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            error=payload.get("error"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepairPlan:
    plan_id: str
    source_index_path: str
    release_label: str
    min_docs: int
    job_ids: list[str]
    summary: dict[str, int]
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RepairPlan":
        return cls(
            plan_id=payload["plan_id"],
            source_index_path=payload["source_index_path"],
            release_label=payload.get("release_label", ""),
            min_docs=int(payload.get("min_docs", 5)),
            job_ids=list(payload.get("job_ids") or []),
            summary={str(k): int(v) for k, v in dict(payload.get("summary") or {}).items()},
            created_at=payload.get("created_at") or utc_now(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
