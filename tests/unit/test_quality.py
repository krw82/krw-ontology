from __future__ import annotations

import json
import sqlite3
import hashlib
from pathlib import Path

from typer.testing import CliRunner

from krw_ontology.cli import main as cli_main
from krw_ontology.cli.main import app
from krw_ontology.agent_index.spine_builder import _company_shard_quality_summary
from krw_ontology.agent_index.spine_schema import create_global_spine_schema, write_global_spine_metadata
from krw_ontology.quality.models import (
    BATCH_FAILURE,
    DOCS_MISSING,
    NORMALIZE_NUMERIC,
    REPAIR_REFERENCE,
    RepairJob,
    RepairPlan,
    SECTION_FAIL,
)
from krw_ontology.quality.queue import QualityRepairStore
from krw_ontology.quality.runner import run_repair_jobs
from krw_ontology.quality.scanner import QualityReleaseScanner, QualityShardScanner
from krw_ontology.pipeline.queue import PipelineQueue
from krw_ontology.release import write_release_manifest_v3
from krw_ontology.utils.io import read_jsonl, write_jsonl


runner = CliRunner()


def test_quality_scanner_summarizes_problem_tickers(tmp_path: Path):
    index_path = _write_quality_index(tmp_path)

    report = QualityShardScanner(index_path).scan(min_docs=5)

    assert report["totals"]["documents"] == 6
    assert report["totals"]["tickers"] == 2
    assert report["problem_ticker_count"] == 1
    assert report["kind_counts"][DOCS_MISSING] == 1
    assert report["kind_counts"][SECTION_FAIL] == 1
    assert report["kind_counts"][BATCH_FAILURE] == 1

    explanation = QualityShardScanner(index_path).explain_ticker("fcx", min_docs=5)
    assert explanation["summary"]["severity"] == "high"
    assert explanation["documents"][0]["section_quality"]["missing_core_sections"] == ["item7"]


def test_quality_scanner_builds_repair_plan_jobs(tmp_path: Path):
    index_path = _write_quality_index(tmp_path)

    jobs = QualityShardScanner(index_path).build_repair_jobs(plan_id="qr_test", min_docs=5)
    by_kind = {}
    for job in jobs:
        by_kind[job.kind] = by_kind.get(job.kind, 0) + 1

    assert by_kind[DOCS_MISSING] == 1
    assert by_kind[SECTION_FAIL] == 1
    assert by_kind[BATCH_FAILURE] == 1
    assert by_kind[NORMALIZE_NUMERIC] == 1
    assert by_kind[REPAIR_REFERENCE] == 1
    assert all(job.plan_id == "qr_test" for job in jobs)


def test_quality_cli_check_tickers_explain_and_events_use_v3_release_root(tmp_path: Path, monkeypatch):
    release_root = _write_v3_quality_release(tmp_path)

    check = runner.invoke(app, ["quality", "check", "--release-root", str(release_root)])
    assert check.exit_code == 0
    assert "Scan: mode=bounded rollup=manifest opened_shards=0 full_consistency=False" in check.output
    assert "Problem tickers: 1" in check.output
    assert "section_fail=1" in check.output
    full_check = runner.invoke(app, ["quality", "check", "--release-root", str(release_root), "--full"])
    assert full_check.exit_code == 0
    assert "Scan: mode=full rollup=shard_scan opened_shards=2 full_consistency=True" in full_check.output

    def fail_if_ticker_quality_reads_shard(*_args, **_kwargs):
        raise AssertionError("bounded quality tickers must use manifest rollups")

    with monkeypatch.context() as patch:
        patch.setattr(QualityShardScanner, "ticker_quality", fail_if_ticker_quality_reads_shard)
        tickers = runner.invoke(app, ["quality", "tickers", "--release-root", str(release_root)])
    assert tickers.exit_code == 0
    assert "Scan: mode=bounded" in tickers.output
    assert "FCX" in tickers.output
    assert "OK" not in tickers.output
    full_tickers = runner.invoke(app, ["quality", "tickers", "--release-root", str(release_root), "--full"])
    assert full_tickers.exit_code == 0
    assert "Scan: mode=full" in full_tickers.output

    explain = runner.invoke(app, ["quality", "explain", "FCX", "--release-root", str(release_root)])
    assert explain.exit_code == 0
    assert "missing=item7" in explain.output
    assert "batch_failure" in explain.output

    events = runner.invoke(
        app,
        ["quality", "events", "--ticker", "FCX", "--category", "batch_failure", "--release-root", str(release_root)],
    )
    assert events.exit_code == 0
    assert "extract_assumption_candidates" in events.output


