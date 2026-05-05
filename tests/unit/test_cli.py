"""Tests for CLI commands: validate and build-report."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

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


class TestHelpOutput:
    def test_help_shows_all_commands(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "init-workspace" in result.output
        assert "build-evidence-ontology" in result.output
        assert "validate" in result.output
        assert "build-report" in result.output
