"""Checkpoint manager for pipeline resume support (Section 9)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from krw_ontology.utils.io import atomic_write_json

PIPELINE_VERSION = "0.1.0"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointManager:
    def __init__(self, checkpoint_path: Path) -> None:
        self.path = checkpoint_path
        self.state = self.load()

    def is_stage_complete(self, stage: str) -> bool:
        return stage in self.state["completed_stages"]

    def mark_complete(self, stage: str, metadata: dict | None = None) -> None:
        if stage not in self.state["completed_stages"]:
            self.state["completed_stages"].append(stage)
        self.state["current_stage"] = stage
        self.state["stage_timestamps"][stage] = _now_utc()
        if metadata:
            self.state["stage_metadata"][stage] = metadata
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, self.state)

    def load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "pipeline_version": PIPELINE_VERSION,
            "completed_stages": [],
            "current_stage": "resolve_ticker",
            "stage_timestamps": {},
            "stage_metadata": {},
        }
