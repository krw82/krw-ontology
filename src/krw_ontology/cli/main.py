"""Typer CLI app with 4 command skeleton."""

from __future__ import annotations

import io
import json
import os
import signal
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
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
    save_cli_config,
    set_config_value,
    unset_config_value,
)
from krw_ontology.cli.init_workspace import init_workspace
from krw_ontology.config.paths import ONTOLOGY_ROOT_ENV, resolve_ontology_root
from krw_ontology.pipeline.queue import (
    CANCELLED,
    FAILED,
    FILING_UPDATE,
    FULL_REFRESH,
    PENDING,
    RUNNING,
    SUCCEEDED,
    FileProcessLock,
    LockHeldError,
    PipelineQueue,
    QueueJob,
    is_pid_running,
)
from krw_ontology.release import (
    RELEASE_MANIFEST_FILENAME,
    build_release_manifest,
    current_release_id,
    list_release_ids,
    normalize_ontology_env,
    promote_local_release,
    release_env_root,
    rollback_local_release,
    verify_release_root,
    write_release_manifest,
)
from krw_ontology.web_catalog import write_web_catalog
from krw_ontology.quality.models import (
    BATCH_FAILURE as QUALITY_BATCH_FAILURE,
    CANCELLED as QUALITY_CANCELLED,
    COVERAGE_GAP as QUALITY_COVERAGE_GAP,
    DEFERRED_REPAIR_KINDS as QUALITY_DEFERRED_REPAIR_KINDS,
    DOCS_MISSING as QUALITY_DOCS_MISSING,
    EXECUTABLE_REPAIR_KINDS as QUALITY_EXECUTABLE_REPAIR_KINDS,
    FAILED as QUALITY_FAILED,
    NORMALIZE_NUMERIC as QUALITY_NORMALIZE_NUMERIC,
    PENDING as QUALITY_PENDING,
    REPAIR_REFERENCE as QUALITY_REPAIR_REFERENCE,
    RUNNING as QUALITY_RUNNING,
    SECTION_FAIL as QUALITY_SECTION_FAIL,
    SECTION_WARN as QUALITY_SECTION_WARN,
    SUCCEEDED as QUALITY_SUCCEEDED,
    RepairPlan,
)
from krw_ontology.quality.queue import QualityRepairStore, default_plan_id
from krw_ontology.quality.scanner import QualityScanner

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
        "Optional prod setup:\n"
        "  krw-ontology prod configure --host ubuntu@prod --remote-root /var/krw-ontology-data\n\n"
        "Daily queue flow:\n"
        "  krw-ontology queue add CVX XOM COP\n"
        "  krw-ontology queue update VG --document-type 10-Q --latest\n"
        "  krw-ontology queue start --publish-prod\n"
        "  krw-ontology queue status\n"
        "  krw-ontology queue watch"
    ),
    no_args_is_help=True,
)
queue_app = typer.Typer(
    name="queue",
    help=(
        "Manage ticker-level research jobs. Jobs live under "
        "<running-root>/.krw_pipeline and the worker processes one ticker at a time."
    ),
    epilog=(
        "[bold]Common flow: full refresh[/bold] `queue add CVX XOM --years 3`, "
        "then `queue start`, `queue status`, and `queue watch`.\n\n"
        "[bold]Common flow: one new filing[/bold] `queue update VG --document-type 10-Q "
        "--period FY2026Q1`, then `queue start`.\n\n"
        "[bold]Safe shutdown[/bold] `queue stop` finishes the current ticker before exit.\n\n"
        "[bold]Immediate interrupt[/bold] `queue kill` terminates now; run "
        "`queue recover-stale` afterwards to requeue jobs left in running status.\n\n"
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
        "  publish-index-path  optional explicit stable SQLite index path\n"
        "  prod-host           SSH host for production data releases\n"
        "  prod-root           production data root containing releases/current\n"
        "  prod-reload-command command run on prod after current release activation\n"
        "  prod-health-url     optional health URL checked on prod after reload\n"
        "  prod-keep-releases  number of prod releases to keep\n\n"
        "Examples:\n"
        "  krw-ontology config set running-root ~/krw-ontology-data-running\n"
        "  krw-ontology config set publish-root ~/krw-ontology-data\n"
        "  krw-ontology config show"
    ),
    no_args_is_help=True,
)
prod_app = typer.Typer(
    name="prod",
    help=(
        "Publish stable ontology data to a production server using versioned releases, "
        "an atomic current symlink swap, and optional MCP reload/health checks."
    ),
    epilog=(
        "Typical flow:\n"
        "  krw-ontology prod configure --host ubuntu@prod --remote-root /var/krw-ontology-data "
        "--reload-command 'sudo systemctl restart krw-ontology-mcp'\n"
        "  krw-ontology prod doctor\n"
        "  krw-ontology prod status\n"
        "  krw-ontology prod publish\n\n"
        "Queue automation:\n"
        "  krw-ontology queue start --publish-prod"
    ),
    no_args_is_help=True,
)
release_app = typer.Typer(
    name="release",
    help=(
        "Manage local immutable ontology releases for dev/staging/prod. "
        "Use this for Mac worker release roots and local promote/rollback."
    ),
    epilog=(
        "Typical local worker flow:\n"
        "  krw-ontology release publish-dev\n"
        "  krw-ontology release write-manifest --root /data/releases/prod/20260529_020000 --env prod\n"
        "  krw-ontology release verify --root /data/releases/prod/20260529_020000 --env prod\n"
        "  krw-ontology release promote 20260529_020000 --releases-root /data/releases --env prod\n"
        "  krw-ontology release prepare-dev\n"
        "  krw-ontology release finalize-dev 20260529_020000\n"
        "  krw-ontology release materialize-prod 20260529_020000\n"
        "  export KRW_ONTOLOGY_RELEASE_ROOT=/data/releases/prod/current"
    ),
    no_args_is_help=True,
)
quality_app = typer.Typer(
    name="quality",
    help=(
        "Inspect release quality and plan targeted repairs. Repair commands create "
        "operator-reviewed plans before any costly execution."
    ),
    epilog=(
        "Typical flow:\n"
        "  krw-ontology quality check --env dev\n"
        "  krw-ontology quality tickers --env dev --severity high\n"
        "  krw-ontology quality explain FCX --env dev\n"
        "  krw-ontology quality repair plan --env dev\n"
        "  krw-ontology quality repair show\n"
        "  krw-ontology quality repair run --plan <plan-id> --limit 20 --yes"
    ),
    no_args_is_help=True,
)
quality_repair_app = typer.Typer(
    name="repair",
    help=(
        "Create, inspect, and run targeted quality repair plans. Internally this is "
        "a separate quality queue under <running-root>/.krw_pipeline/quality."
    ),
    no_args_is_help=True,
)
app.add_typer(queue_app, name="queue")
app.add_typer(config_app, name="config")
app.add_typer(prod_app, name="prod")
app.add_typer(release_app, name="release")
quality_app.add_typer(quality_repair_app, name="repair")
app.add_typer(quality_app, name="quality")

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


def _quality_index_context(
    *,
    index_path: Path | None,
    release_root: Path | None,
    release: str | None,
    env: str,
    releases_root: Path | None,
) -> tuple[Path, str, Path | None]:
    if index_path is not None:
        resolved = index_path.expanduser().resolve()
        return resolved, f"index:{resolved}", None
    if release_root is not None:
        root = release_root.expanduser().resolve()
        return root / "indexes" / "agent_index.sqlite", str(root), root

    release_env = normalize_ontology_env(env)
    release_id = release or "current"
    if release and "/" in release:
        parts = release.split("/", 1)
        release_env = normalize_ontology_env(parts[0])
        release_id = parts[1] or "current"
    root_base = (releases_root or _default_releases_root()).expanduser().resolve()
    root = release_env_root(root_base, release_env) / release_id
    return root / "indexes" / "agent_index.sqlite", f"{release_env}/{release_id}", root


def _quality_scanner(
    *,
    index_path: Path | None,
    release_root: Path | None,
    release: str | None,
    env: str,
    releases_root: Path | None,
) -> tuple[QualityScanner, str, Path | None]:
    resolved_index_path, label, root = _quality_index_context(
        index_path=index_path,
        release_root=release_root,
        release=release,
        env=env,
        releases_root=releases_root,
    )
    return QualityScanner(resolved_index_path), label, root


def _echo_json(payload: dict | list) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _quality_job_description(job) -> str:
    parts = [job.kind, job.ticker]
    if job.doc_type_key and job.period:
        parts.append(f"{job.doc_type_key}:{job.period}")
    if job.stage:
        parts.append(job.stage)
    if job.batch_index is not None:
        parts.append(f"batch={job.batch_index}")
    if job.count and job.count != 1:
        parts.append(f"count={job.count}")
    return " ".join(parts)


def _quality_job_outcome(job) -> str:
    if job.status == QUALITY_FAILED:
        return "failed"
    if job.status == QUALITY_CANCELLED:
        return "cancelled"
    if job.status == QUALITY_RUNNING:
        return "running"
    if job.kind in QUALITY_DEFERRED_REPAIR_KINDS:
        if job.status == QUALITY_SUCCEEDED:
            return str(job.payload.get("repair_outcome") or "deferred_kind_executed_legacy")
        return "deferred"
    if job.kind == QUALITY_COVERAGE_GAP:
        return "needs_manual_review"
    if job.status == QUALITY_PENDING:
        return "pending"
    outcome = job.payload.get("repair_outcome")
    if outcome:
        return str(outcome)
    resolved = int(job.payload.get("resolved_count") or job.payload.get("candidate_now_valid_count") or 0)
    unresolved = int(job.payload.get("unresolved_count") or 0)
    if resolved and unresolved:
        return "partially_resolved_reported"
    if resolved:
        return "resolved_reported"
    if unresolved:
        return "unresolved_reported"
    if job.kind == QUALITY_DOCS_MISSING:
        return "pipeline_job_pending"
    if job.kind in {QUALITY_SECTION_FAIL, QUALITY_SECTION_WARN}:
        return "resectioned_needs_verify"
    if job.kind == QUALITY_BATCH_FAILURE:
        return "batch_retried_needs_verify"
    if job.kind == QUALITY_REPAIR_REFERENCE:
        return "revalidated_needs_verify"
    return "executed"


def _quality_repair_summary(store: QualityRepairStore, plan_id: str | None) -> dict:
    jobs = store.list_jobs(plan_id=plan_id)
    by_kind: dict[str, dict] = {}
    outcomes: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    for job in jobs:
        outcome = _quality_job_outcome(job)
        outcomes[outcome] += 1
        kind = by_kind.setdefault(
            job.kind,
            {
                "jobs": 0,
                "statuses": Counter(),
                "outcomes": Counter(),
                "resolved_candidates": 0,
                "unresolved_candidates": 0,
                "pruned_reference_objects": 0,
                "enqueued_pipeline_jobs": 0,
                "active_pipeline_jobs": 0,
                "deferred_jobs": 0,
                "manual_review_jobs": 0,
            },
        )
        kind["jobs"] += 1
        kind["statuses"][job.status] += 1
        kind["outcomes"][outcome] += 1
        resolved = int(job.payload.get("resolved_count") or job.payload.get("candidate_now_valid_count") or 0)
        unresolved = int(job.payload.get("unresolved_count") or 0)
        kind["resolved_candidates"] += resolved
        kind["unresolved_candidates"] += unresolved
        totals["resolved_candidates"] += resolved
        totals["unresolved_candidates"] += unresolved
        pruned = int(job.payload.get("pruned_reference_object_count") or 0)
        kind["pruned_reference_objects"] += pruned
        totals["pruned_reference_objects"] += pruned
        if job.payload.get("pipeline_queue_action") == "queued_full_refresh":
            kind["enqueued_pipeline_jobs"] += 1
            totals["enqueued_pipeline_jobs"] += 1
        if job.payload.get("pipeline_queue_action") == "skipped_active_job":
            kind["active_pipeline_jobs"] += 1
            totals["active_pipeline_jobs"] += 1
        if outcome == "deferred":
            kind["deferred_jobs"] += 1
            totals["deferred_jobs"] += 1
        if outcome == "needs_manual_review":
            kind["manual_review_jobs"] += 1
            totals["manual_review_jobs"] += 1
    return {
        "jobs": len(jobs),
        "outcomes": dict(outcomes),
        "totals": dict(totals),
        "by_kind": {
            kind: {
                **{
                    key: value
                    for key, value in payload.items()
                    if key not in {"statuses", "outcomes"}
                },
                "statuses": dict(payload["statuses"]),
                "outcomes": dict(payload["outcomes"]),
            }
            for kind, payload in sorted(by_kind.items())
        },
    }


def _quality_report_reasons(jobs, *, limit: int) -> list[tuple[str, int]]:
    reasons: Counter[str] = Counter()
    for job in jobs:
        report_path = job.payload.get("report_path")
        if not report_path:
            continue
        path = Path(str(report_path)).expanduser()
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        for item in (
            list(report.get("unresolved") or [])
            + list(report.get("accepted_reference_failures") or [])
            + list(report.get("accepted_relation_failures") or [])
        ):
            reason = str(item.get("current_reason") or item.get("reason") or item.get("original_reason") or "").strip()
            if reason:
                reasons[reason[:240]] += 1
    return reasons.most_common(limit)


def _quality_selected_plan(store: QualityRepairStore, plan_id: str | None) -> RepairPlan:
    if plan_id:
        return store.load_plan(plan_id)
    plan = store.latest_plan()
    if plan is None:
        raise FileNotFoundError("No quality repair plan found.")
    return plan


def _quality_repair_store(root: Path | None) -> QualityRepairStore:
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    store = QualityRepairStore(output_root)
    store.ensure_dirs()
    return store


