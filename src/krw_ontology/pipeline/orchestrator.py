"""Pipeline orchestrator with checkpoint-based resume (Section 9.3)."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.config.settings import PipelineConfig
from krw_ontology.errors import PipelineStageError
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.pipeline.checkpoint import CheckpointManager
from krw_ontology.pipeline.stages.build_governance import build_governance_artifacts
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.pipeline.stages.build_reports import build_reports
from krw_ontology.pipeline.stages.build_spans import build_spans
from krw_ontology.pipeline.stages.clean_to_markdown import clean_to_markdown
from krw_ontology.pipeline.stages.discover_source import discover_source
from krw_ontology.pipeline.stages.download_source import download_source
from krw_ontology.pipeline.stages.extract_assumption_candidates import extract_assumption_candidates
from krw_ontology.pipeline.stages.extract_business_activities import extract_business_activities
from krw_ontology.pipeline.stages.extract_evidence_quotes import extract_evidence_quotes
from krw_ontology.pipeline.stages.extract_external_factor_exposures import extract_external_factor_exposures
from krw_ontology.pipeline.stages.extract_research_claims import extract_research_claims
from krw_ontology.pipeline.stages.extract_sections import extract_sections
from krw_ontology.pipeline.stages.extract_xbrl import extract_xbrl
from krw_ontology.pipeline.stages.generate_canonical_artifacts import generate_canonical_artifacts
from krw_ontology.pipeline.stages.generate_edges import generate_edges
from krw_ontology.pipeline.stages.generate_support_links import generate_support_links
from krw_ontology.pipeline.stages.resolve_ticker import resolve_ticker
from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology
from krw_ontology.schema.id_utils import generate_source_document_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import atomic_write_json
from krw_ontology.utils.logging import setup_logging

logger = logging.getLogger("krw_ontology")

PIPELINE_STAGES = [
    "resolve_ticker",
    "discover_source_document",
    "download_source_document",
    "clean_to_markdown",
    "extract_sections",
    "build_source_spans",
    "extract_xbrl_facts",
    "extract_evidence_quotes",
    "extract_research_claims",
    "extract_business_activities",
    "extract_external_factor_exposures",
    "extract_assumption_candidates",
    "generate_canonical_artifacts",
    "validate_ontology",
    "generate_support_links",
    "generate_edges",
    "validate_edges",
    "build_governance_artifacts",
    "build_indexes",
    "build_graph_report",
]


def _next_stage(current: str) -> str | None:
    idx = PIPELINE_STAGES.index(current) if current in PIPELINE_STAGES else -1
    if idx + 1 < len(PIPELINE_STAGES):
        return PIPELINE_STAGES[idx + 1]
    return None


def _derive_period_from_dates(
    document_type: str,
    report_date: str | None,
    filing_date: str | None,
    *,
    fiscal_year: object = None,
    fiscal_period: object = None,
    fiscal_year_end: object = None,
) -> str:
    """Derive a stable period key before source contents are parsed."""
    from krw_ontology.pipeline.stages.discover_source import derive_period_key

    return derive_period_key(
        document_type,
        report_date,
        filing_date,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        fiscal_year_end=fiscal_year_end,
    )


def _month_from_date(value: str | None) -> int | None:
    if not value or len(value) < 7:
        return None
    try:
        month = int(value[5:7])
    except ValueError:
        return None
    return month if 1 <= month <= 12 else None


def run_pipeline(
    ticker: str,
    document_type: str = "10-K",
    latest: bool = False,
    period: str | None = None,
    force: bool = False,
    output_dir: Path | None = None,
    pilot: bool = False,
) -> None:
    """Run the full evidence ontology pipeline for a given ticker."""
    setup_logging()
    config = PipelineConfig.load()
    if pilot:
        config.apply_pilot_mode()

    ticker = ticker.upper()
    doc_type_key = normalize_doc_type(document_type)
    base_dir = output_dir or Path.cwd()

    # Context dict shared across stages
    ctx: dict = {
        "ticker": ticker,
        "document_type": document_type,
        "doc_type_key": doc_type_key,
        "config": config,
        "force": force,
        "latest": latest,
        "base_dir": base_dir,
        "period": period,
    }

    # Run stages
    checkpoint_path: Path | None = None

    for stage in PIPELINE_STAGES:
        # Update checkpoint path once we know the period
        if checkpoint_path and _is_code_stage(stage):
            checkpoint = CheckpointManager(checkpoint_path)
            if not force and checkpoint.is_stage_complete(stage):
                logger.info("Skipping completed stage: %s", stage, extra={"stage": stage})
                continue

        logger.info("Running stage: %s", stage, extra={"stage": stage})
        try:
            _execute_stage(stage, ctx)
        except PipelineStageError as e:
            logger.error("Stage %s failed: %s", stage, e, extra={"stage": stage})
            raise

        # Sync checkpoint_path from ctx after discover_source_document sets it
        checkpoint_path = ctx.get("checkpoint_path")

        if checkpoint_path:
            checkpoint = CheckpointManager(checkpoint_path)
            checkpoint.mark_complete(stage)


def _is_code_stage(stage: str) -> bool:
    return stage in {
        "resolve_ticker", "discover_source_document", "download_source_document",
        "clean_to_markdown", "extract_sections", "build_source_spans",
        "extract_xbrl_facts", "extract_evidence_quotes",
        "extract_research_claims",
        "extract_business_activities",
        "extract_external_factor_exposures", "extract_assumption_candidates",
        "generate_canonical_artifacts",
        "validate_ontology", "generate_support_links", "generate_edges", "validate_edges",
        "build_governance_artifacts", "build_indexes", "build_graph_report",
    }


def _execute_stage(stage: str, ctx: dict) -> None:
    """Execute a single pipeline stage, dispatching to the right handler."""
    ticker = ctx["ticker"]
    config = ctx["config"]
    base_dir = ctx["base_dir"]
    doc_type_key = ctx["doc_type_key"]

    if stage == "resolve_ticker":
        result = resolve_ticker(ticker, config)
        ctx["cik"] = result["cik"]
        ctx["company_name"] = result["company_name"]

    elif stage == "discover_source_document":
        result = discover_source(
            ctx["cik"],
            ctx["document_type"],
            ctx["latest"],
            config,
            period=ctx.get("period"),
        )
        ctx["accession_number"] = result["accession_number"]
        ctx["filing_date"] = result["filing_date"]
        ctx["source_url"] = result["source_url"]
        ctx["report_date"] = result["report_date"]
        ctx["fiscal_year"] = result.get("fiscal_year")
        ctx["fiscal_period"] = result.get("fiscal_period")
        ctx["fiscal_year_end"] = result.get("fiscal_year_end")
        # Derive period: prefer explicit override, then report_date/filing_date calendar key
        if ctx.get("period"):
            pass  # Use explicit period from CLI
        elif ctx.get("report_date"):
            ctx["period"] = _derive_period_from_dates(
                ctx["document_type"],
                ctx.get("report_date"),
                ctx.get("filing_date"),
                fiscal_year=ctx.get("fiscal_year"),
                fiscal_period=ctx.get("fiscal_period"),
                fiscal_year_end=ctx.get("fiscal_year_end"),
            )
        elif ctx.get("filing_date"):
            ctx["period"] = _derive_period_from_dates(
                ctx["document_type"],
                None,
                ctx.get("filing_date"),
                fiscal_year=ctx.get("fiscal_year"),
                fiscal_period=ctx.get("fiscal_period"),
                fiscal_year_end=ctx.get("fiscal_year_end"),
            )

        # Now we know period, set up paths
        period = ctx["period"]
        sources_dir = base_dir / "companies" / ticker / "sources" / doc_type_key / period
        ontology_dir = base_dir / "companies" / ticker / "ontology" / doc_type_key / period
        ctx["sources_dir"] = sources_dir
        ctx["ontology_dir"] = ontology_dir
        ctx["raw_html_path"] = sources_dir / "raw.html"
        ctx["clean_md_path"] = sources_dir / "clean.md"
        ctx["metadata_path"] = sources_dir / "metadata.json"

        source_doc_id = generate_source_document_id(ticker, period, doc_type_key)
        ctx["source_document_id"] = source_doc_id

        # Write pipeline_config.json
        _write_pipeline_config(ctx, ontology_dir)

        # Initialize checkpoint
        checkpoint_path = ontology_dir / ".checkpoint.json"
        ctx["checkpoint_path"] = checkpoint_path
        checkpoint = CheckpointManager(checkpoint_path)
        checkpoint.mark_complete("resolve_ticker")
        checkpoint.mark_complete("discover_source_document")

    elif stage == "download_source_document":
        result = download_source(ctx["source_url"], ctx["raw_html_path"], config)
        ctx["raw_html_sha256"] = result["sha256"]
        ctx["raw_html_bytes"] = result["bytes"]
        _update_metadata(ctx)

    elif stage == "clean_to_markdown":
        result = clean_to_markdown(ctx["raw_html_path"], ctx["clean_md_path"])
        ctx["clean_md_sha256"] = result["sha256"]

    elif stage == "extract_sections":
        result = extract_sections(
            ctx["clean_md_path"],
            raw_html_path=ctx.get("raw_html_path"),
            output_dir=ctx["ontology_dir"],
            document_type=ctx["document_type"],
        )
        ctx["sections"] = result["sections"]
        ctx["section_quality"] = result.get("section_quality", {})
        if (
            config.fail_on_section_quality
            and ctx["section_quality"].get("status") == "fail"
        ):
            fail_reasons = ctx["section_quality"].get("fail_reasons", [])
            reason_text = ", ".join(fail_reasons) if fail_reasons else "unknown"
            raise PipelineStageError(f"extract_sections: section_quality=fail ({reason_text})")
        ctx["clean_md_text"] = ctx["clean_md_path"].read_text()

    elif stage == "build_source_spans":
        result = build_spans(
            sections=ctx["sections"],
            doc_type_key=doc_type_key,
            document_type=ctx["document_type"],
            ticker=ticker,
            period=ctx["period"],
            source_document_id=ctx["source_document_id"],
            clean_md_text=ctx["clean_md_text"],
            output_path=ctx["ontology_dir"] / "spans.jsonl",
        )
        ctx["spans"] = result["spans"]

    elif stage == "extract_xbrl_facts":
        result = extract_xbrl(
            raw_html_path=ctx["raw_html_path"],
            ticker=ticker,
            period=ctx["period"],
            doc_type_key=doc_type_key,
            document_type=ctx["document_type"],
            source_document_id=ctx["source_document_id"],
            output_path=ctx["ontology_dir"] / "xbrl_facts.jsonl",
        )
        ctx["xbrl_status"] = result.get("status", "ok")

    elif stage == "extract_evidence_quotes":
        worker = _make_extraction_worker(config, base_dir, stage)
        quotes = asyncio.run(extract_evidence_quotes(
            worker=worker,
            ontology_dir=ctx["ontology_dir"],
            ticker=ticker,
            period=ctx["period"],
            doc_type=ctx["document_type"],
            concurrency=config.concurrency_for_stage(stage),
            force=ctx["force"],
            span_pruning=config.span_pruning,
            pilot_max_quote_spans=config.pilot_max_quote_spans,
            batch_size=config.batch_size_for_stage(stage, default=10),
        ))
        ctx["quotes"] = quotes

    elif stage == "extract_research_claims":
        worker = _make_extraction_worker(config, base_dir, stage)
        claims = asyncio.run(extract_research_claims(
            worker=worker,
            ontology_dir=ctx["ontology_dir"],
            ticker=ticker,
            period=ctx["period"],
            doc_type=ctx["document_type"],
            quotes=ctx.get("quotes"),
            concurrency=config.concurrency_for_stage(stage),
            force=ctx["force"],
            batch_size=config.batch_size_for_stage(stage, default=12),
        ))
        ctx["claims"] = claims

    elif stage == "extract_business_activities":
        worker = _make_extraction_worker(config, base_dir, stage)
        activities = asyncio.run(extract_business_activities(
            worker=worker,
            ontology_dir=ctx["ontology_dir"],
            ticker=ticker,
            period=ctx["period"],
            doc_type=ctx["document_type"],
            claims=ctx.get("claims"),
        ))
        ctx["business_activities"] = activities

    elif stage == "extract_external_factor_exposures":
        worker = _make_extraction_worker(config, base_dir, stage)
        exposures = asyncio.run(extract_external_factor_exposures(
            worker=worker,
            ontology_dir=ctx["ontology_dir"],
            ticker=ticker,
            period=ctx["period"],
            doc_type=ctx["document_type"],
            claims=ctx.get("claims"),
            business_activities=ctx.get("business_activities"),
        ))
        ctx["external_factor_exposures"] = exposures

    elif stage == "extract_assumption_candidates":
        worker = _make_extraction_worker(config, base_dir, stage)
        assumptions = asyncio.run(extract_assumption_candidates(
            worker=worker,
            ontology_dir=ctx["ontology_dir"],
            ticker=ticker,
            period=ctx["period"],
            doc_type=ctx["document_type"],
            claims=ctx.get("claims"),
            quotes=ctx.get("quotes"),
            concurrency=config.concurrency_for_stage(stage),
            force=ctx["force"],
        ))
        ctx["assumptions"] = assumptions

    elif stage == "generate_canonical_artifacts":
        canonical_artifacts = generate_canonical_artifacts(
            ontology_dir=ctx["ontology_dir"],
            ticker=ticker,
            period=ctx["period"],
            document_type=ctx["document_type"],
            source_document_id=ctx["source_document_id"],
            clean_text_hash=ctx.get("clean_md_sha256"),
            raw_text_hash=ctx.get("raw_html_sha256"),
        )
        ctx["canonical_artifacts"] = canonical_artifacts

    elif stage == "generate_edges":
        worker = _make_extraction_worker(config, base_dir, stage)
        edges = asyncio.run(generate_edges(
            worker=worker,
            ontology_dir=ctx["ontology_dir"],
            ticker=ticker,
            period=ctx["period"],
            doc_type=ctx["document_type"],
        ))
        ctx["edges"] = edges

    elif stage == "generate_support_links":
        support_links = generate_support_links(
            ontology_dir=ctx["ontology_dir"],
            ticker=ticker,
            period=ctx["period"],
            document_type=ctx["document_type"],
            source_document_id=ctx["source_document_id"],
        )
        ctx["support_links"] = support_links

    elif stage == "validate_ontology":
        result = run_validate_ontology(ctx["ontology_dir"], include_edges=False)
        ctx["validation_result"] = result

    elif stage == "validate_edges":
        result = run_validate_ontology(ctx["ontology_dir"], include_edges=True)
        ctx["edge_validation_result"] = result

    elif stage == "build_governance_artifacts":
        governance = build_governance_artifacts(
            ontology_dir=ctx["ontology_dir"],
            ticker=ticker,
            period=ctx["period"],
            document_type=ctx["document_type"],
            source_document_id=ctx["source_document_id"],
            config=config,
            raw_html_sha256=ctx.get("raw_html_sha256"),
            clean_md_sha256=ctx.get("clean_md_sha256"),
        )
        ctx["governance_artifacts"] = governance

    elif stage == "build_indexes":
        build_indexes(
            ticker=ticker,
            period=ctx["period"],
            doc_type_key=doc_type_key,
            document_type=ctx["document_type"],
            ontology_dir=ctx["ontology_dir"],
            sources_dir=ctx["sources_dir"],
            output_dir=base_dir,
        )

    elif stage == "build_graph_report":
        build_reports(
            ticker=ticker,
            period=ctx["period"],
            document_type=ctx["document_type"],
            ontology_dir=ctx["ontology_dir"],
        )

    else:
        raise PipelineStageError(f"Unknown stage: {stage}")


def _make_extraction_worker(config: PipelineConfig, base_dir: Path, stage_name: str) -> ExtractionWorker:
    return ExtractionWorker(
        model=config.model_for_stage(stage_name),
        cwd=base_dir,
        max_retries=config.max_retries,
        call_timeout_s=config.call_timeout_seconds,
        max_turns=config.max_turns,
    )


def _write_pipeline_config(ctx: dict, ontology_dir: Path) -> None:
    """Write pipeline_config.json at the start of the run."""
    ontology_dir.mkdir(parents=True, exist_ok=True)
    config_data = {
        "cli_version": "0.1.0",
        "model": ctx["config"].model,
        "execution_mode": ctx["config"].execution_mode,
        "stage_models": ctx["config"].stage_models,
        "ai_concurrency": ctx["config"].ai_concurrency,
        "stage_concurrency": ctx["config"].stage_concurrency,
        "max_turns": ctx["config"].max_turns,
        "call_timeout_seconds": ctx["config"].call_timeout_seconds,
        "fail_on_section_quality": ctx["config"].fail_on_section_quality,
        "span_pruning": ctx["config"].span_pruning,
        "pilot_max_quote_spans": ctx["config"].pilot_max_quote_spans,
        "schema_version": SCHEMA_VERSION,
        "cli_flags": {
            "document_type": ctx["document_type"],
            "latest": ctx["latest"],
            "force": ctx["force"],
            "pilot": ctx["config"].execution_mode == "pilot",
        },
        "started_at": datetime.now(timezone.utc).isoformat(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": sys.platform,
    }
    atomic_write_json(ontology_dir / "pipeline_config.json", config_data)


def _update_metadata(ctx: dict) -> None:
    """Write or update metadata.json in sources directory."""
    metadata = {
        "ticker": ctx["ticker"],
        "cik": ctx["cik"],
        "accession_number": ctx["accession_number"],
        "filing_date": ctx["filing_date"],
        "document_type": ctx["document_type"],
        "doc_type_key": ctx["doc_type_key"],
        "period": ctx["period"],
        "fiscal_year_end": ctx.get("report_date", ""),
        "raw_html_sha256": ctx.get("raw_html_sha256", ""),
        "raw_html_bytes": ctx.get("raw_html_bytes", 0),
        "source_url": ctx["source_url"],
        "pipeline_version": "0.1.0",
        "schema_version": SCHEMA_VERSION,
    }
    if ctx.get("clean_md_sha256"):
        metadata["clean_md_sha256"] = ctx["clean_md_sha256"]
    metadata["extracted_at"] = datetime.now(timezone.utc).isoformat()

    atomic_write_json(ctx["metadata_path"], metadata)
