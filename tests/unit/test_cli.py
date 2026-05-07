"""Tests for CLI commands: validate and build-report."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import krw_ontology.agent_index as agent_index
import krw_ontology.pipeline.orchestrator as orchestrator
import krw_ontology.pipeline.research_plan as research_plan
import krw_ontology.pipeline.stages.build_company_context as company_context_stage
from krw_ontology.cli.main import app
from krw_ontology.pipeline.research_plan import ResearchFilingTarget

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_ontology_root_env(monkeypatch):
    monkeypatch.delenv("KRW_ONTOLOGY_ROOT", raising=False)


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


class TestBuildResearchPipelineCommand:
    def test_build_research_pipeline_runs_targets_and_rebuilds_index(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        pipeline_calls = []
        index_calls = []

        def fake_discover(ticker, *, years, config):
            return [
                ResearchFilingTarget(
                    ticker=ticker,
                    document_type="10-Q",
                    period="FY2024Q1",
                    accession_number="q1",
                    filing_date="2024-05-01",
                    report_date="2024-03-31",
                ),
                ResearchFilingTarget(
                    ticker=ticker,
                    document_type="10-K",
                    period="FY2024",
                    accession_number="k",
                    filing_date="2025-02-01",
                    report_date="2024-12-31",
                ),
            ]

        def fake_run_pipeline(**kwargs):
            pipeline_calls.append(kwargs)

        def fake_build_agent_index(root, *, index_path=None, force=True):
            index_calls.append((root, index_path, force))
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 2, "objects": 4, "edges": 1, "quality_events": 0},
            }

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            ["build-research-pipeline", "aapl", "msft", "--root", str(tmp_path)],
        )

        assert result.exit_code == 0
        assert [
            (call["ticker"], call["document_type"], call["period"])
            for call in pipeline_calls
        ] == [
            ("AAPL", "10-Q", "FY2024Q1"),
            ("AAPL", "10-K", "FY2024"),
            ("MSFT", "10-Q", "FY2024Q1"),
            ("MSFT", "10-K", "FY2024"),
        ]
        assert all(call["latest"] is False for call in pipeline_calls)
        assert all(call["output_dir"] == tmp_path for call in pipeline_calls)
        assert index_calls == [(tmp_path, None, True)]
        assert "Agent index built" in result.output

    def test_build_research_pipeline_rebuilds_index_after_failure(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        index_calls = []

        def fake_discover(ticker, *, years, config):
            return [
                ResearchFilingTarget(
                    ticker=ticker,
                    document_type="10-K",
                    period="FY2024",
                    accession_number="k",
                    filing_date="2025-02-01",
                    report_date="2024-12-31",
                )
            ]

        def fake_run_pipeline(**kwargs):
            raise RuntimeError("boom")

        def fake_build_agent_index(root, *, index_path=None, force=True):
            index_calls.append((root, index_path, force))
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 0, "objects": 0, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            ["build-research-pipeline", "aapl", "--root", str(tmp_path)],
        )

        assert result.exit_code == 1
        assert index_calls == [(tmp_path, None, True)]
        assert "Rebuilding agent index" in result.output
        assert "Failures:" in result.output

    def test_build_research_pipeline_publishes_each_completed_ticker(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        running = tmp_path / "running"
        stable = tmp_path / "stable"
        events = []

        def fake_discover(ticker, *, years, config):
            return [
                ResearchFilingTarget(
                    ticker=ticker,
                    document_type="10-K",
                    period="FY2024",
                    accession_number="k",
                    filing_date="2025-02-01",
                    report_date="2024-12-31",
                )
            ]

        def fake_run_pipeline(**kwargs):
            events.append(("pipeline", kwargs["ticker"]))
            ticker_dir = kwargs["output_dir"] / "companies" / kwargs["ticker"]
            ticker_dir.mkdir(parents=True, exist_ok=True)
            (ticker_dir / "artifact.txt").write_text(kwargs["ticker"])

        def fake_build_company_context(root, ticker):
            events.append(("context", ticker))
            return {
                "artifact_index_path": root / "companies" / ticker / "company_context" / "artifact_index.json",
                "counts": {"company_business_profiles": 1},
            }

        def fake_build_agent_index(root, *, index_path=None, force=True):
            events.append(("index", root))
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 1, "objects": 2, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            [
                "build-research-pipeline",
                "aapl",
                "msft",
                "--root",
                str(running),
                "--publish-root",
                str(stable),
            ],
        )

        assert result.exit_code == 0
        assert (stable / "companies" / "AAPL" / "artifact.txt").read_text() == "AAPL"
        assert (stable / "companies" / "MSFT" / "artifact.txt").read_text() == "MSFT"
        assert events == [
            ("pipeline", "AAPL"),
            ("context", "AAPL"),
            ("index", stable.resolve()),
            ("pipeline", "MSFT"),
            ("context", "MSFT"),
            ("index", stable.resolve()),
            ("index", running.resolve()),
        ]
        assert "Published AAPL" in result.output
        assert "Published MSFT" in result.output

    def test_build_research_pipeline_skips_publish_for_failed_ticker(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        running = tmp_path / "running"
        stable = tmp_path / "stable"
        index_calls = []

        def fake_discover(ticker, *, years, config):
            return [
                ResearchFilingTarget(
                    ticker=ticker,
                    document_type="10-K",
                    period="FY2024",
                    accession_number="k",
                    filing_date="2025-02-01",
                    report_date="2024-12-31",
                )
            ]

        def fake_run_pipeline(**kwargs):
            ticker_dir = kwargs["output_dir"] / "companies" / kwargs["ticker"]
            ticker_dir.mkdir(parents=True, exist_ok=True)
            (ticker_dir / "artifact.txt").write_text(kwargs["ticker"])
            if kwargs["ticker"] == "AAPL":
                raise RuntimeError("boom")

        def fake_build_company_context(root, ticker):
            return {
                "artifact_index_path": root / "companies" / ticker / "company_context" / "artifact_index.json",
                "counts": {"company_business_profiles": 1},
            }

        def fake_build_agent_index(root, *, index_path=None, force=True):
            index_calls.append(root)
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 1, "objects": 2, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            [
                "build-research-pipeline",
                "aapl",
                "msft",
                "--root",
                str(running),
                "--publish-root",
                str(stable),
                "--continue-on-error",
            ],
        )

        assert result.exit_code == 1
        assert not (stable / "companies" / "AAPL").exists()
        assert (stable / "companies" / "MSFT" / "artifact.txt").read_text() == "MSFT"
        assert index_calls == [stable.resolve(), running.resolve()]
        assert "Skipping publish for AAPL" in result.output
        assert "Published MSFT" in result.output


class TestPublishTickerCommand:
    def test_publish_ticker_replaces_ticker_tree_and_rebuilds_stable_index(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        running = tmp_path / "running"
        stable = tmp_path / "stable"
        source = running / "companies" / "CVX" / "ontology"
        target = stable / "companies" / "CVX"
        source.mkdir(parents=True)
        target.mkdir(parents=True)
        (source / "new.txt").write_text("new")
        (target / "old.txt").write_text("old")
        index_calls = []

        def fake_build_agent_index(root, *, index_path=None, force=True):
            index_calls.append((root, index_path, force))
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 1, "objects": 2, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            [
                "publish-ticker",
                "cvx",
                "--from-root",
                str(running),
                "--to-root",
                str(stable),
            ],
        )

        assert result.exit_code == 0
        assert (stable / "companies" / "CVX" / "ontology" / "new.txt").read_text() == "new"
        assert not (stable / "companies" / "CVX" / "old.txt").exists()
        assert index_calls == [(stable.resolve(), None, True)]
        assert "Published CVX" in result.output

    def test_publish_ticker_can_skip_index_rebuild(self, tmp_path: Path, monkeypatch):
        running = tmp_path / "running"
        stable = tmp_path / "stable"
        (running / "companies" / "OXY").mkdir(parents=True)
        index_calls = []

        def fake_build_agent_index(root, *, index_path=None, force=True):
            index_calls.append((root, index_path, force))
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 0, "objects": 0, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            [
                "publish-ticker",
                "oxy",
                "--from-root",
                str(running),
                "--to-root",
                str(stable),
                "--no-rebuild-agent-index",
            ],
        )

        assert result.exit_code == 0
        assert (stable / "companies" / "OXY").is_dir()
        assert index_calls == []

    def test_publish_ticker_dry_run_does_not_copy_or_rebuild_index(self, tmp_path: Path, monkeypatch):
        running = tmp_path / "running"
        stable = tmp_path / "stable"
        (running / "companies" / "LNG").mkdir(parents=True)
        index_calls = []

        def fake_build_agent_index(root, *, index_path=None, force=True):
            index_calls.append((root, index_path, force))
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 0, "objects": 0, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            [
                "publish-ticker",
                "lng",
                "--from-root",
                str(running),
                "--to-root",
                str(stable),
                "--dry-run",
            ],
        )

        assert result.exit_code == 0
        assert not (stable / "companies" / "LNG").exists()
        assert index_calls == []
        assert "Dry run complete" in result.output

    def test_publish_ticker_fails_when_source_ticker_missing(self, tmp_path: Path):
        result = runner.invoke(
            app,
            [
                "publish-ticker",
                "cnq",
                "--from-root",
                str(tmp_path / "running"),
                "--to-root",
                str(tmp_path / "stable"),
            ],
        )

        assert result.exit_code == 1
        assert "Source ticker directory not found" in result.output


class TestHelpOutput:
    def test_help_shows_all_commands(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "init-workspace" in result.output
        assert "build-evidence-ontology" in result.output
        assert "e2e-matrix" in result.output
        assert "build-research-pipeline" in result.output
        assert "publish-ticker" in result.output
        assert "validate" in result.output
        assert "build-report" in result.output
        assert "build-agent-index" in result.output