@quality_app.command("check")
def quality_check_cmd(
    env: str = typer.Option("dev", "--env", help="Release environment: dev, staging, or prod."),
    release: Optional[str] = typer.Option(
        None,
        "--release",
        help="Release id or env/release id. Defaults to <env>/current.",
    ),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(None, "--release-root", help="Explicit release root."),
    index_path: Optional[Path] = typer.Option(None, "--index-path", help="Explicit agent index path."),
    min_docs: int = typer.Option(5, "--min-docs", min=1, help="Minimum expected documents per ticker."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Check release quality at a glance."""
    try:
        scanner, label, root = _quality_scanner(
            index_path=index_path,
            release_root=release_root,
            release=release,
            env=env,
            releases_root=releases_root,
        )
        report = scanner.scan(min_docs=min_docs)
    except Exception as exc:
        typer.echo(f"FAILED quality check: {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        _echo_json({"release": label, "release_root": str(root) if root else None, **report})
        return

    totals = report["totals"]
    kind_counts = report["kind_counts"]
    severity_counts = report["severity_counts"]
    typer.echo(f"Release: {label}")
    typer.echo(f"Index: {report['index_path']}")
    typer.echo(
        "Totals: "
        f"documents={totals['documents']} tickers={totals['tickers']} "
        f"objects={totals['objects']} quality_events={totals['quality_events']}"
    )
    typer.echo(f"Problem tickers: {report['problem_ticker_count']}")
    typer.echo(
        "Severity: "
        f"high={severity_counts.get('high', 0)} "
        f"medium={severity_counts.get('medium', 0)} "
        f"low={severity_counts.get('low', 0)}"
    )
    typer.echo(
        "Kinds: "
        f"docs_missing={kind_counts.get(QUALITY_DOCS_MISSING, 0)} "
        f"section_fail={kind_counts.get(QUALITY_SECTION_FAIL, 0)} "
        f"section_warn={kind_counts.get(QUALITY_SECTION_WARN, 0)} "
        f"batch_failure={kind_counts.get(QUALITY_BATCH_FAILURE, 0)} "
        f"coverage_gap={kind_counts.get(QUALITY_COVERAGE_GAP, 0)}"
    )


@quality_app.command("summary", hidden=True)
def quality_summary_cmd(
    env: str = typer.Option("dev", "--env"),
    release: Optional[str] = typer.Option(None, "--release"),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root"),
    release_root: Optional[Path] = typer.Option(None, "--release-root"),
    index_path: Optional[Path] = typer.Option(None, "--index-path"),
    min_docs: int = typer.Option(5, "--min-docs", min=1),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Backward-compatible alias for `quality check`."""
    quality_check_cmd(
        env=env,
        release=release,
        releases_root=releases_root,
        release_root=release_root,
        index_path=index_path,
        min_docs=min_docs,
        json_output=json_output,
    )


@quality_app.command("tickers")
def quality_tickers_cmd(
    env: str = typer.Option("dev", "--env", help="Release environment: dev, staging, or prod."),
    release: Optional[str] = typer.Option(None, "--release", help="Release id or env/release id."),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(None, "--release-root", help="Explicit release root."),
    index_path: Optional[Path] = typer.Option(None, "--index-path", help="Explicit agent index path."),
    min_docs: int = typer.Option(5, "--min-docs", min=1),
    severity: Optional[str] = typer.Option(None, "--severity", help="Filter by high, medium, low, ok."),
    kind: Optional[str] = typer.Option(None, "--kind", help="Filter by issue kind."),
    bad_only: bool = typer.Option(True, "--bad/--all", help="Show only problematic tickers."),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, help="Maximum tickers to show."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List ticker-level quality status."""
    try:
        scanner, label, _root = _quality_scanner(
            index_path=index_path,
            release_root=release_root,
            release=release,
            env=env,
            releases_root=releases_root,
        )
        tickers = scanner.ticker_quality(min_docs=min_docs)
    except Exception as exc:
        typer.echo(f"FAILED quality tickers: {exc}")
        raise typer.Exit(1) from exc

    rows = []
    for ticker in tickers:
        payload = ticker.to_dict(min_docs=min_docs)
        if bad_only and not payload["problem_kinds"]:
            continue
        if severity and payload["severity"] != severity:
            continue
        if kind and kind not in payload["problem_kinds"]:
            continue
        rows.append(payload)
    if limit is not None:
        rows = rows[:limit]

    if json_output:
        _echo_json({"release": label, "tickers": rows, "count": len(rows)})
        return

    typer.echo(f"Release: {label}")
    typer.echo(f"Tickers: {len(rows)}")
    for row in rows:
        kinds = ",".join(row["problem_kinds"]) or "none"
        typer.echo(
            f"- {row['ticker']} severity={row['severity']} docs={row['docs']} "
            f"kinds={kinds} section_fail={row['section_fail']} "
            f"section_warn={row['section_warn']} batch_failure={row['batch_failure']} "
            f"coverage_gap={row['coverage_gap']}"
        )


@quality_app.command("explain")
def quality_explain_cmd(
    ticker: str = typer.Argument(..., help="Ticker to explain."),
    env: str = typer.Option("dev", "--env", help="Release environment: dev, staging, or prod."),
    release: Optional[str] = typer.Option(None, "--release", help="Release id or env/release id."),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(None, "--release-root", help="Explicit release root."),
    index_path: Optional[Path] = typer.Option(None, "--index-path", help="Explicit agent index path."),
    min_docs: int = typer.Option(5, "--min-docs", min=1),
    limit: int = typer.Option(20, "--limit", min=1),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Explain why a ticker is flagged."""
    try:
        scanner, label, _root = _quality_scanner(
            index_path=index_path,
            release_root=release_root,
            release=release,
            env=env,
            releases_root=releases_root,
        )
        explanation = scanner.explain_ticker(ticker, min_docs=min_docs, limit=limit)
    except Exception as exc:
        typer.echo(f"FAILED quality explain: {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        _echo_json({"release": label, **explanation})
        return

    summary = explanation.get("summary") or {}
    typer.echo(f"Release: {label}")
    typer.echo(
        f"{explanation['ticker']}: severity={summary.get('severity', 'unknown')} "
        f"docs={summary.get('docs', 0)} kinds={','.join(summary.get('problem_kinds', [])) or 'none'}"
    )
    for doc in explanation["documents"]:
        status = doc.get("section_quality_status") or "unknown"
        if status == "pass":
            continue
        quality = doc.get("section_quality") or {}
        missing = ",".join(quality.get("missing_core_sections") or [])
        reasons = ",".join(quality.get("fail_reasons") or quality.get("warn_reasons") or [])
        typer.echo(f"- {doc['document_type']} {doc['period']} section={status} missing={missing} reasons={reasons}")
    for event in explanation["event_summary"]:
        typer.echo(
            f"- event {event['category']} severity={event['severity']} "
            f"stage={event['stage'] or '<none>'} count={event['count']}"
        )
    for reason in explanation["rejected_reasons"][:5]:
        typer.echo(f"- rejected {reason['reason']} count={reason['count']}")


@quality_app.command("events")
def quality_events_cmd(
    ticker: Optional[str] = typer.Option(None, "--ticker", help="Filter by ticker."),
    category: Optional[str] = typer.Option(None, "--category", help="Filter by quality category."),
    stage: Optional[str] = typer.Option(None, "--stage", help="Filter by stage."),
    env: str = typer.Option("dev", "--env", help="Release environment: dev, staging, or prod."),
    release: Optional[str] = typer.Option(None, "--release", help="Release id or env/release id."),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(None, "--release-root", help="Explicit release root."),
    index_path: Optional[Path] = typer.Option(None, "--index-path", help="Explicit agent index path."),
    limit: int = typer.Option(50, "--limit", min=1),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List quality events."""
    try:
        scanner, label, _root = _quality_scanner(
            index_path=index_path,
            release_root=release_root,
            release=release,
            env=env,
            releases_root=releases_root,
        )
        events = scanner.events(ticker=ticker, category=category, stage=stage, limit=limit)
    except Exception as exc:
        typer.echo(f"FAILED quality events: {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        _echo_json({"release": label, "events": events, "count": len(events)})
        return

    typer.echo(f"Release: {label}")
    typer.echo(f"Events: {len(events)}")
    for event in events:
        typer.echo(
            f"- {event['ticker']} {event['doc_type_key']} {event['period']} "
            f"{event['severity']} {event['category']} stage={event.get('stage') or '<none>'} "
            f"message={event['message']}"
        )


@quality_app.command("gate")
def quality_gate_cmd(
    env: str = typer.Option("dev", "--env", help="Release environment: dev, staging, or prod."),
    release: Optional[str] = typer.Option(None, "--release", help="Release id or env/release id."),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(None, "--release-root", help="Explicit release root."),
    index_path: Optional[Path] = typer.Option(None, "--index-path", help="Explicit agent index path."),
    min_docs: int = typer.Option(5, "--min-docs", min=1),
    max_docs_missing: int = typer.Option(0, "--max-docs-missing", min=0),
    max_section_fail: int = typer.Option(0, "--max-section-fail", min=0),
    max_batch_failure: int = typer.Option(0, "--max-batch-failure", min=0),
    max_coverage_gap: int = typer.Option(0, "--max-coverage-gap", min=0),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Fail if release quality exceeds operator thresholds."""
    try:
        scanner, label, _root = _quality_scanner(
            index_path=index_path,
            release_root=release_root,
            release=release,
            env=env,
            releases_root=releases_root,
        )
        report = scanner.scan(min_docs=min_docs)
    except Exception as exc:
        typer.echo(f"FAILED quality gate: {exc}")
        raise typer.Exit(1) from exc

    kind_counts = report["kind_counts"]
    failures = []
    thresholds = {
        QUALITY_DOCS_MISSING: max_docs_missing,
        QUALITY_SECTION_FAIL: max_section_fail,
        QUALITY_BATCH_FAILURE: max_batch_failure,
        QUALITY_COVERAGE_GAP: max_coverage_gap,
    }
    for kind_name, maximum in thresholds.items():
        actual = int(kind_counts.get(kind_name, 0))
        if actual > maximum:
            failures.append({"kind": kind_name, "actual": actual, "maximum": maximum})

    payload = {"release": label, "ok": not failures, "failures": failures, "kind_counts": kind_counts}
    if json_output:
        _echo_json(payload)
    else:
        typer.echo(f"Release: {label}")
        typer.echo("Quality gate: " + ("pass" if not failures else "fail"))
        for failure in failures:
            typer.echo(f"- {failure['kind']}: {failure['actual']} > {failure['maximum']}")
    if failures:
        raise typer.Exit(1)


@quality_repair_app.command("plan")
def quality_repair_plan_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Running root for quality repair state."),
    env: str = typer.Option("dev", "--env", help="Release environment: dev, staging, or prod."),
    release: Optional[str] = typer.Option(None, "--release", help="Release id or env/release id."),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(None, "--release-root", help="Explicit release root."),
    index_path: Optional[Path] = typer.Option(None, "--index-path", help="Explicit agent index path."),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    kind: Optional[list[str]] = typer.Option(None, "--kind", help="Only plan this repair kind."),
    include_deferred: bool = typer.Option(
        False,
        "--include-deferred",
        help="Include deferred repair kinds such as normalize_numeric. Deferred jobs are not run by default.",
    ),
    include_warn: bool = typer.Option(False, "--include-warn", help="Include section_warn repairs."),
    min_docs: int = typer.Option(5, "--min-docs", min=1),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing plan id."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Create a reviewed repair plan from quality events."""
    try:
        scanner, label, _release_root = _quality_scanner(
            index_path=index_path,
            release_root=release_root,
            release=release,
            env=env,
            releases_root=releases_root,
        )
        resolved_plan_id = plan_id or default_plan_id()
        jobs = scanner.build_repair_jobs(
            plan_id=resolved_plan_id,
            min_docs=min_docs,
            kinds=kind,
            include_warn=include_warn,
        )
        deferred_jobs = [job for job in jobs if job.kind in QUALITY_DEFERRED_REPAIR_KINDS]
        if not include_deferred:
            jobs = [job for job in jobs if job.kind not in QUALITY_DEFERRED_REPAIR_KINDS]
        store = _quality_repair_store(root)
        plan = RepairPlan(
            plan_id=resolved_plan_id,
            source_index_path=str(scanner.index_path),
            release_label=label,
            min_docs=min_docs,
            job_ids=[job.job_id for job in jobs],
            summary=dict(Counter(job.kind for job in jobs)),
        )
        plan = store.add_plan(plan, jobs, force=force)
    except Exception as exc:
        typer.echo(f"FAILED quality repair plan: {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        _echo_json({
            "plan": plan.to_dict(),
            "queue_root": str(store.queue_dir),
            "deferred_excluded": dict(Counter(job.kind for job in deferred_jobs)) if not include_deferred else {},
        })
        return
    typer.echo(f"Repair plan created: {plan.plan_id}")
    typer.echo(f"Release: {plan.release_label}")
    typer.echo(f"Queue: {store.queue_dir}")
    typer.echo(f"Jobs: {len(plan.job_ids)}")
    for repair_kind, count in sorted(plan.summary.items()):
        typer.echo(f"- {repair_kind}: {count}")
    if deferred_jobs and not include_deferred:
        typer.echo(
            "Deferred excluded: "
            + ", ".join(
                f"{repair_kind}={count}"
                for repair_kind, count in sorted(Counter(job.kind for job in deferred_jobs).items())
            )
            + " (use --include-deferred to inspect, not recommended for run)"
        )
    typer.echo("Nothing executed yet.")


@quality_repair_app.command("show")
def quality_repair_show_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Running root for quality repair state."),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    limit: int = typer.Option(50, "--limit", min=1),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show repair plan details."""
    try:
        store = _quality_repair_store(root)
        plan = _quality_selected_plan(store, plan_id)
        jobs = store.list_jobs(plan_id=plan.plan_id)
    except Exception as exc:
        typer.echo(f"FAILED quality repair show: {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        _echo_json({"plan": plan.to_dict(), "jobs": [job.to_dict() for job in jobs]})
        return
    typer.echo(f"Repair plan: {plan.plan_id}")
    typer.echo(f"Release: {plan.release_label}")
    typer.echo(f"Source index: {plan.source_index_path}")
    typer.echo(f"Jobs: {len(jobs)}")
    for repair_kind, count in sorted(plan.summary.items()):
        typer.echo(f"- {repair_kind}: {count}")
    for job in jobs[:limit]:
        typer.echo(f"- {job.status} {_quality_job_description(job)} job={job.job_id}")


@quality_repair_app.command("status")
def quality_repair_status_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Running root for quality repair state."),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show quality repair status."""
    try:
        store = _quality_repair_store(root)
        plan = _quality_selected_plan(store, plan_id) if plan_id or store.latest_plan() else None
        selected_plan_id = plan.plan_id if plan else None
        status_counts = store.status_counts(plan_id=selected_plan_id)
        kind_counts = store.kind_counts(plan_id=selected_plan_id)
        repair_summary = _quality_repair_summary(store, selected_plan_id) if selected_plan_id else {}
    except Exception as exc:
        typer.echo(f"FAILED quality repair status: {exc}")
        raise typer.Exit(1) from exc

    payload = {
        "queue_root": str(store.queue_dir),
        "plan_id": selected_plan_id,
        "worker_running": store.worker_is_running(),
        "status_counts": status_counts,
        "kind_counts": kind_counts,
        "repair_summary": repair_summary,
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"QUALITY_QUEUE={store.queue_dir}")
    typer.echo(f"Plan: {selected_plan_id or '<none>'}")
    typer.echo(f"Worker: {'running' if payload['worker_running'] else 'stopped'}")
    typer.echo(
        "Jobs: "
        f"pending={status_counts.get(QUALITY_PENDING, 0)} "
        f"running={status_counts.get(QUALITY_RUNNING, 0)} "
        f"succeeded={status_counts.get(QUALITY_SUCCEEDED, 0)} "
        f"failed={status_counts.get(QUALITY_FAILED, 0)} "
        f"cancelled={status_counts.get(QUALITY_CANCELLED, 0)}"
    )
    for repair_kind, count in sorted(kind_counts.items()):
        typer.echo(f"- {repair_kind}: {count}")
    if repair_summary:
        totals = repair_summary.get("totals", {})
        typer.echo(
            "Resolution: "
            f"resolved_candidates={totals.get('resolved_candidates', 0)} "
            f"unresolved_candidates={totals.get('unresolved_candidates', 0)} "
            f"pruned_reference_objects={totals.get('pruned_reference_objects', 0)} "
            f"enqueued_pipeline_jobs={totals.get('enqueued_pipeline_jobs', 0)} "
            f"active_pipeline_jobs={totals.get('active_pipeline_jobs', 0)} "
            f"deferred_jobs={totals.get('deferred_jobs', 0)} "
            f"manual_review_jobs={totals.get('manual_review_jobs', 0)}"
        )
        outcomes = repair_summary.get("outcomes", {})
        if outcomes:
            typer.echo("Outcomes: " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))


@quality_repair_app.command("watch")
def quality_repair_watch_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Running root for quality repair state."),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    interval: float = typer.Option(5.0, "--interval", min=1.0, help="Seconds between refreshes."),
    once: bool = typer.Option(False, "--once", help="Print one snapshot and exit."),
) -> None:
    """Watch quality repair status."""
    while True:
        try:
            store = _quality_repair_store(root)
            plan = _quality_selected_plan(store, plan_id) if plan_id or store.latest_plan() else None
            selected_plan_id = plan.plan_id if plan else None
            status_counts = store.status_counts(plan_id=selected_plan_id)
            kind_counts = store.kind_counts(plan_id=selected_plan_id)
            repair_summary = _quality_repair_summary(store, selected_plan_id) if selected_plan_id else {}
            running_jobs = store.list_jobs(plan_id=selected_plan_id, statuses=[QUALITY_RUNNING])
            failed_jobs = store.list_jobs(plan_id=selected_plan_id, statuses=[QUALITY_FAILED])[-5:]
        except Exception as exc:
            typer.echo(f"FAILED quality repair watch: {exc}")
            raise typer.Exit(1) from exc

        if not once:
            typer.clear()
        typer.echo(f"QUALITY_QUEUE={store.queue_dir}")
        typer.echo(f"Time: {_now_label()}")
        typer.echo(f"Plan: {selected_plan_id or '<none>'}")
        pid = store.worker_pid()
        typer.echo(
            f"Worker: {'running' if store.worker_is_running() else 'stopped'}"
            + (f" pid={pid}" if pid is not None else "")
        )
        typer.echo(f"Log: {store.worker_log_path}")
        typer.echo(
            "Jobs: "
            f"pending={status_counts.get(QUALITY_PENDING, 0)} "
            f"running={status_counts.get(QUALITY_RUNNING, 0)} "
            f"succeeded={status_counts.get(QUALITY_SUCCEEDED, 0)} "
            f"failed={status_counts.get(QUALITY_FAILED, 0)} "
            f"cancelled={status_counts.get(QUALITY_CANCELLED, 0)}"
        )
        typer.echo("Kinds: " + ", ".join(f"{k}={v}" for k, v in sorted(kind_counts.items())))
        if repair_summary:
            totals = repair_summary.get("totals", {})
            typer.echo(
                "Resolution: "
                f"resolved_candidates={totals.get('resolved_candidates', 0)} "
                f"unresolved_candidates={totals.get('unresolved_candidates', 0)} "
                f"pruned_reference_objects={totals.get('pruned_reference_objects', 0)} "
                f"enqueued_pipeline_jobs={totals.get('enqueued_pipeline_jobs', 0)} "
                f"active_pipeline_jobs={totals.get('active_pipeline_jobs', 0)} "
                f"deferred_jobs={totals.get('deferred_jobs', 0)} "
                f"manual_review_jobs={totals.get('manual_review_jobs', 0)}"
            )
        if running_jobs:
            typer.echo("Running:")
            for job in running_jobs:
                typer.echo(f"- {_quality_job_description(job)} job={job.job_id}")
        if failed_jobs:
            typer.echo("Recent failed:")
            for job in failed_jobs:
                typer.echo(f"- {_quality_job_description(job)} error={job.error}")
        if once:
            return
        time.sleep(interval)


@quality_repair_app.command("report")
def quality_repair_report_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Running root for quality repair state."),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    kind: Optional[str] = typer.Option(None, "--kind", help="Filter by repair kind."),
    ticker: Optional[str] = typer.Option(None, "--ticker", help="Filter by ticker."),
    reasons_limit: int = typer.Option(10, "--reasons-limit", min=0, help="Top unresolved reasons to show."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Summarize executed repair outcomes separately from queue status."""
    try:
        store = _quality_repair_store(root)
        plan = _quality_selected_plan(store, plan_id)
        jobs = store.list_jobs(plan_id=plan.plan_id)
    except Exception as exc:
        typer.echo(f"FAILED quality repair report: {exc}")
        raise typer.Exit(1) from exc

    if kind:
        jobs = [job for job in jobs if job.kind == kind]
    if ticker:
        jobs = [job for job in jobs if job.ticker.upper() == ticker.upper()]
    summary = _quality_repair_summary(store, plan.plan_id)
    if kind or ticker:
        temp_store_jobs = jobs
        outcomes = Counter(_quality_job_outcome(job) for job in temp_store_jobs)
        filtered_by_kind: dict[str, dict] = {}
        for job in temp_store_jobs:
            payload = filtered_by_kind.setdefault(
                job.kind,
                {
                    "jobs": 0,
                    "outcomes": Counter(),
                    "resolved_candidates": 0,
                    "unresolved_candidates": 0,
                    "pruned_reference_objects": 0,
                    "enqueued_pipeline_jobs": 0,
                    "active_pipeline_jobs": 0,
                    "deferred_jobs": 0,
                    "manual_review_jobs": 0,
                },
            )
            outcome = _quality_job_outcome(job)
            payload["jobs"] += 1
            payload["outcomes"][outcome] += 1
            payload["resolved_candidates"] += int(job.payload.get("resolved_count") or job.payload.get("candidate_now_valid_count") or 0)
            payload["unresolved_candidates"] += int(job.payload.get("unresolved_count") or 0)
            payload["pruned_reference_objects"] += int(job.payload.get("pruned_reference_object_count") or 0)
            if job.payload.get("pipeline_queue_action") == "queued_full_refresh":
                payload["enqueued_pipeline_jobs"] += 1
            if job.payload.get("pipeline_queue_action") == "skipped_active_job":
                payload["active_pipeline_jobs"] += 1
            if outcome == "deferred":
                payload["deferred_jobs"] += 1
            if outcome == "needs_manual_review":
                payload["manual_review_jobs"] += 1
        summary = {
            "jobs": len(temp_store_jobs),
            "outcomes": dict(outcomes),
            "totals": {
                "resolved_candidates": sum(v["resolved_candidates"] for v in filtered_by_kind.values()),
                "unresolved_candidates": sum(v["unresolved_candidates"] for v in filtered_by_kind.values()),
                "pruned_reference_objects": sum(v["pruned_reference_objects"] for v in filtered_by_kind.values()),
                "enqueued_pipeline_jobs": sum(v["enqueued_pipeline_jobs"] for v in filtered_by_kind.values()),
                "active_pipeline_jobs": sum(v["active_pipeline_jobs"] for v in filtered_by_kind.values()),
                "deferred_jobs": sum(v["deferred_jobs"] for v in filtered_by_kind.values()),
                "manual_review_jobs": sum(v["manual_review_jobs"] for v in filtered_by_kind.values()),
            },
            "by_kind": {
                key: {**value, "outcomes": dict(value["outcomes"])}
                for key, value in sorted(filtered_by_kind.items())
            },
        }
    reasons = _quality_report_reasons(jobs, limit=reasons_limit) if reasons_limit else []
    payload = {"plan": plan.to_dict(), "summary": summary, "top_unresolved_reasons": reasons}
    if json_output:
        _echo_json(payload)
        return

    typer.echo(f"Repair report: {plan.plan_id}")
    typer.echo(f"Release: {plan.release_label}")
    typer.echo(f"Jobs: {summary.get('jobs', 0)}")
    outcomes = summary.get("outcomes") or {}
    if outcomes:
        typer.echo("Outcomes: " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))
    totals = summary.get("totals") or {}
    typer.echo(
        "Resolution: "
        f"resolved_candidates={totals.get('resolved_candidates', 0)} "
        f"unresolved_candidates={totals.get('unresolved_candidates', 0)} "
        f"pruned_reference_objects={totals.get('pruned_reference_objects', 0)} "
        f"enqueued_pipeline_jobs={totals.get('enqueued_pipeline_jobs', 0)} "
        f"active_pipeline_jobs={totals.get('active_pipeline_jobs', 0)} "
        f"deferred_jobs={totals.get('deferred_jobs', 0)} "
        f"manual_review_jobs={totals.get('manual_review_jobs', 0)}"
    )
    for repair_kind, item in sorted((summary.get("by_kind") or {}).items()):
        typer.echo(
            f"- {repair_kind}: jobs={item.get('jobs', 0)} "
            f"resolved={item.get('resolved_candidates', 0)} "
            f"unresolved={item.get('unresolved_candidates', 0)} "
            f"pruned={item.get('pruned_reference_objects', 0)} "
            f"outcomes={item.get('outcomes', {})}"
        )
    if reasons:
        typer.echo("Top unresolved reasons:")
        for reason, count in reasons:
            typer.echo(f"- {count} {reason}")


@quality_repair_app.command("list")
def quality_repair_list_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Running root for quality repair state."),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    kind: Optional[str] = typer.Option(None, "--kind", help="Filter by repair kind."),
    limit: int = typer.Option(50, "--limit", min=1),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List quality repair jobs."""
    try:
        store = _quality_repair_store(root)
        selected_plan_id = plan_id
        if selected_plan_id is None:
            latest = store.latest_plan()
            selected_plan_id = latest.plan_id if latest else None
        jobs = store.list_jobs(plan_id=selected_plan_id)
    except Exception as exc:
        typer.echo(f"FAILED quality repair list: {exc}")
        raise typer.Exit(1) from exc

    if status:
        jobs = [job for job in jobs if job.status == status]
    if kind:
        jobs = [job for job in jobs if job.kind == kind]
    jobs = jobs[:limit]

    if json_output:
        _echo_json({"plan_id": selected_plan_id, "jobs": [job.to_dict() for job in jobs]})
        return
    typer.echo(f"Plan: {selected_plan_id or '<none>'}")
    typer.echo(f"Jobs: {len(jobs)}")
    for job in jobs:
        typer.echo(f"- {job.status} {_quality_job_description(job)} job={job.job_id}")


@quality_repair_app.command("clear")
def quality_repair_clear_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Running root for quality repair state."),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    yes: bool = typer.Option(False, "--yes", help="Confirm deletion."),
) -> None:
    """Clear quality repair plans/jobs."""
    if not yes:
        typer.echo("Refusing to clear quality repair state without --yes.")
        raise typer.Exit(1)
    try:
        store = _quality_repair_store(root)
        removed = store.clear(plan_id=plan_id)
    except Exception as exc:
        typer.echo(f"FAILED quality repair clear: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"Cleared quality repair state: removed={removed}")


@quality_repair_app.command("log")
def quality_repair_log_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Running root for quality repair state."),
    lines: int = typer.Option(80, "--lines", min=1, help="Number of trailing lines to show."),
    follow: bool = typer.Option(False, "--follow/--no-follow", help="Follow appended log output."),
) -> None:
    """Show the quality repair background worker log."""
    store = _quality_repair_store(root)
    path = store.worker_log_path
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


def _quality_select_pending_jobs(
    *,
    store: QualityRepairStore,
    plan: RepairPlan,
    kind: str | None,
    all_jobs: bool,
    limit: int,
) -> tuple[list, int]:
    jobs = store.list_jobs(plan_id=plan.plan_id, statuses=[QUALITY_PENDING])
    skipped_count = 0
    if kind:
        jobs = [job for job in jobs if job.kind == kind]
        if jobs and kind not in QUALITY_EXECUTABLE_REPAIR_KINDS:
            return [], len(jobs)
    else:
        planned_count = len(jobs)
        jobs = [job for job in jobs if job.kind in QUALITY_EXECUTABLE_REPAIR_KINDS]
        skipped_count = planned_count - len(jobs)
    if not all_jobs:
        jobs = jobs[:limit]
    return jobs, skipped_count


@quality_repair_app.command("run")
def quality_repair_run_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Running root for quality repair state."),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    kind: Optional[str] = typer.Option(None, "--kind", help="Only run this repair kind."),
    limit: int = typer.Option(20, "--limit", min=1),
    all_jobs: bool = typer.Option(
        True,
        "--all/--limit-only",
        help="Run all selected executable pending jobs. Use --limit-only to apply --limit.",
    ),
    concurrency: Optional[int] = typer.Option(
        None,
        "--concurrency",
        min=1,
        help="Override Agent SDK batch concurrency for executable repair stages.",
    ),
    preview: bool = typer.Option(False, "--preview", help="Preview selected jobs without running."),
    foreground: bool = typer.Option(False, "--foreground", help="Run in the foreground instead of starting a background worker."),
    yes: bool = typer.Option(True, "--yes/--no-yes", help="Confirm execution. Defaults to yes; use --preview for dry run."),
) -> None:
    """Run pending repair jobs. This can invoke Agent SDK calls."""
    try:
        store = _quality_repair_store(root)
        plan = _quality_selected_plan(store, plan_id)
        jobs, skipped_count = _quality_select_pending_jobs(
            store=store,
            plan=plan,
            kind=kind,
            all_jobs=all_jobs,
            limit=limit,
        )
    except Exception as exc:
        typer.echo(f"FAILED quality repair run: {exc}")
        raise typer.Exit(1) from exc

    if skipped_count:
        typer.echo(
            f"Skipped {skipped_count} pending jobs that are deferred or lack executors. "
            "Use --kind to inspect a specific kind."
        )
    if not jobs:
        typer.echo("No pending quality repair jobs selected.")
        return

    if preview or not yes:
        typer.echo(f"Repair plan: {plan.plan_id}")
        typer.echo(f"Selected jobs: {len(jobs)}")
        if all_jobs:
            typer.echo("Selection: all executable pending jobs")
        for job in jobs:
            typer.echo(f"- would run {_quality_job_description(job)} job={job.job_id}")
        typer.echo("Nothing executed.")
        raise typer.Exit(1)

    if not foreground:
        if store.worker_is_running():
            typer.echo("Quality repair worker is already running.")
            raise typer.Exit(1)
        command = [
            sys.executable,
            "-c",
            "from krw_ontology.cli.main import app; app()",
            "quality-repair-worker",
            "--root",
            str(store.root),
            "--plan",
            plan.plan_id,
        ]
        if kind:
            command.extend(["--kind", kind])
        if all_jobs:
            command.append("--all")
        else:
            command.extend(["--limit", str(limit)])
        if concurrency is not None:
            command.extend(["--concurrency", str(concurrency)])
        store.ensure_dirs()
        with store.worker_log_path.open("a", encoding="utf-8") as log_handle:
            log_handle.write(f"\n[{_now_label()}] quality repair launching background worker\n")
            log_handle.flush()
            process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        typer.echo(f"Started quality repair worker pid={process.pid}")
        typer.echo(f"plan: {plan.plan_id}")
        typer.echo(f"selected_jobs: {len(jobs)}")
        typer.echo(f"log: {store.worker_log_path}")
        typer.echo(f"watch: krw-ontology quality repair watch --plan {plan.plan_id}")
        return

    from krw_ontology.quality.runner import run_repair_jobs

    try:
        with FileProcessLock(store.worker_lock_path):
            store.write_worker_pid(os.getpid())
            store.write_worker_state(
                os.getpid(),
                mode={
                    "plan_id": plan.plan_id,
                    "kind": kind,
                    "all_jobs": all_jobs,
                    "limit": limit,
                    "concurrency": concurrency,
                    "foreground": True,
                },
            )
            result = run_repair_jobs(
                store=store,
                jobs=jobs,
                root=store.root,
                concurrency=concurrency,
            )
    except Exception as exc:
        typer.echo(f"FAILED quality repair run: {exc}")
        raise typer.Exit(1) from exc
    finally:
        store.clear_worker_state(os.getpid())
        store.clear_worker_pid(os.getpid())
    typer.echo(
        "Quality repair run complete: "
        f"succeeded={result['succeeded']} failed={result['failed']} skipped={result['skipped']} "
        f"resolved={result.get('resolved', 0)} unresolved={result.get('unresolved', 0)} "
        f"enqueued={result.get('enqueued', 0)} active={result.get('active', 0)}"
    )


@app.command("quality-repair-worker", hidden=True)
def quality_repair_worker_cmd(
    root: Path = typer.Option(..., "--root", help="Running root for quality repair state."),
    plan_id: str = typer.Option(..., "--plan", "--plan-id", help="Repair plan id."),
    kind: Optional[str] = typer.Option(None, "--kind", help="Only run this repair kind."),
    limit: int = typer.Option(20, "--limit", min=1),
    all_jobs: bool = typer.Option(False, "--all", help="Run all selected executable pending jobs."),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", min=1),
) -> None:
    """Internal background worker for quality repair runs."""
    from krw_ontology.quality.runner import run_repair_jobs

    store = _quality_repair_store(root)
    plan = _quality_selected_plan(store, plan_id)
    jobs, skipped_count = _quality_select_pending_jobs(
        store=store,
        plan=plan,
        kind=kind,
        all_jobs=all_jobs,
        limit=limit,
    )
    try:
        with FileProcessLock(store.worker_lock_path):
            store.write_worker_pid(os.getpid())
            store.write_worker_state(
                os.getpid(),
                mode={
                    "plan_id": plan.plan_id,
                    "kind": kind,
                    "all_jobs": all_jobs,
                    "limit": limit,
                    "concurrency": concurrency,
                    "foreground": False,
                },
            )
            typer.echo(f"[{_now_label()}] Quality repair worker started plan={plan.plan_id}")
            if skipped_count:
                typer.echo(f"Skipped {skipped_count} pending jobs that are deferred or lack executors.")
            typer.echo(f"Selected jobs: {len(jobs)}")
            result = run_repair_jobs(
                store=store,
                jobs=jobs,
                root=store.root,
                concurrency=concurrency,
            )
            typer.echo(
                "Quality repair run complete: "
                f"succeeded={result['succeeded']} failed={result['failed']} skipped={result['skipped']} "
                f"resolved={result.get('resolved', 0)} unresolved={result.get('unresolved', 0)} "
                f"enqueued={result.get('enqueued', 0)} active={result.get('active', 0)}"
            )
    except Exception as exc:
        typer.echo(f"FAILED quality repair worker: {exc}")
        raise typer.Exit(1) from exc
    finally:
        store.clear_worker_state(os.getpid())
        store.clear_worker_pid(os.getpid())


@config_app.command("show")
def config_show_cmd() -> None:
    """Show the active CLI defaults and the config file path."""
    config = load_cli_config()
    path = cli_config_path()
    typer.echo(f"Config file: {path}")
    typer.echo(f"running-root: {config.running_root or '<unset>'}")
    typer.echo(f"publish-root: {config.publish_root or '<unset>'}")
    typer.echo(f"publish-index-path: {config.publish_index_path or '<unset>'}")
    typer.echo(f"prod-host: {config.prod_host or '<unset>'}")
    typer.echo(f"prod-root: {config.prod_root or '<unset>'}")
    typer.echo(f"prod-reload-command: {config.prod_reload_command or '<unset>'}")
    typer.echo(f"prod-health-url: {config.prod_health_url or '<unset>'}")
    typer.echo(f"prod-keep-releases: {config.prod_keep_releases or '<unset>'}")


@config_app.command("set")
def config_set_cmd(
    key: str = typer.Argument(
        ...,
        help="Config key to set. Run `krw-ontology config show` to inspect saved values.",
    ),
    value: str = typer.Argument(..., help="Value to save for this key."),
) -> None:
    """Set a persistent path default used by queue/update commands."""
    key = validate_config_key(key)
    set_config_value(key, value)
    shown_value = str(Path(value).expanduser().resolve()) if key in {"running-root", "publish-root", "publish-index-path"} else value
    typer.echo(f"Set {key}={shown_value}")
    typer.echo(f"Config file: {cli_config_path()}")


@config_app.command("unset")
def config_unset_cmd(
    key: str = typer.Argument(
        ...,
        help="Config key to clear. Run `krw-ontology config show` to inspect saved values.",
    ),
) -> None:
    """Clear a persistent path default."""
    key = validate_config_key(key)
    unset_config_value(key)
    typer.echo(f"Unset {key}")
    typer.echo(f"Config file: {cli_config_path()}")


@prod_app.command("status")
def prod_status_cmd(
    host: Optional[str] = typer.Option(None, "--host", help="Override configured prod SSH host."),
    remote_root: Optional[str] = typer.Option(
        None,
        "--remote-root",
        help="Override configured production data root.",
    ),
    health_url: Optional[str] = typer.Option(
        None,
        "--health-url",
        help="Override configured prod health URL.",
    ),
) -> None:
    """Show current production release state without changing anything."""
    try:
        settings = _resolve_prod_settings(host=host, remote_root=remote_root, health_url=health_url)
        status = _get_prod_status(
            host=str(settings["host"]),
            remote_root=str(settings["remote_root"]),
        )
        if settings["health_url"]:
            try:
                _check_prod_health(
                    host=str(settings["host"]),
                    health_url=str(settings["health_url"]),
                )
                status["health"] = "ok"
            except Exception as exc:
                status["health"] = f"failed: {exc}"
    except Exception as exc:
        typer.echo(f"FAILED prod status: {exc}")
        raise typer.Exit(1) from exc
    _print_prod_status(
        status,
        health_url=str(settings["health_url"]) if settings["health_url"] else None,
    )


@prod_app.command("doctor")
def prod_doctor_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Local stable publish root. Defaults to configured publish-root.",
    ),
    host: Optional[str] = typer.Option(None, "--host", help="Override configured prod SSH host."),
    remote_root: Optional[str] = typer.Option(
        None,
        "--remote-root",
        help="Override configured production data root.",
    ),
    health_url: Optional[str] = typer.Option(
        None,
        "--health-url",
        help="Override configured prod health URL.",
    ),
) -> None:
    """Check local and remote requirements before publishing to production."""
    stable_root = resolve_publish_root(root)
    failures: list[str] = []
    warnings: list[str] = []
    typer.echo("Prod publish doctor")
    if stable_root is None:
        failures.append("Local publish root is not configured. Set publish-root or pass --root.")
    elif not stable_root.exists() or not stable_root.is_dir():
        failures.append(f"Local publish root does not exist: {stable_root}")
    else:
        typer.echo(f"OK local publish root: {stable_root}")

    try:
        settings = _resolve_prod_settings(host=host, remote_root=remote_root, health_url=health_url)
    except Exception as exc:
        typer.echo(f"FAIL prod config: {exc}")
        raise typer.Exit(1) from exc

    typer.echo(f"OK prod host: {settings['host']}")
    typer.echo(f"OK prod root: {settings['remote_root']}")

    try:
        _run_checked(["ssh", str(settings["host"]), "true"])
        typer.echo("OK ssh connectivity")
    except Exception as exc:
        failures.append(f"SSH connectivity failed: {exc}")

    try:
        doctor = _run_prod_doctor_checks(
            host=str(settings["host"]),
            remote_root=str(settings["remote_root"]),
            need_curl=bool(settings["health_url"]),
        )
        if doctor.get("required_commands_missing"):
            failures.append(f"Missing remote commands: {doctor['required_commands_missing']}")
        else:
            typer.echo("OK remote publish commands: tar ln mv rm mkdir ls readlink xargs")
        if doctor.get("root_state") == "exists" and doctor.get("root_writable") == "yes":
            typer.echo("OK remote root is writable")
        elif doctor.get("root_state") == "missing" and doctor.get("parent_writable") == "yes":
            warnings.append(
                f"Remote root does not exist yet, but parent is writable: {settings['remote_root']}"
            )
        else:
            failures.append(f"Remote root is not writable: {settings['remote_root']}")
        if doctor.get("current_kind") == "directory":
            warnings.append(
                "Remote current is a directory. First publish will preserve it as "
                "releases/pre-prod-<release_id> and convert current to a release symlink."
            )
        elif doctor.get("current_kind") == "symlink":
            typer.echo(f"OK current release symlink: {doctor.get('current_target') or '<unknown>'}")
    except Exception as exc:
        failures.append(f"Remote publish checks failed: {exc}")

    configured_health_url = str(settings["health_url"]) if settings["health_url"] else None
    if configured_health_url:
        try:
            _check_prod_health(host=str(settings["host"]), health_url=configured_health_url)
            typer.echo(f"OK health URL: {configured_health_url}")
        except Exception as exc:
            failures.append(f"Health URL failed: {exc}")
    else:
        warnings.append("No prod-health-url configured; publish will rely on reload-command success.")

    for warning in warnings:
        typer.echo(f"WARN {warning}")
    if failures:
        for failure in failures:
            typer.echo(f"FAIL {failure}")
        raise typer.Exit(1)
    typer.echo("Prod doctor passed.")


