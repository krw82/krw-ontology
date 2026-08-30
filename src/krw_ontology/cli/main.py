"""Typer CLI app with 4 command skeleton."""

from __future__ import annotations

import json
import hashlib
import io
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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import typer

from krw_ontology.cli.config import (
    CONFIG_KEYS,
    cli_config_path,
    load_cli_config,
    resolve_publish_root,
    resolve_running_root,
    save_cli_config,
    set_config_value,
    unset_config_value,
)
from krw_ontology.guru.cli import guru_app
from krw_ontology.cli.init_workspace import init_workspace
from krw_ontology.config.paths import ONTOLOGY_ROOT_ENV, resolve_ontology_root
from krw_ontology.observability import (
    DEFAULT_ALERTMANAGER_TEMPLATE_PATH,
    DEFAULT_PROMETHEUS_ALERTS_TEMPLATE_PATH,
    render_alertmanager_config,
    render_prometheus_alerts_config,
    validate_observability_configs,
    write_observability_doctor_report,
)
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
from krw_ontology.agent_index.source_artifact_sqlite import (
    SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
    SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
)
from krw_ontology.agent_index.cache_seal import inherit_immutable_sqlite_cache_seal
from krw_ontology.agent_index.spine_builder import (
    COMPANY_SHARD_SCHEMA_VERSION,
    SPINE_PROJECTION_VERSION,
)
from krw_ontology.agent_index.spine_schema import (
    GLOBAL_SPINE_ALLOWED_SECONDARY_INDEXES,
    GLOBAL_SPINE_BUILDER_VERSION,
    GLOBAL_SPINE_LAYOUT,
    GLOBAL_SPINE_REPLICA_INVARIANT_VERSION,
    GLOBAL_SPINE_REQUIRED_METADATA_KEYS,
    GLOBAL_SPINE_SCHEMA_VERSION,
    GLOBAL_SPINE_TABLES,
    inherit_spine_verification_seal,
    read_spine_verification_sha256,
)
from krw_ontology.agent_index.router_coherence import (
    ROUTER_COHERENCE_BUILDER_VERSION,
    ROUTER_COHERENCE_RELATIVE_PATH,
    ROUTER_COHERENCE_REQUIRED_METADATA_KEYS,
    ROUTER_COHERENCE_SCHEMA_VERSION,
    ROUTER_COHERENCE_TABLES,
    rebind_router_coherence_release,
)
from krw_ontology.agent_index.router_sidecar import (
    ROUTER_SIDECAR_BUILDER_VERSION,
    ROUTER_SIDECAR_RELATIVE_PATH,
    ROUTER_SIDECAR_REQUIRED_METADATA_KEYS,
    ROUTER_SIDECAR_SCHEMA_VERSION,
    ROUTER_SIDECAR_TABLES,
    rebind_router_sidecar_release,
)
from krw_ontology.release import (
    ALLOWED_ONTOLOGY_ENVS,
    FAILED_RELEASE_DIRNAME,
    FAILED_RELEASE_METADATA_FILENAME,
    RELEASE_ENV_RESERVED_DIRNAMES,
    RELEASE_FORMAT_V3,
    RELEASE_MANIFEST_FILENAME,
    current_release_id,
    list_release_ids,
    load_release_manifest,
    normalize_ontology_env,
    promote_local_release,
    quarantine_local_release,
    release_env_root,
    rollback_local_release,
    verify_release_root,
    verify_release_startup_v3,
    write_release_verification_report,
    write_release_manifest_v3,
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
    PENDING as QUALITY_PENDING,
    REPAIR_REFERENCE as QUALITY_REPAIR_REFERENCE,
    RUNNING as QUALITY_RUNNING,
    SECTION_FAIL as QUALITY_SECTION_FAIL,
    SECTION_WARN as QUALITY_SECTION_WARN,
    SUCCEEDED as QUALITY_SUCCEEDED,
    RepairJob,
    RepairPlan,
)
from krw_ontology.quality.queue import QualityRepairStore, default_plan_id
from krw_ontology.quality.scanner import QualityReleaseScanner

