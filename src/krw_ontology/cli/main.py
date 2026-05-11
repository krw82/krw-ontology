"""Typer CLI app with 4 command skeleton."""

from __future__ import annotations

import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from krw_ontology.cli.config import (
    CONFIG_KEYS,
    cli_config_path,
    load_cli_config,
    resolve_publish_index_path,
    resolve_publish_root,
    resolve_running_root,
    set_config_value,
    unset_config_value,
)
from krw_ontology.cli.init_workspace import init_workspace
from krw_ontology.config.paths import ONTOLOGY_ROOT_ENV, resolve_ontology_root
from krw_ontology.pipeline.queue import (
    CANCELLED,
    FAILED,
    PENDING,
    RUNNING,
    SUCCEEDED,
    FileProcessLock,
    LockHeldError,
    PipelineQueue,
    QueueJob,
    is_pid_running,
)

app = typer.Typer(
    name="krw-ontology",
    help=(
        "Source-grounded equity research ontology CLI. Use `queue` for daily "
        "ticker research operations and `config` to save default roots."
    ),
    epilog=(
        "Recommended first setup:\n"
        "  krw-ontology config set running-root ~/krw-ontology-data-running\n"
        "  krw-ontology config set publish-root ~/krw-ontology-data\n\n"
        "Daily queue flow:\n"
        "  krw-ontology queue add CVX XOM COP\n"
        "  krw-ontology queue start\n"
        "  krw-ontology queue status\n"
        "  krw-ontology queue watch"
    ),
    no_args_is_help=True,
)
queue_app = typer.Typer(
    name="queue",
    help=(
        "Append, run, monitor, and stop ticker-level research jobs. Jobs are stored "
        "under <running-root>/.krw_pipeline and processed one ticker at a time."
    ),
    epilog=(
        "Typical flow:\n"
        "  krw-ontology queue add CVX XOM --years 3\n"
        "  krw-ontology queue start\n"
        "  krw-ontology queue status\n"
        "  krw-ontology queue watch\n\n"
        "Shutdown flow:\n"
        "  krw-ontology queue stop        # finish current job, then stop\n"
        "  krw-ontology queue kill        # terminate the worker process\n\n"
        "Use `krw-ontology config set running-root ...` and `publish-root ...` to avoid "
        "passing long paths on every command."
    ),
    no_args_is_help=True,
)
config_app = typer.Typer(
    name="config",
    help=(
        "Save local CLI defaults such as running-root and publish-root, so routine "
        "commands do not need long path arguments every time."
    ),
    epilog=(
        "Supported keys:\n"
        "  running-root        staging root where active pipeline artifacts are written\n"
        "  publish-root        stable root published for research/MCP use\n"
        "  publish-index-path  optional explicit stable SQLite index path\n\n"
        "Examples:\n"
        "  krw-ontology config set running-root ~/krw-ontology-data-running\n"
        "  krw-ontology config set publish-root ~/krw-ontology-data\n"
        "  krw-ontology config show"
    ),
    no_args_is_help=True,
)
app.add_typer(queue_app, name="queue")
app.add_typer(config_app, name="config")

ACCEPTED_DOC_TYPES = {"10-K", "10-Q"}
DEFAULT_E2E_TICKERS = ["AAPL", "NVDA", "JPM", "XOM"]


def validate_document_type(doc_type: str) -> str:
    """Raise typer.BadParameter if not in ACCEPTED_DOC_TYPES."""
    if doc_type not in ACCEPTED_DOC_TYPES:
        raise typer.BadParameter(
            f"Document type '{doc_type}' not supported. "
            f"Supported: {', '.join(sorted(ACCEPTED_DOC_TYPES))}"
        )
    return doc_type


def validate_config_key(key: str) -> str:
    if key not in CONFIG_KEYS:
        supported = ", ".join(sorted(CONFIG_KEYS))
        raise typer.BadParameter(f"Unknown config key '{key}'. Supported keys: {supported}")
    return key


@config_app.command("show")
def config_show_cmd() -> None:
    """Show the active CLI defaults and the config file path."""
    config = load_cli_config()
    path = cli_config_path()
    typer.echo(f"Config file: {path}")
    typer.echo(f"running-root: {config.running_root or '<unset>'}")
    typer.echo(f"publish-root: {config.publish_root or '<unset>'}")
    typer.echo(f"publish-index-path: {config.publish_index_path or '<unset>'}")


@config_app.command("set")
def config_set_cmd(
    key: str = typer.Argument(
        ...,
        help="Config key to set: running-root, publish-root, or publish-index-path.",
    ),
    value: str = typer.Argument(..., help="Filesystem path to save for this key."),
) -> None:
    """Set a persistent path default used by queue/update commands."""
    key = validate_config_key(key)
    set_config_value(key, value)
    typer.echo(f"Set {key}={Path(value).expanduser().resolve()}")
    typer.echo(f"Config file: {cli_config_path()}")


@config_app.command("unset")
def config_unset_cmd(
    key: str = typer.Argument(
        ...,
        help="Config key to clear: running-root, publish-root, or publish-index-path.",
    ),
) -> None:
    """Clear a persistent path default."""
    key = validate_config_key(key)
    unset_config_value(key)
    typer.echo(f"Unset {key}")
    typer.echo(f"Config file: {cli_config_path()}")


@app.command("init-workspace")
def init_workspace_cmd() -> None:
    """Create ontology schema directory with starter YAML configs."""
    init_workspace()