def test_quality_release_scanner_reads_v3_release_without_monolith(tmp_path: Path):
    release_root = _write_v3_quality_release(tmp_path)

    assert not (release_root / "indexes" / "agent_index.sqlite").exists()

    scanner = QualityReleaseScanner(release_root)
    report = scanner.scan(min_docs=5)

    assert report["metadata"]["format"] == "krw-ontology-release/v3"
    assert report["scan"]["mode"] == "bounded"
    assert report["scan"]["rollup_source"] == "manifest"
    assert report["scan"]["opened_shards"] == 0
    assert report["scan"]["full_consistency"] is False
    assert report["totals"]["documents"] == 6
    assert report["totals"]["tickers"] == 2
    assert report["problem_ticker_count"] == 1
    assert report["kind_counts"][DOCS_MISSING] == 1
    assert report["kind_counts"][SECTION_FAIL] == 1
    assert report["kind_counts"][BATCH_FAILURE] == 1
    assert report["kind_counts"].get("release_consistency", 0) == 0
    assert report["consistency"]["ok"] is True
    assert report["consistency"]["mode"] == "bounded"
    assert report["consistency"]["full_shard_checks"] is False
    full_report = scanner.scan(min_docs=5, mode="full")
    assert full_report["scan"]["mode"] == "full"
    assert full_report["scan"]["opened_shards"] == 2
    assert full_report["scan"]["full_consistency"] is True
    assert full_report["consistency"]["full_shard_checks"] is True

    explanation = scanner.explain_ticker("FCX", min_docs=5)
    assert explanation["summary"]["severity"] == "high"
    assert explanation["shard_path"].endswith("indexes/companies/FCX.sqlite")


def test_quality_release_scanner_reports_declared_missing_company_shards(tmp_path: Path):
    release_root = _write_v3_quality_release(tmp_path)
    missing_shard = release_root / "indexes" / "companies" / "OK.sqlite"
    missing_shard.unlink()

    scanner = QualityReleaseScanner(release_root)
    report = scanner.scan(min_docs=5)

    assert report["shards"]["declared_count"] == 2
    assert report["shards"]["available_count"] == 1
    assert report["shards"]["missing_count"] == 1
    assert report["shards"]["missing"] == [
        {
            "ticker": "OK",
            "path": str(missing_shard),
            "manifest_path": "companies/OK.sqlite",
        }
    ]
    assert report["totals"]["documents"] == 1
    assert report["consistency"]["ok"] is False
    assert report["consistency"]["declared_shards"] == 2
    assert report["consistency"]["available_shards"] == 1
    assert report["consistency"]["missing_shards"] == report["shards"]["missing"]
    assert any(error.startswith(f"shard_missing:OK:{missing_shard}") for error in report["consistency"]["errors"])
    assert report["kind_counts"]["release_consistency"] >= 1

    result = runner.invoke(app, ["quality", "check", "--release-root", str(release_root)])

    assert result.exit_code == 0
    assert "Scan: mode=bounded" in result.output
    assert "Shards: declared=2 available=1 missing=1" in result.output
    assert f"- missing shard OK: {missing_shard}" in result.output
    assert "Consistency: fail" in result.output
    assert "shard_missing:OK:" in result.output


def test_quality_cli_check_uses_v3_release_root(tmp_path: Path):
    release_root = _write_v3_quality_release(tmp_path)

    result = runner.invoke(app, ["quality", "check", "--release-root", str(release_root)])

    assert result.exit_code == 0
    assert "Release:" in result.output
    assert "global_spine.sqlite" in result.output
    assert "Problem tickers: 1" in result.output
    assert "Consistency: pass" in result.output


def test_quality_cli_check_defaults_to_configured_v3_current(tmp_path: Path, monkeypatch):
    release_root = _write_v3_quality_release(tmp_path)
    current = release_root.parent / "current"
    current.symlink_to(release_root.name)
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    config = runner.invoke(app, ["config", "set", "publish-root", str(tmp_path / "releases")])
    assert config.exit_code == 0

    result = runner.invoke(app, ["quality", "check"])

    assert result.exit_code == 0
    assert "Release: dev/current" in result.output
    assert "global_spine.sqlite" in result.output
    assert "Consistency: pass" in result.output


