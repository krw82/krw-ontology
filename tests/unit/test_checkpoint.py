"""Tests for CheckpointManager."""

from __future__ import annotations

from pathlib import Path

from krw_ontology.pipeline.checkpoint import CheckpointManager, PIPELINE_VERSION


class TestSaveLoad:
    def test_save_and_load(self, tmp_path: Path):
        cp_path = tmp_path / ".checkpoint.json"
        mgr = CheckpointManager(cp_path)
        mgr.mark_complete("resolve_ticker", metadata={"cik": "0000320193"})

        # Reload from disk
        mgr2 = CheckpointManager(cp_path)
        assert mgr2.is_stage_complete("resolve_ticker")
        assert mgr2.state["stage_metadata"]["resolve_ticker"]["cik"] == "0000320193"

    def test_save_creates_file(self, tmp_path: Path):
        cp_path = tmp_path / "subdir" / ".checkpoint.json"
        mgr = CheckpointManager(cp_path)
        mgr.mark_complete("resolve_ticker")
        assert cp_path.exists()


class TestStageCompletion:
    def test_is_stage_complete_false_initially(self, tmp_path: Path):
        cp_path = tmp_path / ".checkpoint.json"
        mgr = CheckpointManager(cp_path)
        assert not mgr.is_stage_complete("resolve_ticker")

    def test_mark_complete_changes_state(self, tmp_path: Path):
        cp_path = tmp_path / ".checkpoint.json"
        mgr = CheckpointManager(cp_path)
        mgr.mark_complete("resolve_ticker")
        assert mgr.is_stage_complete("resolve_ticker")

    def test_mark_complete_records_timestamp(self, tmp_path: Path):
        cp_path = tmp_path / ".checkpoint.json"
        mgr = CheckpointManager(cp_path)
        mgr.mark_complete("resolve_ticker")
        ts = mgr.state["stage_timestamps"].get("resolve_ticker")
        assert ts is not None
        assert len(ts) > 0

    def test_mark_complete_with_metadata(self, tmp_path: Path):
        cp_path = tmp_path / ".checkpoint.json"
        mgr = CheckpointManager(cp_path)
        mgr.mark_complete("discover_source_document", metadata={"accession_number": "0001"})
        meta = mgr.state["stage_metadata"]["discover_source_document"]
        assert meta["accession_number"] == "0001"

    def test_multiple_stages(self, tmp_path: Path):
        cp_path = tmp_path / ".checkpoint.json"
        mgr = CheckpointManager(cp_path)
        mgr.mark_complete("resolve_ticker")
        mgr.mark_complete("discover_source_document")
        assert mgr.is_stage_complete("resolve_ticker")
        assert mgr.is_stage_complete("discover_source_document")
        assert len(mgr.state["completed_stages"]) == 2


class TestEmptyCheckpoint:
    def test_default_state(self, tmp_path: Path):
        cp_path = tmp_path / ".checkpoint.json"
        mgr = CheckpointManager(cp_path)
        assert mgr.state["pipeline_version"] == PIPELINE_VERSION
        assert mgr.state["completed_stages"] == []
        assert mgr.state["current_stage"] == "resolve_ticker"
        assert mgr.state["stage_timestamps"] == {}
        assert mgr.state["stage_metadata"] == {}

    def test_nonexistent_path_returns_defaults(self, tmp_path: Path):
        cp_path = tmp_path / "nonexistent" / ".checkpoint.json"
        mgr = CheckpointManager(cp_path)
        assert mgr.state["completed_stages"] == []

    def test_corrupted_file_returns_defaults(self, tmp_path: Path):
        cp_path = tmp_path / ".checkpoint.json"
        cp_path.write_text("not valid json{{{")
        mgr = CheckpointManager(cp_path)
        assert mgr.state["completed_stages"] == []
