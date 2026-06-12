"""Tests for CLI commands: validate and build-report."""

from __future__ import annotations

import os
import json
import sqlite3
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

import krw_ontology.agent_index as agent_index
import krw_ontology.agent_index.builder as agent_index_builder
import krw_ontology.cli.main as cli_main
import krw_ontology.release as release_helpers
import krw_ontology.pipeline.orchestrator as orchestrator
import krw_ontology.pipeline.queue as pipeline_queue
import krw_ontology.pipeline.research_plan as research_plan
import krw_ontology.pipeline.stages.build_company_context as company_context_stage
from krw_ontology.agent_index.builder import AGENT_INDEX_SCHEMA_VERSION
from krw_ontology.agent_index.spine_builder import _company_shard_quality_summary
from krw_ontology.agent_index.spine_schema import initialize_global_spine_database
from krw_ontology.cli.config import load_cli_config
from krw_ontology.cli.main import app
from krw_ontology.release import write_release_manifest_v3
from krw_ontology.pipeline.research_plan import ResearchFilingTarget

runner = CliRunner()


def _write_minimal_agent_index(index_path: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    (index_path.parent.parent / "companies" / "TEST").mkdir(parents=True, exist_ok=True)
    index_path.unlink(missing_ok=True)
    with sqlite3.connect(index_path) as conn:
        agent_index_builder._create_schema(conn)
        conn.execute(
            "INSERT INTO metadata(key, value) VALUES('build', ?)",
            (
                json.dumps(
                    {
                        "schema_version": AGENT_INDEX_SCHEMA_VERSION,
                        "agent_index_schema_version": AGENT_INDEX_SCHEMA_VERSION,
                    }
                ),
            ),
        )
    _write_minimal_artifact_manifest(index_path.parent / "artifact_manifest.json")


def _write_minimal_artifact_manifest(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "format": "krw-agent-index-artifact-manifest/v1",
                "artifact_count": 0,
                "artifacts": [],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_minimal_source_artifact(root: Path, ticker: str = "AAPL") -> None:
    artifact_index_path = root / "companies" / ticker / "context" / "artifact_index.json"
    artifact_index_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_index_path.write_text(
        json.dumps(
            {
                "ticker": ticker,
                "document_type": "COMPANY",
                "doc_type_key": "COMPANY",
                "period": "ALL",
                "files": {},
                "counts": {},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_minimal_v3_release(
    root: Path,
    *,
    release_id: str = "ready",
    env: str = "dev",
    ticker: str = "AAPL",
    artifact_text: str = "ok",
) -> Path:
    (root / "companies" / ticker).mkdir(parents=True, exist_ok=True)
    (root / "companies" / ticker / "artifact.txt").write_text(artifact_text, encoding="utf-8")
    shard_path = root / "indexes" / "companies" / f"{ticker}.sqlite"
    _write_minimal_agent_index(shard_path)
    initialize_global_spine_database(
        root / "indexes" / "global_spine.sqlite",
        metadata={
            "release_id": release_id,
            "source_manifest_hash": "test-source",
        },
        replace=True,
    )
    source_manifest_path = root / "source_manifest.json"
    source_manifest_path.write_text(
        json.dumps(
            {
                "format": "krw-ontology-source-manifest/v3-test",
                "tickers": [ticker],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    shard_manifest_path = root / "indexes" / "shard_manifest.json"
    shard_manifest_path.write_text(
        json.dumps(
            {
                "format": "krw-ontology-shard-manifest/v3",
                "index_layout": "global-spine-and-company-shards",
                "release_id": release_id,
                "source_manifest_hash": "test-source",
                "ticker_count": 1,
                "shards": {
                    ticker: {
                        "ticker": ticker,
                        "path": f"companies/{ticker}.sqlite",
                        "schema_version": "krw-company-shard/v1",
                        "document_count": 0,
                        "object_count": 0,
                        "edge_count": 0,
                        "quality_event_count": 0,
                        "quality_summary": _company_shard_quality_summary(shard_path),
                        "sha256": _sha256(shard_path),
                    }
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_release_manifest_v3(root, release_id=release_id, env=env, source_root=root)
    return root


def _write_minimal_v3_index_outputs(
    root: Path,
    *,
    release_id: str = "refresh",
    ticker: str = "TEST",
):
    indexes_dir = root / "indexes"
    global_spine_path = indexes_dir / "global_spine.sqlite"
    shard_manifest_path = indexes_dir / "shard_manifest.json"
    build_summary_path = indexes_dir / "build_summary.json"
    shard_path = indexes_dir / "companies" / f"{ticker}.sqlite"
    initialize_global_spine_database(
        global_spine_path,
        metadata={
            "release_id": release_id,
            "source_manifest_hash": "test-source",
        },
        replace=True,
    )
    _write_minimal_agent_index(shard_path)
    shard_manifest = {
        "format": "krw-ontology-shard-manifest/v3",
        "index_layout": "global-spine-and-company-shards",
        "release_id": release_id,
        "source_manifest_hash": "test-source",
        "ticker_count": 1,
        "shards": {
            ticker: {
                "ticker": ticker,
                "path": f"indexes/companies/{ticker}.sqlite",
                "sha256": _sha256(shard_path),
                "quality_summary": _company_shard_quality_summary(shard_path),
            }
        },
    }
    shard_manifest_path.write_text(json.dumps(shard_manifest, sort_keys=True), encoding="utf-8")
    build_summary = {
        "format": "krw-ontology-v3-build-summary/v1",
        "release_id": release_id,
        "index_layout": "global-spine-and-company-shards",
        "artifact_count": 1,
        "company_count": 1,
        "company_shard_cache": {"hits": 0, "misses": 1},
        "spine_fragment_cache": {"hits": 0, "misses": 1},
        "global_spine": {
            "path": "indexes/global_spine.sqlite",
            "counts": {
                "global_document_catalog": 1,
                "global_object_locator": 2,
                "global_edge_spine": 0,
                "global_topic_spine": 0,
            },
        },
    }
    build_summary_path.write_text(json.dumps(build_summary, sort_keys=True), encoding="utf-8")
    return SimpleNamespace(
        release_root=root,
        release_id=release_id,
        global_spine_path=global_spine_path,
        shard_manifest_path=shard_manifest_path,
        build_summary_path=build_summary_path,
        build_summary=build_summary,
    )


def _write_company_context_artifact(root: Path, ticker: str) -> Path:
    artifact_payload = {
        "ticker": ticker.upper(),
        "document_type": "COMPANY",
        "doc_type_key": "COMPANY",
        "period": "ALL",
        "artifacts": [],
        "counts": {
            "company_business_profiles": 1,
            "trend_observations": 1,
            "change_events": 1,
            "temporal_links": 1,
            "edges": 1,
        },
    }
    artifact_index_path = root / "companies" / ticker / "context" / "artifact_index.json"
    artifact_index_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_index_path.write_text(json.dumps(artifact_payload, sort_keys=True), encoding="utf-8")
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
    def test_build_research_pipeline_runs_targets_and_refreshes_v3_index(
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

        def fake_build_spine(root, *, release_id, no_cache=False, **kwargs):
            index_calls.append((root, release_id, no_cache))
            return _write_minimal_v3_index_outputs(root, release_id=release_id, ticker="AAPL")

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(agent_index, "build_spine_shard_release_outputs", fake_build_spine)

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
        assert index_calls == [(tmp_path.resolve(), "research-pipeline-refresh", False)]
        assert (tmp_path / "indexes" / "global_spine.sqlite").exists()
        assert not (tmp_path / "indexes" / "agent_index.sqlite").exists()
        assert "V3 index refreshed" in result.output

    def test_build_research_pipeline_refreshes_v3_index_after_failure(
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

        def fake_build_spine(root, *, release_id, no_cache=False, **kwargs):
            index_calls.append((root, release_id, no_cache))
            return _write_minimal_v3_index_outputs(root, release_id=release_id, ticker="AAPL")

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(agent_index, "build_spine_shard_release_outputs", fake_build_spine)

        result = runner.invoke(
            app,
            ["build-research-pipeline", "aapl", "--root", str(tmp_path)],
        )

        assert result.exit_code == 1
        assert index_calls == [(tmp_path.resolve(), "research-pipeline-refresh", False)]
        assert "Refreshing v3 index" in result.output
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

        def fake_build_spine(root, *, release_id, no_cache=False, **kwargs):
            events.append(("index", root))
            result = None
            tickers = sorted(
                path.name for path in (root / "companies").iterdir()
                if path.is_dir()
            )
            for ticker in tickers:
                result = _write_minimal_v3_index_outputs(root, release_id=release_id, ticker=ticker)
            return result or _write_minimal_v3_index_outputs(root, release_id=release_id, ticker="AAPL")

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index, "build_spine_shard_release_outputs", fake_build_spine)

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
                "--release-id",
                "research-rel",
            ],
        )

        release_root = stable / "dev" / "research-rel"
        assert result.exit_code == 0
        assert (release_root / "companies" / "AAPL" / "artifact.txt").read_text() == "AAPL"
        assert (release_root / "companies" / "MSFT" / "artifact.txt").read_text() == "MSFT"
        assert (stable / "dev" / "current").readlink() == Path("research-rel")
        assert events[:4] == [
            ("pipeline", "AAPL"),
            ("context", "AAPL"),
            ("pipeline", "MSFT"),
            ("context", "MSFT"),
        ]
        assert events[4:] == [
            ("index", release_root.resolve()),
            ("index", running.resolve()),
        ]
        assert (release_root / "indexes" / "global_spine.sqlite").exists()
        assert (release_root / "indexes" / "companies" / "AAPL.sqlite").exists()
        assert (release_root / "indexes" / "companies" / "MSFT.sqlite").exists()
        assert not (release_root / "indexes" / "agent_index.sqlite").exists()
        assert "Published AAPL" in result.output
        assert "Published MSFT" in result.output
        assert "global_spine:" in result.output

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

        def fake_build_spine(root, *, release_id, no_cache=False, **kwargs):
            index_calls.append(root)
            result = None
            tickers = sorted(
                path.name for path in (root / "companies").iterdir()
                if path.is_dir()
            )
            for ticker in tickers:
                result = _write_minimal_v3_index_outputs(root, release_id=release_id, ticker=ticker)
            return result or _write_minimal_v3_index_outputs(root, release_id=release_id, ticker="MSFT")

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index, "build_spine_shard_release_outputs", fake_build_spine)

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
                "--release-id",
                "research-partial",
            ],
        )

        release_root = stable / "dev" / "research-partial"
        assert result.exit_code == 1
        assert not (release_root / "companies" / "AAPL").exists()
        assert (release_root / "companies" / "MSFT" / "artifact.txt").read_text() == "MSFT"
        assert (stable / "dev" / "current").readlink() == Path("research-partial")
        assert index_calls == [release_root.resolve(), running.resolve()]
        assert (release_root / "indexes" / "global_spine.sqlite").exists()
        assert not (release_root / "indexes" / "agent_index.sqlite").exists()
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

        def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
            events.append(("index", root, index_path, force))
            _write_minimal_agent_index(index_path or root / "indexes" / "agent_index.sqlite")
            return {
                "index_path": index_path or root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 1, "objects": 2, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)

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
                "--release-id",
                "rel-update",
            ],
        )

        release_root = stable / "dev" / "rel-update"
        assert result.exit_code == 0
        pipeline_kwargs = events[0][1]
        assert pipeline_kwargs["ticker"] == "CVX"
        assert pipeline_kwargs["document_type"] == "10-Q"
        assert pipeline_kwargs["period"] == "FY2026Q1"
        assert pipeline_kwargs["latest"] is False
        assert pipeline_kwargs["output_dir"] == running.resolve()
        assert events[1] == ("context", running.resolve(), "CVX")
        assert (release_root / "companies" / "CVX" / "artifact.txt").read_text() == "FY2026Q1"
        assert (release_root / "indexes" / "global_spine.sqlite").exists()
        assert (release_root / "indexes" / "companies" / "CVX.sqlite").exists()
        assert not (release_root / "indexes" / "agent_index.sqlite").exists()
        assert (stable / "dev" / "current").readlink() == Path("rel-update")
        assert "Published CVX" in result.output
        assert "global_spine:" in result.output

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

        def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
            events.append(("index", root, index_path, force))
            _write_minimal_agent_index(index_path or root / "indexes" / "agent_index.sqlite")
            return {
                "index_path": index_path or root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 2, "objects": 4, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)

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
                "--release-id",
                "rel-multi",
            ],
        )

        release_root = stable / "dev" / "rel-multi"
        assert result.exit_code == 0
        assert events == [
            ("pipeline", "FY2026Q1"),
            ("pipeline", "FY2026Q2"),
            ("context", running.resolve(), "CVX"),
        ]
        assert (release_root / "companies" / "CVX" / "FY2026Q1.txt").read_text() == "FY2026Q1"
        assert (release_root / "companies" / "CVX" / "FY2026Q2.txt").read_text() == "FY2026Q2"
        assert (release_root / "indexes" / "global_spine.sqlite").exists()
        assert not (release_root / "indexes" / "agent_index.sqlite").exists()
        assert (stable / "dev" / "current").readlink() == Path("rel-multi")
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

        def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
            index_calls.append(root)
            return {
                "index_path": root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 0, "objects": 0, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)

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


class TestObservabilityCommand:
    def test_render_alertmanager_reads_env_without_echoing_secret_urls(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        output_path = tmp_path / "alertmanager.yml"
        secret_urls = {
            "KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL": "https://alerts.example/default?token=secret-default",
            "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL": "https://alerts.example/critical?token=secret-critical",
            "KRW_ALERTMANAGER_WARNING_WEBHOOK_URL": "https://alerts.example/warning?token=secret-warning",
        }
        for key, value in secret_urls.items():
            monkeypatch.setenv(key, value)

        result = runner.invoke(
            app,
            [
                "observability",
                "render-alertmanager",
                "--output",
                str(output_path),
            ],
        )

        assert result.exit_code == 0, result.output
        assert output_path.exists()
        for value in secret_urls.values():
            assert value not in result.output
        assert "alertmanager_config:" in result.output
        assert "receiver_critical: KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL" in result.output

        payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        receivers = {receiver["name"]: receiver for receiver in payload["receivers"]}
        assert receivers["krw-ontology-mcp-default"]["webhook_configs"][0]["url"] == secret_urls[
            "KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL"
        ]
        assert receivers["krw-ontology-mcp-critical"]["webhook_configs"][0]["url"] == secret_urls[
            "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL"
        ]
        assert receivers["krw-ontology-mcp-warning"]["webhook_configs"][0]["url"] == secret_urls[
            "KRW_ALERTMANAGER_WARNING_WEBHOOK_URL"
        ]

    def test_render_alertmanager_fails_without_receiver_urls(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        for key in (
            "KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL",
            "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL",
            "KRW_ALERTMANAGER_WARNING_WEBHOOK_URL",
        ):
            monkeypatch.delenv(key, raising=False)

        result = runner.invoke(
            app,
            [
                "observability",
                "render-alertmanager",
                "--output",
                str(tmp_path / "alertmanager.yml"),
            ],
        )

        assert result.exit_code == 1
        assert "missing receiver URLs" in result.output

    def test_render_prometheus_alerts_uses_threshold_options(self, tmp_path: Path):
        output_path = tmp_path / "prometheus-alerts.yml"

        result = runner.invoke(
            app,
            [
                "observability",
                "render-prometheus-alerts",
                "--output",
                str(output_path),
                "--mcp-down-for",
                "4m",
                "--hot-swap-retired-age-seconds",
                "600",
                "--rotation-window",
                "45m",
                "--rotation-count",
                "9",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "prometheus_alerts:" in result.output
        assert "threshold_hot_swap_retired_age_seconds: 600 (argument)" in result.output
        assert "threshold_rotation_count: 9 (argument)" in result.output

        payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        rules = {rule["alert"]: rule for rule in payload["groups"][0]["rules"]}
        assert rules["KRWOntologyMCPDown"]["for"] == "4m"
        assert rules["KRWOntologyMCPHotSwapStuck"]["expr"].endswith("> 600")
        assert rules["KRWOntologyMCPExcessiveRotations"]["expr"] == (
            "increase(krw_ontology_mcp_store_rotations_total[45m]) > 9"
        )

    def test_render_prometheus_alerts_rejects_invalid_duration(self, tmp_path: Path):
        result = runner.invoke(
            app,
            [
                "observability",
                "render-prometheus-alerts",
                "--output",
                str(tmp_path / "prometheus-alerts.yml"),
                "--mcp-down-for",
                "minutes",
            ],
        )

        assert result.exit_code == 1
        assert "mcp_down_for must be a Prometheus duration" in result.output

    def test_observability_doctor_rejects_prod_local_receivers(self):
        result = runner.invoke(app, ["observability", "doctor"])

        assert result.exit_code == 1
        assert "Observability doctor" in result.output
        assert "webhook_url_localhost" in result.output

    def test_observability_doctor_accepts_rendered_configs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        alertmanager_path = tmp_path / "alertmanager.yml"
        report_path = tmp_path / "observability-doctor.json"
        for key, value in {
            "KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL": "https://alerts.example/default",
            "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL": "https://alerts.example/critical",
            "KRW_ALERTMANAGER_WARNING_WEBHOOK_URL": "https://alerts.example/warning",
        }.items():
            monkeypatch.setenv(key, value)
        render_result = runner.invoke(
            app,
            [
                "observability",
                "render-alertmanager",
                "--output",
                str(alertmanager_path),
            ],
        )

        result = runner.invoke(
            app,
            [
                "observability",
                "doctor",
                "--alertmanager",
                str(alertmanager_path),
                "--write-report",
                str(report_path),
            ],
        )

        assert render_result.exit_code == 0, render_result.output
        assert result.exit_code == 0, result.output
        assert "Observability doctor passed." in result.output
        assert "observability_report:" in result.output
        assert "observability_audit_hash:" in result.output
        assert "alertmanager_receiver_count: 3" in result.output
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["format"] == "krw-ontology-observability-doctor/v1"
        assert report["ok"] is True
        assert len(report["audit_hash"]) == 64
        assert len(report["alertmanager"]["sha256"]) == 64


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


def test_release_publish_config_infers_releases_root_from_prepared_release_root(tmp_path: Path):
    releases_root = tmp_path / "releases"
    prepared_release = releases_root / "dev" / "prepared"
    prepared_release.mkdir(parents=True)

    resolved_root, resolved_env = cli_main._resolve_release_publish_config(prepared_release)

    assert resolved_root == releases_root.resolve()
    assert resolved_env == "dev"


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

    def test_prod_activation_quarantines_failed_release_before_current_switch(self, tmp_path: Path):
        remote_root = tmp_path / "remote"
        release_id = "bad-release"
        incoming = remote_root / "incoming"
        old_release = remote_root / "releases" / "old-release"
        incoming.mkdir(parents=True)
        old_release.mkdir(parents=True)
        (remote_root / "current").symlink_to("releases/old-release")
        bundle_source = tmp_path / "bundle-source"
        bundle_source.mkdir()
        (bundle_source / "manifest.json").write_text(
            json.dumps(
                {
                    "format": "krw-ontology-release/v3",
                    "env": "prod",
                    "release_id": release_id,
                    "status": "ready",
                    "index_layout": "global-spine-and-company-shards",
                    "monolith_required": False,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        with tarfile.open(incoming / f"{release_id}.tar.gz", "w:gz") as archive:
            archive.add(bundle_source / "manifest.json", arcname="manifest.json")

        script = cli_main._prod_activation_script(
            remote_root=str(remote_root),
            release_id=release_id,
            reload_command=None,
            health_url=None,
            keep_releases=5,
        )
        completed = cli_main.subprocess.run(
            ["sh"],
            input=script,
            text=True,
            capture_output=True,
        )

        assert completed.returncode != 0
        assert "global_spine.sqlite missing" in completed.stderr
        assert os.readlink(remote_root / "current") == "releases/old-release"
        assert not (remote_root / "releases" / release_id).exists()
        assert (remote_root / "failed" / release_id / "manifest.json").exists()

    def test_prod_activation_rejects_stale_shard_quality_summary_before_current_switch(self, tmp_path: Path):
        remote_root = tmp_path / "remote"
        release_id = "bad-quality"
        incoming = remote_root / "incoming"
        old_release = remote_root / "releases" / "old-release"
        incoming.mkdir(parents=True)
        old_release.mkdir(parents=True)
        (remote_root / "current").symlink_to("releases/old-release")

        stable = tmp_path / "stable"
        _write_minimal_v3_release(stable, release_id="stable-dev", env="dev", ticker="AAPL")
        bundle_source = tmp_path / "bundle-source"
        cli_main._materialize_verified_prod_release(stable, bundle_source, release_id)
        shard_manifest_path = bundle_source / "indexes" / "shard_manifest.json"
        shard_manifest = json.loads(shard_manifest_path.read_text(encoding="utf-8"))
        shard_manifest["shards"]["AAPL"]["quality_summary"]["totals"]["documents"] = 999
        shard_manifest_path.write_text(json.dumps(shard_manifest, sort_keys=True), encoding="utf-8")
        write_release_manifest_v3(bundle_source, release_id=release_id, env="prod", source_root=stable)
        with tarfile.open(incoming / f"{release_id}.tar.gz", "w:gz") as archive:
            for child in sorted(bundle_source.iterdir()):
                archive.add(child, arcname=child.name, recursive=True)

        script = cli_main._prod_activation_script(
            remote_root=str(remote_root),
            release_id=release_id,
            reload_command=None,
            health_url=None,
            keep_releases=5,
        )
        completed = cli_main.subprocess.run(
            ["sh"],
            input=script,
            text=True,
            capture_output=True,
        )

        assert completed.returncode != 0
        assert "remote shard quality_summary mismatch: AAPL" in completed.stderr
        assert os.readlink(remote_root / "current") == "releases/old-release"
        assert not (remote_root / "releases" / release_id).exists()
        assert (remote_root / "failed" / release_id / "indexes" / "shard_manifest.json").exists()

    def test_prod_publish_uploads_bundle_and_activates_release(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        stable = tmp_path / "stable"
        _write_minimal_v3_release(stable, release_id="stable-dev", env="dev", ticker="AAPL")
        (stable / "indexes" / "agent_index.sqlite").write_text("legacy monolith", encoding="utf-8")
        (stable / "indexes" / "agent_index.sqlite-wal").write_text("legacy wal", encoding="utf-8")
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
        bundle_members = []
        bundle_manifest = {}

        class FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(command, *, input=None, text, capture_output):
            run_calls.append((command, input))
            if command[0] == "scp":
                with tarfile.open(command[1], "r:gz") as archive:
                    bundle_members.extend(archive.getnames())
                    manifest_file = archive.extractfile("manifest.json")
                    assert manifest_file is not None
                    bundle_manifest.update(json.loads(manifest_file.read().decode("utf-8")))
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
        assert "Release manifest format is not v3" in activation_script
        assert "Release manifest env is not prod" in activation_script
        assert "global_spine.sqlite missing" in activation_script
        assert "shard_manifest.json missing" in activation_script
        assert "python3 missing for remote v3 verification" in activation_script
        assert "remote manifest format is not v3" in activation_script
        assert "remote global_spine sha256 mismatch" in activation_script
        assert "remote shard_manifest sha256 mismatch" in activation_script
        assert "remote delta changed file sha256 mismatch" in activation_script
        assert "remote manifest company_shards count mismatch" in activation_script
        assert "remote company shard sha256 mismatch" in activation_script
        assert "remote shard quality_summary missing" in activation_script
        assert "remote shard quality_summary mismatch" in activation_script
        assert "quarantine_failed_release" in activation_script
        assert 'rm -f "$ROOT/current"' in activation_script
        assert "remote_v3_preflight=ok" in activation_script
        assert "release_verify.json missing" in activation_script
        assert 'ACTIVATION_LOG="$ROOT/activation_logs/$RELEASE_ID.log"' in activation_script
        assert 'ACTIVATION_EVENTS="$ROOT/activation_events/$RELEASE_ID.jsonl"' in activation_script
        assert 'ACTIVATION_LOG="$ROOT/releases/$RELEASE_ID/' not in activation_script
        assert "activation_event \"current_switched\" \"ok\"" in activation_script
        assert "rollback reload_failed" in activation_script
        assert "rollback health_failed" in activation_script
        assert "rollback health_not_ok" in activation_script
        assert "rollback health_release_mismatch" in activation_script
        assert "rollback health_layout_mismatch" in activation_script
        assert '"release_id"[[:space:]]*:[[:space:]]*"\'"$RELEASE_ID"\'"' in activation_script
        assert '"index_layout"[[:space:]]*:[[:space:]]*"global-spine-and-company-shards"' in activation_script
        assert "manifest.json" in bundle_members
        assert "release_manifest.json" not in bundle_members
        assert "verify/release_verify.json" in bundle_members
        assert "companies/AAPL/artifact.txt" in bundle_members
        assert "indexes/global_spine.sqlite" in bundle_members
        assert "indexes/shard_manifest.json" in bundle_members
        assert "indexes/companies/AAPL.sqlite" in bundle_members
        assert "indexes/agent_index.sqlite" not in bundle_members
        assert "indexes/agent_index.sqlite-wal" not in bundle_members
        assert bundle_manifest["env"] == "prod"
        assert bundle_manifest["release_id"] == "20260515-000000"
        assert bundle_manifest["format"] == "krw-ontology-release/v3"
        assert bundle_manifest["index_layout"] == "global-spine-and-company-shards"
        assert bundle_manifest["monolith_required"] is False

    def test_prod_delta_activation_reconstructs_release_from_current(self, tmp_path: Path):
        remote_root = tmp_path / "remote"
        old_release = remote_root / "releases" / "old-release"
        _write_minimal_v3_release(old_release, release_id="old-release", env="prod", ticker="VG", artifact_text="old")
        (old_release / "companies" / "VG" / "artifact.txt").unlink()
        (old_release / "companies" / "VG" / "old.txt").write_text("old", encoding="utf-8")
        (remote_root / "current").symlink_to("releases/old-release")

        stable = tmp_path / "stable"
        cli_main._materialize_prod_bundle_root(old_release, stable)
        (stable / "companies" / "VG" / "old.txt").unlink()
        (stable / "companies" / "VG" / "new.txt").write_text("new", encoding="utf-8")
        candidate = tmp_path / "candidate" / "delta-release"
        cli_main._materialize_verified_prod_release(stable, candidate, "delta-release")
        remote_files = cli_main._local_release_file_map(old_release)
        delta_bundle = remote_root / "incoming" / "delta-release.delta.tar.gz"
        delta_summary = cli_main._build_prod_release_delta_bundle(
            candidate,
            delta_bundle,
            remote_files=remote_files,
        )

        script = cli_main._prod_activation_script(
            remote_root=str(remote_root),
            release_id="delta-release",
            reload_command=None,
            health_url=None,
            keep_releases=5,
        )
        assert delta_summary["changed_file_count"] > 0
        assert "companies/VG/old.txt" in delta_summary["removed_paths"]
        with tarfile.open(delta_bundle, "r:gz") as archive:
            members = set(archive.getnames())
            delta_manifest_file = archive.extractfile(".krw_delta_manifest.json")
            assert delta_manifest_file is not None
            delta_manifest = json.loads(delta_manifest_file.read().decode("utf-8"))
        assert ".krw_delta_manifest.json" in members
        assert "companies/VG/new.txt" in members
        assert "companies/VG/old.txt" not in members
        assert "companies/VG/old.txt" in delta_manifest["removed"]
        assert "changed_files" in delta_manifest
        assert 'DELTA_BUNDLE="$ROOT/incoming/$RELEASE_ID.delta.tar.gz"' in script
        assert 'tar -C "$ROOT/$CURRENT_TARGET" -cf - .' in script
        assert "delta remove path escapes release root" in script

    def test_prod_rollback_verifies_target_before_switch(self, monkeypatch):
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
            ],
        )
        run_calls = []

        class FakeCompleted:
            returncode = 0
            stderr = ""
            stdout = "releases/20260515-000000\n"

        def fake_run(command, *, input=None, text, capture_output):
            run_calls.append((command, input))
            return FakeCompleted()

        monkeypatch.setattr(cli_main.subprocess, "run", fake_run)

        result = runner.invoke(app, ["prod", "rollback", "20260515-000000"])

        assert result.exit_code == 0
        assert "Prod rollback activated release=20260515-000000" in result.output
        assert [call[0][0] for call in run_calls] == ["ssh"]
        rollback_script = run_calls[0][1]
        assert "Rollback release manifest format is not v3" in rollback_script
        assert "Rollback release manifest env is not prod" in rollback_script
        assert "Rollback global_spine.sqlite missing" in rollback_script
        assert "Rollback shard_manifest.json missing" in rollback_script
        assert "python3 missing for rollback v3 verification" in rollback_script
        assert "rollback manifest missing global_spine" in rollback_script
        assert "rollback manifest missing company_shards" in rollback_script
        assert "rollback global_spine sha256 mismatch" in rollback_script
        assert "rollback shard_manifest sha256 mismatch" in rollback_script
        assert "rollback company shard sha256 mismatch" in rollback_script
        assert "rollback shard quality_summary missing" in rollback_script
        assert "rollback shard quality_summary mismatch" in rollback_script
        assert "rollback release_verify.json missing" in rollback_script
        assert "restore_current reload_failed" in rollback_script
        assert "restore_current health_failed" in rollback_script
        assert "restore_current health_not_ok" in rollback_script
        assert "restore_current health_release_mismatch" in rollback_script
        assert "restore_current health_layout_mismatch" in rollback_script
        assert '"release_id"[[:space:]]*:[[:space:]]*"\'"$TARGET_RELEASE_ID"\'"' in rollback_script
        assert '"index_layout"[[:space:]]*:[[:space:]]*"global-spine-and-company-shards"' in rollback_script
        assert rollback_script.index("rollback release_verify.json missing") < rollback_script.index('ln -sfn "$TARGET"')

    def test_prod_publish_dry_run_skips_subprocess(self, tmp_path: Path, monkeypatch):
        stable = tmp_path / "stable"
        _write_minimal_v3_release(stable, release_id="stable-dev", env="dev")
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

    def test_prod_publish_rejects_v2_release(self, tmp_path: Path):
        stable = tmp_path / "stable-v2"
        stable.mkdir(parents=True)
        (stable / "manifest.json").write_text(
            json.dumps(
                {
                    "format": "krw-ontology-release/v2",
                    "release_id": "stable-v2",
                    "env": "dev",
                    "status": "ready",
                    "index_path": "indexes/agent_index.sqlite",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
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

        result = runner.invoke(app, ["prod", "publish", "--root", str(stable), "--dry-run"])

        assert result.exit_code == 1
        assert "prod publish source must be a v3 immutable release" in result.output

    def test_prod_publish_uses_configured_release_current_by_default(self, tmp_path: Path, monkeypatch):
        releases_root = tmp_path / "releases"
        release_root = releases_root / "dev" / "ready"
        _write_minimal_v3_release(release_root, release_id="ready", env="dev")
        (releases_root / "dev" / "current").symlink_to("ready")
        runner.invoke(app, ["config", "set", "publish-root", str(releases_root)])
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
        calls: list[Path] = []
        monkeypatch.setattr(
            cli_main,
            "_try_get_prod_status",
            lambda **kwargs: {"current_kind": "missing", "releases": []},
        )

        def fake_publish_prod_root(*, stable_root, **kwargs):
            calls.append(stable_root)
            return {
                "release_id": "prod-release",
                "stable_root": str(stable_root),
                "host": "ubuntu@prod",
                "remote_root": "/srv/krw-ontology-data",
                "keep_releases": 5,
                "upload_mode": "delta",
                "changed_file_count": 0,
                "removed_file_count": 0,
            }

        monkeypatch.setattr(cli_main, "_publish_prod_root", fake_publish_prod_root)

        result = runner.invoke(app, ["prod", "publish", "--delta"])

        assert result.exit_code == 0, result.output
        assert calls == [release_root.resolve()]

    def test_prod_publish_dev_alias_defaults_to_delta_dev_current(self, tmp_path: Path, monkeypatch):
        releases_root = tmp_path / "releases"
        release_root = releases_root / "dev" / "ready"
        _write_minimal_v3_release(release_root, release_id="ready", env="dev")
        (releases_root / "dev" / "current").symlink_to("ready")
        runner.invoke(app, ["config", "set", "publish-root", str(releases_root)])
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
        calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            cli_main,
            "_try_get_prod_status",
            lambda **kwargs: {"current_kind": "missing", "releases": []},
        )

        def fake_publish_prod_root(**kwargs):
            calls.append(kwargs)
            return {
                "release_id": "prod-release",
                "stable_root": str(kwargs["stable_root"]),
                "host": "ubuntu@prod",
                "remote_root": "/srv/krw-ontology-data",
                "keep_releases": 5,
                "upload_mode": "delta" if kwargs["delta"] else "full",
                "changed_file_count": 0,
                "removed_file_count": 0,
            }

        monkeypatch.setattr(cli_main, "_publish_prod_root", fake_publish_prod_root)

        result = runner.invoke(app, ["prod", "publish-dev"])

        assert result.exit_code == 0, result.output
        assert calls
        assert calls[0]["stable_root"] == release_root.resolve()
        assert calls[0]["delta"] is True

    def test_quality_check_uses_configured_release_current_by_default(self, tmp_path: Path):
        releases_root = tmp_path / "releases"
        release_root = releases_root / "dev" / "ready"
        _write_minimal_v3_release(release_root, release_id="ready", env="dev")
        (releases_root / "dev" / "current").symlink_to("ready")
        runner.invoke(app, ["config", "set", "publish-root", str(releases_root)])

        result = runner.invoke(app, ["quality", "check", "--min-docs", "1"])

        assert result.exit_code == 0, result.output
        assert "Release: dev/current" in result.output
        assert f"Global spine: {release_root.resolve() / 'indexes' / 'global_spine.sqlite'}" in result.output

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
                    "global_spine_present=yes",
                    "manifest_present=yes",
                    "manifest_format=v3",
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
        assert "Global spine: present" in result.output
        assert "Manifest format: v3" in result.output
        assert "20260515-120000" in result.output

    def test_prod_doctor_warns_on_pre_symlink_current(self, tmp_path: Path, monkeypatch):
        stable = tmp_path / "stable"
        _write_minimal_v3_release(stable, release_id="stable-dev", env="dev")
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
                        "python_bin=/usr/bin/python3",
                        "python_sqlite3=yes",
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
        assert "OK local release format: v3 global-spine-and-company-shards" in result.output
        assert "OK ssh connectivity" in result.output
        assert "OK remote publish commands: tar ln mv rm mkdir ls readlink xargs grep sed" in result.output
        assert "OK remote Python sqlite3: /usr/bin/python3" in result.output
        assert "WARN Remote current is a directory" in result.output
        assert "Prod doctor passed." in result.output
        assert [call[0][0] for call in calls] == ["ssh", "ssh"]

    def test_prod_doctor_rejects_missing_remote_python_sqlite3(self, tmp_path: Path, monkeypatch):
        stable = tmp_path / "stable"
        _write_minimal_v3_release(stable, release_id="stable-dev", env="dev")
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
            stdout = ""

        def fake_run(command, *, input=None, text, capture_output):
            completed = FakeCompleted()
            if input and "python_sqlite3" in input:
                completed.stdout = "\n".join(
                    [
                        "required_commands_missing=",
                        "python_bin=/usr/bin/python3",
                        "python_sqlite3=no",
                        "root_state=exists",
                        "root_writable=yes",
                        "parent_writable=unknown",
                        "current_kind=symlink",
                        "current_target=releases/current-release",
                        "",
                    ]
                )
            return completed

        monkeypatch.setattr(cli_main.subprocess, "run", fake_run)

        result = runner.invoke(app, ["prod", "doctor", "--root", str(stable)])

        assert result.exit_code == 1
        assert "Remote Python sqlite3 unavailable" in result.output


class TestReleaseCommand:
    def test_release_preview_deploy_and_source_manifest_cli(self, tmp_path: Path, monkeypatch):
        source_root = tmp_path / "mutable-root"
        releases_root = tmp_path / "releases"
        artifact_index = source_root / "companies" / "VG" / "context" / "artifact_index.json"
        artifact_index.parent.mkdir(parents=True)
        artifact_index.write_text(
            json.dumps(
                {
                    "ticker": "VG",
                    "document_type": "company-context",
                    "doc_type_key": "company_context",
                    "period": "all",
                    "files": {},
                    "counts": {},
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        manifest_result = runner.invoke(
            app,
            ["source-manifest", "generate", "--root", str(source_root)],
        )
        assert manifest_result.exit_code == 0, manifest_result.output
        assert "Source manifest written:" in manifest_result.output
        verify_result = runner.invoke(
            app,
            ["source-manifest", "verify", "--root", str(source_root)],
        )
        assert verify_result.exit_code == 0, verify_result.output
        assert "Source manifest verify: ok" in verify_result.output

        preview = runner.invoke(
            app,
            [
                "release",
                "preview",
                "--from-root",
                str(source_root),
                "--releases-root",
                str(releases_root),
                "--env",
                "staging",
            ],
        )
        assert preview.exit_code == 0, preview.output
        assert "Release preview" in preview.output
        assert "source_manifest_hash: sha256:" in preview.output
        assert "artifacts: 1" in preview.output

        def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
            _write_minimal_agent_index(index_path or root / "indexes" / "agent_index.sqlite")
            return {
                "index_path": index_path or root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 0, "objects": 0, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)
        deploy = runner.invoke(
            app,
            [
                "release",
                "deploy",
                "--from-root",
                str(source_root),
                "--releases-root",
                str(releases_root),
                "--env",
                "staging",
                "--release-id",
                "deploy-rel",
            ],
        )

        release_root = releases_root / "staging" / "deploy-rel"
        assert deploy.exit_code == 0, deploy.output
        assert "Release deploy completed: env=staging release_id=deploy-rel promoted=True" in deploy.output
        assert (release_root / "indexes" / "source_manifest.json").exists()
        assert os.readlink(releases_root / "staging" / "current") == "deploy-rel"

    def test_release_deploy_and_force_use_configured_roots_and_env(self, tmp_path: Path, monkeypatch):
        source_root = tmp_path / "running"
        releases_root = tmp_path / "releases"
        _write_minimal_source_artifact(source_root, "VG")
        calls: list[dict[str, object]] = []
        runner.invoke(app, ["config", "set", "running-root", str(source_root)])
        runner.invoke(app, ["config", "set", "publish-root", str(releases_root)])
        monkeypatch.setenv("KRW_ONTOLOGY_ENV", "staging")

        def fake_run_full_root_release_command(**kwargs):
            calls.append(kwargs)
            typer_label = kwargs["label"]
            typer_release = kwargs["release_id"]
            print(
                f"Release {typer_label} completed: "
                f"env={kwargs['env']} release_id={typer_release} promoted=True"
            )

        monkeypatch.setattr(cli_main, "_run_full_root_release_command", fake_run_full_root_release_command)

        deploy = runner.invoke(app, ["release", "deploy", "--force", "--release-id", "cfg-deploy"])
        force = runner.invoke(app, ["release", "force", "--release-id", "cfg-force", "--foreground"])

        assert deploy.exit_code == 0, deploy.output
        assert force.exit_code == 0, force.output
        assert calls == [
            {
                "source_root": source_root.resolve(),
                "releases_root": releases_root.resolve(),
                "env": "staging",
                "release_id": "cfg-deploy",
                "promote": True,
                "force_release": True,
                "no_cache": False,
                "label": "deploy",
            },
            {
                "source_root": source_root.resolve(),
                "releases_root": releases_root.resolve(),
                "env": "staging",
                "release_id": "cfg-force",
                "promote": True,
                "force_release": True,
                "no_cache": False,
                "label": "force",
            },
        ]

    def test_release_plan_and_publish_full_root_transaction(self, tmp_path: Path, monkeypatch):
        source_root = tmp_path / "mutable-root"
        releases_root = tmp_path / "releases"
        _write_minimal_source_artifact(source_root, "VG")

        plan = runner.invoke(
            app,
            [
                "release",
                "plan",
                "--from-root",
                str(source_root),
                "--releases-root",
                str(releases_root),
                "--env",
                "staging",
            ],
        )

        assert plan.exit_code == 0, plan.output
        assert "Release plan" in plan.output
        assert "env: staging" in plan.output
        assert "no_op: False" in plan.output
        assert "format: krw-ontology-v3-build-plan/v1" in plan.output
        assert "index_layout: global-spine-and-company-shards" in plan.output
        assert "company_shard:VG" in plan.output
        assert "global_spine_merge" in plan.output
        assert not releases_root.exists()
        json_plan = runner.invoke(
            app,
            [
                "release",
                "plan",
                "--from-root",
                str(source_root),
                "--releases-root",
                str(releases_root),
                "--env",
                "staging",
                "--json",
            ],
        )
        assert json_plan.exit_code == 0, json_plan.output
        json_payload = json.loads(json_plan.output)
        assert json_payload["dag"]["format"] == "krw-ontology-v3-build-plan/v1"
        assert json_payload["dag"]["index_layout"] == "global-spine-and-company-shards"
        assert [node["id"] for node in json_payload["dag"]["nodes"]] == [
            "source_manifest",
            "company_shard:VG",
            "spine_fragment:VG",
            "global_spine_merge",
            "cross_company_links",
            "shard_manifest",
            "release_manifest",
            "verification",
            "promote_current",
        ]
        assert json_payload["dag"]["companies"][0]["ticker"] == "VG"
        assert not releases_root.exists()
        publish = runner.invoke(
            app,
            [
                "release",
                "publish",
                "--from-root",
                str(source_root),
                "--releases-root",
                str(releases_root),
                "--env",
                "staging",
                "--release-id",
                "full-root-rel",
            ],
        )

        release_root = releases_root / "staging" / "full-root-rel"
        assert publish.exit_code == 0, publish.output
        assert "Release publish completed: env=staging release_id=full-root-rel promoted=True" in publish.output
        assert os.readlink(releases_root / "staging" / "current") == "full-root-rel"
        assert (release_root / "manifest.json").exists()
        assert (release_root / "indexes" / "global_spine.sqlite").exists()
        assert (release_root / "indexes" / "shard_manifest.json").exists()
        assert not (release_root / "indexes" / "agent_index.sqlite").exists()
        assert (release_root / "verify" / "release_verify.json").exists()

    def test_release_force_replaces_legacy_current_with_new_v3_release(self, tmp_path: Path):
        source_root = tmp_path / "mutable-root"
        releases_root = tmp_path / "releases"
        _write_minimal_source_artifact(source_root, "VG")

        env_root = releases_root / "dev"
        legacy_release = env_root / "old-v1"
        legacy_release.mkdir(parents=True)
        (legacy_release / "manifest.json").write_text(
            json.dumps(
                {
                    "format": "krw-ontology-release/v1",
                    "env": "dev",
                    "release_id": "old-v1",
                    "index_path": "indexes/agent_index.sqlite",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (env_root / "current").symlink_to("old-v1")

        result = runner.invoke(
            app,
            [
                "release",
                "force",
                "--from-root",
                str(source_root),
                "--releases-root",
                str(releases_root),
                "--env",
                "dev",
                "--release-id",
                "new-v3",
                "--foreground",
            ],
        )

        assert result.exit_code == 0, result.output
        assert os.readlink(env_root / "current") == "new-v3"
        manifest = json.loads((env_root / "new-v3" / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["format"] == "krw-ontology-release/v3"
        assert manifest["status"] == "ready"

    def test_release_build_and_import_current_have_distinct_promotion_semantics(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        source_root = tmp_path / "mutable-root"
        releases_root = tmp_path / "releases"
        _write_minimal_source_artifact(source_root, "VG")
        build = runner.invoke(
            app,
            [
                "release",
                "build",
                "--from-root",
                str(source_root),
                "--releases-root",
                str(releases_root),
                "--release-id",
                "built-only",
                "--force-release",
            ],
        )

        assert build.exit_code == 0, build.output
        assert "promoted=False" in build.output
        assert (releases_root / "dev" / "built-only").is_dir()
        assert not (releases_root / "dev" / "current").exists()

        imported = runner.invoke(
            app,
            [
                "release",
                "import-current",
                "--from-root",
                str(source_root),
                "--releases-root",
                str(releases_root),
                "--release-id",
                "imported",
            ],
        )

        assert imported.exit_code == 0, imported.output
        assert "promoted=True" in imported.output
        assert os.readlink(releases_root / "dev" / "current") == "imported"

    def test_release_list_inspect_and_gc_protect_current(self, tmp_path: Path):
        releases_root = tmp_path / "releases"
        env_root = releases_root / "prod"
        old = env_root / "old"
        current_release = env_root / "current-release"
        newest = env_root / "newest"
        for position, release_root in enumerate((old, current_release, newest), start=1):
            _write_minimal_v3_release(release_root, release_id=release_root.name, env="prod")
            os.utime(release_root, (position, position))
        (env_root / "current").symlink_to(current_release.name)
        (env_root / "locks").mkdir()
        (env_root / "events").mkdir()
        failed = env_root / "failed" / "failed-one"
        failed.mkdir(parents=True)
        (failed / "failure.json").write_text(
            json.dumps({"action": "publish_root", "error": "boom"}, sort_keys=True),
            encoding="utf-8",
        )

        listed = runner.invoke(
            app,
            ["release", "list", "--releases-root", str(releases_root), "--env", "prod", "--include-failed", "--json"],
        )

        assert listed.exit_code == 0, listed.output
        payload = json.loads(listed.output)
        assert payload["current"] == current_release.name
        assert payload["releases"] == ["newest", "current-release", "old"]
        assert payload["failed"] == ["failed-one"]
        assert "locks" not in payload["releases"]
        assert "events" not in payload["releases"]
        assert "failed" not in payload["releases"]

        inspected = runner.invoke(
            app,
            [
                "release",
                "inspect",
                "failed-one",
                "--releases-root",
                str(releases_root),
                "--env",
                "prod",
                "--failed",
                "--json",
            ],
        )
        assert inspected.exit_code == 0, inspected.output
        inspected_payload = json.loads(inspected.output)
        assert inspected_payload["failed"] is True
        assert inspected_payload["failure"]["error"] == "boom"

        dry_run = runner.invoke(
            app,
            [
                "release",
                "gc",
                "--releases-root",
                str(releases_root),
                "--env",
                "prod",
                "--keep",
                "1",
                "--include-failed",
            ],
        )
        assert dry_run.exit_code == 0, dry_run.output
        assert "Release GC: dry-run" in dry_run.output
        assert old.exists()
        assert current_release.exists()
        assert newest.exists()
        assert failed.exists()

        deleted = runner.invoke(
            app,
            [
                "release",
                "gc",
                "--releases-root",
                str(releases_root),
                "--env",
                "prod",
                "--keep",
                "1",
                "--include-failed",
                "--yes",
            ],
        )
        assert deleted.exit_code == 0, deleted.output
        assert not old.exists()
        assert current_release.exists()
        assert newest.exists()
        assert not failed.exists()
        assert (env_root / "events").exists()
        assert os.readlink(env_root / "current") == current_release.name

    def test_release_manifest_verify_promote_and_rollback(self, tmp_path: Path):
        releases_root = tmp_path / "releases"
        first = releases_root / "prod" / "20260528_010000"
        second = releases_root / "prod" / "20260528_020000"
        for release_dir, release_id in ((first, "20260528_010000"), (second, "20260528_020000")):
            _write_minimal_v3_release(release_dir, release_id=release_id, env="prod")
            (release_dir / "manifest.json").unlink()

        first_manifest = runner.invoke(
            app,
            [
                "release",
                "write-manifest",
                "--root",
                str(first),
                "--env",
                "prod",
                "--release-id",
                "20260528_010000",
            ],
        )
        second_manifest = runner.invoke(
            app,
            [
                "release",
                "write-manifest",
                "--root",
                str(second),
                "--env",
                "prod",
                "--release-id",
                "20260528_020000",
            ],
        )

        assert first_manifest.exit_code == 0
        assert second_manifest.exit_code == 0
        manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["format"] == "krw-ontology-release/v3"
        assert manifest["env"] == "prod"
        assert manifest["release_id"] == "20260528_010000"
        assert manifest["index_layout"] == "global-spine-and-company-shards"
        assert manifest["monolith_required"] is False
        assert manifest["indexes"]["global_spine"]["path"] == "indexes/global_spine.sqlite"
        assert manifest["indexes"]["company_shards"]["required"] is True

        verify_result = runner.invoke(
            app,
            ["release", "verify", "--root", str(first), "--env", "prod"],
        )
        assert verify_result.exit_code == 0
        assert "Release verify: ok" in verify_result.output
        assert "index_layout: global-spine-and-company-shards" in verify_result.output

        promote_first = runner.invoke(
            app,
            [
                "release",
                "promote",
                "20260528_010000",
                "--releases-root",
                str(releases_root),
                "--env",
                "prod",
            ],
        )
        assert promote_first.exit_code == 0
        assert (releases_root / "prod" / "current").is_symlink()
        assert os.readlink(releases_root / "prod" / "current") == "20260528_010000"
        assert "event_log:" in promote_first.output
        assert "release_event_log:" in promote_first.output
        first_release_bytes = {
            path.relative_to(first).as_posix(): path.read_bytes()
            for path in first.rglob("*")
            if path.is_file()
        }

        verify_current = runner.invoke(
            app,
            [
                "release",
                "verify",
                "--root",
                str(releases_root / "prod" / "current"),
                "--env",
                "prod",
                "--require-current-symlink",
            ],
        )
        assert verify_current.exit_code == 0

        promote_second = runner.invoke(
            app,
            [
                "release",
                "promote",
                "20260528_020000",
                "--releases-root",
                str(releases_root),
                "--env",
                "prod",
            ],
        )
        assert promote_second.exit_code == 0
        assert os.readlink(releases_root / "prod" / "current") == "20260528_020000"

        rollback = runner.invoke(
            app,
            ["release", "rollback", "--releases-root", str(releases_root), "--env", "prod"],
        )
        assert rollback.exit_code == 0
        assert "release_id=20260528_010000" in rollback.output
        assert os.readlink(releases_root / "prod" / "current") == "20260528_010000"
        assert (first / "verify" / "release_verify.json").exists()
        assert {
            path.relative_to(first).as_posix(): path.read_bytes()
            for path in first.rglob("*")
            if path.is_file()
        } == first_release_bytes
        env_events = [
            json.loads(line)
            for line in (releases_root / "prod" / "release_events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert [event["action"] for event in env_events] == ["promote", "promote", "rollback"]
        assert [event["release_id"] for event in env_events] == [
            "20260528_010000",
            "20260528_020000",
            "20260528_010000",
        ]
        assert env_events[0]["previous_release_id"] is None
        assert env_events[1]["previous_release_id"] == "20260528_010000"
        assert env_events[2]["previous_release_id"] == "20260528_020000"
        assert all(event["event"] == "release_changed" for event in env_events)
        assert all(event["verification_ok"] is True for event in env_events)
        first_release_events = [
            json.loads(line)
            for line in (releases_root / "prod" / "events" / f"{first.name}.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        second_release_events = [
            json.loads(line)
            for line in (releases_root / "prod" / "events" / f"{second.name}.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [event["action"] for event in first_release_events] == ["promote", "rollback"]
        assert [event["action"] for event in second_release_events] == ["promote"]
        assert not (first / "verify" / "release_events.jsonl").exists()
        assert not (second / "verify" / "release_events.jsonl").exists()
        inspected = runner.invoke(
            app,
            [
                "release",
                "inspect",
                first.name,
                "--releases-root",
                str(releases_root),
                "--env",
                "prod",
                "--json",
            ],
        )
        assert inspected.exit_code == 0, inspected.output
        assert [event["action"] for event in json.loads(inspected.output)["events"]] == ["promote", "rollback"]

    def test_release_promote_event_log_preflight_failure_preserves_current(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        releases_root = tmp_path / "releases"
        env_root = releases_root / "prod"
        old = env_root / "old-release"
        target = env_root / "new-release"
        for release_dir, release_id in ((old, "old-release"), (target, "new-release")):
            _write_minimal_v3_release(release_dir, release_id=release_id, env="prod")
        current = env_root / "current"
        current.symlink_to(old.name)

        def fail_prepare(_paths):
            raise OSError("event log path unwritable")

        monkeypatch.setattr(release_helpers, "_prepare_release_event_log_paths", fail_prepare)

        with pytest.raises(OSError, match="event log path unwritable"):
            release_helpers.promote_local_release(releases_root, env="prod", release_id=target.name)

        assert os.readlink(current) == old.name

    def test_release_promote_event_log_append_failure_is_reported_after_switch(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        releases_root = tmp_path / "releases"
        target = releases_root / "prod" / "new-release"
        _write_minimal_v3_release(target, release_id=target.name, env="prod")

        def fail_append(_path, _payload):
            raise OSError("append failed")

        monkeypatch.setattr(release_helpers, "_append_release_event", fail_append)

        result = release_helpers.promote_local_release(releases_root, env="prod", release_id=target.name)

        assert os.readlink(releases_root / "prod" / "current") == target.name
        assert result["verify_report"] == str(target / "verify" / "release_verify.json")
        assert (target / "verify" / "release_verify.json").exists()
        assert len(result["event_log_errors"]) == 2
        assert all("append failed" in error for error in result["event_log_errors"])

    def test_release_promote_runs_local_reload_and_health_hooks(self, tmp_path: Path, monkeypatch):
        releases_root = tmp_path / "releases"
        target = releases_root / "prod" / "new-release"
        _write_minimal_v3_release(target, release_id=target.name, env="prod")
        calls: list[tuple[str, object]] = []

        def fake_reload(command: str) -> None:
            calls.append(("reload", command))

        def fake_health(**kwargs) -> None:
            calls.append(("health", kwargs))

        monkeypatch.setattr(cli_main, "_run_local_reload_command", fake_reload)
        monkeypatch.setattr(cli_main, "_check_local_release_health", fake_health)

        result = runner.invoke(
            app,
            [
                "release",
                "promote",
                target.name,
                "--releases-root",
                str(releases_root),
                "--env",
                "prod",
                "--reload-command",
                "systemctl reload krw-ontology-mcp",
                "--health-url",
                "http://127.0.0.1:8000/health",
                "--health-timeout",
                "2.5",
            ],
        )

        assert result.exit_code == 0
        assert os.readlink(releases_root / "prod" / "current") == target.name
        assert calls[0] == ("reload", "systemctl reload krw-ontology-mcp")
        assert calls[1][0] == "health"
        assert calls[1][1]["expected_release_id"] == target.name
        assert calls[1][1]["timeout"] == 2.5
        assert "reload: ok" in result.output
        assert "health: ok http://127.0.0.1:8000/health" in result.output

    def test_release_rollback_runs_local_reload_and_health_hooks(self, tmp_path: Path, monkeypatch):
        releases_root = tmp_path / "releases"
        env_root = releases_root / "prod"
        old = env_root / "old-release"
        target = env_root / "new-release"
        for release_dir, release_id in ((old, "old-release"), (target, "new-release")):
            _write_minimal_v3_release(release_dir, release_id=release_id, env="prod")
        current = env_root / "current"
        current.symlink_to(target.name)
        calls: list[tuple[str, object]] = []

        def fake_reload(command: str) -> None:
            calls.append(("reload", command))

        def fake_health(**kwargs) -> None:
            calls.append(("health", kwargs))

        monkeypatch.setattr(cli_main, "_run_local_reload_command", fake_reload)
        monkeypatch.setattr(cli_main, "_check_local_release_health", fake_health)

        result = runner.invoke(
            app,
            [
                "release",
                "rollback",
                old.name,
                "--releases-root",
                str(releases_root),
                "--env",
                "prod",
                "--reload-command",
                "systemctl reload krw-ontology-mcp",
                "--health-url",
                "http://127.0.0.1:8000/health",
            ],
        )

        assert result.exit_code == 0
        assert os.readlink(current) == old.name
        assert calls[0] == ("reload", "systemctl reload krw-ontology-mcp")
        assert calls[1][0] == "health"
        assert calls[1][1]["expected_release_id"] == old.name
        assert "release_id=old-release" in result.output

    def test_release_promote_hook_failure_restores_previous_current(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        releases_root = tmp_path / "releases"
        env_root = releases_root / "prod"
        old = env_root / "old-release"
        target = env_root / "new-release"
        for release_dir, release_id in ((old, "old-release"), (target, "new-release")):
            _write_minimal_v3_release(release_dir, release_id=release_id, env="prod")
        current = env_root / "current"
        current.symlink_to(old.name)
        reload_calls: list[str] = []

        def flaky_reload(_command: str) -> None:
            reload_calls.append(os.readlink(current))
            if len(reload_calls) == 1:
                raise RuntimeError("reload failed once")

        monkeypatch.setattr(cli_main, "_run_local_reload_command", flaky_reload)

        result = runner.invoke(
            app,
            [
                "release",
                "promote",
                target.name,
                "--releases-root",
                str(releases_root),
                "--env",
                "prod",
                "--reload-command",
                "systemctl reload krw-ontology-mcp",
            ],
        )

        assert result.exit_code == 1
        assert os.readlink(current) == old.name
        assert reload_calls == [target.name, old.name]
        assert "restored previous release old-release" in result.output

    def test_release_promote_verification_report_failure_preserves_current(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        releases_root = tmp_path / "releases"
        env_root = releases_root / "prod"
        old = env_root / "old-release"
        target = env_root / "new-release"
        for release_dir, release_id in ((old, "old-release"), (target, "new-release")):
            _write_minimal_v3_release(release_dir, release_id=release_id, env="prod")
        current = env_root / "current"
        current.symlink_to(old.name)

        def fail_report(*_args, **_kwargs):
            return {"ok": False, "errors": ["report_failed"], "path": str(target / "verify" / "release_verify.json")}

        monkeypatch.setattr(release_helpers, "write_release_verification_report", fail_report)

        with pytest.raises(ValueError, match="Release verification report failed: report_failed"):
            release_helpers.promote_local_release(releases_root, env="prod", release_id=target.name)

        assert os.readlink(current) == old.name

    def test_release_verify_rejects_invalid_global_spine(self, tmp_path: Path):
        release_root = tmp_path / "releases" / "prod" / "bad"
        _write_minimal_v3_release(release_root, release_id="bad", env="prod")
        (release_root / "indexes" / "global_spine.sqlite").write_text("not-a-real-db")

        result = runner.invoke(
            app,
            ["release", "verify", "--root", str(release_root), "--env", "prod"],
        )

        assert result.exit_code == 1
        assert "global_spine:sqlite_error:file is not a database" in result.output

    def test_release_verify_rejects_filesystem_gate_violations(self, tmp_path: Path):
        release_root = tmp_path / "releases" / "prod" / "fs-bad"
        _write_minimal_v3_release(release_root, release_id="fs-bad", env="prod")
        (release_root / "indexes" / ".build").mkdir()
        (release_root / "indexes" / "global_spine.sqlite.tmp").write_text("stale", encoding="utf-8")
        (release_root / "companies" / "broken-link").symlink_to("missing-target")

        result = runner.invoke(
            app,
            ["release", "verify", "--root", str(release_root), "--env", "prod"],
        )

        assert result.exit_code == 1
        assert "release_temp_artifact:indexes/.build" in result.output
        assert "release_temp_artifact:indexes/global_spine.sqlite.tmp" in result.output
        assert "broken_symlink:companies/broken-link" in result.output

    def test_release_verify_rejects_non_v3_before_other_manifest_validation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        release_root = tmp_path / "releases" / "prod" / "manifest-bad"
        _write_minimal_v3_release(release_root, release_id=release_root.name, env="prod")
        manifest_path = release_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["format"] = "unsupported"
        manifest["status"] = "building"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

        def fail_connect(*_args, **_kwargs):
            raise AssertionError("non-v3 release must be rejected before opening SQLite")

        monkeypatch.setattr("krw_ontology.release.sqlite3.connect", fail_connect)
        result = runner.invoke(
            app,
            ["release", "verify", "--root", str(release_root), "--env", "prod"],
        )

        assert result.exit_code == 1
        assert "manifest_format_unsupported" in result.output

    def test_release_verify_startup_check_rejects_legacy_v1_without_sqlite_open(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        release_root = tmp_path / "releases" / "prod" / "legacy-v1"
        index_path = release_root / "indexes" / "agent_index.sqlite"
        index_path.parent.mkdir(parents=True)
        index_path.write_bytes(b"not a sqlite database")
        (release_root / "manifest.json").write_text(
            json.dumps(
                {
                    "format": "krw-ontology-release/v1",
                    "env": "prod",
                    "release_id": "legacy-v1",
                    "index_path": "indexes/agent_index.sqlite",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        def fail_connect(*_args, **_kwargs):
            raise AssertionError("startup check must reject v1 before opening SQLite")

        monkeypatch.setattr("krw_ontology.release.sqlite3.connect", fail_connect)

        result = runner.invoke(
            app,
            ["release", "verify", "--startup-check", "--root", str(release_root), "--env", "prod"],
        )

        assert result.exit_code == 1, result.output
        assert "Release verify: failed" in result.output
        assert "mode: startup" in result.output
        assert "manifest_format_unsupported" in result.output

    def test_release_startup_check_uses_configured_dev_current_by_default(self, tmp_path: Path):
        releases_root = tmp_path / "releases"
        release_root = releases_root / "dev" / "ready"
        _write_minimal_v3_release(release_root, release_id="ready", env="dev")
        (releases_root / "dev" / "current").symlink_to("ready")
        runner.invoke(app, ["config", "set", "publish-root", str(releases_root)])

        result = runner.invoke(app, ["release", "startup-check"])

        assert result.exit_code == 0, result.output
        assert "Release startup-check: ok" in result.output
        assert "release_id: ready" in result.output
        assert "index_layout: global-spine-and-company-shards" in result.output
        assert "global_spine: present" in result.output
        assert f"root: {release_root.resolve()}" in result.output

    def test_release_verify_rejects_global_spine_digest_mismatch(self, tmp_path: Path):
        release_root = tmp_path / "releases" / "prod" / "digest-bad"
        _write_minimal_v3_release(release_root, release_id="digest-bad", env="prod")
        with sqlite3.connect(release_root / "indexes" / "global_spine.sqlite") as conn:
            conn.execute(
                "INSERT INTO metadata(key, value_json) VALUES('post_manifest_change', '\"changed\"')"
            )

        result = runner.invoke(
            app,
            ["release", "verify", "--root", str(release_root), "--env", "prod"],
        )

        assert result.exit_code == 1
        assert "global_spine_sha256_mismatch" in result.output

    def test_release_verify_rejects_global_spine_path_outside_root(self, tmp_path: Path):
        release_root = tmp_path / "releases" / "prod" / "nested-output-bad"
        _write_minimal_v3_release(release_root, release_id=release_root.name, env="prod")
        manifest_path = release_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["indexes"]["global_spine"]["path"] = "../outside.sqlite"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["release", "verify", "--root", str(release_root), "--env", "prod"],
        )

        assert result.exit_code == 1
        assert "manifest_indexes_global_spine_path_outside_root" in result.output

    def test_release_verify_rejects_legacy_v1_manifest_without_sqlite_open(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        release_root = tmp_path / "releases" / "prod" / "legacy-v1"
        index_path = release_root / "indexes" / "agent_index.sqlite"
        index_path.parent.mkdir(parents=True)
        (release_root / "companies").mkdir(parents=True)
        index_path.write_bytes(b"not a sqlite database")
        (release_root / "manifest.json").write_text(
            json.dumps(
                {
                    "format": "krw-ontology-release/v1",
                    "env": "prod",
                    "release_id": release_root.name,
                    "index_path": "indexes/agent_index.sqlite",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        def fail_connect(*_args, **_kwargs):
            raise AssertionError("legacy manifest rejection must not open SQLite")

        monkeypatch.setattr("krw_ontology.release.sqlite3.connect", fail_connect)
        result = runner.invoke(
            app,
            ["release", "verify", "--root", str(release_root), "--env", "prod"],
        )

        assert result.exit_code == 1
        assert "FAIL manifest_format_unsupported" in result.output

    def test_release_promote_rejects_missing_company_shard(self, tmp_path: Path):
        releases_root = tmp_path / "releases"
        release_root = releases_root / "prod" / "missing-shard"
        _write_minimal_v3_release(release_root, release_id=release_root.name, env="prod")
        (release_root / "indexes" / "companies" / "AAPL.sqlite").unlink()

        result = runner.invoke(
            app,
            ["release", "promote", release_root.name, "--releases-root", str(releases_root), "--env", "prod"],
        )

        report_path = release_root / "verify" / "release_verify.json"
        assert result.exit_code == 1
        assert "FAILED release promote" in result.output
        assert "shard:AAPL:shard_missing" in result.output
        assert not (releases_root / "prod" / "current").exists()
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["ok"] is False
        assert any("shard:AAPL:shard_missing" in error for error in report["errors"])

    def test_release_verify_writes_report_with_stable_reproducibility_hash(self, tmp_path: Path):
        release_root = tmp_path / "releases" / "prod" / "rel-report"
        _write_minimal_v3_release(release_root, release_id="rel-report", env="prod", ticker="CVX")

        result = runner.invoke(
            app,
            ["release", "verify", "--root", str(release_root), "--env", "prod", "--write-report"],
        )

        report_path = release_root / "verify" / "release_verify.json"
        assert result.exit_code == 0, result.output
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["format"] == "krw-ontology-release-verify/v1"
        assert report["ok"] is True
        assert report["global_spine_path"] == "indexes/global_spine.sqlite"
        assert report["verification"]["verification_mode"] == "release-root-v3"
        assert report["verification"]["smoke_verification"] is None
        assert "smoke_queries_path" not in report
        assert "ranking_quality_path" not in report
        assert len(report["reproducibility_hash"]) == 64
        files_by_path = {item["path"]: item for item in report["files"]}
        assert files_by_path["manifest.json"]["role"] == "release_manifest"
        assert files_by_path["indexes/global_spine.sqlite"]["role"] == "global_spine"
        assert files_by_path["indexes/companies/CVX.sqlite"]["role"] == "company_shard"
        assert all(not item["path"].startswith("verify/") for item in report["files"])

        second = runner.invoke(
            app,
            ["release", "verify", "--root", str(release_root), "--env", "prod", "--write-report"],
        )
        second_report = json.loads(report_path.read_text())
        assert second.exit_code == 0, second.output
        assert second_report["reproducibility_hash"] == report["reproducibility_hash"]
        assert second_report["verification"]["verification_mode"] == "release-root-v3"

    def test_release_verify_rejects_removed_smoke_baseline_options(self, tmp_path: Path):
        release_root = tmp_path / "releases" / "prod" / "rel-baseline"
        _write_minimal_v3_release(release_root, release_id="rel-baseline", env="prod")

        update = runner.invoke(
            app,
            [
                "release",
                "verify",
                "--root",
                str(release_root),
                "--env",
                "prod",
                "--update-smoke-baseline",
            ],
        )

        assert update.exit_code == 2
        assert "No such option" in update.output
        assert "--update-smoke-baseline" in update.output

    def test_release_verify_v3_write_report_does_not_emit_legacy_smoke_reports(self, tmp_path: Path):
        release_root = tmp_path / "releases" / "prod" / "rel-mcp-smoke"
        _write_minimal_v3_release(release_root, release_id="rel-mcp-smoke", env="prod")

        result = runner.invoke(
            app,
            ["release", "verify", "--root", str(release_root), "--env", "prod", "--write-report"],
        )

        report = json.loads((release_root / "verify" / "release_verify.json").read_text())
        assert result.exit_code == 0, result.output
        assert "index_layout: global-spine-and-company-shards" in result.output
        assert not (release_root / "verify" / "smoke_queries.json").exists()
        assert not (release_root / "verify" / "ranking_quality.json").exists()
        assert report["verification"]["verification_mode"] == "release-root-v3"
        assert report["verification"]["spine_shard_verification"]["ok"] is True

    def test_release_verify_help_hides_legacy_ranking_threshold_options(self):
        result = runner.invoke(app, ["release", "verify", "--help"])

        assert result.exit_code == 0, result.output
        assert "--ranking-top-k" not in result.output
        assert "--ranking-min-overlap-ratio" not in result.output
        assert "router/monolith" not in result.output
        assert "--index-path" not in result.output

    def test_release_verify_rejects_removed_ranking_threshold_option(self, tmp_path: Path):
        result = runner.invoke(
            app,
            [
                "release",
                "verify",
                "--root",
                str(tmp_path),
                "--ranking-min-overlap-ratio",
                "1.2",
            ],
        )

        assert result.exit_code == 2
        assert "No such option" in result.output
        assert "--ranking-min-overlap-ratio" in result.output

    def test_release_export_web_catalog_is_read_only_and_compact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from tests.unit.test_mcp_server import _write_fixture

        _write_fixture(tmp_path)
        agent_index.build_spine_shard_release_outputs(
            tmp_path,
            release_id="20260528_030000",
            workers=1,
            no_cache=True,
        )
        write_release_manifest_v3(tmp_path, release_id="20260528_030000", env="prod", source_root=tmp_path)
        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["format"] == "krw-ontology-release/v3"
        assert manifest["index_layout"] == "global-spine-and-company-shards"

        def fail_if_rebuilt(*args, **kwargs):
            raise AssertionError("export-web-catalog must not rebuild agent index")

        monkeypatch.setattr(agent_index_builder, "build_agent_index", fail_if_rebuilt)
        out_path = tmp_path / "web_catalog.json"

        result = runner.invoke(
            app,
            [
                "release",
                "export-web-catalog",
                "--root",
                str(tmp_path),
                "--env",
                "prod",
                "--out",
                str(out_path),
            ],
        )

        assert result.exit_code == 0
        assert "Web catalog exported" in result.output
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["format"] == "krw-ontology-web-catalog/v1"
        assert payload["env"] == "prod"
        assert payload["release_id"] == "20260528_030000"
        assert payload["release"]["index_layout"] == "global-spine-and-company-shards"
        assert payload["summary"]["company_count"] == 1
        assert payload["summary"]["document_count"] == 1
        assert payload["summary"]["index_document_count"] == 2
        assert payload["summary"]["object_count"] > 0

        company = payload["companies"][0]
        assert company["ticker"] == "VG"
        assert company["latest_period"] == "FY2025"
        assert company["sector"] == "energy_lng"
        assert company["description"] == "Primary activities: LNG sales. Key external factors: natural_gas_price."
        assert company["coverage"]["document_count"] == 1
        assert company["coverage"]["document_types"] == ["10-K"]
        assert company["coverage"]["periods"] == ["FY2025"]
        assert company["coverage"]["object_counts"]["CompanyBusinessProfile"] == 1
        assert len(company["documents"]) == 1

    def test_release_export_web_catalog_fails_on_env_mismatch(self, tmp_path: Path):
        from tests.unit.test_mcp_server import _write_fixture

        _write_fixture(tmp_path)
        agent_index.build_spine_shard_release_outputs(
            tmp_path,
            release_id="20260528_040000",
            workers=1,
            no_cache=True,
        )
        write_release_manifest_v3(tmp_path, release_id="20260528_040000", env="staging", source_root=tmp_path)
        out_path = tmp_path / "web_catalog.json"

        result = runner.invoke(
            app,
            [
                "release",
                "export-web-catalog",
                "--root",
                str(tmp_path),
                "--env",
                "prod",
                "--out",
                str(out_path),
            ],
        )

        assert result.exit_code == 1
        assert "manifest_env_mismatch" in result.output
        assert not out_path.exists()

    def test_release_prepare_dev_sets_config_and_retargets_pending_queue(self, tmp_path: Path):
        releases_root = tmp_path / "releases"
        base = releases_root / "dev" / "base"
        _write_minimal_v3_release(base, release_id="base", env="dev", ticker="VG", artifact_text="base")
        os.symlink("base", releases_root / "dev" / "current")

        queue_root = tmp_path / "running"
        old_publish_root = tmp_path / "old-publish-root"
        store = pipeline_queue.PipelineQueue(queue_root)
        job = store.add_job(
            "nflx",
            years=1,
            force=False,
            publish_root=old_publish_root,
        )

        result = runner.invoke(
            app,
            [
                "release",
                "prepare-dev",
                "20260528_050000",
                "--releases-root",
                str(releases_root),
                "--queue-root",
                str(queue_root),
            ],
        )

        assert result.exit_code == 0
        release_root = releases_root / "dev" / "20260528_050000"
        assert (release_root / "companies" / "VG" / "artifact.txt").read_text() == "base"
        assert not (release_root / "manifest.json").exists()
        assert not (release_root / "indexes").exists()
        config = load_cli_config()
        assert config.publish_root == str(release_root.resolve())
        retargeted = store.load_job(job.job_id)
        assert retargeted.publish_root == str(release_root.resolve())
        assert "queue_jobs_changed: 1" in result.output

    def test_release_prepare_dev_refuses_running_queue_without_override(self, tmp_path: Path, monkeypatch):
        releases_root = tmp_path / "releases"
        queue_root = tmp_path / "running"
        store = pipeline_queue.PipelineQueue(queue_root)
        store.write_worker_pid(12345)
        monkeypatch.setattr(pipeline_queue, "is_pid_running", lambda pid: pid == 12345)

        result = runner.invoke(
            app,
            [
                "release",
                "prepare-dev",
                "20260528_060000",
                "--releases-root",
                str(releases_root),
                "--queue-root",
                str(queue_root),
                "--empty",
            ],
        )

        assert result.exit_code == 1
        assert "Queue worker is running" in result.output
        assert not (releases_root / "dev" / "20260528_060000").exists()
        assert load_cli_config().publish_root is None

    def test_release_finalize_dev_rebuilds_manifest_promotes_and_clears_config(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        releases_root = tmp_path / "releases"
        release_root = releases_root / "dev" / "20260528_070000"
        _write_minimal_source_artifact(release_root, "CVX")
        runner.invoke(app, ["config", "set", "publish-root", str(release_root)])

        result = runner.invoke(
            app,
            ["release", "finalize-dev", "--releases-root", str(releases_root)],
        )

        assert result.exit_code == 0
        assert os.readlink(releases_root / "dev" / "current") == "20260528_070000"
        manifest = json.loads((release_root / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["release_id"] == "20260528_070000"
        assert manifest["env"] == "dev"
        assert manifest["format"] == "krw-ontology-release/v3"
        assert manifest["index_layout"] == "global-spine-and-company-shards"
        assert (release_root / "indexes" / "global_spine.sqlite").exists()
        assert not (release_root / "indexes" / "agent_index.sqlite").exists()
        config = load_cli_config()
        assert config.publish_root is None
        assert "Dev release finalized: 20260528_070000" in result.output
        assert "V3 indexes built:" in result.output

    def test_release_materialize_prod_copies_dev_release_and_rewrites_manifest(self, tmp_path: Path):
        releases_root = tmp_path / "releases"
        source = releases_root / "dev" / "20260528_080000"
        _write_minimal_v3_release(source, release_id="20260528_080000", env="dev", ticker="VG")

        result = runner.invoke(
            app,
            [
                "release",
                "materialize-prod",
                "20260528_080000",
                "--releases-root",
                str(releases_root),
            ],
        )

        assert result.exit_code == 0
        prod = releases_root / "prod" / "20260528_080000"
        manifest = json.loads((prod / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["format"] == "krw-ontology-release/v3"
        assert manifest["release_id"] == "20260528_080000"
        assert manifest["env"] == "prod"
        assert manifest["index_layout"] == "global-spine-and-company-shards"
        assert (prod / "indexes" / "global_spine.sqlite").exists()
        assert (prod / "indexes" / "companies" / "VG.sqlite").exists()
        assert not (prod / "indexes" / "agent_index.sqlite").exists()
        assert not (releases_root / "prod" / "current").exists()
        assert "krw-ontology release promote 20260528_080000" in result.output


class TestQueueCommands:
    def _prod_current(self, tmp_path: Path) -> Path:
        prod_root = tmp_path / "releases" / "prod"
        release = prod_root / "20260528_150000"
        release.mkdir(parents=True)
        current = prod_root / "current"
        os.symlink(release.name, current)
        return current

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

    def test_queue_add_rejects_prod_current_root(self, tmp_path: Path):
        current = self._prod_current(tmp_path)

        result = runner.invoke(app, ["queue-add", "cvx", "--root", str(current)])

        assert result.exit_code == 1
        assert "prod/current is an immutable release pointer" in result.output
        assert not (current / ".krw_pipeline").exists()

    def test_queue_add_rejects_prod_current_publish_root(self, tmp_path: Path):
        current = self._prod_current(tmp_path)

        result = runner.invoke(
            app,
            [
                "queue-add",
                "cvx",
                "--root",
                str(tmp_path / "running"),
                "--publish-root",
                str(current),
            ],
        )

        assert result.exit_code == 1
        assert "prod/current is an immutable release pointer" in result.output

    def test_queue_refresh_rejects_active_prod_release_target(self, tmp_path: Path):
        current = self._prod_current(tmp_path)
        release_target = current.resolve()

        with pytest.raises(ValueError, match="prod/current is an immutable release pointer"):
            cli_main._queue_refresh_pending_indexes(
                refresh_targets={"prod": release_target},
                publish_prod=False,
                refresh_index_func=lambda *args, **kwargs: None,
            )

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

    def test_queue_retarget_publish_updates_pending_jobs_only(self, tmp_path: Path):
        queue_root = tmp_path / "running"
        old_publish_root = tmp_path / "old-publish-root"
        release_root = tmp_path / "releases" / "dev" / "20260528_090000"
        store = pipeline_queue.PipelineQueue(queue_root)
        pending = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=old_publish_root,
        )
        running = store.add_job(
            "xom",
            years=1,
            force=False,
            publish_root=old_publish_root,
        )
        store.mark_running(running)

        result = runner.invoke(
            app,
            [
                "queue",
                "retarget-publish",
                "--root",
                str(queue_root),
                "--publish-root",
                str(release_root),
            ],
        )

        assert result.exit_code == 0
        assert store.load_job(pending.job_id).publish_root == str(release_root.resolve())
        assert store.load_job(running.job_id).publish_root == str(old_publish_root)
        assert "changed=1 scanned=1" in result.output

    def test_queue_add_skips_active_duplicate(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        first = store.add_job(
            "CVX",
            years=3,
            force=False,
            publish_root=None,
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

        def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
            events.append(("index", root, index_path, force))
            _write_minimal_agent_index(index_path or root / "indexes" / "agent_index.sqlite")
            return {
                "index_path": index_path or root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 1, "objects": 2, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            ["queue-run", "--root", str(tmp_path), "--max-jobs", "1"],
        )

        assert result.exit_code == 0
        assert store.load_job(job.job_id).status == pipeline_queue.SUCCEEDED
        current = stable.resolve() / "dev" / "current"
        release_root = current.resolve()
        assert (release_root / "companies" / "CVX" / "artifact.txt").read_text() == "FY2025"
        assert events[:3] == [
            ("plan", "CVX", 1),
            ("pipeline", "CVX", "FY2025"),
            ("context", "CVX"),
        ]
        assert (release_root / "indexes" / "global_spine.sqlite").exists()
        assert (release_root / "indexes" / "companies" / "CVX.sqlite").exists()
        assert not (release_root / "indexes" / "agent_index.sqlite").exists()
        assert "SUCCEEDED" in result.output
        assert "Queue release promoted" in result.output

    def test_queue_run_refreshes_v3_index_when_explicit(
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
        )
        events = []

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

        def fake_build_spine(root, *, release_id, no_cache=False, **kwargs):
            events.append(("index", root, release_id, no_cache))
            return _write_minimal_v3_index_outputs(root, release_id=release_id, ticker="CVX")

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index, "build_spine_shard_release_outputs", fake_build_spine)

        result = runner.invoke(
            app,
            ["queue-run", "--root", str(tmp_path), "--max-jobs", "1", "--refresh-index"],
        )

        assert result.exit_code == 0
        assert store.load_job(job.job_id).status == pipeline_queue.SUCCEEDED
        assert events == [("index", tmp_path.resolve(), "queue-refresh", False)]
        assert (tmp_path / "indexes" / "global_spine.sqlite").exists()
        assert not (tmp_path / "indexes" / "agent_index.sqlite").exists()
        assert "Refreshing 1 pending queue v3 index root(s)" in result.output
        assert "V3 index refreshed" in result.output

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

        def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
            index_calls.append(root)

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)

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

        def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
            _write_minimal_agent_index(index_path or root / "indexes" / "agent_index.sqlite")
            return {
                "index_path": index_path or root / "indexes" / "agent_index.sqlite",
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
        monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)
        monkeypatch.setattr(cli_main, "_publish_prod_root", fake_publish_prod_root)

        result = runner.invoke(
            app,
            ["queue-run", "--root", str(tmp_path), "--max-jobs", "1", "--publish-prod"],
        )

        assert result.exit_code == 0
        assert store.load_job(job.job_id).status == pipeline_queue.SUCCEEDED
        assert prod_calls == [(stable.resolve() / "dev" / "current").resolve()]
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
        assert "--publish-prod requires jobs with a release publish root" in failed_job.error
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

        def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
            events.append(("index", root, index_path, force))
            _write_minimal_agent_index(index_path or root / "indexes" / "agent_index.sqlite")
            return {
                "index_path": index_path or root / "indexes" / "agent_index.sqlite",
                "totals": {"documents": 1, "objects": 2, "edges": 0, "quality_events": 0},
            }

        monkeypatch.setattr(research_plan, "discover_research_filing_targets", fake_discover)
        monkeypatch.setattr(orchestrator, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(company_context_stage, "build_company_context", fake_build_company_context)
        monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)

        result = runner.invoke(
            app,
            ["queue-run", "--root", str(tmp_path), "--max-jobs", "1"],
        )

        assert result.exit_code == 0
        assert store.load_job(job.job_id).status == pipeline_queue.SUCCEEDED
        current = stable.resolve() / "dev" / "current"
        release_root = current.resolve()
        assert (release_root / "companies" / "VG" / "artifact.txt").read_text() == "FY2026Q1"
        assert events[:2] == [
            ("pipeline", "VG", "10-Q", "FY2026Q1", False, True),
            ("context", "VG"),
        ]
        assert (release_root / "indexes" / "global_spine.sqlite").exists()
        assert (release_root / "indexes" / "companies" / "VG.sqlite").exists()
        assert not (release_root / "indexes" / "agent_index.sqlite").exists()
        assert "START update VG 10-Q FY2026Q1" in result.output
        assert "SUCCEEDED" in result.output
        assert "Queue release promoted" in result.output

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

    def test_queue_start_can_enable_index_refresh(self, tmp_path: Path, monkeypatch):
        calls = []

        class FakeProcess:
            pid = 12345

        def fake_popen(command, *, stdout, stderr, start_new_session):
            calls.append(command)
            return FakeProcess()

        monkeypatch.setattr(cli_main.subprocess, "Popen", fake_popen)

        result = runner.invoke(
            app,
            ["queue-start", "--root", str(tmp_path), "--refresh-index"],
        )

        assert result.exit_code == 0
        assert "--refresh-index" in calls[0]

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
        )
        store.mark_running(running)
        store.add_job(
            "nflx",
            years=3,
            force=False,
            publish_root=None,
        )
        succeeded = store.add_job(
            "ge",
            years=3,
            force=False,
            publish_root=None,
        )
        store.mark_succeeded(succeeded)
        comma_failed = store.add_job(
            "aapl,",
            years=3,
            force=False,
            publish_root=None,
        )
        store.mark_failed(comma_failed, "resolve_ticker: ticker 'AAPL,' not found")
        typo_failed = store.add_job(
            "appl",
            years=3,
            force=False,
            publish_root=None,
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

    def test_queue_status_json_reports_worker_mode_and_active_publish_roots(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        stable = tmp_path / "stable"
        store = pipeline_queue.PipelineQueue(tmp_path)
        pending = store.add_job(
            "nflx",
            years=1,
            force=False,
            publish_root=stable,
        )
        running = store.mark_running(pending)
        store.write_worker_pid(12345)
        store.write_worker_state(
            12345,
            mode={
                "watch": True,
                "poll_interval": 15.0,
                "max_jobs": None,
                "publish_prod": False,
                "refresh_index": False,
            },
        )
        monkeypatch.setattr(pipeline_queue, "is_pid_running", lambda pid: pid == 12345)
        result = runner.invoke(app, ["queue", "status", "--root", str(tmp_path), "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["root"] == str(tmp_path.resolve())
        assert payload["worker"]["state"] == "running"
        assert payload["worker"]["pid"] == 12345
        assert payload["worker"]["mode"]["publish_prod"] is False
        assert payload["worker"]["mode"]["refresh_index"] is False
        assert payload["counts"]["running"] == 1
        assert payload["active_publish_roots"] == [str(stable)]
        assert store.load_job(running.job_id).status == pipeline_queue.RUNNING

    def test_queue_cancel_marks_pending_job_cancelled(self, tmp_path: Path):
        store = pipeline_queue.PipelineQueue(tmp_path)
        job = store.add_job(
            "cvx",
            years=1,
            force=False,
            publish_root=None,
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
    def test_publish_ticker_creates_release_overlay_and_promotes_current(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        running = tmp_path / "running"
        releases_root = tmp_path / "releases"
        base = releases_root / "dev" / "base"
        _write_minimal_v3_release(base, release_id="base", env="dev", ticker="CVX", artifact_text="old")
        (base / "companies" / "CVX" / "old.txt").write_text("old")
        (releases_root / "dev" / "current").symlink_to("base")
        _write_company_context_artifact(running, "CVX")
        source = running / "companies" / "CVX" / "ontology"
        source.mkdir(parents=True)
        (source / "new.txt").write_text("new")

        result = runner.invoke(
            app,
            [
                "publish-ticker",
                "cvx",
                "--from-root",
                str(running),
                "--to-root",
                str(releases_root),
                "--release-id",
                "rel1",
            ],
        )

        release_root = releases_root / "dev" / "rel1"
        assert result.exit_code == 0
        assert (release_root / "companies" / "CVX" / "ontology" / "new.txt").read_text() == "new"
        assert not (release_root / "companies" / "CVX" / "old.txt").exists()
        assert (base / "companies" / "CVX" / "old.txt").read_text() == "old"
        assert (release_root / "indexes" / "global_spine.sqlite").exists()
        assert (release_root / "indexes" / "companies" / "CVX.sqlite").exists()
        assert not (release_root / "indexes" / "agent_index.sqlite").exists()
        assert (releases_root / "dev" / "current").readlink() == Path("rel1")
        manifest = json.loads((release_root / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["format"] == "krw-ontology-release/v3"
        assert manifest["index_layout"] == "global-spine-and-company-shards"
        report_path = release_root / "verify" / "release_verify.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["ok"] is True
        assert report["reproducibility_hash"]
        progress_events = [
            json.loads(line)
            for line in (release_root / "indexes" / "build_progress.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        progress_keys = {(event["stage"], event["node_id"], event["status"]) for event in progress_events}
        assert ("manifest", "release_manifest", "complete") in progress_keys
        assert ("verification", "verification", "complete") in progress_keys
        assert ("promotion", "promote_current", "complete") in progress_keys
        assert "Release verify report:" in result.output
        assert "Release published: env=dev release_id=rel1" in result.output
        assert "Published CVX" in result.output
        assert "global_spine:" in result.output
        assert "V3 indexes built:" in result.output

    def test_publish_ticker_noop_skips_identical_verified_release(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        running = tmp_path / "running"
        releases_root = tmp_path / "releases"
        base = releases_root / "dev" / "base"
        _write_company_context_artifact(running, "VG")
        cli_main._materialize_release_root_from_source(running, base)
        build_result = agent_index.build_spine_shard_release_outputs(base, release_id=base.name)
        write_release_manifest_v3(
            base,
            release_id=base.name,
            env="dev",
            source_root=base,
            global_spine_path=build_result.global_spine_path,
            shard_manifest_path=build_result.shard_manifest_path,
        )
        verification = release_helpers.verify_release_root(base, env="dev")
        release_helpers.write_release_verification_report(base, env="dev", verification=verification)
        (releases_root / "dev" / "current").symlink_to(base.name)

        def fail_if_rebuilt(*_args, **_kwargs):
            raise AssertionError("identical publish must not rebuild v3 indexes")

        monkeypatch.setattr(agent_index, "build_spine_shard_release_outputs", fail_if_rebuilt)
        no_op = runner.invoke(
            app,
            [
                "publish-ticker",
                "vg",
                "--from-root",
                str(running),
                "--to-root",
                str(releases_root),
                "--release-id",
                "rel-noop",
            ],
        )

        assert no_op.exit_code == 0, no_op.output
        assert "No-op release publish" in no_op.output
        assert os.readlink(releases_root / "dev" / "current") == base.name
        assert not (releases_root / "dev" / "rel-noop").exists()

    def test_publish_ticker_rejects_index_rebuild_skip(self, tmp_path: Path, monkeypatch):
        running = tmp_path / "running"
        releases_root = tmp_path / "releases"
        (running / "companies" / "OXY").mkdir(parents=True)
        _write_company_context_artifact(running, "OXY")

        result = runner.invoke(
            app,
            [
                "publish-ticker",
                "oxy",
                "--from-root",
                str(running),
                "--to-root",
                str(releases_root),
                "--no-rebuild-agent-index",
            ],
        )

        assert result.exit_code != 0
        assert "No such option" in result.output
        assert not (releases_root / "dev" / "current").exists()

    def test_publish_ticker_quarantines_candidate_when_index_build_fails(self, tmp_path: Path, monkeypatch):
        running = tmp_path / "running"
        releases_root = tmp_path / "releases"
        source = running / "companies" / "OXY" / "ontology"
        source.mkdir(parents=True)
        (source / "artifact.jsonl").write_text("{}\n")
        _write_company_context_artifact(running, "OXY")

        def fake_build_spine_outputs(*args, **kwargs):
            raise RuntimeError("v3 index boom")

        monkeypatch.setattr(agent_index, "build_spine_shard_release_outputs", fake_build_spine_outputs)

        result = runner.invoke(
            app,
            [
                "publish-ticker",
                "oxy",
                "--from-root",
                str(running),
                "--to-root",
                str(releases_root),
                "--release-id",
                "failed-rel",
            ],
        )

        assert result.exit_code == 1
        assert "FAILED release publish: v3 index boom" in result.output
        assert not (releases_root / "dev" / "failed-rel").exists()
        quarantine = releases_root / "dev" / "failed" / "failed-rel"
        assert quarantine.is_dir()
        failure = json.loads((quarantine / "failure.json").read_text(encoding="utf-8"))
        assert failure["action"] == "publish_tickers"
        assert failure["error"] == "v3 index boom"
        assert not (releases_root / "dev" / "current").exists()

    def test_publish_ticker_dry_run_does_not_copy_or_rebuild_index(self, tmp_path: Path, monkeypatch):
        running = tmp_path / "running"
        stable = tmp_path / "stable"
        (running / "companies" / "LNG").mkdir(parents=True)

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
        assert not (stable / "dev").exists()
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
        assert "release" in result.output
        assert "publish-ticker" in result.output
        assert "validate" in result.output
        assert "build-report" in result.output
        assert "build-agent-index" not in result.output

    def test_index_help_exposes_v3_only_surface(self):
        for command in ("plan", "build", "verify"):
            result = runner.invoke(app, ["index", command, "--help"])
            assert result.exit_code == 0
            assert "global spine" in result.output
            assert "company shard" in result.output
            assert "agent_index.sqlite" not in result.output
            assert "monolith-and-shards" not in result.output
            assert "--index-path" not in result.output
            assert "--layout" not in result.output
            assert "monolith" not in result.output.lower()

    def test_observability_help_uses_global_spine_metric_surface(self):
        result = runner.invoke(app, ["observability", "render-prometheus-alerts", "--help"])

        assert result.exit_code == 0
        assert "--global-spine-missing-for" in result.output
        assert "--index-missing-for" not in result.output
        assert "KRWOntologyMCPIndexMissing" not in result.output
        assert "KRW_PROMETHEUS_INDEX_MISSING_FOR" not in result.output

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


def test_release_finalize_dev_refuses_to_mutate_current_when_publish_root_unset(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    releases_root = tmp_path / "releases"
    release_id = "20260528_090000"
    release_root = releases_root / "dev" / release_id
    (release_root / "companies" / "CVX" / "ontology").mkdir(parents=True)
    (release_root / "companies" / "CVX" / "ontology" / "artifact.jsonl").write_text("{}\n")
    write_release_manifest_v3(release_root, release_id=release_id, env="dev")
    (releases_root / "dev" / "current").symlink_to(release_id)
    calls = []

    def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
        calls.append((root, index_path, force))
        resolved_index_path = index_path or root / "indexes" / "agent_index.sqlite"
        _write_minimal_agent_index(resolved_index_path)
        return {
            "index_path": resolved_index_path,
            "totals": {"documents": 1, "objects": 2, "edges": 0, "quality_events": 0},
        }

    monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)

    result = runner.invoke(app, ["release", "finalize-dev", "--releases-root", str(releases_root)])

    assert result.exit_code == 1
    assert calls == []
    assert "Missing release id. Pass a prepared non-current release id" in result.output

    explicit_current = runner.invoke(
        app,
        ["release", "finalize-dev", release_id, "--releases-root", str(releases_root)],
    )
    assert explicit_current.exit_code == 1
    assert "Refusing to finalize dev/current in place" in explicit_current.output
    assert calls == []


def test_release_finalize_dev_refuses_empty_release_root_before_index_build(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    releases_root = tmp_path / "releases"
    release_id = "20260528_100000"
    release_root = releases_root / "dev" / release_id
    release_root.mkdir(parents=True)
    write_release_manifest_v3(release_root, release_id=release_id, env="dev")
    calls = []

    def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
        calls.append((root, index_path, force))
        return {
            "index_path": index_path or root / "indexes" / "agent_index.sqlite",
            "totals": {"documents": 0, "objects": 0, "edges": 0, "quality_events": 0},
        }

    monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)

    result = runner.invoke(
        app,
        ["release", "finalize-dev", release_id, "--releases-root", str(releases_root)],
    )

    assert result.exit_code == 1
    assert calls == []
    assert "Release has no ontology artifacts under companies/" in result.output
    assert "Refusing to finalize an empty index" in result.output


def test_release_finalize_dev_rejects_index_build_skip(tmp_path: Path):
    releases_root = tmp_path / "releases"

    result = runner.invoke(
        app,
        ["release", "finalize-dev", "candidate", "--releases-root", str(releases_root), "--no-build-index"],
    )

    assert result.exit_code == 1
    assert "Release finalize requires index build and verification" in result.output
    assert not releases_root.exists()


def test_release_publish_dev_builds_v3_release_from_running_root(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(cli_main, "_default_release_id", lambda: "20260603_110000")
    running_root = tmp_path / "running"
    releases_root = tmp_path / "releases"
    _write_minimal_source_artifact(running_root, "CVX")
    (running_root / ".krw_pipeline" / "jobs").mkdir(parents=True)
    (running_root / ".krw_pipeline" / "jobs" / "job.json").write_text("{}")
    (running_root / "indexes").mkdir()
    (running_root / "indexes" / "agent_index.sqlite").write_text("legacy monolith", encoding="utf-8")
    (running_root / "indexes" / "agent_index.sqlite-wal").write_text("legacy wal", encoding="utf-8")
    (running_root / "indexes" / "agent_index.sqlite-shm").write_text("legacy shm", encoding="utf-8")
    runner.invoke(app, ["config", "set", "running-root", str(running_root)])

    result = runner.invoke(app, ["release", "publish-dev", "--foreground", "--releases-root", str(releases_root)])

    release_root = releases_root / "dev" / "20260603_110000"
    assert result.exit_code == 0, result.output
    assert (release_root / "companies" / "CVX" / "context" / "artifact_index.json").exists()
    assert not (release_root / ".krw_pipeline").exists()
    assert (release_root / "indexes" / "global_spine.sqlite").exists()
    assert (release_root / "indexes" / "companies" / "CVX.sqlite").exists()
    assert (release_root / "indexes" / "shard_manifest.json").exists()
    assert not (release_root / "indexes" / "agent_index.sqlite").exists()
    assert not (release_root / "indexes" / "agent_index.sqlite-wal").exists()
    assert not (release_root / "indexes" / "agent_index.sqlite-shm").exists()
    manifest = json.loads((release_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "krw-ontology-release/v3"
    assert manifest["index_layout"] == "global-spine-and-company-shards"
    assert manifest["monolith_required"] is False
    report_path = release_root / "verify" / "release_verify.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["ok"] is True
    assert any(item["path"] == "indexes/global_spine.sqlite" for item in report["files"])
    progress_events = [
        json.loads(line)
        for line in (release_root / "indexes" / "build_progress.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    progress_keys = {(event["stage"], event["node_id"], event["status"]) for event in progress_events}
    assert ("manifest", "release_manifest", "complete") in progress_keys
    assert ("verification", "verification", "complete") in progress_keys
    assert ("promotion", "promote_current", "complete") in progress_keys
    assert (releases_root / "dev" / "current").readlink() == Path("20260603_110000")
    assert "Dev release published: 20260603_110000" in result.output
    assert "index_layout: global-spine-and-company-shards" in result.output
    assert "V3 indexes built:" in result.output
    assert "global_spine:" in result.output
    assert "verify_report:" in result.output
    assert "promoted: True" in result.output


def test_release_publish_dev_rejects_index_build_skip(tmp_path: Path):
    releases_root = tmp_path / "releases"

    result = runner.invoke(
        app,
        ["release", "publish-dev", "--releases-root", str(releases_root), "--no-build-index"],
    )

    assert result.exit_code == 1
    assert "Release publish requires index build and verification" in result.output
    assert not releases_root.exists()


def test_release_publish_dev_refuses_empty_running_root(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    running_root = tmp_path / "running"
    running_root.mkdir()
    releases_root = tmp_path / "releases"
    runner.invoke(app, ["config", "set", "running-root", str(running_root)])
    calls = []

    def fake_build_agent_index(root, *, index_path=None, force=True, source_manifest_path=None):
        calls.append((root, index_path, force))
        return {
            "index_path": index_path or root / "indexes" / "agent_index.sqlite",
            "totals": {"documents": 0, "objects": 0, "edges": 0, "quality_events": 0},
        }

    monkeypatch.setattr(agent_index_builder, "build_agent_index", fake_build_agent_index)

    result = runner.invoke(app, ["release", "publish-dev", "--releases-root", str(releases_root)])

    assert result.exit_code == 1
    assert calls == []
    assert "Source root has no ontology artifacts under companies/" in result.output
    assert not (releases_root / "dev").exists()


class TestCurrentReleaseImmutability:
    def _active_dev_release(self, tmp_path: Path) -> tuple[Path, Path]:
        env_root = tmp_path / "releases" / "dev"
        release_root = env_root / "active-release"
        _write_minimal_v3_release(release_root, release_id=release_root.name, env="dev", ticker="VG")
        current = env_root / "current"
        current.symlink_to(release_root.name)
        return current, release_root

    def test_index_build_rejects_current_symlink_and_active_release_target(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        current, release_root = self._active_dev_release(tmp_path)
        original_manifest = (release_root / "manifest.json").read_bytes()
        calls = []

        def fail_if_built(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("active release index must not be rebuilt")

        monkeypatch.setattr(agent_index_builder, "build_agent_index", fail_if_built)

        via_current = runner.invoke(app, ["index", "build", "--root", str(current)])
        via_target = runner.invoke(app, ["index", "build", "--root", str(release_root)])

        assert via_current.exit_code == 1
        assert via_target.exit_code == 1
        assert "current is an immutable release pointer" in via_current.output
        assert "current is an immutable release pointer" in via_target.output
        assert calls == []
        assert (release_root / "manifest.json").read_bytes() == original_manifest

    def test_index_build_and_manifest_write_reject_current(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        current, release_root = self._active_dev_release(tmp_path)
        original_manifest = (release_root / "manifest.json").read_bytes()
        calls = []

        def fail_if_built(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("active release index must not be rebuilt")

        monkeypatch.setattr(agent_index_builder, "build_agent_index", fail_if_built)

        index_build = runner.invoke(app, ["index", "build", "--root", str(current)])
        manifest_write = runner.invoke(
            app,
            ["release", "write-manifest", "--root", str(current), "--env", "dev"],
        )

        assert index_build.exit_code == 1
        assert manifest_write.exit_code == 1
        assert "current is an immutable release pointer" in index_build.output
        assert "current is an immutable release pointer" in manifest_write.output
        assert calls == []
        assert (release_root / "manifest.json").read_bytes() == original_manifest
        with pytest.raises(ValueError, match="current is an immutable release pointer"):
            write_release_manifest_v3(release_root, release_id=release_root.name, env="dev")
        with pytest.raises(ValueError, match="current is an immutable release pointer"):
            release_helpers.write_release_verification_report(current, env="dev")

    def test_mutating_pipeline_command_rejects_current(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        current, _release_root = self._active_dev_release(tmp_path)
        calls = []

        def fail_if_built(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("active release context must not be rebuilt")

        monkeypatch.setattr(company_context_stage, "build_company_context", fail_if_built)

        result = runner.invoke(
            app,
            ["build-company-context", "VG", "--root", str(current), "--no-refresh-index"],
        )

        assert result.exit_code == 1
        assert "current is an immutable release pointer" in result.output
        assert calls == []

    def test_release_verify_is_read_only_on_current_unless_report_write_requested(self, tmp_path: Path):
        current, release_root = self._active_dev_release(tmp_path)
        report_path = release_root / "verify" / "release_verify.json"

        read_only = runner.invoke(
            app,
            ["release", "verify", "--root", str(current), "--env", "dev"],
        )
        write_report = runner.invoke(
            app,
            [
                "release",
                "verify",
                "--root",
                str(current),
                "--env",
                "dev",
                "--write-report",
            ],
        )

        assert read_only.exit_code == 0, read_only.output
        assert write_report.exit_code == 1
        assert "current is an immutable release pointer" in write_report.output
        assert not report_path.exists()

    def test_release_export_and_repromote_cannot_mutate_current(self, tmp_path: Path):
        current, release_root = self._active_dev_release(tmp_path)
        releases_root = tmp_path / "releases"
        out = current / "web_catalog.json"

        export = runner.invoke(
            app,
            ["release", "export-web-catalog", "--root", str(current), "--out", str(out)],
        )
        promote = runner.invoke(
            app,
            [
                "release",
                "promote",
                release_root.name,
                "--releases-root",
                str(releases_root),
                "--env",
                "dev",
            ],
        )

        assert export.exit_code == 1
        assert "current is an immutable release pointer" in export.output
        assert not out.exists()
        assert promote.exit_code == 1
        assert "already current" in promote.output
        assert not (release_root / "verify" / "release_verify.json").exists()
        with pytest.raises(ValueError, match="already current"):
            release_helpers.promote_local_release(releases_root, env="dev", release_id=release_root.name)

    def test_index_cache_write_paths_reject_current(self, tmp_path: Path):
        current, release_root = self._active_dev_release(tmp_path)
        cache_root = current / "fragment-cache"
        marker = release_root / "fragment-cache" / "marker.txt"
        marker.parent.mkdir()
        marker.write_text("keep", encoding="utf-8")
        running = tmp_path / "running"
        running.mkdir()

        build = runner.invoke(
            app,
            ["index", "build", "--root", str(running), "--cache-root", str(cache_root)],
        )
        gc = runner.invoke(
            app,
            ["index", "cache", "gc", "--root", str(running), "--cache-root", str(cache_root), "--yes"],
        )

        assert build.exit_code == 1
        assert gc.exit_code == 1
        assert "current is an immutable release pointer" in build.output
        assert "current is an immutable release pointer" in gc.output
        assert marker.read_text(encoding="utf-8") == "keep"


def test_release_publish_dev_quarantines_new_release_candidate_when_index_build_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(cli_main, "_default_release_id", lambda: "20260603_failed")
    running_root = tmp_path / "running"
    releases_root = tmp_path / "releases"
    _write_minimal_source_artifact(running_root, "CVX")
    runner.invoke(app, ["config", "set", "running-root", str(running_root)])

    def fake_build_spine_outputs(*args, **kwargs):
        raise RuntimeError("v3 index boom")

    monkeypatch.setattr(agent_index, "build_spine_shard_release_outputs", fake_build_spine_outputs)

    result = runner.invoke(app, ["release", "publish-dev", "--foreground", "--releases-root", str(releases_root)])

    assert result.exit_code == 1
    assert "v3 index boom" in result.output
    assert not (releases_root / "dev" / "20260603_failed").exists()
    quarantine = releases_root / "dev" / "failed" / "20260603_failed"
    assert quarantine.is_dir()
    failure = json.loads((quarantine / "failure.json").read_text(encoding="utf-8"))
    assert failure["action"] == "publish_root"
    assert failure["error"] == "v3 index boom"
    assert not (releases_root / "dev" / "current").exists()


def test_release_publish_dev_status_and_watch_use_configured_publish_root(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    releases_root = tmp_path / "releases"
    release_root = releases_root / "dev" / "ready"
    _write_minimal_v3_release(release_root, release_id="ready", env="dev", ticker="CVX")
    (release_root / "logs").mkdir(parents=True)
    (release_root / "logs" / "publish-dev.log").write_text("worker done\n", encoding="utf-8")
    (release_root / "indexes" / "build_progress.jsonl").write_text(
        json.dumps(
            {
                "format": "krw-ontology-v3-build-progress/v1",
                "release_id": "ready",
                "event": "node_status",
                "node_id": "company_shard:CVX",
                "stage": "company_shard",
                "status": "rebuilt",
                "timestamp": "2026-06-12T00:00:00+00:00",
                "ticker": "CVX",
                "output": "indexes/companies/CVX.sqlite",
                "cache_hit": False,
                "details": {"completed": 1, "total": 1},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (releases_root / "dev" / "current").symlink_to("ready")
    runner.invoke(app, ["config", "set", "publish-root", str(releases_root)])

    status = runner.invoke(app, ["release", "publish-dev-status"])
    watch = runner.invoke(app, ["release", "publish-dev-watch", "--no-follow"])
    release_status = runner.invoke(app, ["release", "status", "--env", "dev"])

    assert status.exit_code == 0, status.output
    assert "release_id: ready" in status.output
    assert f"release_root: {release_root.resolve()}" in status.output
    assert "global_spine: present" in status.output
    assert "shard_manifest: present" in status.output
    assert "progress_status: rebuilt stage=company_shard node=company_shard:CVX" in status.output
    assert "progress_count: 1/1" in status.output
    assert watch.exit_code == 0, watch.output
    assert "worker done" in watch.output
    assert "progress_status: rebuilt stage=company_shard node=company_shard:CVX" in watch.output
    assert release_status.exit_code == 0, release_status.output
    assert "progress_status: rebuilt stage=company_shard node=company_shard:CVX" in release_status.output


def test_release_publish_dev_watch_ignores_cache_dirs_when_selecting_latest(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    releases_root = tmp_path / "releases"
    release_root = releases_root / "dev" / "ready"
    _write_minimal_v3_release(release_root, release_id="ready", env="dev", ticker="CVX")
    (release_root / "logs").mkdir(parents=True)
    (release_root / "logs" / "publish-dev.log").write_text("real release log\n", encoding="utf-8")
    cache_dir = releases_root / "dev" / ".index_fragment_cache"
    (cache_dir / "logs").mkdir(parents=True)
    os.utime(cache_dir, None)
    runner.invoke(app, ["config", "set", "publish-root", str(releases_root)])

    result = runner.invoke(app, ["release", "publish-dev-watch", "--no-follow"])

    assert result.exit_code == 0, result.output
    assert "real release log" in result.output
    assert ".index_fragment_cache" not in result.output


def test_release_cleanup_interrupted_quarantines_stale_worker_candidate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    releases_root = tmp_path / "releases"
    release_root = releases_root / "dev" / "20260611_100844"
    release_root.mkdir(parents=True)
    (release_root / "worker.pid").write_text("99999999", encoding="utf-8")
    (release_root / "worker_state.json").write_text("{}", encoding="utf-8")
    temp_index = release_root / "indexes" / ".global_spine.sqlite.999.tmp"
    temp_index.parent.mkdir(parents=True)
    temp_index.write_text("partial", encoding="utf-8")
    temp_spine = release_root / "indexes" / "fragments" / "spine" / ".AAPL.sqlite.999.tmp"
    temp_spine.parent.mkdir(parents=True)
    temp_spine.write_text("partial", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "release",
            "cleanup-interrupted",
            "20260611_100844",
            "--releases-root",
            str(releases_root),
            "--delete-temp",
        ],
    )

    failed_root = releases_root / "dev" / "failed" / "20260611_100844"
    assert result.exit_code == 0, result.output
    assert "quarantined: 20260611_100844" in result.output
    assert not release_root.exists()
    assert failed_root.is_dir()
    assert not (failed_root / "indexes" / ".global_spine.sqlite.999.tmp").exists()
    assert not (failed_root / "indexes" / "fragments" / "spine" / ".AAPL.sqlite.999.tmp").exists()
    failure = json.loads((failed_root / "failure.json").read_text(encoding="utf-8"))
    assert failure["action"] == "cleanup_interrupted"
    assert "99999999" in failure["error"]


def test_release_publish_dev_defaults_to_background_worker(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(cli_main, "_default_release_id", lambda: "20260603_120000")
    running_root = tmp_path / "running"
    releases_root = tmp_path / "releases"
    _write_minimal_source_artifact(running_root, "CVX")
    runner.invoke(app, ["config", "set", "running-root", str(running_root)])
    runner.invoke(app, ["config", "set", "publish-root", str(releases_root)])
    calls = []

    class FakeProcess:
        pid = 23456

    def fake_popen(command, *, stdout, stderr, start_new_session):
        calls.append((command, stderr, start_new_session))
        stdout.write("fake publish worker\n")
        stdout.flush()
        return FakeProcess()

    monkeypatch.setattr(cli_main.subprocess, "Popen", fake_popen)

    result = runner.invoke(app, ["release", "publish-dev"])

    release_root = releases_root / "dev" / "20260603_120000"
    assert result.exit_code == 0, result.output
    assert calls
    command = calls[0][0]
    assert "release-publish-dev-worker" in command
    assert "--release-id" in command
    assert "20260603_120000" in command
    assert "--from-root" in command
    assert str(running_root.resolve()) in command
    assert calls[0][1] is cli_main.subprocess.STDOUT
    assert calls[0][2] is True
    assert (release_root / "logs" / "publish-dev.log").exists()
    assert "Started dev publish worker pid=23456" in result.output
    assert "index_layout: global-spine-and-company-shards" in result.output
    assert f"global_spine: {release_root / 'indexes' / 'global_spine.sqlite'}" in result.output
    assert f"shard_manifest: {release_root / 'indexes' / 'shard_manifest.json'}" in result.output
    assert "watch: krw-ontology release publish-dev-watch 20260603_120000" in result.output