def test_quality_repair_plan_records_v3_fingerprint_and_refuses_stale_run(tmp_path: Path, monkeypatch):
    release_root = _write_v3_quality_release(tmp_path)
    root = tmp_path / "running"
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))

    plan_result = runner.invoke(
        app,
        [
            "quality",
            "repair",
            "plan",
            "--root",
            str(root),
            "--release-root",
            str(release_root),
            "--plan",
            "qr_v3",
        ],
    )

    assert plan_result.exit_code == 0
    store = QualityRepairStore(root)
    plan = store.load_plan("qr_v3")
    assert plan.release_format == "krw-ontology-release/v3"
    assert plan.release_id == "v3-quality"
    assert plan.release_manifest_sha256
    assert plan.global_spine_sha256
    assert plan.shard_manifest_sha256

    manifest_path = release_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_id"] = "v3-quality-mutated"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    stale = runner.invoke(app, ["quality", "repair", "run", "--root", str(root), "--plan", "qr_v3", "--preview"])
    assert stale.exit_code == 1
    assert "quality repair plan is stale" in stale.output

    allowed = runner.invoke(
        app,
        [
            "quality",
            "repair",
            "run",
            "--root",
            str(root),
            "--plan",
            "qr_v3",
            "--preview",
            "--allow-stale-plan",
        ],
    )
    assert allowed.exit_code == 1
    assert "would run" in allowed.output


def test_quality_repair_plan_refuses_stale_shard_manifest(tmp_path: Path, monkeypatch):
    release_root = _write_v3_quality_release(tmp_path)
    root = tmp_path / "running"
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))

    plan_result = runner.invoke(
        app,
        [
            "quality",
            "repair",
            "plan",
            "--root",
            str(root),
            "--release-root",
            str(release_root),
            "--plan",
            "qr_shard_manifest",
        ],
    )

    assert plan_result.exit_code == 0
    plan = QualityRepairStore(root).load_plan("qr_shard_manifest")
    assert plan.shard_manifest_sha256

    shard_manifest_path = release_root / "indexes" / "shard_manifest.json"
    shard_manifest = json.loads(shard_manifest_path.read_text(encoding="utf-8"))
    shard_manifest["test_mutation"] = "changed"
    shard_manifest_path.write_text(json.dumps(shard_manifest, sort_keys=True), encoding="utf-8")

    stale = runner.invoke(
        app,
        [
            "quality",
            "repair",
            "run",
            "--root",
            str(root),
            "--plan",
            "qr_shard_manifest",
            "--preview",
        ],
    )

    assert stale.exit_code == 1
    assert "quality repair plan is stale" in stale.output
    assert "shard_manifest_sha256" in stale.output


def test_quality_cli_repair_plan_show_status_list_clear(tmp_path: Path, monkeypatch):
    release_root = _write_v3_quality_release(tmp_path)
    root = tmp_path / "running"
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))

    plan = runner.invoke(
        app,
        [
            "quality",
            "repair",
            "plan",
            "--root",
            str(root),
            "--release-root",
            str(release_root),
            "--plan",
            "qr_test",
        ],
    )
    assert plan.exit_code == 0
    assert "Repair plan created: qr_test" in plan.output
    assert "Deferred excluded: normalize_numeric=1" in plan.output
    assert "Nothing executed yet." in plan.output

    store = QualityRepairStore(root)
    assert store.load_plan("qr_test").plan_id == "qr_test"
    assert store.status_counts(plan_id="qr_test")["pending"] == 4

    show = runner.invoke(app, ["quality", "repair", "show", "--root", str(root), "--plan", "qr_test"])
    assert show.exit_code == 0
    assert "Repair plan: qr_test" in show.output
    assert "normalize_numeric" not in show.output

    status = runner.invoke(app, ["quality", "repair", "status", "--root", str(root), "--plan", "qr_test"])
    assert status.exit_code == 0
    assert "pending=4" in status.output

    listed = runner.invoke(
        app,
        ["quality", "repair", "list", "--root", str(root), "--plan", "qr_test", "--kind", BATCH_FAILURE],
    )
    assert listed.exit_code == 0
    assert "extract_assumption_candidates" in listed.output

    clear = runner.invoke(app, ["quality", "repair", "clear", "--root", str(root), "--plan", "qr_test", "--yes"])
    assert clear.exit_code == 0
    assert "removed=" in clear.output


