"""Local file-backed queue for ticker-level research jobs."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL_STATUSES = {SUCCEEDED, FAILED, CANCELLED}
FULL_REFRESH = "full_refresh"
FILING_UPDATE = "filing_update"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class QueueJob:
    job_id: str
    ticker: str
    years: int
    job_type: str = FULL_REFRESH
    force: bool = False
    document_type: str | None = None
    periods: list[str] | None = None
    latest: bool = False
    publish_root: str | None = None
    publish_index_path: str | None = None
    status: str = PENDING
    attempts: int = 0
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None

    @classmethod
    def create(
        cls,
        ticker: str,
        *,
        years: int,
        force: bool,
        publish_root: Path | None,
        publish_index_path: Path | None,
    ) -> "QueueJob":
        normalized_ticker = ticker.upper()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        suffix = uuid.uuid4().hex[:8]
        return cls(
            job_id=f"{stamp}-{normalized_ticker}-{suffix}",
            ticker=normalized_ticker,
            years=years,
            job_type=FULL_REFRESH,
            force=force,
            publish_root=str(publish_root) if publish_root is not None else None,
            publish_index_path=str(publish_index_path) if publish_index_path is not None else None,
            created_at=utc_now(),
        )

    @classmethod
    def create_update(
        cls,
        ticker: str,
        *,
        document_type: str,
        periods: list[str],
        latest: bool,
        force: bool,
        publish_root: Path | None,
        publish_index_path: Path | None,
    ) -> "QueueJob":
        normalized_ticker = ticker.upper()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        suffix = uuid.uuid4().hex[:8]
        return cls(
            job_id=f"{stamp}-{normalized_ticker}-{suffix}",
            ticker=normalized_ticker,
            years=0,
            job_type=FILING_UPDATE,
            force=force,
            document_type=document_type,
            periods=periods,
            latest=latest,
            publish_root=str(publish_root) if publish_root is not None else None,
            publish_index_path=str(publish_index_path) if publish_index_path is not None else None,
            created_at=utc_now(),
        )

    @classmethod
    def from_dict(cls, payload: dict) -> "QueueJob":
        return cls(
            job_id=payload["job_id"],
            ticker=payload["ticker"],
            years=int(payload.get("years", 0)),
            job_type=payload.get("job_type", FULL_REFRESH),
            force=bool(payload.get("force", False)),
            document_type=payload.get("document_type"),
            periods=list(payload.get("periods") or []),
            latest=bool(payload.get("latest", False)),
            publish_root=payload.get("publish_root"),
            publish_index_path=payload.get("publish_index_path"),
            status=payload.get("status", PENDING),
            attempts=int(payload.get("attempts", 0)),
            created_at=payload.get("created_at") or utc_now(),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            error=payload.get("error"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


class LockHeldError(RuntimeError):
    """Raised when another live process owns a queue lock."""


class FileProcessLock:
    """Atomic PID lock based on O_EXCL lock-file creation."""

    def __init__(self, path: Path):
        self.path = path
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "created_at": utc_now()}
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner = self.owner_pid()
                if owner is not None and is_pid_running(owner):
                    raise LockHeldError(f"Lock is held by pid {owner}: {self.path}")
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            self._owned = True
            return

    def owner_pid(self) -> int | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        pid = payload.get("pid")
        return int(pid) if isinstance(pid, int) else None

    def release(self) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False

    def __enter__(self) -> "FileProcessLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class PipelineQueue:
    """Manage ticker jobs under <root>/.krw_pipeline."""

    def __init__(self, root: Path):
        self.root = root
        self.queue_dir = root / ".krw_pipeline"
        self.jobs_dir = self.queue_dir / "jobs"
        self.logs_dir = self.queue_dir / "logs"
        self.locks_dir = self.queue_dir / "locks"
        self.queue_log_path = self.queue_dir / "queue.jsonl"
        self.worker_pid_path = self.queue_dir / "worker.pid"
        self.stop_requested_path = self.queue_dir / "stop_requested"
        self.worker_log_path = self.logs_dir / "worker.log"
        self.worker_lock_path = self.locks_dir / "worker.lock"
        self.publish_lock_path = self.locks_dir / "publish.lock"

    def ensure_dirs(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    def job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def job_log_path(self, job_id: str) -> Path:
        return self.logs_dir / f"{job_id}.log"

    def add_job(
        self,
        ticker: str,
        *,
        years: int,
        force: bool,
        publish_root: Path | None,
        publish_index_path: Path | None = None,
    ) -> QueueJob:
        self.ensure_dirs()
        job = QueueJob.create(
            ticker,
            years=years,
            force=force,
            publish_root=publish_root,
            publish_index_path=publish_index_path,
        )
        self.save_job(job)
        self.append_event("queued", job)
        return job

    def add_update_job(
        self,
        ticker: str,
        *,
        document_type: str,
        periods: list[str],
        latest: bool,
        force: bool,
        publish_root: Path | None,
        publish_index_path: Path | None = None,
    ) -> QueueJob:
        self.ensure_dirs()
        job = QueueJob.create_update(
            ticker,
            document_type=document_type,
            periods=periods,
            latest=latest,
            force=force,
            publish_root=publish_root,
            publish_index_path=publish_index_path,
        )
        self.save_job(job)
        self.append_event("queued", job)
        return job

    def save_job(self, job: QueueJob) -> None:
        self.ensure_dirs()
        path = self.job_path(job.job_id)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(job.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def load_job(self, job_id: str) -> QueueJob:
        return QueueJob.from_dict(json.loads(self.job_path(job_id).read_text(encoding="utf-8")))

    def list_jobs(self, statuses: Iterable[str] | None = None) -> list[QueueJob]:
        self.ensure_dirs()
        wanted = set(statuses) if statuses is not None else None
        jobs: list[QueueJob] = []
        for path in sorted(self.jobs_dir.glob("*.json")):
            job = QueueJob.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if wanted is None or job.status in wanted:
                jobs.append(job)
        return sorted(jobs, key=lambda job: (job.created_at, job.job_id))

    def next_pending_job(self) -> QueueJob | None:
        pending = self.list_jobs(statuses=[PENDING])
        return pending[0] if pending else None

    def active_job_for_ticker(self, ticker: str) -> QueueJob | None:
        normalized = ticker.upper()
        for job in self.list_jobs(statuses=[PENDING, RUNNING]):
            if job.ticker == normalized:
                return job
        return None

    def mark_running(self, job: QueueJob) -> QueueJob:
        job.status = RUNNING
        job.attempts += 1
        job.started_at = utc_now()
        job.finished_at = None
        job.error = None
        self.save_job(job)
        self.append_event("running", job)
        return job

    def mark_pending(self, job: QueueJob, reason: str | None = None) -> QueueJob:
        job.status = PENDING
        job.started_at = None
        job.finished_at = None
        job.error = None
        self.save_job(job)
        self.append_event("requeued", job, {"reason": reason} if reason else None)
        return job

    def mark_succeeded(self, job: QueueJob) -> QueueJob:
        job.status = SUCCEEDED
        job.finished_at = utc_now()
        job.error = None
        self.save_job(job)
        self.append_event("succeeded", job)
        return job

    def mark_failed(self, job: QueueJob, error: str) -> QueueJob:
        job.status = FAILED
        job.finished_at = utc_now()
        job.error = error
        self.save_job(job)
        self.append_event("failed", job, {"error": error})
        return job

    def mark_cancelled(self, job: QueueJob, reason: str | None = None) -> QueueJob:
        job.status = CANCELLED
        job.finished_at = utc_now()
        job.error = reason
        self.save_job(job)
        self.append_event("cancelled", job, {"reason": reason} if reason else None)
        return job

    def append_event(self, event: str, job: QueueJob, extra: dict | None = None) -> None:
        self.ensure_dirs()
        payload = {
            "timestamp": utc_now(),
            "event": event,
            "job_id": job.job_id,
            "job_type": job.job_type,
            "ticker": job.ticker,
            "status": job.status,
        }
        if extra:
            payload.update(extra)
        with self.queue_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def append_job_log(self, job_id: str, line: str) -> None:
        self.ensure_dirs()
        with self.job_log_path(job_id).open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")

    def write_worker_pid(self, pid: int) -> None:
        self.ensure_dirs()
        self.worker_pid_path.write_text(str(pid), encoding="utf-8")

    def clear_worker_pid(self, pid: int | None = None) -> None:
        if not self.worker_pid_path.exists():
            return
        if pid is not None and self.worker_pid() != pid:
            return
        self.worker_pid_path.unlink(missing_ok=True)

    def worker_pid(self) -> int | None:
        try:
            text = self.worker_pid_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    def worker_is_running(self) -> bool:
        pid = self.worker_pid()
        if pid is not None and is_pid_running(pid):
            return True
        lock_pid = FileProcessLock(self.worker_lock_path).owner_pid()
        return lock_pid is not None and is_pid_running(lock_pid)

    def request_stop(self) -> None:
        self.ensure_dirs()
        self.stop_requested_path.write_text(utc_now() + "\n", encoding="utf-8")

    def clear_stop_request(self) -> None:
        self.stop_requested_path.unlink(missing_ok=True)

    def stop_requested(self) -> bool:
        return self.stop_requested_path.exists()
