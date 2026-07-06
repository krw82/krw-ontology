from __future__ import annotations

import json
from pathlib import Path

from krw_ontology.guru import background as guru_background
from krw_ontology.guru.background import (
    GuruRunStore,
    guru_background_status,
    run_background_worker,
    start_background_guru_run,
)


def test_start_background_guru_run_launches_all_authors_by_default(tmp_path: Path, monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 45678

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(guru_background.subprocess, "Popen", fake_popen)

    payload = start_background_guru_run(
        tmp_path / "guru",
        running_root=tmp_path / "guru-running",
    )

    assert payload["pid"] == 45678
    assert payload["status"] == "running"
    assert "run-worker" in captured["command"]
    assert "--authors" not in captured["command"]
    assert "--limit-per-author" not in captured["command"]
    assert captured["kwargs"]["start_new_session"] is True
    assert Path(payload["log_path"]).exists()
    state = json.loads(Path(payload["state_path"]).read_text(encoding="utf-8"))
    assert state["pid"] == 45678


def test_run_background_worker_writes_succeeded_state(tmp_path: Path, monkeypatch):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"

    def fake_run_guru_pipeline(*args, **kwargs):
        return {
            "root": str(root),
            "running_root": str(running_root),
            "initialized": True,
            "collection_started": True,
            "extraction_started": False,
            "execution_mode": "dry_run",
            "agent_sdk_called": False,
            "raw_documents": 10,
            "raw_errors": 0,
            "parsed_documents": 10,
            "parsed_errors": 0,
            "extraction_batches": 2,
            "files": {},
        }

    monkeypatch.setattr(guru_background, "run_guru_pipeline", fake_run_guru_pipeline)

    result = run_background_worker(root, running_root=running_root)

    assert result["raw_documents"] == 10
    store = GuruRunStore(running_root)
    state = store.worker_state()
    assert state is not None
    assert state["status"] == "succeeded"
    assert state["result"]["extraction_batches"] == 2
    assert store.worker_pid() is None


def test_guru_background_status_reports_state(tmp_path: Path):
    running_root = tmp_path / "guru-running"
    store = GuruRunStore(running_root)
    store.write_worker_state({"status": "succeeded", "pid": 1})

    status = guru_background_status(running_root)

    assert status["worker_running"] is False
    assert status["state"]["status"] == "succeeded"
    assert status["log_path"].endswith(".krw_pipeline/guru/logs/worker.log")