@prod_app.command("configure")
def prod_configure_cmd(
    host: Optional[str] = typer.Option(
        None,
        "--host",
        help="SSH host for production, e.g. ubuntu@1.2.3.4.",
    ),
    remote_root: Optional[str] = typer.Option(
        None,
        "--remote-root",
        help="Production data root. MCP should read <remote-root>/current.",
    ),
    reload_command: Optional[str] = typer.Option(
        None,
        "--reload-command",
        help="Command to run on prod after activating a release, e.g. 'sudo systemctl restart krw-ontology-mcp'.",
    ),
    health_url: Optional[str] = typer.Option(
        None,
        "--health-url",
        help="Optional URL checked on prod after reload, e.g. http://127.0.0.1:8000/health.",
    ),
    keep_releases: Optional[int] = typer.Option(
        None,
        "--keep-releases",
        min=1,
        help="Number of production releases to keep for rollback.",
    ),
) -> None:
    """Save production publish defaults."""
    config = load_cli_config()
    if host is not None:
        config.prod_host = host
    if remote_root is not None:
        config.prod_root = remote_root
    if reload_command is not None:
        config.prod_reload_command = reload_command
    if health_url is not None:
        config.prod_health_url = health_url
    if keep_releases is not None:
        config.prod_keep_releases = str(keep_releases)
    save_cli_config(config)
    typer.echo(f"Config file: {cli_config_path()}")
    typer.echo(f"prod-host: {config.prod_host or '<unset>'}")
    typer.echo(f"prod-root: {config.prod_root or '<unset>'}")
    typer.echo(f"prod-reload-command: {config.prod_reload_command or '<unset>'}")
    typer.echo(f"prod-health-url: {config.prod_health_url or '<unset>'}")
    typer.echo(f"prod-keep-releases: {config.prod_keep_releases or '<unset>'}")


