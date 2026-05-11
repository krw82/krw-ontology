"""Tests for CLI commands: validate and build-report."""

from __future__ import annotations

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
                "artifact_index_path": root / "companies" / ticker / "company_context" / "artifact_index.json",
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
                "artifact_index_path": root / "companies" / ticker / "company_context" / "artifact_index.json",
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
                "artifact_index_path": root / "companies" / ticker / "company_context" / "artifact_index.json",
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
                "artifact_index_path": root / "companies" / ticker / "company_context" / "artifact_index.json",
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

    def test_queue_group_status_shows_stop_requested(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        store.request_stop()

        result = runner.invoke(app, ["queue", "status", "--root", str(tmp_path)])

        assert result.exit_code == 0
        assert "Stop requested: yes" in result.output

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
        assert "update-ticker" in result.output
        assert "queue" in result.output
        assert "config" in result.output
        assert "publish-ticker" in result.output
        assert "validate" in result.output
        assert "build-report" in result.output
        assert "build-agent-index" in result.output

    def test_queue_help_shows_operator_commands(self):
        result = runner.invoke(app, ["queue", "--help"])
        assert result.exit_code == 0
        assert "add" in result.output
        assert "start" in result.output
        assert "status" in result.output
        assert "log" in result.output
        assert "watch" in result.output
        assert "stop" in result.output
        assert "kill" in result.output
        assert "cancel" in result.output
