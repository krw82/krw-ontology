"""Tests for CLI commands: validate and build-report."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import krw_ontology.pipeline.orchestrator as orchestrator
from krw_ontology.cli.main import app

runner = CliRunner()


class TestValidateCommand:
    def test_validate_no_data(self, tmp_path: Path, monkeypatch):
        """validate should report missing data when ontology dir doesn't exist."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["validate", "AAPL"])
        assert result.exit_code == 1
        assert "No ontology data found" in result.output

    def test_validate_with_data(self, tmp_path: Path, monkeypatch):
        """validate should run and report results when data exists."""
        ontology_dir = (
            tmp_path / "companies" / "AAPL" / "ontology" / "10K" / "FY2024"
        )
        ontology_dir.mkdir(parents=True)
        (ontology_dir / "spans.jsonl").write_text("")
        (ontology_dir / "evidence_quotes.jsonl").write_text("")
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["validate", "AAPL"])
        assert result.exit_code == 0
        assert "Validation complete" in result.output


class TestBuildReportCommand:
    def test_build_report_no_data(self, tmp_path: Path, monkeypatch):
        """build-report should report missing data when ontology dir doesn't exist."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["build-report", "AAPL"])
        assert result.exit_code == 1
        assert "No ontology data found" in result.output

    def test_build_report_with_data(self, tmp_path: Path, monkeypatch):
        """build-report should generate report files."""
        ontology_dir = (
            tmp_path / "companies" / "AAPL" / "ontology" / "10K" / "FY2024"
        )
        sources_dir = (
            tmp_path / "companies" / "AAPL" / "sources" / "10K" / "FY2024"
        )
        ontology_dir.mkdir(parents=True)
        sources_dir.mkdir(parents=True)
        (ontology_dir / "spans.jsonl").write_text("")
        (ontology_dir / "evidence_quotes.jsonl").write_text("")
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["build-report", "AAPL"])
        assert result.exit_code == 0
        assert "Report generated" in result.output
        report = ontology_dir / "graph_report.md"
        audit = ontology_dir / "audit_report.md"
        assert report.exists()
        assert audit.exists()
        assert "AAPL" in report.read_text()
        assert "FY2024" in report.read_text()


class TestE2EMatrixCommand:
    def test_e2e_matrix_defaults_to_four_tickers(self, tmp_path: Path, monkeypatch):
        calls = []

        def fake_run_pipeline(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)

        result = runner.invoke(app, ["e2e-matrix", "--output-dir", str(tmp_path)])

        assert result.exit_code == 0
        assert [call["ticker"] for call in calls] == ["AAPL", "NVDA", "JPM", "XOM"]
        assert all(call["latest"] is True for call in calls)
        assert all(call["force"] is True for call in calls)
        assert all(call["output_dir"] == tmp_path for call in calls)
        assert f"OUTPUT_ROOT={tmp_path}" in result.output

    def test_e2e_matrix_accepts_custom_tickers(self, tmp_path: Path, monkeypatch):
        calls = []

        def fake_run_pipeline(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)

        result = runner.invoke(app, ["e2e-matrix", "msft", "unh", "--output-dir", str(tmp_path)])

        assert result.exit_code == 0
        assert [call["ticker"] for call in calls] == ["MSFT", "UNH"]

    def test_e2e_matrix_accepts_10q_document_type(self, tmp_path: Path, monkeypatch):
        calls = []

        def fake_run_pipeline(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)

        result = runner.invoke(
            app,
            ["e2e-matrix", "aapl", "--document-type", "10-Q", "--output-dir", str(tmp_path)],
        )

        assert result.exit_code == 0
        assert calls[0]["document_type"] == "10-Q"

    def test_e2e_matrix_stops_on_first_failure(self, tmp_path: Path, monkeypatch):
        calls = []

        def fake_run_pipeline(**kwargs):
            calls.append(kwargs["ticker"])
            if kwargs["ticker"] == "NVDA":
                raise RuntimeError("boom")

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)

        result = runner.invoke(app, ["e2e-matrix", "--output-dir", str(tmp_path)])

        assert result.exit_code == 1
        assert calls == ["AAPL", "NVDA"]
        assert "FAILED ticker=NVDA" in result.output

    def test_e2e_matrix_can_continue_on_error(self, tmp_path: Path, monkeypatch):
        calls = []

        def fake_run_pipeline(**kwargs):
            calls.append(kwargs["ticker"])
            if kwargs["ticker"] == "NVDA":
                raise RuntimeError("boom")

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)

        result = runner.invoke(
            app,
            ["e2e-matrix", "--continue-on-error", "--output-dir", str(tmp_path)],
        )

        assert result.exit_code == 1
        assert calls == ["AAPL", "NVDA", "JPM", "XOM"]
        assert "Failures:" in result.output


class TestHelpOutput:
    def test_help_shows_all_commands(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "init-workspace" in result.output
        assert "build-evidence-ontology" in result.output
        assert "e2e-matrix" in result.output
        assert "validate" in result.output
        assert "build-report" in result.output