@app.command("build-evidence-ontology")
def build_evidence_ontology(
    ticker: str = typer.Argument(..., help="Stock ticker symbol"),
    document_type: str = typer.Option("10-K", "--document-type", help="Document type (10-K or 10-Q)"),
    latest: bool = typer.Option(False, "--latest", help="Use most recent filing"),
    period: Optional[str] = typer.Option(None, "--period", help="Explicit period override (e.g., FY2025)"),
    force: bool = typer.Option(False, "--force", help="Re-process even if content hash matches"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Override default output directory"),
) -> None:
    """Build evidence ontology from SEC filing for a given ticker."""
    validate_document_type(document_type)
    from krw_ontology.pipeline.orchestrator import run_pipeline

    output_root = resolve_ontology_root(output_dir)
    run_pipeline(
        ticker=ticker,
        document_type=document_type,
        latest=latest,
        period=period,
        force=force,
        output_dir=output_root,
    )
    typer.echo(f"Pipeline complete for {ticker}")


@app.command("e2e-matrix")
def e2e_matrix_cmd(
    tickers: Optional[list[str]] = typer.Argument(
        None,
        help="Ticker symbols to run. Defaults to AAPL NVDA JPM XOM.",
    ),
    document_type: str = typer.Option("10-K", "--document-type", help="Document type (10-K or 10-Q)"),
    latest: bool = typer.Option(True, "--latest/--no-latest", help="Use most recent filing"),
    force: bool = typer.Option(True, "--force/--no-force", help="Re-process even if checkpoints exist"),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        help="Output root. Defaults to a timestamped directory under the system temp dir.",
    ),
    continue_on_error: bool = typer.Option(
        False,
        "--continue-on-error",
        help="Continue remaining tickers if one pipeline run fails.",
    ),
) -> None:
    """Run full e2e pipeline for a ticker matrix."""
    validate_document_type(document_type)

    from krw_ontology.pipeline.orchestrator import run_pipeline

    run_tickers = [t.upper() for t in (tickers or DEFAULT_E2E_TICKERS)]
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = (
        resolve_ontology_root(output_dir)
        if output_dir or os.environ.get(ONTOLOGY_ROOT_ENV)
        else Path(tempfile.gettempdir()) / f"krw-e2e-refactor-{timestamp}"
    )
    root.mkdir(parents=True, exist_ok=True)

    typer.echo(f"OUTPUT_ROOT={root}")
    failures: list[tuple[str, str]] = []

    for ticker in run_tickers:
        typer.echo("")
        typer.echo(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] START ticker={ticker}")
        try:
            run_pipeline(
                ticker=ticker,
                document_type=document_type,
                latest=latest,
                period=None,
                force=force,
                output_dir=root,
            )
        except Exception as exc:
            failures.append((ticker, str(exc)))
            typer.echo(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"FAILED ticker={ticker}: {exc}"
            )
            if not continue_on_error:
                typer.echo(f"OUTPUT_ROOT={root}")
                raise typer.Exit(1) from exc
        else:
            typer.echo(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] END ticker={ticker}")

    typer.echo("")
    typer.echo(f"OUTPUT_ROOT={root}")
    if failures:
        typer.echo("Failures:")
        for ticker, reason in failures:
            typer.echo(f"- {ticker}: {reason}")
        raise typer.Exit(1)