@prod_app.command("publish")
def prod_publish_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Local stable publish root. Defaults to configured publish-root.",
    ),
    host: Optional[str] = typer.Option(None, "--host", help="Override configured prod SSH host."),
    remote_root: Optional[str] = typer.Option(
        None,
        "--remote-root",
        help="Override configured production data root.",
    ),
    reload_command: Optional[str] = typer.Option(
        None,
        "--reload-command",
        help="Override configured prod reload command.",
    ),
    health_url: Optional[str] = typer.Option(
        None,
        "--health-url",
        help="Override configured prod health URL.",
    ),
    keep_releases: Optional[int] = typer.Option(
        None,
        "--keep-releases",
        min=1,
        help="Override configured number of prod releases to keep.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be published without uploading or activating.",
    ),
) -> None:
    """Publish the local stable ontology root to production as a versioned release."""
    stable_root = resolve_publish_root(root)
    if stable_root is None:
        typer.echo("Set publish-root or pass --root before publishing to prod.")
        raise typer.Exit(1)
    pre_status = _try_get_prod_status(
        host=host,
        remote_root=remote_root,
        health_url=health_url,
    )
    try:
        result = _publish_prod_root(
            stable_root=stable_root,
            host=host,
            remote_root=remote_root,
            reload_command=reload_command,
            health_url=health_url,
            keep_releases=keep_releases,
            dry_run=dry_run,
        )
    except Exception as exc:
        typer.echo(f"FAILED prod publish: {exc}")
        raise typer.Exit(1) from exc
    _print_prod_publish_result(result, dry_run=dry_run, pre_status=pre_status)


