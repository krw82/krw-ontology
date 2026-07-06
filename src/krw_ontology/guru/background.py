"""Background execution support for the guru ontology pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from krw_ontology.guru.extractor import DEFAULT_AGENT_SDK_CONCURRENCY
from krw_ontology.guru.parser import DEFAULT_MAX_SPAN_CHARS
from krw_ontology.guru.pipeline import run_guru_pipeline
from krw_ontology.guru.workspace import guru_root, guru_running_root
from krw_ontology.pipeline.queue import FileProcessLock, LockHeldError, is_pid_running


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class GuruRunStore:
    """Manage background guru worker state under <running-root>/.krw_pipeline/guru."""

    def __init__(self, running_root: Path):
        self.running_root = running_root
        self.root = running_root / ".krw_pipeline" / "guru"
        self.logs_dir = self.root / "logs"
        self.locks_dir = self.root / "locks"
        self.worker_pid_path = self.root / "worker.pid"
        self.worker_state_path = self.root / "worker_state.json"
        self.worker_lock_path = self.locks_dir / "worker.lock"
        self.worker_log_path = self.logs_dir / "worker.log"

    def ensure_dirs(self) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    def write_worker_pid(self, pid: int) -> None:
        self.ensure_dirs()
        self.worker_pid_path.write_text(f"{pid}\n", encoding="utf-8")

    def clear_worker_pid(self, pid: int | None = None) -> None:
        current = self.worker_pid()
        if pid is None or current == pid:
            self.worker_pid_path.unlink(missing_ok=True)

    def worker_pid(self) -> int | None:
        try:
            return int(self.worker_pid_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError, OSError):
            return None

    def worker_is_running(self) -> bool:
        pid = self.worker_pid()
        return pid is not None and is_pid_running(pid)

    def write_worker_state(self, payload: dict[str, Any]) -> None:
        self.ensure_dirs()
        tmp_path = self.worker_state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.worker_state_path)

    def worker_state(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.worker_state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None


def start_background_guru_run(
    root: Path | str | None = None,
    *,
    running_root: Path | str | None = None,
    authors: str | None = None,
    limit_per_author: int | None = None,
    force: bool = False,
    discover: bool = True,
    execute_agent_sdk: bool = False,
    model: str | None = None,
    max_batches: int | None = None,
    concurrency: int = DEFAULT_AGENT_SDK_CONCURRENCY,
    max_span_chars: int = DEFAULT_MAX_SPAN_CHARS,
) -> dict[str, Any]:
    """Launch ``guru run-worker`` as a detached background process."""
    root_path = guru_root(root)
    running_path = guru_running_root(running_root)
    running_path.mkdir(parents=True, exist_ok=True)
    store = GuruRunStore(running_path)
    store.ensure_dirs()
    if store.worker_is_running():
        raise RuntimeError(f"Guru background run already active pid={store.worker_pid()}")

    command = [
        sys.executable,
        "-c",
        "from krw_ontology.cli.main import app; app()",
        "guru",
        "run-worker",
        "--root",
        str(root_path),
        "--running-root",
        str(running_path),
        "--max-span-chars",
        str(max_span_chars),
        "--concurrency",
        str(concurrency),
    ]
    if authors:
        command.extend(["--authors", authors])
    if limit_per_author is not None:
        command.extend(["--limit-per-author", str(limit_per_author)])
    if force:
        command.append("--force")
    if not discover:
        command.append("--no-discover")
    if execute_agent_sdk:
        command.append("--execute-agent-sdk")
    if model:
        command.extend(["--model", model])
    if max_batches is not None:
        command.extend(["--max-batches", str(max_batches)])

    with store.worker_log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n[{utc_now()}] guru background run launching\n")
        log_handle.write(f"command: {' '.join(command)}\n")
        log_handle.flush()
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    payload = {
        "status": "running",
        "pid": process.pid,
        "root": str(root_path),
        "running_root": str(running_path),
        "started_at": utc_now(),
        "finished_at": None,
        "command": command,
        "log_path": str(store.worker_log_path),
        "state_path": str(store.worker_state_path),
        "execute_agent_sdk": execute_agent_sdk,
        "concurrency": concurrency,
    }
    store.write_worker_pid(process.pid)
    store.write_worker_state(payload)
    return payload


def run_background_worker(
    root: Path | str | None = None,
    *,
    running_root: Path | str | None = None,
    author_keys: list[str] | None = None,
    force: bool = False,
    limit_per_author: int | None = None,
    discover: bool = True,
    execute_agent_sdk: bool = False,
    model: str | None = None,
    max_batches: int | None = None,
    concurrency: int = DEFAULT_AGENT_SDK_CONCURRENCY,
    max_span_chars: int = DEFAULT_MAX_SPAN_CHARS,
) -> dict[str, Any]:
    """Run the guru pipeline with persistent background state."""
    root_path = guru_root(root)
    running_path = guru_running_root(running_root)
    store = GuruRunStore(running_path)
    store.ensure_dirs()
    pid = os.getpid()
    try:
        with FileProcessLock(store.worker_lock_path):
            started_at = utc_now()
            store.write_worker_pid(pid)
            store.write_worker_state(
                {
                    "status": "running",
                    "pid": pid,
                    "root": str(root_path),
                    "running_root": str(running_path),
                    "started_at": started_at,
                    "finished_at": None,
                    "log_path": str(store.worker_log_path),
                    "execute_agent_sdk": execute_agent_sdk,
                    "author_keys": author_keys,
                    "limit_per_author": limit_per_author,
                    "max_batches": max_batches,
                    "concurrency": concurrency,
                }
            )
            result = run_guru_pipeline(
                root_path,
                running_root=running_path,
                author_keys=author_keys,
                force=force,
                limit_per_author=limit_per_author,
                discover=discover,
                execute_agent_sdk=execute_agent_sdk,
                model=model,
                max_batches=max_batches,
                concurrency=concurrency,
                max_span_chars=max_span_chars,
            )
            store.write_worker_state(
                {
                    "status": "succeeded",
                    "pid": pid,
                    "root": str(root_path),
                    "running_root": str(running_path),
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "log_path": str(store.worker_log_path),
                    "concurrency": concurrency,
                    "result": result,
                }
            )
            return result
    except LockHeldError as exc:
        store.write_worker_state(
            {
                "status": "failed",
                "pid": pid,
                "root": str(root_path),
                "running_root": str(running_path),
                "finished_at": utc_now(),
                "error": str(exc),
                "log_path": str(store.worker_log_path),
            }
        )
        raise
    except Exception as exc:
        store.write_worker_state(
            {
                "status": "failed",
                "pid": pid,
                "root": str(root_path),
                "running_root": str(running_path),
                "finished_at": utc_now(),
                "error": str(exc),
                "log_path": str(store.worker_log_path),
            }
        )
        raise
    finally:
        store.clear_worker_pid(pid)


def guru_background_status(running_root: Path | str | None = None) -> dict[str, Any]:
    running_path = guru_running_root(running_root)
    store = GuruRunStore(running_path)
    pid = store.worker_pid()
    state = store.worker_state() or {}
    return {
        "worker_running": pid is not None and is_pid_running(pid),
        "pid": pid,
        "state": state,
        "log_path": str(store.worker_log_path),
        "state_path": str(store.worker_state_path),
    }