@app.command("build-research-pipeline")
def build_research_pipeline_cmd(
    tickers: list[str] = typer.Argument(
        ...,
        help="Ticker symbols to run.",
    ),
    years: int = typer.Option(
        3,
        "--years",
        min=1,
        help="Number of latest 10-K report years to include.",
    ),
    force: bool = typer.Option(
        False,
        "--force/--no-force",
        help="Re-process even if checkpoints exist.",
    ),
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help=(
            "Ontology data root. Defaults to KRW_ONTOLOGY_ROOT or "
            "~/krw-ontology-data for this research command."
        ),
    ),
    index_path: Optional[Path] = typer.Option(
        None,
        "--index-path",
        help="SQLite agent index path. Defaults to <root>/indexes/agent_index.sqlite.",
    ),
    publish_root: Optional[Path] = typer.Option(
        None,
        "--publish-root",
        help=(
            "Stable ontology data root to publish each completed ticker into. "
            "When set, companies/<TICKER> is copied there after ticker context is built."
        ),
    ),
    publish_index_path: Optional[Path] = typer.Option(
        None,
        "--publish-index-path",
        help="Stable SQLite index path. Defaults to <publish-root>/indexes/agent_index.sqlite.",
    ),
    continue_on_error: bool = typer.Option(
        False,
        "--continue-on-error",
        help="Continue remaining filings if one pipeline run fails.",
    ),
) -> None:
    """Build the default research set: latest 3 years of 10-K plus 10-Qs."""
    from krw_ontology.agent_index import build_agent_index
    from krw_ontology.config.settings import PipelineConfig
    from krw_ontology.pipeline.stages.build_company_context import build_company_context
    from krw_ontology.pipeline.orchestrator import run_pipeline
    from krw_ontology.pipeline.research_plan import discover_research_filing_targets

    run_tickers = [ticker.upper() for ticker in tickers]
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    stable_root = resolve_publish_root(publish_root)
    resolved_publish_index_path = resolve_publish_index_path(publish_index_path)
    output_root.mkdir(parents=True, exist_ok=True)
    if stable_root is not None:
        stable_root.mkdir(parents=True, exist_ok=True)
    config = PipelineConfig.load()
    failures: list[tuple[str, str, str]] = []
    stopped_after_failure = False
    index_failure: Exception | None = None

    typer.echo(f"OUTPUT_ROOT={output_root}")
    if stable_root is not None:
        typer.echo(f"PUBLISH_ROOT={stable_root}")
    typer.echo(f"Research scope: {years} latest 10-K report years + matching 10-Q periods")

    try:
        for ticker in run_tickers:
            ticker_failed = False
            typer.echo("")
            typer.echo(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] PLAN ticker={ticker}")
            try:
                targets = discover_research_filing_targets(ticker, years=years, config=config)
            except Exception as exc:
                ticker_failed = True
                failures.append((ticker, "plan", str(exc)))
                typer.echo(f"FAILED ticker={ticker} stage=plan: {exc}")
                if not continue_on_error:
                    stopped_after_failure = True
                    break
                continue

            typer.echo(f"Planned {len(targets)} filings for {ticker}:")
            for target in targets:
                typer.echo(f"- {target.document_type} {target.period} filed={target.filing_date}")

            for target in targets:
                label = f"{target.ticker} {target.document_type} {target.period}"
                typer.echo("")
                typer.echo(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] START {label}")
                try:
                    run_pipeline(
                        ticker=target.ticker,
                        document_type=target.document_type,
                        latest=False,
                        period=target.period,
                        force=force,
                        output_dir=output_root,
                    )
                except Exception as exc:
                    ticker_failed = True
                    failures.append((target.ticker, f"{target.document_type} {target.period}", str(exc)))
                    typer.echo(
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"FAILED {label}: {exc}"
                    )
                    if not continue_on_error:
                        stopped_after_failure = True
                        break
                else:
                    typer.echo(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] END {label}")

            if stopped_after_failure:
                break

            typer.echo("")
            typer.echo(f"Building company context for {ticker}...")
            try:
                context_result = build_company_context(output_root, ticker)
            except Exception as exc:
                ticker_failed = True
                failures.append((ticker, "company_context", str(exc)))
                typer.echo(f"FAILED ticker={ticker} stage=company_context: {exc}")
                if not continue_on_error:
                    stopped_after_failure = True
                    break
            else:
                typer.echo(
                    "Company context built: "
                    f"{context_result['artifact_index_path']} "
                    f"counts={context_result['counts']}"
                )

            if stopped_after_failure:
                break

            if stable_root is not None:
                if ticker_failed:
                    typer.echo(f"Skipping publish for {ticker} because the ticker had failures.")
                    continue
                typer.echo("")
                typer.echo(f"Publishing completed ticker {ticker} to stable root...")
                try:
                    with FileProcessLock(PipelineQueue(stable_root).publish_lock_path):
                        _publish_ticker_tree(output_root, stable_root, ticker)
                        publish_index_result = build_agent_index(
                            stable_root,
                            index_path=resolved_publish_index_path,
                            force=True,
                        )
                except Exception as exc:
                    failures.append((ticker, "publish", str(exc)))
                    typer.echo(f"FAILED ticker={ticker} stage=publish: {exc}")
                    if not continue_on_error:
                        stopped_after_failure = True
                        break
                    continue
                totals = publish_index_result["totals"]
                typer.echo(f"Published {ticker} to {stable_root}")
                typer.echo(f"Stable agent index built: {publish_index_result['index_path']}")
                typer.echo(
                    "Stable index contains "
                    f"{totals['documents']} documents, "
                    f"{totals['objects']} objects, "
                    f"{totals['edges']} edges, "
                    f"{totals['quality_events']} quality events"
                )
    finally:
        typer.echo("")
        typer.echo("Rebuilding agent index...")
        try:
            result = build_agent_index(output_root, index_path=index_path, force=True)
        except Exception as exc:
            index_failure = exc
            typer.echo(f"Agent index rebuild failed: {exc}")
        else:
            totals = result["totals"]
            typer.echo(f"Agent index built: {result['index_path']}")
            typer.echo(
                "Indexed "
                f"{totals['documents']} documents, "
                f"{totals['objects']} objects, "
                f"{totals['edges']} edges, "
                f"{totals['quality_events']} quality events"
            )

    typer.echo("")
    typer.echo(f"OUTPUT_ROOT={output_root}")
    if failures:
        typer.echo("Failures:")
        for ticker, stage, reason in failures:
            typer.echo(f"- {ticker} {stage}: {reason}")
    if failures or index_failure is not None:
        raise typer.Exit(1)


def _now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _queue_emit(store: PipelineQueue, job: QueueJob, message: str) -> None:
    line = f"[{_now_label()}] {message}"
    typer.echo(line)
    store.append_job_log(job.job_id, line)


def _process_queue_job(store: PipelineQueue, job: QueueJob, output_root: Path) -> None:
    from krw_ontology.agent_index import build_agent_index
    from krw_ontology.config.settings import PipelineConfig
    from krw_ontology.pipeline.orchestrator import run_pipeline
    from krw_ontology.pipeline.research_plan import discover_research_filing_targets
    from krw_ontology.pipeline.stages.build_company_context import build_company_context

    job = store.mark_running(job)
    _queue_emit(store, job, f"START job={job.job_id} ticker={job.ticker}")
    try:
        config = PipelineConfig.load()
        targets = discover_research_filing_targets(job.ticker, years=job.years, config=config)
        _queue_emit(store, job, f"Planned {len(targets)} filings for {job.ticker}")
        for target in targets:
            label = f"{target.ticker} {target.document_type} {target.period}"
            _queue_emit(store, job, f"START {label}")
            run_pipeline(
                ticker=target.ticker,
                document_type=target.document_type,
                latest=False,
                period=target.period,
                force=job.force,
                output_dir=output_root,
            )
            _queue_emit(store, job, f"END {label}")

        _queue_emit(store, job, f"Building company context for {job.ticker}")
        context_result = build_company_context(output_root, job.ticker)
        _queue_emit(
            store,
            job,
            "Company context built: "
            f"{context_result['artifact_index_path']} counts={context_result['counts']}",
        )

        if job.publish_root:
            stable_root = resolve_ontology_root(Path(job.publish_root), fallback_to_cwd=False)
            stable_root.mkdir(parents=True, exist_ok=True)
            publish_index_path = (
                Path(job.publish_index_path) if job.publish_index_path is not None else None
            )
            _queue_emit(store, job, f"Publishing {job.ticker} to stable root {stable_root}")
            with FileProcessLock(PipelineQueue(stable_root).publish_lock_path):
                _publish_ticker_tree(output_root, stable_root, job.ticker)
                index_result = build_agent_index(
                    stable_root,
                    index_path=publish_index_path,
                    force=True,
                )
            totals = index_result["totals"]
            _queue_emit(
                store,
                job,
                "Stable index built: "
                f"{index_result['index_path']} "
                f"documents={totals['documents']} "
                f"objects={totals['objects']} "
                f"edges={totals['edges']} "
                f"quality_events={totals['quality_events']}",
            )
        else:
            _queue_emit(store, job, "No publish root configured; rebuilding staging agent index")
            index_result = build_agent_index(output_root, force=True)
            totals = index_result["totals"]
            _queue_emit(
                store,
                job,
                "Staging index built: "
                f"{index_result['index_path']} "
                f"documents={totals['documents']} "
                f"objects={totals['objects']} "
                f"edges={totals['edges']} "
                f"quality_events={totals['quality_events']}",
            )
    except Exception as exc:
        store.mark_failed(job, str(exc))
        _queue_emit(store, job, f"FAILED job={job.job_id} ticker={job.ticker}: {exc}")
        return

    store.mark_succeeded(job)
    _queue_emit(store, job, f"SUCCEEDED job={job.job_id} ticker={job.ticker}")


