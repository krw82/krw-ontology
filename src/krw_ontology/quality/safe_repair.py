"""Evidence-preserving quality repair executors.

These executors intentionally do not rewrite AI-authored claims, numeric values,
or support references. They only recalculate deterministic artifacts and write a
sidecar report describing what remains unresolved.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any, Callable

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.pipeline.stages.build_numeric_evidence import build_numeric_evidence
from krw_ontology.pipeline.stages.generate_edges import generate_edges
from krw_ontology.pipeline.stages.generate_support_links import generate_support_links
from krw_ontology.pipeline.stages.validate_ontology import _load_metric_objects
from krw_ontology.quality.models import RepairJob, utc_now
from krw_ontology.utils.io import atomic_write_json, read_jsonl, write_jsonl
from krw_ontology.validators.numeric_guard import validate_numeric
from krw_ontology.validators.reference_validator import validate_references
from krw_ontology.validators.relation_validator import _load_relations, validate_edge


POLICY = "evidence_preserving_revalidation"
AUTO_PROMOTED_OBJECTS = 0
AUTO_MODIFIED_CLAIMS = 0

NUMERIC_STAGES = {"numeric_guard"}
REFERENCE_STAGES = {
    "reference_validation",
    "reference_alias_resolution",
    "relation_validation",
}

REJECTION_METADATA_KEYS = {
    "rejection_reason",
    "rejection_stage",
    "rejected_at",
    "stage",
    "reason",
    "_rejection_reason",
}

OBJECT_FILES = (
    "taxonomy_terms.jsonl",
    "source_documents.jsonl",
    "source_locations.jsonl",
    "source_tables.jsonl",
    "source_table_cells.jsonl",
    "spans.jsonl",
    "evidence_quotes.jsonl",
    "language_signals.jsonl",
    "support_links.jsonl",
    "canonical_entities.jsonl",
    "entity_mentions.jsonl",
    "claims.jsonl",
    "metric_observations.jsonl",
    "calculations.jsonl",
    "business_factors.jsonl",
    "agreement_terms.jsonl",
    "business_events.jsonl",
    "business_activities.jsonl",
    "external_factor_exposures.jsonl",
    "assumption_candidates.jsonl",
    "edges.jsonl",
    "xbrl_facts.jsonl",
    "financial_metric_values.jsonl",
    "derived_metric_values.jsonl",
    "calculated_numeric_support.jsonl",
    "numeric_evidence.jsonl",
    "company_business_profiles.jsonl",
    "temporal_links.jsonl",
    "trend_observations.jsonl",
    "change_events.jsonl",
    "run_manifests.jsonl",
    "ontology_registry_snapshots.jsonl",
    "validation_reports.jsonl",
)

PRUNABLE_REFERENCE_FILES = (
    "support_links.jsonl",
    "edges.jsonl",
    "entity_mentions.jsonl",
)


def run_numeric_revalidation(job: RepairJob, *, root: Path) -> dict[str, Any]:
    """Rebuild numeric evidence and report numeric rejected candidates.

    This does not promote rejected objects or mutate AI-authored object fields.
    """
    ontology_dir = _resolve_ontology_dir(job)
    identity = _document_identity(ontology_dir, job)
    mutable_paths = [ontology_dir / "numeric_evidence.jsonl"]

    report: dict[str, Any] = {
        "policy": POLICY,
        "job": _job_report_payload(job),
        "ontology_dir": str(ontology_dir),
        "started_at": utc_now(),
        "operation": "normalize_numeric",
        "auto_promoted_objects": AUTO_PROMOTED_OBJECTS,
        "auto_modified_claims": AUTO_MODIFIED_CLAIMS,
        "mutated_artifacts": ["numeric_evidence.jsonl"],
    }

    before = _snapshot_files(mutable_paths)

    def rebuild() -> None:
        result = build_numeric_evidence(
            ontology_dir=ontology_dir,
            ticker=identity["ticker"],
            period=identity["period"],
            doc_type_key=identity["doc_type_key"],
            document_type=identity["document_type"],
            source_document_id=identity["source_document_id"],
        )
        report["numeric_evidence_rows"] = len(result.get("numeric_evidence") or [])

    _with_rollback(mutable_paths, rebuild)

    lookup = _load_lookup(ontology_dir)
    numeric_support = {
        obj_id: obj
        for obj_id, obj in lookup.items()
        if obj.get("type")
        in {
            "XBRLFact",
            "MetricObservation",
            "FinancialMetricValue",
            "DerivedMetricValue",
            "CalculatedNumericSupport",
            "NumericEvidence",
        }
    }
    candidates = _rejected_candidates(ontology_dir, NUMERIC_STAGES)
    valid_candidates: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for row in candidates:
        candidate = _strip_rejection_metadata(row)
        ok, reason = validate_numeric(candidate, lookup, numeric_support)
        payload = _candidate_report(row, reason)
        if ok:
            valid_candidates.append(payload)
        else:
            unresolved.append(payload)

    report.update({
        "candidate_count": len(candidates),
        "candidate_now_valid_count": len(valid_candidates),
        "candidate_now_valid": valid_candidates,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "artifact_hashes": _artifact_hashes(before, mutable_paths),
        "finished_at": utc_now(),
    })
    report_path = write_repair_report(job, root=root, report=report)
    _attach_report_payload(job, report, report_path)
    return report


def run_reference_rebuild(job: RepairJob, *, root: Path) -> dict[str, Any]:
    """Rebuild deterministic reference tail and report unresolved references."""
    ontology_dir = _resolve_ontology_dir(job)
    identity = _document_identity(ontology_dir, job)
    mutable_paths = [
        ontology_dir / "support_links.jsonl",
        ontology_dir / "edges.jsonl",
        ontology_dir / "entity_mentions.jsonl",
    ]

    report: dict[str, Any] = {
        "policy": POLICY,
        "job": _job_report_payload(job),
        "ontology_dir": str(ontology_dir),
        "started_at": utc_now(),
        "operation": "repair_reference",
        "auto_promoted_objects": AUTO_PROMOTED_OBJECTS,
        "auto_modified_claims": AUTO_MODIFIED_CLAIMS,
        "mutated_artifacts": ["support_links.jsonl", "edges.jsonl", "entity_mentions.jsonl"],
    }
    before = _snapshot_files(mutable_paths)

    def rebuild() -> None:
        support_links = generate_support_links(
            ontology_dir=ontology_dir,
            ticker=identity["ticker"],
            period=identity["period"],
            document_type=identity["document_type"],
            source_document_id=identity["source_document_id"],
        )
        edges = asyncio.run(
            generate_edges(
                None,  # type: ignore[arg-type]
                ontology_dir,
                identity["ticker"],
                identity["period"],
                identity["document_type"],
            )
        )
        report["support_links_rows"] = len(support_links)
        report["edges_rows"] = len(edges)
        report.update(_prune_invalid_reference_artifacts(ontology_dir))

    _with_rollback(mutable_paths, rebuild)

    lookup = _load_lookup(ontology_dir)
    relations = _relations_whitelist()
    accepted_reference_failures: list[dict[str, Any]] = []
    accepted_relation_failures: list[dict[str, Any]] = []
    for obj in lookup.values():
        if obj.get("_virtual"):
            continue
        ok, reason = validate_references(obj, lookup)
        if not ok:
            accepted_reference_failures.append(_object_failure_report(obj, reason))
        if obj.get("type") == "Edge":
            ok, reason = validate_edge(obj, relations, lookup)
            if not ok:
                accepted_relation_failures.append(_object_failure_report(obj, reason))

    candidates = _rejected_candidates(ontology_dir, REFERENCE_STAGES)
    valid_candidates: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for row in candidates:
        candidate = _strip_rejection_metadata(row)
        ok, reason = validate_references(candidate, lookup)
        if ok and candidate.get("type") == "Edge":
            ok, reason = validate_edge(candidate, relations, lookup)
        payload = _candidate_report(row, reason)
        if ok:
            valid_candidates.append(payload)
        else:
            unresolved.append(payload)

    unresolved_count = (
        len(unresolved)
        + len(accepted_reference_failures)
        + len(accepted_relation_failures)
    )
    report.update({
        "accepted_reference_failure_count": len(accepted_reference_failures),
        "accepted_reference_failures": accepted_reference_failures,
        "accepted_relation_failure_count": len(accepted_relation_failures),
        "accepted_relation_failures": accepted_relation_failures,
        "candidate_count": len(candidates),
        "candidate_now_valid_count": len(valid_candidates),
        "candidate_now_valid": valid_candidates,
        "unresolved_count": unresolved_count,
        "unresolved": unresolved,
        "artifact_hashes": _artifact_hashes(before, mutable_paths),
        "finished_at": utc_now(),
    })
    report_path = write_repair_report(job, root=root, report=report)
    _attach_report_payload(job, report, report_path)
    return report


def write_repair_report(job: RepairJob, *, root: Path, report: dict[str, Any]) -> Path:
    report_path = (
        Path(root).expanduser().resolve()
        / ".krw_pipeline"
        / "quality"
        / "reports"
        / f"{job.job_id}.json"
    )
    atomic_write_json(report_path, report)
    return report_path


def _attach_report_payload(job: RepairJob, report: dict[str, Any], report_path: Path) -> None:
    resolved_count = int(report.get("candidate_now_valid_count") or 0)
    unresolved_count = int(report.get("unresolved_count") or 0)
    pruned_count = int(report.get("pruned_reference_object_count") or 0)
    if pruned_count and unresolved_count:
        outcome = "pruned_invalid_references_with_unresolved_report"
    elif pruned_count:
        outcome = "pruned_invalid_references_needs_verify"
    elif resolved_count and unresolved_count:
        outcome = "partially_resolved_reported"
    elif resolved_count:
        outcome = "resolved_reported"
    elif unresolved_count:
        outcome = "unresolved_reported"
    else:
        outcome = "revalidated_no_candidates"
    job.payload["policy"] = POLICY
    job.payload["report_path"] = str(report_path)
    job.payload["repair_outcome"] = outcome
    job.payload["resolved_count"] = resolved_count
    job.payload["pruned_reference_object_count"] = pruned_count
    job.payload["auto_promoted_objects"] = AUTO_PROMOTED_OBJECTS
    job.payload["auto_modified_claims"] = AUTO_MODIFIED_CLAIMS
    job.payload["mutated_artifacts"] = list(report.get("mutated_artifacts") or [])
    job.payload["unresolved_count"] = unresolved_count
    job.payload["candidate_now_valid_count"] = resolved_count
    job.payload["verification_required"] = True


def _resolve_ontology_dir(job: RepairJob) -> Path:
    if not job.ontology_dir:
        raise ValueError(f"{job.kind} job is missing ontology_dir")
    ontology_dir = Path(job.ontology_dir).expanduser().resolve()
    if not ontology_dir.exists():
        raise FileNotFoundError(f"ontology_dir does not exist: {ontology_dir}")
    return ontology_dir


def _document_identity(ontology_dir: Path, job: RepairJob) -> dict[str, str]:
    ticker = job.ticker.upper()
    document_type = job.document_type or ""
    doc_type_key = job.doc_type_key or ""
    period = job.period or ""
    source_document_id = ""
    for filename in (
        "source_documents.jsonl",
        "evidence_quotes.jsonl",
        "spans.jsonl",
        "claims.jsonl",
    ):
        for obj in read_jsonl(ontology_dir / filename):
            ticker = str(obj.get("ticker") or ticker).upper()
            document_type = str(obj.get("document_type") or document_type)
            period = str(obj.get("period") or period)
            source_document_id = str(obj.get("source_document_id") or obj.get("id") or source_document_id)
            break
        if document_type and period and source_document_id:
            break
    if not document_type:
        raise ValueError("repair job cannot derive document_type")
    if not period:
        raise ValueError("repair job cannot derive period")
    if not doc_type_key:
        doc_type_key = normalize_doc_type(document_type)
    if not source_document_id:
        source_document_id = f"source:{ticker}:{period}:{doc_type_key}"
    return {
        "ticker": ticker,
        "document_type": document_type,
        "doc_type_key": doc_type_key,
        "period": period,
        "source_document_id": source_document_id,
    }


def _load_lookup(ontology_dir: Path) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for filename in OBJECT_FILES:
        for obj in read_jsonl(ontology_dir / filename):
            obj_id = obj.get("id")
            if obj_id:
                lookup[str(obj_id)] = obj
    lookup.update(_load_metric_objects(ontology_dir))
    return lookup


def _prune_invalid_reference_artifacts(ontology_dir: Path) -> dict[str, Any]:
    """Remove invalid derived reference objects without editing authored claims.

    This is intentionally limited to deterministic/derived link artifacts. If a
    claim or metric has bad support, we report it instead of editing the object.
    """
    lookup = _load_lookup(ontology_dir)
    relations = _relations_whitelist()
    pruned_by_file: dict[str, int] = {}
    pruned_examples: list[dict[str, Any]] = []
    total_pruned = 0
    for filename in PRUNABLE_REFERENCE_FILES:
        path = ontology_dir / filename
        rows = read_jsonl(path)
        if not rows:
            pruned_by_file[filename] = 0
            continue
        kept: list[dict[str, Any]] = []
        pruned: list[dict[str, Any]] = []
        for obj in rows:
            ok, reason = validate_references(obj, lookup)
            if ok and obj.get("type") == "Edge":
                ok, reason = validate_edge(obj, relations, lookup)
            if ok:
                kept.append(obj)
            else:
                pruned.append(_object_failure_report(obj, reason))
        if pruned:
            write_jsonl(path, kept)
            pruned_ids = {str(item.get("id")) for item in pruned if item.get("id")}
            for obj in rows:
                obj_id = obj.get("id")
                if obj_id and str(obj_id) in pruned_ids:
                    lookup.pop(str(obj_id), None)
        pruned_by_file[filename] = len(pruned)
        total_pruned += len(pruned)
        pruned_examples.extend(pruned[:10])
    return {
        "pruned_reference_object_count": total_pruned,
        "pruned_reference_objects_by_file": pruned_by_file,
        "pruned_reference_object_examples": pruned_examples[:50],
    }


def _rejected_candidates(ontology_dir: Path, stages: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for obj in read_jsonl(ontology_dir / "rejected_objects.jsonl"):
        stage = str(obj.get("rejection_stage") or obj.get("stage") or "")
        if stage in stages:
            rows.append(obj)
    return rows


def _strip_rejection_metadata(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in obj.items()
        if key not in REJECTION_METADATA_KEYS and not key.startswith("_")
    }


def _candidate_report(obj: dict[str, Any], reason: str | None) -> dict[str, Any]:
    return {
        "id": obj.get("id"),
        "type": obj.get("type"),
        "ticker": obj.get("ticker"),
        "document_type": obj.get("document_type"),
        "period": obj.get("period"),
        "rejection_stage": obj.get("rejection_stage") or obj.get("stage"),
        "original_reason": obj.get("rejection_reason") or obj.get("reason"),
        "current_reason": reason,
    }


def _object_failure_report(obj: dict[str, Any], reason: str | None) -> dict[str, Any]:
    return {
        "id": obj.get("id"),
        "type": obj.get("type"),
        "reason": reason,
    }


def _job_report_payload(job: RepairJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "plan_id": job.plan_id,
        "kind": job.kind,
        "ticker": job.ticker,
        "document_type": job.document_type,
        "doc_type_key": job.doc_type_key,
        "period": job.period,
        "stage": job.stage,
        "source_event_id": job.source_event_id,
        "count": job.count,
    }


def _relations_whitelist() -> set[tuple[str, str, str]]:
    schema_root = Path(__file__).resolve().parents[3] / "ontology" / "schema"
    return _load_relations(schema_root / "relations.yaml")


def _snapshot_files(paths: list[Path]) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for path in paths:
        data = path.read_bytes() if path.exists() else None
        snapshot[str(path)] = {
            "exists": data is not None,
            "sha256": _sha256(data) if data is not None else None,
        }
    return snapshot


def _artifact_hashes(
    before: dict[str, dict[str, Any]],
    paths: list[Path],
) -> dict[str, dict[str, Any]]:
    hashes: dict[str, dict[str, Any]] = {}
    for path in paths:
        data = path.read_bytes() if path.exists() else None
        prior = before.get(str(path), {})
        after = _sha256(data) if data is not None else None
        hashes[path.name] = {
            "before_exists": bool(prior.get("exists")),
            "after_exists": data is not None,
            "before_sha256": prior.get("sha256"),
            "after_sha256": after,
            "changed": prior.get("sha256") != after,
        }
    return hashes


def _with_rollback(paths: list[Path], action: Callable[[], None]) -> None:
    backups = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        action()
    except Exception:
        for path, data in backups.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        raise


def _sha256(data: bytes | None) -> str | None:
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()