@prod_app.command("rollback")
def prod_rollback_cmd(
    release_id: Optional[str] = typer.Argument(
        None,
        help="Release ID to activate. If omitted, activate the previous release.",
    ),
    host: Optional[str] = typer.Option(None, "--host", help="Override configured prod SSH host."),
    remote_root: Optional[str] = typer.Option(
        None,
        "--remote-root",
        help="Override configured production data root.",
    ),
    reload_command: Optional[str] = typer.Option(
        None,
        "--reload-command",
        help="Override configured prod reload command.",
    ),
    health_url: Optional[str] = typer.Option(
        None,
        "--health-url",
        help="Override configured prod health URL.",
    ),
) -> None:
    """Rollback production current to a previous release."""
    try:
        result = _rollback_prod_release(
            release_id=release_id,
            host=host,
            remote_root=remote_root,
            reload_command=reload_command,
            health_url=health_url,
        )
    except Exception as exc:
        typer.echo(f"FAILED prod rollback: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"Prod rollback activated release={result['release_id']} host={result['host']}")


def _default_releases_root() -> Path:
    return Path.home() / "krw-ontology-data" / "releases"


def _default_release_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _default_release_index_path(root: Path) -> Path:
    return root / "indexes" / "agent_index.sqlite"


def _release_root_has_ontology_artifacts(root: Path) -> bool:
    companies_root = root / "companies"
    if not companies_root.is_dir():
        return False
    return any(path.is_file() for path in companies_root.rglob("*"))


def _resolve_release_id_for_finalize_dev(env: str, releases_root: Path) -> str | None:
    release_id = _resolve_release_id_from_publish_config(env, releases_root)
    if release_id:
        return release_id
    env_root = release_env_root(releases_root, env).expanduser().resolve()
    return current_release_id(env_root)


def _release_publish_dev(
    *,
    releases_root: Path,
    release_id: str,
    source_root: Path,
    build_index: bool,
    promote: bool,
    allow_running_queue: bool,
    dry_run: bool,
    allow_prepared_release_root: bool = False,
) -> dict:
    env = "dev"
    resolved_source_root = source_root.expanduser().resolve()
    if not resolved_source_root.is_dir():
        raise FileNotFoundError(f"Source root not found: {resolved_source_root}")
    if not _release_root_has_ontology_artifacts(resolved_source_root):
        raise RuntimeError(
            "Source root has no ontology artifacts under companies/: "
            f"{resolved_source_root}"
        )

    queue = PipelineQueue(resolved_source_root)
    queue.ensure_dirs()
    if queue.worker_is_running() and not allow_running_queue:
        raise RuntimeError(
            "Queue worker is running. Stop it first, or pass --allow-running-queue."
        )

    env_root = release_env_root(releases_root, env).expanduser().resolve()
    release_root = env_root / release_id
    index_path = _default_release_index_path(release_root)
    if release_root.exists():
        if not allow_prepared_release_root:
            raise FileExistsError(f"Release directory already exists: {release_root}")
        if (release_root / RELEASE_MANIFEST_FILENAME).exists() or index_path.exists():
            raise FileExistsError(f"Release directory already has finalized artifacts: {release_root}")

    if dry_run:
        return {
            "release_id": release_id,
            "source_root": str(resolved_source_root),
            "release_root": str(release_root),
            "index_path": str(index_path),
            "manifest": None,
            "index_present": False,
            "build_index": build_index,
            "promoted": promote,
            "totals": None,
        }

    release_root.mkdir(parents=True, exist_ok=allow_prepared_release_root)

    index_result = None
    if build_index:
        from krw_ontology.agent_index import build_agent_index

        index_result = build_agent_index(resolved_source_root, index_path=index_path, force=True)

    manifest = write_release_manifest(
        release_root,
        release_id=release_id,
        env=env,
        source_root=resolved_source_root,
        index_path=index_path,
        write_legacy=True,
    )
    if build_index:
        verification = verify_release_root(release_root, env=env, index_path=index_path)
        if not verification["ok"]:
            raise RuntimeError(f"Release verify failed: {', '.join(verification['errors'])}")

    promoted = False
    if promote:
        promote_local_release(releases_root, env=env, release_id=release_id)
        promoted = True

    return {
        "release_id": release_id,
        "source_root": str(resolved_source_root),
        "release_root": str(release_root),
        "index_path": str(index_path),
        "manifest": str(release_root / RELEASE_MANIFEST_FILENAME),
        "index_present": manifest["index_present"],
        "build_index": build_index,
        "promoted": promoted,
        "totals": index_result["totals"] if index_result is not None else None,
    }


def _dev_publish_paths(releases_root: Path, release_id: str) -> dict[str, Path]:
    release_root = release_env_root(releases_root, "dev").expanduser().resolve() / release_id
    return {
        "release_root": release_root,
        "index_path": _default_release_index_path(release_root),
        "log_path": release_root / "logs" / "publish-dev.log",
        "progress_path": _default_release_index_path(release_root).parent / "build_progress.jsonl",
        "worker_pid_path": release_root / "worker.pid",
        "worker_state_path": release_root / "worker_state.json",
    }


def _write_dev_publish_worker_state(
    *,
    releases_root: Path,
    release_id: str,
    pid: int,
    mode: dict,
) -> None:
    paths = _dev_publish_paths(releases_root, release_id)
    paths["release_root"].mkdir(parents=True, exist_ok=True)
    paths["worker_pid_path"].write_text(str(pid), encoding="utf-8")
    payload = {
        "pid": pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
    }
    tmp_path = paths["worker_state_path"].with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(paths["worker_state_path"])


def _clear_dev_publish_worker_state(
    *,
    releases_root: Path,
    release_id: str,
    pid: int | None = None,
) -> None:
    paths = _dev_publish_paths(releases_root, release_id)
    if pid is not None and paths["worker_pid_path"].exists():
        try:
            if int(paths["worker_pid_path"].read_text(encoding="utf-8").strip()) != pid:
                return
        except ValueError:
            pass
    paths["worker_pid_path"].unlink(missing_ok=True)
    paths["worker_state_path"].unlink(missing_ok=True)


def _dev_publish_worker_pid(releases_root: Path, release_id: str) -> int | None:
    path = _dev_publish_paths(releases_root, release_id)["worker_pid_path"]
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _dev_publish_worker_state(releases_root: Path, release_id: str) -> dict | None:
    path = _dev_publish_paths(releases_root, release_id)["worker_state_path"]
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _latest_dev_publish_id(releases_root: Path) -> str | None:
    env_root = release_env_root(releases_root, "dev").expanduser().resolve()
    if not env_root.exists():
        return None
    candidates = [
        path
        for path in env_root.iterdir()
        if path.is_dir() and path.name != "current"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime).name


def _copy_release_tree(source_root: Path, target_root: Path) -> None:
    if target_root.exists():
        raise FileExistsError(f"Release directory already exists: {target_root}")
    target_root.parent.mkdir(parents=True, exist_ok=True)
    target_root.mkdir()
    try:
        result = subprocess.run(
            ["cp", "-cR", f"{source_root}/.", str(target_root)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        result = None
    if result is not None and result.returncode == 0:
        return
    shutil.rmtree(target_root)
    shutil.copytree(source_root, target_root, ignore=shutil.ignore_patterns(".krw_pipeline"))


def _retarget_queue_publish_jobs(
    *,
    store: PipelineQueue,
    publish_root: Path,
    publish_index_path: Path | None,
    statuses: set[str],
    dry_run: bool = False,
) -> dict[str, int]:
    jobs = store.list_jobs(statuses=statuses)
    changed = 0
    for job in jobs:
        next_publish_root = str(publish_root)
        next_publish_index_path = str(publish_index_path) if publish_index_path is not None else None
        if job.publish_root == next_publish_root and job.publish_index_path == next_publish_index_path:
            continue
        changed += 1
        if dry_run:
            continue
        job.publish_root = next_publish_root
        job.publish_index_path = next_publish_index_path
        store.save_job(job)
        store.append_event(
            "publish_retargeted",
            job,
            {
                "publish_root": next_publish_root,
                "publish_index_path": next_publish_index_path,
            },
        )
    return {"scanned": len(jobs), "changed": changed}


def _resolve_release_id_from_publish_config(env: str, releases_root: Path) -> str | None:
    config = load_cli_config()
    if not config.publish_root:
        return None
    publish_root = Path(config.publish_root).expanduser().resolve()
    env_root = release_env_root(releases_root, env).expanduser().resolve()
    if publish_root.parent == env_root:
        return publish_root.name
    return None


def _release_prepare_dev(
    *,
    releases_root: Path,
    release_id: str,
    source_root: Path | None,
    empty: bool,
    set_config: bool,
    retarget_queue: bool,
    queue_root: Path | None,
    allow_running_queue: bool,
    dry_run: bool,
) -> dict[str, str | int | bool | None]:
    env = "dev"
    env_root = release_env_root(releases_root, env).expanduser().resolve()
    release_root = env_root / release_id
    if release_root.exists():
        raise FileExistsError(f"Release directory already exists: {release_root}")
    if empty and source_root is not None:
        raise ValueError("Use either --empty or --from-root, not both.")

    resolved_source_root: Path | None = None
    if source_root is not None:
        resolved_source_root = source_root.expanduser().resolve()
    elif not empty:
        current = env_root / "current"
        if current.exists():
            resolved_source_root = current.resolve()

    retarget_store: PipelineQueue | None = None
    if retarget_queue and not dry_run:
        resolved_queue_root = resolve_running_root(queue_root, fallback_to_cwd=False)
        retarget_store = PipelineQueue(resolved_queue_root)
        retarget_store.ensure_dirs()
        if retarget_store.worker_is_running() and not allow_running_queue:
            raise RuntimeError(
                "Queue worker is running. Stop it first, or pass --allow-running-queue."
            )

    if dry_run:
        return {
            "release_id": release_id,
            "release_root": str(release_root),
            "source_root": str(resolved_source_root) if resolved_source_root else None,
            "config_updated": set_config,
            "queue_retargeted": retarget_queue,
            "queue_jobs_changed": 0,
        }

    if resolved_source_root is not None:
        if not resolved_source_root.is_dir():
            raise FileNotFoundError(f"Source release root not found: {resolved_source_root}")
        _copy_release_tree(resolved_source_root, release_root)
    else:
        release_root.mkdir(parents=True)
    shutil.rmtree(release_root / ".krw_pipeline", ignore_errors=True)

    index_path = _default_release_index_path(release_root)
    write_release_manifest(
        release_root,
        release_id=release_id,
        env=env,
        source_root=resolved_source_root or release_root,
        index_path=index_path,
        write_legacy=True,
    )

    if set_config:
        set_config_value("publish-root", str(release_root))
        set_config_value("publish-index-path", str(index_path))

    queue_jobs_changed = 0
    queue_retargeted = False
    if retarget_queue:
        assert retarget_store is not None
        retarget_result = _retarget_queue_publish_jobs(
            store=retarget_store,
            publish_root=release_root,
            publish_index_path=index_path,
            statuses={PENDING},
        )
        queue_jobs_changed = retarget_result["changed"]
        queue_retargeted = True

    return {
        "release_id": release_id,
        "release_root": str(release_root),
        "source_root": str(resolved_source_root) if resolved_source_root else None,
        "config_updated": set_config,
        "queue_retargeted": queue_retargeted,
        "queue_jobs_changed": queue_jobs_changed,
    }


@release_app.command("prepare-dev")
def release_prepare_dev_cmd(
    release_id: Optional[str] = typer.Argument(
        None,
        help="Dev release id to create. Defaults to a timestamp id.",
    ),
    releases_root: Path = typer.Option(
        _default_releases_root(),
        "--releases-root",
        help="Local releases root.",
    ),
    source_root: Optional[Path] = typer.Option(
        None,
        "--from-root",
        help="Seed the new dev release from this root. Defaults to releases/dev/current.",
    ),
    empty: bool = typer.Option(
        False,
        "--empty",
        help="Create an empty dev release instead of seeding from dev/current.",
    ),
    set_config: bool = typer.Option(
        True,
        "--set-config/--no-set-config",
        help="Point CLI publish-root and publish-index-path at the new dev release.",
    ),
    retarget_queue: bool = typer.Option(
        True,
        "--retarget-queue/--no-retarget-queue",
        help="Retarget pending queue jobs to the new dev release publish root.",
    ),
    queue_root: Optional[Path] = typer.Option(
        None,
        "--queue-root",
        help="Queue root to retarget. Defaults to configured running-root.",
    ),
    allow_running_queue: bool = typer.Option(
        False,
        "--allow-running-queue",
        help="Allow retargeting pending jobs while the queue worker is running.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the release that would be prepared."),
) -> None:
    """Create a mutable dev release workspace and point queue publishing at it."""
    resolved_release_id = release_id or _default_release_id()
    try:
        result = _release_prepare_dev(
            releases_root=releases_root,
            release_id=resolved_release_id,
            source_root=source_root,
            empty=empty,
            set_config=set_config,
            retarget_queue=retarget_queue,
            queue_root=queue_root,
            allow_running_queue=allow_running_queue,
            dry_run=dry_run,
        )
    except Exception as exc:
        typer.echo(f"FAILED prepare dev release: {exc}")
        raise typer.Exit(1) from exc

    prefix = "Dry run: would prepare" if dry_run else "Prepared"
    typer.echo(f"{prefix} dev release: {result['release_id']}")
    typer.echo(f"release_root: {result['release_root']}")
    typer.echo(f"source_root: {result['source_root'] or '<empty>'}")
    typer.echo(f"config_updated: {result['config_updated']}")
    typer.echo(f"queue_retargeted: {result['queue_retargeted']}")
    typer.echo(f"queue_jobs_changed: {result['queue_jobs_changed']}")
    typer.echo("Next: krw-ontology queue start --no-rebuild-agent-index")


@release_app.command("publish-dev")
def release_publish_dev_cmd(
    release_id: Optional[str] = typer.Argument(
        None,
        help="Dev release id to create. Defaults to a timestamp id.",
    ),
    releases_root: Path = typer.Option(
        _default_releases_root(),
        "--releases-root",
        help="Local releases root.",
    ),
    source_root: Optional[Path] = typer.Option(
        None,
        "--from-root",
        help="Source running root. Defaults to configured running-root.",
    ),
    build_index: bool = typer.Option(
        True,
        "--build-index/--no-build-index",
        help="Rebuild agent_index.sqlite inside the new dev release.",
    ),
    promote: bool = typer.Option(
        True,
        "--promote/--no-promote",
        help="Promote dev/current to the new release after verification.",
    ),
    allow_running_queue: bool = typer.Option(
        False,
        "--allow-running-queue",
        help="Allow reading running-root while the queue worker is running.",
    ),
    foreground: bool = typer.Option(False, "--foreground", help="Run in the foreground instead of starting a background worker."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be published."),
) -> None:
    """Rebuild the running-root index into a new dev release and promote dev/current."""
    resolved_release_id = release_id or _default_release_id()
    try:
        resolved_source_root = (
            source_root.expanduser().resolve()
            if source_root is not None
            else resolve_running_root(None, fallback_to_cwd=False)
        )
        if dry_run or foreground:
            result = _release_publish_dev(
                releases_root=releases_root,
                release_id=resolved_release_id,
                source_root=resolved_source_root,
                build_index=build_index,
                promote=promote,
                allow_running_queue=allow_running_queue,
                dry_run=dry_run,
            )
        else:
            if not resolved_source_root.is_dir():
                raise FileNotFoundError(f"Source root not found: {resolved_source_root}")
            if not _release_root_has_ontology_artifacts(resolved_source_root):
                raise RuntimeError(
                    "Source root has no ontology artifacts under companies/: "
                    f"{resolved_source_root}"
                )
            queue = PipelineQueue(resolved_source_root)
            queue.ensure_dirs()
            if queue.worker_is_running() and not allow_running_queue:
                raise RuntimeError(
                    "Queue worker is running. Stop it first, or pass --allow-running-queue."
                )
            paths = _dev_publish_paths(releases_root, resolved_release_id)
            if paths["release_root"].exists():
                raise FileExistsError(f"Release directory already exists: {paths['release_root']}")
            paths["log_path"].parent.mkdir(parents=True, exist_ok=True)
            command = [
                sys.executable,
                "-c",
                "from krw_ontology.cli.main import app; app()",
                "release-publish-dev-worker",
                "--release-id",
                resolved_release_id,
                "--releases-root",
                str(releases_root),
                "--from-root",
                str(resolved_source_root),
            ]
            if build_index:
                command.append("--build-index")
            else:
                command.append("--no-build-index")
            if promote:
                command.append("--promote")
            else:
                command.append("--no-promote")
            if allow_running_queue:
                command.append("--allow-running-queue")
            with paths["log_path"].open("a", encoding="utf-8") as log_handle:
                log_handle.write(f"\n[{_now_label()}] publish-dev launching background worker\n")
                log_handle.flush()
                process = subprocess.Popen(
                    command,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            typer.echo(f"Started dev publish worker pid={process.pid}")
            typer.echo(f"release_id: {resolved_release_id}")
            typer.echo(f"source_root: {resolved_source_root}")
            typer.echo(f"release_root: {paths['release_root']}")
            typer.echo(f"index_path: {paths['index_path']}")
            typer.echo(f"log: {paths['log_path']}")
            typer.echo(f"progress: {paths['progress_path']}")
            typer.echo(f"watch: krw-ontology release publish-dev-watch {resolved_release_id}")
            return
    except Exception as exc:
        typer.echo(f"FAILED publish dev release: {exc}")
        raise typer.Exit(1) from exc

    prefix = "Dry run: would publish" if dry_run else "Dev release published"
    typer.echo(f"{prefix}: {result['release_id']}")
    typer.echo(f"source_root: {result['source_root']}")
    typer.echo(f"release_root: {result['release_root']}")
    typer.echo(f"index_path: {result['index_path']}")
    if result["totals"] is not None:
        totals = result["totals"]
        typer.echo(
            "Agent index built: "
            f"documents={totals['documents']} "
            f"objects={totals['objects']} "
            f"edges={totals['edges']} "
            f"quality_events={totals['quality_events']}"
        )
    else:
        typer.echo("Agent index rebuild skipped.")
    typer.echo(f"promoted: {result['promoted']}")
    if not dry_run:
        typer.echo(f"manifest: {result['manifest']}")
        typer.echo("Next: krw-ontology release materialize-prod " + result["release_id"])


@app.command("release-publish-dev-worker", hidden=True)
def release_publish_dev_worker_cmd(
    release_id: str = typer.Option(..., "--release-id", help="Dev release id."),
    releases_root: Path = typer.Option(_default_releases_root(), "--releases-root"),
    source_root: Path = typer.Option(..., "--from-root", help="Source running root."),
    build_index: bool = typer.Option(True, "--build-index/--no-build-index"),
    promote: bool = typer.Option(True, "--promote/--no-promote"),
    allow_running_queue: bool = typer.Option(False, "--allow-running-queue"),
) -> None:
    """Internal background worker for publish-dev."""
    try:
        _write_dev_publish_worker_state(
            releases_root=releases_root,
            release_id=release_id,
            pid=os.getpid(),
            mode={
                "source_root": str(source_root.expanduser().resolve()),
                "build_index": build_index,
                "promote": promote,
            },
        )
        typer.echo(f"[{_now_label()}] publish-dev worker started release_id={release_id}")
        result = _release_publish_dev(
            releases_root=releases_root,
            release_id=release_id,
            source_root=source_root,
            build_index=build_index,
            promote=promote,
            allow_running_queue=allow_running_queue,
            dry_run=False,
            allow_prepared_release_root=True,
        )
        typer.echo(f"Dev release published: {result['release_id']}")
        typer.echo(f"source_root: {result['source_root']}")
        typer.echo(f"release_root: {result['release_root']}")
        typer.echo(f"index_path: {result['index_path']}")
        if result["totals"] is not None:
            totals = result["totals"]
            typer.echo(
                "Agent index built: "
                f"documents={totals['documents']} "
                f"objects={totals['objects']} "
                f"edges={totals['edges']} "
                f"quality_events={totals['quality_events']}"
            )
        else:
            typer.echo("Agent index rebuild skipped.")
        typer.echo(f"promoted: {result['promoted']}")
        typer.echo(f"manifest: {result['manifest']}")
        typer.echo("Next: krw-ontology release materialize-prod " + result["release_id"])
    except Exception as exc:
        typer.echo(f"FAILED publish-dev worker: {exc}")
        raise typer.Exit(1) from exc
    finally:
        _clear_dev_publish_worker_state(
            releases_root=releases_root,
            release_id=release_id,
            pid=os.getpid(),
        )


@release_app.command("publish-dev-status")
def release_publish_dev_status_cmd(
    release_id: Optional[str] = typer.Argument(None, help="Dev release id. Defaults to latest dev publish release."),
    releases_root: Path = typer.Option(_default_releases_root(), "--releases-root", help="Local releases root."),
) -> None:
    """Show publish-dev worker and artifact status."""
    selected_release_id = release_id or _latest_dev_publish_id(releases_root)
    if selected_release_id is None:
        typer.echo("No dev release found.")
        raise typer.Exit(1)
    paths = _dev_publish_paths(releases_root, selected_release_id)
    pid = _dev_publish_worker_pid(releases_root, selected_release_id)
    running = pid is not None and is_pid_running(pid)
    current_id = current_release_id(release_env_root(releases_root, "dev").expanduser().resolve())
    typer.echo("Publish-dev status")
    typer.echo(f"release_id: {selected_release_id}")
    typer.echo(f"worker: {'running' if running else 'stopped'}" + (f" pid={pid}" if pid else ""))
    typer.echo(f"current: {current_id or '<missing>'}")
    typer.echo(f"release_root: {paths['release_root']}")
    typer.echo(f"index: {'present' if paths['index_path'].exists() else 'missing'} {paths['index_path']}")
    typer.echo(f"manifest: {'present' if (paths['release_root'] / RELEASE_MANIFEST_FILENAME).exists() else 'missing'}")
    typer.echo(f"log: {paths['log_path']}")
    typer.echo(f"progress: {paths['progress_path']}")


@release_app.command("publish-dev-watch")
def release_publish_dev_watch_cmd(
    release_id: Optional[str] = typer.Argument(None, help="Dev release id. Defaults to latest dev publish release."),
    releases_root: Path = typer.Option(_default_releases_root(), "--releases-root", help="Local releases root."),
    lines: int = typer.Option(80, "--lines", min=1, help="Number of trailing lines to show first."),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Follow appended log output."),
) -> None:
    """Watch publish-dev logs."""
    selected_release_id = release_id or _latest_dev_publish_id(releases_root)
    if selected_release_id is None:
        typer.echo("No dev release found.")
        raise typer.Exit(1)
    path = _dev_publish_paths(releases_root, selected_release_id)["log_path"]
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


@release_app.command("finalize-dev")
def release_finalize_dev_cmd(
    release_id: Optional[str] = typer.Argument(
        None,
        help=(
            "Dev release id to finalize. Defaults to the configured publish-root "
            "release id, then dev/current."
        ),
    ),
    releases_root: Path = typer.Option(
        _default_releases_root(),
        "--releases-root",
        help="Local releases root.",
    ),
    build_index: bool = typer.Option(
        True,
        "--build-index/--no-build-index",
        help="Rebuild agent_index.sqlite before writing the manifest.",
    ),
    promote: bool = typer.Option(
        True,
        "--promote/--no-promote",
        help="Promote dev/current to this release after verification.",
    ),
    clear_config: bool = typer.Option(
        True,
        "--clear-config/--keep-config",
        help="Clear publish-root config after finalizing to avoid mutating the immutable release.",
    ),
) -> None:
    """Rebuild, verify, and promote a completed dev release."""
    env = "dev"
    release_id = release_id or _resolve_release_id_for_finalize_dev(env, releases_root)
    if release_id is None:
        typer.echo("Missing release id. Pass one, run prepare-dev first, or promote dev/current.")
        raise typer.Exit(1)
    release_root = release_env_root(releases_root, env).expanduser().resolve() / release_id
    index_path = _default_release_index_path(release_root)
    if not release_root.is_dir():
        typer.echo(f"Release directory not found: {release_root}")
        raise typer.Exit(1)
    if not _release_root_has_ontology_artifacts(release_root):
        typer.echo(
            "Release has no ontology artifacts under companies/: "
            f"{release_root}. Refusing to finalize an empty index."
        )
        raise typer.Exit(1)

    if build_index:
        from krw_ontology.agent_index import build_agent_index

        index_result = build_agent_index(release_root, index_path=index_path, force=True)
        totals = index_result["totals"]
        typer.echo(
            "Agent index built: "
            f"{index_result['index_path']} "
            f"documents={totals['documents']} "
            f"objects={totals['objects']} "
            f"edges={totals['edges']} "
            f"quality_events={totals['quality_events']}"
        )

    manifest = write_release_manifest(
        release_root,
        release_id=release_id,
        env=env,
        index_path=index_path,
        write_legacy=True,
    )
    verification = verify_release_root(release_root, env=env, index_path=index_path)
    if not verification["ok"]:
        typer.echo(f"Release verify failed: {', '.join(verification['errors'])}")
        raise typer.Exit(1)

    if promote:
        promote_local_release(releases_root, env=env, release_id=release_id)
        typer.echo(f"Release promoted: env=dev release_id={release_id}")

    if clear_config:
        config = load_cli_config()
        if config.publish_root == str(release_root):
            unset_config_value("publish-root")
        if config.publish_index_path == str(index_path):
            unset_config_value("publish-index-path")

    typer.echo(f"Dev release finalized: {release_id}")
    typer.echo(f"release_root: {release_root}")
    typer.echo(f"manifest: {release_root / RELEASE_MANIFEST_FILENAME}")
    typer.echo(f"index_present: {manifest['index_present']}")
    typer.echo("Next: krw-ontology release materialize-prod " + release_id)


@release_app.command("materialize-prod")
def release_materialize_prod_cmd(
    release_id: str = typer.Argument(..., help="Source dev/staging release id to copy."),
    releases_root: Path = typer.Option(
        _default_releases_root(),
        "--releases-root",
        help="Local releases root.",
    ),
    from_env: str = typer.Option(
        "dev",
        "--from-env",
        help="Source env to copy from: dev or staging.",
    ),
    prod_release_id: Optional[str] = typer.Option(
        None,
        "--prod-release-id",
        help="Prod release id to create. Defaults to the source release id.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the prod release that would be created."),
) -> None:
    """Copy a verified dev/staging release into prod release space without activating it."""
    source_env = normalize_ontology_env(from_env)
    if source_env == "prod":
        typer.echo("--from-env must be dev or staging.")
        raise typer.Exit(1)
    target_id = prod_release_id or release_id
    source_root = release_env_root(releases_root, source_env).expanduser().resolve() / release_id
    prod_root = release_env_root(releases_root, "prod").expanduser().resolve() / target_id
    source_verification = verify_release_root(source_root, env=source_env)
    if not source_verification["ok"]:
        typer.echo(f"Source release verify failed: {', '.join(source_verification['errors'])}")
        raise typer.Exit(1)
    if prod_root.exists():
        typer.echo(f"Prod release already exists: {prod_root}")
        raise typer.Exit(1)
    if dry_run:
        typer.echo(f"Dry run: would materialize prod release {target_id}")
        typer.echo(f"source_root: {source_root}")
        typer.echo(f"prod_root: {prod_root}")
        return

    _copy_release_tree(source_root, prod_root)
    shutil.rmtree(prod_root / ".krw_pipeline", ignore_errors=True)
    index_path = _default_release_index_path(prod_root)
    write_release_manifest(
        prod_root,
        release_id=target_id,
        env="prod",
        source_root=source_root,
        index_path=index_path,
        write_legacy=True,
    )
    prod_verification = verify_release_root(prod_root, env="prod", index_path=index_path)
    if not prod_verification["ok"]:
        typer.echo(f"Prod release verify failed: {', '.join(prod_verification['errors'])}")
        raise typer.Exit(1)

    typer.echo(f"Prod release materialized: {target_id}")
    typer.echo(f"prod_root: {prod_root}")
    typer.echo(f"Next: npm run deploy:data -- --release-id {target_id}")


@release_app.command("write-manifest")
def release_write_manifest_cmd(
    root: Path = typer.Option(..., "--root", help="Immutable release root to describe."),
    env: str = typer.Option(
        "dev",
        "--env",
        help="Ontology environment: dev, staging, or prod.",
    ),
    release_id: Optional[str] = typer.Option(
        None,
        "--release-id",
        help="Release id. Defaults to a timestamp id.",
    ),
    index_path: Optional[Path] = typer.Option(
        None,
        "--index-path",
        help="Optional explicit agent_index.sqlite path.",
    ),
) -> None:
    """Write canonical manifest.json for a local immutable release root."""
    resolved_release_id = release_id or _new_release_id()
    manifest = write_release_manifest(
        root,
        release_id=resolved_release_id,
        env=env,
        index_path=index_path,
        write_legacy=True,
    )
    typer.echo(f"Release manifest written: {Path(root).expanduser().resolve() / RELEASE_MANIFEST_FILENAME}")
    typer.echo(f"env: {manifest['env']}")
    typer.echo(f"release_id: {manifest['release_id']}")
    typer.echo(f"index_present: {manifest['index_present']}")


@release_app.command("verify")
def release_verify_cmd(
    root: Path = typer.Option(..., "--root", help="Release root or env/current symlink to verify."),
    env: Optional[str] = typer.Option(None, "--env", help="Expected ontology environment."),
    require_current_symlink: bool = typer.Option(
        False,
        "--require-current-symlink",
        help="Require --root to be the env current symlink.",
    ),
    index_path: Optional[Path] = typer.Option(
        None,
        "--index-path",
        help="Optional explicit agent_index.sqlite path.",
    ),
) -> None:
    """Verify release manifest, env, release id, and index presence."""
    verification = verify_release_root(
        root,
        env=env,
        index_path=index_path,
        require_current_symlink=require_current_symlink,
    )
    typer.echo(f"Release verify: {'ok' if verification['ok'] else 'failed'}")
    typer.echo(f"root: {verification['root']}")
    typer.echo(f"env: {verification.get('env') or '<missing>'}")
    typer.echo(f"release_id: {verification.get('release_id') or '<missing>'}")
    typer.echo(f"manifest: {verification.get('manifest_path') or '<missing>'}")
    typer.echo(f"index: {'present' if verification.get('index_present') else 'missing'}")
    if verification["errors"]:
        for error in verification["errors"]:
            typer.echo(f"FAIL {error}")
        raise typer.Exit(1)


@release_app.command("status")
def release_status_cmd(
    releases_root: Path = typer.Option(
        ...,
        "--releases-root",
        help="Path to releases root. Accepts either /data/releases or /data/releases/<env>.",
    ),
    env: str = typer.Option("dev", "--env", help="Ontology environment: dev, staging, or prod."),
) -> None:
    """Show local env release current pointer and release list."""
    resolved_env = normalize_ontology_env(env)
    env_root = release_env_root(releases_root, resolved_env)
    current_id = current_release_id(env_root)
    typer.echo("Release status")
    typer.echo(f"env: {resolved_env}")
    typer.echo(f"env_root: {env_root.expanduser().resolve()}")
    typer.echo(f"current: {current_id or '<missing>'}")
    releases = list_release_ids(env_root)
    if releases:
        typer.echo("releases:")
        for release_id in releases[:10]:
            marker = " current" if release_id == current_id else ""
            typer.echo(f"  - {release_id}{marker}")
    else:
        typer.echo("releases: <none>")


@release_app.command("promote")
def release_promote_cmd(
    release_id: str = typer.Argument(..., help="Release id to promote to env current."),
    releases_root: Path = typer.Option(
        ...,
        "--releases-root",
        help="Path to releases root. Accepts either /data/releases or /data/releases/<env>.",
    ),
    env: str = typer.Option("dev", "--env", help="Ontology environment: dev, staging, or prod."),
) -> None:
    """Atomically point env current to a verified release."""
    result = promote_local_release(releases_root, env=env, release_id=release_id)
    typer.echo(f"Release promoted: env={result['env']} release_id={result['release_id']}")
    typer.echo(f"current: {result['current']}")


@release_app.command("rollback")
def release_rollback_cmd(
    release_id: Optional[str] = typer.Argument(
        None,
        help="Release id to activate. If omitted, activate the newest non-current release.",
    ),
    releases_root: Path = typer.Option(
        ...,
        "--releases-root",
        help="Path to releases root. Accepts either /data/releases or /data/releases/<env>.",
    ),
    env: str = typer.Option("dev", "--env", help="Ontology environment: dev, staging, or prod."),
) -> None:
    """Rollback local env current to a requested or previous release."""
    result = rollback_local_release(releases_root, env=env, release_id=release_id)
    typer.echo(f"Release rollback activated: env={result['env']} release_id={result['release_id']}")
    typer.echo(f"current: {result['current']}")


@release_app.command("export-web-catalog")
def release_export_web_catalog_cmd(
    root: Path = typer.Option(..., "--root", help="Existing release root to export from."),
    env: Optional[str] = typer.Option(None, "--env", help="Expected ontology environment."),
    out: Path = typer.Option(..., "--out", help="Output web_catalog.json path."),
    index_path: Optional[Path] = typer.Option(
        None,
        "--index-path",
        help="Optional explicit agent_index.sqlite path.",
    ),
) -> None:
    """Export a read-only compact web catalog JSON from an existing release."""
    try:
        catalog = write_web_catalog(root, out, env=env, index_path=index_path)
    except Exception as exc:
        typer.echo(f"FAILED export web catalog: {exc}")
        raise typer.Exit(1) from exc
    summary = catalog["summary"]
    typer.echo(f"Web catalog exported: {Path(out).expanduser().resolve()}")
    typer.echo(f"env: {catalog['env']}")
    typer.echo(f"release_id: {catalog['release_id']}")
    typer.echo(
        "Catalog contains "
        f"{summary['company_count']} companies, "
        f"{summary['document_count']} documents, "
        f"{summary['object_count']} objects"
    )


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
    pilot: bool = typer.Option(
        False,
        "--pilot",
        help="Run a fast development profile: core sections only, capped quote spans, same artifact contract.",
    ),
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
        pilot=pilot,
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
    pilot: bool = typer.Option(
        False,
        "--pilot",
        help="Run a fast development profile: core sections only, capped quote spans, same artifact contract.",
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
    if pilot:
        typer.echo("EXECUTION_MODE=pilot")
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
                pilot=pilot,
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
        help="Number of latest 10-K filings to include; 10-Qs are limited to the current calendar year.",
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
    pilot: bool = typer.Option(
        False,
        "--pilot",
        help="Run a fast development profile for each filing; do not use for final publish quality.",
    ),
) -> None:
    """Build the default research set: latest 10-Ks plus current calendar year 10-Qs."""
    from krw_ontology.agent_index import build_agent_index
    from krw_ontology.config.settings import PipelineConfig
    from krw_ontology.pipeline.stages.build_company_context import build_company_context
    from krw_ontology.pipeline.orchestrator import run_pipeline
    from krw_ontology.pipeline.research_plan import discover_research_filing_targets

    run_tickers = [ticker.upper() for ticker in tickers]
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    stable_root = resolve_publish_root(publish_root)
    resolved_publish_index_path = resolve_publish_index_path(publish_index_path)
    if pilot and stable_root is not None:
        typer.echo("Refusing --pilot with --publish-root. Pilot artifacts are for development only.")
        raise typer.Exit(1)
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
    if pilot:
        typer.echo("EXECUTION_MODE=pilot")
    typer.echo(f"Research scope: {years} latest 10-K filings + current calendar year 10-Q periods")

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
                        pilot=pilot,
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


def _path_points_at_prod_current(path: Path) -> bool:
    expanded = path.expanduser()
    candidates = [expanded, *expanded.parents]
    for candidate in candidates:
        parts = candidate.parts
        for index in range(0, max(len(parts) - 2, 0)):
            if parts[index : index + 3] == ("releases", "prod", "current"):
                return True

        if (
            candidate.parent.name == "prod"
            and candidate.parent.parent.name == "releases"
            and candidate.exists()
        ):
            current = candidate.parent / "current"
            try:
                if current.is_symlink() and candidate.resolve() == current.resolve():
                    return True
            except OSError:
                pass

    return False


def _assert_queue_path_not_prod_current(path: Path | None, label: str) -> None:
    if path is None:
        return
    if not _path_points_at_prod_current(path):
        return
    raise ValueError(
        f"Refusing queue {label}={path}: prod/current is an immutable release pointer. "
        "Build in a dev/staging release directory and promote the verified release instead."
    )


def _exit_if_queue_path_mutates_prod_current(path: Path | None, label: str) -> None:
    try:
        _assert_queue_path_not_prod_current(path, label)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc


def _queue_publish_and_defer_index(
    *,
    store: PipelineQueue,
    job: QueueJob,
    output_root: Path,
    rebuild_agent_index: bool,
) -> tuple[Path, Path | None] | None:
    _assert_queue_path_not_prod_current(output_root, "--root")
    if job.publish_root:
        stable_root = resolve_ontology_root(Path(job.publish_root), fallback_to_cwd=False)
        _assert_queue_path_not_prod_current(stable_root, "--publish-root")
        stable_root.mkdir(parents=True, exist_ok=True)
        publish_index_path = Path(job.publish_index_path) if job.publish_index_path is not None else None
        _assert_queue_path_not_prod_current(publish_index_path, "--publish-index-path")
        _queue_emit(store, job, f"Publishing {job.ticker} to stable root {stable_root}")
        with FileProcessLock(PipelineQueue(stable_root).publish_lock_path):
            _publish_ticker_tree(output_root, stable_root, job.ticker)
        if not rebuild_agent_index:
            _queue_emit(
                store,
                job,
                f"Published {job.ticker} to stable root; agent index rebuild skipped by default",
            )
            return None
        _queue_emit(
            store,
            job,
            f"Published {job.ticker} to stable root; stable index rebuild deferred until batch completion",
        )
        return stable_root, publish_index_path

    _queue_emit(
        store,
        job,
        (
            "No publish root configured; staging agent index rebuild deferred until batch completion"
            if rebuild_agent_index
            else "No publish root configured; staging agent index rebuild skipped by default"
        ),
    )
    return (output_root, None) if rebuild_agent_index else None


def _queue_rebuild_pending_indexes(
    *,
    rebuild_targets: dict[str, tuple[Path, Path | None]],
    publish_prod: bool,
    build_agent_index,
) -> None:
    if not rebuild_targets:
        return
    typer.echo(f"[{_now_label()}] Rebuilding {len(rebuild_targets)} pending queue index root(s)")
    for root, index_path in list(rebuild_targets.values()):
        _assert_queue_path_not_prod_current(root, "index root")
        _assert_queue_path_not_prod_current(index_path, "index path")
        typer.echo(f"[{_now_label()}] Rebuilding agent index root={root}")
        with FileProcessLock(PipelineQueue(root).publish_lock_path):
            index_result = build_agent_index(root, index_path=index_path, force=True)
        totals = index_result["totals"]
        typer.echo(
            f"[{_now_label()}] Agent index built: "
            f"{index_result['index_path']} "
            f"documents={totals['documents']} "
            f"objects={totals['objects']} "
            f"edges={totals['edges']} "
            f"quality_events={totals['quality_events']}"
        )
        if publish_prod:
            typer.echo(f"[{_now_label()}] Publishing stable root to prod from {root}")
            prod_result = _publish_prod_root(stable_root=root)
            typer.echo(
                f"[{_now_label()}] Prod release activated: "
                f"release={prod_result['release_id']} "
                f"host={prod_result['host']} "
                f"remote_root={prod_result['remote_root']}"
            )
    rebuild_targets.clear()


def _queue_build_company_context(
    *,
    store: PipelineQueue,
    job: QueueJob,
    output_root: Path,
    build_company_context,
) -> None:
    _queue_emit(store, job, f"Building company context for {job.ticker}")
    context_result = build_company_context(output_root, job.ticker)
    _queue_emit(
        store,
        job,
        "Company context built: "
        f"{context_result['artifact_index_path']} counts={context_result['counts']}",
    )


def _process_queue_job(
    store: PipelineQueue,
    job: QueueJob,
    output_root: Path,
    *,
    publish_prod: bool = False,
    rebuild_agent_index: bool = False,
) -> tuple[Path, Path | None] | None:
    from krw_ontology.config.settings import PipelineConfig
    from krw_ontology.pipeline.orchestrator import run_pipeline
    from krw_ontology.pipeline.research_plan import discover_research_filing_targets
    from krw_ontology.pipeline.stages.build_company_context import build_company_context

    job = store.mark_running(job)
    _queue_emit(store, job, f"START job={job.job_id} type={job.job_type} ticker={job.ticker}")
    rebuild_target: tuple[Path, Path | None] | None = None
    try:
        _assert_queue_path_not_prod_current(output_root, "--root")
        if publish_prod and not job.publish_root:
            raise ValueError(
                "--publish-prod requires jobs with a stable publish root. "
                "Set `krw-ontology config set publish-root ...` before adding jobs, "
                "or add jobs with --publish-root."
            )
        if job.job_type == FULL_REFRESH:
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
        elif job.job_type == FILING_UPDATE:
            document_type = job.document_type or "10-Q"
            filing_periods: list[Optional[str]] = [None] if job.latest else list(job.periods or [])
            if not filing_periods:
                raise ValueError("filing_update job requires periods or latest=true")
            for filing_period in filing_periods:
                filing_label = f"{job.ticker} {document_type} {'latest' if job.latest else filing_period}"
                _queue_emit(store, job, f"START update {filing_label}")
                run_pipeline(
                    ticker=job.ticker,
                    document_type=document_type,
                    latest=job.latest,
                    period=filing_period,
                    force=job.force,
                    output_dir=output_root,
                )
                _queue_emit(store, job, f"END update {filing_label}")
        else:
            raise ValueError(f"Unsupported queue job_type: {job.job_type}")

        _queue_build_company_context(
            store=store,
            job=job,
            output_root=output_root,
            build_company_context=build_company_context,
        )
        rebuild_target = _queue_publish_and_defer_index(
            store=store,
            job=job,
            output_root=output_root,
            rebuild_agent_index=rebuild_agent_index,
        )
    except Exception as exc:
        store.mark_failed(job, str(exc))
        _queue_emit(store, job, f"FAILED job={job.job_id} ticker={job.ticker}: {exc}")
        return None

    store.mark_succeeded(job)
    _queue_emit(store, job, f"SUCCEEDED job={job.job_id} ticker={job.ticker}")
    return rebuild_target


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
        help="Number of latest 10-K filings to include; 10-Qs are limited to the current calendar year.",
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
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
    _exit_if_queue_path_mutates_prod_current(stable_root, "--publish-root")
    _exit_if_queue_path_mutates_prod_current(resolved_publish_index_path, "--publish-index-path")
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
    "update",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue update VG --document-type 10-Q --period FY2026Q1\n"
        "  krw-ontology queue update VG --document-type 10-Q --latest\n"
        "  krw-ontology queue update VG --period FY2026Q1 --root /path/to/running "
        "--publish-root /path/to/stable\n\n"
        "A filing update job runs only the selected filing(s), then rebuilds company context "
        "and publishes the ticker once."
    ),
)
@app.command("queue-update", hidden=True)
def queue_update_cmd(
    ticker: str = typer.Argument(..., help="Ticker symbol to append as a filing update job."),
    document_type: str = typer.Option(
        "10-Q",
        "--document-type",
        help="Document type to update (10-K or 10-Q).",
    ),
    periods: Optional[list[str]] = typer.Option(
        None,
        "--period",
        help=(
            "Explicit period to update, e.g. FY2026Q1. Repeat this option to update "
            "multiple filings before publishing."
        ),
    ),
    latest: bool = typer.Option(
        False,
        "--latest",
        help="Update the latest filing for the selected document type.",
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
        help="Stable ontology data root to publish the completed ticker into.",
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
    """Append one ticker filing-update job to the local queue."""
    validate_document_type(document_type)
    selected_periods = list(periods or [])
    if latest and selected_periods:
        typer.echo("Use either --latest or --period, not both.")
        raise typer.Exit(1)
    if not latest and not selected_periods:
        typer.echo("Specify at least one --period <PERIOD> or --latest.")
        raise typer.Exit(1)

    output_root = resolve_running_root(root, fallback_to_cwd=False)
    stable_root = resolve_publish_root(publish_root)
    resolved_publish_index_path = resolve_publish_index_path(publish_index_path)
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
    _exit_if_queue_path_mutates_prod_current(stable_root, "--publish-root")
    _exit_if_queue_path_mutates_prod_current(resolved_publish_index_path, "--publish-index-path")
    store = PipelineQueue(output_root)
    store.ensure_dirs()

    normalized = ticker.upper()
    typer.echo(f"QUEUE_ROOT={store.queue_dir}")
    active = store.active_job_for_ticker(normalized)
    if active is not None and not allow_duplicate:
        typer.echo(
            f"Skipped {normalized}; active job already exists: "
            f"{active.job_id} status={active.status}"
        )
        typer.echo("Added 0 job(s)")
        return

    job = store.add_update_job(
        normalized,
        document_type=document_type,
        periods=selected_periods,
        latest=latest,
        force=force,
        publish_root=stable_root,
        publish_index_path=resolved_publish_index_path,
    )
    target_label = "latest" if latest else ", ".join(selected_periods)
    typer.echo(
        f"Queued update {job.ticker} job={job.job_id} "
        f"{document_type} {target_label}"
    )
    typer.echo("Added 1 job(s)")


@queue_app.command(
    "retarget-publish",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue retarget-publish --publish-root /data/releases/dev/20260529_010000\n"
        "  krw-ontology queue retarget-publish --dry-run\n\n"
        "By default this rewrites pending jobs only. It is intended for moving old queue jobs "
        "from a legacy stable root onto a new dev release directory."
    ),
)
def queue_retarget_publish_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Queue/running root. Defaults to configured running-root.",
    ),
    publish_root: Optional[Path] = typer.Option(
        None,
        "--publish-root",
        help="New publish root. Defaults to configured publish-root.",
    ),
    publish_index_path: Optional[Path] = typer.Option(
        None,
        "--publish-index-path",
        help="New publish index path. Defaults to configured publish-index-path or <publish-root>/indexes/agent_index.sqlite.",
    ),
    statuses: Optional[list[str]] = typer.Option(
        None,
        "--status",
        help="Job status to retarget. Repeatable. Defaults to pending.",
    ),
    allow_running_queue: bool = typer.Option(
        False,
        "--allow-running-queue",
        help="Allow retargeting while the queue worker is running.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show changes without rewriting job files."),
) -> None:
    """Retarget queued job publish paths to a release directory."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    stable_root = resolve_publish_root(publish_root)
    if stable_root is None:
        typer.echo("Set publish-root or pass --publish-root.")
        raise typer.Exit(1)
    resolved_publish_index_path = resolve_publish_index_path(publish_index_path)
    if resolved_publish_index_path is None:
        resolved_publish_index_path = _default_release_index_path(stable_root)
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
    _exit_if_queue_path_mutates_prod_current(stable_root, "--publish-root")
    _exit_if_queue_path_mutates_prod_current(resolved_publish_index_path, "--publish-index-path")

    wanted_statuses = set(statuses or [PENDING])
    supported_statuses = {PENDING, RUNNING, SUCCEEDED, FAILED, CANCELLED}
    unknown_statuses = sorted(wanted_statuses - supported_statuses)
    if unknown_statuses:
        typer.echo(f"Unknown status value(s): {', '.join(unknown_statuses)}")
        raise typer.Exit(1)

    store = PipelineQueue(output_root)
    store.ensure_dirs()
    if store.worker_is_running() and not allow_running_queue:
        typer.echo("Queue worker is running. Stop it first, or pass --allow-running-queue.")
        raise typer.Exit(1)

    result = _retarget_queue_publish_jobs(
        store=store,
        publish_root=stable_root,
        publish_index_path=resolved_publish_index_path,
        statuses=wanted_statuses,
        dry_run=dry_run,
    )
    action = "would retarget" if dry_run else "retargeted"
    typer.echo(f"Queue publish paths {action}: changed={result['changed']} scanned={result['scanned']}")
    typer.echo(f"publish_root: {stable_root}")
    typer.echo(f"publish_index_path: {resolved_publish_index_path}")


@queue_app.command(
    "run",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue run\n"
        "  krw-ontology queue run --watch\n"
        "  krw-ontology queue run --watch --rebuild-agent-index\n"
        "  krw-ontology queue run --watch --publish-prod\n"
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
    publish_prod: bool = typer.Option(
        False,
        "--publish-prod/--no-publish-prod",
        help=(
            "After the queue batch publish and one agent-index rebuild, upload the stable root "
            "to prod and atomically activate a release."
        ),
    ),
    rebuild_agent_index: bool = typer.Option(
        False,
        "--rebuild-agent-index/--no-rebuild-agent-index",
        help=(
            "Rebuild pending agent indexes after a drained/stopped queue batch. Disabled by "
            "default so queue processing only updates artifacts; use build-agent-index or release "
            "publish explicitly when ready."
        ),
    ),
) -> None:
    """Run queued ticker jobs in the foreground."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
    output_root.mkdir(parents=True, exist_ok=True)
    store = PipelineQueue(output_root)
    store.ensure_dirs()
    from krw_ontology.agent_index import build_agent_index
    effective_rebuild_agent_index = rebuild_agent_index or publish_prod

    try:
        with FileProcessLock(store.worker_lock_path):
            store.clear_stop_request()
            store.write_worker_pid(os.getpid())
            store.write_worker_state(
                os.getpid(),
                mode={
                    "watch": watch,
                    "poll_interval": poll_interval,
                    "max_jobs": max_jobs,
                    "publish_prod": publish_prod,
                    "rebuild_agent_index": effective_rebuild_agent_index,
                },
            )
            typer.echo(f"[{_now_label()}] Queue worker started root={output_root}")
            processed = 0
            pending_rebuild_targets: dict[str, tuple[Path, Path | None]] = {}
            while True:
                if store.stop_requested():
                    _queue_rebuild_pending_indexes(
                        rebuild_targets=pending_rebuild_targets,
                        publish_prod=publish_prod,
                        build_agent_index=build_agent_index,
                    )
                    typer.echo(f"[{_now_label()}] Stop requested; worker exiting")
                    break
                job = store.next_pending_job()
                if job is None:
                    _queue_rebuild_pending_indexes(
                        rebuild_targets=pending_rebuild_targets,
                        publish_prod=publish_prod,
                        build_agent_index=build_agent_index,
                    )
                    if not watch:
                        typer.echo(f"[{_now_label()}] Queue drained")
                        break
                    time.sleep(poll_interval)
                    continue

                rebuild_target = _process_queue_job(
                    store,
                    job,
                    output_root,
                    publish_prod=publish_prod,
                    rebuild_agent_index=effective_rebuild_agent_index,
                )
                if rebuild_target is not None:
                    pending_rebuild_targets[str(rebuild_target[0])] = rebuild_target
                processed += 1
                if max_jobs is not None and processed >= max_jobs:
                    _queue_rebuild_pending_indexes(
                        rebuild_targets=pending_rebuild_targets,
                        publish_prod=publish_prod,
                        build_agent_index=build_agent_index,
                    )
                    typer.echo(f"[{_now_label()}] Reached --max-jobs={max_jobs}")
                    break
    except LockHeldError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    finally:
        store.clear_worker_state(os.getpid())
        store.clear_worker_pid(os.getpid())


@queue_app.command(
    "start",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue start\n"
        "  krw-ontology queue start --poll-interval 5\n"
        "  krw-ontology queue start --rebuild-agent-index\n"
        "  krw-ontology queue start --publish-prod\n\n"
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
    publish_prod: bool = typer.Option(
        False,
        "--publish-prod/--no-publish-prod",
        help=(
            "Start the worker in mode that publishes prod after batch stable publish/index rebuild."
        ),
    ),
    rebuild_agent_index: bool = typer.Option(
        False,
        "--rebuild-agent-index/--no-rebuild-agent-index",
        help=(
            "Start the worker in mode that rebuilds pending agent indexes after each drained "
            "batch. Disabled by default."
        ),
    ),
) -> None:
    """Start a detached background queue worker."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
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
    if publish_prod:
        command.append("--publish-prod")
    if rebuild_agent_index:
        command.append("--rebuild-agent-index")
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
    """Safely stop the worker after the current ticker job finishes."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
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
    """Immediately terminate the background worker process."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
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
    "recover-stale",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue recover-stale\n"
        "  krw-ontology queue recover-stale --dry-run\n"
        "  krw-ontology queue recover-stale --mark-failed --reason \"worker killed\"\n\n"
        "Use this after `queue kill`, a machine reboot, or a crashed worker leaves jobs "
        "stuck in running status. By default it requeues stale running jobs so the next "
        "`queue start` can retry them. It refuses to run while a queue worker appears live "
        "unless --force is passed."
    ),
)
@app.command("queue-recover-stale", hidden=True)
def queue_recover_stale_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "--output-dir",
        help="Staging/running ontology data root.",
    ),
    mark_failed: bool = typer.Option(
        False,
        "--mark-failed/--requeue",
        help="Mark stale running jobs failed instead of requeueing them as pending.",
    ),
    reason: str = typer.Option(
        "stale running job recovered after worker exit",
        "--reason",
        help="Reason recorded in the queue event or failed job error.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--no-dry-run",
        help="Show stale running jobs without changing job files.",
    ),
    force: bool = typer.Option(
        False,
        "--force/--no-force",
        help="Recover even if a worker appears to be running.",
    ),
) -> None:
    """Requeue or fail jobs left running after kill, crash, or reboot."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
    store = PipelineQueue(output_root)
    store.ensure_dirs()

    worker_running = store.worker_is_running()
    if worker_running and not force:
        typer.echo(
            "Queue worker appears to be running; refusing to recover running jobs. "
            "Use `queue stop`, `queue kill`, or pass --force if this is a stale PID/lock."
        )
        raise typer.Exit(1)

    if not worker_running:
        stale_pid = store.worker_pid()
        if stale_pid is not None:
            store.clear_worker_pid(stale_pid)

    running_jobs = store.list_jobs(statuses=[RUNNING])
    if not running_jobs:
        typer.echo("No stale running jobs found.")
        return

    action = "mark failed" if mark_failed else "requeue"
    typer.echo(f"Found {len(running_jobs)} stale running job(s); action={action}")
    for job in running_jobs:
        typer.echo(f"- {job.ticker} job={job.job_id} attempts={job.attempts}")
        if dry_run:
            continue
        if mark_failed:
            store.mark_failed(job, reason)
        else:
            store.mark_pending(job, reason)

    if dry_run:
        typer.echo("Dry run complete; no jobs changed.")
    elif mark_failed:
        typer.echo(f"Marked {len(running_jobs)} stale job(s) failed.")
    else:
        typer.echo(f"Requeued {len(running_jobs)} stale job(s).")


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
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
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


def _queue_job_description(job: QueueJob) -> str:
    if job.job_type == FILING_UPDATE:
        document_type = job.document_type or "10-Q"
        target = "latest" if job.latest else ", ".join(job.periods or [])
        return f"{job.ticker} filing_update {document_type} {target}".rstrip()
    return f"{job.ticker} full_refresh years={job.years}"


def _queue_job_summary(job: QueueJob) -> str:
    return f"{_queue_job_description(job)} attempts={job.attempts} job={job.job_id}"


def _format_ticker_list(jobs: list[QueueJob], *, limit: int) -> str:
    tickers = [job.ticker for job in jobs]
    shown = tickers[:limit]
    suffix = f" (+{len(tickers) - limit} more)" if len(tickers) > limit else ""
    return ", ".join(f"`{ticker}`" for ticker in shown) + suffix if shown else "(none)"


def _queue_failure_group(job: QueueJob) -> str:
    error = job.error or ""
    if job.ticker.endswith(","):
        return "invalid ticker(s) with trailing comma"
    if "not found in SEC company_tickers.json" in error:
        return "ticker not found in SEC company_tickers.json"
    if error:
        return error.splitlines()[0][:120]
    return "unknown failure"


def _show_queue_status_compact(
    *,
    store: PipelineQueue,
    jobs: list[QueueJob],
    counts: dict[str, int],
    worker_state: str,
    pid: int | None,
    limit: int,
) -> None:
    typer.echo(f"QUEUE_ROOT={store.queue_dir}")
    typer.echo(f"Worker: {worker_state}" + (f" pid={pid}" if pid is not None else ""))
    typer.echo(f"Stop requested: {'yes' if store.stop_requested() else 'no'}")
    typer.echo(
        "Jobs: "
        f"pending={counts.get(PENDING, 0)} "
        f"running={counts.get(RUNNING, 0)} "
        f"succeeded={counts.get(SUCCEEDED, 0)} "
        f"failed={counts.get(FAILED, 0)} "
        f"cancelled={counts.get(CANCELLED, 0)}"
    )

    running_jobs = [job for job in jobs if job.status == RUNNING]
    pending_jobs = [job for job in jobs if job.status == PENDING]
    succeeded_jobs = [job for job in jobs if job.status == SUCCEEDED]
    failed_jobs = [job for job in jobs if job.status == FAILED]

    typer.echo("")
    typer.echo("Running:")
    if running_jobs:
        for job in running_jobs:
            typer.echo(f"  {_queue_job_summary(job)}")
    else:
        typer.echo("  (none)")

    typer.echo("Pending:")
    typer.echo(f"  {_format_ticker_list(pending_jobs, limit=limit)}")

    typer.echo("Recent succeeded:")
    typer.echo(f"  {_format_ticker_list(succeeded_jobs[-limit:], limit=limit)}")

    typer.echo("Failed groups:")
    if not failed_jobs:
        typer.echo("  (none)")
        return

    groups: dict[str, list[QueueJob]] = {}
    for job in failed_jobs:
        groups.setdefault(_queue_failure_group(job), []).append(job)
    for label, group_jobs in groups.items():
        typer.echo(f"  {len(group_jobs)} {label}: {_format_ticker_list(group_jobs, limit=limit)}")


@queue_app.command(
    "status",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue status\n"
        "  krw-ontology queue status --compact\n"
        "  krw-ontology queue status --limit 50\n\n"
        "Default mode shows recent jobs in detail. Compact mode groups running, pending, "
        "recent succeeded, and repeated failure causes."
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
    compact: bool = typer.Option(
        False,
        "--compact/--details",
        help="Show a compact operator summary instead of detailed recent jobs.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable queue status for deploy/preflight scripts.",
    ),
) -> None:
    """Show queue worker and job status."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    store = PipelineQueue(output_root)
    store.ensure_dirs()
    worker_state = "running" if store.worker_is_running() else "stopped"
    pid = store.worker_pid()

    jobs = store.list_jobs()
    counts = {PENDING: 0, RUNNING: 0, SUCCEEDED: 0, FAILED: 0, CANCELLED: 0}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1

    if json_output:
        active_jobs = [job for job in jobs if job.status in {PENDING, RUNNING}]
        payload = {
            "root": str(output_root),
            "queue_root": str(store.queue_dir),
            "worker": {
                "state": worker_state,
                "pid": pid,
                "mode": (store.worker_state() or {}).get("mode"),
            },
            "stop_requested": store.stop_requested(),
            "counts": counts,
            "active_publish_roots": sorted(
                {job.publish_root for job in active_jobs if job.publish_root}
            ),
            "active_publish_index_paths": sorted(
                {job.publish_index_path for job in active_jobs if job.publish_index_path}
            ),
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return

    if compact:
        _show_queue_status_compact(
            store=store,
            jobs=jobs,
            counts=counts,
            worker_state=worker_state,
            pid=pid,
            limit=limit,
        )
        return

    typer.echo(f"QUEUE_ROOT={store.queue_dir}")
    typer.echo(f"Worker: {worker_state}" + (f" pid={pid}" if pid is not None else ""))
    typer.echo(f"Stop requested: {'yes' if store.stop_requested() else 'no'}")
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
            f"- {job.status} {_queue_job_description(job)} "
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


def _new_release_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _resolve_prod_settings(
    *,
    host: str | None = None,
    remote_root: str | None = None,
    reload_command: str | None = None,
    health_url: str | None = None,
    keep_releases: int | None = None,
) -> dict[str, str | int | None]:
    config = load_cli_config()
    resolved_host = host or config.prod_host
    resolved_remote_root = remote_root or config.prod_root
    resolved_reload_command = reload_command if reload_command is not None else config.prod_reload_command
    resolved_health_url = health_url if health_url is not None else config.prod_health_url
    keep_value = keep_releases
    if keep_value is None and config.prod_keep_releases:
        try:
            keep_value = int(config.prod_keep_releases)
        except ValueError as exc:
            raise ValueError("prod-keep-releases must be an integer") from exc
    if keep_value is None:
        keep_value = 5
    if keep_value < 1:
        raise ValueError("prod keep releases must be at least 1")
    if not resolved_host:
        raise ValueError("Set prod-host with `krw-ontology prod configure --host ...`.")
    if not resolved_remote_root:
        raise ValueError("Set prod-root with `krw-ontology prod configure --remote-root ...`.")
    return {
        "host": resolved_host,
        "remote_root": resolved_remote_root.rstrip("/"),
        "reload_command": resolved_reload_command,
        "health_url": resolved_health_url,
        "keep_releases": keep_value,
    }


def _bundle_filter(path: Path) -> bool:
    ignored_names = {
        ".DS_Store",
        ".krw_pipeline",
        "releases",
        "incoming",
        "current",
        "current.next",
        "current.rollback",
        "manifest.json",
        "release_manifest.json",
    }
    if path.name in ignored_names:
        return False
    if path.name.endswith(".publish-tmp") or path.name.endswith(".publish-backup"):
        return False
    return True


def _build_prod_release_bundle(stable_root: Path, bundle_path: Path, release_id: str) -> None:
    if not stable_root.exists() or not stable_root.is_dir():
        raise FileNotFoundError(f"Stable publish root not found: {stable_root}")
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_release_manifest(stable_root, release_id=release_id, env="prod", source_root=stable_root)
    with tarfile.open(bundle_path, "w:gz") as archive:
        for child in sorted(stable_root.iterdir()):
            if not _bundle_filter(child):
                continue
            archive.add(child, arcname=child.name, recursive=True)
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        for manifest_name in (RELEASE_MANIFEST_FILENAME, "release_manifest.json"):
            info = tarfile.TarInfo(manifest_name)
            info.size = len(manifest_bytes)
            info.mtime = time.time()
            archive.addfile(info, io.BytesIO(manifest_bytes))


def _run_checked(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{' '.join(command)} failed" + (f": {detail}" if detail else ""))
    return result


def _parse_shell_kv(stdout: str) -> dict[str, str | list[str]]:
    parsed: dict[str, str | list[str]] = {"releases": []}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "release":
            parsed.setdefault("releases", [])
            releases = parsed["releases"]
            if isinstance(releases, list):
                releases.append(value)
            continue
        parsed[key] = value
    return parsed


def _prod_status_script(*, remote_root: str) -> str:
    root_q = shlex.quote(remote_root)
    return f"""set -eu
ROOT={root_q}
printf 'remote_root=%s\\n' "$ROOT"
if [ -L "$ROOT/current" ]; then
  CURRENT_TARGET="$(readlink "$ROOT/current" 2>/dev/null || true)"
  printf 'current_kind=symlink\\n'
  printf 'current_target=%s\\n' "$CURRENT_TARGET"
  case "$CURRENT_TARGET" in
    releases/*) printf 'current_release=%s\\n' "${{CURRENT_TARGET#releases/}}" ;;
    *) printf 'current_release=%s\\n' "$CURRENT_TARGET" ;;
  esac
elif [ -d "$ROOT/current" ]; then
  printf 'current_kind=directory\\n'
  printf 'current_target=%s\\n' "$ROOT/current"
  printf 'current_release=pre-symlink-directory\\n'
elif [ -e "$ROOT/current" ]; then
  printf 'current_kind=other\\n'
  printf 'current_target=%s\\n' "$ROOT/current"
  printf 'current_release=\\n'
else
  printf 'current_kind=missing\\n'
  printf 'current_target=\\n'
  printf 'current_release=\\n'
fi
if [ -f "$ROOT/current/indexes/agent_index.sqlite" ]; then
  printf 'index_present=yes\\n'
else
  printf 'index_present=no\\n'
fi
if [ -f "$ROOT/current/manifest.json" ]; then
  printf 'manifest_present=yes\\n'
  printf 'manifest_file=manifest.json\\n'
elif [ -f "$ROOT/current/release_manifest.json" ]; then
  printf 'manifest_present=yes\\n'
  printf 'manifest_file=release_manifest.json\\n'
else
  printf 'manifest_present=no\\n'
  printf 'manifest_file=\\n'
fi
if [ -d "$ROOT/releases" ]; then
  for release_dir in $(cd "$ROOT/releases" && ls -1dt */ 2>/dev/null || true); do
    printf 'release=%s\\n' "${{release_dir%/}}"
  done
fi
"""


def _prod_doctor_script(*, remote_root: str, need_curl: bool) -> str:
    root_q = shlex.quote(remote_root)
    required = "tar ln mv rm mkdir ls readlink xargs grep" + (" curl" if need_curl else "")
    return f"""set -eu
ROOT={root_q}
REQUIRED={shlex.quote(required)}
MISSING=""
for cmd in $REQUIRED; do
  command -v "$cmd" >/dev/null 2>&1 || MISSING="$MISSING $cmd"
done
printf 'required_commands_missing=%s\\n' "${{MISSING# }}"
if [ -d "$ROOT" ]; then
  printf 'root_state=exists\\n'
  if [ -w "$ROOT" ]; then printf 'root_writable=yes\\n'; else printf 'root_writable=no\\n'; fi
  printf 'parent_writable=unknown\\n'
elif [ -e "$ROOT" ]; then
  printf 'root_state=other\\n'
  printf 'root_writable=no\\n'
  printf 'parent_writable=unknown\\n'
else
  printf 'root_state=missing\\n'
  printf 'root_writable=no\\n'
  PARENT="${{ROOT%/*}}"
  if [ -z "$PARENT" ] || [ "$PARENT" = "$ROOT" ]; then PARENT="."; fi
  if [ -w "$PARENT" ]; then printf 'parent_writable=yes\\n'; else printf 'parent_writable=no\\n'; fi
fi
if [ -L "$ROOT/current" ]; then
  printf 'current_kind=symlink\\n'
  printf 'current_target=%s\\n' "$(readlink "$ROOT/current" 2>/dev/null || true)"
elif [ -d "$ROOT/current" ]; then
  printf 'current_kind=directory\\n'
  printf 'current_target=%s\\n' "$ROOT/current"
elif [ -e "$ROOT/current" ]; then
  printf 'current_kind=other\\n'
  printf 'current_target=%s\\n' "$ROOT/current"
else
  printf 'current_kind=missing\\n'
  printf 'current_target=\\n'
fi
"""


def _get_prod_status(*, host: str, remote_root: str) -> dict[str, str | list[str]]:
    completed = _run_checked(
        ["ssh", host, "sh", "-s"],
        input_text=_prod_status_script(remote_root=remote_root),
    )
    status = _parse_shell_kv(completed.stdout)
    status["host"] = host
    return status


def _try_get_prod_status(
    *,
    host: str | None = None,
    remote_root: str | None = None,
    health_url: str | None = None,
) -> dict[str, str | list[str]]:
    try:
        settings = _resolve_prod_settings(host=host, remote_root=remote_root, health_url=health_url)
        return _get_prod_status(
            host=str(settings["host"]),
            remote_root=str(settings["remote_root"]),
        )
    except Exception as exc:
        return {"error": str(exc), "releases": []}


def _run_prod_doctor_checks(
    *,
    host: str,
    remote_root: str,
    need_curl: bool,
) -> dict[str, str | list[str]]:
    completed = _run_checked(
        ["ssh", host, "sh", "-s"],
        input_text=_prod_doctor_script(remote_root=remote_root, need_curl=need_curl),
    )
    return _parse_shell_kv(completed.stdout)


def _check_prod_health(*, host: str, health_url: str) -> None:
    _run_checked(["ssh", host, "curl", "-fsS", health_url])


def _current_status_label(status: dict[str, str | list[str]] | None) -> str:
    if not status:
        return "<unknown>"
    if status.get("error"):
        return f"<unavailable: {status['error']}>"
    kind = str(status.get("current_kind") or "unknown")
    target = str(status.get("current_target") or "")
    release = str(status.get("current_release") or "")
    if kind == "symlink":
        return target or f"releases/{release}"
    if kind == "directory":
        return "existing current directory (will be preserved on first publish)"
    if kind == "missing":
        return "<missing>"
    return target or f"<{kind}>"


def _print_prod_status(
    status: dict[str, str | list[str]],
    *,
    health_url: str | None = None,
) -> None:
    releases = status.get("releases")
    release_list = releases if isinstance(releases, list) else []
    typer.echo("Prod status")
    typer.echo(f"Host: {status.get('host') or '<unknown>'}")
    typer.echo(f"Remote root: {status.get('remote_root') or '<unknown>'}")
    typer.echo(f"Current: {_current_status_label(status)}")
    typer.echo(f"Current kind: {status.get('current_kind') or '<unknown>'}")
    typer.echo(f"Index: {'present' if status.get('index_present') == 'yes' else 'missing'}")
    typer.echo(f"Release manifest: {'present' if status.get('manifest_present') == 'yes' else 'missing'}")
    if release_list:
        typer.echo("Releases:")
        for release in release_list[:10]:
            marker = " current" if release == status.get("current_release") else ""
            typer.echo(f"  - {release}{marker}")
    else:
        typer.echo("Releases: <none>")
    if health_url:
        typer.echo(f"Health URL: {health_url}")
        typer.echo(f"Health: {status.get('health') or 'not checked'}")


def _print_prod_publish_result(
    result: dict[str, str | int | None],
    *,
    dry_run: bool,
    pre_status: dict[str, str | list[str]] | None,
) -> None:
    release_path = f"releases/{result['release_id']}"
    typer.echo("Prod publish dry run" if dry_run else "Prod publish completed")
    typer.echo(f"Local stable root: {result['stable_root']}")
    typer.echo(f"Host: {result['host']}")
    typer.echo(f"Remote root: {result['remote_root']}")
    typer.echo(f"Current release: {_current_status_label(pre_status)}")
    typer.echo(f"New release: {release_path}")
    typer.echo("Action: upload bundle, extract new release, atomically point current to new release")
    typer.echo(f"Keep releases: {result['keep_releases']}")
    if dry_run:
        typer.echo("No upload performed.")
    else:
        typer.echo(f"Activated: {release_path}")
    typer.echo("Rollback: uv run krw-ontology prod rollback")


def _prod_activation_script(
    *,
    remote_root: str,
    release_id: str,
    reload_command: str | None,
    health_url: str | None,
    keep_releases: int,
) -> str:
    root_q = shlex.quote(remote_root)
    release_q = shlex.quote(release_id)
    reload_q = shlex.quote(reload_command or "")
    health_q = shlex.quote(health_url or "")
    return f"""set -eu
ROOT={root_q}
RELEASE_ID={release_q}
RELOAD_COMMAND={reload_q}
HEALTH_URL={health_q}
KEEP_RELEASES={keep_releases}
BUNDLE="$ROOT/incoming/$RELEASE_ID.tar.gz"
mkdir -p "$ROOT/incoming" "$ROOT/releases"
rm -rf "$ROOT/releases/$RELEASE_ID.tmp" "$ROOT/releases/$RELEASE_ID"
mkdir -p "$ROOT/releases/$RELEASE_ID.tmp"
tar -xzf "$BUNDLE" -C "$ROOT/releases/$RELEASE_ID.tmp"
mv "$ROOT/releases/$RELEASE_ID.tmp" "$ROOT/releases/$RELEASE_ID"
MANIFEST="$ROOT/releases/$RELEASE_ID/manifest.json"
if [ ! -f "$MANIFEST" ]; then MANIFEST="$ROOT/releases/$RELEASE_ID/release_manifest.json"; fi
if [ ! -f "$MANIFEST" ]; then echo "Release manifest missing" >&2; exit 1; fi
if ! grep -q '"env": "prod"' "$MANIFEST"; then echo "Release manifest env is not prod" >&2; exit 1; fi
if ! grep -q '"release_id": "'"$RELEASE_ID"'"' "$MANIFEST"; then echo "Release manifest release_id mismatch" >&2; exit 1; fi
if [ ! -f "$ROOT/releases/$RELEASE_ID/indexes/agent_index.sqlite" ]; then echo "agent_index.sqlite missing" >&2; exit 1; fi
PREV=""
if [ -L "$ROOT/current" ]; then
  PREV="$(readlink "$ROOT/current" 2>/dev/null || true)"
elif [ -e "$ROOT/current" ]; then
  PREV="releases/pre-prod-$RELEASE_ID"
  rm -rf "$ROOT/$PREV"
  mv "$ROOT/current" "$ROOT/$PREV"
fi
rollback() {{
  if [ -n "$PREV" ]; then
    ln -sfn "$PREV" "$ROOT/current.rollback"
    mv -Tf "$ROOT/current.rollback" "$ROOT/current"
    if [ -n "$RELOAD_COMMAND" ]; then sh -c "$RELOAD_COMMAND" >/dev/null 2>&1 || true; fi
  fi
}}
ln -sfn "releases/$RELEASE_ID" "$ROOT/current.next"
mv -Tf "$ROOT/current.next" "$ROOT/current"
if [ -n "$RELOAD_COMMAND" ]; then
  sh -c "$RELOAD_COMMAND" || {{ rollback; exit 1; }}
fi
if [ -n "$HEALTH_URL" ]; then
  curl -fsS "$HEALTH_URL" >/dev/null || {{ rollback; exit 1; }}
fi
rm -f "$BUNDLE"
if [ "$KEEP_RELEASES" -gt 0 ]; then
  cd "$ROOT/releases"
  OLD="$(ls -1dt */ 2>/dev/null | tail -n +"$((KEEP_RELEASES + 1))" || true)"
  if [ -n "$OLD" ]; then printf '%s\\n' "$OLD" | xargs rm -rf; fi
fi
"""


def _prod_rollback_script(
    *,
    remote_root: str,
    release_id: str | None,
    reload_command: str | None,
    health_url: str | None,
) -> str:
    root_q = shlex.quote(remote_root)
    release_q = shlex.quote(release_id or "")
    reload_q = shlex.quote(reload_command or "")
    health_q = shlex.quote(health_url or "")
    return f"""set -eu
ROOT={root_q}
REQUESTED_RELEASE={release_q}
RELOAD_COMMAND={reload_q}
HEALTH_URL={health_q}
mkdir -p "$ROOT/releases"
CURRENT="$(readlink "$ROOT/current" 2>/dev/null || true)"
if [ -n "$REQUESTED_RELEASE" ]; then
  TARGET="releases/$REQUESTED_RELEASE"
else
  TARGET=""
  for candidate in $(cd "$ROOT" && ls -1dt releases/* 2>/dev/null || true); do
    if [ "$candidate" != "$CURRENT" ]; then TARGET="$candidate"; break; fi
  done
fi
if [ -z "$TARGET" ] || [ ! -d "$ROOT/$TARGET" ]; then
  echo "No rollback target found" >&2
  exit 1
fi
ln -sfn "$TARGET" "$ROOT/current.next"
mv -Tf "$ROOT/current.next" "$ROOT/current"
if [ -n "$RELOAD_COMMAND" ]; then sh -c "$RELOAD_COMMAND"; fi
if [ -n "$HEALTH_URL" ]; then curl -fsS "$HEALTH_URL" >/dev/null; fi
printf '%s\\n' "$TARGET"
"""


def _publish_prod_root(
    *,
    stable_root: Path,
    host: str | None = None,
    remote_root: str | None = None,
    reload_command: str | None = None,
    health_url: str | None = None,
    keep_releases: int | None = None,
    dry_run: bool = False,
) -> dict[str, str | int | None]:
    settings = _resolve_prod_settings(
        host=host,
        remote_root=remote_root,
        reload_command=reload_command,
        health_url=health_url,
        keep_releases=keep_releases,
    )
    resolved_root = stable_root.expanduser().resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise FileNotFoundError(f"Stable publish root not found: {resolved_root}")
    release_id = _new_release_id()
    result = {
        "release_id": release_id,
        "stable_root": str(resolved_root),
        "host": settings["host"],
        "remote_root": settings["remote_root"],
        "keep_releases": settings["keep_releases"],
    }
    if dry_run:
        return result
    with tempfile.TemporaryDirectory(prefix="krw-ontology-prod-") as tmp_dir:
        bundle_path = Path(tmp_dir) / f"{release_id}.tar.gz"
        _build_prod_release_bundle(resolved_root, bundle_path, release_id)
        remote_bundle = f"{settings['remote_root']}/incoming/{release_id}.tar.gz"
        _run_checked(
            [
                "ssh",
                str(settings["host"]),
                "mkdir",
                "-p",
                f"{settings['remote_root']}/incoming",
                f"{settings['remote_root']}/releases",
            ]
        )
        _run_checked(["scp", str(bundle_path), f"{settings['host']}:{remote_bundle}"])
        _run_checked(
            ["ssh", str(settings["host"]), "sh", "-s"],
            input_text=_prod_activation_script(
                remote_root=str(settings["remote_root"]),
                release_id=release_id,
                reload_command=str(settings["reload_command"]) if settings["reload_command"] else None,
                health_url=str(settings["health_url"]) if settings["health_url"] else None,
                keep_releases=int(settings["keep_releases"]),
            ),
        )
    return result


def _rollback_prod_release(
    *,
    release_id: str | None,
    host: str | None = None,
    remote_root: str | None = None,
    reload_command: str | None = None,
    health_url: str | None = None,
) -> dict[str, str | None]:
    settings = _resolve_prod_settings(
        host=host,
        remote_root=remote_root,
        reload_command=reload_command,
        health_url=health_url,
        keep_releases=None,
    )
    completed = _run_checked(
        ["ssh", str(settings["host"]), "sh", "-s"],
        input_text=_prod_rollback_script(
            remote_root=str(settings["remote_root"]),
            release_id=release_id,
            reload_command=str(settings["reload_command"]) if settings["reload_command"] else None,
            health_url=str(settings["health_url"]) if settings["health_url"] else None,
        ),
    )
    target = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    activated = target.removeprefix("releases/").rstrip("/")
    return {"release_id": activated, "host": str(settings["host"]), "remote_root": str(settings["remote_root"])}


def _publish_ticker_tree(source_root: Path, stable_root: Path, ticker: str) -> None:
    """Publish one completed ticker tree from source_root into stable_root."""
    ticker = ticker.upper()
    source_dir = source_root / "companies" / ticker
    target_dir = stable_root / "companies" / ticker
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(f"Source ticker directory not found: {source_dir}")
    _assert_company_context_publishable(source_root, ticker)
    _replace_tree(source_dir, target_dir)


def _assert_company_context_publishable(source_root: Path, ticker: str) -> None:
    context_index_path = source_root / "companies" / ticker / "context" / "artifact_index.json"
    if not context_index_path.exists():
        raise RuntimeError(f"Publish blocked for {ticker}: missing company context artifact index")

    artifact_index = json.loads(context_index_path.read_text())
    counts = artifact_index.get("counts") or {}
    required_positive = {
        "company_business_profiles": "company profile was not generated",
        "temporal_links": "cross-period temporal links were not generated",
        "trend_observations": "trend observations were not generated",
        "change_events": "change events were not generated",
        "edges": "company context graph edges were not generated",
    }
    required_zero = {
        "quality_events": "company context quality warnings are present",
        "rejected_objects": "company context rejected objects are present",
    }

    failures: list[str] = []
    for key, reason in required_positive.items():
        value = int(counts.get(key) or 0)
        if value <= 0:
            failures.append(f"{key}={value} ({reason})")
    for key, reason in required_zero.items():
        value = int(counts.get(key) or 0)
        if value > 0:
            failures.append(f"{key}={value} ({reason})")

    if failures:
        detail = "; ".join(failures)
        raise RuntimeError(f"Publish blocked for {ticker}: unhealthy company context counts: {detail}")


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