@queue_app.command(
    "add",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue add CVX XOM COP\n"
        "  krw-ontology queue add CVX --years 3 --force\n"
        "  krw-ontology queue add LNG --root /path/to/running --publish-root /path/to/stable\n\n"
        "A job is ticker-level: one queued ticker expands to the configured 10-K/10-Q research set, "
        "then builds company context and publishes once after the ticker succeeds."
    ),
)
@app.command("queue-add", hidden=True)
def queue_add_cmd(
    tickers: list[str] = typer.Argument(..., help="Ticker symbols to append to the queue."),
    years: int = typer.Option(
        3,
        "--years",
        min=1,
        help="Number of latest 10-K report years to include.",
    ),
    force: bool = typer.Option(
        False,
        "--force/--no-force",
        help="Re-process even if checkpoints exist.",
    ),
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Staging/running ontology data root.",
    ),
    publish_root: Optional[Path] = typer.Option(
        None,
        "--publish-root",
        help="Stable ontology data root to publish each completed ticker into.",
    ),
    publish_index_path: Optional[Path] = typer.Option(
        None,
        "--publish-index-path",
        help="Stable SQLite index path. Defaults to <publish-root>/indexes/agent_index.sqlite.",
    ),
    allow_duplicate: bool = typer.Option(
        False,
        "--allow-duplicate/--skip-duplicate",
        help="Allow adding a ticker even if it already has a pending/running job.",
    ),
) -> None:
    """Append ticker-level research jobs to the local queue."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    stable_root = resolve_publish_root(publish_root)
    resolved_publish_index_path = resolve_publish_index_path(publish_index_path)
    store = PipelineQueue(output_root)
    store.ensure_dirs()

    typer.echo(f"QUEUE_ROOT={store.queue_dir}")
    added = 0
    for ticker in tickers:
        normalized = ticker.upper()
        active = store.active_job_for_ticker(normalized)
        if active is not None and not allow_duplicate:
            typer.echo(
                f"Skipped {normalized}; active job already exists: "
                f"{active.job_id} status={active.status}"
            )
            continue
        job = store.add_job(
            normalized,
            years=years,
            force=force,
            publish_root=stable_root,
            publish_index_path=resolved_publish_index_path,
        )
        added += 1
        typer.echo(f"Queued {job.ticker} job={job.job_id} years={job.years}")
    typer.echo(f"Added {added} job(s)")


@queue_app.command(
    "run",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue run\n"
        "  krw-ontology queue run --watch\n"
        "  krw-ontology queue run --max-jobs 1\n\n"
        "This runs in the foreground. Use `queue start` for a detached background worker."
    ),
)
@app.command("queue-run", hidden=True)
def queue_run_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Staging/running ontology data root.",
    ),
    watch: bool = typer.Option(
        False,
        "--watch/--no-watch",
        help="Keep waiting for newly appended jobs after the queue is drained.",
    ),
    poll_interval: float = typer.Option(
        15.0,
        "--poll-interval",
        min=1.0,
        help="Seconds to wait between queue polls in --watch mode.",
    ),
    max_jobs: Optional[int] = typer.Option(
        None,
        "--max-jobs",
        min=1,
        help="Stop after processing this many jobs. Primarily useful for tests/manual drains.",
    ),
) -> None:
    """Run queued ticker jobs in the foreground."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    output_root.mkdir(parents=True, exist_ok=True)
    store = PipelineQueue(output_root)
    store.ensure_dirs()

    try:
        with FileProcessLock(store.worker_lock_path):
            store.clear_stop_request()
            store.write_worker_pid(os.getpid())
            typer.echo(f"[{_now_label()}] Queue worker started root={output_root}")
            processed = 0
            while True:
                if store.stop_requested():
                    typer.echo(f"[{_now_label()}] Stop requested; worker exiting")
                    break
                job = store.next_pending_job()
                if job is None:
                    if not watch:
                        typer.echo(f"[{_now_label()}] Queue drained")
                        break
                    time.sleep(poll_interval)
                    continue

                _process_queue_job(store, job, output_root)
                processed += 1
                if max_jobs is not None and processed >= max_jobs:
                    typer.echo(f"[{_now_label()}] Reached --max-jobs={max_jobs}")
                    break
    except LockHeldError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    finally:
        store.clear_worker_pid(os.getpid())


