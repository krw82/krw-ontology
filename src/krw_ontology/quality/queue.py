"""File-backed quality repair plan storage."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from krw_ontology.pipeline.queue import FileProcessLock, LockHeldError, is_pid_running
from krw_ontology.quality.models import (
    CANCELLED,
    FAILED,
    PENDING,
    RUNNING,
    SUCCEEDED,
    RepairJob,
    RepairPlan,
    utc_now,
)


def default_plan_id() -> str:
    return "qr_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


class QualityRepairStore:
    """Manage quality repair plans under <root>/.krw_pipeline/quality."""

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()
        self.queue_dir = self.root / ".krw_pipeline" / "quality"
        self.plans_dir = self.queue_dir / "plans"
        self.jobs_dir = self.queue_dir / "jobs"
        self.logs_dir = self.queue_dir / "logs"
        self.locks_dir = self.queue_dir / "locks"
        self.queue_log_path = self.queue_dir / "queue.jsonl"
        self.worker_pid_path = self.queue_dir / "worker.pid"
        self.worker_state_path = self.queue_dir / "worker_state.json"
        self.worker_log_path = self.logs_dir / "worker.log"
        self.worker_lock_path = self.locks_dir / "worker.lock"

    def ensure_dirs(self) -> None:
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    def plan_path(self, plan_id: str) -> Path:
        return self.plans_dir / f"{plan_id}.json"

    def job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def save_plan(self, plan: RepairPlan) -> None:
        self.ensure_dirs()
        self._atomic_write_json(self.plan_path(plan.plan_id), plan.to_dict())

    def load_plan(self, plan_id: str) -> RepairPlan:
        return RepairPlan.from_dict(json.loads(self.plan_path(plan_id).read_text(encoding="utf-8")))

    def latest_plan(self) -> RepairPlan | None:
        self.ensure_dirs()
        paths = sorted(self.plans_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
        if not paths:
            return None
        return RepairPlan.from_dict(json.loads(paths[-1].read_text(encoding="utf-8")))

    def list_plans(self) -> list[RepairPlan]:
        self.ensure_dirs()
        plans = [
            RepairPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in self.plans_dir.glob("*.json")
        ]
        return sorted(plans, key=lambda plan: plan.created_at)

    def save_job(self, job: RepairJob) -> None:
        self.ensure_dirs()
        self._atomic_write_json(self.job_path(job.job_id), job.to_dict())

    def load_job(self, job_id: str) -> RepairJob:
        return RepairJob.from_dict(json.loads(self.job_path(job_id).read_text(encoding="utf-8")))

    def list_jobs(
        self,
        *,
        plan_id: str | None = None,
        statuses: Iterable[str] | None = None,
    ) -> list[RepairJob]:
        self.ensure_dirs()
        wanted_statuses = set(statuses) if statuses is not None else None
        jobs: list[RepairJob] = []
        for path in self.jobs_dir.glob("*.json"):
            job = RepairJob.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if plan_id is not None and job.plan_id != plan_id:
                continue
            if wanted_statuses is not None and job.status not in wanted_statuses:
                continue
            jobs.append(job)
        return sorted(jobs, key=lambda job: (job.created_at, job.job_id))

    def add_plan(self, plan: RepairPlan, jobs: list[RepairJob], *, force: bool = False) -> RepairPlan:
        self.ensure_dirs()
        if self.plan_path(plan.plan_id).exists() and not force:
            raise FileExistsError(f"repair plan already exists: {plan.plan_id}")
        existing_job_ids = {job.job_id for job in self.list_jobs(plan_id=plan.plan_id)}
        saved_job_ids: list[str] = []
        for job in jobs:
            if job.job_id in existing_job_ids and not force:
                continue
            self.save_job(job)
            saved_job_ids.append(job.job_id)
            self.append_event("planned", job)
        plan.job_ids = saved_job_ids
        plan.summary = dict(Counter(job.kind for job in jobs if job.job_id in saved_job_ids))
        self.save_plan(plan)
        return plan

    def mark_running(self, job: RepairJob) -> RepairJob:
        job.status = RUNNING
        job.attempts += 1
        job.started_at = utc_now()
        job.finished_at = None
        job.error = None
        self.save_job(job)
        self.append_event("running", job)
        return job

    def mark_succeeded(self, job: RepairJob) -> RepairJob:
        job.status = SUCCEEDED
        job.finished_at = utc_now()
        job.error = None
        self.save_job(job)
        self.append_event("succeeded", job)
        return job

    def mark_failed(self, job: RepairJob, error: str) -> RepairJob:
        job.status = FAILED
        job.finished_at = utc_now()
        job.error = error
        self.save_job(job)
        self.append_event("failed", job, {"error": error})
        return job

    def mark_pending(self, job: RepairJob, reason: str | None = None) -> RepairJob:
        job.status = PENDING
        job.started_at = None
        job.finished_at = None
        job.error = reason
        self.save_job(job)
        self.append_event("pending", job, {"reason": reason} if reason else None)
        return job

    def mark_cancelled(self, job: RepairJob, reason: str | None = None) -> RepairJob:
        job.status = CANCELLED
        job.finished_at = utc_now()
        job.error = reason
        self.save_job(job)
        self.append_event("cancelled", job, {"reason": reason} if reason else None)
        return job

    def status_counts(self, *, plan_id: str | None = None) -> dict[str, int]:
        counts = {PENDING: 0, RUNNING: 0, SUCCEEDED: 0, FAILED: 0, CANCELLED: 0}
        for job in self.list_jobs(plan_id=plan_id):
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts

    def kind_counts(self, *, plan_id: str | None = None) -> dict[str, int]:
        return dict(Counter(job.kind for job in self.list_jobs(plan_id=plan_id)))

    def append_event(self, event: str, job: RepairJob, extra: dict | None = None) -> None:
        self.ensure_dirs()
        payload = {
            "timestamp": utc_now(),
            "event": event,
            "job_id": job.job_id,
            "plan_id": job.plan_id,
            "kind": job.kind,
            "ticker": job.ticker,
            "status": job.status,
        }
        if extra:
            payload.update(extra)
        with self.queue_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

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

    def write_worker_pid(self, pid: int) -> None:
        self.ensure_dirs()
        self.worker_pid_path.write_text(str(pid), encoding="utf-8")

    def write_worker_state(self, pid: int, *, mode: dict) -> None:
        self.ensure_dirs()
        payload = {
            "pid": pid,
            "started_at": utc_now(),
            "mode": mode,
        }
        tmp_path = self.worker_state_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(self.worker_state_path)

    def worker_state(self) -> dict | None:
        try:
            return json.loads(self.worker_state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def clear_worker_pid(self, pid: int | None = None) -> None:
        if not self.worker_pid_path.exists():
            return
        if pid is not None and self.worker_pid() != pid:
            return
        self.worker_pid_path.unlink(missing_ok=True)

    def clear_worker_state(self, pid: int | None = None) -> None:
        if not self.worker_state_path.exists():
            return
        if pid is not None:
            payload = self.worker_state()
            if payload and payload.get("pid") != pid:
                return
        self.worker_state_path.unlink(missing_ok=True)

    def clear(self, *, plan_id: str | None = None) -> int:
        self.ensure_dirs()
        removed = 0
        if plan_id is None:
            for path in [*self.plans_dir.glob("*.json"), *self.jobs_dir.glob("*.json")]:
                path.unlink()
                removed += 1
            return removed
        for job in self.list_jobs(plan_id=plan_id):
            self.job_path(job.job_id).unlink(missing_ok=True)
            removed += 1
        self.plan_path(plan_id).unlink(missing_ok=True)
        removed += 1
        return removed

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(path)


__all__ = ["QualityRepairStore", "default_plan_id", "FileProcessLock", "LockHeldError"]