app = typer.Typer(
    name="krw-ontology",
    help=(
        "Source-grounded equity research ontology CLI. Use `queue` for daily "
        "ticker research operations and `config` to save default roots."
    ),
    epilog=(
        "Recommended first setup:\n"
        "  krw-ontology config set running-root ~/krw-ontology-data-running\n"
        "  krw-ontology config set publish-root ~/krw-ontology-data/releases\n\n"
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
        "  publish-root        local releases root used for verified release publish\n"
        "  prod-host           SSH host for production data releases\n"
        "  prod-root           production data root containing releases/current\n"
        "  prod-reload-command command run on prod after current release activation\n"
        "  prod-health-url     optional health URL checked on prod after reload\n"
        "  prod-keep-releases  number of prod releases to keep\n\n"
        "Examples:\n"
        "  krw-ontology config set running-root ~/krw-ontology-data-running\n"
        "  krw-ontology config set publish-root ~/krw-ontology-data/releases\n"
        "  krw-ontology config show"
    ),
    no_args_is_help=True,
)
prod_app = typer.Typer(
    name="prod",
    help=(
        "Publish verified ontology data to a production server using versioned releases, "
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
index_app = typer.Typer(
    name="index",
    help=(
        "Plan, build, and verify v3 global spine + company shard outputs. "
        "These commands wrap the v3 cache builder and topology verifier."
    ),
    epilog=(
        "Typical flow:\n"
        "  krw-ontology index plan --root /data/running\n"
        "  krw-ontology release build --from-root /data/running --releases-root /data/releases --env dev\n"
        "  krw-ontology release promote <release-id> --releases-root /data/releases --env dev\n"
        "  krw-ontology index verify --root /data/releases/dev/current\n\n"
        "`index build` is for mutable running roots or non-current candidates. "
        "It refuses active current releases."
    ),
    no_args_is_help=True,
)
index_cache_app = typer.Typer(
    name="cache",
    help=(
        "Inspect and garbage-collect artifact fragment, v3 company shard, "
        "and spine fragment cache files."
    ),
    no_args_is_help=True,
)
source_manifest_app = typer.Typer(
    name="source-manifest",
    help="Generate, verify, and diff canonical artifact source manifests.",
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
        "  krw-ontology release force\n"
        "  krw-ontology release status\n"
        "  krw-ontology release watch\n"
        "  krw-ontology release startup-check --env dev\n"
        "  krw-ontology prod publish-dev\n"
        "  export KRW_ONTOLOGY_RELEASE_ROOT=/data/releases/prod/current"
    ),
    no_args_is_help=True,
)
release_cache_app = typer.Typer(
    name="cache",
    help=(
        "Inspect and garbage-collect release index caches. By default this keeps "
        "cache files referenced by the selected release and deletes nothing unless --yes is passed."
    ),
    no_args_is_help=True,
)
observability_app = typer.Typer(
    name="observability",
    help=("Render and validate deployable monitoring configs for the MCP release serving process."),
    epilog=(
        "Typical flow:\n"
        "  export KRW_PROMETHEUS_HOT_SWAP_RETIRED_AGE_SECONDS=600\n"
        "  krw-ontology observability render-prometheus-alerts "
        "--output /etc/prometheus/rules/krw-ontology-mcp-alerts.yml\n"
        "  export KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL=https://alerts.example/default\n"
        "  export KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL=https://alerts.example/critical\n"
        "  export KRW_ALERTMANAGER_WARNING_WEBHOOK_URL=https://alerts.example/warning\n"
        "  krw-ontology observability render-alertmanager --output /etc/alertmanager/krw-ontology-mcp.yml\n"
        "  krw-ontology observability doctor "
        "--prometheus-alerts /etc/prometheus/rules/krw-ontology-mcp-alerts.yml "
        "--alertmanager /etc/alertmanager/krw-ontology-mcp.yml"
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
observation_app = typer.Typer(
    name="observation",
    help=(
        "Collect and verify the advisory-only observation sidecar "
        "(indexes/observations.sqlite). Collection is a separate step: the "
        "main release build never fetches."
    ),
    epilog=(
        "Typical flow:\n"
        "  krw-ontology observation build --root /data/running --ticker AAPL MSFT\n"
        "  krw-ontology observation verify --path /data/running/indexes/observations.sqlite\n"
        "  krw-ontology release build --from-root /data/running --releases-root /data/releases --env dev"
    ),
    no_args_is_help=True,
)
app.add_typer(queue_app, name="queue")
app.add_typer(config_app, name="config")
app.add_typer(prod_app, name="prod")
index_app.add_typer(index_cache_app, name="cache")
app.add_typer(index_app, name="index")
app.add_typer(source_manifest_app, name="source-manifest")
release_app.add_typer(release_cache_app, name="cache")
app.add_typer(release_app, name="release")
app.add_typer(observability_app, name="observability")
app.add_typer(guru_app, name="guru")
quality_app.add_typer(quality_repair_app, name="repair")
app.add_typer(quality_app, name="quality")
app.add_typer(observation_app, name="observation")

ACCEPTED_DOC_TYPES = {"10-K", "10-Q"}
DEFAULT_E2E_TICKERS = ["AAPL", "NVDA", "JPM", "XOM"]


@observability_app.command("render-alertmanager")
def observability_render_alertmanager_cmd(
    output: Path = typer.Option(
        ...,
        "--output",
        help="Output Alertmanager YAML path to write atomically.",
    ),
    template: Path = typer.Option(
        DEFAULT_ALERTMANAGER_TEMPLATE_PATH,
        "--template",
        help="Alertmanager YAML template path.",
    ),
    default_webhook_url: Optional[str] = typer.Option(
        None,
        "--default-webhook-url",
        help="Default receiver webhook URL. Prefer KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL in production.",
    ),
    critical_webhook_url: Optional[str] = typer.Option(
        None,
        "--critical-webhook-url",
        help="Critical receiver webhook URL. Prefer KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL in production.",
    ),
    warning_webhook_url: Optional[str] = typer.Option(
        None,
        "--warning-webhook-url",
        help="Warning receiver webhook URL. Prefer KRW_ALERTMANAGER_WARNING_WEBHOOK_URL in production.",
    ),
) -> None:
    """Render Alertmanager config with deployment-specific webhook receivers."""
    try:
        result = render_alertmanager_config(
            template,
            output,
            receiver_urls={
                "default": default_webhook_url,
                "critical": critical_webhook_url,
                "warning": warning_webhook_url,
            },
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"FAIL {exc}")
        raise typer.Exit(1) from exc

    typer.echo(f"alertmanager_config: {result['path']}")
    for kind, source in sorted(result["receiver_sources"].items()):
        typer.echo(f"receiver_{kind}: {source}")


@observability_app.command("render-prometheus-alerts")
def observability_render_prometheus_alerts_cmd(
    output: Path = typer.Option(
        ...,
        "--output",
        help="Output Prometheus alert rules YAML path to write atomically.",
    ),
    template: Path = typer.Option(
        DEFAULT_PROMETHEUS_ALERTS_TEMPLATE_PATH,
        "--template",
        help="Prometheus alert rules YAML template path.",
    ),
    mcp_down_for: Optional[str] = typer.Option(
        None,
        "--mcp-down-for",
        help="Duration before KRWOntologyMCPDown fires. Env: KRW_PROMETHEUS_MCP_DOWN_FOR.",
    ),
    global_spine_missing_for: Optional[str] = typer.Option(
        None,
        "--global-spine-missing-for",
        help=(
            "Duration before KRWOntologyMCPGlobalSpineMissing fires. "
            "Env: KRW_PROMETHEUS_GLOBAL_SPINE_MISSING_FOR."
        ),
    ),
    hot_swap_stuck_for: Optional[str] = typer.Option(
        None,
        "--hot-swap-stuck-for",
        help="Duration before KRWOntologyMCPHotSwapStuck fires. Env: KRW_PROMETHEUS_HOT_SWAP_STUCK_FOR.",
    ),
    hot_swap_retired_age_seconds: Optional[int] = typer.Option(
        None,
        "--hot-swap-retired-age-seconds",
        help=(
            "Retired lease age threshold in seconds. "
            "Env: KRW_PROMETHEUS_HOT_SWAP_RETIRED_AGE_SECONDS."
        ),
    ),
    retired_leases_for: Optional[str] = typer.Option(
        None,
        "--retired-leases-for",
        help="Duration before KRWOntologyMCPRetiredLeasesPresent fires. Env: KRW_PROMETHEUS_RETIRED_LEASES_FOR.",
    ),
    rotation_window: Optional[str] = typer.Option(
        None,
        "--rotation-window",
        help="Prometheus range window for store rotation counting. Env: KRW_PROMETHEUS_ROTATION_WINDOW.",
    ),
    rotation_count: Optional[int] = typer.Option(
        None,
        "--rotation-count",
        help="Rotation count threshold. Env: KRW_PROMETHEUS_ROTATION_COUNT.",
    ),
    excessive_rotations_for: Optional[str] = typer.Option(
        None,
        "--excessive-rotations-for",
        help=(
            "Duration before KRWOntologyMCPExcessiveRotations fires. "
            "Env: KRW_PROMETHEUS_EXCESSIVE_ROTATIONS_FOR."
        ),
    ),
    prod_empty_for: Optional[str] = typer.Option(
        None,
        "--prod-empty-for",
        help="Duration before KRWOntologyMCPProdReleaseEmpty fires. Env: KRW_PROMETHEUS_PROD_EMPTY_FOR.",
    ),
) -> None:
    """Render Prometheus alert rules with deployment-specific thresholds."""
    try:
        result = render_prometheus_alerts_config(
            template,
            output,
            overrides={
                "mcp_down_for": mcp_down_for,
                "global_spine_missing_for": global_spine_missing_for,
                "hot_swap_stuck_for": hot_swap_stuck_for,
                "hot_swap_retired_age_seconds": hot_swap_retired_age_seconds,
                "retired_leases_for": retired_leases_for,
                "rotation_window": rotation_window,
                "rotation_count": rotation_count,
                "excessive_rotations_for": excessive_rotations_for,
                "prod_empty_for": prod_empty_for,
            },
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"FAIL {exc}")
        raise typer.Exit(1) from exc

    typer.echo(f"prometheus_alerts: {result['path']}")
    for key, source in sorted(result["threshold_sources"].items()):
        typer.echo(f"threshold_{key}: {result['thresholds'][key]} ({source})")


@observability_app.command("doctor")
def observability_doctor_cmd(
    prometheus_alerts: Path = typer.Option(
        DEFAULT_PROMETHEUS_ALERTS_TEMPLATE_PATH,
        "--prometheus-alerts",
        help="Rendered Prometheus alert rules YAML path.",
    ),
    alertmanager: Path = typer.Option(
        DEFAULT_ALERTMANAGER_TEMPLATE_PATH,
        "--alertmanager",
        help="Rendered Alertmanager YAML path.",
    ),
    env: str = typer.Option(
        "prod",
        "--env",
        help="Deployment environment. Prod rejects localhost/loopback receivers by default.",
    ),
    allow_local_receivers: bool = typer.Option(
        False,
        "--allow-local-receivers",
        help="Allow localhost/loopback Alertmanager webhook URLs.",
    ),
    write_report: Optional[Path] = typer.Option(
        None,
        "--write-report",
        help="Write observability doctor JSON audit report to this path.",
    ),
) -> None:
    """Validate rendered observability configs before deployment."""
    result = validate_observability_configs(
        prometheus_alerts_path=prometheus_alerts,
        alertmanager_path=alertmanager,
        env_name=env,
        allow_local_receivers=allow_local_receivers,
    )
    typer.echo("Observability doctor")
    typer.echo(f"env: {result['env']}")
    typer.echo(f"prometheus_alerts: {result['prometheus_alerts']['path']}")
    typer.echo(f"prometheus_alert_count: {result['prometheus_alerts']['alert_count']}")
    typer.echo(f"alertmanager: {result['alertmanager']['path']}")
    typer.echo(f"alertmanager_receiver_count: {result['alertmanager']['receiver_count']}")
    report = None
    if write_report is not None:
        report = write_observability_doctor_report(write_report, result)
        typer.echo(f"observability_report: {report['path']}")
        typer.echo(f"observability_audit_hash: {report['audit_hash']}")
    if result["errors"]:
        for error in result["errors"]:
            typer.echo(f"FAIL {error}")
        raise typer.Exit(1)
    typer.echo("Observability doctor passed.")


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


def _quality_release_context(
    *,
    release_root: Path | None,
    release: str | None,
    env: str | None,
    releases_root: Path | None,
) -> tuple[str, Path]:
    if release_root is not None:
        root = release_root.expanduser().resolve()
        return str(root), root

    release_env = normalize_ontology_env(env)
    release_id = release or "current"
    if release and "/" in release:
        parts = release.split("/", 1)
        release_env = normalize_ontology_env(parts[0])
        release_id = parts[1] or "current"
    if releases_root is not None:
        root_base = releases_root.expanduser().resolve()
    else:
        configured_publish_root = resolve_publish_root(None)
        if configured_publish_root is not None:
            configured_publish_root = configured_publish_root.expanduser().resolve()
            if (configured_publish_root / RELEASE_MANIFEST_FILENAME).is_file() and release is None:
                root = configured_publish_root
                return str(root), root
            root_base, release_env = _resolve_release_publish_config(
                configured_publish_root,
                release_env,
            )
        else:
            root_base = _default_releases_root().expanduser().resolve()
    root = release_env_root(root_base, release_env) / release_id
    return f"{release_env}/{release_id}", root


def _quality_scanner(
    *,
    release_root: Path | None,
    release: str | None,
    env: str | None,
    releases_root: Path | None,
) -> tuple[QualityReleaseScanner, str, Path]:
    label, root = _quality_release_context(
        release_root=release_root,
        release=release,
        env=env,
        releases_root=releases_root,
    )
    manifest, _manifest_path = load_release_manifest(root)
    release_format = manifest.get("format")
    if release_format != "krw-ontology-release/v3":
        raise ValueError(
            "quality release-root scanning requires a v3 release "
            f"(found {release_format or '<missing>'}: {root})"
        )
    return QualityReleaseScanner(root), label, root


def _quality_plan_fingerprint(scanner: QualityReleaseScanner) -> dict[str, str | None]:
    return scanner.fingerprint()


def _quality_validate_plan_fingerprint(plan: RepairPlan, *, allow_stale_plan: bool = False) -> None:
    if allow_stale_plan:
        return
    if not plan.release_root:
        return
    scanner = QualityReleaseScanner(plan.release_root)
    current = scanner.fingerprint()
    mismatches: list[str] = []
    for key, expected in (
        ("release_id", plan.release_id),
        ("release_format", plan.release_format),
        ("release_manifest_sha256", plan.release_manifest_sha256),
        ("source_manifest_sha256", plan.source_manifest_sha256),
        ("global_spine_sha256", plan.global_spine_sha256),
        ("shard_manifest_sha256", plan.shard_manifest_sha256),
    ):
        if not expected:
            continue
        actual = current.get(key)
        if actual != expected:
            mismatches.append(f"{key}:{expected}!={actual}")
    if mismatches:
        raise RuntimeError(
            "quality repair plan is stale for its release root; regenerate the plan "
            "or pass --allow-stale-plan if this is intentional: " + ", ".join(mismatches)
        )


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
            return str(job.payload.get("repair_outcome") or "deferred_kind_executed")
        return "deferred"
    if job.kind == QUALITY_COVERAGE_GAP:
        return "needs_manual_review"
    if job.status == QUALITY_PENDING:
        return "pending"
    outcome = job.payload.get("repair_outcome")
    if outcome:
        return str(outcome)
    resolved = int(
        job.payload.get("resolved_count") or job.payload.get("candidate_now_valid_count") or 0
    )
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
        resolved = int(
            job.payload.get("resolved_count") or job.payload.get("candidate_now_valid_count") or 0
        )
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
            reason = str(
                item.get("current_reason")
                or item.get("reason")
                or item.get("original_reason")
                or ""
            ).strip()
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
    _assert_path_not_current_release(output_root, "--root")
    store = QualityRepairStore(output_root)
    store.ensure_dirs()
    return store


@quality_app.command("check")
def quality_check_cmd(
    env: Optional[str] = typer.Option(
        None, "--env", help="Release environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release: Optional[str] = typer.Option(
        None,
        "--release",
        help="Release id or env/release id. Defaults to <env>/current.",
    ),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(
        None, "--release-root", help="Explicit release root."
    ),
    min_docs: int = typer.Option(
        5, "--min-docs", min=1, help="Minimum expected documents per ticker."
    ),
    sample_limit: int = typer.Option(
        20, "--sample-limit", min=1, help="Maximum consistency samples per check."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Run a full v3 release quality diagnostic."""
    try:
        scanner, label, root = _quality_scanner(
            release_root=release_root,
            release=release,
            env=env,
            releases_root=releases_root,
        )
        report = scanner.scan(
            min_docs=min_docs,
            mode="full",
            sample_limit=sample_limit,
        )
    except Exception as exc:
        typer.echo(f"FAILED quality check: {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        _echo_json({"release": label, "release_root": str(root) if root else None, **report})
        return

    totals = report["totals"]
    scan = report.get("scan") if isinstance(report.get("scan"), Mapping) else {}
    shards = report.get("shards") if isinstance(report.get("shards"), Mapping) else {}
    kind_counts = report["kind_counts"]
    severity_counts = report["severity_counts"]
    metric_gap = report.get("metric_gap") if isinstance(report.get("metric_gap"), Mapping) else {}
    typer.echo(f"Release: {label}")
    typer.echo(f"Global spine: {report['global_spine_path']}")
    typer.echo(
        "Scan: "
        f"mode={scan.get('mode', '<unknown>')} "
        f"rollup={scan.get('rollup_source', '<unknown>')} "
        f"opened_shards={scan.get('opened_shards', 0)} "
        f"full_consistency={scan.get('full_consistency', False)}"
    )
    typer.echo(
        "Shards: "
        f"declared={shards.get('declared_count', 0)} "
        f"available={shards.get('available_count', 0)} "
        f"missing={shards.get('missing_count', 0)}"
    )
    metric_gap_summary = (
        metric_gap.get("summary") if isinstance(metric_gap.get("summary"), Mapping) else {}
    )
    if metric_gap_summary:
        typer.echo(
            "Metric gap: "
            f"eligible={metric_gap_summary.get('eligible', 0)} "
            f"present={metric_gap_summary.get('present', 0)} "
            f"blocked={metric_gap_summary.get('blocked', 0)}"
        )
    for missing_shard in list(shards.get("missing") or [])[:10]:
        if isinstance(missing_shard, Mapping):
            typer.echo(
                f"- missing shard {missing_shard.get('ticker')}: {missing_shard.get('path')}"
            )
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
        f"coverage_gap={kind_counts.get(QUALITY_COVERAGE_GAP, 0)} "
        f"release_consistency={kind_counts.get('release_consistency', 0)}"
    )
    consistency = report.get("consistency")
    if isinstance(consistency, dict):
        typer.echo("Consistency: " + ("pass" if consistency.get("ok") else "fail"))
        for error in list(consistency.get("errors") or [])[:10]:
            typer.echo(f"- consistency {error}")


@quality_app.command("summary", hidden=True)
def quality_summary_cmd(
    env: Optional[str] = typer.Option(None, "--env"),
    release: Optional[str] = typer.Option(None, "--release"),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root"),
    release_root: Optional[Path] = typer.Option(None, "--release-root"),
    min_docs: int = typer.Option(5, "--min-docs", min=1),
    sample_limit: int = typer.Option(20, "--sample-limit", min=1),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Backward-compatible alias for `quality check`."""
    quality_check_cmd(
        env=env,
        release=release,
        releases_root=releases_root,
        release_root=release_root,
        min_docs=min_docs,
        sample_limit=sample_limit,
        json_output=json_output,
    )


@quality_app.command("tickers")
def quality_tickers_cmd(
    env: Optional[str] = typer.Option(
        None, "--env", help="Release environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release: Optional[str] = typer.Option(None, "--release", help="Release id or env/release id."),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(
        None, "--release-root", help="Explicit release root."
    ),
    min_docs: int = typer.Option(5, "--min-docs", min=1),
    severity: Optional[str] = typer.Option(
        None, "--severity", help="Filter by high, medium, low, ok."
    ),
    kind: Optional[str] = typer.Option(None, "--kind", help="Filter by issue kind."),
    bad_only: bool = typer.Option(True, "--bad/--all", help="Show only problematic tickers."),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, help="Maximum tickers to show."),
    full: bool = typer.Option(
        False,
        "--full/--bounded",
        help="Open every shard. Default bounded uses shard manifest rollups.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List ticker-level quality status."""
    try:
        scanner, label, _root = _quality_scanner(
            release_root=release_root,
            release=release,
            env=env,
            releases_root=releases_root,
        )
        mode = "full" if full else "bounded"
        tickers = scanner.ticker_quality(min_docs=min_docs, mode=mode)
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
        _echo_json({"release": label, "mode": mode, "tickers": rows, "count": len(rows)})
        return

    typer.echo(f"Release: {label}")
    typer.echo(f"Scan: mode={mode}")
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
    env: Optional[str] = typer.Option(
        None, "--env", help="Release environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release: Optional[str] = typer.Option(None, "--release", help="Release id or env/release id."),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(
        None, "--release-root", help="Explicit release root."
    ),
    min_docs: int = typer.Option(5, "--min-docs", min=1),
    limit: int = typer.Option(20, "--limit", min=1),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Explain why a ticker is flagged."""
    try:
        scanner, label, _root = _quality_scanner(
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
        typer.echo(
            f"- {doc['document_type']} {doc['period']} section={status} missing={missing} reasons={reasons}"
        )
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
    env: Optional[str] = typer.Option(
        None, "--env", help="Release environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release: Optional[str] = typer.Option(None, "--release", help="Release id or env/release id."),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(
        None, "--release-root", help="Explicit release root."
    ),
    limit: int = typer.Option(50, "--limit", min=1),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List quality events."""
    try:
        scanner, label, _root = _quality_scanner(
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
    env: Optional[str] = typer.Option(
        None, "--env", help="Release environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release: Optional[str] = typer.Option(None, "--release", help="Release id or env/release id."),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(
        None, "--release-root", help="Explicit release root."
    ),
    min_docs: int = typer.Option(5, "--min-docs", min=1),
    max_docs_missing: int = typer.Option(0, "--max-docs-missing", min=0),
    max_section_fail: int = typer.Option(0, "--max-section-fail", min=0),
    max_batch_failure: int = typer.Option(0, "--max-batch-failure", min=0),
    max_coverage_gap: int = typer.Option(
        0,
        "--max-coverage-gap",
        min=0,
        help="Deprecated; coverage_gap is advisory and does not fail the gate.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Fail if release quality exceeds operator thresholds."""
    try:
        scanner, label, _root = _quality_scanner(
            release_root=release_root,
            release=release,
            env=env,
            releases_root=releases_root,
        )
        report = scanner.scan(min_docs=min_docs, mode="full")
    except Exception as exc:
        typer.echo(f"FAILED quality gate: {exc}")
        raise typer.Exit(1) from exc

    kind_counts = report["kind_counts"]
    failures = []
    thresholds = {
        QUALITY_DOCS_MISSING: max_docs_missing,
        QUALITY_SECTION_FAIL: max_section_fail,
        QUALITY_BATCH_FAILURE: max_batch_failure,
    }
    for kind_name, maximum in thresholds.items():
        actual = int(kind_counts.get(kind_name, 0))
        if actual > maximum:
            failures.append({"kind": kind_name, "actual": actual, "maximum": maximum})

    payload = {
        "release": label,
        "ok": not failures,
        "failures": failures,
        "kind_counts": kind_counts,
    }
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
    root: Optional[Path] = typer.Option(
        None, "--root", help="Running root for quality repair state."
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Release environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release: Optional[str] = typer.Option(None, "--release", help="Release id or env/release id."),
    releases_root: Optional[Path] = typer.Option(None, "--releases-root", help="Releases root."),
    release_root: Optional[Path] = typer.Option(
        None, "--release-root", help="Explicit release root."
    ),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    kind: Optional[list[str]] = typer.Option(None, "--kind", help="Only plan this repair kind."),
    include_deferred: bool = typer.Option(
        False,
        "--include-deferred",
        help="Include deferred repair kinds such as normalize_numeric. Deferred jobs are not run by default.",
    ),
    include_warn: bool = typer.Option(
        False, "--include-warn", help="Include section_warn repairs."
    ),
    min_docs: int = typer.Option(5, "--min-docs", min=1),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing plan id."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    details: bool = typer.Option(
        False,
        "--details",
        help="Show representative jobs with ticker, document, stage, and reason.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        min=1,
        help="Maximum representative jobs to show per repair kind with --details.",
    ),
) -> None:
    """Create a reviewed repair plan from quality events."""
    try:
        scanner, label, _release_root = _quality_scanner(
            release_root=release_root,
            release=release,
            env=env,
            releases_root=releases_root,
        )
        resolved_plan_id = plan_id or default_plan_id()
        store = _quality_repair_store(root)
        jobs = scanner.build_repair_jobs(
            plan_id=resolved_plan_id,
            min_docs=min_docs,
            kinds=kind,
            include_warn=include_warn,
            running_root=store.root,
        )
        deferred_jobs = [job for job in jobs if job.kind in QUALITY_DEFERRED_REPAIR_KINDS]
        if not include_deferred:
            jobs = [job for job in jobs if job.kind not in QUALITY_DEFERRED_REPAIR_KINDS]
        fingerprint = _quality_plan_fingerprint(scanner)
        plan = RepairPlan(
            plan_id=resolved_plan_id,
            global_spine_path=str(scanner.global_spine_path),
            release_label=label,
            min_docs=min_docs,
            job_ids=[job.job_id for job in jobs],
            summary=dict(Counter(job.kind for job in jobs)),
            release_root=fingerprint.get("release_root"),
            release_id=fingerprint.get("release_id"),
            release_format=fingerprint.get("release_format"),
            release_manifest_sha256=fingerprint.get("release_manifest_sha256"),
            source_manifest_sha256=fingerprint.get("source_manifest_sha256"),
            global_spine_sha256=fingerprint.get("global_spine_sha256"),
            shard_manifest_sha256=fingerprint.get("shard_manifest_sha256"),
        )
        plan = store.add_plan(plan, jobs, force=force)
    except Exception as exc:
        typer.echo(f"FAILED quality repair plan: {exc}")
        raise typer.Exit(1) from exc

    planned_job_ids = set(plan.job_ids)
    planned_jobs = [job for job in jobs if job.job_id in planned_job_ids]
    job_details = _quality_plan_job_details(planned_jobs)

    if json_output:
        _echo_json(
            {
                "plan": plan.to_dict(),
                "queue_root": str(store.queue_dir),
                "job_details": _quality_plan_job_details_json(job_details, limit=limit),
                "deferred_excluded": dict(Counter(job.kind for job in deferred_jobs))
                if not include_deferred
                else {},
            }
        )
        return
    typer.echo(f"Repair plan created: {plan.plan_id}")
    typer.echo(f"Release: {plan.release_label}")
    typer.echo(f"Queue: {store.queue_dir}")
    typer.echo(f"Jobs: {len(plan.job_ids)}")
    for detail in job_details:
        parts = [
            f"- {detail['kind']}: {detail['jobs']}",
            f"tickers={detail['tickers']}",
            f"documents={detail['documents']}",
        ]
        if detail["stages"]:
            parts.append("stages=" + ", ".join(detail["stages"]))
        if detail["executors"]:
            parts.append("executor=" + ", ".join(detail["executors"]))
        typer.echo("; ".join(parts))
        if details:
            for job in detail["samples"][:limit]:
                reason = str(job.reason or "").replace("\n", " ")[:180]
                suffix = f" reason={reason}" if reason else ""
                typer.echo(f"  sample: {_quality_job_description(job)}{suffix}")
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


def _quality_plan_job_details(jobs: list[RepairJob]) -> list[dict[str, Any]]:
    """Build a concise, human-readable plan summary without rescanning data."""
    grouped: dict[str, list[RepairJob]] = {}
    for job in jobs:
        grouped.setdefault(job.kind, []).append(job)

    details: list[dict[str, Any]] = []
    for kind, kind_jobs in sorted(grouped.items()):
        documents = {
            (
                str(job.ticker or "").upper(),
                str(job.doc_type_key or job.document_type or ""),
                str(job.period or ""),
            )
            for job in kind_jobs
        }
        stages = sorted({str(job.stage) for job in kind_jobs if job.stage})
        executors = sorted(
            {
                str(
                    job.payload.get("repair_strategy")
                    or job.payload.get("action")
                    or _quality_plan_default_executor(job.kind)
                )
                for job in kind_jobs
            }
        )
        details.append(
            {
                "kind": kind,
                "jobs": len(kind_jobs),
                "tickers": len({str(job.ticker or "").upper() for job in kind_jobs}),
                "documents": len(documents),
                "stages": stages,
                "executors": executors,
                "samples": kind_jobs,
            }
        )
    return details


def _quality_plan_job_details_json(
    details: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Serialize plan details without exposing in-memory RepairJob instances."""
    serialized: list[dict[str, Any]] = []
    for detail in details:
        serialized_detail = {key: value for key, value in detail.items() if key != "samples"}
        serialized_detail["samples"] = [
            {
                "job_id": job.job_id,
                "status": job.status,
                "ticker": job.ticker,
                "document_type": job.document_type,
                "doc_type_key": job.doc_type_key,
                "period": job.period,
                "stage": job.stage,
                "reason": job.reason,
                "count": job.count,
                "executor": (
                    job.payload.get("repair_strategy")
                    or job.payload.get("action")
                    or _quality_plan_default_executor(job.kind)
                ),
            }
            for job in detail["samples"][:limit]
        ]
        serialized.append(serialized_detail)
    return serialized


def _quality_plan_default_executor(kind: str) -> str:
    return {
        QUALITY_BATCH_FAILURE: "document_clean_rerun",
        QUALITY_DOCS_MISSING: "pipeline_queue",
        "repair_reference": "deterministic_reference_rebuild",
        "direct_xbrl_metric_gap": "staged_direct_xbrl_metric_addition",
        "normalize_numeric": "report_only_numeric_revalidation",
    }.get(kind, "manual_review")


@quality_repair_app.command("show")
def quality_repair_show_cmd(
    root: Optional[Path] = typer.Option(
        None, "--root", help="Running root for quality repair state."
    ),
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
    typer.echo(f"Global spine: {plan.global_spine_path}")
    if plan.release_root:
        typer.echo(f"Release root: {plan.release_root}")
        typer.echo(f"Release id: {plan.release_id or '<unknown>'}")
        typer.echo(f"Manifest sha256: {plan.release_manifest_sha256 or '<unknown>'}")
        typer.echo(f"Global spine sha256: {plan.global_spine_sha256 or '<unknown>'}")
        typer.echo(f"Shard manifest sha256: {plan.shard_manifest_sha256 or '<unknown>'}")
    typer.echo(f"Jobs: {len(jobs)}")
    for repair_kind, count in sorted(plan.summary.items()):
        typer.echo(f"- {repair_kind}: {count}")
    for job in jobs[:limit]:
        typer.echo(f"- {job.status} {_quality_job_description(job)} job={job.job_id}")


@quality_repair_app.command("status")
def quality_repair_status_cmd(
    root: Optional[Path] = typer.Option(
        None, "--root", help="Running root for quality repair state."
    ),
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
        repair_summary = (
            _quality_repair_summary(store, selected_plan_id) if selected_plan_id else {}
        )
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
    root: Optional[Path] = typer.Option(
        None, "--root", help="Running root for quality repair state."
    ),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    interval: float = typer.Option(5.0, "--interval", min=1.0, help="Seconds between refreshes."),
    once: bool = typer.Option(False, "--once", help="Print one snapshot and exit."),
) -> None:
    """Watch quality repair status."""
    while True:
        try:
            store = _quality_repair_store(root)
            plan = (
                _quality_selected_plan(store, plan_id) if plan_id or store.latest_plan() else None
            )
            selected_plan_id = plan.plan_id if plan else None
            status_counts = store.status_counts(plan_id=selected_plan_id)
            kind_counts = store.kind_counts(plan_id=selected_plan_id)
            repair_summary = (
                _quality_repair_summary(store, selected_plan_id) if selected_plan_id else {}
            )
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
    root: Optional[Path] = typer.Option(
        None, "--root", help="Running root for quality repair state."
    ),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    kind: Optional[str] = typer.Option(None, "--kind", help="Filter by repair kind."),
    ticker: Optional[str] = typer.Option(None, "--ticker", help="Filter by ticker."),
    reasons_limit: int = typer.Option(
        10, "--reasons-limit", min=0, help="Top unresolved reasons to show."
    ),
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
            payload["resolved_candidates"] += int(
                job.payload.get("resolved_count")
                or job.payload.get("candidate_now_valid_count")
                or 0
            )
            payload["unresolved_candidates"] += int(job.payload.get("unresolved_count") or 0)
            payload["pruned_reference_objects"] += int(
                job.payload.get("pruned_reference_object_count") or 0
            )
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
                "resolved_candidates": sum(
                    v["resolved_candidates"] for v in filtered_by_kind.values()
                ),
                "unresolved_candidates": sum(
                    v["unresolved_candidates"] for v in filtered_by_kind.values()
                ),
                "pruned_reference_objects": sum(
                    v["pruned_reference_objects"] for v in filtered_by_kind.values()
                ),
                "enqueued_pipeline_jobs": sum(
                    v["enqueued_pipeline_jobs"] for v in filtered_by_kind.values()
                ),
                "active_pipeline_jobs": sum(
                    v["active_pipeline_jobs"] for v in filtered_by_kind.values()
                ),
                "deferred_jobs": sum(v["deferred_jobs"] for v in filtered_by_kind.values()),
                "manual_review_jobs": sum(
                    v["manual_review_jobs"] for v in filtered_by_kind.values()
                ),
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
    root: Optional[Path] = typer.Option(
        None, "--root", help="Running root for quality repair state."
    ),
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
    root: Optional[Path] = typer.Option(
        None, "--root", help="Running root for quality repair state."
    ),
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


@quality_repair_app.command("stop")
def quality_repair_stop_cmd(
    root: Optional[Path] = typer.Option(
        None, "--root", help="Running root for quality repair state."
    ),
    plan_id: Optional[str] = typer.Option(None, "--plan", "--plan-id", help="Repair plan id."),
    timeout_seconds: int = typer.Option(
        10, "--timeout", min=1, help="Seconds to wait before SIGKILL."
    ),
    requeue_running: bool = typer.Option(
        True,
        "--requeue-running/--keep-running-state",
        help="Move interrupted running jobs back to pending so the plan can resume.",
    ),
) -> None:
    """Stop the quality repair background worker and clean interrupted state."""
    store = _quality_repair_store(root)
    pid = store.worker_pid()
    stopped = False
    if pid is not None and is_pid_running(pid):
        _terminate_process_group(pid, timeout_seconds=timeout_seconds)
        stopped = True
        typer.echo(f"Stopped quality repair worker pid={pid}")
    else:
        typer.echo("Quality repair worker is not running.")
    store.clear_worker_state(pid)
    store.clear_worker_pid(pid)

    requeued = 0
    if requeue_running:
        plan = _quality_selected_plan(store, plan_id)
        for job in store.list_jobs(plan_id=plan.plan_id, statuses=[QUALITY_RUNNING]):
            store.mark_pending(job, "interrupted_by_quality_repair_stop")
            requeued += 1
        typer.echo(f"Requeued interrupted running jobs: {requeued}")
    if not stopped and not requeued:
        raise typer.Exit(1)


def _terminate_process_group(pid: int, *, timeout_seconds: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not is_pid_running(pid):
            return
        time.sleep(0.25)
    if not is_pid_running(pid):
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return


@quality_repair_app.command("log")
def quality_repair_log_cmd(
    root: Optional[Path] = typer.Option(
        None, "--root", help="Running root for quality repair state."
    ),
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
    root: Optional[Path] = typer.Option(
        None, "--root", help="Running root for quality repair state."
    ),
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
        help="Override Agent SDK batch concurrency for batch_failure repair. Defaults to 1.",
    ),
    allow_stale_plan: bool = typer.Option(
        False,
        "--allow-stale-plan",
        help="Allow running a repair plan whose v3 release fingerprint no longer matches.",
    ),
    preview: bool = typer.Option(False, "--preview", help="Preview selected jobs without running."),
    foreground: bool = typer.Option(
        False, "--foreground", help="Run in the foreground instead of starting a background worker."
    ),
    yes: bool = typer.Option(
        True,
        "--yes/--no-yes",
        help="Confirm execution. Defaults to yes; use --preview for dry run.",
    ),
) -> None:
    """Run pending repair jobs. This can invoke Agent SDK calls."""
    try:
        store = _quality_repair_store(root)
        plan = _quality_selected_plan(store, plan_id)
        _quality_validate_plan_fingerprint(plan, allow_stale_plan=allow_stale_plan)
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
        dispatch_jobs = [
            job for job in jobs if job.kind in {QUALITY_BATCH_FAILURE, QUALITY_DOCS_MISSING}
        ]
        worker_jobs = [job for job in jobs if job.kind == QUALITY_REPAIR_REFERENCE]
        worker_job_ids = {job.job_id for job in worker_jobs}
        other_jobs = [
            job
            for job in jobs
            if job.kind
            not in {QUALITY_BATCH_FAILURE, QUALITY_DOCS_MISSING, QUALITY_REPAIR_REFERENCE}
        ]
        typer.echo(f"Repair plan: {plan.plan_id}")
        typer.echo(f"Selected jobs: {len(jobs)}")
        typer.echo(f"Dispatch to pipeline queue: {len(dispatch_jobs)}")
        typer.echo(f"Background reference jobs: {len(worker_jobs)}")
        if other_jobs:
            typer.echo(f"Other foreground jobs: {len(other_jobs)}")
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
        from krw_ontology.quality.runner import run_repair_jobs

        dispatch_jobs = [
            job for job in jobs if job.kind in {QUALITY_BATCH_FAILURE, QUALITY_DOCS_MISSING}
        ]
        worker_jobs = [job for job in jobs if job.kind == QUALITY_REPAIR_REFERENCE]
        other_jobs = [
            job
            for job in jobs
            if job.kind
            not in {QUALITY_BATCH_FAILURE, QUALITY_DOCS_MISSING, QUALITY_REPAIR_REFERENCE}
        ]
        dispatch_result = {
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "resolved": 0,
            "unresolved": 0,
            "enqueued": 0,
            "active": 0,
        }
        if dispatch_jobs:
            with FileProcessLock(store.worker_lock_path):
                dispatch_result = run_repair_jobs(
                    store=store,
                    jobs=dispatch_jobs,
                    root=store.root,
                    concurrency=concurrency,
                )

        foreground_result = {
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "resolved": 0,
            "unresolved": 0,
            "enqueued": 0,
            "active": 0,
        }
        if other_jobs:
            with FileProcessLock(store.worker_lock_path):
                foreground_result = run_repair_jobs(
                    store=store,
                    jobs=other_jobs,
                    root=store.root,
                    concurrency=concurrency,
                )

        pending_reference_jobs = [
            job
            for job in store.list_jobs(plan_id=plan.plan_id, statuses=[QUALITY_PENDING])
            if job.kind == QUALITY_REPAIR_REFERENCE
        ]
        worker_jobs = (
            pending_reference_jobs
            if all_jobs
            else [job for job in pending_reference_jobs if job.job_id in worker_job_ids]
        )

        typer.echo(f"Repair plan: {plan.plan_id}")
        typer.echo(
            "Pipeline dispatch complete: "
            f"jobs={len(dispatch_jobs)} "
            f"succeeded={dispatch_result['succeeded']} "
            f"failed={dispatch_result['failed']} "
            f"skipped={dispatch_result['skipped']} "
            f"enqueued={dispatch_result.get('enqueued', 0)} "
            f"active={dispatch_result.get('active', 0)}"
        )
        if other_jobs:
            typer.echo(
                "Foreground repair complete: "
                f"jobs={len(other_jobs)} "
                f"succeeded={foreground_result['succeeded']} "
                f"failed={foreground_result['failed']} "
                f"skipped={foreground_result['skipped']}"
            )

        if not worker_jobs:
            typer.echo("No pending repair_reference jobs selected.")
            typer.echo("Next: krw-ontology queue status")
            return

        command = [
            sys.executable,
            "-c",
            "from krw_ontology.cli.main import app; app()",
            "quality-repair-worker",
            "--root",
            str(store.root),
            "--plan",
            plan.plan_id,
            "--kind",
            QUALITY_REPAIR_REFERENCE,
        ]
        if all_jobs:
            command.append("--all")
        else:
            command.extend(["--limit", str(len(worker_jobs))])
        if concurrency is not None:
            command.extend(["--concurrency", str(concurrency)])
        if allow_stale_plan:
            command.append("--allow-stale-plan")
        store.ensure_dirs()
        with store.worker_log_path.open("a", encoding="utf-8") as log_handle:
            log_handle.write(f"\n[{_now_label()}] quality repair launching reference worker\n")
            log_handle.flush()
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        typer.echo(f"Started quality repair reference worker pid={process.pid}")
        typer.echo(f"reference_jobs: {len(worker_jobs)}")
        typer.echo(f"log: {store.worker_log_path}")
        typer.echo(f"watch: krw-ontology quality repair watch --plan {plan.plan_id}")
        typer.echo("queue: krw-ontology queue status")
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
                    "concurrency": concurrency or 1,
                    "allow_stale_plan": allow_stale_plan,
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
    allow_stale_plan: bool = typer.Option(False, "--allow-stale-plan"),
) -> None:
    """Internal background worker for quality repair runs."""
    from krw_ontology.quality.runner import run_repair_jobs

    store = _quality_repair_store(root)
    plan = _quality_selected_plan(store, plan_id)
    _quality_validate_plan_fingerprint(plan, allow_stale_plan=allow_stale_plan)
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
                    "concurrency": concurrency or 1,
                    "allow_stale_plan": allow_stale_plan,
                    "foreground": False,
                },
            )
            typer.echo(f"[{_now_label()}] Quality repair worker started plan={plan.plan_id}")
            if skipped_count:
                typer.echo(
                    f"Skipped {skipped_count} pending jobs that are deferred or lack executors."
                )
            typer.echo(f"Selected jobs: {len(jobs)}")
            typer.echo(f"batch_failure_concurrency: {concurrency or 1}")
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
    typer.echo(f"prod-host: {config.prod_host or '<unset>'}")
    typer.echo(f"prod-root: {config.prod_root or '<unset>'}")
    typer.echo(f"prod-reload-command: {config.prod_reload_command or '<unset>'}")
    typer.echo(f"prod-health-url: {config.prod_health_url or '<unset>'}")
    typer.echo(f"prod-keep-releases: {config.prod_keep_releases or '<unset>'}")
    typer.echo(f"release-keep-releases: {config.release_keep_releases or '<unset>'}")
    typer.echo(f"release-auto-gc: {config.release_auto_gc or '<unset>'}")


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
    shown_value = (
        str(Path(value).expanduser().resolve())
        if key in {"running-root", "publish-root"}
        else value
    )
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
        help="Local verified release root to upload. Defaults to configured publish-root current release.",
    ),
    from_env: Optional[str] = typer.Option(
        None,
        "--from-env",
        help="Local release env to upload when publish-root points at a releases root. Defaults to KRW_ONTOLOGY_ENV or dev.",
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
    stable_root = _resolve_local_release_upload_root(root, env=from_env)
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
            _assert_prod_publish_source_v3(stable_root)
            typer.echo("OK local release format: v3 global-spine-and-company-shards")
        except Exception as exc:
            failures.append(f"Local release preflight failed: {exc}")

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
            typer.echo("OK remote publish commands: tar ln mv rm mkdir ls readlink xargs grep sed")
        if doctor.get("python_sqlite3") == "yes":
            typer.echo(f"OK remote Python sqlite3: {doctor.get('python_bin') or '<unknown>'}")
        else:
            failures.append(
                "Remote Python sqlite3 unavailable. Install python3 with sqlite3 support "
                "for prod publish/rollback SQLite verification."
            )
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
        warnings.append(
            "No prod-health-url configured; publish will rely on reload-command success."
        )

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
        help="Local verified release root to upload. Defaults to configured publish-root current release.",
    ),
    from_env: Optional[str] = typer.Option(
        None,
        "--from-env",
        help="Local release env to upload when publish-root points at a releases root. Defaults to KRW_ONTOLOGY_ENV or dev.",
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
    delta: bool = typer.Option(
        False,
        "--delta",
        help="Upload only files changed from remote current and activate via a reconstructed release.",
    ),
) -> None:
    """Publish the local verified ontology root to production as a versioned release."""
    stable_root = _resolve_local_release_upload_root(root, env=from_env)
    if stable_root is None:
        typer.echo("Set publish-root or pass --root before publishing to prod.")
        raise typer.Exit(1)
    try:
        _assert_prod_publish_source_v3(stable_root)
    except Exception as exc:
        typer.echo(f"FAILED prod publish preflight: {exc}")
        raise typer.Exit(1) from exc
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
            delta=delta,
        )
    except Exception as exc:
        typer.echo(f"FAILED prod publish: {exc}")
        raise typer.Exit(1) from exc
    _print_prod_publish_result(result, dry_run=dry_run, pre_status=pre_status)


@prod_app.command("publish-dev")
def prod_publish_dev_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Local dev release root to upload. Defaults to configured publish-root dev/current.",
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
    delta: bool = typer.Option(
        True,
        "--delta/--full",
        help="Use delta upload by default; pass --full for a full bundle upload.",
    ),
) -> None:
    """Publish configured dev/current to production, using delta upload by default."""
    stable_root = _resolve_local_release_upload_root(root, env="dev")
    if stable_root is None:
        typer.echo("Set publish-root or pass --root before publishing dev/current to prod.")
        raise typer.Exit(1)
    try:
        _assert_prod_publish_source_v3(stable_root)
    except Exception as exc:
        typer.echo(f"FAILED prod publish-dev preflight: {exc}")
        raise typer.Exit(1) from exc
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
            delta=delta,
        )
    except Exception as exc:
        typer.echo(f"FAILED prod publish-dev: {exc}")
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


def _resolve_configured_releases_root(
    releases_root: Path | None = None,
    *,
    env: str | None = "dev",
) -> Path:
    if releases_root is not None:
        return releases_root.expanduser().resolve()
    configured_publish_root = resolve_publish_root(None)
    if configured_publish_root is not None:
        resolved_releases_root, _resolved_env = _resolve_release_publish_config(
            configured_publish_root,
            env,
        )
        return resolved_releases_root.expanduser().resolve()
    return _default_releases_root().expanduser().resolve()


def _default_release_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _resolve_release_command_defaults(
    *,
    source_root: Path | None,
    releases_root: Path | None,
    env: str | None,
) -> tuple[Path, Path, str]:
    resolved_source_root = resolve_running_root(source_root, fallback_to_cwd=False)
    _exit_if_path_mutates_current(resolved_source_root, "--from-root")
    if releases_root is not None:
        resolved_releases_root = releases_root.expanduser().resolve()
        resolved_env = normalize_ontology_env(env)
    else:
        configured_publish_root = resolve_publish_root(None)
        if configured_publish_root is not None:
            resolved_releases_root, resolved_env = _resolve_release_publish_config(
                configured_publish_root,
                env,
            )
        else:
            resolved_releases_root = _default_releases_root().expanduser().resolve()
            resolved_env = normalize_ontology_env(env)
    return resolved_source_root, resolved_releases_root, resolved_env


def _resolve_local_release_upload_root(root: Path | None, *, env: str | None = None) -> Path | None:
    configured_root = resolve_publish_root(root)
    if configured_root is None:
        return None
    resolved = configured_root.expanduser().resolve()
    if (resolved / RELEASE_MANIFEST_FILENAME).is_file():
        return resolved
    resolved_env = normalize_ontology_env(env)
    current = resolved / "current"
    if current.exists():
        return current.resolve()
    env_current = release_env_root(resolved, resolved_env) / "current"
    if env_current.exists():
        return env_current.resolve()
    return resolved


def _assert_prod_publish_source_v3(root: Path) -> dict[str, object]:
    verification = verify_release_startup_v3(root, check_sqlite=False)
    if not verification["ok"]:
        raise RuntimeError(
            "prod publish source must be a v3 immutable release: "
            + ", ".join(str(error) for error in verification["errors"])
        )
    return verification


def _release_root_has_ontology_artifacts(root: Path) -> bool:
    companies_root = root / "companies"
    if not companies_root.is_dir():
        return False
    return any(path.is_file() for path in companies_root.rglob("*"))


def _resolve_release_id_for_finalize_dev(env: str, releases_root: Path) -> str | None:
    return _resolve_release_id_from_publish_config(env, releases_root)


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
    if not build_index:
        raise ValueError(
            "Release publish requires index build and verification; --no-build-index is not supported."
        )
    resolved_source_root = source_root.expanduser().resolve()
    if not resolved_source_root.is_dir():
        raise FileNotFoundError(f"Source root not found: {resolved_source_root}")
    if not _release_root_has_ontology_artifacts(resolved_source_root):
        raise RuntimeError(
            f"Source root has no ontology artifacts under companies/: {resolved_source_root}"
        )

    queue = PipelineQueue(resolved_source_root)
    queue.ensure_dirs()
    if queue.worker_is_running() and not allow_running_queue:
        raise RuntimeError("Queue worker is running. Stop it first, or pass --allow-running-queue.")

    env_root = release_env_root(releases_root, env).expanduser().resolve()
    release_root = env_root / release_id
    global_spine_path = release_root / "indexes" / "global_spine.sqlite"
    if release_root.exists():
        if not allow_prepared_release_root:
            raise FileExistsError(f"Release directory already exists: {release_root}")
        if (release_root / RELEASE_MANIFEST_FILENAME).exists() or global_spine_path.exists():
            raise FileExistsError(
                f"Release directory already has finalized artifacts: {release_root}"
            )

    if dry_run:
        return {
            "release_id": release_id,
            "source_root": str(resolved_source_root),
            "release_root": str(release_root),
            "global_spine_path": str(global_spine_path),
            "index_layout": "global-spine-and-company-shards",
            "manifest": None,
            "global_spine_present": False,
            "build_index": build_index,
            "promoted": promote,
            "totals": None,
        }

    with FileProcessLock(queue.source_mutation_lock_path):
        result = _publish_root_as_local_release(
            source_root=resolved_source_root,
            releases_root=releases_root,
            env=env,
            release_id=release_id,
            promote=promote,
            force_release=True,
            allow_prepared_release_root=allow_prepared_release_root,
        )
    result["build_index"] = build_index
    result["global_spine_present"] = Path(str(result["global_spine_path"])).is_file()
    result["index_layout"] = result.get("layout") or "global-spine-and-company-shards"
    return result


def _publish_tickers_as_release(
    *,
    source_root: Path,
    releases_root: Path,
    tickers: list[str],
    env: str = "dev",
    release_id: str | None = None,
    promote: bool = True,
    force_release: bool = False,
) -> dict:
    """Publish ticker overlays as a verified immutable local release."""
    from krw_ontology.agent_index import build_spine_shard_release_outputs

    resolved_source_root = source_root.expanduser().resolve()
    resolved_releases_root = releases_root.expanduser().resolve()
    _assert_releases_root_not_nested_in_source(
        source_root=resolved_source_root,
        releases_root=resolved_releases_root,
    )
    resolved_env = normalize_ontology_env(env)
    env_root = release_env_root(resolved_releases_root, resolved_env).expanduser().resolve()
    selected_tickers = [ticker.upper() for ticker in tickers]
    if not selected_tickers:
        raise ValueError("At least one ticker is required for release publish")

    release_id = release_id or _default_release_id()
    release_root = env_root / release_id
    global_spine_path = release_root / "indexes" / "global_spine.sqlite"
    if release_root.exists():
        raise FileExistsError(f"Release directory already exists: {release_root}")

    lock_path = env_root / "locks" / "release_transaction.lock"
    promoted = False
    with FileProcessLock(lock_path):
        if release_root.exists():
            raise FileExistsError(f"Release directory already exists: {release_root}")
        current_root = env_root / "current"
        current_verification: dict | None = None
        try:
            release_root.mkdir(parents=True)
            if current_root.exists() or current_root.is_symlink():
                current_verification = verify_release_startup_v3(
                    current_root,
                    env=resolved_env,
                    require_current_symlink=current_root.is_symlink(),
                )
                if not current_verification["ok"]:
                    errors = ", ".join(current_verification["errors"])
                    raise RuntimeError(
                        f"Current release startup verify failed before publish: {errors}"
                    )
                _materialize_release_root_from_source(current_root.resolve(), release_root)

            for ticker in selected_tickers:
                _publish_ticker_tree(resolved_source_root, release_root, ticker)

            if (
                current_verification is not None
                and not force_release
                and _release_verification_artifacts_ok(current_root.resolve())
            ):
                candidate_manifest_hash = _source_manifest_hash_for_root(release_root)
                current_manifest_hash = _current_v3_source_manifest_hash(current_root.resolve())
                if (
                    candidate_manifest_hash is not None
                    and candidate_manifest_hash == current_manifest_hash
                ):
                    shutil.rmtree(release_root)
                    current_release_root = current_root.resolve()
                    current_manifest = current_verification.get("manifest") or {}
                    current_summary = (
                        _read_json_object(current_release_root / "indexes" / "build_summary.json")
                        or {}
                    )
                    return {
                        "release_id": current_verification.get("release_id"),
                        "env": resolved_env,
                        "source_root": str(resolved_source_root),
                        "releases_root": str(resolved_releases_root),
                        "release_root": str(current_release_root),
                        "global_spine_path": str(current_verification["global_spine_path"]),
                        "manifest": str(current_release_root / RELEASE_MANIFEST_FILENAME),
                        "manifest_payload": current_manifest,
                        "verification": current_verification,
                        "verify_report": str(
                            current_release_root / "verify" / "release_verify.json"
                        ),
                        "promoted": False,
                        "no_op": True,
                        "tickers": selected_tickers,
                        "totals": current_summary,
                        "layout": GLOBAL_SPINE_LAYOUT,
                    }

            build_result = build_spine_shard_release_outputs(
                release_root,
                release_id=release_id,
            )
            progress_path = getattr(build_result, "progress_path", None) or (
                release_root / "indexes" / "build_progress.jsonl"
            )
            manifest_started_at = time.perf_counter()
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="release_manifest",
                stage="manifest",
                status="started",
                output=release_root / RELEASE_MANIFEST_FILENAME,
            )
            manifest = write_release_manifest_v3(
                release_root,
                release_id=release_id,
                env=resolved_env,
                source_root=release_root,
                global_spine_path=build_result.global_spine_path,
                shard_manifest_path=build_result.shard_manifest_path,
            )
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="release_manifest",
                stage="manifest",
                status="complete",
                output=release_root / RELEASE_MANIFEST_FILENAME,
                started_at=manifest_started_at,
            )
            verification_started_at = time.perf_counter()
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="verification",
                stage="verification",
                status="started",
                output=release_root / "verify" / "release_verify.json",
            )
            verification = verify_release_root(release_root, env=resolved_env)
            if not verification["ok"]:
                errors = ", ".join(verification["errors"])
                _append_release_progress_event(
                    progress_path,
                    release_root=release_root,
                    release_id=release_id,
                    node_id="verification",
                    stage="verification",
                    status="failed",
                    output=release_root / "verify" / "release_verify.json",
                    error=errors,
                    started_at=verification_started_at,
                )
                raise RuntimeError(f"Release verify failed: {errors}")
            verify_report = write_release_verification_report(
                release_root,
                env=resolved_env,
                verification=verification,
            )
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="verification",
                stage="verification",
                status="complete",
                output=Path(str(verify_report["path"])),
                details={"ok": True},
                started_at=verification_started_at,
            )

            if promote:
                promote_started_at = time.perf_counter()
                _append_release_progress_event(
                    progress_path,
                    release_root=release_root,
                    release_id=release_id,
                    node_id="promote_current",
                    stage="promotion",
                    status="started",
                    output=env_root / "current",
                )
                promote_local_release(
                    resolved_releases_root,
                    env=resolved_env,
                    release_id=release_id,
                    preverified=verification,
                    preverified_report=verify_report,
                )
                promoted = True
                _append_release_progress_event(
                    progress_path,
                    release_root=release_root,
                    release_id=release_id,
                    node_id="promote_current",
                    stage="promotion",
                    status="complete",
                    output=env_root / "current",
                    details={"env": resolved_env},
                    started_at=promote_started_at,
                )
                if promoted:
                    _run_post_promote_gc(
                        releases_root=resolved_releases_root,
                        env=resolved_env,
                        release_id=release_id,
                        env_root=env_root,
                        progress_path=progress_path,
                        release_root=release_root,
                        started_at=promote_started_at,
                    )
        except Exception as exc:
            if not promoted:
                quarantine_local_release(
                    resolved_releases_root,
                    env=resolved_env,
                    release_path=release_root,
                    action="publish_tickers",
                    error=str(exc),
                )
            raise

    return {
        "release_id": release_id,
        "env": resolved_env,
        "source_root": str(resolved_source_root),
        "releases_root": str(resolved_releases_root),
        "release_root": str(release_root),
        "global_spine_path": str(global_spine_path),
        "manifest": str(release_root / RELEASE_MANIFEST_FILENAME),
        "manifest_payload": manifest,
        "verification": verification,
        "verify_report": verify_report["path"],
        "promoted": promoted,
        "no_op": False,
        "tickers": selected_tickers,
        "totals": build_result.build_summary,
        "layout": GLOBAL_SPINE_LAYOUT,
    }


def _publish_root_as_local_release(
    *,
    source_root: Path,
    releases_root: Path,
    env: str,
    release_id: str,
    promote: bool,
    force_release: bool,
    no_cache: bool = False,
    allow_prepared_release_root: bool = False,
) -> dict[str, object]:
    """Materialize, build, verify, and optionally promote a v3 full-root local release."""
    from krw_ontology.agent_index import build_spine_shard_release_outputs

    resolved_source_root = source_root.expanduser().resolve()
    resolved_releases_root = releases_root.expanduser().resolve()
    _assert_releases_root_not_nested_in_source(
        source_root=resolved_source_root,
        releases_root=resolved_releases_root,
    )
    resolved_env = normalize_ontology_env(env)
    if not resolved_source_root.is_dir():
        raise FileNotFoundError(f"Source root not found: {resolved_source_root}")
    if not _release_root_has_ontology_artifacts(resolved_source_root):
        raise RuntimeError(
            f"Source root has no ontology artifacts under companies/: {resolved_source_root}"
        )

    env_root = release_env_root(resolved_releases_root, resolved_env).expanduser().resolve()
    release_root = env_root / release_id
    global_spine_path = release_root / "indexes" / "global_spine.sqlite"
    lock_path = env_root / "locks" / "release_transaction.lock"
    with FileProcessLock(lock_path):
        _quarantine_stale_release_candidates(
            resolved_releases_root,
            resolved_env,
            reason="preflight stale release cleanup before publish_root",
            exclude_release_ids={release_id},
        )
        _release_disk_preflight(
            source_root=resolved_source_root,
            release_root=release_root,
        )
        if release_root.exists():
            if not allow_prepared_release_root:
                raise FileExistsError(f"Release directory already exists: {release_root}")
            if (release_root / RELEASE_MANIFEST_FILENAME).exists() or global_spine_path.exists():
                raise FileExistsError(
                    f"Release directory already has finalized artifacts: {release_root}"
                )
        current_root = env_root / "current"
        if not force_release and (current_root.exists() or current_root.is_symlink()):
            current_verification = verify_release_startup_v3(
                current_root,
                env=resolved_env,
                require_current_symlink=current_root.is_symlink(),
            )
            if not current_verification["ok"]:
                raise RuntimeError(
                    "Current release startup verify failed before publish: "
                    + ", ".join(current_verification["errors"])
                )
            source_manifest_hash = _source_manifest_hash_for_root(resolved_source_root)
            current_manifest_hash = _current_v3_source_manifest_hash(current_root.resolve())
            if source_manifest_hash is not None and source_manifest_hash == current_manifest_hash:
                current_release_root = current_root.resolve()
                return {
                    "release_id": current_verification.get("release_id"),
                    "env": resolved_env,
                    "source_root": str(resolved_source_root),
                    "releases_root": str(resolved_releases_root),
                    "release_root": str(current_release_root),
                    "global_spine_path": str(current_verification["global_spine_path"]),
                    "manifest": str(current_release_root / RELEASE_MANIFEST_FILENAME),
                    "verification": current_verification,
                    "verify_report": str(current_release_root / "verify" / "release_verify.json"),
                    "promoted": False,
                    "no_op": True,
                    "totals": {},
                    "layout": "global-spine-and-company-shards",
                }

        promoted = False
        try:
            release_root.mkdir(parents=True, exist_ok=allow_prepared_release_root)
            _materialize_release_root_from_source(resolved_source_root, release_root)
            build_result = build_spine_shard_release_outputs(
                release_root,
                release_id=release_id,
                no_cache=no_cache,
            )
            progress_path = getattr(build_result, "progress_path", None) or (
                release_root / "indexes" / "build_progress.jsonl"
            )
            manifest_started_at = time.perf_counter()
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="release_manifest",
                stage="manifest",
                status="started",
                output=release_root / RELEASE_MANIFEST_FILENAME,
            )
            write_release_manifest_v3(
                release_root,
                release_id=release_id,
                env=resolved_env,
                source_root=resolved_source_root,
                global_spine_path=build_result.global_spine_path,
                shard_manifest_path=build_result.shard_manifest_path,
            )
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="release_manifest",
                stage="manifest",
                status="complete",
                output=release_root / RELEASE_MANIFEST_FILENAME,
                started_at=manifest_started_at,
            )
            verification_started_at = time.perf_counter()
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="verification",
                stage="verification",
                status="started",
                output=release_root / "verify" / "release_verify.json",
            )
            verification = verify_release_root(
                release_root,
                env=resolved_env,
            )
            if not verification["ok"]:
                _append_release_progress_event(
                    progress_path,
                    release_root=release_root,
                    release_id=release_id,
                    node_id="verification",
                    stage="verification",
                    status="failed",
                    output=release_root / "verify" / "release_verify.json",
                    error=", ".join(verification["errors"]),
                    started_at=verification_started_at,
                )
                raise RuntimeError(f"Release verify failed: {', '.join(verification['errors'])}")
            verify_report = write_release_verification_report(
                release_root,
                env=resolved_env,
                verification=verification,
            )
            if not verify_report["ok"]:
                _append_release_progress_event(
                    progress_path,
                    release_root=release_root,
                    release_id=release_id,
                    node_id="verification",
                    stage="verification",
                    status="failed",
                    output=Path(
                        str(
                            verify_report.get("path")
                            or release_root / "verify" / "release_verify.json"
                        )
                    ),
                    error=", ".join(verify_report["errors"]),
                    started_at=verification_started_at,
                )
                raise RuntimeError(
                    f"Release verify report failed: {', '.join(verify_report['errors'])}"
                )
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="verification",
                stage="verification",
                status="complete",
                output=Path(str(verify_report["path"])),
                details={"ok": True},
                started_at=verification_started_at,
            )
            if promote:
                promote_started_at = time.perf_counter()
                _append_release_progress_event(
                    progress_path,
                    release_root=release_root,
                    release_id=release_id,
                    node_id="promote_current",
                    stage="promotion",
                    status="started",
                    output=env_root / "current",
                )
                promote_local_release(
                    resolved_releases_root,
                    env=resolved_env,
                    release_id=release_id,
                    preverified=verification,
                    preverified_report=verify_report,
                )
                promoted = True
                _append_release_progress_event(
                    progress_path,
                    release_root=release_root,
                    release_id=release_id,
                    node_id="promote_current",
                    stage="promotion",
                    status="complete",
                    output=env_root / "current",
                    details={"env": resolved_env},
                    started_at=promote_started_at,
                )
                if promoted:
                    _run_post_promote_gc(
                        releases_root=resolved_releases_root,
                        env=resolved_env,
                        release_id=release_id,
                        env_root=env_root,
                        progress_path=progress_path,
                        release_root=release_root,
                        started_at=promote_started_at,
                    )
        except Exception as exc:
            if not promoted:
                quarantine_local_release(
                    resolved_releases_root,
                    env=resolved_env,
                    release_path=release_root,
                    action="publish_root",
                    error=str(exc),
                )
            raise

    return {
        "release_id": release_id,
        "env": resolved_env,
        "source_root": str(resolved_source_root),
        "releases_root": str(resolved_releases_root),
        "release_root": str(release_root),
        "global_spine_path": str(global_spine_path),
        "manifest": str(release_root / RELEASE_MANIFEST_FILENAME),
        "verification": verification,
        "verify_report": verify_report["path"],
        "promoted": promoted,
        "no_op": False,
        "totals": build_result.build_summary,
        "layout": "global-spine-and-company-shards",
    }


def _source_manifest_hash_for_root(root: Path) -> str | None:
    from krw_ontology.agent_index import write_source_artifact_manifest

    resolved_root = root.expanduser().resolve()
    try:
        with tempfile.TemporaryDirectory(prefix="krw-v3-source-manifest-") as tmp_dir:
            manifest = write_source_artifact_manifest(
                resolved_root,
                manifest_path=Path(tmp_dir) / "source_manifest.json",
            )
        raw_hash = manifest.get("manifest_hash")
    except Exception:
        return None
    return str(raw_hash) if raw_hash else None


def _current_v3_source_manifest_hash(root: Path) -> str | None:
    for path in (root / "source_manifest.json", root / "indexes" / "source_manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        raw_hash = payload.get("manifest_hash") if isinstance(payload, dict) else None
        if raw_hash:
            return str(raw_hash)
    return None


def _release_verification_artifacts_ok(root: Path) -> bool:
    release_report_path = root / "verify" / "release_verify.json"
    try:
        release_report = json.loads(release_report_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(release_report, dict) or release_report.get("ok") is not True:
        return False
    manifest = release_report.get("verification", {}).get("manifest")
    return isinstance(manifest, Mapping) and manifest.get("format") == RELEASE_FORMAT_V3


_MATERIALIZED_SOURCE_IGNORED_TOP_LEVEL = {".krw_pipeline"}
_LEGACY_AGENT_INDEX_PREFIX = "agent_index" + ".sqlite"


def _is_legacy_agent_index_file_name(name: str) -> bool:
    return (
        name == _LEGACY_AGENT_INDEX_PREFIX
        or name.startswith(f"{_LEGACY_AGENT_INDEX_PREFIX}-")
        or name.startswith(f"{_LEGACY_AGENT_INDEX_PREFIX}.")
    )


def _ignore_legacy_agent_index_files(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if _is_legacy_agent_index_file_name(name)}


def _remove_legacy_agent_index_files(root: Path) -> None:
    indexes_dir = root / "indexes"
    if not indexes_dir.exists():
        return
    for path in sorted(indexes_dir.glob(f"{_LEGACY_AGENT_INDEX_PREFIX}*")):
        if not _is_legacy_agent_index_file_name(path.name):
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def _materialize_release_root_from_source(
    source_root: Path,
    release_root: Path,
) -> dict[str, int]:
    """Copy source ontology artifacts into an immutable release root candidate."""
    from krw_ontology.agent_index.spine_builder import (
        clone_or_copy_immutable_file,
        clone_or_copy_immutable_tree,
    )

    copy_modes = {"reflink": 0, "copy": 0}
    release_root.mkdir(parents=True, exist_ok=True)
    for source_path in source_root.iterdir():
        if source_path.name in _MATERIALIZED_SOURCE_IGNORED_TOP_LEVEL:
            continue
        target_path = release_root / source_path.name
        if target_path.exists() or target_path.is_symlink():
            if target_path.is_dir() and not target_path.is_symlink():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()
        if source_path.is_dir() and not source_path.is_symlink():
            if source_path.name == "indexes":
                source_manifest_path = source_path / "source_manifest.json"
                if source_manifest_path.is_file():
                    target_path.mkdir(parents=True, exist_ok=True)
                    mode = clone_or_copy_immutable_file(
                        source_manifest_path,
                        target_path / source_manifest_path.name,
                    )
                    copy_modes[mode] += 1
                # The observation sidecar is collected ahead of time by the
                # standalone `observation build` step; carry it into the
                # release candidate when present (the release build itself
                # never fetches). chart_series is rebuilt from shards instead.
                observations_source_path = source_path / "observations.sqlite"
                if observations_source_path.is_file():
                    target_path.mkdir(parents=True, exist_ok=True)
                    mode = clone_or_copy_immutable_file(
                        observations_source_path,
                        target_path / observations_source_path.name,
                    )
                    copy_modes[mode] += 1
                continue
            mode = clone_or_copy_immutable_tree(
                source_path,
                target_path,
                ignored_names=(".krw_pipeline",),
            )
            copy_modes[mode] += 1
        else:
            if _is_legacy_agent_index_file_name(source_path.name):
                continue
            mode = clone_or_copy_immutable_file(source_path, target_path)
            copy_modes[mode] += 1
    return copy_modes


def _release_disk_preflight(*, source_root: Path, release_root: Path) -> None:
    release_root.parent.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(str(release_root.parent)).free
    source_bytes = _estimate_materialized_source_size(source_root)
    reserve_bytes = _release_min_free_bytes()
    required_bytes = max(source_bytes * 3, reserve_bytes)
    if free_bytes < required_bytes:
        raise RuntimeError(
            "release_disk_preflight_failed: "
            f"free={free_bytes} required={required_bytes} "
            f"estimated_source={source_bytes} release_root={release_root}"
        )


def _assert_releases_root_not_nested_in_source(
    *,
    source_root: Path,
    releases_root: Path,
) -> None:
    resolved_source = source_root.expanduser().resolve()
    resolved_releases = releases_root.expanduser().resolve()
    try:
        resolved_releases.relative_to(resolved_source)
    except ValueError:
        return
    raise ValueError(
        "release_roots_overlap: --releases-root must not be inside --from-root: "
        f"source_root={resolved_source} releases_root={resolved_releases}"
    )


def _release_min_free_bytes() -> int:
    raw_bytes = os.environ.get("KRW_ONTOLOGY_RELEASE_MIN_FREE_BYTES")
    if raw_bytes:
        try:
            return max(1, int(raw_bytes))
        except ValueError:
            pass
    raw_gb = os.environ.get("KRW_ONTOLOGY_RELEASE_MIN_FREE_GB")
    if raw_gb:
        try:
            return max(1, int(float(raw_gb) * 1024**3))
        except ValueError:
            pass
    return 5 * 1024**3


def _estimate_materialized_source_size(source_root: Path) -> int:
    total = 0
    resolved_root = source_root.expanduser().resolve()
    for dirpath, dirnames, filenames in os.walk(resolved_root):
        current = Path(dirpath)
        if current == resolved_root:
            dirnames[:] = [
                name for name in dirnames if name not in _MATERIALIZED_SOURCE_IGNORED_TOP_LEVEL
            ]
        if current == resolved_root / "indexes":
            filenames[:] = [name for name in filenames if name == "source_manifest.json"]
            dirnames[:] = []
        for filename in filenames:
            path = current / filename
            if _is_legacy_agent_index_file_name(path.name):
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _dev_publish_paths(releases_root: Path, release_id: str) -> dict[str, Path]:
    release_root = release_env_root(releases_root, "dev").expanduser().resolve() / release_id
    return {
        "release_root": release_root,
        "global_spine_path": release_root / "indexes" / "global_spine.sqlite",
        "shard_manifest_path": release_root / "indexes" / "shard_manifest.json",
        "build_summary_path": release_root / "indexes" / "build_summary.json",
        "log_path": release_root / "logs" / "publish-dev.log",
        "progress_path": release_root / "indexes" / "build_progress.jsonl",
        "worker_pid_path": release_root / "worker.pid",
        "worker_state_path": release_root / "worker_state.json",
    }


def _echo_v3_release_build_summary(summary: Mapping[str, object] | None) -> None:
    if not isinstance(summary, Mapping):
        typer.echo("V3 indexes: not built")
        return
    company_cache = summary.get("company_shard_cache") or {}
    fragment_cache = summary.get("spine_fragment_cache") or {}
    global_spine = summary.get("global_spine") or {}
    counts = global_spine.get("counts") if isinstance(global_spine, Mapping) else {}
    typer.echo(
        "V3 indexes built: "
        f"companies={summary.get('company_count', 0)} "
        f"artifacts={summary.get('artifact_count', 0)}"
    )
    if isinstance(company_cache, Mapping):
        typer.echo(
            "company_shard_cache: "
            f"hits={company_cache.get('hits', 0)} "
            f"misses={company_cache.get('misses', 0)}"
        )
    if isinstance(fragment_cache, Mapping):
        typer.echo(
            "spine_fragment_cache: "
            f"hits={fragment_cache.get('hits', 0)} "
            f"misses={fragment_cache.get('misses', 0)}"
        )
    if isinstance(global_spine, Mapping):
        typer.echo(f"global_spine: {global_spine.get('path') or '<missing>'}")
    if isinstance(counts, Mapping):
        typer.echo(
            "global_counts: "
            f"documents={counts.get('global_document_catalog', 0)} "
            f"objects={counts.get('global_object_locator', 0)} "
            f"edges={counts.get('global_edge_spine', 0)} "
            f"topics={counts.get('global_topic_spine', 0)}"
        )


def _build_v3_indexes_for_mutable_root(
    root: Path,
    *,
    release_id_hint: str = "mutable-refresh",
    no_cache: bool = False,
) -> dict[str, object]:
    """Refresh v3 index outputs inside a mutable staging/running root."""
    from krw_ontology.agent_index import build_spine_shard_release_outputs

    resolved_root = root.expanduser().resolve()
    _assert_queue_path_not_prod_current(resolved_root, "index root")
    result = build_spine_shard_release_outputs(
        resolved_root,
        release_id=release_id_hint,
        no_cache=no_cache,
    )
    return {
        "root": resolved_root,
        "index_layout": GLOBAL_SPINE_LAYOUT,
        "global_spine_path": result.global_spine_path,
        "shard_manifest_path": result.shard_manifest_path,
        "build_summary_path": result.build_summary_path,
        "totals": result.build_summary,
    }


def _echo_v3_index_refresh_result(result: Mapping[str, object]) -> None:
    typer.echo(f"V3 index refreshed: {result['global_spine_path']}")
    typer.echo(f"index_layout: {result.get('index_layout') or GLOBAL_SPINE_LAYOUT}")
    typer.echo(f"Shard manifest: {result['shard_manifest_path']}")
    typer.echo(f"Build summary: {result['build_summary_path']}")
    _echo_v3_release_build_summary(result.get("totals"))


def _release_command_paths(releases_root: Path, env: str, release_id: str) -> dict[str, Path]:
    release_root = release_env_root(releases_root, env).expanduser().resolve() / release_id
    return {
        "release_root": release_root,
        "global_spine_path": release_root / "indexes" / "global_spine.sqlite",
        "shard_manifest_path": release_root / "indexes" / "shard_manifest.json",
        "build_summary_path": release_root / "indexes" / "build_summary.json",
        "progress_path": release_root / "indexes" / "build_progress.jsonl",
        "log_path": release_root / "logs" / "release-build.log",
        "worker_pid_path": release_root / "worker.pid",
        "worker_state_path": release_root / "worker_state.json",
    }


def _write_release_worker_state(
    *,
    releases_root: Path,
    env: str,
    release_id: str,
    pid: int,
    mode: dict,
    status: str = "running",
) -> None:
    paths = _release_command_paths(releases_root, env, release_id)
    paths["release_root"].mkdir(parents=True, exist_ok=True)
    paths["worker_pid_path"].write_text(str(pid), encoding="utf-8")
    payload = {
        "pid": pid,
        "status": status,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
    }
    _write_json_atomic(paths["worker_state_path"], payload)


def _update_release_worker_status(
    *,
    releases_root: Path,
    env: str,
    release_id: str,
    status: str,
    extra: dict | None = None,
) -> None:
    paths = _release_command_paths(releases_root, env, release_id)
    try:
        payload = json.loads(paths["worker_state_path"].read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {}
    payload.update(
        {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if extra:
        payload.update(extra)
    _write_json_atomic(paths["worker_state_path"], payload)


def _release_worker_state(releases_root: Path, env: str, release_id: str) -> dict | None:
    path = _release_command_paths(releases_root, env, release_id)["worker_state_path"]
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _release_worker_pid(releases_root: Path, env: str, release_id: str) -> int | None:
    path = _release_command_paths(releases_root, env, release_id)["worker_pid_path"]
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _release_worker_runtime_status(
    release_root: Path, pid: int | None, state: Mapping[str, object]
) -> dict[str, object]:
    running = pid is not None and is_pid_running(pid)
    state_status = str(state.get("status") or "<unknown>")
    stale_pid = _stale_release_worker_pid(release_root)
    stale = (
        stale_pid is not None
        and state_status in {"running", "cancel_requested", "<unknown>"}
        and _release_looks_interrupted(release_root)
    )
    return {
        "running": running,
        "stale": stale,
        "stale_pid": stale_pid,
        "status": "stale" if stale else state_status,
    }


def _quarantine_stale_release_candidates(
    releases_root: Path,
    env: str,
    *,
    reason: str,
    exclude_release_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    resolved_env = normalize_ontology_env(env)
    env_root = release_env_root(releases_root, resolved_env).expanduser().resolve()
    if not env_root.exists():
        return []
    current_id = current_release_id(env_root)
    excluded = set(exclude_release_ids or set())
    if current_id:
        excluded.add(current_id)
    quarantined: list[dict[str, object]] = []
    candidates = [
        path
        for path in sorted(env_root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
        if _is_release_candidate(path) and path.name not in excluded
    ]
    for candidate in candidates:
        stale_pid = _stale_release_worker_pid(candidate)
        if stale_pid is None or not _release_looks_interrupted(candidate):
            continue
        result = quarantine_local_release(
            releases_root,
            env=resolved_env,
            release_path=candidate,
            action="cleanup_interrupted",
            error=f"{reason}: interrupted release worker pid={stale_pid} is not running",
        )
        if result is not None:
            quarantined.append(result)
    return quarantined


def _latest_release_candidate_id(releases_root: Path, env: str) -> str | None:
    env_root = release_env_root(releases_root, env).expanduser().resolve()
    if not env_root.exists():
        return None
    candidates = [path for path in env_root.iterdir() if _is_release_candidate(path)]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime).name


def _is_release_candidate(path: Path) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    if path.name.startswith(".") or path.name in RELEASE_ENV_RESERVED_DIRNAMES:
        return False
    return any(
        candidate.exists()
        for candidate in (
            path / "worker.pid",
            path / "worker_state.json",
            path / "logs" / "release-build.log",
            path / RELEASE_MANIFEST_FILENAME,
            path / "indexes" / "global_spine.sqlite",
        )
    )


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
    candidates = [path for path in env_root.iterdir() if _is_dev_publish_candidate(path)]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime).name


def _is_dev_publish_candidate(path: Path) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    if path.name.startswith(".") or path.name in RELEASE_ENV_RESERVED_DIRNAMES:
        return False
    return any(
        candidate.exists()
        for candidate in (
            path / "worker.pid",
            path / "worker_state.json",
            path / "logs" / "publish-dev.log",
            path / RELEASE_MANIFEST_FILENAME,
            path / "indexes" / "global_spine.sqlite",
        )
    )


def _release_worker_pid_path(release_root: Path) -> Path:
    return release_root / "worker.pid"


def _stale_release_worker_pid(release_root: Path) -> int | None:
    pid_path = _release_worker_pid_path(release_root)
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None
    return None if is_pid_running(pid) else pid


def _release_looks_interrupted(release_root: Path) -> bool:
    stale_pid = _stale_release_worker_pid(release_root)
    if stale_pid is None:
        return False
    manifest_path = release_root / RELEASE_MANIFEST_FILENAME
    verify_path = release_root / "verify" / "release_verify.json"
    return not (manifest_path.exists() and verify_path.exists())


def _delete_interrupted_release_temp_files(release_root: Path) -> list[str]:
    removed: list[str] = []
    indexes_dir = release_root / "indexes"
    if not indexes_dir.is_dir():
        return removed
    for path in sorted(indexes_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.name.startswith("."):
            continue
        if not (
            path.name.startswith(
                (".agent_index.", ".index_shards.", ".global_spine.", ".shard_manifest.")
            )
            or path.name.endswith(".tmp")
            or ".sqlite." in path.name
        ):
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        removed.append(str(path))
    return removed


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
        _remove_legacy_agent_index_files(target_root)
        return
    shutil.rmtree(target_root)
    shutil.copytree(source_root, target_root, ignore=shutil.ignore_patterns(".krw_pipeline"))
    _remove_legacy_agent_index_files(target_root)


def _retarget_queue_publish_jobs(
    *,
    store: PipelineQueue,
    publish_root: Path,
    statuses: set[str],
    dry_run: bool = False,
) -> dict[str, int]:
    jobs = store.list_jobs(statuses=statuses)
    changed = 0
    for job in jobs:
        next_publish_root = str(publish_root)
        if job.publish_root == next_publish_root:
            continue
        changed += 1
        if dry_run:
            continue
        job.publish_root = next_publish_root
        store.save_job(job)
        store.append_event(
            "publish_retargeted",
            job,
            {
                "publish_root": next_publish_root,
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
    for stale_file in (
        release_root / RELEASE_MANIFEST_FILENAME,
        release_root / "source_manifest.json",
    ):
        stale_file.unlink(missing_ok=True)
    for stale_dir in ("indexes", "verify", "logs", "debug"):
        shutil.rmtree(release_root / stale_dir, ignore_errors=True)

    if set_config:
        set_config_value("publish-root", str(release_root))

    queue_jobs_changed = 0
    queue_retargeted = False
    if retarget_queue:
        assert retarget_store is not None
        retarget_result = _retarget_queue_publish_jobs(
            store=retarget_store,
            publish_root=release_root,
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
        help="Point CLI publish-root at the new dev release.",
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
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the release that would be prepared."
    ),
) -> None:
    """Prepare a dev release seed and optionally retarget queued publish jobs."""
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
    typer.echo("Next: krw-ontology queue start --no-refresh-index")


@release_app.command("publish-dev")
def release_publish_dev_cmd(
    release_id: Optional[str] = typer.Argument(
        None,
        help="Dev release id to create. Defaults to a timestamp id.",
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or ~/krw-ontology-data/releases.",
    ),
    source_root: Optional[Path] = typer.Option(
        None,
        "--from-root",
        help="Source running root. Defaults to configured running-root.",
    ),
    build_index: bool = typer.Option(
        True,
        "--build-index/--no-build-index",
        help="Build v3 global spine and company shards inside the new dev release.",
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
    foreground: bool = typer.Option(
        False, "--foreground", help="Run in the foreground instead of starting a background worker."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be published."),
) -> None:
    """Build a v3 dev release from running-root and promote dev/current."""
    if not build_index:
        typer.echo(
            "Release publish requires index build and verification; --no-build-index is not supported."
        )
        raise typer.Exit(1)
    resolved_release_id = release_id or _default_release_id()
    try:
        resolved_releases_root = _resolve_configured_releases_root(releases_root, env="dev")
        resolved_source_root = (
            source_root.expanduser().resolve()
            if source_root is not None
            else resolve_running_root(None, fallback_to_cwd=False)
        )
        if dry_run or foreground:
            result = _release_publish_dev(
                releases_root=resolved_releases_root,
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
            paths = _dev_publish_paths(resolved_releases_root, resolved_release_id)
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
                str(resolved_releases_root),
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
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            typer.echo(f"Started dev publish worker pid={process.pid}")
            typer.echo(f"release_id: {resolved_release_id}")
            typer.echo(f"source_root: {resolved_source_root}")
            typer.echo(f"release_root: {paths['release_root']}")
            typer.echo("index_layout: global-spine-and-company-shards")
            typer.echo(f"global_spine: {paths['global_spine_path']}")
            typer.echo(f"shard_manifest: {paths['shard_manifest_path']}")
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
    typer.echo(
        f"index_layout: {result.get('index_layout') or result.get('layout') or GLOBAL_SPINE_LAYOUT}"
    )
    typer.echo(f"global_spine: {result['global_spine_path']}")
    _echo_v3_release_build_summary(result.get("totals") if not dry_run else None)
    typer.echo(f"promoted: {result['promoted']}")
    if not dry_run:
        typer.echo(f"manifest: {result['manifest']}")
        typer.echo(f"verify_report: {result['verify_report']}")
        typer.echo("Next: krw-ontology prod publish")


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
        typer.echo(
            f"index_layout: {result.get('index_layout') or result.get('layout') or GLOBAL_SPINE_LAYOUT}"
        )
        typer.echo(f"global_spine: {result['global_spine_path']}")
        _echo_v3_release_build_summary(result.get("totals"))
        typer.echo(f"promoted: {result['promoted']}")
        typer.echo(f"manifest: {result['manifest']}")
        typer.echo(f"verify_report: {result['verify_report']}")
        typer.echo("Next: krw-ontology prod publish")
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
    release_id: Optional[str] = typer.Argument(
        None, help="Dev release id. Defaults to latest dev publish release."
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or ~/krw-ontology-data/releases.",
    ),
) -> None:
    """Show publish-dev worker and artifact status."""
    resolved_releases_root = _resolve_configured_releases_root(releases_root, env="dev")
    selected_release_id = release_id or _latest_dev_publish_id(resolved_releases_root)
    if selected_release_id is None:
        typer.echo("No dev release found.")
        raise typer.Exit(1)
    paths = _dev_publish_paths(resolved_releases_root, selected_release_id)
    pid = _dev_publish_worker_pid(resolved_releases_root, selected_release_id)
    running = pid is not None and is_pid_running(pid)
    current_id = current_release_id(
        release_env_root(resolved_releases_root, "dev").expanduser().resolve()
    )
    typer.echo("Publish-dev status")
    typer.echo(f"release_id: {selected_release_id}")
    typer.echo(f"worker: {'running' if running else 'stopped'}" + (f" pid={pid}" if pid else ""))
    typer.echo(f"current: {current_id or '<missing>'}")
    typer.echo(f"release_root: {paths['release_root']}")
    typer.echo(
        "global_spine: "
        f"{'present' if paths['global_spine_path'].exists() else 'missing'} "
        f"{paths['global_spine_path']}"
    )
    typer.echo(
        "shard_manifest: "
        f"{'present' if paths['shard_manifest_path'].exists() else 'missing'} "
        f"{paths['shard_manifest_path']}"
    )
    typer.echo(
        f"manifest: {'present' if (paths['release_root'] / RELEASE_MANIFEST_FILENAME).exists() else 'missing'}"
    )
    typer.echo(
        f"verify_report: {'present' if (paths['release_root'] / 'verify' / 'release_verify.json').exists() else 'missing'}"
    )
    typer.echo(f"log: {paths['log_path']}")
    _echo_release_progress_status(paths["progress_path"])


@release_app.command("publish-dev-watch")
def release_publish_dev_watch_cmd(
    release_id: Optional[str] = typer.Argument(
        None, help="Dev release id. Defaults to latest dev publish release."
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or ~/krw-ontology-data/releases.",
    ),
    lines: int = typer.Option(80, "--lines", min=1, help="Number of trailing lines to show first."),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Follow appended log output."),
) -> None:
    """Watch publish-dev logs."""
    resolved_releases_root = _resolve_configured_releases_root(releases_root, env="dev")
    selected_release_id = release_id or _latest_dev_publish_id(resolved_releases_root)
    if selected_release_id is None:
        typer.echo("No dev release found.")
        raise typer.Exit(1)
    paths = _dev_publish_paths(resolved_releases_root, selected_release_id)
    if not paths["log_path"].exists() and not paths["progress_path"].exists():
        typer.echo(f"Log does not exist: {paths['log_path']}")
        raise typer.Exit(1)
    _watch_release_log_and_progress(
        log_path=paths["log_path"],
        progress_path=paths["progress_path"],
        lines=lines,
        follow=follow,
    )


@release_app.command("cleanup-interrupted")
def release_cleanup_interrupted_cmd(
    release_id: Optional[str] = typer.Argument(
        None,
        help="Release id to quarantine. Defaults to all interrupted non-current candidates.",
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or ~/krw-ontology-data/releases.",
    ),
    env: str = typer.Option(
        "dev",
        "--env",
        help="Release environment.",
    ),
    delete_temp: bool = typer.Option(
        False,
        "--delete-temp",
        help="Delete interrupted temporary index files after quarantine.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show candidates without moving anything.",
    ),
) -> None:
    """Quarantine interrupted release candidates with stale worker state."""
    resolved_env = normalize_ontology_env(env)
    resolved_releases_root = _resolve_configured_releases_root(releases_root, env=resolved_env)
    env_root = release_env_root(resolved_releases_root, resolved_env).expanduser().resolve()
    current_id = current_release_id(env_root)
    if release_id is not None:
        candidates = [env_root / release_id]
    elif env_root.exists():
        candidates = [
            path
            for path in sorted(
                env_root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True
            )
            if path.is_dir()
            and not path.is_symlink()
            and not path.name.startswith(".")
            and path.name not in RELEASE_ENV_RESERVED_DIRNAMES
        ]
    else:
        candidates = []

    interrupted: list[tuple[Path, int]] = []
    for candidate in candidates:
        if candidate.name == current_id:
            continue
        stale_pid = _stale_release_worker_pid(candidate)
        if stale_pid is None:
            continue
        if not _release_looks_interrupted(candidate):
            continue
        interrupted.append((candidate, stale_pid))

    if not interrupted:
        typer.echo("No interrupted release candidates found.")
        return

    for candidate, stale_pid in interrupted:
        if dry_run:
            typer.echo(f"would quarantine: {candidate.name} stale_worker_pid={stale_pid}")
            continue
        result = quarantine_local_release(
            resolved_releases_root,
            env=resolved_env,
            release_path=candidate,
            action="cleanup_interrupted",
            error=f"interrupted release worker pid={stale_pid} is not running",
        )
        if result is None:
            typer.echo(f"skipped: {candidate.name}")
            continue
        quarantine_path = Path(str(result["path"]))
        removed = _delete_interrupted_release_temp_files(quarantine_path) if delete_temp else []
        typer.echo(f"quarantined: {candidate.name} -> {quarantine_path}")
        typer.echo(f"failure: {quarantine_path / FAILED_RELEASE_METADATA_FILENAME}")
        if removed:
            typer.echo(f"removed_temp_files: {len(removed)}")


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
        help="Build v3 global spine and company shards before writing the manifest.",
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
    if not build_index:
        typer.echo(
            "Release finalize requires index build and verification; --no-build-index is not supported."
        )
        raise typer.Exit(1)
    env = "dev"
    release_id = release_id or _resolve_release_id_for_finalize_dev(env, releases_root)
    if release_id is None:
        typer.echo(
            "Missing release id. Pass a prepared non-current release id or run prepare-dev first."
        )
        raise typer.Exit(1)
    env_root = release_env_root(releases_root, env).expanduser().resolve()
    if release_id == current_release_id(env_root):
        typer.echo(
            "Refusing to finalize dev/current in place. "
            "Create a new candidate with release prepare-dev or release publish."
        )
        raise typer.Exit(1)
    release_root = env_root / release_id
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
        from krw_ontology.agent_index import build_spine_shard_release_outputs

        index_result = build_spine_shard_release_outputs(
            release_root,
            release_id=release_id,
            no_cache=True,
        )
        summary = index_result.build_summary
        counts = (summary.get("global_spine") or {}).get("counts") or {}
        typer.echo(
            "V3 indexes built: "
            f"{index_result.global_spine_path} "
            f"documents={counts.get('global_document_catalog', 0)} "
            f"objects={counts.get('global_object_locator', 0)} "
            f"edges={counts.get('global_edge_spine', 0)} "
            f"topics={counts.get('global_topic_spine', 0)}"
        )

    manifest = write_release_manifest_v3(
        release_root,
        release_id=release_id,
        env=env,
        source_root=release_root,
    )
    verification = verify_release_root(release_root, env=env)
    if not verification["ok"]:
        typer.echo(f"Release verify failed: {', '.join(verification['errors'])}")
        raise typer.Exit(1)
    verify_report = write_release_verification_report(
        release_root,
        env=env,
        verification=verification,
    )

    if promote:
        promote_local_release(releases_root, env=env, release_id=release_id)
        typer.echo(f"Release promoted: env=dev release_id={release_id}")

    if clear_config:
        config = load_cli_config()
        if config.publish_root == str(release_root):
            unset_config_value("publish-root")

    typer.echo(f"Dev release finalized: {release_id}")
    typer.echo(f"release_root: {release_root}")
    typer.echo(f"manifest: {release_root / RELEASE_MANIFEST_FILENAME}")
    typer.echo(f"verify_report: {verify_report['path']}")
    typer.echo(f"index_layout: {manifest.get('index_layout') or '<missing>'}")
    typer.echo(f"global_spine: {release_root / 'indexes' / 'global_spine.sqlite'}")
    typer.echo("Next: krw-ontology prod publish")


@release_app.command("materialize-prod")
def release_materialize_prod_cmd(
    release_id: str = typer.Argument(
        ...,
        help="Source dev/staging release id to copy. Use 'current' for the source env current release.",
    ),
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
    promote: bool = typer.Option(
        True,
        "--promote/--no-promote",
        help="Promote prod/current after materializing the prod release.",
    ),
    startup_check: bool = typer.Option(
        True,
        "--startup-check/--no-startup-check",
        help="Run the lightweight prod/current MCP startup check after promotion.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the prod release that would be created."
    ),
) -> None:
    """Copy a verified dev/staging release into prod release space and promote prod/current by default."""
    source_env = normalize_ontology_env(from_env)
    if source_env == "prod":
        typer.echo("--from-env must be dev or staging.")
        raise typer.Exit(1)
    source_env_root = release_env_root(releases_root, source_env).expanduser().resolve()
    source_release_id = (
        current_release_id(source_env_root) if release_id == "current" else release_id
    )
    if not source_release_id:
        typer.echo(f"No current release found for env={source_env}")
        raise typer.Exit(1)
    target_id = prod_release_id or source_release_id
    source_root = source_env_root / source_release_id
    prod_env_root = release_env_root(releases_root, "prod").expanduser().resolve()
    prod_root = prod_env_root / target_id
    source_startup = verify_release_startup_v3(source_root, env=source_env, check_sqlite=False)
    if not source_startup["ok"]:
        typer.echo(f"Source release must be v3: {', '.join(source_startup['errors'])}")
        raise typer.Exit(1)
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
        typer.echo(f"promote: {promote}")
        typer.echo(f"startup_check: {startup_check and promote}")
        return

    _copy_release_tree(source_root, prod_root)
    shutil.rmtree(prod_root / ".krw_pipeline", ignore_errors=True)
    inherit_spine_verification_seal(
        source_root / "indexes" / "global_spine.sqlite",
        prod_root / "indexes" / "global_spine.sqlite",
        details={"materialization": "local-prod"},
    )
    global_spine_sha256 = read_spine_verification_sha256(
        prod_root / "indexes" / "global_spine.sqlite"
    )
    rebind_router_sidecar_release(
        prod_root / ROUTER_SIDECAR_RELATIVE_PATH,
        release_id=target_id,
        expected_global_spine_sha256=global_spine_sha256,
        expected_previous_release_id=source_release_id,
        trusted_source_path=source_root / ROUTER_SIDECAR_RELATIVE_PATH,
    )
    rebind_router_coherence_release(
        prod_root / ROUTER_COHERENCE_RELATIVE_PATH,
        release_id=target_id,
        expected_global_spine_sha256=global_spine_sha256,
        expected_previous_release_id=source_release_id,
        trusted_source_path=source_root / ROUTER_COHERENCE_RELATIVE_PATH,
    )
    manifest = write_release_manifest_v3(
        prod_root,
        release_id=target_id,
        env="prod",
        source_root=source_root,
        global_spine_path=prod_root / "indexes" / "global_spine.sqlite",
        shard_manifest_path=prod_root / "indexes" / "shard_manifest.json",
    )
    prod_verification = verify_release_root(prod_root, env="prod")
    if not prod_verification["ok"]:
        typer.echo(f"Prod release verify failed: {', '.join(prod_verification['errors'])}")
        raise typer.Exit(1)
    verify_report = write_release_verification_report(
        prod_root,
        env="prod",
        verification=prod_verification,
    )

    typer.echo(f"Prod release materialized: {target_id}")
    typer.echo(f"prod_root: {prod_root}")
    typer.echo(f"verify_report: {verify_report['path']}")
    typer.echo(f"index_layout: {manifest.get('index_layout') or '<missing>'}")
    typer.echo(f"global_spine: {prod_root / 'indexes' / 'global_spine.sqlite'}")
    if not promote:
        typer.echo(
            "Next: krw-ontology release promote "
            f"{target_id} --releases-root {releases_root} --env prod"
        )
        return

    try:
        promotion = promote_local_release(
            releases_root,
            env="prod",
            release_id=target_id,
            preverified=prod_verification,
            preverified_report=verify_report,
        )
    except Exception as exc:
        typer.echo(f"FAILED prod promote: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"Prod release promoted: {promotion['release_id']}")
    typer.echo(f"current: {promotion['current']}")
    typer.echo(f"promote_verify_report: {promotion['verify_report']}")

    if not startup_check:
        return
    startup_verification = verify_release_startup_v3(
        prod_env_root / "current",
        env="prod",
        require_current_symlink=True,
        check_sqlite=True,
    )
    if not startup_verification["ok"]:
        typer.echo(f"FAILED prod startup-check: {', '.join(startup_verification['errors'])}")
        raise typer.Exit(1)
    typer.echo("Prod startup-check: ok")
    typer.echo(f"startup_release_id: {startup_verification.get('release_id')}")
    typer.echo(f"startup_global_spine: {startup_verification.get('global_spine_path')}")


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
) -> None:
    """Write canonical v3 manifest.json for a local immutable release root."""
    _exit_if_path_mutates_current(root, "--root")
    resolved_release_id = release_id or _new_release_id()
    manifest = write_release_manifest_v3(
        root,
        release_id=resolved_release_id,
        env=env,
        source_root=root,
    )
    typer.echo(
        f"Release manifest written: {Path(root).expanduser().resolve() / RELEASE_MANIFEST_FILENAME}"
    )
    typer.echo(f"env: {manifest['env']}")
    typer.echo(f"release_id: {manifest['release_id']}")
    typer.echo(f"format: {manifest['format']}")
    typer.echo(f"index_layout: {manifest.get('index_layout') or '<missing>'}")
    typer.echo(
        "global_spine: "
        + (
            "present"
            if (Path(root).expanduser().resolve() / "indexes" / "global_spine.sqlite").is_file()
            else "missing"
        )
    )


@release_app.command("verify")
def release_verify_cmd(
    root: Path = typer.Option(..., "--root", help="Release root or env/current symlink to verify."),
    env: Optional[str] = typer.Option(None, "--env", help="Expected ontology environment."),
    require_current_symlink: bool = typer.Option(
        False,
        "--require-current-symlink",
        help="Require --root to be the env current symlink.",
    ),
    startup_check: bool = typer.Option(
        False,
        "--startup-check",
        help="Run the MCP startup contract check instead of release activation verification.",
    ),
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Run full topology/hash/endpoint verification. Default release verification is lightweight.",
    ),
    write_report: bool = typer.Option(
        False,
        "--write-report",
        help="Write verify/release_verify.json inside the resolved release root.",
    ),
) -> None:
    """Verify the v3 release manifest and global spine + company shard topology."""
    if startup_check and write_report:
        typer.echo("FAIL --startup-check is read-only and cannot write verification artifacts")
        raise typer.Exit(1)
    if startup_check and deep:
        typer.echo("FAIL --startup-check and --deep cannot be used together")
        raise typer.Exit(1)
    if write_report:
        _exit_if_path_mutates_current(root, "--root")
    try:
        if startup_check:
            verification = verify_release_startup_v3(
                root,
                env=env,
                require_current_symlink=require_current_symlink,
            )
        else:
            verification = verify_release_root(
                root,
                env=env,
                require_current_symlink=require_current_symlink,
                deep=deep,
            )
    except ValueError as exc:
        typer.echo(f"FAIL {exc}")
        raise typer.Exit(1) from exc
    verify_report = None
    if write_report:
        verify_report = write_release_verification_report(
            root,
            env=env,
            require_current_symlink=require_current_symlink,
            verification=verification,
        )
    typer.echo(f"Release verify: {'ok' if verification['ok'] else 'failed'}")
    if startup_check:
        typer.echo("mode: startup")
    elif deep:
        typer.echo("mode: deep")
    else:
        typer.echo("mode: light")
    typer.echo(f"root: {verification['root']}")
    typer.echo(f"env: {verification.get('env') or '<missing>'}")
    typer.echo(f"release_id: {verification.get('release_id') or '<missing>'}")
    typer.echo(f"manifest: {verification.get('manifest_path') or '<missing>'}")
    manifest = (
        verification.get("manifest") if isinstance(verification.get("manifest"), Mapping) else {}
    )
    typer.echo(f"format: {manifest.get('format') or '<missing>'}")
    typer.echo(
        f"index_layout: {verification.get('index_layout') or manifest.get('index_layout') or '<missing>'}"
    )
    global_spine = verification.get("global_spine_path")
    typer.echo(
        f"global_spine: {'present' if verification.get('global_spine_present') else 'missing'}"
    )
    typer.echo(f"global_spine_path: {global_spine or '<missing>'}")
    if verify_report is not None:
        typer.echo(f"verify_report: {verify_report['path']}")
    effective_errors = list((verify_report or verification).get("errors") or [])
    if effective_errors:
        for error in effective_errors:
            typer.echo(f"FAIL {error}")
        raise typer.Exit(1)


@release_app.command("startup-check")
def release_startup_check_cmd(
    env: Optional[str] = typer.Option(
        None, "--env", help="Release environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or ~/krw-ontology-data/releases.",
    ),
) -> None:
    """Run the lightweight MCP startup contract check against <env>/current."""
    resolved_env = normalize_ontology_env(env)
    resolved_releases_root = _resolve_configured_releases_root(releases_root, env=resolved_env)
    root = release_env_root(resolved_releases_root, resolved_env) / "current"
    verification = verify_release_startup_v3(
        root,
        env=resolved_env,
        require_current_symlink=True,
    )
    typer.echo(f"Release startup-check: {'ok' if verification['ok'] else 'failed'}")
    typer.echo(f"root: {verification['root']}")
    typer.echo(f"env: {verification.get('env') or '<missing>'}")
    typer.echo(f"release_id: {verification.get('release_id') or '<missing>'}")
    typer.echo(f"manifest: {verification.get('manifest_path') or '<missing>'}")
    typer.echo(f"index_layout: {verification.get('index_layout') or '<missing>'}")
    typer.echo(
        f"global_spine: {'present' if verification.get('global_spine_present') else 'missing'}"
    )
    typer.echo(f"global_spine_path: {verification.get('global_spine_path') or '<missing>'}")
    for error in verification["errors"]:
        typer.echo(f"FAIL {error}")
    if not verification["ok"]:
        raise typer.Exit(1)


@source_manifest_app.command("generate")
def source_manifest_generate_cmd(
    root: Path = typer.Option(..., "--root", help="Ontology artifact root."),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Manifest output path. Defaults to <root>/indexes/source_manifest.json.",
    ),
) -> None:
    """Generate the canonical source manifest used by production index builds."""
    from krw_ontology.agent_index import write_source_artifact_manifest

    _exit_if_path_mutates_current(root, "--root")
    _exit_if_path_mutates_current(output, "--output")
    try:
        manifest = write_source_artifact_manifest(root, manifest_path=output)
    except Exception as exc:
        typer.echo(f"FAILED source-manifest generate: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"Source manifest written: {manifest['path']}")
    typer.echo(f"manifest_hash: {manifest['manifest_hash']}")
    typer.echo(f"artifacts: {manifest['artifact_count']}")


@source_manifest_app.command("verify")
def source_manifest_verify_cmd(
    root: Path = typer.Option(..., "--root", help="Ontology artifact root."),
    manifest: Optional[Path] = typer.Option(
        None,
        "--manifest",
        help="Source manifest path. Defaults to <root>/indexes/source_manifest.json.",
    ),
) -> None:
    """Verify a canonical source manifest against current artifact bytes."""
    from krw_ontology.agent_index import verify_source_artifact_manifest

    result = verify_source_artifact_manifest(root, manifest_path=manifest)
    typer.echo(f"Source manifest verify: {'ok' if result['ok'] else 'failed'}")
    typer.echo(f"path: {result['path']}")
    typer.echo(f"manifest_hash: {result.get('manifest_hash') or '<missing>'}")
    typer.echo(f"artifacts: {result['artifact_count']}")
    for error in result.get("errors") or []:
        typer.echo(f"FAIL {error}")
    if not result["ok"]:
        raise typer.Exit(1)


@source_manifest_app.command("diff")
def source_manifest_diff_cmd(
    left: Path = typer.Argument(..., help="Older source manifest path."),
    right: Path = typer.Argument(..., help="Newer source manifest path."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Diff two source manifests by artifact content hash."""
    from krw_ontology.agent_index import diff_source_artifact_manifests

    try:
        diff = diff_source_artifact_manifests(left, right)
    except Exception as exc:
        typer.echo(f"FAILED source-manifest diff: {exc}")
        raise typer.Exit(1) from exc
    if json_output:
        typer.echo(json.dumps(diff, sort_keys=True))
        return
    summary = diff["summary"]
    typer.echo("Source manifest diff")
    typer.echo(f"left: {diff['left_path']}")
    typer.echo(f"right: {diff['right_path']}")
    typer.echo(
        "changes: "
        f"added={summary['added']} removed={summary['removed']} "
        f"changed={summary['changed']} unchanged={summary['unchanged']}"
    )
    for path in diff["added"][:20]:
        typer.echo(f"added: {path}")
    for path in diff["removed"][:20]:
        typer.echo(f"removed: {path}")
    for path in diff["changed"][:20]:
        typer.echo(f"changed: {path}")


@release_app.command("preview")
def release_preview_cmd(
    source_root: Optional[Path] = typer.Option(
        None,
        "--from-root",
        help="Source ontology root. Defaults to configured running-root.",
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or the built-in releases root.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Ontology environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Preview the production release transaction without materializing a candidate."""
    try:
        resolved_source_root, resolved_releases_root, resolved_env = (
            _resolve_release_command_defaults(
                source_root=source_root,
                releases_root=releases_root,
                env=env,
            )
        )
        preview = _release_preview_payload(
            source_root=resolved_source_root,
            releases_root=resolved_releases_root,
            env=resolved_env,
        )
    except Exception as exc:
        typer.echo(f"FAILED release preview: {exc}")
        raise typer.Exit(1) from exc
    if json_output:
        typer.echo(json.dumps(preview, sort_keys=True))
        return
    plan = preview["plan"]
    typer.echo("Release preview")
    typer.echo(f"env: {preview['env']}")
    typer.echo(f"source_root: {preview['source_root']}")
    typer.echo(f"current: {preview['current'] or '<missing>'}")
    typer.echo(f"no_op: {preview['no_op']}")
    typer.echo(f"source_manifest_hash: {preview['source_manifest']['manifest_hash']}")
    typer.echo(f"artifacts: {plan['artifact_count']}")
    typer.echo(f"dirty_artifacts: {plan['dirty_artifact_count']}")
    typer.echo(f"cached_fragments: {plan['cached_artifact_count']}")
    typer.echo(f"companies: {plan['company_count']}")
    typer.echo(f"cached_companies: {plan['cached_company_count']}")
    typer.echo(f"dirty_companies: {plan['dirty_company_count']}")
    typer.echo(
        f"dirty_tickers: {', '.join(plan['dirty_tickers']) if plan['dirty_tickers'] else '<none>'}"
    )
    typer.echo(f"layout: {plan['layout']}")


@release_app.command("deploy")
def release_deploy_cmd(
    source_root: Optional[Path] = typer.Option(
        None,
        "--from-root",
        help="Source ontology root. Defaults to configured running-root.",
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or the built-in releases root.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Ontology environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release_id: Optional[str] = typer.Option(
        None, "--release-id", help="Release id. Defaults to a timestamp id."
    ),
    force_release: bool = typer.Option(
        False,
        "--force-release",
        "--force",
        help="Create a release even when the source manifest matches current.",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Bypass v3 artifact, company shard, and spine fragment caches.",
    ),
) -> None:
    """Run the full production-safe local release pipeline and promote current."""
    resolved_source_root, resolved_releases_root, resolved_env = _resolve_release_command_defaults(
        source_root=source_root,
        releases_root=releases_root,
        env=env,
    )
    _run_full_root_release_command(
        source_root=resolved_source_root,
        releases_root=resolved_releases_root,
        env=resolved_env,
        release_id=release_id or _default_release_id(),
        promote=True,
        force_release=force_release,
        no_cache=no_cache,
        label="deploy",
    )


@release_app.command("force")
def release_force_cmd(
    source_root: Optional[Path] = typer.Option(
        None,
        "--from-root",
        help="Source ontology root. Defaults to configured running-root.",
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or the built-in releases root.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Ontology environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release_id: Optional[str] = typer.Option(
        None, "--release-id", help="Release id. Defaults to a timestamp id."
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Bypass v3 artifact, company shard, and spine fragment caches.",
    ),
    foreground: bool = typer.Option(
        False,
        "--foreground",
        help="Run in the foreground instead of starting a background release worker.",
    ),
) -> None:
    """Force a canonical local release from configured roots and promote current."""
    resolved_source_root, resolved_releases_root, resolved_env = _resolve_release_command_defaults(
        source_root=source_root,
        releases_root=releases_root,
        env=env,
    )
    resolved_release_id = release_id or _default_release_id()
    if not foreground:
        try:
            _start_release_build_worker(
                source_root=resolved_source_root,
                releases_root=resolved_releases_root,
                env=resolved_env,
                release_id=resolved_release_id,
                promote=True,
                force_release=True,
                no_cache=no_cache,
                label="force",
            )
        except Exception as exc:
            typer.echo(f"FAILED release force: {exc}")
            raise typer.Exit(1) from exc
        return
    _run_full_root_release_command(
        source_root=resolved_source_root,
        releases_root=resolved_releases_root,
        env=resolved_env,
        release_id=resolved_release_id,
        promote=True,
        force_release=True,
        no_cache=no_cache,
        label="force",
    )


@app.command("release-build-worker", hidden=True)
def release_build_worker_cmd(
    source_root: Path = typer.Option(..., "--from-root", help="Source ontology root."),
    releases_root: Path = typer.Option(..., "--releases-root", help="Local releases root."),
    env: str = typer.Option("dev", "--env", help="Ontology environment."),
    release_id: str = typer.Option(..., "--release-id", help="Release id."),
    promote: bool = typer.Option(True, "--promote/--no-promote"),
    force_release: bool = typer.Option(False, "--force-release/--no-force-release"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    label: str = typer.Option("release", "--label"),
) -> None:
    """Internal background worker for v3 release build/publish commands."""
    resolved_env = normalize_ontology_env(env)
    try:
        _write_release_worker_state(
            releases_root=releases_root,
            env=resolved_env,
            release_id=release_id,
            pid=os.getpid(),
            mode={
                "source_root": str(source_root.expanduser().resolve()),
                "promote": promote,
                "force_release": force_release,
                "no_cache": no_cache,
                "label": label,
                "layout": "global-spine-and-company-shards",
            },
        )
        typer.echo(
            f"[{_now_label()}] release worker started env={resolved_env} release_id={release_id} label={label}"
        )
        result = _publish_root_as_local_release(
            source_root=source_root,
            releases_root=releases_root,
            env=resolved_env,
            release_id=release_id,
            promote=promote,
            force_release=force_release,
            no_cache=no_cache,
            allow_prepared_release_root=True,
        )
        _update_release_worker_status(
            releases_root=releases_root,
            env=resolved_env,
            release_id=release_id,
            status="ready",
            extra={"result": result},
        )
        typer.echo(
            f"Release {label} completed: env={result['env']} "
            f"release_id={result['release_id']} promoted={result['promoted']}"
        )
        typer.echo(f"release_root: {result['release_root']}")
        typer.echo(f"global_spine: {result['global_spine_path']}")
        typer.echo(f"layout: {result.get('layout', '<unknown>')}")
        typer.echo(f"verify_report: {result['verify_report']}")
    except Exception as exc:
        _update_release_worker_status(
            releases_root=releases_root,
            env=resolved_env,
            release_id=release_id,
            status="failed",
            extra={"error": str(exc)},
        )
        typer.echo(f"FAILED release worker: {exc}")
        raise typer.Exit(1) from exc


@release_app.command("history")
def release_history_cmd(
    releases_root: Path = typer.Option(
        _default_releases_root(),
        "--releases-root",
        help="Local releases root.",
    ),
    env: str = typer.Option("dev", "--env", help="Ontology environment: dev, staging, or prod."),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum releases to print."),
    include_failed: bool = typer.Option(
        False, "--include-failed", help="Also show quarantined failed candidates."
    ),
) -> None:
    """Show recent release history with the current release marked."""
    resolved_env = normalize_ontology_env(env)
    env_root = release_env_root(releases_root, resolved_env).expanduser().resolve()
    current_id = current_release_id(env_root)
    releases = list_release_ids(env_root)[:limit]
    typer.echo(f"Release history: env={resolved_env} current={current_id or '<missing>'}")
    for release_id in releases:
        marker = " current" if release_id == current_id else ""
        typer.echo(f"release: {release_id}{marker}")
    if include_failed:
        failed_root = env_root / FAILED_RELEASE_DIRNAME
        failed = (
            [
                path.name
                for path in sorted(
                    failed_root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True
                )
                if path.is_dir()
            ]
            if failed_root.is_dir()
            else []
        )
        for release_id in failed[:limit]:
            typer.echo(f"failed: {release_id}")


@release_app.command("plan")
def release_plan_cmd(
    source_root: Optional[Path] = typer.Option(
        None,
        "--from-root",
        help="Source ontology root. Defaults to configured running-root.",
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or the built-in releases root.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Ontology environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Preview the v3 DAG as if all artifact, company, and spine caches are bypassed.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the full v3 DAG preview as JSON."
    ),
) -> None:
    """Plan the v3 immutable release DAG without writing release outputs."""
    try:
        resolved_source_root, resolved_releases_root, resolved_env = (
            _resolve_release_command_defaults(
                source_root=source_root,
                releases_root=releases_root,
                env=env,
            )
        )
        preview = _release_preview_payload(
            source_root=resolved_source_root,
            releases_root=resolved_releases_root,
            env=resolved_env,
            no_cache=no_cache,
        )
    except Exception as exc:
        typer.echo(f"FAILED release plan: {exc}")
        raise typer.Exit(1) from exc
    if json_output:
        _echo_json(preview)
        return
    plan = preview["plan"]
    dag = preview["dag"]
    typer.echo("Release plan")
    typer.echo(f"env: {preview['env']}")
    typer.echo(f"source_root: {preview['source_root']}")
    typer.echo(f"current: {preview['current'] or '<missing>'}")
    typer.echo(f"no_op: {preview['no_op']}")
    typer.echo(f"format: {dag['format']}")
    typer.echo(f"index_layout: {dag['index_layout']}")
    typer.echo(f"artifacts: {plan['artifact_count']}")
    typer.echo(f"companies: {dag['company_count']}")
    typer.echo(f"dirty_companies: {dag['dirty_company_count']}")
    typer.echo(f"cached_companies: {dag['cached_company_count']}")
    typer.echo(f"dirty_spine_fragments: {dag['dirty_spine_fragment_count']}")
    typer.echo(f"cached_spine_fragments: {dag['cached_spine_fragment_count']}")
    typer.echo(
        f"dirty_tickers: {', '.join(dag['dirty_tickers']) if dag['dirty_tickers'] else '<none>'}"
    )
    typer.echo(f"workers: {dag['worker_count']}")
    typer.echo(f"cache_root: {dag['cache_root']}")
    typer.echo("dag:")
    for node in dag["nodes"]:
        depends = ",".join(node.get("depends_on") or []) or "<none>"
        typer.echo(
            f"  {node['id']}: stage={node['stage']} status={node['status']} "
            f"cache_hit={node['cache_hit']} depends_on={depends}"
        )


@release_app.command("build")
def release_build_cmd(
    source_root: Optional[Path] = typer.Option(
        None,
        "--from-root",
        help="Source ontology root. Defaults to configured running-root.",
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or the built-in releases root.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Ontology environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release_id: Optional[str] = typer.Option(
        None, "--release-id", help="Release id. Defaults to a timestamp id."
    ),
    force_release: bool = typer.Option(
        False,
        "--force-release",
        "--force",
        help="Build a release even when the source manifest matches current.",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Bypass v3 artifact, company shard, and spine fragment caches.",
    ),
) -> None:
    """Build and verify a full-root immutable release without promoting current."""
    resolved_source_root, resolved_releases_root, resolved_env = _resolve_release_command_defaults(
        source_root=source_root,
        releases_root=releases_root,
        env=env,
    )
    _run_full_root_release_command(
        source_root=resolved_source_root,
        releases_root=resolved_releases_root,
        env=resolved_env,
        release_id=release_id or _default_release_id(),
        promote=False,
        force_release=force_release,
        no_cache=no_cache,
        label="build",
    )


@release_app.command("publish")
def release_publish_cmd(
    source_root: Optional[Path] = typer.Option(
        None,
        "--from-root",
        help="Source ontology root. Defaults to configured running-root.",
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or the built-in releases root.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Ontology environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release_id: Optional[str] = typer.Option(
        None, "--release-id", help="Release id. Defaults to a timestamp id."
    ),
    promote: bool = typer.Option(
        True, "--promote/--no-promote", help="Promote current after verification."
    ),
    force_release: bool = typer.Option(
        False,
        "--force-release",
        "--force",
        help="Create a release even when the source manifest matches current.",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Bypass v3 artifact, company shard, and spine fragment caches.",
    ),
) -> None:
    """Publish a full ontology root through one verified immutable transaction."""
    resolved_source_root, resolved_releases_root, resolved_env = _resolve_release_command_defaults(
        source_root=source_root,
        releases_root=releases_root,
        env=env,
    )
    _run_full_root_release_command(
        source_root=resolved_source_root,
        releases_root=resolved_releases_root,
        env=resolved_env,
        release_id=release_id or _default_release_id(),
        promote=promote,
        force_release=force_release,
        no_cache=no_cache,
        label="publish",
    )


@release_app.command("import-current")
def release_import_current_cmd(
    source_root: Optional[Path] = typer.Option(
        None,
        "--from-root",
        help="Existing mutable/stable ontology root. Defaults to configured running-root.",
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Local releases root. Defaults to configured publish-root or the built-in releases root.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Ontology environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release_id: Optional[str] = typer.Option(
        None, "--release-id", help="Release id. Defaults to a timestamp id."
    ),
) -> None:
    """Import an existing mutable root into a verified immutable release and promote it."""
    resolved_source_root, resolved_releases_root, resolved_env = _resolve_release_command_defaults(
        source_root=source_root,
        releases_root=releases_root,
        env=env,
    )
    _run_full_root_release_command(
        source_root=resolved_source_root,
        releases_root=resolved_releases_root,
        env=resolved_env,
        release_id=release_id or _default_release_id(),
        promote=True,
        force_release=True,
        no_cache=False,
        label="import-current",
    )


def _run_full_root_release_command(
    *,
    source_root: Path,
    releases_root: Path,
    env: str,
    release_id: str,
    promote: bool,
    force_release: bool,
    no_cache: bool,
    label: str,
) -> None:
    try:
        result = _publish_root_as_local_release(
            source_root=source_root,
            releases_root=releases_root,
            env=env,
            release_id=release_id,
            promote=promote,
            force_release=force_release,
            no_cache=no_cache,
        )
    except Exception as exc:
        typer.echo(f"FAILED release {label}: {exc}")
        raise typer.Exit(1) from exc
    if result["no_op"]:
        typer.echo(
            f"No-op release {label}: source manifest is unchanged; "
            f"current remains {result['release_id']}"
        )
        return
    typer.echo(
        f"Release {label} completed: env={result['env']} "
        f"release_id={result['release_id']} promoted={result['promoted']}"
    )
    typer.echo(f"release_root: {result['release_root']}")
    typer.echo(f"global_spine: {result['global_spine_path']}")
    typer.echo(f"layout: {result.get('layout', '<unknown>')}")
    typer.echo(f"verify_report: {result['verify_report']}")


def _start_release_build_worker(
    *,
    source_root: Path,
    releases_root: Path,
    env: str,
    release_id: str,
    promote: bool,
    force_release: bool,
    no_cache: bool,
    label: str,
) -> None:
    _assert_releases_root_not_nested_in_source(
        source_root=source_root,
        releases_root=releases_root,
    )
    paths = _release_command_paths(releases_root, env, release_id)
    _quarantine_stale_release_candidates(
        releases_root,
        env,
        reason=f"preflight stale release cleanup before {label}",
        exclude_release_ids={release_id},
    )
    if paths["release_root"].exists():
        raise FileExistsError(f"Release directory already exists: {paths['release_root']}")
    if not source_root.is_dir():
        raise FileNotFoundError(f"Source root not found: {source_root}")
    if not _release_root_has_ontology_artifacts(source_root):
        raise RuntimeError(f"Source root has no ontology artifacts under companies/: {source_root}")
    _release_disk_preflight(source_root=source_root, release_root=paths["release_root"])
    paths["log_path"].parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-c",
        "from krw_ontology.cli.main import app; app()",
        "release-build-worker",
        "--release-id",
        release_id,
        "--releases-root",
        str(releases_root),
        "--from-root",
        str(source_root),
        "--env",
        env,
        "--label",
        label,
    ]
    command.append("--promote" if promote else "--no-promote")
    command.append("--force-release" if force_release else "--no-force-release")
    if no_cache:
        command.append("--no-cache")
    with paths["log_path"].open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n[{_now_label()}] release {label} launching background worker\n")
        log_handle.flush()
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    paths["release_root"].mkdir(parents=True, exist_ok=True)
    paths["worker_pid_path"].write_text(str(process.pid), encoding="utf-8")
    typer.echo(f"Started release worker pid={process.pid}")
    typer.echo(f"release_id: {release_id}")
    typer.echo(f"env: {env}")
    typer.echo(f"source_root: {source_root}")
    typer.echo(f"release_root: {paths['release_root']}")
    typer.echo(f"global_spine: {paths['global_spine_path']}")
    typer.echo(f"log: {paths['log_path']}")
    typer.echo(f"progress: {paths['progress_path']}")
    typer.echo(f"watch: krw-ontology release watch {release_id}")


def _release_preview_payload(
    *,
    source_root: Path,
    releases_root: Path,
    env: str,
    no_cache: bool = False,
) -> dict[str, object]:
    from krw_ontology.agent_index import plan_spine_shard_release_outputs

    resolved_source = source_root.expanduser().resolve()
    _assert_releases_root_not_nested_in_source(
        source_root=resolved_source,
        releases_root=releases_root,
    )
    resolved_env = normalize_ontology_env(env)
    env_root = release_env_root(releases_root, resolved_env).expanduser().resolve()
    current = env_root / "current"
    with tempfile.TemporaryDirectory(prefix="krw-source-manifest-preview-") as tmp_dir:
        manifest_path = Path(tmp_dir) / "source_manifest.json"
        dag = plan_spine_shard_release_outputs(
            resolved_source,
            cache_root=env_root / ".index_fragment_cache",
            source_manifest_path=manifest_path,
            no_cache=no_cache,
        )
        source_manifest = dag["source_manifest"]
        source_manifest_hash = dag.get("source_manifest_hash")
        current_manifest_hash = (
            _current_v3_source_manifest_hash(current.resolve())
            if current.exists() or current.is_symlink()
            else None
        )
        return {
            "env": resolved_env,
            "source_root": str(resolved_source),
            "releases_root": str(releases_root.expanduser().resolve()),
            "current": current_release_id(env_root),
            "no_op": source_manifest_hash is not None
            and source_manifest_hash == current_manifest_hash,
            "source_manifest": source_manifest,
            "plan": _plan_preview_dict_from_v3_dag(dag),
            "dag": dag,
        }


def _plan_preview_dict(plan) -> dict[str, object]:
    return {
        "artifact_count": len(plan.items),
        "dirty_artifact_count": len(plan.dirty_items),
        "cached_artifact_count": len(plan.cached_items),
        "company_count": len(plan.company_items),
        "dirty_company_count": len(plan.dirty_company_items),
        "cached_company_count": len(plan.cached_company_items),
        "dirty_tickers": list(plan.dirty_tickers),
        "workers": plan.workers,
        "layout": plan.layout,
        "discovery_mode": plan.discovery_mode,
        "source_manifest_hash": plan.source_manifest_hash,
    }


def _plan_preview_dict_from_v3_dag(dag: Mapping[str, object]) -> dict[str, object]:
    return {
        "artifact_count": int(dag.get("artifact_count") or 0),
        "dirty_artifact_count": int(dag.get("artifact_count") or 0),
        "cached_artifact_count": 0,
        "company_count": int(dag.get("company_count") or 0),
        "dirty_company_count": int(dag.get("dirty_company_count") or 0),
        "cached_company_count": int(dag.get("cached_company_count") or 0),
        "dirty_tickers": list(dag.get("dirty_tickers") or []),
        "workers": int(dag.get("worker_count") or 0),
        "layout": str(dag.get("index_layout") or "global-spine-and-company-shards"),
        "discovery_mode": (
            (dag.get("plan") or {}) if isinstance(dag.get("plan"), Mapping) else {}
        ).get("discovery_mode"),
        "source_manifest_hash": dag.get("source_manifest_hash"),
    }


@release_app.command("status")
def release_status_cmd(
    release_id: Optional[str] = typer.Argument(
        None,
        help="Release id to inspect. Defaults to latest release candidate.",
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Path to releases root. Defaults to configured publish-root or built-in releases root.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Ontology environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
) -> None:
    """Show local env release current pointer, latest worker, and release list."""
    resolved_env = normalize_ontology_env(env)
    resolved_releases_root = _resolve_configured_releases_root(releases_root, env=resolved_env)
    env_root = release_env_root(resolved_releases_root, resolved_env)
    current_id = current_release_id(env_root)
    selected_release_id = release_id or _latest_release_candidate_id(
        resolved_releases_root, resolved_env
    )
    typer.echo("Release status")
    typer.echo(f"env: {resolved_env}")
    typer.echo(f"env_root: {env_root.expanduser().resolve()}")
    typer.echo(f"current: {current_id or '<missing>'}")
    if selected_release_id is not None:
        paths = _release_command_paths(resolved_releases_root, resolved_env, selected_release_id)
        pid = _release_worker_pid(resolved_releases_root, resolved_env, selected_release_id)
        state = (
            _release_worker_state(resolved_releases_root, resolved_env, selected_release_id) or {}
        )
        runtime = _release_worker_runtime_status(paths["release_root"], pid, state)
        running = bool(runtime["running"])
        typer.echo(f"selected: {selected_release_id}")
        typer.echo(
            f"worker: {'running' if running else 'stopped'}" + (f" pid={pid}" if pid else "")
        )
        typer.echo(f"worker_status: {runtime['status']}")
        if runtime["stale"]:
            typer.echo(f"stale_worker: true pid={runtime['stale_pid']}")
            typer.echo(f"cleanup: krw-ontology release cleanup-interrupted {selected_release_id}")
        typer.echo(f"release_root: {paths['release_root']}")
        typer.echo(
            f"global_spine: {'present' if paths['global_spine_path'].exists() else 'missing'} {paths['global_spine_path']}"
        )
        typer.echo(
            f"manifest: {'present' if (paths['release_root'] / RELEASE_MANIFEST_FILENAME).exists() else 'missing'}"
        )
        typer.echo(
            f"verify_report: {'present' if (paths['release_root'] / 'verify' / 'release_verify.json').exists() else 'missing'}"
        )
        typer.echo(f"log: {paths['log_path']}")
        _echo_release_progress_status(paths["progress_path"])
    releases = list_release_ids(env_root)
    if releases:
        typer.echo("releases:")
        for release_id in releases[:10]:
            marker = " current" if release_id == current_id else ""
            typer.echo(f"  - {release_id}{marker}")
    else:
        typer.echo("releases: <none>")


def _echo_release_progress_status(progress_path: Path) -> None:
    typer.echo(f"progress: {progress_path}")
    event = _latest_progress_event(progress_path)
    if event is None:
        typer.echo("progress_status: <none>")
        return
    typer.echo(_format_progress_status_line(event))
    details = event.get("details")
    if isinstance(details, Mapping) and "completed" in details and "total" in details:
        typer.echo(f"progress_count: {details.get('completed')}/{details.get('total')}")
    if event.get("ticker"):
        typer.echo(f"progress_ticker: {event['ticker']}")
    if event.get("output"):
        typer.echo(f"progress_output: {event['output']}")
    if event.get("error"):
        typer.echo(f"progress_error: {event['error']}")


def _latest_progress_event(progress_path: Path) -> dict[str, object] | None:
    events = _read_jsonl_objects(progress_path)
    return events[-1] if events else None


def _format_progress_status_line(event: Mapping[str, object]) -> str:
    status = str(event.get("status") or "<unknown>")
    stage = str(event.get("stage") or "<unknown>")
    node_id = str(event.get("node_id") or "<unknown>")
    return f"progress_status: {status} stage={stage} node={node_id}"


def _format_progress_watch_line(event: Mapping[str, object]) -> str:
    timestamp = str(event.get("timestamp") or _now_label())
    status = str(event.get("status") or "<unknown>")
    stage = str(event.get("stage") or "<unknown>")
    node_id = str(event.get("node_id") or "<unknown>")
    parts = [f"[{timestamp}] progress {stage}/{node_id}: {status}"]
    ticker = event.get("ticker")
    if ticker:
        parts.append(f"ticker={ticker}")
    details = event.get("details")
    if isinstance(details, Mapping) and "completed" in details and "total" in details:
        parts.append(f"count={details.get('completed')}/{details.get('total')}")
    cache_hit = event.get("cache_hit")
    if isinstance(cache_hit, bool):
        parts.append(f"cache_hit={cache_hit}")
    duration_ms = event.get("duration_ms")
    if isinstance(duration_ms, (int, float)):
        parts.append(f"duration_ms={int(duration_ms)}")
    error = event.get("error")
    if error:
        parts.append(f"error={error}")
    return " ".join(parts)


def _append_release_progress_event(
    progress_path: Path,
    *,
    release_root: Path,
    release_id: str,
    node_id: str,
    stage: str,
    status: str,
    output: Path | str | None = None,
    details: Mapping[str, object] | None = None,
    error: str | None = None,
    started_at: float | None = None,
) -> None:
    payload: dict[str, object] = {
        "format": "krw-ontology-v3-build-progress/v1",
        "release_id": release_id,
        "event": "node_status",
        "node_id": node_id,
        "stage": stage,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if output is not None:
        payload["output"] = _release_progress_output_label(release_root, output)
    if details:
        payload["details"] = dict(details)
    if error:
        payload["error"] = error
    if started_at is not None:
        payload["duration_ms"] = max(0, int((time.perf_counter() - started_at) * 1000))
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with progress_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _release_progress_output_label(release_root: Path, output: Path | str) -> str:
    if not isinstance(output, Path):
        return str(output)
    resolved_root = release_root.expanduser().resolve()
    resolved_output = output.expanduser().resolve()
    try:
        return str(resolved_output.relative_to(resolved_root))
    except ValueError:
        return str(resolved_output)


def _watch_release_log_and_progress(
    *,
    log_path: Path,
    progress_path: Path,
    lines: int,
    follow: bool,
) -> None:
    if log_path.exists():
        tail = _tail_text(log_path, lines)
        if tail:
            typer.echo(tail)
    if progress_path.exists():
        _echo_release_progress_status(progress_path)
    if not follow:
        return
    offsets = {
        log_path: log_path.stat().st_size if log_path.exists() else 0,
        progress_path: progress_path.stat().st_size if progress_path.exists() else 0,
    }
    while True:
        for path, kind in ((log_path, "log"), (progress_path, "progress")):
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offsets.get(path, 0))
                for line in handle:
                    raw = line.rstrip()
                    if not raw:
                        continue
                    if kind == "progress":
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            typer.echo(raw)
                            continue
                        if isinstance(event, Mapping):
                            typer.echo(_format_progress_watch_line(event))
                        else:
                            typer.echo(raw)
                    else:
                        typer.echo(raw)
                offsets[path] = handle.tell()
        time.sleep(1)


@release_app.command("watch")
def release_watch_cmd(
    release_id: Optional[str] = typer.Argument(
        None, help="Release id. Defaults to latest release candidate."
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Path to releases root. Defaults to configured publish-root or built-in releases root.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Ontology environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    lines: int = typer.Option(80, "--lines", min=1, help="Number of trailing lines to show first."),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Follow appended log output."),
) -> None:
    """Watch the v3 release worker log."""
    resolved_env = normalize_ontology_env(env)
    resolved_releases_root = _resolve_configured_releases_root(releases_root, env=resolved_env)
    selected_release_id = release_id or _latest_release_candidate_id(
        resolved_releases_root, resolved_env
    )
    if selected_release_id is None:
        typer.echo("No release candidate found.")
        raise typer.Exit(1)
    paths = _release_command_paths(resolved_releases_root, resolved_env, selected_release_id)
    if not paths["log_path"].exists() and not paths["progress_path"].exists():
        typer.echo(f"Log does not exist: {paths['log_path']}")
        raise typer.Exit(1)
    _watch_release_log_and_progress(
        log_path=paths["log_path"],
        progress_path=paths["progress_path"],
        lines=lines,
        follow=follow,
    )


@release_app.command("cancel")
def release_cancel_cmd(
    release_id: Optional[str] = typer.Argument(
        None, help="Release id. Defaults to latest release candidate."
    ),
    releases_root: Optional[Path] = typer.Option(
        None,
        "--releases-root",
        help="Path to releases root. Defaults to configured publish-root or built-in releases root.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Ontology environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
) -> None:
    """Request graceful cancellation of a running v3 release worker."""
    resolved_env = normalize_ontology_env(env)
    resolved_releases_root = _resolve_configured_releases_root(releases_root, env=resolved_env)
    selected_release_id = release_id or _latest_release_candidate_id(
        resolved_releases_root, resolved_env
    )
    if selected_release_id is None:
        typer.echo("No release candidate found.")
        raise typer.Exit(1)
    pid = _release_worker_pid(resolved_releases_root, resolved_env, selected_release_id)
    if pid is None:
        typer.echo(f"No worker pid found for release {selected_release_id}.")
        raise typer.Exit(1)
    if not is_pid_running(pid):
        typer.echo(f"Worker is not running: release={selected_release_id} pid={pid}")
        raise typer.Exit(1)
    _update_release_worker_status(
        releases_root=resolved_releases_root,
        env=resolved_env,
        release_id=selected_release_id,
        status="cancel_requested",
    )
    os.kill(pid, signal.SIGTERM)
    typer.echo(f"Cancel requested: release={selected_release_id} pid={pid}")


@release_app.command("list")
def release_list_cmd(
    releases_root: Path = typer.Option(..., "--releases-root", help="Local releases root."),
    env: str = typer.Option("dev", "--env", help="Ontology environment: dev, staging, or prod."),
    include_failed: bool = typer.Option(
        False,
        "--include-failed",
        help="Also list quarantined failed candidates.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List immutable releases and optionally quarantined failed candidates."""
    resolved_env = normalize_ontology_env(env)
    env_root = release_env_root(releases_root, resolved_env).expanduser().resolve()
    current_id = current_release_id(env_root)
    releases = list_release_ids(env_root)
    failed_root = env_root / FAILED_RELEASE_DIRNAME
    failed = (
        [
            path.name
            for path in sorted(
                failed_root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True
            )
            if path.is_dir()
        ]
        if include_failed and failed_root.is_dir()
        else []
    )
    payload = {
        "env": resolved_env,
        "env_root": str(env_root),
        "current": current_id,
        "releases": releases,
        "failed": failed,
    }
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return
    typer.echo(f"Release list: env={resolved_env} current={current_id or '<missing>'}")
    for release_id in releases:
        marker = " current" if release_id == current_id else ""
        typer.echo(f"release: {release_id}{marker}")
    for release_id in failed:
        typer.echo(f"failed: {release_id}")


@release_app.command("inspect")
def release_inspect_cmd(
    release_id: Optional[str] = typer.Argument(None, help="Release id. Defaults to current."),
    releases_root: Path = typer.Option(..., "--releases-root", help="Local releases root."),
    env: str = typer.Option("dev", "--env", help="Ontology environment: dev, staging, or prod."),
    failed: bool = typer.Option(False, "--failed", help="Inspect a quarantined failed candidate."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Inspect release manifest, verification reports, and build summary."""
    resolved_env = normalize_ontology_env(env)
    env_root = release_env_root(releases_root, resolved_env).expanduser().resolve()
    selected_id = release_id or current_release_id(env_root)
    if selected_id is None:
        typer.echo("No current release found.")
        raise typer.Exit(1)
    root = env_root / FAILED_RELEASE_DIRNAME / selected_id if failed else env_root / selected_id
    if not root.is_dir():
        typer.echo(f"Release not found: {root}")
        raise typer.Exit(1)
    manifest, manifest_path = load_release_manifest(root)
    verification = None if failed else verify_release_root(root, env=resolved_env)
    payload = {
        "env": resolved_env,
        "release_id": selected_id,
        "root": str(root),
        "failed": failed,
        "failure": _read_json_object(root / "failure.json"),
        "manifest_path": str(manifest_path) if manifest_path else None,
        "manifest": manifest,
        "verification": verification,
        "release_verify": _read_json_object(root / "verify" / "release_verify.json"),
        "build_summary": _read_json_object(root / "indexes" / "build_summary.json"),
        "events": _read_jsonl_objects(env_root / "events" / f"{selected_id}.jsonl"),
    }
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return
    typer.echo(f"Release inspect: env={resolved_env} release_id={selected_id}")
    typer.echo(f"root: {root}")
    typer.echo(f"failed: {failed}")
    typer.echo(f"manifest_format: {manifest.get('format') or '<missing>'}")
    typer.echo(f"manifest_status: {manifest.get('status') or '<missing>'}")
    if verification is not None:
        typer.echo(f"verification: {'ok' if verification['ok'] else 'failed'}")
        for error in verification.get("errors") or []:
            typer.echo(f"FAIL {error}")
    failure_payload = payload["failure"]
    if isinstance(failure_payload, dict):
        typer.echo(f"failure_action: {failure_payload.get('action')}")
        typer.echo(f"failure_error: {failure_payload.get('error')}")


@release_app.command("gc")
def release_gc_cmd(
    releases_root: Path = typer.Option(..., "--releases-root", help="Local releases root."),
    env: str = typer.Option("dev", "--env", help="Ontology environment: dev, staging, or prod."),
    keep: int = typer.Option(
        5, "--keep", min=1, help="Number of newest successful releases to retain."
    ),
    include_failed: bool = typer.Option(
        False,
        "--include-failed",
        help="Also delete quarantined failed candidates.",
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Delete candidates. Without this flag, only print a dry run."
    ),
) -> None:
    """Garbage-collect old local releases while always preserving current."""
    resolved_env = normalize_ontology_env(env)
    env_root = release_env_root(releases_root, resolved_env).expanduser().resolve()
    lock = FileProcessLock(env_root / "locks" / "cache_gc.lock")
    try:
        lock.acquire()
    except LockHeldError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    try:
        result = _execute_release_gc(env_root, keep=keep, include_failed=include_failed, yes=yes)
    finally:
        lock.release()
    candidates = result["candidates"]
    typer.echo(f"Release GC: {'delete' if yes else 'dry-run'} env={resolved_env}")
    typer.echo(f"current: {result['current'] or '<missing>'}")
    typer.echo(f"protected: {len(result['protected'])}")
    typer.echo(f"candidates: {len(candidates)}")
    for path in candidates:
        typer.echo(f"candidate: {path}")
    if not yes:
        return
    typer.echo(f"deleted: {result['candidate_count']}")


@release_cache_app.command("status")
def release_cache_status_cmd(
    releases_root: Optional[Path] = typer.Option(
        None, "--releases-root", help="Local releases root."
    ),
    env: str = typer.Option("dev", "--env", help="Ontology environment: dev, staging, or prod."),
    keep: str = typer.Option(
        "current",
        "--keep",
        help="Release cache generation to preserve: current, latest, or a concrete release id.",
    ),
    workers: Optional[int] = typer.Option(
        None, "--workers", min=1, help="Planned artifact compile worker count."
    ),
    limit: int = typer.Option(20, "--limit", min=0, help="Maximum candidate entries to print."),
) -> None:
    """Show release index cache reachability for the selected release."""
    target = _release_cache_target(releases_root=releases_root, env=env, keep=keep)
    snapshot = _v3_index_cache_snapshot(
        target["release_root"],
        target["cache_root"],
        workers=workers,
        source_manifest_path=None,
    )
    candidates = [
        entry
        for entry in snapshot["entries"]
        if entry.get("referenced") is False and not entry.get("missing")
    ]
    candidate_bytes = sum(int(entry.get("size_bytes") or 0) for entry in candidates)
    typer.echo("Release cache status")
    typer.echo(f"env: {target['env']}")
    typer.echo(f"keep: {target['keep']} release={target['release_id']}")
    typer.echo(f"release_root: {target['release_root']}")
    typer.echo(f"cache_root: {snapshot['cache_root']}")
    typer.echo(
        "entries: "
        f"total={snapshot['entry_count']} "
        f"referenced={snapshot['referenced_existing_count']} "
        f"missing_referenced={snapshot['missing_referenced_count']} "
        f"unreferenced={snapshot['unreferenced_count']}"
    )
    typer.echo(f"bytes: total={snapshot['total_size_bytes']} reclaimable={candidate_bytes}")
    for entry in candidates[:limit]:
        typer.echo(f"candidate: {entry['kind']} {entry['path']} ({entry['size_bytes']} bytes)")
    if len(candidates) > limit:
        typer.echo(f"... {len(candidates) - limit} more")


@release_cache_app.command("gc")
def release_cache_gc_cmd(
    releases_root: Optional[Path] = typer.Option(
        None, "--releases-root", help="Local releases root."
    ),
    env: str = typer.Option("dev", "--env", help="Ontology environment: dev, staging, or prod."),
    keep: str = typer.Option(
        "current",
        "--keep",
        help="Release cache generation to preserve: current, latest, or a concrete release id.",
    ),
    workers: Optional[int] = typer.Option(
        None, "--workers", min=1, help="Planned artifact compile worker count."
    ),
    include_unreferenced: bool = typer.Option(
        True,
        "--unreferenced/--no-unreferenced",
        help="Delete cache files not referenced by the selected release.",
    ),
    include_tmp: bool = typer.Option(
        True,
        "--tmp/--no-tmp",
        help="Delete orphan cache temp files older than --tmp-minutes.",
    ),
    tmp_minutes: int = typer.Option(
        60,
        "--tmp-minutes",
        min=1,
        help="Minimum age in minutes for orphan cache temp file deletion.",
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Delete candidates. Without this flag this is a dry run."
    ),
    limit: int = typer.Option(50, "--limit", min=0, help="Maximum candidate entries to print."),
) -> None:
    """Garbage-collect release index caches while preserving the selected release generation."""
    resolved_env = normalize_ontology_env(env)
    resolved_releases_root = _resolve_configured_releases_root(releases_root, env=resolved_env)
    env_root = release_env_root(resolved_releases_root, resolved_env).expanduser().resolve()
    result = _run_release_cache_gc_locked(
        env_root,
        keep_label=keep,
        include_unreferenced=include_unreferenced,
        include_tmp=include_tmp,
        tmp_minutes=tmp_minutes,
        yes=yes,
        workers=workers,
    )
    if result.get("status") == "skipped_lock_held":
        typer.echo(f"Release cache GC skipped: {result['lock_path']} is held by another gc run.")
        raise typer.Exit(1)
    candidates = result["candidates"]
    typer.echo(f"Release cache GC: {'deleted' if yes else 'dry-run'}")
    typer.echo(f"env: {result['env']}")
    typer.echo(f"keep: {result['keep']} release={result['release_id']}")
    typer.echo(f"release_root: {result['release_root']}")
    typer.echo(f"cache_root: {result['cache_root']}")
    typer.echo(
        "entries: "
        f"total={result['entry_count']} "
        f"referenced={result['referenced_existing_count']} "
        f"missing_referenced={result['missing_referenced_count']} "
        f"unreferenced={result['unreferenced_count']}"
    )
    typer.echo(f"candidates: {result['candidate_count']} bytes={result['candidate_bytes']}")
    typer.echo(f"deleted: {result['deleted_count']} bytes={result['deleted_bytes']}")
    for entry in candidates[:limit]:
        typer.echo(
            f"candidate: {entry.get('reason')} {entry['kind']} "
            f"{entry['path']} ({entry['size_bytes']} bytes)"
        )
    if len(candidates) > limit:
        typer.echo(f"... {len(candidates) - limit} more")


def _release_cache_target(
    *,
    releases_root: Path | None,
    env: str,
    keep: str,
) -> dict[str, Any]:
    resolved_env = normalize_ontology_env(env)
    resolved_releases_root = _resolve_configured_releases_root(releases_root, env=resolved_env)
    env_root = release_env_root(resolved_releases_root, resolved_env).expanduser().resolve()
    keep_label = keep.strip()
    if keep_label in ("", "current"):
        release_id = current_release_id(env_root)
        if not release_id:
            raise typer.BadParameter(f"No current release found under {env_root}")
        resolved_keep = "current"
    elif keep_label == "latest":
        release_ids = list_release_ids(env_root)
        if not release_ids:
            raise typer.BadParameter(f"No releases found under {env_root}")
        release_id = release_ids[0]
        resolved_keep = "latest"
    else:
        release_id = keep_label
        resolved_keep = "release-id"

    release_root = (env_root / release_id).expanduser().resolve()
    if not release_root.is_dir():
        raise typer.BadParameter(f"Release directory not found: {release_root}")
    return {
        "env": resolved_env,
        "keep": resolved_keep,
        "release_id": release_id,
        "release_root": release_root,
        "cache_root": env_root / ".index_fragment_cache",
    }


def _iter_release_cache_tmp_files(cache_root: Path) -> list[Path]:
    resolved_cache_root = cache_root.expanduser().resolve()
    if not resolved_cache_root.is_dir():
        return []
    paths: list[Path] = []
    for path in resolved_cache_root.rglob("*.tmp"):
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if not resolved.is_file() or not _path_inside(resolved, resolved_cache_root):
            continue
        paths.append(resolved)
    return sorted(paths)


def _delete_release_cache_candidate(path: Path, cache_root: Path) -> int:
    resolved_cache_root = cache_root.expanduser().resolve()
    resolved_path = path.expanduser().resolve()
    if not _path_inside(resolved_path, resolved_cache_root):
        raise RuntimeError(f"Refusing to delete cache file outside cache root: {resolved_path}")
    if resolved_path.suffix not in (".sqlite", ".tmp"):
        raise RuntimeError(f"Refusing to delete non-cache candidate: {resolved_path}")
    size = 0
    for candidate in _sqlite_cache_file_family(resolved_path):
        try:
            size += candidate.stat().st_size
        except FileNotFoundError:
            continue
        candidate.unlink()
    return size


def _sqlite_cache_file_family(path: Path) -> tuple[Path, ...]:
    if path.suffix == ".sqlite":
        return (
            path,
            Path(f"{path}-wal"),
            Path(f"{path}-shm"),
            Path(f"{path}-journal"),
            path.with_name(path.name + ".seal.json"),
            path.with_name(path.name + ".verify.json"),
        )
    return (path,)


def _prune_empty_release_cache_dirs(cache_root: Path) -> None:
    resolved_cache_root = cache_root.expanduser().resolve()
    if not resolved_cache_root.is_dir():
        return
    for path in sorted(resolved_cache_root.rglob("*"), reverse=True):
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            pass


def _path_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _execute_release_gc(
    env_root: Path, *, keep: int, include_failed: bool = False, yes: bool
) -> dict[str, Any]:
    env_root = env_root.expanduser().resolve()
    current_id = current_release_id(env_root)
    releases = list_release_ids(env_root)
    protected = set(releases[:keep])
    if current_id:
        protected.add(current_id)
    candidates = [env_root / release_id for release_id in releases if release_id not in protected]
    if include_failed and (env_root / FAILED_RELEASE_DIRNAME).is_dir():
        candidates.extend(
            path for path in (env_root / FAILED_RELEASE_DIRNAME).iterdir() if path.is_dir()
        )
    deleted: list[str] = []
    if yes:
        for path in candidates:
            if path.is_symlink() or not path.is_dir():
                raise RuntimeError(f"Refusing to delete non-directory release candidate: {path}")
            shutil.rmtree(path)
            deleted.append(path.name)
    return {
        "keep": keep,
        "current": current_id,
        "protected": sorted(protected),
        "candidates": [str(path) for path in candidates],
        "candidate_count": len(candidates),
        "deleted": deleted,
    }


def _execute_release_cache_gc(
    env_root: Path,
    *,
    keep_label: str = "current",
    include_unreferenced: bool = True,
    include_tmp: bool = True,
    tmp_minutes: int = 60,
    yes: bool,
    workers: int | None = None,
) -> dict[str, Any]:
    env_root = env_root.expanduser().resolve()
    # Layout is <releases_root>/<env>, so the releases root is the env root's parent.
    releases_root = env_root.parent
    target = _release_cache_target(releases_root=releases_root, env=env_root.name, keep=keep_label)
    snapshot = _v3_index_cache_snapshot(
        target["release_root"], target["cache_root"], workers=workers, source_manifest_path=None
    )
    candidates: list[dict[str, Any]] = []
    if include_unreferenced:
        candidates.extend(
            {**entry, "reason": "unreferenced"}
            for entry in snapshot["entries"]
            if entry.get("referenced") is False and not entry.get("missing")
        )
    if include_tmp:
        now = time.time()
        minimum_age_seconds = tmp_minutes * 60
        for path in _iter_release_cache_tmp_files(target["cache_root"]):
            try:
                stat = path.stat()
            except OSError:
                continue
            if max(0, int(now - stat.st_mtime)) < minimum_age_seconds:
                continue
            candidates.append(
                {
                    "kind": "tmp",
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "reason": f"tmp_older_than_{tmp_minutes}m",
                }
            )
    deleted_count = 0
    deleted_bytes = 0
    if yes:
        for entry in candidates:
            removed = _delete_release_cache_candidate(
                Path(str(entry["path"])), target["cache_root"]
            )
            deleted_count += 1 if removed >= 0 else 0
            deleted_bytes += max(0, removed)
        _prune_empty_release_cache_dirs(target["cache_root"])
    return {
        "env": target["env"],
        "keep": target["keep"],
        "release_id": target["release_id"],
        "release_root": str(target["release_root"]),
        "cache_root": str(target["cache_root"]),
        "entry_count": snapshot["entry_count"],
        "referenced_existing_count": snapshot["referenced_existing_count"],
        "missing_referenced_count": snapshot["missing_referenced_count"],
        "unreferenced_count": snapshot["unreferenced_count"],
        "candidate_count": len(candidates),
        "candidate_bytes": sum(int(entry.get("size_bytes") or 0) for entry in candidates),
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
        "candidates": candidates,
    }


def _run_release_cache_gc_locked(env_root: Path, **kwargs: Any) -> dict[str, Any]:
    lock = FileProcessLock(env_root.expanduser().resolve() / "locks" / "cache_gc.lock")
    try:
        lock.acquire()
    except LockHeldError:
        return {
            "status": "skipped_lock_held",
            "env_root": str(env_root),
            "lock_path": str(lock.path),
        }
    try:
        result = _execute_release_cache_gc(env_root, **kwargs)
    finally:
        lock.release()
    result["status"] = "ok"
    return result


def _run_post_promote_gc(
    *,
    releases_root: Path,
    env: str,
    release_id: str,
    env_root: Path,
    progress_path: Path,
    release_root: Path,
    started_at: float,
) -> dict[str, Any]:
    """Run non-fatal cache/release GC immediately after a successful promote."""
    summary: dict[str, Any] = {"status": "skipped_disabled"}
    try:
        if _release_auto_gc_enabled():
            keep = _resolve_release_keep(None)
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="post_promote_gc",
                stage="gc",
                status="started",
                output=env_root / ".index_fragment_cache",
                details={"keep": keep},
            )
            lock = FileProcessLock(env_root.expanduser().resolve() / "locks" / "cache_gc.lock")
            try:
                lock.acquire()
            except LockHeldError:
                summary = {
                    "status": "skipped_lock_held",
                    "env_root": str(env_root),
                    "lock_path": str(lock.path),
                    "keep": keep,
                }
            else:
                try:
                    cache_result = _execute_release_cache_gc(env_root, yes=True)
                    release_result = _execute_release_gc(env_root, keep=keep, yes=True)
                finally:
                    lock.release()
                summary = {
                    "status": "ok",
                    "cache_deleted_bytes": cache_result.get("deleted_bytes", 0),
                    "release_deleted": release_result.get("deleted", []),
                    "keep": keep,
                }
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="post_promote_gc",
                stage="gc",
                status="complete",
                output=env_root / ".index_fragment_cache",
                details={"summary": summary},
                started_at=started_at,
            )
    except Exception as exc:  # gc 절대 빌드 실패로 전파 금지
        summary = {"status": "failed", "error": str(exc)}
        try:
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="post_promote_gc",
                stage="gc",
                status="failed",
                output=env_root / ".index_fragment_cache",
                details={"error": str(exc)},
                started_at=started_at,
            )
        except Exception:
            pass
    return summary


def _read_json_object(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_jsonl_objects(path: Path) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    payloads: list[dict[str, object]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


@release_app.command("promote")
def release_promote_cmd(
    release_id: str = typer.Argument(..., help="Release id to promote to env current."),
    releases_root: Path = typer.Option(
        ...,
        "--releases-root",
        help="Path to releases root. Accepts either /data/releases or /data/releases/<env>.",
    ),
    env: str = typer.Option("dev", "--env", help="Ontology environment: dev, staging, or prod."),
    reload_command: Optional[str] = typer.Option(
        None,
        "--reload-command",
        help="Optional local command to run after current is switched.",
    ),
    health_url: Optional[str] = typer.Option(
        None,
        "--health-url",
        help="Optional local health URL. JSON must include ok=true and the promoted release_id.",
    ),
    health_timeout: float = typer.Option(
        10.0,
        "--health-timeout",
        min=0.1,
        help="Seconds to wait for the local health URL.",
    ),
    lightweight: bool = typer.Option(
        False,
        "--lightweight",
        help=(
            "Use manifest, immutable-seal, and release-contract verification only. "
            "Skips the default full deep scan of every shard."
        ),
    ),
) -> None:
    """Atomically point env current to a verified release.

    Default promotion performs a full deep verification.  --lightweight is an
    explicit local-operator choice for a release whose immutable provenance is
    already trusted; it still verifies the manifest, immutable seals, and v3
    release contract before switching current.
    """
    env_root = release_env_root(releases_root, env)
    previous_release_id = current_release_id(env_root)
    if previous_release_id == release_id:
        typer.echo(f"FAILED release promote: release {release_id} is already current")
        raise typer.Exit(1)
    try:
        preverified = None
        if lightweight:
            release_dir = release_env_root(releases_root, env) / release_id
            preverified = verify_release_root(release_dir, env=env, deep=False)
            if not preverified["ok"]:
                raise ValueError(
                    "Lightweight release verification failed: "
                    + ", ".join(preverified["errors"])
                )
        result = promote_local_release(
            releases_root,
            env=env,
            release_id=release_id,
            preverified=preverified,
        )
        hook_result = _run_local_release_post_switch_hooks(
            releases_root=releases_root,
            env=env,
            activated_release_id=result["release_id"],
            previous_release_id=previous_release_id,
            action="promote",
            reload_command=reload_command,
            health_url=health_url,
            health_timeout=health_timeout,
        )
    except Exception as exc:
        typer.echo(f"FAILED release promote: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"Release promoted: env={result['env']} release_id={result['release_id']}")
    typer.echo(f"current: {result['current']}")
    typer.echo(f"verify_report: {result['verify_report']}")
    typer.echo(f"event_log: {result['event_log']}")
    typer.echo(f"release_event_log: {result['release_event_log']}")
    _print_local_release_hook_result(hook_result)
    for error in result.get("event_log_errors") or []:
        typer.echo(f"WARN event_log: {error}")


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
    reload_command: Optional[str] = typer.Option(
        None,
        "--reload-command",
        help="Optional local command to run after current is switched.",
    ),
    health_url: Optional[str] = typer.Option(
        None,
        "--health-url",
        help="Optional local health URL. JSON must include ok=true and the rollback release_id.",
    ),
    health_timeout: float = typer.Option(
        10.0,
        "--health-timeout",
        min=0.1,
        help="Seconds to wait for the local health URL.",
    ),
) -> None:
    """Rollback local env current to a requested or previous release."""
    previous_release_id = current_release_id(release_env_root(releases_root, env))
    try:
        result = rollback_local_release(releases_root, env=env, release_id=release_id)
        hook_result = _run_local_release_post_switch_hooks(
            releases_root=releases_root,
            env=env,
            activated_release_id=result["release_id"],
            previous_release_id=previous_release_id,
            action="rollback",
            reload_command=reload_command,
            health_url=health_url,
            health_timeout=health_timeout,
        )
    except Exception as exc:
        typer.echo(f"FAILED release rollback: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"Release rollback activated: env={result['env']} release_id={result['release_id']}")
    typer.echo(f"current: {result['current']}")
    typer.echo(f"verify_report: {result['verify_report']}")
    typer.echo(f"event_log: {result['event_log']}")
    typer.echo(f"release_event_log: {result['release_event_log']}")
    _print_local_release_hook_result(hook_result)
    for error in result.get("event_log_errors") or []:
        typer.echo(f"WARN event_log: {error}")


def _run_local_release_post_switch_hooks(
    *,
    releases_root: Path,
    env: str,
    activated_release_id: str,
    previous_release_id: str | None,
    action: str,
    reload_command: str | None,
    health_url: str | None,
    health_timeout: float,
) -> dict[str, object]:
    try:
        return _run_local_release_hooks(
            release_id=activated_release_id,
            reload_command=reload_command,
            health_url=health_url,
            health_timeout=health_timeout,
        )
    except Exception as exc:
        if previous_release_id and previous_release_id != activated_release_id:
            try:
                promote_local_release(
                    releases_root,
                    env=env,
                    release_id=previous_release_id,
                    action=f"{action}_hook_failure_restore",
                )
                _run_local_release_hooks(
                    release_id=previous_release_id,
                    reload_command=reload_command,
                    health_url=health_url,
                    health_timeout=health_timeout,
                )
            except Exception as restore_exc:
                raise RuntimeError(
                    f"{action} hook failed after activating {activated_release_id}; "
                    f"restore to previous release {previous_release_id} also failed: {restore_exc}; "
                    f"original hook failure: {exc}"
                ) from exc
            raise RuntimeError(
                f"{action} hook failed after activating {activated_release_id}; "
                f"restored previous release {previous_release_id}: {exc}"
            ) from exc
        raise RuntimeError(
            f"{action} hook failed after activating {activated_release_id}; "
            f"no previous release was available to restore: {exc}"
        ) from exc


def _run_local_release_hooks(
    *,
    release_id: str,
    reload_command: str | None,
    health_url: str | None,
    health_timeout: float,
) -> dict[str, object]:
    result: dict[str, object] = {
        "reload_ran": False,
        "health_checked": False,
        "health_url": health_url,
    }
    if reload_command:
        _run_local_reload_command(reload_command)
        result["reload_ran"] = True
    if health_url:
        _check_local_release_health(
            health_url=health_url,
            expected_release_id=release_id,
            timeout=health_timeout,
        )
        result["health_checked"] = True
    return result


def _run_local_reload_command(command: str) -> None:
    result = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError("reload command failed" + (f": {detail}" if detail else ""))


def _check_local_release_health(
    *, health_url: str, expected_release_id: str, timeout: float
) -> None:
    response = httpx.get(health_url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("health response must be a JSON object")
    if payload.get("ok") is not True:
        raise RuntimeError("health response ok is not true")
    actual_release_id = payload.get("release_id")
    if actual_release_id != expected_release_id:
        raise RuntimeError(
            f"health release_id mismatch: expected={expected_release_id} actual={actual_release_id}"
        )


def _print_local_release_hook_result(result: dict[str, object]) -> None:
    if result.get("reload_ran"):
        typer.echo("reload: ok")
    if result.get("health_checked"):
        typer.echo(f"health: ok {result.get('health_url')}")


@release_app.command("export-web-catalog")
def release_export_web_catalog_cmd(
    root: Path = typer.Option(..., "--root", help="Existing release root to export from."),
    env: Optional[str] = typer.Option(None, "--env", help="Expected ontology environment."),
    out: Path = typer.Option(..., "--out", help="Output web_catalog.json path."),
) -> None:
    """Export a read-only compact web catalog JSON from an existing release."""
    _exit_if_path_mutates_current(out, "--out")
    try:
        catalog = write_web_catalog(root, out, env=env)
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
    document_type: str = typer.Option(
        "10-K", "--document-type", help="Document type (10-K or 10-Q)"
    ),
    latest: bool = typer.Option(False, "--latest", help="Use most recent filing"),
    period: Optional[str] = typer.Option(
        None, "--period", help="Explicit period override (e.g., FY2025)"
    ),
    force: bool = typer.Option(False, "--force", help="Re-process even if content hash matches"),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="Override default output directory"
    ),
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
    _exit_if_path_mutates_current(output_root, "--output-dir")
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
    document_type: str = typer.Option(
        "10-K", "--document-type", help="Document type (10-K or 10-Q)"
    ),
    latest: bool = typer.Option(True, "--latest/--no-latest", help="Use most recent filing"),
    force: bool = typer.Option(
        True, "--force/--no-force", help="Re-process even if checkpoints exist"
    ),
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
    _exit_if_path_mutates_current(root, "--output-dir")
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
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] FAILED ticker={ticker}: {exc}"
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
    refresh_index: bool = typer.Option(
        True,
        "--refresh-index/--no-refresh-index",
        help="Refresh v3 global spine and company shard indexes in the running root after the batch.",
    ),
    publish_root: Optional[Path] = typer.Option(
        None,
        "--publish-root",
        help=(
            "Local releases root for publishing completed tickers through a release transaction."
        ),
    ),
    publish_env: str = typer.Option(
        "dev",
        "--publish-env",
        help="Release environment for publish: dev, staging, or prod.",
    ),
    release_id: Optional[str] = typer.Option(
        None,
        "--release-id",
        help="Release id to create when publishing. Defaults to a timestamp id.",
    ),
    force_release: bool = typer.Option(
        False,
        "--force-release",
        help="Create and promote a release even when the source manifest is unchanged.",
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
    from krw_ontology.config.settings import PipelineConfig
    from krw_ontology.pipeline.stages.build_company_context import build_company_context
    from krw_ontology.pipeline.orchestrator import run_pipeline
    from krw_ontology.pipeline.research_plan import discover_research_filing_targets

    run_tickers = [ticker.upper() for ticker in tickers]
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    _exit_if_path_mutates_current(output_root, "--root")
    resolved_publish_root = resolve_publish_root(publish_root)
    publish_releases_root: Path | None = None
    publish_target_env = normalize_ontology_env(publish_env)
    if resolved_publish_root is not None:
        publish_releases_root, publish_target_env = _resolve_release_publish_config(
            resolved_publish_root,
            publish_env,
        )
    if pilot and publish_releases_root is not None:
        typer.echo(
            "Refusing --pilot with --publish-root. Pilot artifacts are for development only."
        )
        raise typer.Exit(1)
    output_root.mkdir(parents=True, exist_ok=True)
    if publish_releases_root is not None:
        publish_releases_root.mkdir(parents=True, exist_ok=True)
    config = PipelineConfig.load()
    failures: list[tuple[str, str, str]] = []
    stopped_after_failure = False
    index_refresh_failure: Exception | None = None
    publish_ready_tickers: list[str] = []

    typer.echo(f"OUTPUT_ROOT={output_root}")
    if publish_releases_root is not None:
        typer.echo(f"PUBLISH_ROOT={publish_releases_root}")
        typer.echo(f"PUBLISH_ENV={publish_target_env}")
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
                    failures.append(
                        (target.ticker, f"{target.document_type} {target.period}", str(exc))
                    )
                    typer.echo(
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] FAILED {label}: {exc}"
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

            if publish_releases_root is not None:
                if ticker_failed:
                    typer.echo(f"Skipping publish for {ticker} because the ticker had failures.")
                    continue
                publish_ready_tickers.append(ticker)
                typer.echo(f"Queued completed ticker {ticker} for release publish.")

        if (
            publish_releases_root is not None
            and publish_ready_tickers
            and not stopped_after_failure
        ):
            typer.echo("")
            typer.echo(
                "Publishing completed ticker batch through release transaction: "
                + ", ".join(publish_ready_tickers)
            )
            try:
                publish_result = _publish_tickers_as_release(
                    source_root=output_root,
                    releases_root=publish_releases_root,
                    tickers=publish_ready_tickers,
                    env=publish_target_env,
                    release_id=release_id,
                    promote=True,
                    force_release=force_release,
                )
            except Exception as exc:
                failures.append((",".join(publish_ready_tickers), "publish", str(exc)))
                typer.echo(f"FAILED ticker batch stage=publish: {exc}")
                if not continue_on_error:
                    stopped_after_failure = True
            else:
                if publish_result["no_op"]:
                    typer.echo(
                        f"No-op release publish: source manifest is unchanged; "
                        f"current remains {publish_result['release_id']}"
                    )
                else:
                    for ticker in publish_ready_tickers:
                        typer.echo(f"Published {ticker} to {publish_result['release_root']}")
                    typer.echo(
                        f"Release promoted: env={publish_result['env']} "
                        f"release_id={publish_result['release_id']}"
                    )
                    typer.echo(
                        f"index_layout: {publish_result.get('layout') or GLOBAL_SPINE_LAYOUT}"
                    )
                    typer.echo(f"global_spine: {publish_result['global_spine_path']}")
                    typer.echo(f"Release verify report: {publish_result['verify_report']}")
                    _echo_v3_release_build_summary(publish_result["totals"])
    finally:
        typer.echo("")
        if not refresh_index:
            typer.echo("V3 index refresh skipped")
        else:
            typer.echo("Refreshing v3 index...")
            try:
                result = _build_v3_indexes_for_mutable_root(
                    output_root,
                    release_id_hint="research-pipeline-refresh",
                )
            except Exception as exc:
                index_refresh_failure = exc
                typer.echo(f"V3 index refresh failed: {exc}")
            else:
                _echo_v3_index_refresh_result(result)

    typer.echo("")
    typer.echo(f"OUTPUT_ROOT={output_root}")
    if failures:
        typer.echo("Failures:")
        for ticker, stage, reason in failures:
            typer.echo(f"- {ticker} {stage}: {reason}")
    if failures or index_refresh_failure is not None:
        raise typer.Exit(1)


def _now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _queue_emit(store: PipelineQueue, job: QueueJob, message: str) -> None:
    line = f"[{_now_label()}] {message}"
    typer.echo(line)
    store.append_job_log(job.job_id, line)


@dataclass(frozen=True)
class _QueueIndexRefreshTarget:
    root: Path


@dataclass(frozen=True)
class _QueueReleasePublishTarget:
    releases_root: Path
    env: str
    ticker: str
    job_id: str


@dataclass
class _QueueReleasePublishBatch:
    releases_root: Path
    env: str
    tickers: list[str]
    job_ids: list[str]


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


def _path_points_at_current_release(path: Path) -> bool:
    """Return whether a path addresses an active immutable release."""
    expanded = path.expanduser().absolute()
    candidates = [expanded, *expanded.parents]
    for candidate in candidates:
        if candidate.name == "current" and candidate.parent.name in ALLOWED_ONTOLOGY_ENVS:
            return True

        env_root = candidate.parent
        if env_root.name not in ALLOWED_ONTOLOGY_ENVS:
            continue
        current = env_root / "current"
        try:
            if (
                current.is_symlink()
                and candidate.exists()
                and candidate.resolve() == current.resolve()
            ):
                return True
        except OSError:
            pass

    return False


def _assert_path_not_current_release(path: Path | None, label: str) -> None:
    if path is None or not _path_points_at_current_release(path):
        return
    raise ValueError(
        f"Refusing {label}={path}: current is an immutable release pointer. "
        "Build a candidate release and promote the verified release instead."
    )


def _assert_queue_path_not_prod_current(path: Path | None, label: str) -> None:
    if path is None:
        return
    if _path_points_at_prod_current(path):
        raise ValueError(
            f"Refusing {label}={path}: prod/current is an immutable release pointer. "
            "Build in a dev/staging release directory and promote the verified release instead."
        )
    _assert_path_not_current_release(path, label)


def _exit_if_queue_path_mutates_prod_current(path: Path | None, label: str) -> None:
    try:
        _assert_queue_path_not_prod_current(path, label)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc


def _exit_if_path_mutates_current(path: Path | None, label: str) -> None:
    try:
        _assert_path_not_current_release(path, label)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc


def _resolve_release_publish_config(
    publish_root: Path, env: str | None = "dev"
) -> tuple[Path, str]:
    """Resolve publish config to (releases_root, env).

    Queue jobs historically stored either a stable root or a concrete prepared
    release root. The release transaction path treats --publish-root as the
    releases root, but this resolver keeps existing jobs usable by recognizing
    <releases-root>/<env> and <releases-root>/<env>/<release-id> shapes.
    """
    resolved = publish_root.expanduser().resolve()
    _assert_queue_path_not_prod_current(resolved, "--publish-root")
    requested_env = normalize_ontology_env(env)
    inferred_env = "dev"
    releases_root = resolved
    if resolved.name in ALLOWED_ONTOLOGY_ENVS:
        releases_root = resolved.parent
        inferred_env = normalize_ontology_env(resolved.name)
    elif resolved.parent.name in ALLOWED_ONTOLOGY_ENVS:
        releases_root = resolved.parent.parent
        inferred_env = normalize_ontology_env(resolved.parent.name)
    resolved_env = inferred_env if requested_env == "dev" else requested_env
    return releases_root, resolved_env


def _queue_publish_and_defer_index(
    *,
    store: PipelineQueue,
    job: QueueJob,
    output_root: Path,
    refresh_index: bool,
) -> _QueueReleasePublishTarget | _QueueIndexRefreshTarget | None:
    _assert_queue_path_not_prod_current(output_root, "--root")
    if job.publish_root:
        releases_root, env = _resolve_release_publish_config(Path(job.publish_root))
        _queue_emit(
            store,
            job,
            f"Queued {job.ticker} for release publish env={env} releases_root={releases_root}",
        )
        return _QueueReleasePublishTarget(
            releases_root=releases_root,
            env=env,
            ticker=job.ticker,
            job_id=job.job_id,
        )

    _queue_emit(
        store,
        job,
        (
            "No publish root configured; staging v3 index refresh deferred until batch completion"
            if refresh_index
            else "No publish root configured; staging v3 index refresh skipped by default"
        ),
    )
    return _QueueIndexRefreshTarget(root=output_root) if refresh_index else None


def _queue_refresh_pending_indexes(
    *,
    refresh_targets: dict[str, Path],
    publish_prod: bool,
    refresh_index_func=_build_v3_indexes_for_mutable_root,
) -> None:
    if not refresh_targets:
        return
    typer.echo(f"[{_now_label()}] Refreshing {len(refresh_targets)} pending queue v3 index root(s)")
    for root in list(refresh_targets.values()):
        _assert_queue_path_not_prod_current(root, "index root")
        typer.echo(f"[{_now_label()}] Refreshing v3 index root={root}")
        with FileProcessLock(PipelineQueue(root).publish_lock_path):
            index_result = refresh_index_func(root, release_id_hint="queue-refresh")
        typer.echo(f"[{_now_label()}] V3 index refreshed: {index_result['global_spine_path']}")
        _echo_v3_release_build_summary(index_result.get("totals"))
        if publish_prod:
            typer.echo(f"[{_now_label()}] Publishing rebuilt root to prod from {root}")
            prod_result = _publish_prod_root(stable_root=root)
            typer.echo(
                f"[{_now_label()}] Prod release activated: "
                f"release={prod_result['release_id']} "
                f"host={prod_result['host']} "
                f"remote_root={prod_result['remote_root']}"
            )
    refresh_targets.clear()


def _queue_refresh_pending_staging_indexes(
    *,
    refresh_targets: dict[str, _QueueIndexRefreshTarget],
    refresh_index_func=_build_v3_indexes_for_mutable_root,
) -> None:
    staging_targets = {key: target.root for key, target in refresh_targets.items()}
    _queue_refresh_pending_indexes(
        refresh_targets=staging_targets,
        publish_prod=False,
        refresh_index_func=refresh_index_func,
    )
    refresh_targets.clear()


def _queue_publish_pending_releases(
    *,
    store: PipelineQueue,
    publish_targets: dict[str, _QueueReleasePublishBatch],
    publish_prod: bool,
) -> None:
    if not publish_targets:
        return
    typer.echo(
        f"[{_now_label()}] Publishing {len(publish_targets)} pending queue release batch(es)"
    )
    for batch in list(publish_targets.values()):
        unique_tickers = list(dict.fromkeys(batch.tickers))
        typer.echo(
            f"[{_now_label()}] Publishing queue release "
            f"env={batch.env} releases_root={batch.releases_root} tickers={','.join(unique_tickers)}"
        )
        try:
            result = _publish_tickers_as_release(
                source_root=store.root,
                releases_root=batch.releases_root,
                tickers=unique_tickers,
                env=batch.env,
                promote=True,
            )
        except Exception as exc:
            for job_id in batch.job_ids:
                job = store.load_job(job_id)
                _queue_emit(store, job, f"FAILED release publish: {exc}")
                store.append_event("publish_failed", job, {"error": str(exc)})
            raise

        if result["no_op"]:
            typer.echo(
                f"[{_now_label()}] Queue release no-op: source manifest unchanged; "
                f"current remains {result['release_id']}"
            )
        else:
            typer.echo(
                f"[{_now_label()}] Queue release promoted: "
                f"env={result['env']} release={result['release_id']} root={result['release_root']}"
            )
            typer.echo(
                f"[{_now_label()}] V3 indexes built: "
                f"layout={result.get('layout') or GLOBAL_SPINE_LAYOUT} "
                f"global_spine={result['global_spine_path']}"
            )
            _echo_v3_release_build_summary(result["totals"])
            typer.echo(f"[{_now_label()}] Release verify report: {result['verify_report']}")
        for job_id in batch.job_ids:
            job = store.load_job(job_id)
            _queue_emit(
                store,
                job,
                f"Release {'no-op' if result['no_op'] else 'published'} "
                f"env={result['env']} release={result['release_id']} "
                f"root={result['release_root']} verify_report={result['verify_report']}",
            )
            store.append_event(
                "publish_noop" if result["no_op"] else "publish_succeeded",
                job,
                {
                    "env": result["env"],
                    "release_id": result["release_id"],
                    "release_root": result["release_root"],
                    "global_spine_path": result["global_spine_path"],
                    "verify_report": result["verify_report"],
                },
            )
        if publish_prod and not result["no_op"]:
            typer.echo(
                f"[{_now_label()}] Publishing verified release to prod from {result['release_root']}"
            )
            prod_result = _publish_prod_root(stable_root=Path(str(result["release_root"])))
            typer.echo(
                f"[{_now_label()}] Prod release activated: "
                f"release={prod_result['release_id']} "
                f"host={prod_result['host']} "
                f"remote_root={prod_result['remote_root']}"
            )
    publish_targets.clear()


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
    refresh_index: bool = False,
) -> _QueueReleasePublishTarget | _QueueIndexRefreshTarget | None:
    from krw_ontology.config.settings import PipelineConfig
    from krw_ontology.pipeline.orchestrator import run_pipeline
    from krw_ontology.pipeline.research_plan import discover_research_filing_targets
    from krw_ontology.pipeline.stages.build_company_context import build_company_context

    job = store.mark_running(job)
    _queue_emit(store, job, f"START job={job.job_id} type={job.job_type} ticker={job.ticker}")
    deferred_target: _QueueReleasePublishTarget | _QueueIndexRefreshTarget | None = None
    try:
        _assert_queue_path_not_prod_current(output_root, "--root")
        if publish_prod and not job.publish_root:
            raise ValueError(
                "--publish-prod requires jobs with a release publish root. "
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
                filing_label = (
                    f"{job.ticker} {document_type} {'latest' if job.latest else filing_period}"
                )
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
        deferred_target = _queue_publish_and_defer_index(
            store=store,
            job=job,
            output_root=output_root,
            refresh_index=refresh_index,
        )
    except Exception as exc:
        store.mark_failed(job, str(exc))
        _queue_emit(store, job, f"FAILED job={job.job_id} ticker={job.ticker}: {exc}")
        return None

    store.mark_succeeded(job)
    _queue_emit(store, job, f"SUCCEEDED job={job.job_id} ticker={job.ticker}")
    return deferred_target


@queue_app.command(
    "add",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue add CVX XOM COP\n"
        "  krw-ontology queue add CVX --years 3 --force\n"
        "  krw-ontology queue add LNG --root /path/to/running --publish-root /path/to/releases\n\n"
        "A job is ticker-level: one queued ticker expands to the configured 10-K/10-Q research set, "
        "then builds company context and publishes through a verified release transaction."
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
        help="Local releases root for verified queue publish batches.",
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
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
    _exit_if_queue_path_mutates_prod_current(stable_root, "--publish-root")
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
        "--publish-root /path/to/releases\n\n"
        "A filing update job runs only the selected filing(s), then rebuilds company context "
        "and publishes through a verified release transaction."
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
        help="Local releases root for verified queue publish batches.",
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
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
    _exit_if_queue_path_mutates_prod_current(stable_root, "--publish-root")
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
    )
    target_label = "latest" if latest else ", ".join(selected_periods)
    typer.echo(f"Queued update {job.ticker} job={job.job_id} {document_type} {target_label}")
    typer.echo("Added 1 job(s)")


@queue_app.command(
    "retarget-publish",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue retarget-publish --publish-root /data/releases\n"
        "  krw-ontology queue retarget-publish --dry-run\n\n"
        "By default this rewrites pending jobs only. It moves queued publish intent "
        "onto a release-native publish root."
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
        help="New local releases root. Defaults to configured publish-root.",
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
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show changes without rewriting job files."
    ),
) -> None:
    """Retarget queued job publish paths to a release directory."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    stable_root = resolve_publish_root(publish_root)
    if stable_root is None:
        typer.echo("Set publish-root or pass --publish-root.")
        raise typer.Exit(1)
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
    _exit_if_queue_path_mutates_prod_current(stable_root, "--publish-root")

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
        statuses=wanted_statuses,
        dry_run=dry_run,
    )
    action = "would retarget" if dry_run else "retargeted"
    typer.echo(
        f"Queue publish paths {action}: changed={result['changed']} scanned={result['scanned']}"
    )
    typer.echo(f"publish_root: {stable_root}")


@queue_app.command(
    "run",
    epilog=(
        "Examples:\n"
        "  krw-ontology queue run\n"
        "  krw-ontology queue run --watch\n"
        "  krw-ontology queue run --watch --refresh-index\n"
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
            "After the queue batch creates and verifies a local release, upload that release "
            "to prod and atomically activate it."
        ),
    ),
    refresh_index: bool = typer.Option(
        False,
        "--refresh-index/--no-refresh-index",
        help=(
            "Refresh v3 global spine and company shard indexes after drained/stopped batches "
            "that do not publish. Release publish batches always build and verify their release indexes."
        ),
    ),
) -> None:
    """Run queued ticker jobs in the foreground."""
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    _exit_if_queue_path_mutates_prod_current(output_root, "--root")
    output_root.mkdir(parents=True, exist_ok=True)
    store = PipelineQueue(output_root)
    store.ensure_dirs()
    effective_refresh_index = refresh_index

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
                    "refresh_index": effective_refresh_index,
                },
            )
            typer.echo(f"[{_now_label()}] Queue worker started root={output_root}")
            processed = 0
            pending_refresh_targets: dict[str, _QueueIndexRefreshTarget] = {}
            pending_publish_targets: dict[str, _QueueReleasePublishBatch] = {}

            def flush_pending_targets() -> None:
                if pending_publish_targets:
                    batch_count = len(pending_publish_targets)
                    ticker_count = sum(
                        len(list(dict.fromkeys(batch.tickers)))
                        for batch in pending_publish_targets.values()
                    )
                    typer.echo(
                        f"[{_now_label()}] Skipping automatic queue release publish "
                        f"for {ticker_count} ticker(s) in {batch_count} batch(es); "
                        "run `krw-ontology release publish-dev` manually when ready"
                    )
                    pending_publish_targets.clear()
                _queue_refresh_pending_staging_indexes(
                    refresh_targets=pending_refresh_targets,
                )

            while True:
                if store.stop_requested():
                    flush_pending_targets()
                    typer.echo(f"[{_now_label()}] Stop requested; worker exiting")
                    break
                job = store.next_pending_job()
                if job is None:
                    flush_pending_targets()
                    if not watch:
                        typer.echo(f"[{_now_label()}] Queue drained")
                        break
                    time.sleep(poll_interval)
                    continue

                with FileProcessLock(store.source_mutation_lock_path):
                    deferred_target = _process_queue_job(
                        store,
                        job,
                        output_root,
                        publish_prod=publish_prod,
                        refresh_index=effective_refresh_index,
                    )
                if isinstance(deferred_target, _QueueReleasePublishTarget):
                    key = f"{deferred_target.env}:{deferred_target.releases_root}"
                    batch = pending_publish_targets.setdefault(
                        key,
                        _QueueReleasePublishBatch(
                            releases_root=deferred_target.releases_root,
                            env=deferred_target.env,
                            tickers=[],
                            job_ids=[],
                        ),
                    )
                    batch.tickers.append(deferred_target.ticker)
                    batch.job_ids.append(deferred_target.job_id)
                elif isinstance(deferred_target, _QueueIndexRefreshTarget):
                    pending_refresh_targets[str(deferred_target.root)] = deferred_target
                processed += 1
                if max_jobs is not None and processed >= max_jobs:
                    flush_pending_targets()
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
        "  krw-ontology queue start --refresh-index\n"
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
        help=("Start the worker in mode that uploads each verified local queue release to prod."),
    ),
    refresh_index: bool = typer.Option(
        False,
        "--refresh-index/--no-refresh-index",
        help=("Start the worker in mode that refreshes v3 indexes after non-publish batches."),
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
    if refresh_index:
        command.append("--refresh-index")
    with store.worker_log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n[{_now_label()}] queue-start launching background worker\n")
        log_handle.flush()
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
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
        typer.echo(
            f"Stop requested. Queue worker pid={pid} is not running; cleared stale pid file."
        )
        return

    typer.echo(
        f"Stop requested for queue worker pid={pid}. It will exit before starting the next job."
    )
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
        '  krw-ontology queue recover-stale --mark-failed --reason "worker killed"\n\n'
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
        '  krw-ontology queue cancel <job-id> --reason "no longer needed"\n\n'
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
            f"- {job.status} {_queue_job_description(job)} attempts={job.attempts} job={job.job_id}"
        )
        if job.error:
            typer.echo(f"  error={job.error}")


def _tail_text(path: Path, lines: int) -> str:
    if lines <= 0:
        return ""
    content = path.read_text(encoding="utf-8")
    return "\n".join(content.splitlines()[-lines:])


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


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
    refresh_index: bool = typer.Option(
        True,
        "--refresh-index/--no-refresh-index",
        help="Refresh v3 global spine and company shard indexes after writing company context artifacts.",
    ),
) -> None:
    """Build company-level profile and temporal artifacts from existing filings."""
    from krw_ontology.pipeline.stages.build_company_context import build_company_context

    ticker = ticker.upper()
    output_root = resolve_ontology_root(root)
    _exit_if_path_mutates_current(output_root, "--root")
    result = build_company_context(output_root, ticker)
    typer.echo(f"Company context built: {result['artifact_index_path']}")
    typer.echo(f"Counts: {result['counts']}")
    if refresh_index:
        index_result = _build_v3_indexes_for_mutable_root(
            output_root,
            release_id_hint=f"company-context-{ticker}",
        )
        _echo_v3_index_refresh_result(index_result)


@app.command("update-ticker")
def update_ticker_cmd(
    ticker: str = typer.Argument(..., help="Ticker symbol to update."),
    document_type: str = typer.Option(
        "10-Q", "--document-type", help="Document type to update (10-K or 10-Q)."
    ),
    periods: Optional[list[str]] = typer.Option(
        None,
        "--period",
        help=(
            "Explicit period to update, e.g. FY2026Q1. Repeat this option to update "
            "multiple periods for the ticker before publishing."
        ),
    ),
    latest: bool = typer.Option(
        False, "--latest", help="Update the latest filing for the selected document type."
    ),
    force: bool = typer.Option(
        False, "--force/--no-force", help="Re-process even if checkpoints exist."
    ),
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
        help="Publish the completed ticker through a verified release transaction after context rebuild.",
    ),
    publish_root: Optional[Path] = typer.Option(
        None,
        "--publish-root",
        help="Local releases root. Required unless --no-publish is set.",
    ),
    publish_env: str = typer.Option(
        "dev",
        "--publish-env",
        help="Release environment for publish: dev, staging, or prod.",
    ),
    release_id: Optional[str] = typer.Option(
        None,
        "--release-id",
        help="Release id to create when publishing. Defaults to a timestamp id.",
    ),
    force_release: bool = typer.Option(
        False,
        "--force-release",
        help="Create and promote a release even when the source manifest is unchanged.",
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

    from krw_ontology.pipeline.orchestrator import run_pipeline
    from krw_ontology.pipeline.stages.build_company_context import build_company_context

    ticker = ticker.upper()
    output_root = resolve_running_root(root, fallback_to_cwd=False)
    _exit_if_path_mutates_current(output_root, "--root")
    publish_releases_root: Path | None = None
    publish_target_env = normalize_ontology_env(publish_env)
    if resolved_publish_root is not None:
        publish_releases_root, publish_target_env = _resolve_release_publish_config(
            resolved_publish_root,
            publish_env,
        )
    output_root.mkdir(parents=True, exist_ok=True)
    if publish_releases_root is not None:
        publish_releases_root.mkdir(parents=True, exist_ok=True)
    filing_periods: list[Optional[str]] = [None] if latest else selected_periods
    target_label = "latest" if latest else ", ".join(selected_periods)
    label = f"{ticker} {document_type} {target_label}"
    typer.echo(f"OUTPUT_ROOT={output_root}")
    if publish_releases_root is not None:
        typer.echo(f"PUBLISH_ROOT={publish_releases_root}")
        typer.echo(f"PUBLISH_ENV={publish_target_env}")
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

    assert publish_releases_root is not None
    typer.echo(f"Publishing {ticker} through release transaction...")
    try:
        publish_result = _publish_tickers_as_release(
            source_root=output_root,
            releases_root=publish_releases_root,
            tickers=[ticker],
            env=publish_target_env,
            release_id=release_id,
            promote=True,
            force_release=force_release,
        )
    except Exception as exc:
        typer.echo(f"FAILED {ticker} stage=publish: {exc}")
        raise typer.Exit(1) from exc

    totals = publish_result["totals"]
    if publish_result["no_op"]:
        typer.echo(
            f"No-op release publish: source manifest is unchanged; "
            f"current remains {publish_result['release_id']}"
        )
        typer.echo(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] END update {label}")
        return
    typer.echo(f"Published {ticker} to {publish_result['release_root']}")
    typer.echo(
        f"Release promoted: env={publish_result['env']} release_id={publish_result['release_id']}"
    )
    typer.echo(f"index_layout: {publish_result.get('layout') or GLOBAL_SPINE_LAYOUT}")
    typer.echo(f"global_spine: {publish_result['global_spine_path']}")
    typer.echo(f"Release verify report: {publish_result['verify_report']}")
    _echo_v3_release_build_summary(totals)
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
        help="Local releases root. The command creates <to-root>/<env>/<release-id> and promotes current.",
    ),
    env: Optional[str] = typer.Option(
        None, "--env", help="Release environment. Defaults to KRW_ONTOLOGY_ENV or dev."
    ),
    release_id: Optional[str] = typer.Option(
        None,
        "--release-id",
        help="Release id to create. Defaults to a timestamp id.",
    ),
    force_release: bool = typer.Option(
        False,
        "--force-release",
        help="Create and promote a release even when the source manifest is unchanged.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be published without copying files.",
    ),
) -> None:
    """Publish completed ticker artifacts through an immutable release transaction."""
    source_root = resolve_ontology_root(from_root)
    releases_root, target_env = _resolve_release_publish_config(to_root, env)
    run_tickers = [ticker.upper() for ticker in tickers]

    if dry_run:
        for ticker in run_tickers:
            source_dir = source_root / "companies" / ticker
            typer.echo(
                f"Publishing {ticker}: {source_dir} -> "
                f"{release_env_root(releases_root, target_env) / '<release-id>' / 'companies' / ticker}"
            )
        typer.echo("Dry run complete; no files changed.")
        return

    try:
        result = _publish_tickers_as_release(
            source_root=source_root,
            releases_root=releases_root,
            tickers=run_tickers,
            env=target_env,
            release_id=release_id,
            promote=True,
            force_release=force_release,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"FAILED release publish: {exc}")
        raise typer.Exit(1) from exc

    totals = result["totals"]
    if result["no_op"]:
        typer.echo(
            f"No-op release publish: source manifest is unchanged; "
            f"current remains {result['release_id']}"
        )
        typer.echo(f"Current release root: {result['release_root']}")
        return
    typer.echo(f"Release published: env={result['env']} release_id={result['release_id']}")
    typer.echo(f"Release root: {result['release_root']}")
    typer.echo(f"index_layout: {result.get('layout') or GLOBAL_SPINE_LAYOUT}")
    typer.echo(f"global_spine: {result['global_spine_path']}")
    typer.echo(f"Release verify report: {result['verify_report']}")
    _echo_v3_release_build_summary(totals)
    typer.echo(f"Published {', '.join(run_tickers)} to {result['release_root']}")


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
    _exit_if_path_mutates_current(output_root, "--root")

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
    _exit_if_path_mutates_current(output_root, "--root")

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
    resolved_reload_command = (
        reload_command if reload_command is not None else config.prod_reload_command
    )
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


def _resolve_release_keep(keep_releases: int | None) -> int:
    config = load_cli_config()
    keep_value = keep_releases
    if keep_value is None and config.release_keep_releases:
        try:
            keep_value = int(config.release_keep_releases)
        except ValueError as exc:
            raise ValueError("release-keep-releases must be an integer") from exc
    if keep_value is None:
        keep_value = 1
    if keep_value < 1:
        raise ValueError("release keep releases must be at least 1")
    return keep_value


def _release_auto_gc_enabled() -> bool:
    env_flag = os.environ.get("KRW_RELEASE_AUTO_GC", "").strip().lower()
    if env_flag in {"0", "false", "no", "off"}:
        return False
    config = load_cli_config()
    return (config.release_auto_gc or "1").strip().lower() not in {"0", "false", "no", "off"}


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
        "verify",
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
    with tempfile.TemporaryDirectory(prefix="krw-prod-release-") as tmp_dir:
        candidate_root = Path(tmp_dir) / release_id
        _materialize_verified_prod_release(stable_root, candidate_root, release_id)
        with tarfile.open(bundle_path, "w:gz") as archive:
            for child in sorted(candidate_root.iterdir()):
                archive.add(child, arcname=child.name, recursive=True)


def _materialize_verified_prod_release(
    stable_root: Path, candidate_root: Path, release_id: str
) -> None:
    _materialize_prod_bundle_root(stable_root, candidate_root)
    inherit_spine_verification_seal(
        stable_root / "indexes" / "global_spine.sqlite",
        candidate_root / "indexes" / "global_spine.sqlite",
        details={"materialization": "prod-bundle"},
    )
    global_spine_sha256 = read_spine_verification_sha256(
        candidate_root / "indexes" / "global_spine.sqlite"
    )
    rebind_router_sidecar_release(
        candidate_root / ROUTER_SIDECAR_RELATIVE_PATH,
        release_id=release_id,
        expected_global_spine_sha256=global_spine_sha256,
        trusted_source_path=stable_root / ROUTER_SIDECAR_RELATIVE_PATH,
    )
    rebind_router_coherence_release(
        candidate_root / ROUTER_COHERENCE_RELATIVE_PATH,
        release_id=release_id,
        expected_global_spine_sha256=global_spine_sha256,
        trusted_source_path=stable_root / ROUTER_COHERENCE_RELATIVE_PATH,
    )
    write_release_manifest_v3(
        candidate_root,
        release_id=release_id,
        env="prod",
        source_root=stable_root,
    )
    verification = verify_release_root(
        candidate_root,
        env="prod",
    )
    if not verification["ok"]:
        raise RuntimeError(
            f"Prod bundle release verify failed: {', '.join(verification['errors'])}"
        )
    write_release_verification_report(
        candidate_root,
        env="prod",
        verification=verification,
    )


def _build_prod_release_delta_bundle(
    candidate_root: Path,
    bundle_path: Path,
    *,
    remote_files: dict[str, dict[str, object]],
) -> dict[str, object]:
    candidate_files = _local_release_file_map(candidate_root)
    changed_paths = sorted(
        path
        for path, metadata in candidate_files.items()
        if (remote_files.get(path) or {}).get("sha256") != metadata.get("sha256")
    )
    removed_paths = sorted(set(remote_files) - set(candidate_files))
    delta_manifest = {
        "format": "krw-ontology-release-delta/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "changed": changed_paths,
        "changed_files": {path: candidate_files[path] for path in changed_paths},
        "removed": removed_paths,
        "candidate_file_count": len(candidate_files),
        "remote_file_count": len(remote_files),
    }
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_bytes = json.dumps(delta_manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with tarfile.open(bundle_path, "w:gz") as archive:
        manifest_info = tarfile.TarInfo(".krw_delta_manifest.json")
        manifest_info.size = len(manifest_bytes)
        manifest_info.mtime = int(time.time())
        archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
        for relative_path in changed_paths:
            archive.add(candidate_root / relative_path, arcname=relative_path, recursive=False)
    return {
        "bundle_path": str(bundle_path),
        "changed_file_count": len(changed_paths),
        "removed_file_count": len(removed_paths),
        "candidate_file_count": len(candidate_files),
        "remote_file_count": len(remote_files),
        "changed_paths": changed_paths,
        "removed_paths": removed_paths,
    }


def _local_release_file_map(root: Path) -> dict[str, dict[str, object]]:
    files: dict[str, dict[str, object]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root).as_posix()
        files[relative_path] = {
            "sha256": _file_sha256(path),
            "size_bytes": path.stat().st_size,
        }
    return files


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _materialize_prod_bundle_root(stable_root: Path, candidate_root: Path) -> None:
    candidate_root.mkdir(parents=True, exist_ok=True)
    for child in sorted(stable_root.iterdir()):
        if not _bundle_filter(child):
            continue
        if _is_legacy_agent_index_file_name(child.name):
            continue
        target = candidate_root / child.name
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(child, target, ignore=_ignore_legacy_agent_index_files)
        else:
            shutil.copy2(child, target)
    _remove_legacy_agent_index_files(candidate_root)
    inherit_spine_verification_seal(
        stable_root / "indexes" / "global_spine.sqlite",
        candidate_root / "indexes" / "global_spine.sqlite",
        details={"materialization": "prod-bundle-copy"},
    )
    inherit_immutable_sqlite_cache_seal(
        stable_root / ROUTER_SIDECAR_RELATIVE_PATH,
        candidate_root / ROUTER_SIDECAR_RELATIVE_PATH,
        kind="router_sidecar",
        role="router sidecar",
        details={"materialization": "prod-bundle-copy"},
    )
    inherit_immutable_sqlite_cache_seal(
        stable_root / ROUTER_COHERENCE_RELATIVE_PATH,
        candidate_root / ROUTER_COHERENCE_RELATIVE_PATH,
        kind="router_coherence",
        role="router coherence index",
        details={"materialization": "prod-bundle-copy"},
    )


def _run_checked(
    command: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess:
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
if [ -f "$ROOT/current/indexes/global_spine.sqlite" ]; then
  printf 'global_spine_present=yes\\n'
else
  printf 'global_spine_present=no\\n'
fi
	if [ -f "$ROOT/current/manifest.json" ]; then
	  printf 'manifest_present=yes\\n'
	  printf 'manifest_file=manifest.json\\n'
	  if grep -q '"format": "krw-ontology-release/v3"' "$ROOT/current/manifest.json"; then
	    printf 'manifest_format=v3\\n'
	  else
	    printf 'manifest_format=unsupported\\n'
	  fi
	else
	  printf 'manifest_present=no\\n'
	  printf 'manifest_file=\\n'
	  printf 'manifest_format=missing\\n'
fi
if [ -d "$ROOT/releases" ]; then
  for release_dir in $(cd "$ROOT/releases" && ls -1dt */ 2>/dev/null || true); do
    printf 'release=%s\\n' "${{release_dir%/}}"
  done
fi
"""


def _prod_doctor_script(*, remote_root: str, need_curl: bool) -> str:
    root_q = shlex.quote(remote_root)
    required = "tar ln mv rm mkdir ls readlink xargs grep sed" + (" curl" if need_curl else "")
    return f"""set -eu
ROOT={root_q}
REQUIRED={shlex.quote(required)}
MISSING=""
for cmd in $REQUIRED; do
  command -v "$cmd" >/dev/null 2>&1 || MISSING="$MISSING $cmd"
done
printf 'required_commands_missing=%s\\n' "${{MISSING# }}"
PYTHON_BIN="$(command -v python3 || command -v python || true)"
printf 'python_bin=%s\\n' "$PYTHON_BIN"
if [ -n "$PYTHON_BIN" ] && "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import sqlite3
PY
then
  printf 'python_sqlite3=yes\\n'
else
  printf 'python_sqlite3=no\\n'
fi
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


def _fetch_remote_current_file_map(
    *,
    host: str,
    remote_root: str,
) -> dict[str, dict[str, object]]:
    script = f"""set -eu
ROOT={shlex.quote(remote_root)}
PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON_BIN" ]; then echo '{{"files":{{}}}}'; exit 0; fi
"$PYTHON_BIN" - "$ROOT" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
current = root / "current"
if not current.exists():
    print(json.dumps({{"files": {{}}}}, sort_keys=True))
    sys.exit(0)
try:
    target = current.resolve()
except OSError:
    print(json.dumps({{"files": {{}}}}, sort_keys=True))
    sys.exit(0)
if not target.is_dir():
    print(json.dumps({{"files": {{}}}}, sort_keys=True))
    sys.exit(0)
files = {{}}
for path in sorted(target.rglob("*"), key=lambda item: item.relative_to(target).as_posix()):
    if not path.is_file():
        continue
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    files[path.relative_to(target).as_posix()] = {{
        "sha256": digest.hexdigest(),
        "size_bytes": path.stat().st_size,
    }}
print(json.dumps({{"files": files}}, sort_keys=True))
PY
"""
    completed = _run_checked(["ssh", host, "sh", "-s"], input_text=script)
    payload = json.loads(completed.stdout or '{"files":{}}')
    files = payload.get("files") if isinstance(payload, dict) else {}
    if not isinstance(files, dict):
        return {}
    return {
        str(path): dict(metadata) for path, metadata in files.items() if isinstance(metadata, dict)
    }


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
    typer.echo(
        f"Global spine: {'present' if status.get('global_spine_present') == 'yes' else 'missing'}"
    )
    typer.echo(
        f"Release manifest: {'present' if status.get('manifest_present') == 'yes' else 'missing'}"
    )
    typer.echo(f"Manifest format: {status.get('manifest_format') or '<unknown>'}")
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
    typer.echo(f"Local source root: {result['stable_root']}")
    typer.echo(f"Host: {result['host']}")
    typer.echo(f"Remote root: {result['remote_root']}")
    typer.echo(f"Current release: {_current_status_label(pre_status)}")
    typer.echo(f"New release: {release_path}")
    upload_mode = result.get("upload_mode") or "bundle"
    if upload_mode == "delta":
        typer.echo(
            "Action: upload delta bundle, reconstruct new release, atomically point current to new release"
        )
        if result.get("changed_file_count") is not None:
            typer.echo(
                "Delta: "
                f"changed_files={result['changed_file_count']} "
                f"removed_files={result.get('removed_file_count') or 0}"
            )
    else:
        typer.echo(
            "Action: upload bundle, extract new release, atomically point current to new release"
        )
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
    return (
        """set -eu
ROOT=__KRW_ROOT__
RELEASE_ID=__KRW_RELEASE_ID__
RELOAD_COMMAND=__KRW_RELOAD_COMMAND__
HEALTH_URL=__KRW_HEALTH_URL__
KEEP_RELEASES=__KRW_KEEP_RELEASES__
BUNDLE="$ROOT/incoming/$RELEASE_ID.tar.gz"
DELTA_BUNDLE="$ROOT/incoming/$RELEASE_ID.delta.tar.gz"
mkdir -p "$ROOT/incoming" "$ROOT/releases"
rm -rf "$ROOT/releases/$RELEASE_ID.tmp" "$ROOT/releases/$RELEASE_ID"
mkdir -p "$ROOT/releases/$RELEASE_ID.tmp"
if [ -f "$DELTA_BUNDLE" ]; then
  CURRENT_TARGET="$(readlink "$ROOT/current" 2>/dev/null || true)"
  if [ -n "$CURRENT_TARGET" ] && [ -d "$ROOT/$CURRENT_TARGET" ]; then
    tar -C "$ROOT/$CURRENT_TARGET" -cf - . | tar -C "$ROOT/releases/$RELEASE_ID.tmp" -xf -
  fi
  if tar -tzf "$DELTA_BUNDLE" .krw_delta_manifest.json >/dev/null 2>&1; then
    tar -xzf "$DELTA_BUNDLE" -C "$ROOT/releases/$RELEASE_ID.tmp" .krw_delta_manifest.json
    PYTHON_BIN="$(command -v python3 || command -v python || true)"
    if [ -z "$PYTHON_BIN" ]; then echo "python3 missing for remote delta application" >&2; exit 1; fi
    "$PYTHON_BIN" - "$ROOT/releases/$RELEASE_ID.tmp" <<'PY'
import json
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
manifest_path = root / ".krw_delta_manifest.json"
try:
    manifest = json.loads(manifest_path.read_text())
except FileNotFoundError:
    manifest = {}
for raw_path in manifest.get("removed", []):
    if not isinstance(raw_path, str) or not raw_path:
        continue
    target = (root / raw_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        print("delta remove path escapes release root: %s" % raw_path, file=sys.stderr)
        sys.exit(1)
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()
PY
  fi
  tar -xzf "$DELTA_BUNDLE" -C "$ROOT/releases/$RELEASE_ID.tmp"
else
  tar -xzf "$BUNDLE" -C "$ROOT/releases/$RELEASE_ID.tmp"
fi
mv "$ROOT/releases/$RELEASE_ID.tmp" "$ROOT/releases/$RELEASE_ID"
ACTIVATION_SUCCEEDED=0
quarantine_failed_release() {
  CODE="$?"
  trap - EXIT
  if [ "$CODE" -ne 0 ] && [ "$ACTIVATION_SUCCEEDED" -ne 1 ]; then
    CURRENT_TARGET="$(readlink "$ROOT/current" 2>/dev/null || true)"
    if [ "$CURRENT_TARGET" != "releases/$RELEASE_ID" ]; then
      mkdir -p "$ROOT/failed"
      FAILED_TARGET="$ROOT/failed/$RELEASE_ID"
      if [ -e "$FAILED_TARGET" ]; then
        FAILED_TARGET="$ROOT/failed/$RELEASE_ID-$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || echo failed)-$$"
      fi
      if [ -e "$ROOT/releases/$RELEASE_ID" ]; then
        mv "$ROOT/releases/$RELEASE_ID" "$FAILED_TARGET"
      fi
    fi
  fi
  exit "$CODE"
}
trap quarantine_failed_release EXIT
MANIFEST="$ROOT/releases/$RELEASE_ID/manifest.json"
if [ ! -f "$MANIFEST" ]; then echo "Release manifest missing" >&2; exit 1; fi
if ! grep -q '"format": "krw-ontology-release/v3"' "$MANIFEST"; then echo "Release manifest format is not v3" >&2; exit 1; fi
if ! grep -q '"env": "prod"' "$MANIFEST"; then echo "Release manifest env is not prod" >&2; exit 1; fi
if ! grep -q '"release_id": "'"$RELEASE_ID"'"' "$MANIFEST"; then echo "Release manifest release_id mismatch" >&2; exit 1; fi
if ! grep -q '"index_layout": "global-spine-and-company-shards"' "$MANIFEST"; then echo "Release manifest index_layout is not v3" >&2; exit 1; fi
if ! grep -q '"monolith_required": false' "$MANIFEST"; then echo "Release manifest monolith_required is not false" >&2; exit 1; fi
if [ ! -f "$ROOT/releases/$RELEASE_ID/indexes/global_spine.sqlite" ]; then echo "global_spine.sqlite missing" >&2; exit 1; fi
if [ ! -f "$ROOT/releases/$RELEASE_ID/indexes/shard_manifest.json" ]; then echo "shard_manifest.json missing" >&2; exit 1; fi
PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON_BIN" ]; then echo "python3 missing for remote v3 verification" >&2; exit 1; fi
"$PYTHON_BIN" - "$ROOT/releases/$RELEASE_ID" "$RELEASE_ID" <<'PY'
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

release_root = Path(sys.argv[1]).resolve()
release_id = sys.argv[2]
GLOBAL_SPINE_SCHEMA_VERSION = __KRW_GLOBAL_SPINE_SCHEMA_VERSION__
GLOBAL_SPINE_BUILDER_VERSION = __KRW_GLOBAL_SPINE_BUILDER_VERSION__
SPINE_PROJECTION_VERSION = __KRW_SPINE_PROJECTION_VERSION__
SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION = __KRW_SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION__
SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION = __KRW_SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION__
COMPANY_SHARD_SCHEMA_VERSION = __KRW_COMPANY_SHARD_SCHEMA_VERSION__
GLOBAL_SPINE_LAYOUT = "global-spine-and-company-shards"
GLOBAL_SPINE_TABLES = tuple(__KRW_GLOBAL_SPINE_TABLES__)
GLOBAL_SPINE_REQUIRED_METADATA_KEYS = tuple(__KRW_GLOBAL_SPINE_REQUIRED_METADATA_KEYS__)
GLOBAL_SPINE_REQUIRED_INDEXES = tuple(__KRW_GLOBAL_SPINE_REQUIRED_INDEXES__)
GLOBAL_SPINE_REPLICA_INVARIANT_VERSION = __KRW_GLOBAL_SPINE_REPLICA_INVARIANT_VERSION__
ROUTER_SIDECAR_SCHEMA_VERSION = __KRW_ROUTER_SIDECAR_SCHEMA_VERSION__
ROUTER_SIDECAR_BUILDER_VERSION = __KRW_ROUTER_SIDECAR_BUILDER_VERSION__
ROUTER_SIDECAR_TABLES = tuple(__KRW_ROUTER_SIDECAR_TABLES__)
ROUTER_SIDECAR_REQUIRED_METADATA_KEYS = tuple(__KRW_ROUTER_SIDECAR_REQUIRED_METADATA_KEYS__)
ROUTER_COHERENCE_SCHEMA_VERSION = __KRW_ROUTER_COHERENCE_SCHEMA_VERSION__
ROUTER_COHERENCE_BUILDER_VERSION = __KRW_ROUTER_COHERENCE_BUILDER_VERSION__
ROUTER_COHERENCE_TABLES = tuple(__KRW_ROUTER_COHERENCE_TABLES__)
ROUTER_COHERENCE_REQUIRED_METADATA_KEYS = tuple(__KRW_ROUTER_COHERENCE_REQUIRED_METADATA_KEYS__)
SHARD_QUALITY_SUMMARY_FORMAT_VERSION = "krw-ontology-shard-quality-summary/v1"
MANIFEST_BUILDER_BINDINGS = {
    "spine_schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
    "spine_builder_version": GLOBAL_SPINE_BUILDER_VERSION,
    "spine_projection_version": SPINE_PROJECTION_VERSION,
    "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
    "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
    "company_shard_schema_version": COMPANY_SHARD_SCHEMA_VERSION,
    "router_sidecar_schema_version": ROUTER_SIDECAR_SCHEMA_VERSION,
    "router_sidecar_builder_version": ROUTER_SIDECAR_BUILDER_VERSION,
    "router_coherence_schema_version": ROUTER_COHERENCE_SCHEMA_VERSION,
    "router_coherence_builder_version": ROUTER_COHERENCE_BUILDER_VERSION,
}
GLOBAL_SPINE_MANIFEST_BINDINGS = {
    "schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
    "builder_version": GLOBAL_SPINE_BUILDER_VERSION,
    "spine_projection_version": SPINE_PROJECTION_VERSION,
}
GLOBAL_SPINE_METADATA_BINDINGS = {
    **GLOBAL_SPINE_MANIFEST_BINDINGS,
    "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
    "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
    "company_shard_schema_version": COMPANY_SHARD_SCHEMA_VERSION,
}
COMPANY_SHARDS_BINDINGS = {
    "schema_version": COMPANY_SHARD_SCHEMA_VERSION,
    "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
    "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
}
SHARD_MANIFEST_BINDINGS = {
    "company_shard_schema_version": COMPANY_SHARD_SCHEMA_VERSION,
    "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
    "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
}

def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)

def require_bindings(payload, expected, label):
    if not isinstance(payload, dict):
        fail("%s missing" % label)
    for key, value in expected.items():
        if payload.get(key) != value:
            fail("%s binding mismatch: %s" % (label, key))

def load_json(path):
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        fail("%s invalid: %s" % (path.name, exc))

def resolve_rel(raw_path, role):
    if not isinstance(raw_path, str) or not raw_path:
        fail("%s path missing" % role)
    candidate = Path(raw_path)
    if candidate.is_absolute():
        fail("%s path must be relative: %s" % (role, raw_path))
    resolved = (release_root / candidate).resolve()
    try:
        resolved.relative_to(release_root)
    except ValueError:
        fail("%s path escapes release root: %s" % (role, raw_path))
    return resolved

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def rebind_transport_seal(path, expected_sha256, *, suffix, seal_format, kind, cache_key=None):
    seal_path = path.with_name(path.name + suffix)
    payload = load_json(seal_path)
    if payload.get("format") != seal_format:
        fail("remote %s seal format mismatch" % kind)
    if payload.get("kind") != kind or payload.get("deep_verified") is not True:
        fail("remote %s seal contract mismatch" % kind)
    if cache_key is not None and payload.get("cache_key") != cache_key:
        fail("remote %s seal cache key mismatch" % kind)
    if payload.get("sha256") != expected_sha256:
        fail("remote %s seal sha256 mismatch" % kind)
    stat = path.stat()
    if suffix == ".verify.json":
        identity = {
            "device": int(stat.st_dev),
            "inode": int(stat.st_ino),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    else:
        identity = {
            "device": int(stat.st_dev),
            "inode": int(stat.st_ino),
            "size_bytes": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    payload["database_identity"] = identity
    payload["sha256_database_identity"] = identity
    details = payload.get("details")
    payload["details"] = {
        **(details if isinstance(details, dict) else {}),
        "transport_rebind": "manifest-sha256-verified",
    }
    tmp_path = seal_path.with_name(".%s.%s.transport.tmp" % (seal_path.name, os.getpid()))
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\\n")
        os.replace(tmp_path, seal_path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass

def resolve_shard_manifest_path(raw_path, role):
    if not isinstance(raw_path, str) or not raw_path:
        fail("%s path missing" % role)
    candidate = Path(raw_path)
    if candidate.is_absolute():
        fail("%s path must be relative: %s" % (role, raw_path))
    if candidate.parts and candidate.parts[0] == "indexes":
        resolved = (release_root / candidate).resolve()
    else:
        resolved = (release_root / "indexes" / candidate).resolve()
    try:
        resolved.relative_to(release_root)
    except ValueError:
        fail("%s path escapes release root: %s" % (role, raw_path))
    return resolved

def table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None

def count_table(conn, table_name):
    if not table_exists(conn, table_name):
        return 0
    return int(conn.execute("SELECT COUNT(*) FROM %s" % table_name).fetchone()[0])

def count_distinct(conn, table_name, column_name):
    if not table_exists(conn, table_name):
        return 0
    return int(conn.execute("SELECT COUNT(DISTINCT %s) FROM %s" % (column_name, table_name)).fetchone()[0])

def read_global_spine_metadata(conn):
    if not table_exists(conn, "metadata"):
        return {}
    metadata = {}
    for key, value_json in conn.execute("SELECT key, value_json FROM metadata").fetchall():
        try:
            metadata[str(key)] = json.loads(str(value_json))
        except json.JSONDecodeError:
            metadata[str(key)] = str(value_json)
    return metadata

def quality_summary(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        totals = {
            "documents": count_table(conn, "documents"),
            "tickers": count_distinct(conn, "documents", "ticker"),
            "objects": count_table(conn, "objects"),
            "quality_events": count_table(conn, "quality_events"),
        }
        section_status = (
            {
                str(row["section_quality_status"] or "unknown"): int(row["cnt"] or 0)
                for row in conn.execute(
                    '''
                    SELECT section_quality_status, COUNT(*) AS cnt
                    FROM documents
                    GROUP BY section_quality_status
                    '''
                )
            }
            if table_exists(conn, "documents")
            else {}
        )
        event_counts = (
            [
                {
                    "category": str(row["category"] or ""),
                    "severity": str(row["severity"] or ""),
                    "stage": str(row["stage"] or ""),
                    "count": int(row["count"] or 0),
                }
                for row in conn.execute(
                    '''
                    SELECT category, severity, COALESCE(stage, '') AS stage, COUNT(*) AS count
                    FROM quality_events
                    GROUP BY category, severity, stage
                    ORDER BY count DESC, category, severity, stage
                    '''
                )
            ]
            if table_exists(conn, "quality_events")
            else []
        )
        if not table_exists(conn, "documents"):
            ticker_quality = []
        elif not table_exists(conn, "quality_events"):
            ticker_quality_sql = '''
                SELECT ticker,
                       COUNT(*) AS docs,
                       SUM(CASE WHEN section_quality_status='fail' THEN 1 ELSE 0 END) AS section_fail,
                       SUM(CASE WHEN section_quality_status='warn' THEN 1 ELSE 0 END) AS section_warn,
                       0 AS batch_failure,
                       0 AS coverage_gap,
                       0 AS rejected_object
                FROM documents
                GROUP BY ticker
                ORDER BY ticker
            '''
            ticker_quality = [
                {
                    "ticker": str(row["ticker"] or ""),
                    "docs": int(row["docs"] or 0),
                    "section_fail": int(row["section_fail"] or 0),
                    "section_warn": int(row["section_warn"] or 0),
                    "batch_failure": int(row["batch_failure"] or 0),
                    "coverage_gap": int(row["coverage_gap"] or 0),
                    "rejected_object": int(row["rejected_object"] or 0),
                }
                for row in conn.execute(ticker_quality_sql)
            ]
        else:
            ticker_quality_sql = '''
                WITH d AS (
                    SELECT ticker,
                           COUNT(*) AS docs,
                           SUM(CASE WHEN section_quality_status='fail' THEN 1 ELSE 0 END) AS section_fail,
                           SUM(CASE WHEN section_quality_status='warn' THEN 1 ELSE 0 END) AS section_warn
                    FROM documents
                    GROUP BY ticker
                ),
                e AS (
                    SELECT ticker,
                           SUM(CASE WHEN category='batch_failure' THEN 1 ELSE 0 END) AS batch_failure,
                           SUM(CASE WHEN category='coverage_gap' THEN 1 ELSE 0 END) AS coverage_gap,
                           SUM(CASE WHEN category='rejected_object' THEN 1 ELSE 0 END) AS rejected_object
                    FROM quality_events
                    GROUP BY ticker
                )
                SELECT d.ticker, d.docs, d.section_fail, d.section_warn,
                       COALESCE(e.batch_failure, 0) AS batch_failure,
                       COALESCE(e.coverage_gap, 0) AS coverage_gap,
                       COALESCE(e.rejected_object, 0) AS rejected_object
                FROM d
                LEFT JOIN e ON d.ticker=e.ticker
                ORDER BY d.ticker
            '''
            ticker_quality = [
                {
                    "ticker": str(row["ticker"] or ""),
                    "docs": int(row["docs"] or 0),
                    "section_fail": int(row["section_fail"] or 0),
                    "section_warn": int(row["section_warn"] or 0),
                    "batch_failure": int(row["batch_failure"] or 0),
                    "coverage_gap": int(row["coverage_gap"] or 0),
                    "rejected_object": int(row["rejected_object"] or 0),
                }
                for row in conn.execute(ticker_quality_sql)
            ]
        rejected_reasons = (
            [
                {"reason": str(row["reason"] or ""), "count": int(row["count"] or 0)}
                for row in conn.execute(
                    '''
                    SELECT
                        CASE
                            WHEN message LIKE 'Unsupported numeric values:%'
                                THEN 'Unsupported numeric values'
                            WHEN message LIKE 'Dangling references:%'
                                THEN 'Dangling references'
                            ELSE message
                        END AS reason,
                        COUNT(*) AS count
                    FROM quality_events
                    WHERE category='rejected_object'
                    GROUP BY reason
                    ORDER BY count DESC, reason
                    LIMIT 20
                    '''
                )
            ]
            if table_exists(conn, "quality_events")
            else []
        )
    return {
        "format": SHARD_QUALITY_SUMMARY_FORMAT_VERSION,
        "totals": totals,
        "section_status": section_status,
        "event_counts": event_counts,
        "ticker_quality": ticker_quality,
        "rejected_reasons": rejected_reasons,
    }

manifest = load_json(release_root / "manifest.json")
if manifest.get("format") != "krw-ontology-release/v3":
    fail("remote manifest format is not v3")
if manifest.get("env") != "prod":
    fail("remote manifest env is not prod")
if manifest.get("release_id") != release_id:
    fail("remote manifest release_id mismatch")
if manifest.get("index_layout") != "global-spine-and-company-shards":
    fail("remote manifest index_layout is not v3")
if manifest.get("monolith_required") is not False:
    fail("remote manifest monolith_required is not false")
if manifest.get("status") != "ready":
    fail("remote manifest status is not ready")
require_bindings(manifest.get("builder"), MANIFEST_BUILDER_BINDINGS, "remote manifest builder")

indexes = manifest.get("indexes")
if not isinstance(indexes, dict):
    fail("remote manifest missing indexes")
global_spine = indexes.get("global_spine")
if not isinstance(global_spine, dict):
    fail("remote manifest missing global_spine")
require_bindings(global_spine, GLOBAL_SPINE_MANIFEST_BINDINGS, "remote manifest global_spine")
global_spine_path = resolve_rel(global_spine.get("path"), "global_spine")
if not global_spine_path.is_file():
    fail("remote global_spine missing")
expected_global_sha = global_spine.get("sha256")
if not isinstance(expected_global_sha, str) or not expected_global_sha:
    fail("remote global_spine sha256 missing")
if sha256(global_spine_path) != expected_global_sha:
    fail("remote global_spine sha256 mismatch")
try:
    with sqlite3.connect(global_spine_path) as conn:
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        secondary_indexes = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        metadata = read_global_spine_metadata(conn)
except sqlite3.Error as exc:
    fail("remote global_spine sqlite open failed: %s" % exc)
for table in GLOBAL_SPINE_TABLES:
    if table not in tables:
        fail("remote global_spine table missing: %s" % table)
for key in GLOBAL_SPINE_REQUIRED_METADATA_KEYS:
    if key not in metadata:
        fail("remote global_spine metadata missing: %s" % key)
for index_name in GLOBAL_SPINE_REQUIRED_INDEXES:
    if index_name not in secondary_indexes:
        fail("remote global_spine index missing: %s" % index_name)
if metadata.get("schema_version") != GLOBAL_SPINE_SCHEMA_VERSION:
    fail("remote global_spine schema_version mismatch")
if metadata.get("index_layout") != GLOBAL_SPINE_LAYOUT:
    fail("remote global_spine index_layout mismatch")
if metadata.get("replica_invariant_version") != GLOBAL_SPINE_REPLICA_INVARIANT_VERSION:
    fail("remote global_spine replica invariant version mismatch")
require_bindings(metadata, GLOBAL_SPINE_METADATA_BINDINGS, "remote global_spine metadata")

router_sidecar = indexes.get("router_sidecar")
if not isinstance(router_sidecar, dict):
    fail("remote manifest missing router_sidecar")
require_bindings(
    router_sidecar,
    {
        "schema_version": ROUTER_SIDECAR_SCHEMA_VERSION,
        "builder_version": ROUTER_SIDECAR_BUILDER_VERSION,
    },
    "remote manifest router_sidecar",
)
if router_sidecar.get("required") is not True:
    fail("remote manifest router_sidecar is not required")
if router_sidecar.get("verification_ok") is not True:
    fail("remote manifest router_sidecar verification is not ok")
router_sidecar_path = resolve_rel(router_sidecar.get("path"), "router_sidecar")
if not router_sidecar_path.is_file():
    fail("remote router_sidecar missing")
expected_router_sha = router_sidecar.get("sha256")
if not isinstance(expected_router_sha, str) or not expected_router_sha:
    fail("remote router_sidecar sha256 missing")
if sha256(router_sidecar_path) != expected_router_sha:
    fail("remote router_sidecar sha256 mismatch")
try:
    with sqlite3.connect(router_sidecar_path) as conn:
        sidecar_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        sidecar_metadata = read_global_spine_metadata(conn)
except sqlite3.Error as exc:
    fail("remote router_sidecar sqlite open failed: %s" % exc)
for table in ROUTER_SIDECAR_TABLES:
    if table not in sidecar_tables:
        fail("remote router_sidecar table missing: %s" % table)
for key in ROUTER_SIDECAR_REQUIRED_METADATA_KEYS:
    if key not in sidecar_metadata:
        fail("remote router_sidecar metadata missing: %s" % key)
if sidecar_metadata.get("schema_version") != ROUTER_SIDECAR_SCHEMA_VERSION:
    fail("remote router_sidecar schema_version mismatch")
if sidecar_metadata.get("builder_version") != ROUTER_SIDECAR_BUILDER_VERSION:
    fail("remote router_sidecar builder_version mismatch")
if sidecar_metadata.get("source_global_spine_sha256") != expected_global_sha:
    fail("remote router_sidecar global spine binding mismatch")
if sidecar_metadata.get("release_id") != release_id:
    fail("remote router_sidecar release_id mismatch")
for key in (
    "ranking_profile_sha256",
    "content_sha256",
    "build_fingerprint_sha256",
):
    if sidecar_metadata.get(key) != router_sidecar.get(key):
        fail("remote router_sidecar manifest binding mismatch: %s" % key)
if sidecar_metadata.get("counts") != router_sidecar.get("counts"):
    fail("remote router_sidecar counts mismatch")
fingerprint_payload = {
    "builder_version": sidecar_metadata.get("builder_version"),
    "content_sha256": sidecar_metadata.get("content_sha256"),
    "ranking_profile_sha256": sidecar_metadata.get("ranking_profile_sha256"),
    "release_id": sidecar_metadata.get("release_id"),
    "schema_sql_sha256": sidecar_metadata.get("schema_sql_sha256"),
    "schema_version": sidecar_metadata.get("schema_version"),
    "source_global_spine_sha256": sidecar_metadata.get("source_global_spine_sha256"),
}
expected_fingerprint = hashlib.sha256(
    json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
if sidecar_metadata.get("build_fingerprint_sha256") != expected_fingerprint:
    fail("remote router_sidecar build fingerprint mismatch")

router_coherence = indexes.get("router_coherence")
if not isinstance(router_coherence, dict):
    fail("remote manifest missing router_coherence")
require_bindings(
    router_coherence,
    {
        "schema_version": ROUTER_COHERENCE_SCHEMA_VERSION,
        "builder_version": ROUTER_COHERENCE_BUILDER_VERSION,
    },
    "remote manifest router_coherence",
)
if router_coherence.get("required") is not True:
    fail("remote manifest router_coherence is not required")
if router_coherence.get("verification_ok") is not True:
    fail("remote manifest router_coherence verification is not ok")
router_coherence_path = resolve_rel(router_coherence.get("path"), "router_coherence")
if not router_coherence_path.is_file():
    fail("remote router_coherence missing")
expected_coherence_sha = router_coherence.get("sha256")
if not isinstance(expected_coherence_sha, str) or not expected_coherence_sha:
    fail("remote router_coherence sha256 missing")
if sha256(router_coherence_path) != expected_coherence_sha:
    fail("remote router_coherence sha256 mismatch")
try:
    with sqlite3.connect(
        router_coherence_path.as_uri() + "?mode=ro&immutable=1",
        uri=True,
    ) as conn:
        coherence_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        coherence_metadata = read_global_spine_metadata(conn)
except sqlite3.Error as exc:
    fail("remote router_coherence sqlite open failed: %s" % exc)
for table in ROUTER_COHERENCE_TABLES:
    if table not in coherence_tables:
        fail("remote router_coherence table missing: %s" % table)
for key in ROUTER_COHERENCE_REQUIRED_METADATA_KEYS:
    if key not in coherence_metadata:
        fail("remote router_coherence metadata missing: %s" % key)
if coherence_metadata.get("schema_version") != ROUTER_COHERENCE_SCHEMA_VERSION:
    fail("remote router_coherence schema_version mismatch")
if coherence_metadata.get("builder_version") != ROUTER_COHERENCE_BUILDER_VERSION:
    fail("remote router_coherence builder_version mismatch")
if coherence_metadata.get("source_global_spine_sha256") != expected_global_sha:
    fail("remote router_coherence global spine binding mismatch")
if coherence_metadata.get("release_id") != release_id:
    fail("remote router_coherence release_id mismatch")
for key in (
    "profile_sha256",
    "semantic_cache_key",
    "build_fingerprint_sha256",
):
    if coherence_metadata.get(key) != router_coherence.get(key):
        fail("remote router_coherence manifest binding mismatch: %s" % key)
if coherence_metadata.get("counts") != router_coherence.get("counts"):
    fail("remote router_coherence counts mismatch")
coherence_fingerprint_payload = {
    "builder_version": coherence_metadata.get("builder_version"),
    "counts": coherence_metadata.get("counts"),
    "profile_sha256": coherence_metadata.get("profile_sha256"),
    "release_id": coherence_metadata.get("release_id"),
    "schema_sql_sha256": coherence_metadata.get("schema_sql_sha256"),
    "schema_version": coherence_metadata.get("schema_version"),
    "semantic_cache_key": coherence_metadata.get("semantic_cache_key"),
    "source_global_spine_sha256": coherence_metadata.get("source_global_spine_sha256"),
}
expected_coherence_fingerprint = hashlib.sha256(
    json.dumps(
        coherence_fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
if coherence_metadata.get("build_fingerprint_sha256") != expected_coherence_fingerprint:
    fail("remote router_coherence build fingerprint mismatch")

shard_manifest_entry = indexes.get("shard_manifest")
if not isinstance(shard_manifest_entry, dict):
    fail("remote manifest missing shard_manifest")
shard_manifest_path = resolve_rel(shard_manifest_entry.get("path"), "shard_manifest")
if not shard_manifest_path.is_file():
    fail("remote shard_manifest missing")
expected_shard_manifest_sha = shard_manifest_entry.get("sha256")
if isinstance(expected_shard_manifest_sha, str) and expected_shard_manifest_sha and sha256(shard_manifest_path) != expected_shard_manifest_sha:
    fail("remote shard_manifest sha256 mismatch")

delta_manifest_path = release_root / ".krw_delta_manifest.json"
changed_files = {}
if delta_manifest_path.is_file():
    delta_manifest = load_json(delta_manifest_path)
    if delta_manifest.get("format") != "krw-ontology-release-delta/v1":
        fail("remote delta manifest format mismatch")
    raw_changed_files = delta_manifest.get("changed_files") or {}
    if not isinstance(raw_changed_files, dict):
        fail("remote delta changed_files invalid")
    changed_files = raw_changed_files
for raw_path, metadata in sorted(changed_files.items()):
    changed_path = resolve_rel(raw_path, "delta_changed")
    if not changed_path.is_file():
        fail("remote delta changed file missing: %s" % raw_path)
    expected_sha = metadata.get("sha256") if isinstance(metadata, dict) else None
    if not isinstance(expected_sha, str) or not expected_sha:
        fail("remote delta changed file sha256 missing: %s" % raw_path)
    if sha256(changed_path) != expected_sha:
        fail("remote delta changed file sha256 mismatch: %s" % raw_path)

shard_manifest = load_json(shard_manifest_path)
require_bindings(shard_manifest, SHARD_MANIFEST_BINDINGS, "remote shard_manifest")
raw_shards = shard_manifest.get("shards")
if not isinstance(raw_shards, dict):
    fail("remote shard_manifest missing shards")
company_shards = indexes.get("company_shards")
if not isinstance(company_shards, dict):
    fail("remote manifest missing company_shards")
require_bindings(company_shards, COMPANY_SHARDS_BINDINGS, "remote manifest company_shards")
manifest_tickers = company_shards.get("tickers")
if not isinstance(manifest_tickers, dict):
    fail("remote manifest company_shards missing tickers")
expected_count = company_shards.get("count")
if not isinstance(expected_count, int) or expected_count != len(manifest_tickers):
    fail("remote manifest company_shards count mismatch")
if set(manifest_tickers) != set(raw_shards):
    fail("remote manifest shard_manifest ticker mismatch")
for ticker, entry in sorted(manifest_tickers.items()):
    if not isinstance(entry, dict):
        fail("remote manifest company shard invalid: %s" % ticker)
    shard_entry = raw_shards.get(ticker)
    if not isinstance(shard_entry, dict):
        fail("remote shard_manifest shard invalid: %s" % ticker)
    require_bindings(entry, COMPANY_SHARDS_BINDINGS, "remote manifest company shard:%s" % ticker)
    require_bindings(shard_entry, COMPANY_SHARDS_BINDINGS, "remote shard_manifest shard:%s" % ticker)
    manifest_shard_path = resolve_rel(entry.get("path"), "company_shard:%s" % ticker)
    shard_path = resolve_shard_manifest_path(shard_entry.get("path"), "shard_manifest:%s" % ticker)
    if manifest_shard_path != shard_path:
        fail("remote company shard path mismatch: %s" % ticker)
    if not shard_path.is_file():
        fail("remote company shard missing: %s" % ticker)
    expected_shard_sha = entry.get("sha256") or shard_entry.get("sha256")
    if not isinstance(expected_shard_sha, str) or not expected_shard_sha:
        fail("remote company shard sha256 missing: %s" % ticker)
    if sha256(shard_path) != expected_shard_sha:
        fail("remote company shard sha256 mismatch: %s" % ticker)
    expected_quality = shard_entry.get("quality_summary")
    if not isinstance(expected_quality, dict):
        fail("remote shard quality_summary missing: %s" % ticker)
    if expected_quality.get("format") != SHARD_QUALITY_SUMMARY_FORMAT_VERSION:
        fail("remote shard quality_summary format mismatch: %s" % ticker)
    actual_quality = quality_summary(shard_path)
    if json.dumps(expected_quality, ensure_ascii=False, sort_keys=True, default=str) != json.dumps(
        actual_quality,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ):
        fail("remote shard quality_summary mismatch: %s" % ticker)

verify_report = release_root / "verify" / "release_verify.json"
if not verify_report.is_file():
    fail("release_verify.json missing")
verify_payload = load_json(verify_report)
if verify_payload.get("ok") is not True:
    fail("Release verify report is not ok")
if verify_payload.get("release_id") != release_id:
    fail("Release verify report release_id mismatch")
if verify_payload.get("env") != "prod":
    fail("Release verify report env mismatch")

# Transport changes inode identity.  The full manifest SHA-256 checks above
# prove the bytes before seals are rebound to this server's immutable files.
rebind_transport_seal(
    global_spine_path,
    expected_global_sha,
    suffix=".verify.json",
    seal_format="krw-ontology-spine-verification-seal/v1",
    kind="global_spine",
)
rebind_transport_seal(
    router_sidecar_path,
    expected_router_sha,
    suffix=".cache-seal.json",
    seal_format="krw-ontology-immutable-sqlite-cache-seal/v1",
    kind="router_sidecar",
    cache_key=sidecar_metadata.get("build_fingerprint_sha256"),
)
rebind_transport_seal(
    router_coherence_path,
    expected_coherence_sha,
    suffix=".cache-seal.json",
    seal_format="krw-ontology-immutable-sqlite-cache-seal/v1",
    kind="router_coherence",
    cache_key=coherence_metadata.get("semantic_cache_key"),
)
PY
rm -f "$ROOT/releases/$RELEASE_ID/.krw_delta_manifest.json"
mkdir -p "$ROOT/activation_logs" "$ROOT/activation_events"
ACTIVATION_LOG="$ROOT/activation_logs/$RELEASE_ID.log"
ACTIVATION_EVENTS="$ROOT/activation_events/$RELEASE_ID.jsonl"
PREV=""
json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}
activation_event() {
  EVENT="${1:-unknown}"
  STATUS="${2:-info}"
  REASON="${3:-}"
  TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || true)"
  printf '{"ts":"%s","event":"%s","status":"%s","release_id":"%s","previous_current":"%s","reason":"%s"}\n' "$(json_escape "$TS")" "$(json_escape "$EVENT")" "$(json_escape "$STATUS")" "$(json_escape "$RELEASE_ID")" "$(json_escape "$PREV")" "$(json_escape "$REASON")" >> "$ACTIVATION_EVENTS"
}
printf 'release_id=%s\n' "$RELEASE_ID" >> "$ACTIVATION_LOG"
printf 'remote_v3_preflight=ok\n' >> "$ACTIVATION_LOG"
activation_event "bundle_extracted" "ok"
if [ -L "$ROOT/current" ]; then
  PREV="$(readlink "$ROOT/current" 2>/dev/null || true)"
elif [ -e "$ROOT/current" ]; then
  PREV="releases/pre-prod-$RELEASE_ID"
  rm -rf "$ROOT/$PREV"
  mv "$ROOT/current" "$ROOT/$PREV"
fi
activation_event "previous_current_detected" "ok"
rollback() {
  REASON="${1:-activation_failed}"
  printf 'failed_activation=%s\n' "$REASON" >> "$ACTIVATION_LOG"
  activation_event "rollback" "failed" "$REASON"
  if [ -n "$PREV" ]; then
    ln -sfn "$PREV" "$ROOT/current.rollback"
    mv -Tf "$ROOT/current.rollback" "$ROOT/current"
    if [ -n "$RELOAD_COMMAND" ]; then sh -c "$RELOAD_COMMAND" >/dev/null 2>&1 || true; fi
  else
    rm -f "$ROOT/current"
  fi
}
printf 'previous_current=%s\n' "$PREV" >> "$ACTIVATION_LOG"
ln -sfn "releases/$RELEASE_ID" "$ROOT/current.next"
mv -Tf "$ROOT/current.next" "$ROOT/current"
printf 'current_switched=yes\n' >> "$ACTIVATION_LOG"
activation_event "current_switched" "ok"
if [ -n "$RELOAD_COMMAND" ]; then
  activation_event "reload" "started"
  sh -c "$RELOAD_COMMAND" || { rollback reload_failed; exit 1; }
  activation_event "reload" "ok"
fi
if [ -n "$HEALTH_URL" ]; then
  activation_event "health_check" "started"
  HEALTH_RESPONSE="$(curl -fsS "$HEALTH_URL")" || { rollback health_failed; exit 1; }
  printf '%s' "$HEALTH_RESPONSE" | grep -q '"ok"[[:space:]]*:[[:space:]]*true' || { rollback health_not_ok; exit 1; }
  printf '%s' "$HEALTH_RESPONSE" | grep -q '"release_id"[[:space:]]*:[[:space:]]*"'"$RELEASE_ID"'"' || { rollback health_release_mismatch; exit 1; }
  printf '%s' "$HEALTH_RESPONSE" | grep -q '"index_layout"[[:space:]]*:[[:space:]]*"global-spine-and-company-shards"' || { rollback health_layout_mismatch; exit 1; }
  activation_event "health_check" "ok"
fi
printf 'activation_ok=yes\n' >> "$ACTIVATION_LOG"
activation_event "activation" "ok"
ACTIVATION_SUCCEEDED=1
rm -f "$BUNDLE" "$DELTA_BUNDLE"
if [ "$KEEP_RELEASES" -gt 0 ]; then
  cd "$ROOT/releases"
  OLD="$(ls -1dt */ 2>/dev/null | tail -n +"$((KEEP_RELEASES + 1))" || true)"
  if [ -n "$OLD" ]; then printf '%s\n' "$OLD" | xargs rm -rf; fi
fi
""".replace("__KRW_ROOT__", root_q)
        .replace("__KRW_RELEASE_ID__", release_q)
        .replace("__KRW_RELOAD_COMMAND__", reload_q)
        .replace("__KRW_HEALTH_URL__", health_q)
        .replace("__KRW_KEEP_RELEASES__", str(keep_releases))
        .replace("__KRW_GLOBAL_SPINE_SCHEMA_VERSION__", json.dumps(GLOBAL_SPINE_SCHEMA_VERSION))
        .replace("__KRW_GLOBAL_SPINE_BUILDER_VERSION__", json.dumps(GLOBAL_SPINE_BUILDER_VERSION))
        .replace("__KRW_GLOBAL_SPINE_TABLES__", json.dumps(list(GLOBAL_SPINE_TABLES)))
        .replace(
            "__KRW_GLOBAL_SPINE_REQUIRED_METADATA_KEYS__",
            json.dumps(list(GLOBAL_SPINE_REQUIRED_METADATA_KEYS)),
        )
        .replace(
            "__KRW_GLOBAL_SPINE_REQUIRED_INDEXES__",
            json.dumps(list(GLOBAL_SPINE_ALLOWED_SECONDARY_INDEXES)),
        )
        .replace(
            "__KRW_GLOBAL_SPINE_REPLICA_INVARIANT_VERSION__",
            json.dumps(GLOBAL_SPINE_REPLICA_INVARIANT_VERSION),
        )
        .replace("__KRW_ROUTER_SIDECAR_SCHEMA_VERSION__", json.dumps(ROUTER_SIDECAR_SCHEMA_VERSION))
        .replace(
            "__KRW_ROUTER_SIDECAR_BUILDER_VERSION__", json.dumps(ROUTER_SIDECAR_BUILDER_VERSION)
        )
        .replace("__KRW_ROUTER_SIDECAR_TABLES__", json.dumps(list(ROUTER_SIDECAR_TABLES)))
        .replace(
            "__KRW_ROUTER_SIDECAR_REQUIRED_METADATA_KEYS__",
            json.dumps(list(ROUTER_SIDECAR_REQUIRED_METADATA_KEYS)),
        )
        .replace(
            "__KRW_ROUTER_COHERENCE_SCHEMA_VERSION__",
            json.dumps(ROUTER_COHERENCE_SCHEMA_VERSION),
        )
        .replace(
            "__KRW_ROUTER_COHERENCE_BUILDER_VERSION__",
            json.dumps(ROUTER_COHERENCE_BUILDER_VERSION),
        )
        .replace(
            "__KRW_ROUTER_COHERENCE_TABLES__",
            json.dumps(list(ROUTER_COHERENCE_TABLES)),
        )
        .replace(
            "__KRW_ROUTER_COHERENCE_REQUIRED_METADATA_KEYS__",
            json.dumps(list(ROUTER_COHERENCE_REQUIRED_METADATA_KEYS)),
        )
        .replace("__KRW_SPINE_PROJECTION_VERSION__", json.dumps(SPINE_PROJECTION_VERSION))
        .replace(
            "__KRW_SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION__",
            json.dumps(SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION),
        )
        .replace(
            "__KRW_SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION__",
            json.dumps(SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION),
        )
        .replace("__KRW_COMPANY_SHARD_SCHEMA_VERSION__", json.dumps(COMPANY_SHARD_SCHEMA_VERSION))
    )


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
    return (
        """set -eu
ROOT=__KRW_ROOT__
REQUESTED_RELEASE=__KRW_RELEASE_ID__
RELOAD_COMMAND=__KRW_RELOAD_COMMAND__
HEALTH_URL=__KRW_HEALTH_URL__
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
TARGET_RELEASE_ID="$(basename "$TARGET")"
MANIFEST="$ROOT/$TARGET/manifest.json"
if [ ! -f "$MANIFEST" ]; then echo "Rollback release manifest missing" >&2; exit 1; fi
if ! grep -q '"format": "krw-ontology-release/v3"' "$MANIFEST"; then echo "Rollback release manifest format is not v3" >&2; exit 1; fi
if ! grep -q '"env": "prod"' "$MANIFEST"; then echo "Rollback release manifest env is not prod" >&2; exit 1; fi
if ! grep -q '"release_id": "'"$TARGET_RELEASE_ID"'"' "$MANIFEST"; then echo "Rollback release manifest release_id mismatch" >&2; exit 1; fi
if ! grep -q '"index_layout": "global-spine-and-company-shards"' "$MANIFEST"; then echo "Rollback release manifest index_layout is not v3" >&2; exit 1; fi
if [ ! -f "$ROOT/$TARGET/indexes/global_spine.sqlite" ]; then echo "Rollback global_spine.sqlite missing" >&2; exit 1; fi
if [ ! -f "$ROOT/$TARGET/indexes/shard_manifest.json" ]; then echo "Rollback shard_manifest.json missing" >&2; exit 1; fi
PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON_BIN" ]; then echo "python3 missing for rollback v3 verification" >&2; exit 1; fi
"$PYTHON_BIN" - "$ROOT/$TARGET" "$TARGET_RELEASE_ID" <<'PY'
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

release_root = Path(sys.argv[1]).resolve()
release_id = sys.argv[2]
GLOBAL_SPINE_SCHEMA_VERSION = __KRW_GLOBAL_SPINE_SCHEMA_VERSION__
GLOBAL_SPINE_BUILDER_VERSION = __KRW_GLOBAL_SPINE_BUILDER_VERSION__
SPINE_PROJECTION_VERSION = __KRW_SPINE_PROJECTION_VERSION__
SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION = __KRW_SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION__
SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION = __KRW_SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION__
COMPANY_SHARD_SCHEMA_VERSION = __KRW_COMPANY_SHARD_SCHEMA_VERSION__
GLOBAL_SPINE_LAYOUT = "global-spine-and-company-shards"
GLOBAL_SPINE_TABLES = tuple(__KRW_GLOBAL_SPINE_TABLES__)
GLOBAL_SPINE_REQUIRED_METADATA_KEYS = tuple(__KRW_GLOBAL_SPINE_REQUIRED_METADATA_KEYS__)
GLOBAL_SPINE_REQUIRED_INDEXES = tuple(__KRW_GLOBAL_SPINE_REQUIRED_INDEXES__)
GLOBAL_SPINE_REPLICA_INVARIANT_VERSION = __KRW_GLOBAL_SPINE_REPLICA_INVARIANT_VERSION__
ROUTER_SIDECAR_SCHEMA_VERSION = __KRW_ROUTER_SIDECAR_SCHEMA_VERSION__
ROUTER_SIDECAR_BUILDER_VERSION = __KRW_ROUTER_SIDECAR_BUILDER_VERSION__
ROUTER_SIDECAR_TABLES = tuple(__KRW_ROUTER_SIDECAR_TABLES__)
ROUTER_SIDECAR_REQUIRED_METADATA_KEYS = tuple(__KRW_ROUTER_SIDECAR_REQUIRED_METADATA_KEYS__)
ROUTER_COHERENCE_SCHEMA_VERSION = __KRW_ROUTER_COHERENCE_SCHEMA_VERSION__
ROUTER_COHERENCE_BUILDER_VERSION = __KRW_ROUTER_COHERENCE_BUILDER_VERSION__
ROUTER_COHERENCE_TABLES = tuple(__KRW_ROUTER_COHERENCE_TABLES__)
ROUTER_COHERENCE_REQUIRED_METADATA_KEYS = tuple(__KRW_ROUTER_COHERENCE_REQUIRED_METADATA_KEYS__)
SHARD_QUALITY_SUMMARY_FORMAT_VERSION = "krw-ontology-shard-quality-summary/v1"
MANIFEST_BUILDER_BINDINGS = {
    "spine_schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
    "spine_builder_version": GLOBAL_SPINE_BUILDER_VERSION,
    "spine_projection_version": SPINE_PROJECTION_VERSION,
    "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
    "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
    "company_shard_schema_version": COMPANY_SHARD_SCHEMA_VERSION,
    "router_sidecar_schema_version": ROUTER_SIDECAR_SCHEMA_VERSION,
    "router_sidecar_builder_version": ROUTER_SIDECAR_BUILDER_VERSION,
    "router_coherence_schema_version": ROUTER_COHERENCE_SCHEMA_VERSION,
    "router_coherence_builder_version": ROUTER_COHERENCE_BUILDER_VERSION,
}
GLOBAL_SPINE_MANIFEST_BINDINGS = {
    "schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
    "builder_version": GLOBAL_SPINE_BUILDER_VERSION,
    "spine_projection_version": SPINE_PROJECTION_VERSION,
}
GLOBAL_SPINE_METADATA_BINDINGS = {
    **GLOBAL_SPINE_MANIFEST_BINDINGS,
    "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
    "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
    "company_shard_schema_version": COMPANY_SHARD_SCHEMA_VERSION,
}
COMPANY_SHARDS_BINDINGS = {
    "schema_version": COMPANY_SHARD_SCHEMA_VERSION,
    "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
    "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
}
SHARD_MANIFEST_BINDINGS = {
    "company_shard_schema_version": COMPANY_SHARD_SCHEMA_VERSION,
    "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
    "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
}

def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)

def require_bindings(payload, expected, label):
    if not isinstance(payload, dict):
        fail("%s missing" % label)
    for key, value in expected.items():
        if payload.get(key) != value:
            fail("%s binding mismatch: %s" % (label, key))

def load_json(path):
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        fail("%s invalid: %s" % (path.name, exc))

def resolve_rel(raw_path, role):
    if not isinstance(raw_path, str) or not raw_path:
        fail("%s path missing" % role)
    candidate = Path(raw_path)
    if candidate.is_absolute():
        fail("%s path must be relative: %s" % (role, raw_path))
    resolved = (release_root / candidate).resolve()
    try:
        resolved.relative_to(release_root)
    except ValueError:
        fail("%s path escapes release root: %s" % (role, raw_path))
    return resolved

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def resolve_shard_manifest_path(raw_path, role):
    if not isinstance(raw_path, str) or not raw_path:
        fail("%s path missing" % role)
    candidate = Path(raw_path)
    if candidate.is_absolute():
        fail("%s path must be relative: %s" % (role, raw_path))
    if candidate.parts and candidate.parts[0] == "indexes":
        resolved = (release_root / candidate).resolve()
    else:
        resolved = (release_root / "indexes" / candidate).resolve()
    try:
        resolved.relative_to(release_root)
    except ValueError:
        fail("%s path escapes release root: %s" % (role, raw_path))
    return resolved

def table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None

def count_table(conn, table_name):
    if not table_exists(conn, table_name):
        return 0
    return int(conn.execute("SELECT COUNT(*) FROM %s" % table_name).fetchone()[0])

def count_distinct(conn, table_name, column_name):
    if not table_exists(conn, table_name):
        return 0
    return int(conn.execute("SELECT COUNT(DISTINCT %s) FROM %s" % (column_name, table_name)).fetchone()[0])

def read_global_spine_metadata(conn):
    if not table_exists(conn, "metadata"):
        return {}
    metadata = {}
    for key, value_json in conn.execute("SELECT key, value_json FROM metadata").fetchall():
        try:
            metadata[str(key)] = json.loads(str(value_json))
        except json.JSONDecodeError:
            metadata[str(key)] = str(value_json)
    return metadata

def quality_summary(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        totals = {
            "documents": count_table(conn, "documents"),
            "tickers": count_distinct(conn, "documents", "ticker"),
            "objects": count_table(conn, "objects"),
            "quality_events": count_table(conn, "quality_events"),
        }
        section_status = (
            {
                str(row["section_quality_status"] or "unknown"): int(row["cnt"] or 0)
                for row in conn.execute(
                    '''
                    SELECT section_quality_status, COUNT(*) AS cnt
                    FROM documents
                    GROUP BY section_quality_status
                    '''
                )
            }
            if table_exists(conn, "documents")
            else {}
        )
        event_counts = (
            [
                {
                    "category": str(row["category"] or ""),
                    "severity": str(row["severity"] or ""),
                    "stage": str(row["stage"] or ""),
                    "count": int(row["count"] or 0),
                }
                for row in conn.execute(
                    '''
                    SELECT category, severity, COALESCE(stage, '') AS stage, COUNT(*) AS count
                    FROM quality_events
                    GROUP BY category, severity, stage
                    ORDER BY count DESC, category, severity, stage
                    '''
                )
            ]
            if table_exists(conn, "quality_events")
            else []
        )
        if not table_exists(conn, "documents"):
            ticker_quality = []
        elif not table_exists(conn, "quality_events"):
            ticker_quality_sql = '''
                SELECT ticker,
                       COUNT(*) AS docs,
                       SUM(CASE WHEN section_quality_status='fail' THEN 1 ELSE 0 END) AS section_fail,
                       SUM(CASE WHEN section_quality_status='warn' THEN 1 ELSE 0 END) AS section_warn,
                       0 AS batch_failure,
                       0 AS coverage_gap,
                       0 AS rejected_object
                FROM documents
                GROUP BY ticker
                ORDER BY ticker
            '''
            ticker_quality = [
                {
                    "ticker": str(row["ticker"] or ""),
                    "docs": int(row["docs"] or 0),
                    "section_fail": int(row["section_fail"] or 0),
                    "section_warn": int(row["section_warn"] or 0),
                    "batch_failure": int(row["batch_failure"] or 0),
                    "coverage_gap": int(row["coverage_gap"] or 0),
                    "rejected_object": int(row["rejected_object"] or 0),
                }
                for row in conn.execute(ticker_quality_sql)
            ]
        else:
            ticker_quality_sql = '''
                WITH d AS (
                    SELECT ticker,
                           COUNT(*) AS docs,
                           SUM(CASE WHEN section_quality_status='fail' THEN 1 ELSE 0 END) AS section_fail,
                           SUM(CASE WHEN section_quality_status='warn' THEN 1 ELSE 0 END) AS section_warn
                    FROM documents
                    GROUP BY ticker
                ),
                e AS (
                    SELECT ticker,
                           SUM(CASE WHEN category='batch_failure' THEN 1 ELSE 0 END) AS batch_failure,
                           SUM(CASE WHEN category='coverage_gap' THEN 1 ELSE 0 END) AS coverage_gap,
                           SUM(CASE WHEN category='rejected_object' THEN 1 ELSE 0 END) AS rejected_object
                    FROM quality_events
                    GROUP BY ticker
                )
                SELECT d.ticker, d.docs, d.section_fail, d.section_warn,
                       COALESCE(e.batch_failure, 0) AS batch_failure,
                       COALESCE(e.coverage_gap, 0) AS coverage_gap,
                       COALESCE(e.rejected_object, 0) AS rejected_object
                FROM d
                LEFT JOIN e ON d.ticker=e.ticker
                ORDER BY d.ticker
            '''
            ticker_quality = [
                {
                    "ticker": str(row["ticker"] or ""),
                    "docs": int(row["docs"] or 0),
                    "section_fail": int(row["section_fail"] or 0),
                    "section_warn": int(row["section_warn"] or 0),
                    "batch_failure": int(row["batch_failure"] or 0),
                    "coverage_gap": int(row["coverage_gap"] or 0),
                    "rejected_object": int(row["rejected_object"] or 0),
                }
                for row in conn.execute(ticker_quality_sql)
            ]
        rejected_reasons = (
            [
                {"reason": str(row["reason"] or ""), "count": int(row["count"] or 0)}
                for row in conn.execute(
                    '''
                    SELECT
                        CASE
                            WHEN message LIKE 'Unsupported numeric values:%'
                                THEN 'Unsupported numeric values'
                            WHEN message LIKE 'Dangling references:%'
                                THEN 'Dangling references'
                            ELSE message
                        END AS reason,
                        COUNT(*) AS count
                    FROM quality_events
                    WHERE category='rejected_object'
                    GROUP BY reason
                    ORDER BY count DESC, reason
                    LIMIT 20
                    '''
                )
            ]
            if table_exists(conn, "quality_events")
            else []
        )
    return {
        "format": SHARD_QUALITY_SUMMARY_FORMAT_VERSION,
        "totals": totals,
        "section_status": section_status,
        "event_counts": event_counts,
        "ticker_quality": ticker_quality,
        "rejected_reasons": rejected_reasons,
    }

manifest = load_json(release_root / "manifest.json")
if manifest.get("format") != "krw-ontology-release/v3":
    fail("rollback manifest format is not v3")
if manifest.get("env") != "prod":
    fail("rollback manifest env is not prod")
if manifest.get("release_id") != release_id:
    fail("rollback manifest release_id mismatch")
if manifest.get("index_layout") != "global-spine-and-company-shards":
    fail("rollback manifest index_layout is not v3")
if manifest.get("monolith_required") is not False:
    fail("rollback manifest monolith_required is not false")
require_bindings(manifest.get("builder"), MANIFEST_BUILDER_BINDINGS, "rollback manifest builder")
indexes = manifest.get("indexes")
if not isinstance(indexes, dict):
    fail("rollback manifest missing indexes")
global_spine = indexes.get("global_spine")
if not isinstance(global_spine, dict):
    fail("rollback manifest missing global_spine")
require_bindings(global_spine, GLOBAL_SPINE_MANIFEST_BINDINGS, "rollback manifest global_spine")
global_spine_path = resolve_rel(global_spine.get("path"), "global_spine")
if not global_spine_path.is_file():
    fail("rollback global_spine missing")
expected_global_sha = global_spine.get("sha256")
if not isinstance(expected_global_sha, str) or not expected_global_sha:
    fail("rollback global_spine sha256 missing")
if sha256(global_spine_path) != expected_global_sha:
    fail("rollback global_spine sha256 mismatch")
try:
    with sqlite3.connect(global_spine_path) as conn:
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        secondary_indexes = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        metadata = read_global_spine_metadata(conn)
except sqlite3.Error as exc:
    fail("rollback global_spine sqlite open failed: %s" % exc)
for table in GLOBAL_SPINE_TABLES:
    if table not in tables:
        fail("rollback global_spine table missing: %s" % table)
for key in GLOBAL_SPINE_REQUIRED_METADATA_KEYS:
    if key not in metadata:
        fail("rollback global_spine metadata missing: %s" % key)
for index_name in GLOBAL_SPINE_REQUIRED_INDEXES:
    if index_name not in secondary_indexes:
        fail("rollback global_spine index missing: %s" % index_name)
if metadata.get("schema_version") != GLOBAL_SPINE_SCHEMA_VERSION:
    fail("rollback global_spine schema_version mismatch")
if metadata.get("index_layout") != GLOBAL_SPINE_LAYOUT:
    fail("rollback global_spine index_layout mismatch")
if metadata.get("replica_invariant_version") != GLOBAL_SPINE_REPLICA_INVARIANT_VERSION:
    fail("rollback global_spine replica invariant version mismatch")
require_bindings(metadata, GLOBAL_SPINE_METADATA_BINDINGS, "rollback global_spine metadata")

router_sidecar = indexes.get("router_sidecar")
if not isinstance(router_sidecar, dict):
    fail("rollback manifest missing router_sidecar")
require_bindings(
    router_sidecar,
    {
        "schema_version": ROUTER_SIDECAR_SCHEMA_VERSION,
        "builder_version": ROUTER_SIDECAR_BUILDER_VERSION,
    },
    "rollback manifest router_sidecar",
)
if router_sidecar.get("required") is not True:
    fail("rollback manifest router_sidecar is not required")
if router_sidecar.get("verification_ok") is not True:
    fail("rollback manifest router_sidecar verification is not ok")
router_sidecar_path = resolve_rel(router_sidecar.get("path"), "router_sidecar")
if not router_sidecar_path.is_file():
    fail("rollback router_sidecar missing")
expected_router_sha = router_sidecar.get("sha256")
if not isinstance(expected_router_sha, str) or not expected_router_sha:
    fail("rollback router_sidecar sha256 missing")
if sha256(router_sidecar_path) != expected_router_sha:
    fail("rollback router_sidecar sha256 mismatch")
try:
    with sqlite3.connect(router_sidecar_path) as conn:
        sidecar_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        sidecar_metadata = read_global_spine_metadata(conn)
except sqlite3.Error as exc:
    fail("rollback router_sidecar sqlite open failed: %s" % exc)
for table in ROUTER_SIDECAR_TABLES:
    if table not in sidecar_tables:
        fail("rollback router_sidecar table missing: %s" % table)
for key in ROUTER_SIDECAR_REQUIRED_METADATA_KEYS:
    if key not in sidecar_metadata:
        fail("rollback router_sidecar metadata missing: %s" % key)
if sidecar_metadata.get("schema_version") != ROUTER_SIDECAR_SCHEMA_VERSION:
    fail("rollback router_sidecar schema_version mismatch")
if sidecar_metadata.get("builder_version") != ROUTER_SIDECAR_BUILDER_VERSION:
    fail("rollback router_sidecar builder_version mismatch")
if sidecar_metadata.get("source_global_spine_sha256") != expected_global_sha:
    fail("rollback router_sidecar global spine binding mismatch")
if sidecar_metadata.get("release_id") != release_id:
    fail("rollback router_sidecar release_id mismatch")
for key in (
    "ranking_profile_sha256",
    "content_sha256",
    "build_fingerprint_sha256",
):
    if sidecar_metadata.get(key) != router_sidecar.get(key):
        fail("rollback router_sidecar manifest binding mismatch: %s" % key)
if sidecar_metadata.get("counts") != router_sidecar.get("counts"):
    fail("rollback router_sidecar counts mismatch")
fingerprint_payload = {
    "builder_version": sidecar_metadata.get("builder_version"),
    "content_sha256": sidecar_metadata.get("content_sha256"),
    "ranking_profile_sha256": sidecar_metadata.get("ranking_profile_sha256"),
    "release_id": sidecar_metadata.get("release_id"),
    "schema_sql_sha256": sidecar_metadata.get("schema_sql_sha256"),
    "schema_version": sidecar_metadata.get("schema_version"),
    "source_global_spine_sha256": sidecar_metadata.get("source_global_spine_sha256"),
}
expected_fingerprint = hashlib.sha256(
    json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
if sidecar_metadata.get("build_fingerprint_sha256") != expected_fingerprint:
    fail("rollback router_sidecar build fingerprint mismatch")
router_coherence = indexes.get("router_coherence")
if not isinstance(router_coherence, dict):
    fail("rollback manifest missing router_coherence")
require_bindings(
    router_coherence,
    {
        "schema_version": ROUTER_COHERENCE_SCHEMA_VERSION,
        "builder_version": ROUTER_COHERENCE_BUILDER_VERSION,
    },
    "rollback manifest router_coherence",
)
if router_coherence.get("required") is not True:
    fail("rollback manifest router_coherence is not required")
if router_coherence.get("verification_ok") is not True:
    fail("rollback manifest router_coherence verification is not ok")
router_coherence_path = resolve_rel(router_coherence.get("path"), "router_coherence")
if not router_coherence_path.is_file():
    fail("rollback router_coherence missing")
expected_coherence_sha = router_coherence.get("sha256")
if not isinstance(expected_coherence_sha, str) or not expected_coherence_sha:
    fail("rollback router_coherence sha256 missing")
if sha256(router_coherence_path) != expected_coherence_sha:
    fail("rollback router_coherence sha256 mismatch")
try:
    with sqlite3.connect(
        router_coherence_path.as_uri() + "?mode=ro&immutable=1",
        uri=True,
    ) as conn:
        coherence_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        coherence_metadata = read_global_spine_metadata(conn)
except sqlite3.Error as exc:
    fail("rollback router_coherence sqlite open failed: %s" % exc)
for table in ROUTER_COHERENCE_TABLES:
    if table not in coherence_tables:
        fail("rollback router_coherence table missing: %s" % table)
for key in ROUTER_COHERENCE_REQUIRED_METADATA_KEYS:
    if key not in coherence_metadata:
        fail("rollback router_coherence metadata missing: %s" % key)
if coherence_metadata.get("schema_version") != ROUTER_COHERENCE_SCHEMA_VERSION:
    fail("rollback router_coherence schema_version mismatch")
if coherence_metadata.get("builder_version") != ROUTER_COHERENCE_BUILDER_VERSION:
    fail("rollback router_coherence builder_version mismatch")
if coherence_metadata.get("source_global_spine_sha256") != expected_global_sha:
    fail("rollback router_coherence global spine binding mismatch")
if coherence_metadata.get("release_id") != release_id:
    fail("rollback router_coherence release_id mismatch")
for key in ("profile_sha256", "semantic_cache_key", "build_fingerprint_sha256"):
    if coherence_metadata.get(key) != router_coherence.get(key):
        fail("rollback router_coherence manifest binding mismatch: %s" % key)
if coherence_metadata.get("counts") != router_coherence.get("counts"):
    fail("rollback router_coherence counts mismatch")
coherence_fingerprint_payload = {
    "builder_version": coherence_metadata.get("builder_version"),
    "counts": coherence_metadata.get("counts"),
    "profile_sha256": coherence_metadata.get("profile_sha256"),
    "release_id": coherence_metadata.get("release_id"),
    "schema_sql_sha256": coherence_metadata.get("schema_sql_sha256"),
    "schema_version": coherence_metadata.get("schema_version"),
    "semantic_cache_key": coherence_metadata.get("semantic_cache_key"),
    "source_global_spine_sha256": coherence_metadata.get("source_global_spine_sha256"),
}
expected_coherence_fingerprint = hashlib.sha256(
    json.dumps(
        coherence_fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
if coherence_metadata.get("build_fingerprint_sha256") != expected_coherence_fingerprint:
    fail("rollback router_coherence build fingerprint mismatch")
shard_manifest_entry = indexes.get("shard_manifest")
if not isinstance(shard_manifest_entry, dict):
    fail("rollback manifest missing shard_manifest")
shard_manifest_path = resolve_rel(shard_manifest_entry.get("path"), "shard_manifest")
if not shard_manifest_path.is_file():
    fail("rollback shard_manifest missing")
expected_shard_manifest_sha = shard_manifest_entry.get("sha256")
if (
    isinstance(expected_shard_manifest_sha, str)
    and expected_shard_manifest_sha
    and sha256(shard_manifest_path) != expected_shard_manifest_sha
):
    fail("rollback shard_manifest sha256 mismatch")
shard_manifest = load_json(shard_manifest_path)
require_bindings(shard_manifest, SHARD_MANIFEST_BINDINGS, "rollback shard_manifest")
raw_shards = shard_manifest.get("shards")
if not isinstance(raw_shards, dict):
    fail("rollback shard_manifest missing shards")
company_shards = indexes.get("company_shards")
if not isinstance(company_shards, dict) or not isinstance(company_shards.get("tickers"), dict):
    fail("rollback manifest missing company_shards")
require_bindings(company_shards, COMPANY_SHARDS_BINDINGS, "rollback manifest company_shards")
manifest_tickers = company_shards["tickers"]
expected_count = company_shards.get("count")
if not isinstance(expected_count, int) or expected_count != len(manifest_tickers):
    fail("rollback manifest company_shards count mismatch")
if set(manifest_tickers) != set(raw_shards):
    fail("rollback manifest shard_manifest ticker mismatch")
for ticker, entry in sorted(company_shards["tickers"].items()):
    if not isinstance(entry, dict):
        fail("rollback company shard invalid: %s" % ticker)
    shard_entry = raw_shards.get(ticker)
    if not isinstance(shard_entry, dict):
        fail("rollback shard_manifest shard invalid: %s" % ticker)
    require_bindings(entry, COMPANY_SHARDS_BINDINGS, "rollback manifest company shard:%s" % ticker)
    require_bindings(shard_entry, COMPANY_SHARDS_BINDINGS, "rollback shard_manifest shard:%s" % ticker)
    manifest_shard_path = resolve_rel(entry.get("path"), "company_shard:%s" % ticker)
    shard_path = resolve_shard_manifest_path(shard_entry.get("path"), "shard_manifest:%s" % ticker)
    if manifest_shard_path != shard_path:
        fail("rollback company shard path mismatch: %s" % ticker)
    if not shard_path.is_file():
        fail("rollback company shard missing: %s" % ticker)
    expected_shard_sha = entry.get("sha256") or shard_entry.get("sha256")
    if not isinstance(expected_shard_sha, str) or not expected_shard_sha:
        fail("rollback company shard sha256 missing: %s" % ticker)
    if sha256(shard_path) != expected_shard_sha:
        fail("rollback company shard sha256 mismatch: %s" % ticker)
    expected_quality = shard_entry.get("quality_summary")
    if not isinstance(expected_quality, dict):
        fail("rollback shard quality_summary missing: %s" % ticker)
    if expected_quality.get("format") != SHARD_QUALITY_SUMMARY_FORMAT_VERSION:
        fail("rollback shard quality_summary format mismatch: %s" % ticker)
    actual_quality = quality_summary(shard_path)
    if json.dumps(expected_quality, ensure_ascii=False, sort_keys=True, default=str) != json.dumps(
        actual_quality,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ):
        fail("rollback shard quality_summary mismatch: %s" % ticker)
verify_report = release_root / "verify" / "release_verify.json"
if not verify_report.is_file():
    fail("rollback release_verify.json missing")
verify_payload = load_json(verify_report)
if verify_payload.get("ok") is not True:
    fail("Rollback release verify report is not ok")
if verify_payload.get("release_id") != release_id:
    fail("Rollback release verify report release_id mismatch")
if verify_payload.get("env") != "prod":
    fail("Rollback release verify report env mismatch")
PY
restore_current() {
  REASON="${1:-rollback_failed}"
  if [ -n "$CURRENT" ]; then
    ln -sfn "$CURRENT" "$ROOT/current.rollback"
    mv -Tf "$ROOT/current.rollback" "$ROOT/current"
    if [ -n "$RELOAD_COMMAND" ]; then sh -c "$RELOAD_COMMAND" >/dev/null 2>&1 || true; fi
  fi
  echo "Rollback post-switch check failed: $REASON" >&2
  exit 1
}
ln -sfn "$TARGET" "$ROOT/current.next"
mv -Tf "$ROOT/current.next" "$ROOT/current"
if [ -n "$RELOAD_COMMAND" ]; then sh -c "$RELOAD_COMMAND" || restore_current reload_failed; fi
if [ -n "$HEALTH_URL" ]; then
  HEALTH_RESPONSE="$(curl -fsS "$HEALTH_URL")" || restore_current health_failed
  printf '%s' "$HEALTH_RESPONSE" | grep -q '"ok"[[:space:]]*:[[:space:]]*true' || restore_current health_not_ok
  printf '%s' "$HEALTH_RESPONSE" | grep -q '"release_id"[[:space:]]*:[[:space:]]*"'"$TARGET_RELEASE_ID"'"' || restore_current health_release_mismatch
  printf '%s' "$HEALTH_RESPONSE" | grep -q '"index_layout"[[:space:]]*:[[:space:]]*"global-spine-and-company-shards"' || restore_current health_layout_mismatch
fi
printf '%s\n' "$TARGET"
""".replace("__KRW_ROOT__", root_q)
        .replace("__KRW_RELEASE_ID__", release_q)
        .replace("__KRW_RELOAD_COMMAND__", reload_q)
        .replace("__KRW_HEALTH_URL__", health_q)
        .replace("__KRW_GLOBAL_SPINE_SCHEMA_VERSION__", json.dumps(GLOBAL_SPINE_SCHEMA_VERSION))
        .replace("__KRW_GLOBAL_SPINE_BUILDER_VERSION__", json.dumps(GLOBAL_SPINE_BUILDER_VERSION))
        .replace("__KRW_GLOBAL_SPINE_TABLES__", json.dumps(list(GLOBAL_SPINE_TABLES)))
        .replace(
            "__KRW_GLOBAL_SPINE_REQUIRED_METADATA_KEYS__",
            json.dumps(list(GLOBAL_SPINE_REQUIRED_METADATA_KEYS)),
        )
        .replace(
            "__KRW_GLOBAL_SPINE_REQUIRED_INDEXES__",
            json.dumps(list(GLOBAL_SPINE_ALLOWED_SECONDARY_INDEXES)),
        )
        .replace(
            "__KRW_GLOBAL_SPINE_REPLICA_INVARIANT_VERSION__",
            json.dumps(GLOBAL_SPINE_REPLICA_INVARIANT_VERSION),
        )
        .replace("__KRW_ROUTER_SIDECAR_SCHEMA_VERSION__", json.dumps(ROUTER_SIDECAR_SCHEMA_VERSION))
        .replace(
            "__KRW_ROUTER_SIDECAR_BUILDER_VERSION__", json.dumps(ROUTER_SIDECAR_BUILDER_VERSION)
        )
        .replace("__KRW_ROUTER_SIDECAR_TABLES__", json.dumps(list(ROUTER_SIDECAR_TABLES)))
        .replace(
            "__KRW_ROUTER_SIDECAR_REQUIRED_METADATA_KEYS__",
            json.dumps(list(ROUTER_SIDECAR_REQUIRED_METADATA_KEYS)),
        )
        .replace(
            "__KRW_ROUTER_COHERENCE_SCHEMA_VERSION__",
            json.dumps(ROUTER_COHERENCE_SCHEMA_VERSION),
        )
        .replace(
            "__KRW_ROUTER_COHERENCE_BUILDER_VERSION__",
            json.dumps(ROUTER_COHERENCE_BUILDER_VERSION),
        )
        .replace(
            "__KRW_ROUTER_COHERENCE_TABLES__",
            json.dumps(list(ROUTER_COHERENCE_TABLES)),
        )
        .replace(
            "__KRW_ROUTER_COHERENCE_REQUIRED_METADATA_KEYS__",
            json.dumps(list(ROUTER_COHERENCE_REQUIRED_METADATA_KEYS)),
        )
        .replace("__KRW_SPINE_PROJECTION_VERSION__", json.dumps(SPINE_PROJECTION_VERSION))
        .replace(
            "__KRW_SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION__",
            json.dumps(SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION),
        )
        .replace(
            "__KRW_SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION__",
            json.dumps(SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION),
        )
        .replace("__KRW_COMPANY_SHARD_SCHEMA_VERSION__", json.dumps(COMPANY_SHARD_SCHEMA_VERSION))
    )


def _publish_prod_root(
    *,
    stable_root: Path,
    host: str | None = None,
    remote_root: str | None = None,
    reload_command: str | None = None,
    health_url: str | None = None,
    keep_releases: int | None = None,
    dry_run: bool = False,
    delta: bool = False,
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
    _assert_prod_publish_source_v3(resolved_root)
    release_id = _new_release_id()
    result = {
        "release_id": release_id,
        "stable_root": str(resolved_root),
        "host": settings["host"],
        "remote_root": settings["remote_root"],
        "keep_releases": settings["keep_releases"],
        "upload_mode": "delta" if delta else "bundle",
        "changed_file_count": None,
        "removed_file_count": None,
    }
    if dry_run:
        return result
    with tempfile.TemporaryDirectory(prefix="krw-ontology-prod-") as tmp_dir:
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
        if delta:
            candidate_root = Path(tmp_dir) / release_id
            _materialize_verified_prod_release(resolved_root, candidate_root, release_id)
            remote_files = _fetch_remote_current_file_map(
                host=str(settings["host"]),
                remote_root=str(settings["remote_root"]),
            )
            bundle_path = Path(tmp_dir) / f"{release_id}.delta.tar.gz"
            delta_summary = _build_prod_release_delta_bundle(
                candidate_root,
                bundle_path,
                remote_files=remote_files,
            )
            result["changed_file_count"] = int(delta_summary["changed_file_count"])
            result["removed_file_count"] = int(delta_summary["removed_file_count"])
            remote_bundle = f"{settings['remote_root']}/incoming/{release_id}.delta.tar.gz"
        else:
            bundle_path = Path(tmp_dir) / f"{release_id}.tar.gz"
            _build_prod_release_bundle(resolved_root, bundle_path, release_id)
            remote_bundle = f"{settings['remote_root']}/incoming/{release_id}.tar.gz"
        _run_checked(["scp", str(bundle_path), f"{settings['host']}:{remote_bundle}"])
        _run_checked(
            ["ssh", str(settings["host"]), "sh", "-s"],
            input_text=_prod_activation_script(
                remote_root=str(settings["remote_root"]),
                release_id=release_id,
                reload_command=str(settings["reload_command"])
                if settings["reload_command"]
                else None,
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
    return {
        "release_id": activated,
        "host": str(settings["host"]),
        "remote_root": str(settings["remote_root"]),
    }


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
    blocking_quality_events = _blocking_company_context_quality_event_count(source_root, ticker)
    if blocking_quality_events > 0:
        failures.append(
            f"quality_events={blocking_quality_events} "
            "(company context blocking quality events are present)"
        )

    if failures:
        detail = "; ".join(failures)
        raise RuntimeError(
            f"Publish blocked for {ticker}: unhealthy company context counts: {detail}"
        )


def _blocking_company_context_quality_event_count(source_root: Path, ticker: str) -> int:
    quality_events_path = source_root / "companies" / ticker / "context" / "quality_events.jsonl"
    if not quality_events_path.exists():
        return 0
    blocking = 0
    with quality_events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                blocking += 1
                continue
            if event.get("category") == "coverage_gap":
                continue
            blocking += 1
    return blocking


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


def _print_agent_index_plan(
    *,
    root: Optional[Path],
    cache_root: Optional[Path],
    workers: Optional[int],
    source_manifest_path: Optional[Path] = None,
    no_cache: bool = False,
    json_output: bool = False,
) -> None:
    from krw_ontology.agent_index import plan_spine_shard_release_outputs

    output_root = resolve_ontology_root(root)
    if source_manifest_path is None:
        with tempfile.TemporaryDirectory(prefix="krw-v3-index-plan-") as tmp_dir:
            payload = plan_spine_shard_release_outputs(
                output_root,
                release_id="index-preview",
                cache_root=cache_root,
                workers=workers,
                source_manifest_path=Path(tmp_dir) / "source_manifest.json",
                no_cache=no_cache,
            )
    else:
        payload = plan_spine_shard_release_outputs(
            output_root,
            release_id="index-preview",
            cache_root=cache_root,
            workers=workers,
            source_manifest_path=source_manifest_path,
            no_cache=no_cache,
        )
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return
    typer.echo("V3 index plan")
    typer.echo(f"format: {payload['format']}")
    typer.echo(f"layout: {payload['index_layout']}")
    typer.echo(f"artifacts: {payload['artifact_count']}")
    typer.echo(f"companies: {payload['company_count']}")
    typer.echo(f"dirty_companies: {payload['dirty_company_count']}")
    typer.echo(f"cached_companies: {payload['cached_company_count']}")
    typer.echo(f"dirty_spine_fragments: {payload['dirty_spine_fragment_count']}")
    typer.echo(f"cached_spine_fragments: {payload['cached_spine_fragment_count']}")
    typer.echo(
        f"dirty_tickers: {', '.join(payload['dirty_tickers']) if payload['dirty_tickers'] else '<none>'}"
    )
    typer.echo(f"workers: {payload['worker_count']}")
    typer.echo(f"cache_root: {payload['cache_root']}")
    typer.echo(f"global_spine: {payload['outputs']['global_spine']}")
    typer.echo("dag:")
    for node in payload["nodes"]:
        depends = ",".join(node.get("depends_on") or []) or "<none>"
        typer.echo(
            f"  {node['id']}: stage={node['stage']} status={node['status']} "
            f"cache_hit={node['cache_hit']} depends_on={depends}"
        )


def _build_agent_index_and_print(
    *,
    root: Optional[Path],
    no_cache: bool,
    cache_root: Optional[Path],
    workers: Optional[int],
    source_manifest_path: Optional[Path] = None,
) -> None:
    from krw_ontology.agent_index import build_spine_shard_release_outputs

    output_root = resolve_ontology_root(root)
    _exit_if_path_mutates_current(output_root, "--root")
    _exit_if_path_mutates_current(source_manifest_path, "--source-manifest")
    effective_cache_root = cache_root or (
        Path(os.environ["KRW_INDEX_FRAGMENT_CACHE_ROOT"])
        if os.environ.get("KRW_INDEX_FRAGMENT_CACHE_ROOT")
        else None
    )
    _exit_if_path_mutates_current(effective_cache_root, "--cache-root")
    progress_log = os.environ.get("KRW_BUILD_PROGRESS_LOG")
    _exit_if_path_mutates_current(
        Path(progress_log) if progress_log else None, "KRW_BUILD_PROGRESS_LOG"
    )
    result = build_spine_shard_release_outputs(
        output_root,
        release_id=output_root.name or "index-build",
        cache_root=effective_cache_root,
        workers=workers,
        source_manifest_path=source_manifest_path,
        progress_path=Path(progress_log).expanduser().resolve() if progress_log else None,
        no_cache=no_cache,
    )
    summary = result.build_summary
    typer.echo(f"V3 index built: {result.global_spine_path}")
    typer.echo(f"Build plan: {result.build_plan_path}")
    typer.echo(f"Build summary: {result.build_summary_path}")
    typer.echo(f"Build progress: {result.progress_path}")
    typer.echo(f"Shard manifest: {result.shard_manifest_path}")
    typer.echo(f"Company shards: {result.release_root / 'indexes' / 'companies'}")
    fragment_cleanup = (summary.get("artifact_cleanup") or {}).get("spine_fragments") or {}
    if fragment_cleanup.get("removed"):
        typer.echo(
            "Spine fragments: cleaned "
            f"{fragment_cleanup.get('file_count', 0)} temporary files "
            f"({fragment_cleanup.get('size_bytes', 0)} bytes)"
        )
    else:
        typer.echo(f"Spine fragments: {result.release_root / 'indexes' / 'fragments' / 'spine'}")
    typer.echo(
        "Indexed "
        f"{summary.get('artifact_count', 0)} artifacts, "
        f"{summary.get('company_count', 0)} companies, "
        f"{(summary.get('global_spine') or {}).get('counts', {}).get('global_document_catalog', 0)} documents, "
        f"{(summary.get('global_spine') or {}).get('counts', {}).get('global_object_locator', 0)} objects"
    )


def _verify_agent_index_and_print(
    *,
    root: Optional[Path],
) -> None:
    from krw_ontology.agent_index import verify_spine_shard_release

    output_root = resolve_ontology_root(root)
    verification = verify_spine_shard_release(output_root, require_manifest=False)
    typer.echo(f"V3 index verify: {'ok' if verification['ok'] else 'failed'}")
    typer.echo(f"global_spine: {verification['global_spine_path']}")
    typer.echo(f"shard_manifest: {verification['shard_manifest_path']}")
    counts = verification.get("counts") or {}
    if counts:
        typer.echo(
            "Counts: "
            f"documents={counts.get('global_document_catalog', 0)} "
            f"objects={counts.get('global_object_locator', 0)} "
            f"edges={counts.get('global_edge_spine', 0)} "
            f"topics={counts.get('global_topic_spine', 0)}"
        )
    errors = list(verification.get("errors") or [])
    if errors:
        for error in errors:
            typer.echo(f"FAIL {error}")
        raise typer.Exit(1)


@index_app.command("plan")
def index_plan_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Ontology output root. Defaults to the current working directory.",
    ),
    cache_root: Optional[Path] = typer.Option(
        None,
        "--cache-root",
        help="v3 company shard and spine fragment cache root used for build planning.",
    ),
    workers: Optional[int] = typer.Option(
        None, "--workers", min=1, help="Artifact compile worker count."
    ),
    source_manifest_path: Optional[Path] = typer.Option(
        None,
        "--source-manifest",
        help="Canonical source manifest path for manifest-only discovery.",
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="Preview with all v3 caches bypassed."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Print the deterministic v3 global spine + company shard build DAG."""
    _print_agent_index_plan(
        root=root,
        cache_root=cache_root,
        workers=workers,
        source_manifest_path=source_manifest_path,
        no_cache=no_cache,
        json_output=json_output,
    )


@index_app.command("build")
def index_build_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Ontology output root. Defaults to the current working directory.",
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Bypass v3 artifact, company shard, and spine caches."
    ),
    cache_root: Optional[Path] = typer.Option(
        None,
        "--cache-root",
        help="v3 company shard and spine fragment cache root used for build planning.",
    ),
    workers: Optional[int] = typer.Option(
        None, "--workers", min=1, help="Artifact compile worker count."
    ),
    source_manifest_path: Optional[Path] = typer.Option(
        None,
        "--source-manifest",
        help="Canonical source manifest path for manifest-only discovery.",
    ),
) -> None:
    """Build v3 global spine + company shard index outputs."""
    _build_agent_index_and_print(
        root=root,
        no_cache=no_cache,
        cache_root=cache_root,
        workers=workers,
        source_manifest_path=source_manifest_path,
    )


@index_app.command("verify")
def index_verify_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Ontology output root. Defaults to the current working directory.",
    ),
) -> None:
    """Verify v3 global spine + company shard outputs without rebuilding."""
    _verify_agent_index_and_print(
        root=root,
    )


@index_app.command("inspect")
def index_inspect_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Ontology or release root."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Inspect v3 global spine verification, shard manifest, build plan, and build summary."""
    from krw_ontology.agent_index import verify_spine_shard_release

    output_root = resolve_ontology_root(root)
    indexes_dir = output_root / "indexes"
    global_spine_path = indexes_dir / "global_spine.sqlite"
    verification = verify_spine_shard_release(output_root, require_manifest=False)
    payload = {
        "root": str(output_root),
        "index_layout": GLOBAL_SPINE_LAYOUT,
        "global_spine_path": str(global_spine_path),
        "verification": verification,
        "build_plan": _read_json_object(indexes_dir / "build_plan.json"),
        "build_summary": _read_json_object(indexes_dir / "build_summary.json"),
        "source_manifest": _read_json_object(output_root / "source_manifest.json"),
        "shard_manifest": _read_json_object(indexes_dir / "shard_manifest.json"),
    }
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return
    typer.echo(f"V3 index inspect: {'ok' if verification['ok'] else 'failed'}")
    typer.echo(f"global_spine: {global_spine_path}")
    counts = verification.get("counts") or {}
    typer.echo(
        f"counts: documents={counts.get('global_document_catalog', 0)} "
        f"objects={counts.get('global_object_locator', 0)} "
        f"edges={counts.get('global_edge_spine', 0)} "
        f"topics={counts.get('global_topic_spine', 0)}"
    )
    summary = payload["build_summary"]
    if isinstance(summary, dict):
        company_cache = summary.get("company_shard_cache") or {}
        fragment_cache = summary.get("spine_fragment_cache") or {}
        typer.echo(
            f"company_shard_cache: hits={company_cache.get('hits', 0)} "
            f"misses={company_cache.get('misses', 0)}"
        )
        typer.echo(
            f"spine_fragment_cache: hits={fragment_cache.get('hits', 0)} "
            f"misses={fragment_cache.get('misses', 0)}"
        )
    for error in verification.get("errors") or []:
        typer.echo(f"FAIL {error}")
    if not verification["ok"]:
        raise typer.Exit(1)


@index_app.command("explain-last-build")
def index_explain_last_build_cmd(
    root: Optional[Path] = typer.Option(None, "--root", help="Ontology or release root."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Explain the latest v3 index build plan, cache use, and global spine output."""

    output_root = resolve_ontology_root(root)
    summary_path = output_root / "indexes" / "build_summary.json"
    summary = _read_json_object(summary_path)
    if summary is None:
        typer.echo(f"Build summary not found: {summary_path}")
        raise typer.Exit(1)
    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True))
        return
    company_cache = summary.get("company_shard_cache") or {}
    fragment_cache = summary.get("spine_fragment_cache") or {}
    global_spine = summary.get("global_spine") or {}
    typer.echo("Last v3 index build")
    typer.echo(f"summary: {summary_path}")
    typer.echo(f"layout: {summary.get('index_layout') or '<unknown>'}")
    typer.echo(f"artifacts: {summary.get('artifact_count', 0)}")
    typer.echo(f"companies: {summary.get('company_count', 0)}")
    typer.echo(
        f"company_shard_cache: hits={company_cache.get('hits', 0)} misses={company_cache.get('misses', 0)}"
    )
    typer.echo(
        f"spine_fragment_cache: hits={fragment_cache.get('hits', 0)} misses={fragment_cache.get('misses', 0)}"
    )
    typer.echo(f"global_spine: {global_spine.get('path') or '<missing>'}")
    counts = global_spine.get("counts") or {}
    typer.echo(
        f"global_counts: documents={counts.get('global_document_catalog', 0)} "
        f"objects={counts.get('global_object_locator', 0)} "
        f"edges={counts.get('global_edge_spine', 0)} "
        f"topics={counts.get('global_topic_spine', 0)}"
    )


@index_cache_app.command("status")
def index_cache_status_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Ontology output root. Defaults to the current working directory.",
    ),
    cache_root: Optional[Path] = typer.Option(
        None,
        "--cache-root",
        help="v3 company shard, spine fragment, and router sidecar cache root.",
    ),
    workers: Optional[int] = typer.Option(
        None, "--workers", min=1, help="Planned artifact compile worker count."
    ),
    source_manifest_path: Optional[Path] = typer.Option(
        None,
        "--source-manifest",
        help="Canonical source manifest path for manifest-only discovery.",
    ),
    limit: int = typer.Option(20, "--limit", min=0, help="Maximum problem entries to print."),
) -> None:
    """Inspect v3 company shard, spine fragment, and router cache reachability."""
    output_root = resolve_ontology_root(root)
    snapshot = _v3_index_cache_snapshot(
        output_root,
        cache_root=cache_root,
        workers=workers,
        source_manifest_path=source_manifest_path,
    )
    needs_attention = bool(snapshot["missing_referenced_count"] or snapshot["unreferenced_count"])
    typer.echo(f"V3 index cache: {'attention' if needs_attention else 'ok'}")
    typer.echo(f"Cache root: {snapshot['cache_root']}")
    typer.echo(
        "Entries: "
        f"total={snapshot['entry_count']} "
        f"referenced={snapshot['referenced_existing_count']} "
        f"missing_referenced={snapshot['missing_referenced_count']} "
        f"unreferenced={snapshot['unreferenced_count']}"
    )
    typer.echo(f"Company cache: referenced={snapshot['referenced_by_kind']['company_shard']}")
    typer.echo(f"Spine cache: referenced={snapshot['referenced_by_kind']['spine_fragment']}")
    typer.echo(f"Router cache: referenced={snapshot['referenced_by_kind']['router_sidecar']}")
    typer.echo(f"Global spine cache: referenced={snapshot['referenced_by_kind']['global_spines']}")
    typer.echo(
        f"Artifact fragment cache: referenced={snapshot['referenced_by_kind']['artifact_fragment']}"
    )
    typer.echo(f"Bytes: {snapshot['total_size_bytes']}")
    tickers = snapshot.get("tickers") or []
    typer.echo(f"Tickers: {', '.join(tickers) if tickers else '<none>'}")
    problem_entries = [
        entry
        for entry in snapshot["entries"]
        if entry.get("missing") or entry.get("referenced") is False
    ]
    if not problem_entries:
        return
    typer.echo(f"Problem cache entries: {len(problem_entries)}")
    for entry in problem_entries[:limit]:
        reason = "missing" if entry.get("missing") else "unreferenced"
        typer.echo(f"{reason}: {entry['kind']} {entry['path']} ({entry['size_bytes']} bytes)")
    if len(problem_entries) > limit:
        typer.echo(f"... {len(problem_entries) - limit} more")


@index_cache_app.command("gc")
def index_cache_gc_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Ontology output root. Defaults to the current working directory.",
    ),
    cache_root: Optional[Path] = typer.Option(
        None,
        "--cache-root",
        help="v3 company shard, spine fragment, and router sidecar cache root.",
    ),
    workers: Optional[int] = typer.Option(
        None, "--workers", min=1, help="Planned artifact compile worker count."
    ),
    source_manifest_path: Optional[Path] = typer.Option(
        None,
        "--source-manifest",
        help="Canonical source manifest path for manifest-only discovery.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Delete candidates. Without this flag the command only prints a dry run.",
    ),
    include_unreferenced: bool = typer.Option(
        True,
        "--unreferenced/--no-unreferenced",
        help="Include v3 cache files that are not referenced by the current build plan.",
    ),
    limit: int = typer.Option(50, "--limit", min=0, help="Maximum candidate entries to print."),
) -> None:
    """Garbage-collect stale artifact, shard, spine, and router caches."""
    output_root = resolve_ontology_root(root)
    snapshot = _v3_index_cache_snapshot(
        output_root,
        cache_root,
        workers=workers,
        source_manifest_path=source_manifest_path,
    )
    candidates = [
        entry
        for entry in snapshot["entries"]
        if include_unreferenced and entry.get("referenced") is False and not entry.get("missing")
    ]
    deleted_count = 0
    deleted_bytes = 0
    if yes:
        _exit_if_path_mutates_current(Path(snapshot["cache_root"]), "--cache-root")
        for entry in candidates:
            path = Path(str(entry["path"]))
            size = int(entry.get("size_bytes") or 0)
            family_paths = _sqlite_cache_file_family(path)
            try:
                family_paths[0].unlink()
            except FileNotFoundError:
                continue
            for family_path in family_paths[1:]:
                family_path.unlink(missing_ok=True)
            deleted_count += 1
            deleted_bytes += size
    candidate_bytes = sum(int(entry.get("size_bytes") or 0) for entry in candidates)
    typer.echo(f"V3 index cache GC: {'deleted' if yes else 'dry-run'}")
    typer.echo(f"Cache root: {snapshot['cache_root']}")
    typer.echo(f"Candidates: {len(candidates)} bytes={candidate_bytes}")
    typer.echo(f"Deleted: {deleted_count} bytes={deleted_bytes}")
    for entry in candidates[:limit]:
        typer.echo(f"candidate: {entry['kind']} {entry['path']} ({entry['size_bytes']} bytes)")
    if len(candidates) > limit:
        typer.echo(f"... {len(candidates) - limit} more")


def _v3_index_cache_snapshot(
    root: Path,
    cache_root: Path | None,
    *,
    workers: int | None,
    source_manifest_path: Path | None,
) -> dict[str, Any]:
    from krw_ontology.agent_index import plan_spine_shard_release_outputs

    if source_manifest_path is None:
        with tempfile.TemporaryDirectory(prefix="krw-v3-cache-plan-") as tmp_dir:
            plan = plan_spine_shard_release_outputs(
                root,
                release_id="index-cache",
                cache_root=cache_root,
                workers=workers,
                source_manifest_path=Path(tmp_dir) / "source_manifest.json",
            )
    else:
        plan = plan_spine_shard_release_outputs(
            root,
            release_id="index-cache",
            cache_root=cache_root,
            workers=workers,
            source_manifest_path=source_manifest_path,
        )
    resolved_cache_root = Path(str(plan["cache_root"])).expanduser().resolve()
    referenced: dict[Path, dict[str, Any]] = {}
    tickers: set[str] = set()
    plan_payload = plan.get("plan") if isinstance(plan.get("plan"), Mapping) else {}
    artifact_items = plan_payload.get("items") if isinstance(plan_payload, Mapping) else []
    for row in artifact_items or []:
        if not isinstance(row, Mapping):
            continue
        fragment_path = str(row.get("fragment_path") or "")
        if not fragment_path:
            continue
        path = Path(fragment_path).expanduser().resolve()
        ticker = str(row.get("ticker") or "")
        if ticker:
            tickers.add(ticker)
        referenced[path] = {
            "kind": "artifact_fragment",
            "ticker": ticker or None,
            "cache_key": row.get("cache_key"),
        }
    for row in plan.get("companies") or []:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker") or "")
        if ticker:
            tickers.add(ticker)
        company_key = str(row.get("company_cache_key") or "")
        if company_key:
            path = _v3_cache_path_from_key(resolved_cache_root, "company_shard", company_key)
            referenced[path] = {
                "kind": "company_shard",
                "ticker": ticker,
                "cache_key": company_key,
            }
        fragment_key = str(row.get("spine_fragment_cache_key") or "")
        if fragment_key:
            path = _v3_cache_path_from_key(resolved_cache_root, "spine_fragment", fragment_key)
            referenced[path] = {
                "kind": "spine_fragment",
                "ticker": ticker,
                "cache_key": fragment_key,
            }
    router_cache = plan.get("router_sidecar_cache")
    if isinstance(router_cache, Mapping):
        router_key = str(router_cache.get("key") or "")
        if router_key:
            path = _v3_cache_path_from_key(
                resolved_cache_root,
                "router_sidecar",
                router_key,
            )
            referenced[path] = {
                "kind": "router_sidecar",
                "ticker": None,
                "cache_key": router_key,
            }
    global_spine_cache = plan.get("global_spine_cache")
    if isinstance(global_spine_cache, Mapping):
        global_spine_key = str(global_spine_cache.get("key") or "")
        if global_spine_key:
            referenced[
                _v3_cache_path_from_key(resolved_cache_root, "global_spine", global_spine_key)
            ] = {
                "kind": "global_spines",
                "ticker": None,
                "cache_key": global_spine_key,
            }

    entries: list[dict[str, Any]] = []
    referenced_existing_count = 0
    missing_referenced_count = 0
    total_size_bytes = 0
    for path, metadata in sorted(referenced.items(), key=lambda item: str(item[0])):
        exists = path.is_file()
        size = path.stat().st_size if exists else 0
        total_size_bytes += size
        referenced_existing_count += 1 if exists else 0
        missing_referenced_count += 0 if exists else 1
        entries.append(
            {
                **metadata,
                "path": str(path),
                "referenced": True,
                "missing": not exists,
                "size_bytes": size,
            }
        )

    actual_files = set(_iter_v3_cache_files(resolved_cache_root))
    referenced_paths = set(referenced)
    for path in sorted(actual_files - referenced_paths, key=str):
        kind = _index_cache_kind(path, resolved_cache_root)
        size = path.stat().st_size if path.is_file() else 0
        total_size_bytes += size
        entries.append(
            {
                "kind": kind,
                "ticker": None,
                "cache_key": None,
                "path": str(path),
                "referenced": False,
                "missing": False,
                "size_bytes": size,
            }
        )

    referenced_by_kind = {
        "artifact_fragment": sum(
            1 for entry in entries if entry["kind"] == "artifact_fragment" and entry["referenced"]
        ),
        "company_shard": sum(
            1 for entry in entries if entry["kind"] == "company_shard" and entry["referenced"]
        ),
        "spine_fragment": sum(
            1 for entry in entries if entry["kind"] == "spine_fragment" and entry["referenced"]
        ),
        "router_sidecar": sum(
            1 for entry in entries if entry["kind"] == "router_sidecar" and entry["referenced"]
        ),
        "global_spines": sum(
            1 for entry in entries if entry["kind"] == "global_spines" and entry["referenced"]
        ),
    }
    return {
        "cache_root": str(resolved_cache_root),
        "entry_count": len(entries),
        "referenced_existing_count": referenced_existing_count,
        "missing_referenced_count": missing_referenced_count,
        "unreferenced_count": sum(1 for entry in entries if entry.get("referenced") is False),
        "referenced_by_kind": referenced_by_kind,
        "total_size_bytes": total_size_bytes,
        "tickers": sorted(tickers),
        "entries": entries,
    }


def _v3_cache_path_from_key(cache_root: Path, kind: str, cache_key: str) -> Path:
    digest = cache_key.split(":", 1)[-1]
    directory = {
        "company_shard": "company_shards",
        "spine_fragment": "spine_fragments",
        "router_sidecar": "router_sidecars",
        "global_spine": "global_spines",
    }[kind]
    return cache_root / "v3" / directory / digest[:2] / f"{digest}.sqlite"


def _iter_v3_cache_files(cache_root: Path) -> list[Path]:
    paths: list[Path] = []
    for directory in (
        cache_root / "fragments",
        cache_root / "v3" / "company_shards",
        cache_root / "v3" / "spine_fragments",
        cache_root / "v3" / "router_sidecars",
        cache_root / "v3" / "global_spines",
    ):
        if directory.is_dir():
            paths.extend(path for path in directory.glob("*/*.sqlite") if path.is_file())
    return paths


def _index_cache_kind(path: Path, cache_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(cache_root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"cache file outside cache root: {path}") from exc
    if relative.parts and relative.parts[0] == "fragments":
        return "artifact_fragment"
    if "company_shards" in relative.parts:
        return "company_shard"
    if "spine_fragments" in relative.parts:
        return "spine_fragment"
    if "router_sidecars" in relative.parts:
        return "router_sidecar"
    if "global_spines" in relative.parts:
        return "global_spines"
    raise RuntimeError(f"unknown cache file kind: {path}")


# ---------------------------------------------------------------------------
# Observation collection (advisory-only market/macro sidecar).
#
# `observation build` is a STANDALONE collection step: it talks to providers
# and must never be part of the main release build (releases stay
# network-free). The built indexes/observations.sqlite is carried into
# releases when present, exactly like the chart_series sidecar.
# ---------------------------------------------------------------------------

# Release trees are immutable published artifacts; collecting observations
# into them (or anywhere beneath a releases/prod root) is refused — collect
# into the running root and let the release pipeline carry the sidecar.
_RELEASE_TREE_DIR_NAMES = frozenset({"releases", "prod"})


def _exit_if_observation_path_in_release_tree(path: Path, label: str) -> None:
    resolved = path.expanduser().resolve()
    if any(part in _RELEASE_TREE_DIR_NAMES for part in resolved.parts):
        typer.echo(
            f"Refusing {label}={path}: releases/prod trees are immutable release "
            "artifacts. Collect into the running root; the release build carries "
            "indexes/observations.sqlite into release candidates when present."
        )
        raise typer.Exit(1)


@observation_app.command("build")
def observation_build_cmd(
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help=(
            "Ontology output root; the store is written to "
            "<root>/indexes/observations.sqlite. Defaults to KRW_ONTOLOGY_ROOT."
        ),
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Explicit store path (overrides --root resolution).",
    ),
    seed_path: Optional[Path] = typer.Option(
        None,
        "--seed",
        help="Series seed YAML (defaults to the packaged ontology seed).",
    ),
    ticker: Optional[list[str]] = typer.Option(
        None,
        "--ticker",
        help="Equity ticker for per-ticker price/valuation families (repeatable).",
    ),
    start: Optional[str] = typer.Option(
        None, "--start", help="Observation window start (ISO date)."
    ),
    end: Optional[str] = typer.Option(None, "--end", help="Observation window end (ISO date)."),
    include_vintages: bool = typer.Option(
        True,
        "--include-vintages/--no-vintages",
        help="Fetch all vintages (first releases plus revisions) for macro series.",
    ),
) -> None:
    """Fetch observation series and build the verified observation store."""
    from krw_ontology.observation.builder import (
        build_observations_store,
        collect_observations,
        normalize_ticker,
    )
    from krw_ontology.observation.providers.fmp import FmpHistoryProvider
    from krw_ontology.observation.providers.fred import FredProvider
    from krw_ontology.observation.providers.polygon import build_polygon_provider
    from krw_ontology.observation.seed import load_series_seed, series_seed_path
    from krw_ontology.observation.store import (
        OBSERVATIONS_RELATIVE_PATH,
        verify_observations_schema,
    )

    # Never collect into immutable release trees: refuse before any work.
    if output is not None:
        _exit_if_observation_path_in_release_tree(output, "--output")
    else:
        _exit_if_observation_path_in_release_tree(resolve_ontology_root(root), "--root")
    target_path = (
        output.expanduser().resolve()
        if output is not None
        else resolve_ontology_root(root) / OBSERVATIONS_RELATIVE_PATH
    )
    seed = load_series_seed(seed_path.expanduser() if seed_path is not None else series_seed_path())

    # Provider construction is credential-gated: a provider whose API key is
    # absent is not constructed and its series degrade to unavailable status.
    providers: list[Any] = []
    if os.getenv("FRED_API_KEY", "").strip():
        providers.append(FredProvider(include_vintages=include_vintages))
    else:
        typer.echo("FRED_API_KEY not set: macro series will be unavailable")
    if os.getenv("FMP_API_KEY", "").strip():
        providers.append(FmpHistoryProvider())
    else:
        typer.echo("FMP_API_KEY not set: per-ticker FMP families will be unavailable")
    polygon_provider = build_polygon_provider()
    if polygon_provider is not None:
        providers.append(polygon_provider)
    else:
        typer.echo("POLYGON_API_KEY not set: polygon_ohlcv family will be unavailable")

    try:
        # Lowercase input is normalized to uppercase; malformed symbols
        # ("A|B", spaces, symbols) are rejected by the canonical charset rule.
        tickers = [normalize_ticker(item) for item in (ticker or [])]
    except ValueError as exc:
        typer.echo(f"Invalid --ticker value: {exc}")
        raise typer.Exit(1) from exc
    results = collect_observations(
        seed,
        providers=providers,
        tickers=tickers,
        start=start,
        end=end,
    )
    available = sum(1 for result in results if result.status == "available")
    typer.echo(
        f"Collected {available}/{len(results)} series "
        f"({len(tickers)} ticker(s), vintage mode "
        f"{'on' if include_vintages else 'off'})"
    )
    try:
        build = build_observations_store(target_path, seed, results)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Observation store build failed: {exc}")
        raise typer.Exit(1) from exc

    verification = verify_observations_schema(build.path)
    if not verification.get("ok"):
        typer.echo(
            "Observation store verification failed: "
            + ", ".join(str(error) for error in verification.get("errors") or [])
        )
        raise typer.Exit(1)
    counts = dict(build.counts)
    typer.echo(f"Observation store verified: {build.path}")
    typer.echo("Counts: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    typer.echo("Doctrine: advisory_only=true, source_usage=research_only (no vendor names served)")


@observation_app.command("verify")
def observation_verify_cmd(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        help="Observation store path (defaults to <root>/indexes/observations.sqlite).",
    ),
    root: Optional[Path] = typer.Option(
        None, "--root", help="Ontology output root used when --path is omitted."
    ),
) -> None:
    """Verify a built observation store against the schema-v1 contract."""
    from krw_ontology.observation.store import (
        OBSERVATIONS_RELATIVE_PATH,
        verify_observations_schema,
    )

    resolved = (
        path.expanduser().resolve()
        if path is not None
        else resolve_ontology_root(root) / OBSERVATIONS_RELATIVE_PATH
    )
    verification = verify_observations_schema(resolved)
    if not verification.get("ok"):
        typer.echo(
            "Observation store verification failed: "
            + ", ".join(str(error) for error in verification.get("errors") or [])
        )
        raise typer.Exit(1)
    typer.echo(f"Observation store OK: {resolved}")
    typer.echo(
        "Counts: "
        + ", ".join(f"{key}={value}" for key, value in sorted(verification["counts"].items()))
    )