def test_quality_repair_run_preview_defaults_to_executable_jobs(tmp_path: Path, monkeypatch):
    release_root = _write_v3_quality_release(tmp_path)
    root = tmp_path / "running"
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))

    plan = runner.invoke(
        app,
        [
            "quality",
            "repair",
            "plan",
            "--root",
            str(root),
            "--release-root",
            str(release_root),
            "--plan",
            "qr_test",
        ],
    )
    assert plan.exit_code == 0

    preview = runner.invoke(app, ["quality", "repair", "run", "--root", str(root), "--plan", "qr_test", "--preview"])

    assert preview.exit_code == 1
    assert "Skipped" not in preview.output
    assert "would run section_fail" in preview.output
    assert "would run batch_failure" in preview.output
    assert "would run docs_missing" in preview.output
    assert "would run repair_reference" in preview.output

    numeric_preview = runner.invoke(
        app,
        ["quality", "repair", "run", "--root", str(root), "--plan", "qr_test", "--kind", NORMALIZE_NUMERIC, "--preview"],
    )

    assert numeric_preview.exit_code == 0
    assert "Skipped" not in numeric_preview.output
    assert "No pending quality repair jobs selected." in numeric_preview.output


def test_quality_repair_run_all_and_watch_once(tmp_path: Path, monkeypatch):
    release_root = _write_v3_quality_release(tmp_path)
    root = tmp_path / "running"
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))

    plan = runner.invoke(
        app,
        [
            "quality",
            "repair",
            "plan",
            "--root",
            str(root),
            "--release-root",
            str(release_root),
            "--plan",
            "qr_test",
        ],
    )
    assert plan.exit_code == 0

    preview = runner.invoke(
        app,
        ["quality", "repair", "run", "--root", str(root), "--plan", "qr_test", "--all", "--preview"],
    )

    assert preview.exit_code == 1
    assert "Selection: all executable pending jobs" in preview.output
    assert preview.output.count("would run") == 4
    assert "would run docs_missing" in preview.output

    watch = runner.invoke(
        app,
        ["quality", "repair", "watch", "--root", str(root), "--plan", "qr_test", "--once"],
    )

    assert watch.exit_code == 0
    assert "QUALITY_QUEUE=" in watch.output
    assert "pending=4" in watch.output


def test_quality_repair_run_defaults_to_latest_all_background(tmp_path: Path, monkeypatch):
    release_root = _write_v3_quality_release(tmp_path)
    root = tmp_path / "running"
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    calls = []

    class FakeProcess:
        pid = 12345

    def fake_popen(command, *, stdout, stderr, start_new_session):
        calls.append((command, stderr, start_new_session))
        stdout.write("fake quality worker\n")
        stdout.flush()
        return FakeProcess()

    monkeypatch.setattr(cli_main.subprocess, "Popen", fake_popen)

    plan = runner.invoke(
        app,
        [
            "quality",
            "repair",
            "plan",
            "--root",
            str(root),
            "--release-root",
            str(release_root),
            "--plan",
            "qr_test",
        ],
    )
    assert plan.exit_code == 0

    result = runner.invoke(app, ["quality", "repair", "run", "--root", str(root)])

    assert result.exit_code == 0
    assert calls
    command = calls[0][0]
    assert "quality-repair-worker" in command
    assert "--plan" in command
    assert "qr_test" in command
    assert "--all" in command
    assert calls[0][1] is cli_main.subprocess.STDOUT
    assert calls[0][2] is True
    assert "Started quality repair worker pid=12345" in result.output
    assert "selected_jobs: 4" in result.output
    assert (root / ".krw_pipeline" / "quality" / "logs" / "worker.log").exists()


def test_docs_missing_repair_enqueues_general_pipeline_queue(tmp_path: Path):
    index_path = _write_quality_index(tmp_path)
    jobs = QualityShardScanner(index_path).build_repair_jobs(plan_id="qr_test", min_docs=5, kinds=[DOCS_MISSING])
    root = tmp_path / "running"
    store = QualityRepairStore(root)
    plan = store.add_plan(
        RepairPlan(
            plan_id="qr_test",
            global_spine_path=str(index_path),
            release_label="test",
            min_docs=5,
            job_ids=[],
            summary={},
        ),
        jobs,
    )

    result = run_repair_jobs(store=store, jobs=store.list_jobs(plan_id=plan.plan_id), root=root)

    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["skipped"] == 0
    assert result["enqueued"] == 1
    assert result["active"] == 0
    pipeline_jobs = PipelineQueue(root).list_jobs()
    assert len(pipeline_jobs) == 1
    assert pipeline_jobs[0].ticker == "FCX"
    assert pipeline_jobs[0].job_type == "full_refresh"
    assert pipeline_jobs[0].years == 3
    repaired = store.list_jobs(plan_id=plan.plan_id)[0]
    assert repaired.status == "succeeded"
    assert repaired.payload["pipeline_queue_action"] == "queued_full_refresh"


