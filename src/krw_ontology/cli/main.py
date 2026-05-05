"""Typer CLI app with 4 command skeleton."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from krw_ontology.cli.init_workspace import init_workspace

app = typer.Typer(
    name="krw-ontology",
    help="10-K Evidence Ontology Builder — file-canonical equity research pipeline",
)

ACCEPTED_DOC_TYPES = {"10-K"}


def validate_document_type(doc_type: str) -> str:
    """Raise typer.BadParameter if not in ACCEPTED_DOC_TYPES."""
    if doc_type not in ACCEPTED_DOC_TYPES:
        raise typer.BadParameter(
            f"Document type '{doc_type}' not supported in v1. "
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
    document_type: str = typer.Option("10-K", "--document-type", help="Document type (10-K only in v1)"),
    latest: bool = typer.Option(False, "--latest", help="Use most recent filing"),
    period: Optional[str] = typer.Option(None, "--period", help="Explicit period override (e.g., FY2025)"),
    force: bool = typer.Option(False, "--force", help="Re-process even if content hash matches"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Override default output directory"),
) -> None:
    """Build evidence ontology from SEC filing for a given ticker."""
    validate_document_type(document_type)
    from krw_ontology.pipeline.orchestrator import run_pipeline
    run_pipeline(
        ticker=ticker,
        document_type=document_type,
        latest=latest,
        period=period,
        force=force,
        output_dir=output_dir,
    )
    typer.echo(f"Pipeline complete for {ticker}")


@app.command("validate")
def validate_cmd(
    ticker: str = typer.Argument(..., help="Stock ticker symbol"),
    document_type: str = typer.Option("10-K", "--document-type", help="Document type"),
    period: Optional[str] = typer.Option(None, "--period", help="Filing period"),
) -> None:
    """Re-run validation on existing ontology artifacts."""
    validate_document_type(document_type)
    from krw_ontology.config.constants import DOCUMENT_TYPE_KEY
    from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology

    ticker = ticker.upper()
    doc_type_key = DOCUMENT_TYPE_KEY

    if period is None:
        ontology_base = Path.cwd() / "companies" / ticker / "ontology" / doc_type_key
        if not ontology_base.exists():
            typer.echo(f"No ontology data found for {ticker}")
            raise typer.Exit(1)
        periods = sorted(d.name for d in ontology_base.iterdir() if d.is_dir())
        if not periods:
            typer.echo(f"No periods found for {ticker}")
            raise typer.Exit(1)
        period = periods[-1]
        typer.echo(f"Using latest period: {period}")

    ontology_dir = Path.cwd() / "companies" / ticker / "ontology" / doc_type_key / period
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
) -> None:
    """Regenerate graph_report.md and audit_report.md from existing artifacts."""
    validate_document_type(document_type)

    from krw_ontology.config.constants import DOCUMENT_TYPE_KEY
    from krw_ontology.pipeline.stages.build_indexes import build_indexes
    from krw_ontology.pipeline.stages.build_reports import build_reports

    ticker = ticker.upper()
    doc_type_key = DOCUMENT_TYPE_KEY

    if period is None:
        ontology_base = Path.cwd() / "companies" / ticker / "ontology" / doc_type_key
        if not ontology_base.exists():
            typer.echo(f"No ontology data found for {ticker}")
            raise typer.Exit(1)
        periods = sorted(d.name for d in ontology_base.iterdir() if d.is_dir())
        if not periods:
            typer.echo(f"No periods found for {ticker}")
            raise typer.Exit(1)
        period = periods[-1]
        typer.echo(f"Using latest period: {period}")

    ontology_dir = Path.cwd() / "companies" / ticker / "ontology" / doc_type_key / period
    sources_dir = Path.cwd() / "companies" / ticker / "sources" / doc_type_key / period
    if not ontology_dir.exists():
        typer.echo(f"Ontology directory not found: {ontology_dir}")
        raise typer.Exit(1)

    build_indexes(
        ticker=ticker,
        period=period,
        doc_type_key=doc_type_key,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=Path.cwd(),
    )

    build_reports(
        ticker=ticker,
        period=period,
        document_type=document_type,
        ontology_dir=ontology_dir,
    )
    typer.echo(f"Report generated at {ontology_dir}")
