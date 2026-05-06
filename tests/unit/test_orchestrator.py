"""Tests for orchestrator period override and checkpoint sync."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from krw_ontology.config.settings import PipelineConfig
from krw_ontology.errors import PipelineStageError
from krw_ontology.pipeline.orchestrator import (
    PIPELINE_STAGES,
    _execute_stage,
    _is_code_stage,
)


def _make_ctx(tmp_path: Path, **overrides) -> dict:
    """Build a minimal ctx dict for _execute_stage tests."""
    ctx: dict = {
        "ticker": "AAPL",
        "document_type": "10-K",
        "doc_type_key": "10K",
        "config": PipelineConfig(),
        "force": False,
        "latest": False,
        "base_dir": tmp_path,
        "period": None,
        "cik": "0000320193",
    }
    ctx.update(overrides)
    return ctx


class TestPeriodOverride:
    def test_explicit_period_stored_in_ctx(self):
        ctx = _make_ctx(Path("/tmp"), period="FY2025")
        assert ctx["period"] == "FY2025"

    def test_explicit_period_overrides_report_date(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, period="FY2025")

        with patch("krw_ontology.pipeline.orchestrator.discover_source") as mock_ds, \
             patch("krw_ontology.pipeline.orchestrator._write_pipeline_config"):
            mock_ds.return_value = {
                "accession_number": "0001",
                "filing_date": "2024-11-01",
                "source_url": "https://example.com/filing.html",
                "report_date": "2024-09-28",
            }
            _execute_stage("discover_source_document", ctx)

            mock_ds.assert_called_once_with(
                "0000320193",
                "10-K",
                False,
                ctx["config"],
                period="FY2025",
            )

        assert ctx["period"] == "FY2025"

    def test_period_derived_from_report_date(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, period=None)

        with patch("krw_ontology.pipeline.orchestrator.discover_source") as mock_ds, \
             patch("krw_ontology.pipeline.orchestrator._write_pipeline_config"):
            mock_ds.return_value = {
                "accession_number": "0001",
                "filing_date": "2024-11-01",
                "source_url": "https://example.com/filing.html",
                "report_date": "2024-09-28",
            }
            _execute_stage("discover_source_document", ctx)

        assert ctx["period"] == "FY2024"

    def test_period_derived_from_filing_date(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, period=None)

        with patch("krw_ontology.pipeline.orchestrator.discover_source") as mock_ds, \
             patch("krw_ontology.pipeline.orchestrator._write_pipeline_config"):
            mock_ds.return_value = {
                "accession_number": "0001",
                "filing_date": "2023-10-27",
                "source_url": "https://example.com/filing.html",
                "report_date": None,
            }
            _execute_stage("discover_source_document", ctx)

        assert ctx["period"] == "FY2023"

    def test_ten_q_period_and_paths_use_10q_key(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            document_type="10-Q",
            doc_type_key="10Q",
            period=None,
        )

        with patch("krw_ontology.pipeline.orchestrator.discover_source") as mock_ds, \
             patch("krw_ontology.pipeline.orchestrator._write_pipeline_config"):
            mock_ds.return_value = {
                "accession_number": "0001",
                "filing_date": "2025-08-01",
                "source_url": "https://example.com/filing.html",
                "report_date": "2025-06-30",
            }
            _execute_stage("discover_source_document", ctx)

        assert ctx["period"] == "FY2025Q2"
        assert ctx["source_document_id"] == "source:AAPL:FY2025Q2:10Q"
        assert ctx["ontology_dir"] == (
            tmp_path / "companies" / "AAPL" / "ontology" / "10Q" / "FY2025Q2"
        )


class TestCheckpointSync:
    def test_checkpoint_path_syncs_from_ctx(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, period=None)

        with patch("krw_ontology.pipeline.orchestrator.discover_source") as mock_ds, \
             patch("krw_ontology.pipeline.orchestrator._write_pipeline_config"):
            mock_ds.return_value = {
                "accession_number": "0001",
                "filing_date": "2024-11-01",
                "source_url": "https://example.com/filing.html",
                "report_date": "2024-09-28",
            }
            _execute_stage("discover_source_document", ctx)

        expected_cp = tmp_path / "companies" / "AAPL" / "ontology" / "10K" / "FY2024" / ".checkpoint.json"
        assert ctx.get("checkpoint_path") == expected_cp
        assert ctx["checkpoint_path"].exists()


class TestSectionQualityGate:
    def test_extract_sections_continues_on_quality_fail_by_default(self, tmp_path: Path):
        clean_md = tmp_path / "clean.md"
        raw_html = tmp_path / "raw.html"
        clean_md.write_text("No useful sections.\n")
        raw_html.write_text("<html></html>")
        ctx = _make_ctx(
            tmp_path,
            period="FY2025",
            clean_md_path=clean_md,
            raw_html_path=raw_html,
            ontology_dir=tmp_path / "ontology",
        )

        with patch("krw_ontology.pipeline.orchestrator.extract_sections") as mock_extract:
            mock_extract.return_value = {
                "sections": [],
                "section_quality": {
                    "status": "fail",
                    "fail_reasons": ["no_sections_detected"],
                },
            }

            _execute_stage("extract_sections", ctx)

        assert ctx["sections"] == []
        assert ctx["section_quality"]["status"] == "fail"
        assert ctx["clean_md_text"] == "No useful sections.\n"

    def test_extract_sections_fails_fast_when_quality_gate_enabled(self, tmp_path: Path):
        clean_md = tmp_path / "clean.md"
        raw_html = tmp_path / "raw.html"
        clean_md.write_text("No useful sections.\n")
        raw_html.write_text("<html></html>")
        config = PipelineConfig(fail_on_section_quality=True)
        ctx = _make_ctx(
            tmp_path,
            period="FY2025",
            config=config,
            clean_md_path=clean_md,
            raw_html_path=raw_html,
            ontology_dir=tmp_path / "ontology",
        )

        with patch("krw_ontology.pipeline.orchestrator.extract_sections") as mock_extract:
            mock_extract.return_value = {
                "sections": [],
                "section_quality": {
                    "status": "fail",
                    "fail_reasons": ["multiple_core_sections_missing"],
                },
            }

            with pytest.raises(PipelineStageError, match="multiple_core_sections_missing"):
                _execute_stage("extract_sections", ctx)

        assert ctx["section_quality"]["status"] == "fail"


class TestPipelineStages:
    def test_all_stages_are_code_stages(self):
        for stage in PIPELINE_STAGES:
            assert _is_code_stage(stage), f"{stage} not in _is_code_stage"

    def test_seventeen_stages(self):
        assert len(PIPELINE_STAGES) == 17

    def test_numeric_evidence_runs_after_quotes_before_claims(self):
        assert PIPELINE_STAGES.index("extract_evidence_quotes") < PIPELINE_STAGES.index("build_numeric_evidence")
        assert PIPELINE_STAGES.index("build_numeric_evidence") < PIPELINE_STAGES.index("extract_research_claims")

    def test_validation_runs_before_and_after_edge_generation(self):
        assert PIPELINE_STAGES.index("validate_ontology") < PIPELINE_STAGES.index("generate_edges")
        assert PIPELINE_STAGES.index("generate_edges") < PIPELINE_STAGES.index("validate_edges")