def test_docs_missing_repair_skips_existing_active_pipeline_job(tmp_path: Path):
    index_path = _write_quality_index(tmp_path)
    jobs = QualityShardScanner(index_path).build_repair_jobs(plan_id="qr_test", min_docs=5, kinds=[DOCS_MISSING])
    root = tmp_path / "running"
    pipeline_queue = PipelineQueue(root)
    active = pipeline_queue.add_job(
        "FCX",
        years=3,
        force=False,
        publish_root=None,
    )
    store = QualityRepairStore(root)
    plan = store.add_plan(
        RepairPlan(
            plan_id="qr_test",
            global_spine_path=str(index_path),
            release_label="test",
            min_docs=5,
            job_ids=[],
            summary={},
        ),
        jobs,
    )

    result = run_repair_jobs(store=store, jobs=store.list_jobs(plan_id=plan.plan_id), root=root)

    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["skipped"] == 0
    assert result["enqueued"] == 0
    assert result["active"] == 1
    assert len(PipelineQueue(root).list_jobs()) == 1
    repaired = store.list_jobs(plan_id=plan.plan_id)[0]
    assert repaired.payload["pipeline_queue_action"] == "skipped_active_job"
    assert repaired.payload["pipeline_queue_job_id"] == active.job_id


def _write_v3_quality_release(tmp_path: Path) -> Path:
    release_root = tmp_path / "releases" / "dev" / "v3-quality"
    companies_dir = release_root / "indexes" / "companies"
    companies_dir.mkdir(parents=True, exist_ok=True)

    ok_periods = [f"CY202{idx}" for idx in range(5)]
    fcx_periods = ["CY2025"]
    _write_quality_shard(
        companies_dir / "OK.sqlite",
        release_root=release_root,
        ticker="OK",
        periods=ok_periods,
        status="pass",
        quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        include_problem_events=False,
    )
    _write_quality_shard(
        companies_dir / "FCX.sqlite",
        release_root=release_root,
        ticker="FCX",
        periods=fcx_periods,
        status="fail",
        quality={
            "status": "fail",
            "missing_core_sections": ["item7"],
            "fail_reasons": ["multiple_core_sections_missing"],
            "warn_reasons": ["core_sections_missing"],
        },
        include_problem_events=True,
    )

    global_spine_path = release_root / "indexes" / "global_spine.sqlite"
    with sqlite3.connect(global_spine_path) as conn:
        create_global_spine_schema(conn)
        write_global_spine_metadata(
            conn,
            {
                "release_id": "v3-quality",
                "source_manifest_hash": "test-source",
            },
        )
        for ticker, periods in {"OK": ok_periods, "FCX": fcx_periods}.items():
            for period in periods:
                conn.execute(
                    """
                    INSERT INTO global_document_catalog(
                        document_id, ticker, company_name, document_type, period,
                        source_path, shard_id, shard_path, document_hash,
                        object_count, edge_count, quality_event_count, quality_status
                    )
                    VALUES (?, ?, ?, '10-K', ?, ?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        f"doc:{ticker}:10K:{period}",
                        ticker,
                        ticker,
                        period,
                        f"companies/{ticker}/ontology/10K/{period}/artifact_index.json",
                        ticker,
                        f"companies/{ticker}.sqlite",
                        f"doc-hash:{ticker}:{period}",
                        4 if ticker == "FCX" else 0,
                        "fail" if ticker == "FCX" else "pass",
                    ),
                )
        conn.commit()

    source_manifest_path = release_root / "source_manifest.json"
    source_manifest_path.write_text(
        json.dumps(
            {
                "format": "krw-ontology-source-manifest/v3-test",
                "tickers": ["FCX", "OK"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    fcx_shard = companies_dir / "FCX.sqlite"
    ok_shard = companies_dir / "OK.sqlite"
    shard_manifest_path = release_root / "indexes" / "shard_manifest.json"
    shard_manifest_path.write_text(
        json.dumps(
            {
                "format": "krw-ontology-shard-manifest/v3",
                "index_layout": "global-spine-and-company-shards",
                "release_id": "v3-quality",
                "shards": {
                    "FCX": {
                        "ticker": "FCX",
                        "path": "companies/FCX.sqlite",
                        "document_count": 1,
                        "object_count": 0,
                        "edge_count": 0,
                        "quality_event_count": 4,
                        "quality_summary": _company_shard_quality_summary(fcx_shard),
                        "sha256": _sha256(fcx_shard),
                    },
                    "OK": {
                        "ticker": "OK",
                        "path": "companies/OK.sqlite",
                        "document_count": 5,
                        "object_count": 0,
                        "edge_count": 0,
                        "quality_event_count": 0,
                        "quality_summary": _company_shard_quality_summary(ok_shard),
                        "sha256": _sha256(ok_shard),
                    },
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    write_release_manifest_v3(release_root, release_id="v3-quality", env="dev", source_root=release_root)
    return release_root


def _write_quality_shard(
    path: Path,
    *,
    release_root: Path,
    ticker: str,
    periods: list[str],
    status: str,
    quality: dict,
    include_problem_events: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE documents(
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            artifact_index_path TEXT NOT NULL,
            ontology_dir TEXT NOT NULL,
            sources_json TEXT NOT NULL,
            reports_json TEXT NOT NULL,
            counts_json TEXT NOT NULL,
            section_quality_status TEXT,
            section_quality_json TEXT NOT NULL,
            generated_at TEXT,
            schema_version TEXT
        )
        """
    )
    conn.execute("CREATE TABLE objects(id TEXT, ticker TEXT)")
    conn.execute("CREATE TABLE edges(id TEXT)")
    conn.execute(
        """
        CREATE TABLE quality_events(
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            object_id TEXT,
            stage TEXT,
            message TEXT NOT NULL,
            json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES('build', ?)",
        (json.dumps({"root": str(release_root), "ticker": ticker}),),
    )
    for period in periods:
        _insert_doc(conn, ticker=ticker, period=period, status=status, quality=quality, root=release_root)
    if include_problem_events:
        _insert_event(
            conn,
            event_id="quality:FCX:section",
            ticker="FCX",
            category="section_quality",
            severity="fail",
            stage="extract_sections",
            message="section_quality=fail",
            payload={"status": "fail"},
        )
        _insert_event(
            conn,
            event_id="batch_failure:FCX:0001",
            ticker="FCX",
            category="batch_failure",
            severity="error",
            stage="extract_assumption_candidates",
            message="Batch failure",
            payload={
                "batch_index": 1,
                "error_message": "extract_assumption_candidates: Claude CLI exited 143",
            },
        )
        _insert_event(
            conn,
            event_id="rejected:FCX:numeric",
            ticker="FCX",
            category="rejected_object",
            severity="warn",
            stage="numeric_guard",
            message="Unsupported numeric values: {'2,'}",
            payload={"rejection_stage": "numeric_guard"},
        )
        _insert_event(
            conn,
            event_id="rejected:FCX:ref",
            ticker="FCX",
            category="rejected_object",
            severity="warn",
            stage="reference_validation",
            message="Dangling references: [('to_id', 'missing')]",
            payload={"rejection_stage": "reference_validation"},
        )
    conn.commit()
    conn.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_normalize_numeric_repair_rebuilds_evidence_without_promoting_rejected(
    tmp_path: Path,
):
    root = tmp_path / "running"
    ontology_dir = _write_safe_repair_ontology(root)
    store = QualityRepairStore(root)
    job = RepairJob(
        job_id="qr_test-numeric",
        plan_id="qr_test",
        kind=NORMALIZE_NUMERIC,
        ticker="FCX",
        document_type="10-K",
        doc_type_key="10K",
        period="CY2025",
        stage="numeric_guard",
        ontology_dir=str(ontology_dir),
    )
    plan = store.add_plan(
        RepairPlan(
            plan_id="qr_test",
            global_spine_path="test",
            release_label="test",
            min_docs=5,
            job_ids=[],
            summary={},
        ),
        [job],
    )
    before_claims = read_jsonl(ontology_dir / "claims.jsonl")
    before_rejected = read_jsonl(ontology_dir / "rejected_objects.jsonl")

    result = run_repair_jobs(store=store, jobs=store.list_jobs(plan_id=plan.plan_id), root=root)

    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["skipped"] == 0
    assert result["unresolved"] == 1
    repaired = store.list_jobs(plan_id=plan.plan_id)[0]
    assert repaired.payload["policy"] == "evidence_preserving_revalidation"
    assert repaired.payload["auto_promoted_objects"] == 0
    assert repaired.payload["auto_modified_claims"] == 0
    assert read_jsonl(ontology_dir / "claims.jsonl") == before_claims
    assert read_jsonl(ontology_dir / "rejected_objects.jsonl") == before_rejected
    numeric_evidence = read_jsonl(ontology_dir / "numeric_evidence.jsonl")
    assert numeric_evidence
    report = json.loads(Path(repaired.payload["report_path"]).read_text(encoding="utf-8"))
    assert report["operation"] == "normalize_numeric"
    assert report["candidate_count"] == 1
    assert report["auto_promoted_objects"] == 0


def test_repair_reference_rebuilds_tail_without_guessing_references(tmp_path: Path):
    root = tmp_path / "running"
    ontology_dir = _write_safe_repair_ontology(root)
    store = QualityRepairStore(root)
    job = RepairJob(
        job_id="qr_test-ref",
        plan_id="qr_test",
        kind=REPAIR_REFERENCE,
        ticker="FCX",
        document_type="10-K",
        doc_type_key="10K",
        period="CY2025",
        stage="reference_validation",
        ontology_dir=str(ontology_dir),
    )
    plan = store.add_plan(
        RepairPlan(
            plan_id="qr_test",
            global_spine_path="test",
            release_label="test",
            min_docs=5,
            job_ids=[],
            summary={},
        ),
        [job],
    )
    before_claims = read_jsonl(ontology_dir / "claims.jsonl")
    before_rejected = read_jsonl(ontology_dir / "rejected_objects.jsonl")

    result = run_repair_jobs(store=store, jobs=store.list_jobs(plan_id=plan.plan_id), root=root)

    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["skipped"] == 0
    assert result["unresolved"] >= 1
    repaired = store.list_jobs(plan_id=plan.plan_id)[0]
    assert repaired.payload["policy"] == "evidence_preserving_revalidation"
    assert repaired.payload["auto_promoted_objects"] == 0
    assert repaired.payload["auto_modified_claims"] == 0
    assert read_jsonl(ontology_dir / "claims.jsonl") == before_claims
    assert read_jsonl(ontology_dir / "rejected_objects.jsonl") == before_rejected
    assert read_jsonl(ontology_dir / "support_links.jsonl")
    assert read_jsonl(ontology_dir / "edges.jsonl")
    report = json.loads(Path(repaired.payload["report_path"]).read_text(encoding="utf-8"))
    assert report["operation"] == "repair_reference"
    assert report["candidate_count"] == 1
    assert report["unresolved_count"] >= 1
    assert report["auto_promoted_objects"] == 0


def _write_quality_index(tmp_path: Path) -> Path:
    index_path = tmp_path / "indexes" / "agent_index.sqlite"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(index_path)
    conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE documents(
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            artifact_index_path TEXT NOT NULL,
            ontology_dir TEXT NOT NULL,
            sources_json TEXT NOT NULL,
            reports_json TEXT NOT NULL,
            counts_json TEXT NOT NULL,
            section_quality_status TEXT,
            section_quality_json TEXT NOT NULL,
            generated_at TEXT,
            schema_version TEXT
        )
        """
    )
    conn.execute("CREATE TABLE objects(id TEXT)")
    conn.execute(
        """
        CREATE TABLE quality_events(
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            document_type TEXT NOT NULL,
            doc_type_key TEXT NOT NULL,
            period TEXT NOT NULL,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            object_id TEXT,
            stage TEXT,
            message TEXT NOT NULL,
            json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES('build', ?)",
        (json.dumps({"root": str(tmp_path), "generated_at": "2026-06-01T00:00:00Z"}),),
    )
    for idx in range(5):
        _insert_doc(
            conn,
            ticker="OK",
            period=f"CY202{idx}",
            status="pass",
            quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
            root=tmp_path,
        )
    _insert_doc(
        conn,
        ticker="FCX",
        period="CY2025",
        status="fail",
        quality={
            "status": "fail",
            "missing_core_sections": ["item7"],
            "fail_reasons": ["multiple_core_sections_missing"],
            "warn_reasons": ["core_sections_missing"],
        },
        root=tmp_path,
    )
    _insert_event(
        conn,
        event_id="quality:FCX:section",
        ticker="FCX",
        category="section_quality",
        severity="fail",
        stage="extract_sections",
        message="section_quality=fail",
        payload={"status": "fail"},
    )
    _insert_event(
        conn,
        event_id="batch_failure:FCX:0001",
        ticker="FCX",
        category="batch_failure",
        severity="error",
        stage="extract_assumption_candidates",
        message="Batch failure",
        payload={
            "batch_index": 1,
            "error_message": "extract_assumption_candidates: Claude CLI exited 143",
        },
    )
    _insert_event(
        conn,
        event_id="rejected:FCX:numeric",
        ticker="FCX",
        category="rejected_object",
        severity="warn",
        stage="numeric_guard",
        message="Unsupported numeric values: {'2,'}",
        payload={"rejection_stage": "numeric_guard"},
    )
    _insert_event(
        conn,
        event_id="rejected:FCX:ref",
        ticker="FCX",
        category="rejected_object",
        severity="warn",
        stage="reference_validation",
        message="Dangling references: [('to_id', 'missing')]",
        payload={"rejection_stage": "reference_validation"},
    )
    conn.commit()
    conn.close()
    return index_path


def _insert_doc(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    period: str,
    status: str,
    quality: dict,
    root: Path,
) -> None:
    ontology_dir = root / "companies" / ticker / "ontology" / "10K" / period
    artifact_index_path = ontology_dir / "artifact_index.json"
    conn.execute(
        """
        INSERT INTO documents(
            ticker, document_type, doc_type_key, period, artifact_index_path,
            ontology_dir, sources_json, reports_json, counts_json,
            section_quality_status, section_quality_json, generated_at, schema_version
        )
        VALUES (?, '10-K', '10K', ?, ?, ?, '{}', '{}', '{}', ?, ?, NULL, 'test')
        """,
        (
            ticker,
            period,
            str(artifact_index_path),
            str(ontology_dir),
            status,
            json.dumps(quality),
        ),
    )


def _insert_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    ticker: str,
    category: str,
    severity: str,
    stage: str,
    message: str,
    payload: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO quality_events(
            id, ticker, document_type, doc_type_key, period,
            severity, category, object_id, stage, message, json
        )
        VALUES (?, ?, '10-K', '10K', 'CY2025', ?, ?, NULL, ?, ?, ?)
        """,
        (event_id, ticker, severity, category, stage, message, json.dumps(payload)),
    )


