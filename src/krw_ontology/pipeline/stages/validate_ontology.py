"""validate_ontology pipeline stage — orchestrates 7 validators in sequence."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from krw_ontology.schema.id_utils import generate_metric_id
from krw_ontology.utils.io import find_project_root, read_jsonl, write_jsonl
from krw_ontology.validators.schema_validator import validate_schema
from krw_ontology.validators.exact_match import validate_exact_match
from krw_ontology.validators.reference_validator import validate_references
from krw_ontology.validators.support_validator import validate_has_support
from krw_ontology.validators.metric_validator import (
    validate_metric_fields,
    _load_metric_names,
)
from krw_ontology.validators.numeric_guard import validate_numeric
from krw_ontology.validators.relation_validator import (
    validate_edge,
    _load_relations,
)

logger = logging.getLogger("krw_ontology")

# JSONL files to load (type -> filename)
_JSONL_FILES: dict[str, str] = {
    "SourceSpan": "spans.jsonl",
    "EvidenceQuote": "evidence_quotes.jsonl",
    "LanguageSignal": "language_signals.jsonl",
    "ResearchClaim": "claims.jsonl",
    "RiskFactor": "risks.jsonl",
    "GrowthDriver": "growth_drivers.jsonl",
    "Headwind": "headwinds.jsonl",
    "AssumptionCandidate": "assumption_candidates.jsonl",
    "Edge": "edges.jsonl",
    "XBRLFact": "xbrl_facts.jsonl",
    "FinancialMetricValue": "financial_metric_values.jsonl",
    "DerivedMetricValue": "derived_metric_values.jsonl",
    "NumericEvidence": "numeric_evidence.jsonl",
    "CalculatedNumericSupport": "calculated_numeric_support.jsonl",
}

# Filename mapping for writing accepted objects back
_ACCEPTED_FILES: dict[str, str] = {
    "SourceSpan": "spans.jsonl",
    "EvidenceQuote": "evidence_quotes.jsonl",
    "LanguageSignal": "language_signals.jsonl",
    "ResearchClaim": "claims.jsonl",
    "RiskFactor": "risks.jsonl",
    "GrowthDriver": "growth_drivers.jsonl",
    "Headwind": "headwinds.jsonl",
    "AssumptionCandidate": "assumption_candidates.jsonl",
    "Edge": "edges.jsonl",
    "XBRLFact": "xbrl_facts.jsonl",
    "FinancialMetricValue": "financial_metric_values.jsonl",
    "DerivedMetricValue": "derived_metric_values.jsonl",
    "NumericEvidence": "numeric_evidence.jsonl",
    "CalculatedNumericSupport": "calculated_numeric_support.jsonl",
}


def _load_all_objects(ontology_dir: Path, *, include_edges: bool = True) -> dict[str, dict]:
    """Load all objects from JSONL files into a single id->object dict."""
    all_objects: dict[str, dict] = {}
    for obj_type, filename in _JSONL_FILES.items():
        if obj_type == "Edge" and not include_edges:
            continue
        filepath = ontology_dir / filename
        if filepath.exists():
            for obj in read_jsonl(filepath):
                obj_id = obj.get("id")
                if obj_id:
                    all_objects[obj_id] = obj
    return all_objects


def _load_metric_objects(ontology_dir: Path) -> dict[str, dict]:
    """Load canonical metric objects for reference/relation validation."""
    try:
        project_root = find_project_root(ontology_dir)
        metric_path = project_root / "ontology" / "schema" / "metric_dictionary.yaml"
        if metric_path.exists():
            with open(metric_path) as f:
                data = yaml.safe_load(f) or {}
            metrics = {}
            for name, spec in (data.get("canonical_metrics") or {}).items():
                metric_id = generate_metric_id(name)
                metrics[metric_id] = {
                    "id": metric_id,
                    "type": "Metric",
                    "name": name,
                    "category": spec.get("category", ""),
                    "unit": spec.get("unit", ""),
                    "description": spec.get("description", ""),
                    "schema_version": data.get("schema_version", "0.1.0"),
                    "_virtual": True,
                }
            return metrics
    except Exception:
        pass
    return {}


def _get_spans(all_objects: dict[str, dict]) -> dict[str, dict]:
    return {
        oid: obj for oid, obj in all_objects.items() if obj.get("type") == "SourceSpan"
    }


def _get_quotes(all_objects: dict[str, dict]) -> dict[str, dict]:
    return {
        oid: obj
        for oid, obj in all_objects.items()
        if obj.get("type") == "EvidenceQuote"
    }


def _get_xbrl_facts(all_objects: dict[str, dict]) -> dict[str, dict]:
    return {
        oid: obj
        for oid, obj in all_objects.items()
        if obj.get("type") in (
            "XBRLFact",
            "FinancialMetricValue",
            "DerivedMetricValue",
            "NumericEvidence",
            "CalculatedNumericSupport",
        )
    }


def _prune_dangling_supports(accepted: dict[str, dict]) -> list[dict]:
    """Remove support references that no longer point at accepted objects."""
    pruned: list[dict] = []
    valid_ids = set(accepted)
    for obj in accepted.values():
        obj_type = obj.get("type")
        if obj_type == "ResearchClaim":
            quotes = [qid for qid in obj.get("supported_by_quotes") or [] if qid in valid_ids]
            if quotes != (obj.get("supported_by_quotes") or []):
                obj["supported_by_quotes"] = quotes
                pruned.append(obj)
        if obj_type in ("RiskFactor", "GrowthDriver", "Headwind", "AssumptionCandidate"):
            claims = [cid for cid in obj.get("supported_by_claims") or [] if cid in valid_ids]
            quotes = [qid for qid in obj.get("supported_by_quotes") or [] if qid in valid_ids]
            if claims != (obj.get("supported_by_claims") or []) or quotes != (obj.get("supported_by_quotes") or []):
                obj["supported_by_claims"] = claims
                obj["supported_by_quotes"] = quotes
                pruned.append(obj)
    return pruned


def run_validate_ontology(ontology_dir: Path, *, include_edges: bool = True) -> dict:
    """Run all 7 validators in sequence on ontology artifacts.

    1. Load all JSONL files
    2. Run validators: schema -> exact_match -> reference -> support -> metric -> numeric_guard -> relation
    3. Write accepted + rejected JSONL
    4. Return summary stats

    Set include_edges=False for the first pipeline validation pass. That pass
    removes invalid non-edge artifacts before deterministic edge generation, so
    stale or pre-validation edges do not amplify rejected claim/object counts.

    Returns dict with keys: accepted (type -> count), rejected (list), stats.
    """
    schema_root = Path(__file__).resolve().parents[4] / "ontology" / "schema"

    # Load config files
    canonical_metrics = _load_metric_names(schema_root / "metric_dictionary.yaml")
    relations_whitelist = _load_relations(schema_root / "relations.yaml")

    # Load all artifact objects
    all_objects = _load_all_objects(ontology_dir, include_edges=include_edges)
    total_input = len(all_objects)
    logger.info("Loaded %d objects from %s", len(all_objects), ontology_dir)

    # Register canonical metrics as virtual objects so edges to metric:* validate.
    # These are not written as accepted artifacts.
    all_objects.update(_load_metric_objects(ontology_dir))

    # Track results
    accepted: dict[str, dict] = dict(all_objects)  # copy
    rejected: list[dict] = []

    # Existing rejected objects (preserve)
    rejected_path = ontology_dir / "rejected_objects.jsonl"
    existing_rejected = read_jsonl(rejected_path)

    # --- Stage 1: Schema validation ---
    failed_ids: set[str] = set()
    for obj_id, obj in list(accepted.items()):
        is_valid, reason = validate_schema(obj)
        if not is_valid:
            rejected.append({
                **obj,
                "rejection_reason": reason,
                "rejection_stage": "schema_validation",
            })
            failed_ids.add(obj_id)
    for fid in failed_ids:
        del accepted[fid]
    logger.info("Schema validation: %d passed, %d rejected", len(accepted) - (len(all_objects) - len(failed_ids)), len(failed_ids))

    # --- Stage 2: Exact match (EvidenceQuote only) ---
    spans = _get_spans(accepted)
    failed_ids = set()
    for obj_id, obj in list(accepted.items()):
        is_valid, reason = validate_exact_match(obj, spans)
        if not is_valid:
            rejected.append({
                **obj,
                "rejection_reason": reason,
                "rejection_stage": "exact_match_validation",
            })
            failed_ids.add(obj_id)
    for fid in failed_ids:
        del accepted[fid]
    if failed_ids:
        logger.info("Exact match: %d rejected", len(failed_ids))

    # --- Stage 3: Reference validation ---
    failed_ids = set()
    for obj_id, obj in list(accepted.items()):
        is_valid, reason = validate_references(obj, accepted)
        if not is_valid:
            rejected.append({
                **obj,
                "rejection_reason": reason,
                "rejection_stage": "reference_validation",
            })
            failed_ids.add(obj_id)
    for fid in failed_ids:
        del accepted[fid]
    if failed_ids:
        logger.info("Reference validation: %d rejected", len(failed_ids))

    # --- Stage 4: Support validation ---
    failed_ids = set()
    for obj_id, obj in list(accepted.items()):
        is_valid, reason = validate_has_support(obj)
        if not is_valid:
            rejected.append({
                **obj,
                "rejection_reason": reason,
                "rejection_stage": "support_validation",
            })
            failed_ids.add(obj_id)
    for fid in failed_ids:
        del accepted[fid]
    if failed_ids:
        logger.info("Support validation: %d rejected", len(failed_ids))

    # --- Stage 5: Metric validation (modifies objects in place, never rejects) ---
    for obj_id, obj in accepted.items():
        validate_metric_fields(obj, canonical_metrics)

    # --- Stage 6: Numeric guard ---
    xbrl_facts = _get_xbrl_facts(accepted)
    all_lookup = dict(accepted)  # Full lookup including claims
    failed_ids = set()
    for obj_id, obj in list(accepted.items()):
        is_valid, reason = validate_numeric(obj, all_lookup, xbrl_facts)
        if not is_valid:
            rejected.append({
                **obj,
                "rejection_reason": reason,
                "rejection_stage": "numeric_guard",
            })
            failed_ids.add(obj_id)
    for fid in failed_ids:
        del accepted[fid]
    if failed_ids:
        logger.info("Numeric guard: %d rejected", len(failed_ids))

    # Numeric rejection can invalidate parent support paths. Prune those
    # references immediately so later edge generation does not amplify a single
    # rejected claim into many dangling relation rejects.
    pruned_rejections = 0
    while True:
        _prune_dangling_supports(accepted)
        failed_ids = set()
        for obj_id, obj in list(accepted.items()):
            if obj.get("type") == "ResearchClaim" and not obj.get("supported_by_quotes"):
                reason = "ResearchClaim has no accepted supporting quotes after numeric pruning"
                rejected.append({
                    **obj,
                    "rejection_reason": reason,
                    "rejection_stage": "post_numeric_support_pruning",
                })
                failed_ids.add(obj_id)
                continue
            is_valid, reason = validate_has_support(obj)
            if not is_valid:
                rejected.append({
                    **obj,
                    "rejection_reason": reason,
                    "rejection_stage": "post_numeric_support_pruning",
                })
                failed_ids.add(obj_id)
        for fid in failed_ids:
            del accepted[fid]
        pruned_rejections += len(failed_ids)
        if not failed_ids:
            break
    if pruned_rejections:
        logger.info("Post numeric support pruning: %d rejected", pruned_rejections)

    # --- Stage 7: Relation validation (Edge only) ---
    failed_ids = set()
    for obj_id, obj in list(accepted.items()):
        is_valid, reason = validate_edge(obj, relations_whitelist, accepted)
        if not is_valid:
            rejected.append({
                **obj,
                "rejection_reason": reason,
                "rejection_stage": "relation_validation",
            })
            failed_ids.add(obj_id)
    for fid in failed_ids:
        del accepted[fid]
    if failed_ids:
        logger.info("Relation validation: %d rejected", len(failed_ids))

    # --- Write results ---
    # Write accepted objects back to their type-specific JSONL files
    accepted_by_type: dict[str, list[dict]] = {}
    for obj in accepted.values():
        obj_type = obj.get("type", "")
        if obj_type == "Metric" and obj.get("_virtual"):
            continue
        # Normalize ResearchObject subtypes to their filenames
        if obj_type in ("RiskFactor", "GrowthDriver", "Headwind"):
            pass  # they have their own files
        accepted_by_type.setdefault(obj_type, []).append(
            {k: v for k, v in obj.items() if k != "_virtual"}
        )

    for obj_type, filename in _ACCEPTED_FILES.items():
        objs = accepted_by_type.get(obj_type, [])
        write_jsonl(ontology_dir / filename, objs)

    # Append new rejected objects to existing rejected file
    all_rejected = existing_rejected + rejected
    write_jsonl(rejected_path, all_rejected)

    # Build summary
    accepted_counts = {t: len(objs) for t, objs in accepted_by_type.items()}
    total_accepted = sum(accepted_counts.values())
    total_rejected = len(rejected)

    logger.info(
        "Validation complete: %d accepted, %d rejected",
        total_accepted,
        total_rejected,
    )

    return {
        "accepted": accepted_counts,
        "rejected": rejected,
        "stats": {
            "total_input": total_input,
            "total_accepted": total_accepted,
            "total_rejected": total_rejected,
        },
    }