@queue_app.command(
    "start",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue start\n"
        "  krw-ontology queue start --poll-interval 5\n\n"
        "The worker logs to <running-root>/.krw_pipeline/logs/worker.log. "
        "Use `queue watch` to follow that log."
    ),
)
@app.command("queue-start", hidden=True)
def queue_start_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Staging/running ontology data root.",
    ),
    poll_interval: float = typer.Option(
        15.0,
        "--poll-interval",
        min=1.0,
        help="Seconds to wait between queue polls.",
    ),
) -> None:
    """Start a detached background queue worker."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    output_root.mkdir(parents=True, exist_ok=True)
    store = PipelineQueue(output_root)
    store.ensure_dirs()
    if store.worker_is_running():
        typer.echo("Queue worker is already running.")
        raise typer.Exit(1)

    command = [
        sys.executable,
        "-c",
        "from krw_ontology.cli.main import app; app()",
        "queue-run",
        "--root",
        str(output_root),
        "--watch",
        "--poll-interval",
        str(poll_interval),
    ]
    with store.worker_log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n[{_now_label()}] queue-start launching background worker\n")
        log_handle.flush()
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    typer.echo(f"Started queue worker pid={process.pid}")
    typer.echo(f"Log: {store.worker_log_path}")


@queue_app.command(
    "stop",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue stop\n"
        "  krw-ontology queue stop --wait --timeout 60\n\n"
        "This is graceful: it requests the worker to finish the current ticker job and exit "
        "before taking another job. It does not kill the process. Use `queue kill` if you "
        "need to terminate immediately."
    ),
)
@app.command("queue-stop", hidden=True)
def queue_stop_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Staging/running ontology data root.",
    ),
    wait: bool = typer.Option(
        False,
        "--wait/--no-wait",
        help="Wait for the worker to exit after it finishes the current job.",
    ),
    timeout: float = typer.Option(
        10.0,
        "--timeout",
        min=0.0,
        help="Seconds to wait when --wait is set.",
    ),
) -> None:
    """Request a graceful queue worker stop after the current job finishes."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    store = PipelineQueue(output_root)
    store.request_stop()
    pid = store.worker_pid()
    if pid is None:
        typer.echo("Stop requested. No queue worker pid file found.")
        return
    if not is_pid_running(pid):
        store.clear_worker_pid(pid)
        typer.echo(f"Stop requested. Queue worker pid={pid} is not running; cleared stale pid file.")
        return

    typer.echo(f"Stop requested for queue worker pid={pid}. It will exit before starting the next job.")
    if not wait:
        return

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_running(pid):
            store.clear_worker_pid(pid)
            store.clear_stop_request()
            typer.echo("Queue worker stopped.")
            return
        time.sleep(0.25)

    typer.echo(f"Queue worker pid={pid} is still running; current job may still be active.")
    raise typer.Exit(1)


@queue_app.command(
    "kill",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue kill\n"
        "  krw-ontology queue kill --force --timeout 0\n\n"
        "This terminates the worker process and can leave the currently running ticker with "
        "partial artifacts. Prefer `queue stop` unless you need an immediate interrupt."
    ),
)
@app.command("queue-kill", hidden=True)
def queue_kill_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Staging/running ontology data root.",
    ),
    force: bool = typer.Option(
        False,
        "--force/--no-force",
        help="Escalate to SIGKILL if the worker does not stop after SIGTERM.",
    ),
    timeout: float = typer.Option(
        10.0,
        "--timeout",
        min=0.0,
        help="Seconds to wait for SIGTERM shutdown before optional --force escalation.",
    ),
) -> None:
    """Terminate the background queue worker process."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    store = PipelineQueue(output_root)
    pid = store.worker_pid()
    if pid is None:
        typer.echo("No queue worker pid file found.")
        return
    if not is_pid_running(pid):
        store.clear_worker_pid(pid)
        typer.echo(f"Queue worker pid={pid} is not running; cleared stale pid file.")
        return

    typer.echo(f"Terminating queue worker pid={pid} with SIGTERM")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_running(pid):
            store.clear_worker_pid(pid)
            store.clear_stop_request()
            typer.echo("Queue worker terminated.")
            return
        time.sleep(0.25)

    if not force:
        typer.echo(f"Queue worker pid={pid} is still running. Re-run with --force to SIGKILL.")
        raise typer.Exit(1)

    typer.echo(f"Force killing queue worker pid={pid} with SIGKILL")
    os.kill(pid, signal.SIGKILL)
    store.clear_worker_pid(pid)
    store.clear_stop_request()
    typer.echo("Queue worker kill signal sent.")


@queue_app.command(
    "cancel",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue cancel 20260509010101-CVX-abc12345\n"
        "  krw-ontology queue cancel <job-id> --reason \"no longer needed\"\n\n"
        "By default only pending jobs are cancelled. Cancelling a running job does not stop "
        "the worker process; use `queue stop` or `queue kill` for the worker."
    ),
)
@app.command("queue-cancel", hidden=True)
def queue_cancel_cmd(
    job_ids: list[str] = typer.Argument(..., help="Queue job ids to cancel."),
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Staging/running ontology data root.",
    ),
    reason: str = typer.Option("cancelled by user", "--reason", help="Cancellation reason."),
    allow_running: bool = typer.Option(
        False,
        "--allow-running/--pending-only",
        help="Mark running jobs cancelled too. This does not kill the worker process.",
    ),
) -> None:
    """Cancel pending queue jobs."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    store = PipelineQueue(output_root)
    cancelled = 0
    for job_id in job_ids:
        try:
            job = store.load_job(job_id)
        except FileNotFoundError:
            typer.echo(f"Job not found: {job_id}")
            raise typer.Exit(1)
        if job.status == PENDING or (allow_running and job.status == RUNNING):
            store.mark_cancelled(job, reason)
            typer.echo(f"Cancelled {job.ticker} job={job.job_id} status={job.status}")
            cancelled += 1
            continue
        typer.echo(f"Skipped {job.ticker} job={job.job_id}; status={job.status}")
    typer.echo(f"Cancelled {cancelled} job(s)")


