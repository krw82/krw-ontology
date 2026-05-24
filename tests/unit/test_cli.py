"""Tests for CLI commands: validate and build-report."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

import krw_ontology.agent_index as agent_index
import krw_ontology.cli.main as cli_main
import krw_ontology.pipeline.orchestrator as orchestrator
import krw_ontology.pipeline.queue as pipeline_queue
import krw_ontology.pipeline.research_plan as research_plan
import krw_ontology.pipeline.stages.build_company_context as company_context_stage
from krw_ontology.cli.main import app
from krw_ontology.pipeline.research_plan import ResearchFilingTarget

runner = CliRunner()


def _write_company_context_artifact(root: Path, ticker: str) -> Path:
    artifact_payload = (
        '{"artifacts":[],"counts":{"company_business_profiles":1,'
        '"trend_observations":1,"change_events":1,"temporal_links":1,"edges":1}}'
    )
    artifact_index_path = root / "companies" / ticker / "context" / "artifact_index.json"
    artifact_index_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_index_path.write_text(artifact_payload, encoding="utf-8")
    compatibility_path = root / "companies" / ticker / "company_context" / "artifact_index.json"
    compatibility_path.parent.mkdir(parents=True, exist_ok=True)
    compatibility_path.write_text(artifact_payload, encoding="utf-8")
    return artifact_index_path


@pytest.fixture(autouse=True)
def _clear_ontology_root_env(monkeypatch, tmp_path):
    monkeypatch.delenv("KRW_ONTOLOGY_ROOT", raising=False)
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "cli-config.json"))


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
                "artifact_index_path": _write_company_context_artifact(root, ticker),
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
                "artifact_index_path": _write_company_context_artifact(root, ticker),
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


class TestUpdateTickerCommand:
    def test_update_ticker_builds_context_publishes_and_rebuilds_stable_index(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        running = tmp_path / "running"
        stable = tmp_path / "stable"
        events = []

        def fake_run_pipeline(**kwargs):
            events.append(("pipeline", kwargs))
            ticker_dir = kwargs["output_dir"] / "companies" / kwargs["ticker"]
            ticker_dir.mkdir(parents=True, exist_ok=True)
            (ticker_dir / "artifact.txt").write_text(kwargs["period"])

        def fake_build_company_context(root, ticker):
            events.append(("context", root, ticker))
            return {
                "artifact_index_path": _write_company_context_artifact(root, ticker),
                "counts": {"company_business_profiles": 1},
            }

        def fake_build_agent_index(root, *, index_path=None, force=True):
            events.append(("index", root, index_path, force))
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 1, "objects": 2, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            [
                "update-ticker",
                "cvx",
                "--document-type",
                "10-Q",
                "--period",
                "FY2026Q1",
                "--root",
                str(running),
                "--publish-root",
                str(stable),
            ],
        )

        assert result.exit_code == 0
        pipeline_kwargs = events[0][1]
        assert pipeline_kwargs["ticker"] == "CVX"
        assert pipeline_kwargs["document_type"] == "10-Q"
        assert pipeline_kwargs["period"] == "FY2026Q1"
        assert pipeline_kwargs["latest"] is False
        assert pipeline_kwargs["output_dir"] == running.resolve()
        assert events[1] == ("context", running.resolve(), "CVX")
        assert events[2] == ("index", stable.resolve(), None, True)
        assert (stable / "companies" / "CVX" / "artifact.txt").read_text() == "FY2026Q1"
        assert "Published CVX" in result.output

    def test_update_ticker_accepts_multiple_periods_and_publishes_once(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        running = tmp_path / "running"
        stable = tmp_path / "stable"
        events = []

        def fake_run_pipeline(**kwargs):
            events.append(("pipeline", kwargs["period"]))
            ticker_dir = kwargs["output_dir"] / "companies" / kwargs["ticker"]
            ticker_dir.mkdir(parents=True, exist_ok=True)
            (ticker_dir / f"{kwargs['period']}.txt").write_text(kwargs["period"])

        def fake_build_company_context(root, ticker):
            events.append(("context", root, ticker))
            return {
                "artifact_index_path": _write_company_context_artifact(root, ticker),
                "counts": {"company_business_profiles": 1},
            }

        def fake_build_agent_index(root, *, index_path=None, force=True):
            events.append(("index", root, index_path, force))
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 2, "objects": 4, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            [
                "update-ticker",
                "cvx",
                "--document-type",
                "10-Q",
                "--period",
                "FY2026Q1",
                "--period",
                "FY2026Q2",
                "--root",
                str(running),
                "--publish-root",
                str(stable),
            ],
        )

        assert result.exit_code == 0
        assert events == [
            ("pipeline", "FY2026Q1"),
            ("pipeline", "FY2026Q2"),
            ("context", running.resolve(), "CVX"),
            ("index", stable.resolve(), None, True),
        ]
        assert (stable / "companies" / "CVX" / "FY2026Q1.txt").read_text() == "FY2026Q1"
        assert (stable / "companies" / "CVX" / "FY2026Q2.txt").read_text() == "FY2026Q2"
        assert "Filing pipeline complete for CVX 10-Q FY2026Q1" in result.output
        assert "Filing pipeline complete for CVX 10-Q FY2026Q2" in result.output

    def test_update_ticker_latest_builds_latest_without_period(self, tmp_path: Path, monkeypatch):
        running = tmp_path / "running"
        calls = []

        def fake_run_pipeline(**kwargs):
            calls.append(kwargs)
            (kwargs["output_dir"] / "companies" / kwargs["ticker"]).mkdir(parents=True, exist_ok=True)

        def fake_build_company_context(root, ticker):
            return {
                "artifact_index_path": _write_company_context_artifact(root, ticker),
                "counts": {},
            }

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)

        result = runner.invoke(
            app,
            [
                "update-ticker",
                "oxy",
                "--document-type",
                "10-K",
                "--latest",
                "--root",
                str(running),
                "--no-publish",
            ],
        )

        assert result.exit_code == 0
        assert calls[0]["ticker"] == "OXY"
        assert calls[0]["document_type"] == "10-K"
        assert calls[0]["latest"] is True
        assert calls[0]["period"] is None
        assert "publish skipped" in result.output

    def test_update_ticker_requires_period_or_latest(self, tmp_path: Path):
        result = runner.invoke(
            app,
            [
                "update-ticker",
                "cvx",
                "--root",
                str(tmp_path / "running"),
                "--publish-root",
                str(tmp_path / "stable"),
            ],
        )

        assert result.exit_code == 1
        assert "Specify at least one --period" in result.output

    def test_update_ticker_rejects_latest_with_period(self, tmp_path: Path):
        result = runner.invoke(
            app,
            [
                "update-ticker",
                "cvx",
                "--latest",
                "--period",
                "FY2026Q1",
                "--root",
                str(tmp_path / "running"),
                "--publish-root",
                str(tmp_path / "stable"),
            ],
        )

        assert result.exit_code == 1
        assert "Use either --latest or --period" in result.output

    def test_update_ticker_requires_publish_root_unless_no_publish(self, tmp_path: Path):
        result = runner.invoke(
            app,
            [
                "update-ticker",
                "cvx",
                "--period",
                "FY2026Q1",
                "--root",
                str(tmp_path / "running"),
            ],
        )

        assert result.exit_code == 1
        assert "Specify --publish-root" in result.output

    def test_update_ticker_does_not_publish_when_pipeline_fails(self, tmp_path: Path, monkeypatch):
        running = tmp_path / "running"
        stable = tmp_path / "stable"
        index_calls = []

        def fake_run_pipeline(**kwargs):
            ticker_dir = kwargs["output_dir"] / "companies" / kwargs["ticker"]
            ticker_dir.mkdir(parents=True, exist_ok=True)
            (ticker_dir / "partial.txt").write_text("partial")
            raise RuntimeError("boom")

        def fake_build_agent_index(root, *, index_path=None, force=True):
            index_calls.append(root)
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 0, "objects": 0, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            [
                "update-ticker",
                "cvx",
                "--period",
                "FY2026Q1",
                "--root",
                str(running),
                "--publish-root",
                str(stable),
            ],
        )

        assert result.exit_code == 1
        assert not (stable / "companies" / "CVX").exists()
        assert index_calls == []
        assert "stage=filing_pipeline" in result.output


class TestConfigCommand:
    def test_config_set_show_and_unset(self, tmp_path: Path):
        running = tmp_path / "running"

        set_result = runner.invoke(app, ["config", "set", "running-root", str(running)])
        show_result = runner.invoke(app, ["config", "show"])
        unset_result = runner.invoke(app, ["config", "unset", "running-root"])
        final_show = runner.invoke(app, ["config", "show"])

        assert set_result.exit_code == 0
        assert show_result.exit_code == 0
        assert f"running-root: {running.resolve()}" in show_result.output
        assert unset_result.exit_code == 0
        assert final_show.exit_code == 0
        assert "running-root: <unset>" in final_show.output

    def test_config_set_keeps_prod_values_raw(self):
        set_result = runner.invoke(app, ["config", "set", "prod-host", "ubuntu@prod"])
        show_result = runner.invoke(app, ["config", "show"])

        assert set_result.exit_code == 0
        assert "Set prod-host=ubuntu@prod" in set_result.output
        assert show_result.exit_code == 0
        assert "prod-host: ubuntu@prod" in show_result.output


class TestProdCommand:
    def test_prod_configure_saves_settings(self):
        result = runner.invoke(
            app,
            [
                "prod",
                "configure",
                "--host",
                "ubuntu@prod",
                "--remote-root",
                "/srv/krw-ontology-data",
                "--reload-command",
                "sudo systemctl restart krw-ontology-mcp",
                "--health-url",
                "http://127.0.0.1:8000/health",
                "--keep-releases",
                "3",
            ],
        )
        show_result = runner.invoke(app, ["config", "show"])

        assert result.exit_code == 0
        assert show_result.exit_code == 0
        assert "prod-host: ubuntu@prod" in show_result.output
        assert "prod-root: /srv/krw-ontology-data" in show_result.output
        assert "prod-reload-command: sudo systemctl restart krw-ontology-mcp" in show_result.output
        assert "prod-health-url: http://127.0.0.1:8000/health" in show_result.output
        assert "prod-keep-releases: 3" in show_result.output

    def test_prod_publish_uploads_bundle_and_activates_release(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        stable = tmp_path / "stable"
        (stable / "companies" / "AAPL").mkdir(parents=True)
        (stable / "companies" / "AAPL" / "artifact.txt").write_text("ok")
        (stable / "indexes").mkdir()
        (stable / "indexes" / "agent_index.sqlite").write_text("sqlite")
        runner.invoke(
            app,
            [
                "prod",
                "configure",
                "--host",
                "ubuntu@prod",
                "--remote-root",
                "/srv/krw-ontology-data",
                "--reload-command",
                "sudo systemctl restart krw-ontology-mcp",
                "--health-url",
                "http://127.0.0.1:8000/health",
                "--keep-releases",
                "2",
            ],
        )
        run_calls = []

        class FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(command, *, input=None, text, capture_output):
            run_calls.append((command, input))
            return FakeCompleted()

        monkeypatch.setattr(cli_main, "_new_release_id", lambda: "20260515-000000")
        monkeypatch.setattr(
            cli_main,
            "_try_get_prod_status",
            lambda **kwargs: {
                "current_kind": "symlink",
                "current_target": "releases/old-release",
                "current_release": "old-release",
                "releases": ["old-release"],
            },
        )
        monkeypatch.setattr(cli_main.subprocess, "run", fake_run)

        result = runner.invoke(app, ["prod", "publish", "--root", str(stable)])

        assert result.exit_code == 0
        assert "Activated: releases/20260515-000000" in result.output
        assert [call[0][0] for call in run_calls] == ["ssh", "scp", "ssh"]
        assert "/srv/krw-ontology-data/incoming" in run_calls[0][0]
        assert run_calls[1][0][2] == "ubuntu@prod:/srv/krw-ontology-data/incoming/20260515-000000.tar.gz"
        activation_script = run_calls[2][1]
        assert "RELEASE_ID=20260515-000000" in activation_script
        assert "RELOAD_COMMAND='sudo systemctl restart krw-ontology-mcp'" in activation_script
        assert "HEALTH_URL=http://127.0.0.1:8000/health" in activation_script

    def test_prod_publish_dry_run_skips_subprocess(self, tmp_path: Path, monkeypatch):
        stable = tmp_path / "stable"
        stable.mkdir()
        runner.invoke(
            app,
            [
                "prod",
                "configure",
                "--host",
                "ubuntu@prod",
                "--remote-root",
                "/srv/krw-ontology-data",
            ],
        )
        run_calls = []
        monkeypatch.setattr(
            cli_main,
            "_try_get_prod_status",
            lambda **kwargs: {"current_kind": "missing", "releases": []},
        )
        monkeypatch.setattr(cli_main.subprocess, "run", lambda *args, **kwargs: run_calls.append(args))

        result = runner.invoke(app, ["prod", "publish", "--root", str(stable), "--dry-run"])

        assert result.exit_code == 0
        assert "Prod publish dry run" in result.output
        assert "No upload performed." in result.output
        assert run_calls == []

    def test_prod_status_shows_current_release(self, monkeypatch):
        runner.invoke(
            app,
            [
                "prod",
                "configure",
                "--host",
                "ubuntu@prod",
                "--remote-root",
                "/srv/krw-ontology-data",
            ],
        )

        class FakeCompleted:
            returncode = 0
            stderr = ""
            stdout = "\n".join(
                [
                    "remote_root=/srv/krw-ontology-data",
                    "current_kind=symlink",
                    "current_target=releases/20260516-090000",
                    "current_release=20260516-090000",
                    "index_present=yes",
                    "manifest_present=yes",
                    "release=20260516-090000",
                    "release=20260515-120000",
                    "",
                ]
            )

        monkeypatch.setattr(cli_main.subprocess, "run", lambda *args, **kwargs: FakeCompleted())

        result = runner.invoke(app, ["prod", "status"])

        assert result.exit_code == 0
        assert "Prod status" in result.output
        assert "Current: releases/20260516-090000" in result.output
        assert "Index: present" in result.output
        assert "20260515-120000" in result.output

    def test_prod_doctor_warns_on_pre_symlink_current(self, tmp_path: Path, monkeypatch):
        stable = tmp_path / "stable"
        stable.mkdir()
        runner.invoke(
            app,
            [
                "prod",
                "configure",
                "--host",
                "ubuntu@prod",
                "--remote-root",
                "/srv/krw-ontology-data",
            ],
        )
        calls = []

        class FakeCompleted:
            returncode = 0
            stderr = ""
            stdout = ""

        def fake_run(command, *, input=None, text, capture_output):
            calls.append((command, input))
            completed = FakeCompleted()
            if input and "required_commands_missing" in input:
                completed.stdout = "\n".join(
                    [
                        "required_commands_missing=",
                        "root_state=exists",
                        "root_writable=yes",
                        "parent_writable=unknown",
                        "current_kind=directory",
                        "current_target=/srv/krw-ontology-data/current",
                        "",
                    ]
                )
            return completed

        monkeypatch.setattr(cli_main.subprocess, "run", fake_run)

        result = runner.invoke(app, ["prod", "doctor", "--root", str(stable)])

        assert result.exit_code == 0
        assert "OK ssh connectivity" in result.output
        assert "WARN Remote current is a directory" in result.output
        assert "Prod doctor passed." in result.output
        assert [call[0][0] for call in calls] == ["ssh", "ssh"]


class TestQueueCommands:
    def test_queue_add_creates_ticker_jobs(self, tmp_path: Path):
        stable = tmp_path / "stable"

        result = runner.invoke(
            app,
            [
                "queue-add",
                "cvx",
                "xom",
                "--years",
                "2",
                "--root",
                str(tmp_path),
                "--publish-root",
                str(stable),
            ],
        )

        assert result.exit_code == 0
        jobs = pipeline_queue.PipelineQueue(tmp_path.resolve()).list_jobs()
        assert [job.ticker for job in jobs] == ["CVX", "XOM"]
        assert all(job.years == 2 for job in jobs)
        assert all(job.status == pipeline_queue.PENDING for job in jobs)
        assert all(job.publish_root == str(stable.resolve()) for job in jobs)
        assert "Added 2 job(s)" in result.output

    def test_queue_group_add_uses_configured_roots(self, tmp_path: Path):
        running = tmp_path / "running"
        stable = tmp_path / "stable"
        runner.invoke(app, ["config", "set", "running-root", str(running)])
        runner.invoke(app, ["config", "set", "publish-root", str(stable)])

        result = runner.invoke(app, ["queue", "add", "cvx", "--years", "2"])

        assert result.exit_code == 0
        jobs = pipeline_queue.PipelineQueue(running.resolve()).list_jobs()
        assert [job.ticker for job in jobs] == ["CVX"]
        assert jobs[0].publish_root == str(stable.resolve())
        assert "Queued CVX" in result.output

    def test_queue_add_skips_active_duplicate(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        first = store.add_job(
            "CVX",
            years=3,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )

        result = runner.invoke(
            app,
            ["queue-add", "cvx", "--root", str(tmp_path)],
        )

        assert result.exit_code == 0
        jobs = store.list_jobs()
        assert [job.job_id for job in jobs] == [first.job_id]
        assert "Skipped CVX" in result.output
        assert "Added 0 job(s)" in result.output

    def test_queue_update_creates_filing_update_job(self, tmp_path: Path):
        stable = tmp_path / "stable"

        result = runner.invoke(
            app,
            [
                "queue-update",
                "vg",
                "--document-type",
                "10-Q",
                "--period",
                "FY2026Q1",
                "--root",
                str(tmp_path),
                "--publish-root",
                str(stable),
            ],
        )

        assert result.exit_code == 0
        jobs = pipeline_queue.PipelineQueue(tmp_path.resolve()).list_jobs()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.ticker == "VG"
        assert job.job_type == pipeline_queue.FILING_UPDATE
        assert job.document_type == "10-Q"
        assert job.periods == ["FY2026Q1"]
        assert job.latest is False
        assert job.publish_root == str(stable.resolve())
        assert "Queued update VG" in result.output

    def test_queue_update_latest_creates_filing_update_job(self, tmp_path: Path):
        result = runner.invoke(
            app,
            [
                "queue",
                "update",
                "vg",
                "--document-type",
                "10-Q",
                "--latest",
                "--root",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0
        job = pipeline_queue.PipelineQueue(tmp_path.resolve()).list_jobs()[0]
        assert job.job_type == pipeline_queue.FILING_UPDATE
        assert job.document_type == "10-Q"
        assert job.periods == []
        assert job.latest is True
        assert "10-Q latest" in result.output

    def test_queue_update_requires_period_or_latest(self, tmp_path: Path):
        result = runner.invoke(
            app,
            [
                "queue-update",
                "vg",
                "--document-type",
                "10-Q",
                "--root",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 1
        assert "Specify at least one --period" in result.output

    def test_queue_update_rejects_latest_with_period(self, tmp_path: Path):
        result = runner.invoke(
            app,
            [
                "queue-update",
                "vg",
                "--document-type",
                "10-Q",
                "--latest",
                "--period",
                "FY2026Q1",
                "--root",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 1
        assert "Use either --latest or --period" in result.output

    def test_queue_run_processes_job_and_publishes_once(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        stable = tmp_path / "stable"
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=stable,
            publish_index_path=None,
        )
        events = []

        def fake_discover(ticker, *, years, config):
            events.append(("plan", ticker, years))
            return [
                ResearchFilingTarget(
                    ticker=ticker,
                    document_type="10-K",
                    period="FY2025",
                    accession_number="k",
                    filing_date="2026-02-01",
                    report_date="2025-12-31",
                )
            ]

        def fake_run_pipeline(**kwargs):
            events.append(("pipeline", kwargs["ticker"], kwargs["period"]))
            ticker_dir = kwargs["output_dir"] / "companies" / kwargs["ticker"]
            ticker_dir.mkdir(parents=True, exist_ok=True)
            (ticker_dir / "artifact.txt").write_text(kwargs["period"])

        def fake_build_company_context(root, ticker):
            events.append(("context", ticker))
            return {
                "artifact_index_path": _write_company_context_artifact(root, ticker),
                "counts": {"company_business_profiles": 1},
            }

        def fake_build_agent_index(root, *, index_path=None, force=True):
            events.append(("index", root, index_path, force))
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
            ["queue-run", "--root", str(tmp_path), "--max-jobs", "1"],
        )

        assert result.exit_code == 0
        assert store.load_job(job.job_id).status == pipeline_queue.SUCCEEDED
        assert (stable / "companies" / "CVX" / "artifact.txt").read_text() == "FY2025"
        assert events == [
            ("plan", "CVX", 1),
            ("pipeline", "CVX", "FY2025"),
            ("context", "CVX"),
            ("index", stable.resolve(), None, True),
        ]
        assert "SUCCEEDED" in result.output

    def test_queue_run_marks_failed_job_without_publish(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        stable = tmp_path / "stable"
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=stable,
            publish_index_path=None,
        )
        index_calls = []

        def fake_discover(ticker, *, years, config):
            return [
                ResearchFilingTarget(
                    ticker=ticker,
                    document_type="10-K",
                    period="FY2025",
                    accession_number="k",
                    filing_date="2026-02-01",
                    report_date="2025-12-31",
                )
            ]

        def fake_run_pipeline(**kwargs):
            raise RuntimeError("boom")

        def fake_build_agent_index(root, *, index_path=None, force=True):
            index_calls.append(root)

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            ["queue-run", "--root", str(tmp_path), "--max-jobs", "1"],
        )

        failed_job = store.load_job(job.job_id)
        assert result.exit_code == 0
        assert failed_job.status == pipeline_queue.FAILED
        assert failed_job.error == "boom"
        assert not (stable / "companies" / "CVX").exists()
        assert index_calls == []
        assert "FAILED" in result.output

    def test_queue_run_publish_prod_after_stable_publish(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        stable = tmp_path / "stable"
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=stable,
            publish_index_path=None,
        )
        prod_calls = []

        def fake_discover(ticker, *, years, config):
            return [
                ResearchFilingTarget(
                    ticker=ticker,
                    document_type="10-K",
                    period="FY2025",
                    accession_number="k",
                    filing_date="2026-02-01",
                    report_date="2025-12-31",
                )
            ]

        def fake_run_pipeline(**kwargs):
            ticker_dir = kwargs["output_dir"] / "companies" / kwargs["ticker"]
            ticker_dir.mkdir(parents=True, exist_ok=True)
            (ticker_dir / "artifact.txt").write_text(kwargs["period"])

        def fake_build_company_context(root, ticker):
            return {
                "artifact_index_path": _write_company_context_artifact(root, ticker),
                "counts": {"company_business_profiles": 1},
            }

        def fake_build_agent_index(root, *, index_path=None, force=True):
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 1, "objects": 2, "edges": 0, "quality_events": 0},
            }

        def fake_publish_prod_root(*, stable_root, **kwargs):
            prod_calls.append(stable_root)
            return {
                "release_id": "release-1",
                "host": "ubuntu@prod",
                "remote_root": "/srv/krw-ontology-data",
            }

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index, "build_agent_index", fake_build_agent_index)
        monkeypatch.setattr(cli_main, "_publish_prod_root", fake_publish_prod_root)

        result = runner.invoke(
            app,
            ["queue-run", "--root", str(tmp_path), "--max-jobs", "1", "--publish-prod"],
        )

        assert result.exit_code == 0
        assert store.load_job(job.job_id).status == pipeline_queue.SUCCEEDED
        assert prod_calls == [stable.resolve()]
        assert "Prod release activated: release=release-1" in result.output

    def test_queue_run_publish_prod_requires_publish_root(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        pipeline_calls = []
        monkeypatch.setattr(
            orchestrator,
            "run_pipeline",
            lambda **kwargs: pipeline_calls.append(kwargs),
        )

        result = runner.invoke(
            app,
            ["queue-run", "--root", str(tmp_path), "--max-jobs", "1", "--publish-prod"],
        )

        failed_job = store.load_job(job.job_id)
        assert result.exit_code == 0
        assert failed_job.status == pipeline_queue.FAILED
        assert "--publish-prod requires jobs with a stable publish root" in failed_job.error
        assert pipeline_calls == []

    def test_queue_run_processes_filing_update_job_and_publishes_once(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        stable = tmp_path / "stable"
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_update_job(
            "vg",
            document_type="10-Q",
            periods=["FY2026Q1"],
            latest=False,
            force=True,
            publish_root=stable,
            publish_index_path=None,
        )
        events = []

        def fake_discover(*args, **kwargs):
            raise AssertionError("filing_update jobs should not run full-refresh planning")

        def fake_run_pipeline(**kwargs):
            events.append(
                (
                    "pipeline",
                    kwargs["ticker"],
                    kwargs["document_type"],
                    kwargs["period"],
                    kwargs["latest"],
                    kwargs["force"],
                )
            )
            ticker_dir = kwargs["output_dir"] / "companies" / kwargs["ticker"]
            ticker_dir.mkdir(parents=True, exist_ok=True)
            (ticker_dir / "artifact.txt").write_text(kwargs["period"])

        def fake_build_company_context(root, ticker):
            events.append(("context", ticker))
            return {
                "artifact_index_path": _write_company_context_artifact(root, ticker),
                "counts": {"company_business_profiles": 1},
            }

        def fake_build_agent_index(root, *, index_path=None, force=True):
            events.append(("index", root, index_path, force))
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
            ["queue-run", "--root", str(tmp_path), "--max-jobs", "1"],
        )

        assert result.exit_code == 0
        assert store.load_job(job.job_id).status == pipeline_queue.SUCCEEDED
        assert (stable / "companies" / "VG" / "artifact.txt").read_text() == "FY2026Q1"
        assert events == [
            ("pipeline", "VG", "10-Q", "FY2026Q1", False, True),
            ("context", "VG"),
            ("index", stable.resolve(), None, True),
        ]
        assert "START update VG 10-Q FY2026Q1" in result.output
        assert "SUCCEEDED" in result.output

    def test_queue_start_launches_detached_worker(self, tmp_path: Path, monkeypatch):
        calls = []

        class FakeProcess:
            pid = 12345

        def fake_popen(command, *, stdout, stderr, start_new_session):
            calls.append((command, stderr, start_new_session))
            stdout.write("fake worker\n")
            stdout.flush()
            return FakeProcess()

        monkeypatch.setattr(cli_main.subprocess, "Popen", fake_popen)

        result = runner.invoke(
            app,
            ["queue-start", "--root", str(tmp_path), "--poll-interval", "2"],
        )

        assert result.exit_code == 0
        assert calls
        command = calls[0][0]
        assert "queue-run" in command
        assert "--watch" in command
        assert str(tmp_path.resolve()) in command
        assert calls[0][2] is True
        assert "Started queue worker pid=12345" in result.output
        assert (tmp_path / ".krw_pipeline" / "logs" / "worker.log").exists()

    def test_queue_start_can_publish_prod(self, tmp_path: Path, monkeypatch):
        calls = []

        class FakeProcess:
            pid = 12345

        def fake_popen(command, *, stdout, stderr, start_new_session):
            calls.append(command)
            return FakeProcess()

        monkeypatch.setattr(cli_main.subprocess, "Popen", fake_popen)

        result = runner.invoke(
            app,
            ["queue-start", "--root", str(tmp_path), "--publish-prod"],
        )

        assert result.exit_code == 0
        assert "--publish-prod" in calls[0]

    def test_queue_stop_requests_graceful_worker_stop(self, tmp_path: Path, monkeypatch):
        store = pipeline_queue.PipelineQueue(tmp_path)
        store.write_worker_pid(12345)
        signals = []
        monkeypatch.setattr(cli_main, "is_pid_running", lambda pid: True)
        monkeypatch.setattr(cli_main.os, "kill", lambda pid, sig: signals.append((pid, sig)))

        result = runner.invoke(
            app,
            ["queue-stop", "--root", str(tmp_path)],
        )

        assert result.exit_code == 0
        assert signals == []
        assert store.stop_requested()
        assert store.worker_pid_path.exists()
        assert "Stop requested" in result.output

    def test_queue_kill_force_kills_unresponsive_worker(self, tmp_path: Path, monkeypatch):
        store = pipeline_queue.PipelineQueue(tmp_path)
        store.write_worker_pid(12345)
        signals = []

        monkeypatch.setattr(cli_main, "is_pid_running", lambda pid: True)
        monkeypatch.setattr(cli_main.os, "kill", lambda pid, sig: signals.append((pid, sig)))

        result = runner.invoke(
            app,
            ["queue-kill", "--root", str(tmp_path), "--timeout", "0", "--force"],
        )

        assert result.exit_code == 0
        assert signals == [
            (12345, cli_main.signal.SIGTERM),
            (12345, cli_main.signal.SIGKILL),
        ]
        assert not store.worker_pid_path.exists()
        assert "Force killing" in result.output

    def test_queue_recover_stale_requeues_running_job_when_worker_stopped(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        store.mark_running(job)
        store.write_worker_pid(999999)

        result = runner.invoke(
            app,
            ["queue-recover-stale", "--root", str(tmp_path), "--reason", "worker killed"],
        )

        recovered = store.load_job(job.job_id)
        assert result.exit_code == 0
        assert recovered.status == pipeline_queue.PENDING
        assert recovered.attempts == 1
        assert recovered.started_at is None
        assert recovered.finished_at is None
        assert recovered.error is None
        assert not store.worker_pid_path.exists()
        assert "Requeued 1 stale job(s)" in result.output

    def test_queue_recover_stale_mark_failed(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        store.mark_running(job)

        result = runner.invoke(
            app,
            [
                "queue-recover-stale",
                "--root",
                str(tmp_path),
                "--mark-failed",
                "--reason",
                "worker killed",
            ],
        )

        recovered = store.load_job(job.job_id)
        assert result.exit_code == 0
        assert recovered.status == pipeline_queue.FAILED
        assert recovered.error == "worker killed"
        assert recovered.finished_at is not None
        assert "Marked 1 stale job(s) failed" in result.output

    def test_queue_recover_stale_refuses_live_worker(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        store.mark_running(job)
        store.write_worker_pid(os.getpid())

        result = runner.invoke(
            app,
            ["queue-recover-stale", "--root", str(tmp_path)],
        )

        assert result.exit_code == 1
        assert store.load_job(job.job_id).status == pipeline_queue.RUNNING
        assert "Queue worker appears to be running" in result.output

    def test_queue_recover_stale_dry_run_does_not_change_jobs(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        store.mark_running(job)

        result = runner.invoke(
            app,
            ["queue-recover-stale", "--root", str(tmp_path), "--dry-run"],
        )

        assert result.exit_code == 0
        assert store.load_job(job.job_id).status == pipeline_queue.RUNNING
        assert "Dry run complete" in result.output

    def test_queue_group_status_shows_stop_requested(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        store.request_stop()

        result = runner.invoke(app, ["queue", "status", "--root", str(tmp_path)])

        assert result.exit_code == 0
        assert "Stop requested: yes" in result.output

    def test_queue_group_status_compact_groups_operator_summary(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        running = store.add_job(
            "cost",
            years=3,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        store.mark_running(running)
        store.add_job(
            "nflx",
            years=3,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        succeeded = store.add_job(
            "ge",
            years=3,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        store.mark_succeeded(succeeded)
        comma_failed = store.add_job(
            "aapl,",
            years=3,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        store.mark_failed(comma_failed, "resolve_ticker: ticker 'AAPL,' not found")
        typo_failed = store.add_job(
            "appl",
            years=3,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        store.mark_failed(
            typo_failed,
            "resolve_ticker: ticker 'APPL' not found in SEC company_tickers.json",
        )

        result = runner.invoke(app, ["queue", "status", "--root", str(tmp_path), "--compact"])

        assert result.exit_code == 0
        assert "Jobs: pending=1 running=1 succeeded=1 failed=2 cancelled=0" in result.output
        assert "Running:" in result.output
        assert "COST full_refresh years=3 attempts=1" in result.output
        assert "Pending:" in result.output
        assert "NFLX" in result.output
        assert "Recent succeeded:" in result.output
        assert "GE" in result.output
        assert "1 invalid ticker(s) with trailing comma: `AAPL,`" in result.output
        assert "1 ticker not found in SEC company_tickers.json: `APPL`" in result.output

    def test_queue_cancel_marks_pending_job_cancelled(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )

        result = runner.invoke(
            app,
            ["queue-cancel", job.job_id, "--root", str(tmp_path), "--reason", "not needed"],
        )

        cancelled = store.load_job(job.job_id)
        assert result.exit_code == 0
        assert cancelled.status == pipeline_queue.CANCELLED
        assert cancelled.error == "not needed"
        assert "Cancelled 1 job(s)" in result.output

    def test_queue_cancel_skips_running_without_allow_running(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        store.mark_running(job)

        result = runner.invoke(
            app,
            ["queue-cancel", job.job_id, "--root", str(tmp_path)],
        )

        assert result.exit_code == 0
        assert store.load_job(job.job_id).status == pipeline_queue.RUNNING
        assert "Skipped CVX" in result.output
        assert "Cancelled 0 job(s)" in result.output

    def test_queue_log_reads_job_log(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=None,
            publish_index_path=None,
        )
        store.append_job_log(job.job_id, "first")
        store.append_job_log(job.job_id, "second")

        result = runner.invoke(
            app,
            ["queue-log", "--root", str(tmp_path), "--job-id", job.job_id, "--lines", "1"],
        )

        assert result.exit_code == 0
        assert "second" in result.output
        assert "first" not in result.output


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
        _write_company_context_artifact(running, "CVX")
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
        _write_company_context_artifact(running, "OXY")
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
        assert "update-ticker" in result.output
        assert "queue" in result.output
        assert "config" in result.output
        assert "prod" in result.output
        assert "publish-ticker" in result.output
        assert "validate" in result.output
        assert "build-report" in result.output
        assert "build-agent-index" in result.output

    def test_queue_help_shows_operator_commands(self):
        result = runner.invoke(app, ["queue", "--help"])
        assert result.exit_code == 0
        assert "add" in result.output
        assert "update" in result.output
        assert "start" in result.output
        assert "status" in result.output
        assert "log" in result.output
        assert "watch" in result.output
        assert "stop" in result.output
        assert "kill" in result.output
        assert "recover-stale" in result.output
        assert "cancel" in result.output
        assert "Common flow: full refresh" in result.output
        assert "Safe shutdown" in result.output
        assert "Immediate interrupt" in result.output

    def test_queue_status_help_shows_compact_mode(self):
        result = runner.invoke(app, ["queue", "status", "--help"])
        assert result.exit_code == 0
        assert "--compact" in result.output
        assert "operator summary" in result.output
