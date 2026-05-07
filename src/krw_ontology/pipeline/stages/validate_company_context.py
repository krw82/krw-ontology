"""Validate company-level context artifacts against the full company graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from krw_ontology.schema.id_utils import generate_metric_id
from krw_ontology.utils.io import atomic_write_json, read_jsonl, write_jsonl
from krw_ontology.validators.metric_validator import _load_metric_names
from krw_ontology.validators.reference_validator import validate_references
from krw_ontology.validators.relation_validator import _load_relations, validate_edge
from krw_ontology.validators.schema_validator import validate_schema
from krw_ontology.validators.support_validator import validate_has_support

_CONTEXT_FILES: dict[str, str] = {
    "CompanyBusinessProfile": "company_business_profiles.jsonl",
    "TemporalLink": "temporal_links.jsonl",
    "TrendObservation": "trend_observations.jsonl",
    "ChangeEvent": "change_events.jsonl",
    "Edge": "edges.jsonl",
}


def run_validate_company_context(root: Path, ticker: str) -> dict[str, Any]:
    """Validate context artifacts using all accepted document artifacts as universe.

    Document-level validation runs inside each filing directory. Company context
    artifacts intentionally span multiple filing directories, so their reference
    and relation checks need a company-wide lookup table.
    """
    root = root.resolve()
    ticker = ticker.upper()
    context_dir = root / "companies" / ticker / "context"
    schema_root = Path(__file__).resolve().parents[4] / "ontology" / "schema"
    relations_whitelist = _load_relations(schema_root / "relations.yaml")

    document_objects = _load_document_objects(root, ticker)
    virtual_metrics = _load_metric_objects(schema_root / "metric_dictionary.yaml")
    context_objects = _load_context_objects(context_dir)
    total_input = len(context_objects)
    accepted = dict(context_objects)
    rejected: list[dict[str, Any]] = []

    failed_ids = set()
    for obj_id, obj in list(accepted.items()):
        is_valid, reason = validate_schema(obj)
        if not is_valid:
            rejected.append(_reject(obj, reason, "schema_validation"))
            failed_ids.add(obj_id)
    for obj_id in failed_ids:
        del accepted[obj_id]

    failed_ids = set()
    lookup = _lookup(document_objects, accepted, virtual_metrics)
    for obj_id, obj in list(accepted.items()):
        is_valid, reason = validate_references(obj, lookup)
        if not is_valid:
            rejected.append(_reject(obj, reason, "reference_validation"))
            failed_ids.add(obj_id)
    for obj_id in failed_ids:
        del accepted[obj_id]

    failed_ids = set()
    for obj_id, obj in list(accepted.items()):
        is_valid, reason = validate_has_support(obj)
        if not is_valid:
            rejected.append(_reject(obj, reason, "support_validation"))
            failed_ids.add(obj_id)
    for obj_id in failed_ids:
        del accepted[obj_id]

    failed_ids = set()
    lookup = _lookup(document_objects, accepted, virtual_metrics)
    for obj_id, obj in list(accepted.items()):
        is_valid, reason = validate_edge(obj, relations_whitelist, lookup)
        if not is_valid:
            rejected.append(_reject(obj, reason, "relation_validation"))
            failed_ids.add(obj_id)
    for obj_id in failed_ids:
        del accepted[obj_id]

    accepted_by_type: dict[str, list[dict[str, Any]]] = {}
    for obj in accepted.values():
        accepted_by_type.setdefault(obj.get("type", ""), []).append(obj)
    for obj_type, filename in _CONTEXT_FILES.items():
        write_jsonl(context_dir / filename, accepted_by_type.get(obj_type, []))
    write_jsonl(context_dir / "rejected_objects.jsonl", rejected)

    accepted_counts = {
        key: len(accepted_by_type.get(obj_type, []))
        for obj_type, key in (
            ("CompanyBusinessProfile", "company_business_profiles"),
            ("TemporalLink", "temporal_links"),
            ("TrendObservation", "trend_observations"),
            ("ChangeEvent", "change_events"),
            ("Edge", "edges"),
        )
    }
    stats = {
        "total_input": total_input,
        "total_accepted": sum(accepted_counts.values()),
        "total_rejected": len(rejected),
    }
    result = {"accepted": accepted_counts, "rejected": rejected, "stats": stats}
    atomic_write_json(context_dir / "context_validation.json", result)
    return result


def _load_document_objects(root: Path, ticker: str) -> dict[str, dict[str, Any]]:
    objects: dict[str, dict[str, Any]] = {}
    for artifact_path in sorted((root / "companies" / ticker / "ontology").glob("*/*/artifact_index.json")):
        artifact = json.loads(artifact_path.read_text())
        if artifact.get("document_type") == "COMPANY":
            continue
        for key, rel_path in (artifact.get("files") or {}).items():
            if key in {"rejected_objects", "context_validation"} or not str(rel_path).endswith(".jsonl"):
                continue
            for obj in _read_artifact(root, rel_path):
                obj_id = obj.get("id")
                if obj_id:
                    objects[obj_id] = obj
    return objects


def _load_context_objects(context_dir: Path) -> dict[str, dict[str, Any]]:
    objects: dict[str, dict[str, Any]] = {}
    for filename in _CONTEXT_FILES.values():
        for obj in read_jsonl(context_dir / filename):
            obj_id = obj.get("id")
            if obj_id:
                objects[obj_id] = obj
    return objects


def _load_metric_objects(metric_path: Path) -> dict[str, dict[str, Any]]:
    metrics = {}
    for name in _load_metric_names(metric_path):
        metrics[generate_metric_id(name)] = {
            "id": generate_metric_id(name),
            "type": "Metric",
            "name": name,
            "_virtual": True,
        }
    return metrics


def _read_artifact(root: Path, rel_path: str | None) -> list[dict[str, Any]]:
    if not rel_path:
        return []
    path = Path(rel_path)
    if not path.is_absolute():
        path = root / path
    return read_jsonl(path)


def _lookup(
    document_objects: dict[str, dict[str, Any]],
    accepted_context: dict[str, dict[str, Any]],
    virtual_metrics: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {**document_objects, **accepted_context, **virtual_metrics}


def _reject(obj: dict[str, Any], reason: str | None, stage: str) -> dict[str, Any]:
    return {
        **obj,
        "rejection_reason": reason,
        "rejection_stage": stage,
    }