@queue_app.command(
    "status",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue status\n"
        "  krw-ontology queue status --limit 50\n\n"
        "Shows worker state, stop-request state, status counts, and recent jobs."
    ),
)
@app.command("queue-status", hidden=True)
def queue_status_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Staging/running ontology data root.",
    ),
    limit: int = typer.Option(20, "--limit", min=1, help="Number of recent jobs to show."),
) -> None:
    """Show queue worker and job status."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    store = PipelineQueue(output_root)
    store.ensure_dirs()
    worker_state = "running" if store.worker_is_running() else "stopped"
    pid = store.worker_pid()
    typer.echo(f"QUEUE_ROOT={store.queue_dir}")
    typer.echo(f"Worker: {worker_state}" + (f" pid={pid}" if pid is not None else ""))
    typer.echo(f"Stop requested: {'yes' if store.stop_requested() else 'no'}")

    jobs = store.list_jobs()
    counts = {PENDING: 0, RUNNING: 0, SUCCEEDED: 0, FAILED: 0, CANCELLED: 0}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
    typer.echo(
        "Jobs: "
        f"pending={counts.get(PENDING, 0)} "
        f"running={counts.get(RUNNING, 0)} "
        f"succeeded={counts.get(SUCCEEDED, 0)} "
        f"failed={counts.get(FAILED, 0)} "
        f"cancelled={counts.get(CANCELLED, 0)}"
    )
    for job in jobs[-limit:]:
        typer.echo(
            f"- {job.status} {job.ticker} years={job.years} "
            f"attempts={job.attempts} job={job.job_id}"
        )
        if job.error:
            typer.echo(f"  error={job.error}")


def _tail_text(path: Path, lines: int) -> str:
    if lines <= 0:
        return ""
    content = path.read_text(encoding="utf-8")
    return "\n".join(content.splitlines()[-lines:])


def _show_queue_log(
    *,
    root: Path | None,
    job_id: str | None,
    lines: int,
    follow: bool,
) -> None:
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    store = PipelineQueue(output_root)
    path = store.job_log_path(job_id) if job_id is not None else store.worker_log_path
    if not path.exists():
        typer.echo(f"Log does not exist: {path}")
        raise typer.Exit(1)

    tail = _tail_text(path, lines)
    if tail:
        typer.echo(tail)
    if not follow:
        return

    with path.open("r", encoding="utf-8") as handle:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                typer.echo(line.rstrip())
            else:
                time.sleep(1)


@queue_app.command(
    "log",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue log\n"
        "  krw-ontology queue log --follow\n"
        "  krw-ontology queue log --job-id <job-id> --lines 200\n\n"
        "Without --job-id this reads the worker log. With --job-id it reads that specific "
        "ticker job log."
    ),
)
@app.command("queue-log", hidden=True)
def queue_log_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Staging/running ontology data root.",
    ),
    job_id: Optional[str] = typer.Option(
        None,
        "--job-id",
        help="Show a specific job log instead of the worker log.",
    ),
    lines: int = typer.Option(80, "--lines", min=1, help="Number of trailing lines to show."),
    follow: bool = typer.Option(False, "--follow/--no-follow", help="Follow appended log output."),
) -> None:
    """Show the background worker log or a specific job log."""
    _show_queue_log(root=root, job_id=job_id, lines=lines, follow=follow)


@queue_app.command(
    "watch",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue watch\n"
        "  krw-ontology queue watch --job-id <job-id>\n\n"
        "Convenience alias for following logs. Use Ctrl+C to stop watching; the worker keeps running."
    ),
)
def queue_watch_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Staging/running ontology data root.",
    ),
    job_id: Optional[str] = typer.Option(
        None,
        "--job-id",
        help="Follow a specific job log instead of the worker log.",
    ),
    lines: int = typer.Option(80, "--lines", min=1, help="Number of trailing lines to show first."),
) -> None:
    """Follow queue logs. Equivalent to `queue log --follow`."""
    _show_queue_log(root=root, job_id=job_id, lines=lines, follow=True)


@app.command("build-company-context")
def build_company_context_cmd(
    ticker: str = typer.Argument(..., help="Stock ticker symbol"),
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Ontology data root. Defaults to KRW_ONTOLOGY_ROOT or the current working directory.",
    ),
    rebuild_agent_index: bool = typer.Option(
        True,
        "--rebuild-agent-index/--no-rebuild-agent-index",
        help="Rebuild agent_index.sqlite after writing company context artifacts.",
    ),
) -> None:
    """Build company-level profile and temporal artifacts from existing filings."""
    from krw_ontology.agent_index import build_agent_index
    from krw_ontology.pipeline.stages.build_company_context import build_company_context

    ticker = ticker.upper()
    output_root = resolve_ontology_root(root)
    result = build_company_context(output_root, ticker)
    typer.echo(f"Company context built: {result['artifact_index_path']}")
    typer.echo(f"Counts: {result['counts']}")
    if rebuild_agent_index:
        index_result = build_agent_index(output_root, force=True)
        totals = index_result["totals"]
        typer.echo(f"Agent index built: {index_result['index_path']}")
        typer.echo(
            "Indexed "
            f"{totals['documents']} documents, "
            f"{totals['objects']} objects, "
            f"{totals['edges']} edges, "
            f"{totals['quality_events']} quality events"
        )


@app.command("update-ticker")
def update_ticker_cmd(
    ticker: str = typer.Argument(..., help="Ticker symbol to update."),
    document_type: str = typer.Option("10-Q", "--document-type", help="Document type to update (10-K or 10-Q)."),
    periods: Optional[list[str]] = typer.Option(
        None,
        "--period",
        help=(
            "Explicit period to update, e.g. FY2026Q1. Repeat this option to update "
            "multiple periods for the ticker before publishing."
        ),
    ),
    latest: bool = typer.Option(False, "--latest", help="Update the latest filing for the selected document type."),
    force: bool = typer.Option(False, "--force/--no-force", help="Re-process even if checkpoints exist."),
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help=(
            "Staging/running ontology data root. Defaults to KRW_ONTOLOGY_ROOT or "
            "~/krw-ontology-data."
        ),
    ),
    publish: bool = typer.Option(
        True,
        "--publish/--no-publish",
        help="Publish the completed ticker to the stable root after context rebuild.",
    ),
    publish_root: Optional[Path] = typer.Option(
        None,
        "--publish-root",
        help="Stable ontology data root. Required unless --no-publish is set.",
    ),
    publish_index_path: Optional[Path] = typer.Option(
        None,
        "--publish-index-path",
        help="Stable SQLite index path. Defaults to <publish-root>/indexes/agent_index.sqlite.",
    ),
) -> None:
    """Update one ticker with one or more new filings, rebuild context, then optionally publish."""
    validate_document_type(document_type)
    selected_periods = list(periods or [])
    if latest and selected_periods:
        typer.echo("Use either --latest or --period, not both.")
        raise typer.Exit(1)
    if not latest and not selected_periods:
        typer.echo("Specify at least one --period <PERIOD> or --latest.")
        raise typer.Exit(1)
    resolved_publish_root = resolve_publish_root(publish_root)
    if publish and resolved_publish_root is None:
        typer.echo("Specify --publish-root when publishing, or pass --no-publish.")
        raise typer.Exit(1)

    from krw_ontology.agent_index import build_agent_index
    from krw_ontology.pipeline.orchestrator import run_pipeline
    from krw_ontology.pipeline.stages.build_company_context import build_company_context

    ticker = ticker.upper()
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    stable_root = resolved_publish_root
    resolved_publish_index_path = resolve_publish_index_path(publish_index_path)
    output_root.mkdir(parents=True, exist_ok=True)
    if stable_root is not None:
        stable_root.mkdir(parents=True, exist_ok=True)

    filing_periods: list[Optional[str]] = [None] if latest else selected_periods
    target_label = "latest" if latest else ", ".join(selected_periods)
    label = f"{ticker} {document_type} {target_label}"
    typer.echo(f"OUTPUT_ROOT={output_root}")
    if stable_root is not None:
        typer.echo(f"PUBLISH_ROOT={stable_root}")
    typer.echo(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] START update {label}")

    for filing_period in filing_periods:
        filing_label = f"{ticker} {document_type} {'latest' if latest else filing_period}"
        try:
            run_pipeline(
                ticker=ticker,
                document_type=document_type,
                latest=latest,
                period=filing_period,
                force=force,
                output_dir=output_root,
            )
        except Exception as exc:
            typer.echo(f"FAILED {filing_label} stage=filing_pipeline: {exc}")
            raise typer.Exit(1) from exc
        typer.echo(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"Filing pipeline complete for {filing_label}"
        )

    typer.echo(f"Building company context for {ticker}...")
    try:
        context_result = build_company_context(output_root, ticker)
    except Exception as exc:
        typer.echo(f"FAILED {ticker} stage=company_context: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(
        "Company context built: "
        f"{context_result['artifact_index_path']} "
        f"counts={context_result['counts']}"
    )

    if not publish:
        typer.echo(f"Updated {ticker} in staging root only; publish skipped.")
        return

    assert stable_root is not None
    typer.echo(f"Publishing {ticker} to stable root...")
    try:
        with FileProcessLock(PipelineQueue(stable_root).publish_lock_path):
            _publish_ticker_tree(output_root, stable_root, ticker)
            index_result = build_agent_index(
                stable_root,
                index_path=resolved_publish_index_path,
                force=True,
            )
    except Exception as exc:
        typer.echo(f"FAILED {ticker} stage=publish: {exc}")
        raise typer.Exit(1) from exc

    totals = index_result["totals"]
    typer.echo(f"Published {ticker} to {stable_root}")
    typer.echo(f"Stable agent index built: {index_result['index_path']}")
    typer.echo(
        "Stable index contains "
        f"{totals['documents']} documents, "
        f"{totals['objects']} objects, "
        f"{totals['edges']} edges, "
        f"{totals['quality_events']} quality events"
    )
    typer.echo(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] END update {label}")


@app.command("publish-ticker")
def publish_ticker_cmd(
    tickers: list[str] = typer.Argument(..., help="Ticker symbols to publish."),
    from_root: Path = typer.Option(
        ...,
        "--from-root",
        help="Staging/running ontology data root to copy from.",
    ),
    to_root: Path = typer.Option(
        ...,
        "--to-root",
        help="Stable/published ontology data root to copy into.",
    ),
    rebuild_agent_index: bool = typer.Option(
        True,
        "--rebuild-agent-index/--no-rebuild-agent-index",
        help="Rebuild the stable root agent index after publishing.",
    ),
    index_path: Optional[Path] = typer.Option(
        None,
        "--index-path",
        help="Stable SQLite index path. Defaults to <to-root>/indexes/agent_index.sqlite.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be published without copying files.",
    ),
) -> None:
    """Publish completed ticker artifacts from a staging root into a stable root."""
    from krw_ontology.agent_index import build_agent_index

    source_root = resolve_ontology_root(from_root)
    stable_root = resolve_ontology_root(to_root)
    run_tickers = [ticker.upper() for ticker in tickers]

    if dry_run:
        for ticker in run_tickers:
            source_dir = source_root / "companies" / ticker
            target_dir = stable_root / "companies" / ticker
            typer.echo(f"Publishing {ticker}: {source_dir} -> {target_dir}")
        typer.echo("Dry run complete; no files changed.")
        return

    try:
        with FileProcessLock(PipelineQueue(stable_root).publish_lock_path):
            for ticker in run_tickers:
                source_dir = source_root / "companies" / ticker
                target_dir = stable_root / "companies" / ticker
                typer.echo(f"Publishing {ticker}: {source_dir} -> {target_dir}")
                _publish_ticker_tree(source_root, stable_root, ticker)

            if rebuild_agent_index:
                result = build_agent_index(stable_root, index_path=index_path, force=True)
                totals = result["totals"]
                typer.echo(f"Agent index built: {result['index_path']}")
                typer.echo(
                    "Indexed "
                    f"{totals['documents']} documents, "
                    f"{totals['objects']} objects, "
                    f"{totals['edges']} edges, "
                    f"{totals['quality_events']} quality events"
                )
    except FileNotFoundError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    typer.echo(f"Published {', '.join(run_tickers)} to {stable_root}")


@app.command("validate")
def validate_cmd(
    ticker: str = typer.Argument(..., help="Stock ticker symbol"),
    document_type: str = typer.Option("10-K", "--document-type", help="Document type"),
    period: Optional[str] = typer.Option(None, "--period", help="Filing period"),
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Ontology output root. Defaults to KRW_ONTOLOGY_ROOT or the current working directory.",
    ),
) -> None:
    """Re-run validation on existing ontology artifacts."""
    validate_document_type(document_type)
    from krw_ontology.config.constants import normalize_doc_type
    from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology

    ticker = ticker.upper()
    doc_type_key = normalize_doc_type(document_type)
    output_root = resolve_ontology_root(root)

    if period is None:
        ontology_base = output_root / "companies" / ticker / "ontology" / doc_type_key
        if not ontology_base.exists():
            typer.echo(f"No ontology data found for {ticker}")
            raise typer.Exit(1)
        periods = sorted(d.name for d in ontology_base.iterdir() if d.is_dir())
        if not periods:
            typer.echo(f"No periods found for {ticker}")
            raise typer.Exit(1)
        period = periods[-1]
        typer.echo(f"Using latest period: {period}")

    ontology_dir = output_root / "companies" / ticker / "ontology" / doc_type_key / period
    if not ontology_dir.exists():
        typer.echo(f"Ontology directory not found: {ontology_dir}")
        raise typer.Exit(1)

    result = run_validate_ontology(ontology_dir)
    stats = result.get("stats", {})
    typer.echo(
        f"Validation complete: {stats.get('total_accepted', 0)} accepted, "
        f"{stats.get('total_rejected', 0)} rejected"
    )


@app.command("build-report")
def build_report_cmd(
    ticker: str = typer.Argument(..., help="Stock ticker symbol"),
    document_type: str = typer.Option("10-K", "--document-type", help="Document type"),
    period: Optional[str] = typer.Option(None, "--period", help="Filing period"),
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Ontology output root. Defaults to KRW_ONTOLOGY_ROOT or the current working directory.",
    ),
) -> None:
    """Regenerate graph_report.md and audit_report.md from existing artifacts."""
    validate_document_type(document_type)

    from krw_ontology.config.constants import normalize_doc_type
    from krw_ontology.pipeline.stages.build_indexes import build_indexes
    from krw_ontology.pipeline.stages.build_reports import build_reports

    ticker = ticker.upper()
    doc_type_key = normalize_doc_type(document_type)
    output_root = resolve_ontology_root(root)

    if period is None:
        ontology_base = output_root / "companies" / ticker / "ontology" / doc_type_key
        if not ontology_base.exists():
            typer.echo(f"No ontology data found for {ticker}")
            raise typer.Exit(1)
        periods = sorted(d.name for d in ontology_base.iterdir() if d.is_dir())
        if not periods:
            typer.echo(f"No periods found for {ticker}")
            raise typer.Exit(1)
        period = periods[-1]
        typer.echo(f"Using latest period: {period}")

    ontology_dir = output_root / "companies" / ticker / "ontology" / doc_type_key / period
    sources_dir = output_root / "companies" / ticker / "sources" / doc_type_key / period
    if not ontology_dir.exists():
        typer.echo(f"Ontology directory not found: {ontology_dir}")
        raise typer.Exit(1)

    build_indexes(
        ticker=ticker,
        period=period,
        doc_type_key=doc_type_key,
        document_type=document_type,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=output_root,
    )

    build_reports(
        ticker=ticker,
        period=period,
        document_type=document_type,
        ontology_dir=ontology_dir,
    )
    typer.echo(f"Report generated at {ontology_dir}")


def _publish_ticker_tree(source_root: Path, stable_root: Path, ticker: str) -> None:
    """Publish one completed ticker tree from source_root into stable_root."""
    ticker = ticker.upper()
    source_dir = source_root / "companies" / ticker
    target_dir = stable_root / "companies" / ticker
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(f"Source ticker directory not found: {source_dir}")
    _replace_tree(source_dir, target_dir)


def _replace_tree(source_dir: Path, target_dir: Path) -> None:
    """Replace target_dir with source_dir using a temporary tree in target parent."""
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = target_dir.parent / f".{target_dir.name}.publish-tmp"
    backup_dir = target_dir.parent / f".{target_dir.name}.publish-backup"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    shutil.copytree(source_dir, tmp_dir)
    if target_dir.exists():
        target_dir.rename(backup_dir)
    try:
        tmp_dir.rename(target_dir)
    except Exception:
        if backup_dir.exists() and not target_dir.exists():
            backup_dir.rename(target_dir)
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir)


@app.command("build-agent-index")
def build_agent_index_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Ontology output root. Defaults to the current working directory.",
    ),
    index_path: Optional[Path] = typer.Option(
        None,
        "--index-path",
        help="SQLite index path. Defaults to <root>/indexes/agent_index.sqlite.",
    ),
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        help="Rebuild the SQLite index from scratch if it already exists.",
    ),
) -> None:
    """Build the SQLite agent retrieval index from existing ontology artifacts."""
    from krw_ontology.agent_index import build_agent_index

    output_root = resolve_ontology_root(root)
    result = build_agent_index(output_root, index_path=index_path, force=force)
    totals = result["totals"]
    typer.echo(f"Agent index built: {result['index_path']}")
    typer.echo(
        "Indexed "
        f"{totals['documents']} documents, "
        f"{totals['objects']} objects, "
        f"{totals['edges']} edges, "
        f"{totals['quality_events']} quality events"
    )
