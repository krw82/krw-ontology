"""Quality repair job executors.

The CLI keeps execution behind an explicit confirmation because these jobs can
invoke Agent SDK calls. Planning and inspection remain read-only.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.config.settings import PipelineConfig
from krw_ontology.errors import PipelineStageError
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.pipeline.ai_batches import batch_cache_dir
from krw_ontology.pipeline.queue import PipelineQueue
import krw_ontology.pipeline.stages.extract_assumption_candidates as assumption_stage
from krw_ontology.pipeline.stages.extract_assumption_candidates import (
    extract_assumption_candidates,
)
from krw_ontology.pipeline.stages.build_company_context import build_company_context
from krw_ontology.pipeline.stages.build_governance import build_governance_artifacts
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.pipeline.stages.build_reports import build_reports
from krw_ontology.pipeline.stages.extract_business_activities import extract_business_activities
from krw_ontology.pipeline.stages.extract_evidence_quotes import extract_evidence_quotes
from krw_ontology.pipeline.stages.extract_external_factor_exposures import extract_external_factor_exposures
from krw_ontology.pipeline.stages.extract_research_claims import extract_research_claims
from krw_ontology.pipeline.stages.extract_sections import extract_sections
from krw_ontology.pipeline.stages.generate_canonical_artifacts import generate_canonical_artifacts
from krw_ontology.pipeline.stages.generate_edges import generate_edges
from krw_ontology.pipeline.stages.generate_support_links import generate_support_links
from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology
from krw_ontology.quality.models import (
    BATCH_FAILURE,
    DIRECT_XBRL_METRIC_GAP,
    DOCS_MISSING,
    EXECUTABLE_REPAIR_KINDS,
    NORMALIZE_NUMERIC,
    PENDING,
    REPAIR_REFERENCE,
    SECTION_FAIL,
    SECTION_WARN,
    RepairJob,
)
from krw_ontology.quality.queue import QualityRepairStore
from krw_ontology.quality.safe_repair import (
    run_direct_xbrl_metric_gap_repair,
    run_numeric_revalidation,
    run_reference_rebuild,
)
from krw_ontology.utils.io import read_jsonl, write_jsonl


# User-facing queue selection excludes deferred kinds such as normalize_numeric.
# Section extraction repairs are intentionally disabled: section quality remains
# a diagnostic signal, but repair must not mutate or rerun documents from it.
# Keep legacy no-op support so existing plans can drain without resectioning.
SUPPORTED_RUN_KINDS = EXECUTABLE_REPAIR_KINDS | {NORMALIZE_NUMERIC, SECTION_FAIL, SECTION_WARN}
DEFAULT_BATCH_FAILURE_REPAIR_CONCURRENCY = 1
DEFAULT_BATCH_FAILURE_REPAIR_MODEL = "glm-5.2"
DOCUMENT_CLEAN_RERUN_STAGES = {
    "extract_evidence_quotes",
    "extract_research_claims",
    "extract_assumption_candidates",
}
DOCUMENT_CLEAN_RERUN_ORDER = [
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
    "build_graph_report",
]


def run_repair_jobs(
    *,
    store: QualityRepairStore,
    jobs: list[RepairJob],
    root: Path,
    concurrency: int | None = None,
) -> dict[str, int]:
    """Run repair jobs sequentially and update job status."""
    counts = {
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "resolved": 0,
        "unresolved": 0,
        "enqueued": 0,
        "active": 0,
    }
    config = PipelineConfig.load()
    resolved_root = root.expanduser().resolve()
    for job in jobs:
        job = store.load_job(job.job_id)
        if job.status != PENDING:
            if job.payload.get("repair_outcome") not in {
                "superseded_by_document_clean_rerun",
                "superseded_by_document_rerun",
            }:
                counts["skipped"] += 1
            continue
        if job.kind not in SUPPORTED_RUN_KINDS:
            counts["skipped"] += 1
            continue
        store.mark_running(job)
        try:
            _normalize_job_paths_for_running_root(job, root=resolved_root)
            _run_one(job, root=resolved_root, config=config, concurrency=concurrency)
        except Exception as exc:
            store.mark_failed(job, str(exc))
            counts["failed"] += 1
            continue
        store.mark_succeeded(job)
        superseded_count = _mark_same_document_jobs_superseded_after_document_rerun(store, job)
        counts["succeeded"] += 1
        if superseded_count:
            counts["succeeded"] += superseded_count
            job.payload["superseded_same_document_job_count"] = superseded_count
            store.save_job(job)
        counts["resolved"] += int(job.payload.get("resolved_count") or job.payload.get("candidate_now_valid_count") or 0)
        counts["unresolved"] += int(job.payload.get("unresolved_count") or 0)
        if str(job.payload.get("pipeline_queue_action") or "").startswith("queued_"):
            counts["enqueued"] += int(job.payload.get("pipeline_queue_job_count") or 1)
        if str(job.payload.get("pipeline_queue_action") or "").startswith("skipped_active"):
            counts["active"] += int(job.payload.get("active_pipeline_job_count") or 1)
    return counts


def _mark_same_document_jobs_superseded_after_document_rerun(
    store: QualityRepairStore,
    completed_job: RepairJob,
) -> int:
    if not _is_successful_document_rerun_repair(completed_job):
        return 0
    document_key = _repair_document_key(completed_job)
    if document_key is None:
        return 0

    superseded = 0
    superseded_ids: list[str] = []
    for other in store.list_jobs(plan_id=completed_job.plan_id, statuses=[PENDING]):
        if other.job_id == completed_job.job_id:
            continue
        if other.kind == DOCS_MISSING:
            continue
        if _repair_document_key(other) != document_key:
            continue
        other.payload["repair_outcome"] = "superseded_by_document_rerun"
        other.payload["superseded_by_job_id"] = completed_job.job_id
        other.payload["superseded_by_kind"] = completed_job.kind
        other.payload["superseded_reason"] = "same_document_rerun_enqueued_or_completed"
        other.payload["verification_required"] = True
        store.mark_succeeded(other)
        superseded += 1
        superseded_ids.append(other.job_id)

    if superseded_ids:
        completed_job.payload["superseded_same_document_job_ids"] = superseded_ids
    return superseded


def _is_successful_document_rerun_repair(job: RepairJob) -> bool:
    if job.kind != BATCH_FAILURE or job.payload.get("repair_strategy") != "document_clean_rerun":
        return False
    return job.payload.get("repair_outcome") in {
        "document_clean_rerun_replaced_needs_verify",
        "enqueued_document_clean_rerun",
        "document_clean_rerun_already_active",
        "document_clean_rerun_full_refresh_already_active",
    }


def _repair_document_key(job: RepairJob) -> tuple[str, str, str] | None:
    ticker = str(job.ticker or "").upper()
    period = str(job.period or "")
    doc_type_key = str(job.doc_type_key or "")
    if not doc_type_key and job.document_type:
        doc_type_key = normalize_doc_type(str(job.document_type))
    if not ticker or not period or not doc_type_key:
        return None
    return (ticker, doc_type_key, period)


def _run_one(
    job: RepairJob,
    *,
    root: Path,
    config: PipelineConfig,
    concurrency: int | None = None,
) -> None:
    if job.kind == BATCH_FAILURE:
        asyncio.run(_retry_batch_failure(job, root=root, config=config, concurrency=concurrency))
        return
    if job.kind == DOCS_MISSING:
        _enqueue_docs_missing(job, root=root)
        return
    if job.kind == NORMALIZE_NUMERIC:
        run_numeric_revalidation(job, root=root)
        return
    if job.kind == REPAIR_REFERENCE:
        run_reference_rebuild(job, root=root)
        return
    if job.kind == DIRECT_XBRL_METRIC_GAP:
        run_direct_xbrl_metric_gap_repair(job, root=root)
        return
    if job.kind in {SECTION_FAIL, SECTION_WARN}:
        job.payload["repair_outcome"] = "section_repair_disabled"
        job.payload["verification_required"] = False
        return
    raise ValueError(f"unsupported repair kind: {job.kind}")


def _enqueue_docs_missing(job: RepairJob, *, root: Path) -> None:
    queue = PipelineQueue(root)
    queue.ensure_dirs()
    action = str(job.payload.get("action") or "full_refresh_fallback")
    if action == "targeted_filing_update":
        _enqueue_targeted_docs_missing(job, queue=queue)
        return
    _enqueue_full_refresh_fallback(job, queue=queue)


def _enqueue_document_clean_rerun(job: RepairJob, *, root: Path) -> None:
    if not job.document_type or not job.period:
        raise ValueError("document clean rerun enqueue requires document_type and period")
    queue = PipelineQueue(root)
    queue.ensure_dirs()

    active_full_refresh = queue.active_full_refresh_for_ticker(job.ticker)
    if active_full_refresh is not None:
        job.payload["pipeline_queue_action"] = "skipped_active_full_refresh"
        job.payload["pipeline_queue_job_id"] = active_full_refresh.job_id
        job.payload["pipeline_queue_status"] = active_full_refresh.status
        job.payload["active_pipeline_job_count"] = 1
        job.payload["repair_outcome"] = "document_clean_rerun_full_refresh_already_active"
        job.payload["verification_required"] = True
        return

    active_update = queue.active_update_job_for_filing(
        job.ticker,
        document_type=job.document_type,
        period=job.period,
    )
    if active_update is not None:
        job.payload["pipeline_queue_action"] = "skipped_active_filing_update"
        job.payload["pipeline_queue_job_id"] = active_update.job_id
        job.payload["pipeline_queue_status"] = active_update.status
        job.payload["active_pipeline_job_count"] = 1
        job.payload["repair_outcome"] = "document_clean_rerun_already_active"
        job.payload["verification_required"] = True
        return

    pipeline_job = queue.add_update_job(
        job.ticker,
        document_type=job.document_type,
        periods=[str(job.period)],
        latest=False,
        force=True,
        publish_root=None,
    )
    job.payload["pipeline_queue_action"] = "queued_targeted_filing_update"
    job.payload["pipeline_queue_job_ids"] = [pipeline_job.job_id]
    job.payload["pipeline_queue_job_count"] = 1
    job.payload["pipeline_queue_statuses"] = {pipeline_job.job_id: pipeline_job.status}
    job.payload["targeted_update_count"] = 1
    job.payload["pipeline_queue_force"] = True
    job.payload["repair_outcome"] = "enqueued_document_clean_rerun"
    job.payload["verification_required"] = True


def _enqueue_full_refresh_fallback(job: RepairJob, *, queue: PipelineQueue) -> None:
    active = queue.active_job_for_ticker(job.ticker)
    if active is not None:
        job.payload["pipeline_queue_action"] = "skipped_active_job"
        job.payload["pipeline_queue_job_id"] = active.job_id
        job.payload["pipeline_queue_status"] = active.status
        job.payload["active_pipeline_job_count"] = 1
        job.payload["repair_outcome"] = "pipeline_job_already_active"
        job.payload["verification_required"] = True
        return
    pipeline_job = queue.add_job(
        job.ticker,
        years=int(job.payload.get("years") or 3),
        force=False,
        publish_root=None,
    )
    job.payload["pipeline_queue_action"] = "queued_full_refresh_fallback"
    job.payload["pipeline_queue_job_id"] = pipeline_job.job_id
    job.payload["pipeline_queue_job_count"] = 1
    job.payload["pipeline_queue_status"] = pipeline_job.status
    job.payload["repair_outcome"] = "enqueued_full_refresh_fallback"
    job.payload["verification_required"] = True


def _enqueue_targeted_docs_missing(job: RepairJob, *, queue: PipelineQueue) -> None:
    active_full_refresh = queue.active_full_refresh_for_ticker(job.ticker)
    if active_full_refresh is not None:
        job.payload["pipeline_queue_action"] = "skipped_active_full_refresh"
        job.payload["pipeline_queue_job_id"] = active_full_refresh.job_id
        job.payload["pipeline_queue_status"] = active_full_refresh.status
        job.payload["active_pipeline_job_count"] = 1
        job.payload["repair_outcome"] = "full_refresh_already_active"
        job.payload["verification_required"] = True
        return

    missing_documents = [
        document
        for document in job.payload.get("missing_documents") or []
        if isinstance(document, Mapping)
    ]
    grouped: dict[str, list[str]] = {}
    skipped_active: list[dict[str, Any]] = []
    for document in missing_documents:
        document_type = str(document.get("document_type") or "")
        period = str(document.get("period") or "")
        if not document_type or not period:
            continue
        active_update = queue.active_update_job_for_filing(
            job.ticker,
            document_type=document_type,
            period=period,
        )
        if active_update is not None:
            skipped_active.append(
                {
                    "document_type": document_type,
                    "period": period,
                    "job_id": active_update.job_id,
                    "status": active_update.status,
                }
            )
            continue
        grouped.setdefault(document_type, []).append(period)

    queued_jobs = []
    for document_type, periods in sorted(grouped.items()):
        unique_periods = sorted(set(periods), key=_period_order_key)
        if not unique_periods:
            continue
        queued_jobs.append(
            queue.add_update_job(
                job.ticker,
                document_type=document_type,
                periods=unique_periods,
                latest=False,
                force=False,
                publish_root=None,
            )
        )

    if queued_jobs:
        job.payload["pipeline_queue_action"] = "queued_targeted_filing_update"
        job.payload["pipeline_queue_job_ids"] = [queued.job_id for queued in queued_jobs]
        job.payload["pipeline_queue_job_count"] = len(queued_jobs)
        job.payload["pipeline_queue_statuses"] = {
            queued.job_id: queued.status
            for queued in queued_jobs
        }
        job.payload["targeted_update_count"] = sum(len(set(periods)) for periods in grouped.values())
        job.payload["skipped_active_updates"] = skipped_active
        job.payload["active_pipeline_job_count"] = len(skipped_active)
        job.payload["repair_outcome"] = "enqueued_targeted_filing_update"
        job.payload["verification_required"] = True
        return

    job.payload["pipeline_queue_action"] = "skipped_active_filing_update"
    job.payload["pipeline_queue_job_ids"] = [item["job_id"] for item in skipped_active]
    job.payload["active_pipeline_job_count"] = len(skipped_active)
    job.payload["skipped_active_updates"] = skipped_active
    job.payload["repair_outcome"] = "targeted_filing_update_already_active"
    job.payload["verification_required"] = True


def _period_order_key(period: str) -> tuple[int, int, str]:
    normalized = str(period or "").upper()
    year_text = normalized[2:6] if normalized.startswith(("CY", "FY")) else normalized[:4]
    year = int(year_text) if year_text.isdigit() else 0
    quarter = 0
    if "Q" in normalized:
        quarter_text = normalized.rsplit("Q", 1)[-1]
        quarter = int(quarter_text) if quarter_text.isdigit() else 0
    return (year, quarter, normalized)


async def _retry_batch_failure(
    job: RepairJob,
    *,
    root: Path,
    config: PipelineConfig,
    concurrency: int | None = None,
) -> None:
    if not job.period or not job.document_type:
        raise ValueError("batch_failure queue dispatch requires document_type and period")

    _enqueue_document_clean_rerun(job, root=root)
    return

    model = _quality_model_for_stage(config, job.stage)
    job.payload["repair_model"] = model
    worker = ExtractionWorker(
        model=model,
        cwd=root,
        max_retries=config.max_retries,
        call_timeout_s=config.call_timeout_seconds,
        max_turns=config.max_turns,
    )
    _attach_quality_repair_call_context(worker, job)
    resolved_concurrency = concurrency or DEFAULT_BATCH_FAILURE_REPAIR_CONCURRENCY
    job.payload["repair_concurrency"] = resolved_concurrency
    job.payload["repair_mode"] = "batch_only" if job.stage == "extract_assumption_candidates" else "manual_review_no_batch_executor"

    if job.stage == "extract_evidence_quotes":
        _mark_manual_review(
            job,
            reason="batch_only_executor_not_implemented",
            detail="extract_evidence_quotes repair no longer performs implicit full-stage retry",
        )
        return

    if job.stage == "extract_research_claims":
        _mark_manual_review(
            job,
            reason="batch_only_executor_not_implemented",
            detail="extract_research_claims repair no longer performs implicit full-stage retry",
        )
        return

    if job.stage == "extract_assumption_candidates":
        try:
            await _retry_assumption_candidate_batch_only(job, ontology_dir=ontology_dir, worker=worker)
        except Exception as exc:
            _mark_manual_review(
                job,
                reason="batch_only_retry_failed",
                detail=str(exc),
            )
        return

    raise ValueError(f"unsupported batch retry stage: {job.stage}")


def _quality_model_for_stage(config: PipelineConfig, stage_name: str) -> str:
    suffix = stage_name.upper()
    return (
        os.environ.get(f"KRW_QUALITY_STAGE_MODEL_{suffix}")
        or os.environ.get("KRW_QUALITY_MODEL")
        or DEFAULT_BATCH_FAILURE_REPAIR_MODEL
        or config.model_for_stage(stage_name)
    )


async def _retry_assumption_candidate_batch_only(
    job: RepairJob,
    *,
    ontology_dir: Path,
    worker: ExtractionWorker,
) -> None:
    input_claim_ids = [str(value) for value in (job.payload.get("input_span_ids") or []) if value]
    if not input_claim_ids:
        _mark_manual_review(job, reason="missing_batch_input_ids")
        return

    claims = read_jsonl(ontology_dir / "claims.jsonl")
    quotes = read_jsonl(ontology_dir / "evidence_quotes.jsonl")
    claim_by_id = {str(claim.get("id")): claim for claim in claims if claim.get("id")}
    batch_claims = [claim_by_id[claim_id] for claim_id in input_claim_ids if claim_id in claim_by_id]
    if not batch_claims:
        _mark_manual_review(job, reason="batch_input_claims_not_found")
        return

    cue_claims = assumption_stage._filter_modeling_cue_claims(batch_claims)
    if not cue_claims:
        _remove_matching_batch_failure(ontology_dir, job)
        job.payload["repair_outcome"] = "batch_only_no_modeling_cues_needs_verify"
        job.payload["verification_required"] = True
        job.payload["batch_input_count"] = len(input_claim_ids)
        job.payload["repaired_batch_item_count"] = 0
        return

    quote_lookup = {quote.get("id"): quote for quote in quotes if quote.get("id")}
    batch_quote_ids = assumption_stage._dedupe_list(
        [
            quote_id
            for claim in cue_claims
            for quote_id in claim.get("supported_by_quotes", []) or []
            if quote_id in quote_lookup
        ]
    )
    batch_quotes = [quote_lookup[quote_id] for quote_id in batch_quote_ids]
    aliased_claims, claim_alias_to_id = assumption_stage.alias_objects(cue_claims, "c")
    aliased_quotes, quote_alias_to_id = assumption_stage.alias_objects(batch_quotes, "q")
    quote_id_to_alias = assumption_stage.canonical_to_alias(quote_alias_to_id)
    input_data = {
        "claims_json": json.dumps(
            [
                {
                    "id": claim["id"],
                    "claim_text": claim["claim_text"],
                    "claim_type": claim.get("claim_type", ""),
                    "related_metrics": claim.get("related_metrics", []),
                    "supported_by_quotes": [
                        quote_id_to_alias[quote_id]
                        for quote_id in claim.get("supported_by_quotes", []) or []
                        if quote_id in quote_id_to_alias
                    ],
                }
                for claim in aliased_claims
            ],
            ensure_ascii=False,
        ),
        "quotes_json": json.dumps(
            [
                {
                    "id": quote["id"],
                    "quote_text": quote["quote_text"],
                    "quote_type": quote.get("quote_type", ""),
                }
                for quote in aliased_quotes
            ],
            ensure_ascii=False,
        ),
        "assumption_types": ", ".join(assumption_stage.ASSUMPTION_TYPES),
        "metrics_list": assumption_stage._load_metrics_list(ontology_dir),
    }
    raw_items = await worker.extract(
        assumption_stage.ASSUMPTION_EXTRACTION_PROMPT,
        input_data,
        assumption_stage._SCHEMA,
        "extract_assumption_candidates",
        call_metadata={
            "batch_index": job.batch_index,
            "claim_count": len(cue_claims),
            "quote_count": len(batch_quotes),
            "split_retry": False,
            "split_depth": 0,
            "repair_mode": "batch_only",
        },
    )
    raw_items = assumption_stage._resolve_assumption_references(
        raw_items,
        claim_alias_to_id=claim_alias_to_id,
        quote_alias_to_id=quote_alias_to_id,
    )
    doc_type_key = job.doc_type_key or str(job.document_type or "").replace("-", "")
    source_document_id = job.payload.get("source_document_id") or f"source:{job.ticker}:{job.period}:{doc_type_key}"
    repaired_items, rejected_items = assumption_stage._materialize_assumption_items(
        raw_items=raw_items,
        ticker=job.ticker,
        period=str(job.period),
        doc_type=str(job.document_type),
        doc_type_key=doc_type_key,
        source_document_id=str(source_document_id),
    )
    _merge_assumption_batch_output(ontology_dir, input_claim_ids=input_claim_ids, repaired_items=repaired_items)
    if rejected_items:
        assumption_stage._append_rejected_objects(ontology_dir, rejected_items)
    _remove_matching_batch_failure(ontology_dir, job)
    job.payload["repair_outcome"] = "batch_only_retried_needs_verify"
    job.payload["verification_required"] = True
    job.payload["batch_input_count"] = len(input_claim_ids)
    job.payload["repaired_batch_item_count"] = len(repaired_items)
    job.payload["rejected_batch_item_count"] = len(rejected_items)


async def _document_clean_rerun(
    job: RepairJob,
    *,
    root: Path,
    ontology_dir: Path,
    config: PipelineConfig,
    concurrency: int | None = None,
) -> None:
    if not job.stage or job.stage not in DOCUMENT_CLEAN_RERUN_STAGES:
        raise ValueError(f"document clean rerun does not support stage: {job.stage}")
    if not job.document_type or not job.period:
        raise ValueError("document clean rerun requires document_type and period")

    ticker = job.ticker.upper()
    document_type = job.document_type
    doc_type_key = job.doc_type_key or normalize_doc_type(document_type)
    period = job.period
    source_document_id = str(job.payload.get("source_document_id") or f"source:{ticker}:{period}:{doc_type_key}")
    tmp_dir = ontology_dir.parent / f".repair_tmp_{job.job_id}"
    backup_dir = ontology_dir.parent / f".repair_backup_{job.job_id}"
    for path in (tmp_dir, backup_dir):
        if path.exists():
            shutil.rmtree(path)
    shutil.copytree(ontology_dir, tmp_dir)

    replaced = False
    try:
        await _run_document_clean_rerun_stages(
            job,
            root=root,
            ontology_dir=tmp_dir,
            config=config,
            ticker=ticker,
            document_type=document_type,
            doc_type_key=doc_type_key,
            period=period,
            source_document_id=source_document_id,
            concurrency=concurrency,
        )
        _validate_repair_document_structure(tmp_dir)
        ontology_dir.rename(backup_dir)
        try:
            tmp_dir.rename(ontology_dir)
            replaced = True
        except Exception:
            if not ontology_dir.exists() and backup_dir.exists():
                backup_dir.rename(ontology_dir)
            raise
        _rebuild_replaced_document_indexes(
            job,
            root=root,
            ontology_dir=ontology_dir,
            ticker=ticker,
            document_type=document_type,
            doc_type_key=doc_type_key,
            period=period,
        )
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        if not replaced and backup_dir.exists() and not ontology_dir.exists():
            backup_dir.rename(ontology_dir)
        raise
    finally:
        if replaced and backup_dir.exists():
            shutil.rmtree(backup_dir)


async def _run_document_clean_rerun_stages(
    job: RepairJob,
    *,
    root: Path,
    ontology_dir: Path,
    config: PipelineConfig,
    ticker: str,
    document_type: str,
    doc_type_key: str,
    period: str,
    source_document_id: str,
    concurrency: int | None,
) -> None:
    start_stage = job.stage or "extract_evidence_quotes"
    start_index = DOCUMENT_CLEAN_RERUN_ORDER.index(start_stage)
    resolved_concurrency = concurrency or DEFAULT_BATCH_FAILURE_REPAIR_CONCURRENCY
    metadata = _load_source_metadata(root, ticker=ticker, doc_type_key=doc_type_key, period=period)
    claims: list[dict[str, Any]] | None = None
    quotes: list[dict[str, Any]] | None = None
    activities: list[dict[str, Any]] | None = None
    exposures: list[dict[str, Any]] | None = None

    for stage in DOCUMENT_CLEAN_RERUN_ORDER[start_index:]:
        worker = _worker_for_repair_stage(stage, config=config, root=root)
        _attach_quality_repair_call_context(worker, job)
        if stage == "extract_evidence_quotes":
            try:
                quotes = await extract_evidence_quotes(
                    worker=worker,
                    ontology_dir=ontology_dir,
                    ticker=ticker,
                    period=period,
                    doc_type=document_type,
                    concurrency=resolved_concurrency,
                    force=True,
                    span_pruning=config.span_pruning,
                    pilot_max_quote_spans=config.pilot_max_quote_spans,
                    batch_size=_repair_batch_size(config.batch_size_for_stage(stage, default=10)),
                )
            except PipelineStageError as exc:
                quotes = _materialize_partial_stage_cache(
                    ontology_dir,
                    stage_name=stage,
                    output_filename="evidence_quotes.jsonl",
                )
                write_jsonl(ontology_dir / "language_signals.jsonl", [])
                _record_partial_repair(job, stage=stage, error=exc, item_count=len(quotes))
        elif stage == "extract_research_claims":
            try:
                claims = await extract_research_claims(
                    worker=worker,
                    ontology_dir=ontology_dir,
                    ticker=ticker,
                    period=period,
                    doc_type=document_type,
                    quotes=quotes,
                    concurrency=resolved_concurrency,
                    force=True,
                    batch_size=_repair_batch_size(config.batch_size_for_stage(stage, default=12)),
                )
            except PipelineStageError as exc:
                claims = _materialize_partial_stage_cache(
                    ontology_dir,
                    stage_name=stage,
                    output_filename="claims.jsonl",
                )
                _record_partial_repair(job, stage=stage, error=exc, item_count=len(claims))
        elif stage == "extract_business_activities":
            claims = claims if claims is not None else read_jsonl(ontology_dir / "claims.jsonl")
            activities = await extract_business_activities(
                worker=worker,
                ontology_dir=ontology_dir,
                ticker=ticker,
                period=period,
                doc_type=document_type,
                claims=claims,
            )
        elif stage == "extract_external_factor_exposures":
            claims = claims if claims is not None else read_jsonl(ontology_dir / "claims.jsonl")
            activities = activities if activities is not None else read_jsonl(ontology_dir / "business_activities.jsonl")
            exposures = await extract_external_factor_exposures(
                worker=worker,
                ontology_dir=ontology_dir,
                ticker=ticker,
                period=period,
                doc_type=document_type,
                claims=claims,
                business_activities=activities,
            )
        elif stage == "extract_assumption_candidates":
            claims = claims if claims is not None else read_jsonl(ontology_dir / "claims.jsonl")
            quotes = quotes if quotes is not None else read_jsonl(ontology_dir / "evidence_quotes.jsonl")
            await extract_assumption_candidates(
                worker=worker,
                ontology_dir=ontology_dir,
                ticker=ticker,
                period=period,
                doc_type=document_type,
                claims=claims,
                quotes=quotes,
                concurrency=resolved_concurrency,
                force=True,
            )
        elif stage == "generate_canonical_artifacts":
            generate_canonical_artifacts(
                ontology_dir=ontology_dir,
                ticker=ticker,
                period=period,
                document_type=document_type,
                source_document_id=source_document_id,
                clean_text_hash=str(metadata.get("clean_md_sha256") or ""),
                raw_text_hash=str(metadata.get("raw_html_sha256") or ""),
            )
        elif stage == "validate_ontology":
            run_validate_ontology(ontology_dir, include_edges=False)
        elif stage == "generate_support_links":
            generate_support_links(
                ontology_dir=ontology_dir,
                ticker=ticker,
                period=period,
                document_type=document_type,
                source_document_id=source_document_id,
            )
        elif stage == "generate_edges":
            await generate_edges(
                worker=worker,
                ontology_dir=ontology_dir,
                ticker=ticker,
                period=period,
                doc_type=document_type,
            )
        elif stage == "validate_edges":
            run_validate_ontology(ontology_dir, include_edges=True)
        elif stage == "build_governance_artifacts":
            build_governance_artifacts(
                ontology_dir=ontology_dir,
                ticker=ticker,
                period=period,
                document_type=document_type,
                source_document_id=source_document_id,
                config=config,
                raw_html_sha256=str(metadata.get("raw_html_sha256") or ""),
                clean_md_sha256=str(metadata.get("clean_md_sha256") or ""),
            )
        elif stage == "build_graph_report":
            build_reports(
                ticker=ticker,
                period=period,
                document_type=document_type,
                ontology_dir=ontology_dir,
            )

    job.payload["repair_outcome"] = "document_clean_rerun_replaced_needs_verify"
    job.payload["verification_required"] = True
    job.payload["replaced_document"] = {
        "ticker": ticker,
        "document_type": document_type,
        "doc_type_key": doc_type_key,
        "period": period,
        "start_stage": start_stage,
    }


def _worker_for_repair_stage(stage: str, *, config: PipelineConfig, root: Path) -> ExtractionWorker:
    return ExtractionWorker(
        model=_quality_model_for_stage(config, stage),
        cwd=root,
        max_retries=config.max_retries,
        call_timeout_s=config.call_timeout_seconds,
        max_turns=config.max_turns,
    )


def _attach_quality_repair_call_context(worker: ExtractionWorker, job: RepairJob) -> None:
    worker.call_log_context = {
        "job_id": job.job_id,
        "plan_id": job.plan_id,
        "repair_kind": job.kind,
        "ticker": job.ticker,
        "document_type": job.document_type,
        "doc_type_key": job.doc_type_key,
        "period": job.period,
        "repair_stage": job.stage,
        "repair_strategy": job.payload.get("repair_strategy"),
    }


def _materialize_partial_stage_cache(
    ontology_dir: Path,
    *,
    stage_name: str,
    output_filename: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(batch_cache_dir(ontology_dir, stage_name).glob("batch_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            if item_id and item_id in seen:
                continue
            if item_id:
                seen.add(item_id)
            rows.append(item)
    write_jsonl(ontology_dir / output_filename, rows)
    return rows


def _record_partial_repair(job: RepairJob, *, stage: str, error: Exception, item_count: int) -> None:
    partials = list(job.payload.get("partial_stage_replacements") or [])
    partials.append(
        {
            "stage": stage,
            "item_count": item_count,
            "error": str(error)[:1000],
        }
    )
    job.payload["partial_stage_replacements"] = partials
    job.payload["replace_partial_on_rate_limit"] = True


def _load_source_metadata(root: Path, *, ticker: str, doc_type_key: str, period: str) -> dict[str, Any]:
    path = root / "companies" / ticker / "sources" / doc_type_key / period / "metadata.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _validate_repair_document_structure(ontology_dir: Path) -> None:
    for required in ("spans.jsonl", "evidence_quotes.jsonl", "claims.jsonl", "batch_failures.jsonl"):
        path = ontology_dir / required
        if not path.exists():
            write_jsonl(path, [])
    for path in ontology_dir.glob("*.jsonl"):
        read_jsonl(path)


def _rebuild_replaced_document_indexes(
    job: RepairJob,
    *,
    root: Path,
    ontology_dir: Path,
    ticker: str,
    document_type: str,
    doc_type_key: str,
    period: str,
) -> None:
    sources_dir = root / "companies" / ticker / "sources" / doc_type_key / period
    try:
        build_indexes(
            ticker=ticker,
            period=period,
            doc_type_key=doc_type_key,
            document_type=document_type,
            ontology_dir=ontology_dir,
            sources_dir=sources_dir,
            output_dir=root,
        )
        build_company_context(root, ticker)
    except Exception as exc:
        job.payload["post_replace_rebuild_error"] = str(exc)[:1000]
        job.payload["verification_required"] = True


def _merge_assumption_batch_output(
    ontology_dir: Path,
    *,
    input_claim_ids: list[str],
    repaired_items: list[dict[str, Any]],
) -> None:
    input_claim_set = set(input_claim_ids)
    output_path = ontology_dir / "assumption_candidates.jsonl"
    existing = read_jsonl(output_path)
    retained = [
        item for item in existing
        if not (input_claim_set & set(item.get("supported_by_claims") or []))
    ]
    by_id = {str(item.get("id")): item for item in retained if item.get("id")}
    ordered = [item for item in retained if item.get("id")]
    for item in repaired_items:
        item_id = str(item.get("id") or "")
        if item_id and item_id in by_id:
            by_id[item_id].update(item)
            continue
        if item_id:
            by_id[item_id] = item
        ordered.append(item)
    write_jsonl(output_path, ordered)


def _remove_matching_batch_failure(ontology_dir: Path, job: RepairJob) -> None:
    failures_path = ontology_dir / "batch_failures.jsonl"
    if not failures_path.exists():
        return
    failures = [
        row for row in read_jsonl(failures_path)
        if not (
            row.get("stage") == job.stage
            and row.get("batch_index") == job.batch_index
            and str(row.get("period") or "") == str(job.period or "")
        )
    ]
    write_jsonl(failures_path, failures)


def _mark_manual_review(job: RepairJob, *, reason: str, detail: str | None = None) -> None:
    job.payload["repair_outcome"] = "needs_manual_review"
    job.payload["manual_review_required"] = True
    job.payload["manual_review_reason"] = reason
    if detail:
        job.payload["manual_review_detail"] = detail[:1000]
    job.payload["unresolved_count"] = max(1, int(job.payload.get("unresolved_count") or 0))
    job.payload["verification_required"] = False


def _resection_document(job: RepairJob, *, root: Path) -> None:
    if not job.ontology_dir:
        raise ValueError("resection job is missing ontology_dir")
    if not job.artifact_index_path:
        raise ValueError("resection job is missing artifact_index_path")
    if not job.document_type:
        raise ValueError("resection job is missing document_type")

    artifact_index_path = _resolve_running_root_path(
        root,
        job.artifact_index_path,
        field="artifact_index_path",
        must_exist=True,
    )
    artifact_index = json.loads(artifact_index_path.read_text(encoding="utf-8"))
    sources = artifact_index.get("sources") or {}
    clean_md_path = _resolve_artifact_path(root, sources.get("clean_md"))
    raw_html_path = _resolve_artifact_path(root, sources.get("raw_html"))
    if clean_md_path is None:
        raise ValueError("resection job cannot resolve clean_md source")
    ontology_dir = _resolve_running_root_path(root, job.ontology_dir, field="ontology_dir", must_exist=True)
    extract_sections(
        clean_md_path,
        raw_html_path=raw_html_path,
        output_dir=ontology_dir,
        document_type=job.document_type,
    )
    job.payload["repair_outcome"] = "resectioned_needs_verify"
    job.payload["verification_required"] = True


def _repair_batch_size(default_size: int) -> int:
    """Use smaller batches for repair retries to reduce repeated agent failures."""
    return max(1, min(int(default_size), max(1, int(default_size) // 2)))


def _mark_batch_retry_payload(job: RepairJob, *, retry_batch_size: int) -> None:
    job.payload["repair_outcome"] = "batch_retried_needs_verify"
    job.payload["retry_batch_size"] = retry_batch_size
    job.payload["verification_required"] = True


def _resolve_artifact_path(root: Path, raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    return _resolve_running_root_path(root, raw_path, field="artifact_source", must_exist=False)


def _normalize_job_paths_for_running_root(job: RepairJob, *, root: Path) -> None:
    normalized = False
    if job.ontology_dir:
        original = job.ontology_dir
        job.ontology_dir = str(
            _resolve_running_root_path(root, job.ontology_dir, field="ontology_dir", must_exist=True)
        )
        normalized = normalized or job.ontology_dir != original
    if job.artifact_index_path:
        original = job.artifact_index_path
        job.artifact_index_path = str(
            _resolve_running_root_path(
                root,
                job.artifact_index_path,
                field="artifact_index_path",
                must_exist=True,
            )
        )
        normalized = normalized or job.artifact_index_path != original
    if normalized:
        job.payload["repair_paths_normalized_to_running_root"] = True


def _resolve_running_root_path(
    root: Path,
    raw_path: str | None,
    *,
    field: str,
    must_exist: bool,
) -> Path:
    if not raw_path:
        raise ValueError(f"{field} is required")
    root_path = root.expanduser().resolve()
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root_path)
        except ValueError:
            resolved = _map_release_path_to_running_root(root_path, resolved, field=field)
    else:
        resolved = (root_path / candidate).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"{field} escapes running root: {raw_path}") from exc
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"{field} does not exist under running root: {resolved}")
    return resolved


def _map_release_path_to_running_root(root: Path, path: Path, *, field: str) -> Path:
    parts = path.parts
    try:
        company_index = parts.index("companies")
    except ValueError as exc:
        raise ValueError(f"{field} is outside running root and cannot be mapped: {path}") from exc
    return (root / Path(*parts[company_index:])).resolve()