def _write_safe_repair_ontology(root: Path) -> Path:
    ontology_dir = root / "companies" / "FCX" / "ontology" / "10K" / "CY2025"
    source_document_id = "source:FCX:CY2025:10K"
    common = {
        "ticker": "FCX",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": "CY2025",
        "schema_version": "test",
    }
    write_jsonl(
        ontology_dir / "source_documents.jsonl",
        [{
            "id": source_document_id,
            "type": "SourceDocument",
            "ticker": "FCX",
            "document_type": "10-K",
            "period": "CY2025",
            "schema_version": "test",
        }],
    )
    write_jsonl(
        ontology_dir / "spans.jsonl",
        [{
            "id": "span:FCX:CY2025:10K:item7",
            "type": "SourceSpan",
            **common,
            "section_name": "item7",
            "text": "Revenue increased 12% year over year.",
        }],
    )
    write_jsonl(
        ontology_dir / "evidence_quotes.jsonl",
        [{
            "id": "quote:FCX:CY2025:10K:revenue",
            "type": "EvidenceQuote",
            **common,
            "source_span_id": "span:FCX:CY2025:10K:item7",
            "section_name": "item7",
            "quote_text": "Revenue increased 12% year over year.",
            "quote_type": "metric",
        }],
    )
    write_jsonl(
        ontology_dir / "claims.jsonl",
        [{
            "id": "claim:FCX:CY2025:10K:revenue",
            "type": "ResearchClaim",
            **common,
            "claim_text": "Revenue increased 12% year over year.",
            "claim_type": "financial_performance",
            "supported_by_quotes": ["quote:FCX:CY2025:10K:revenue"],
            "confidence": "high",
            "review_status": "accepted",
        }],
    )
    write_jsonl(
        ontology_dir / "rejected_objects.jsonl",
        [
            {
                "id": "claim:FCX:CY2025:10K:unsupported-number",
                "type": "ResearchClaim",
                **common,
                "claim_text": "Revenue increased 13% year over year.",
                "claim_type": "financial_performance",
                "supported_by_quotes": ["quote:FCX:CY2025:10K:revenue"],
                "confidence": "high",
                "review_status": "accepted",
                "rejection_stage": "numeric_guard",
                "rejection_reason": "Unsupported numeric values: {'13%'}",
            },
            {
                "id": "claim:FCX:CY2025:10K:missing-ref",
                "type": "ResearchClaim",
                **common,
                "claim_text": "Revenue increased year over year.",
                "claim_type": "financial_performance",
                "supported_by_quotes": ["quote:FCX:CY2025:10K:missing"],
                "confidence": "high",
                "review_status": "accepted",
                "rejection_stage": "reference_validation",
                "rejection_reason": (
                    "Dangling references: [('supported_by_quotes', "
                    "'quote:FCX:CY2025:10K:missing')]"
                ),
            },
        ],
    )
    return ontology_dir
