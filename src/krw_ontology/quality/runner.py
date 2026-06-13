"""Quality repair job executors.

The CLI keeps execution behind an explicit confirmation because these jobs can
invoke Agent SDK calls. Planning and inspection remain read-only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

from krw_ontology.config.settings import PipelineConfig
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.pipeline.queue import PipelineQueue
import krw_ontology.pipeline.stages.extract_assumption_candidates as assumption_stage
from krw_ontology.pipeline.stages.extract_assumption_candidates import (
    extract_assumption_candidates,
)
from krw_ontology.pipeline.stages.extract_evidence_quotes import extract_evidence_quotes
from krw_ontology.pipeline.stages.extract_research_claims import extract_research_claims
from krw_ontology.pipeline.stages.extract_sections import extract_sections
from krw_ontology.quality.models import (
    BATCH_FAILURE,
    DOCS_MISSING,
    EXECUTABLE_REPAIR_KINDS,
    NORMALIZE_NUMERIC,
    REPAIR_REFERENCE,
    SECTION_FAIL,
    SECTION_WARN,
    RepairJob,
)
from krw_ontology.quality.queue import QualityRepairStore
from krw_ontology.quality.safe_repair import (
    run_numeric_revalidation,
    run_reference_rebuild,
)


# User-facing queue selection excludes deferred kinds such as normalize_numeric.
# Keep legacy direct runner support so existing report-only revalidation remains
# available to tests or explicit internal callers.
SUPPORTED_RUN_KINDS = EXECUTABLE_REPAIR_KINDS | {NORMALIZE_NUMERIC}


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
        counts["succeeded"] += 1
        counts["resolved"] += int(job.payload.get("resolved_count") or job.payload.get("candidate_now_valid_count") or 0)
        counts["unresolved"] += int(job.payload.get("unresolved_count") or 0)
        if str(job.payload.get("pipeline_queue_action") or "").startswith("queued_"):
            counts["enqueued"] += int(job.payload.get("pipeline_queue_job_count") or 1)
        if str(job.payload.get("pipeline_queue_action") or "").startswith("skipped_active"):
            counts["active"] += int(job.payload.get("active_pipeline_job_count") or 1)
    return counts


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
    if job.kind in {SECTION_FAIL, SECTION_WARN}:
        _resection_document(job, root=root)
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
    if not job.ontology_dir:
        raise ValueError("retry_batch job is missing ontology_dir")
    if not job.stage:
        raise ValueError("retry_batch job is missing stage")
    if not job.period or not job.document_type:
        raise ValueError("retry_batch job is missing document identity")

    ontology_dir = _resolve_running_root_path(root, job.ontology_dir, field="ontology_dir", must_exist=True)
    worker = ExtractionWorker(
        model=config.model_for_stage(job.stage),
        cwd=root,
        max_retries=config.max_retries,
        call_timeout_s=config.call_timeout_seconds,
        max_turns=config.max_turns,
    )
    resolved_concurrency = concurrency or config.concurrency_for_stage(job.stage)

    if job.stage == "extract_evidence_quotes":
        retry_batch_size = _repair_batch_size(config.batch_size_for_stage(job.stage, 5))
        await extract_evidence_quotes(
            worker,
            ontology_dir,
            job.ticker,
            job.period,
            job.document_type,
            concurrency=resolved_concurrency,
            force=False,
            span_pruning=config.span_pruning,
            pilot_max_quote_spans=config.pilot_max_quote_spans,
            batch_size=retry_batch_size,
        )
        _mark_batch_retry_payload(job, retry_batch_size=retry_batch_size)
        return

    if job.stage == "extract_research_claims":
        retry_batch_size = _repair_batch_size(config.batch_size_for_stage(job.stage, 8))
        await extract_research_claims(
            worker,
            ontology_dir,
            job.ticker,
            job.period,
            job.document_type,
            concurrency=resolved_concurrency,
            force=False,
            batch_size=retry_batch_size,
        )
        _mark_batch_retry_payload(job, retry_batch_size=retry_batch_size)
        return

    if job.stage == "extract_assumption_candidates":
        retry_batch_size = _repair_batch_size(assumption_stage.BATCH_SIZE)
        original_batch_size = assumption_stage.BATCH_SIZE
        try:
            assumption_stage.BATCH_SIZE = retry_batch_size
            await extract_assumption_candidates(
                worker,
                ontology_dir,
                job.ticker,
                job.period,
                job.document_type,
                concurrency=resolved_concurrency,
                force=False,
            )
        finally:
            assumption_stage.BATCH_SIZE = original_batch_size
        _mark_batch_retry_payload(job, retry_batch_size=retry_batch_size)
        return

    raise ValueError(f"unsupported batch retry stage: {job.stage}")


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
