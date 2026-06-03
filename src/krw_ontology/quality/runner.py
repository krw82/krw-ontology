"""Quality repair job executors.

The CLI keeps execution behind an explicit confirmation because these jobs can
invoke Agent SDK calls. Planning and inspection remain read-only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from krw_ontology.config.settings import PipelineConfig
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.pipeline.queue import PipelineQueue
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


SUPPORTED_RUN_KINDS = EXECUTABLE_REPAIR_KINDS


def run_repair_jobs(
    *,
    store: QualityRepairStore,
    jobs: list[RepairJob],
    root: Path,
    concurrency: int | None = None,
) -> dict[str, int]:
    """Run repair jobs sequentially and update job status."""
    counts = {"succeeded": 0, "failed": 0, "skipped": 0}
    config = PipelineConfig.load()
    for job in jobs:
        if job.kind not in SUPPORTED_RUN_KINDS:
            counts["skipped"] += 1
            continue
        store.mark_running(job)
        try:
            _run_one(job, root=root, config=config, concurrency=concurrency)
        except Exception as exc:
            store.mark_failed(job, str(exc))
            counts["failed"] += 1
            continue
        store.mark_succeeded(job)
        counts["succeeded"] += 1
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
    active = queue.active_job_for_ticker(job.ticker)
    if active is not None:
        job.payload["pipeline_queue_action"] = "skipped_active_job"
        job.payload["pipeline_queue_job_id"] = active.job_id
        job.payload["pipeline_queue_status"] = active.status
        return
    pipeline_job = queue.add_job(
        job.ticker,
        years=int(job.payload.get("years") or 3),
        force=False,
        publish_root=None,
        publish_index_path=None,
    )
    job.payload["pipeline_queue_action"] = "queued_full_refresh"
    job.payload["pipeline_queue_job_id"] = pipeline_job.job_id


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

    ontology_dir = Path(job.ontology_dir).expanduser().resolve()
    worker = ExtractionWorker(
        model=config.model_for_stage(job.stage),
        cwd=root,
        max_retries=config.max_retries,
        call_timeout_s=config.call_timeout_seconds,
        max_turns=config.max_turns,
    )
    resolved_concurrency = concurrency or config.concurrency_for_stage(job.stage)

    if job.stage == "extract_evidence_quotes":
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
            batch_size=config.batch_size_for_stage(job.stage, 5),
        )
        return

    if job.stage == "extract_research_claims":
        await extract_research_claims(
            worker,
            ontology_dir,
            job.ticker,
            job.period,
            job.document_type,
            concurrency=resolved_concurrency,
            force=False,
            batch_size=config.batch_size_for_stage(job.stage, 8),
        )
        return

    if job.stage == "extract_assumption_candidates":
        await extract_assumption_candidates(
            worker,
            ontology_dir,
            job.ticker,
            job.period,
            job.document_type,
            concurrency=resolved_concurrency,
            force=False,
        )
        return

    raise ValueError(f"unsupported batch retry stage: {job.stage}")


def _resection_document(job: RepairJob, *, root: Path) -> None:
    if not job.ontology_dir:
        raise ValueError("resection job is missing ontology_dir")
    if not job.artifact_index_path:
        raise ValueError("resection job is missing artifact_index_path")
    if not job.document_type:
        raise ValueError("resection job is missing document_type")

    artifact_index_path = Path(job.artifact_index_path).expanduser().resolve()
    artifact_index = json.loads(artifact_index_path.read_text(encoding="utf-8"))
    sources = artifact_index.get("sources") or {}
    clean_md_path = _resolve_artifact_path(root, sources.get("clean_md"))
    raw_html_path = _resolve_artifact_path(root, sources.get("raw_html"))
    if clean_md_path is None:
        raise ValueError("resection job cannot resolve clean_md source")
    extract_sections(
        clean_md_path,
        raw_html_path=raw_html_path,
        output_dir=Path(job.ontology_dir).expanduser().resolve(),
        document_type=job.document_type,
    )


def _resolve_artifact_path(root: Path, raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    return path.expanduser().resolve() if path.is_absolute() else (root / path).resolve()
