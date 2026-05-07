"""Typer CLI app with 4 command skeleton."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from krw_ontology.cli.init_workspace import init_workspace
from krw_ontology.config.paths import ONTOLOGY_ROOT_ENV, resolve_ontology_root

app = typer.Typer(
    name="krw-ontology",
    help="Evidence Ontology Builder - file-canonical equity research pipeline",
)

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
    output_root = resolve_ontology_root(root, fallback_to_cwd=False)
    stable_root = (
        resolve_ontology_root(publish_root, fallback_to_cwd=False)
        if publish_root is not None
        else None
    )
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
                    _publish_ticker_tree(output_root, stable_root, ticker)
                    publish_index_result = build_agent_index(
                        stable_root,
                        index_path=publish_index_path,
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

    for ticker in run_tickers:
        source_dir = source_root / "companies" / ticker
        target_dir = stable_root / "companies" / ticker
        typer.echo(f"Publishing {ticker}: {source_dir} -> {target_dir}")
        if not dry_run:
            try:
                _publish_ticker_tree(source_root, stable_root, ticker)
            except FileNotFoundError as exc:
                typer.echo(str(exc))
                raise typer.Exit(1) from exc

    if dry_run:
        typer.echo("Dry run complete; no files changed.")
        return

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
