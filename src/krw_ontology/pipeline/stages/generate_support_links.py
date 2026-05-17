"""Generate evidence SupportLink rows from explicit support fields."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.schema.id_utils import generate_edge_local_id, generate_scoped_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import read_jsonl, write_jsonl

logger = logging.getLogger("krw_ontology")


def generate_support_links(
    *,
    ontology_dir: Path,
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
) -> list[dict[str, Any]]:
    """Project support references into first-class evidence relationship rows."""
    doc_type_key = normalize_doc_type(document_type)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    objects = []
    for filename in (
        "claims.jsonl",
        "business_activities.jsonl",
        "external_factor_exposures.jsonl",
        "assumption_candidates.jsonl",
        "change_events.jsonl",
        "business_factors.jsonl",
        "agreement_terms.jsonl",
        "business_events.jsonl",
        "metric_observations.jsonl",
        "calculations.jsonl",
    ):
        objects.extend(read_jsonl(ontology_dir / filename))

    valid_objects = {
        obj["id"]: obj
        for filename in (
            "spans.jsonl",
            "evidence_quotes.jsonl",
            "claims.jsonl",
            "business_activities.jsonl",
            "external_factor_exposures.jsonl",
            "assumption_candidates.jsonl",
            "xbrl_facts.jsonl",
            "metric_observations.jsonl",
            "calculations.jsonl",
            "business_factors.jsonl",
            "agreement_terms.jsonl",
            "business_events.jsonl",
            "canonical_entities.jsonl",
            "entity_mentions.jsonl",
        )
        for obj in read_jsonl(ontology_dir / filename)
        if obj.get("id")
    }
    valid_ids = set(valid_objects)

    for obj in objects:
        target_id = obj.get("id")
        if not target_id or target_id not in valid_ids:
            continue
        for quote_id in obj.get("supported_by_quotes") or []:
            _add_support(
                rows,
                seen,
                valid_ids,
                ticker,
                period,
                doc_type_key,
                document_type,
                source_document_id,
                valid_objects=valid_objects,
                from_id=quote_id,
                to_id=target_id,
                support_type="direct_quote_support",
                support_role="quote_support",
                evidence_strength="direct",
                requires_inference=bool(obj.get("requires_inference", False)),
            )
        for claim_id in obj.get("supported_by_claims") or []:
            _add_support(
                rows,
                seen,
                valid_ids,
                ticker,
                period,
                doc_type_key,
                document_type,
                source_document_id,
                valid_objects=valid_objects,
                from_id=claim_id,
                to_id=target_id,
                support_type="claim_support",
                support_role="claim_support",
                evidence_strength=str(obj.get("evidence_grade") or obj.get("evidence_strength") or "indirect"),
                requires_inference=True,
            )
        for source_fact_id in obj.get("source_fact_ids") or []:
            _add_support(
                rows,
                seen,
                valid_ids,
                ticker,
                period,
                doc_type_key,
                document_type,
                source_document_id,
                valid_objects=valid_objects,
                from_id=source_fact_id,
                to_id=target_id,
                support_type="derived_from",
                support_role="numeric_source",
                evidence_strength="direct",
                requires_inference=False,
            )
        for source_metric_id in obj.get("source_metric_ids") or []:
            _add_support(
                rows,
                seen,
                valid_ids,
                ticker,
                period,
                doc_type_key,
                document_type,
                source_document_id,
                valid_objects=valid_objects,
                from_id=source_metric_id,
                to_id=target_id,
                support_type="calculation_input",
                support_role="numeric_source",
                evidence_strength="derived",
                requires_inference=False,
            )
        for input_metric_id in obj.get("input_metric_ids") or []:
            _add_support(
                rows,
                seen,
                valid_ids,
                ticker,
                period,
                doc_type_key,
                document_type,
                source_document_id,
                valid_objects=valid_objects,
                from_id=input_metric_id,
                to_id=target_id,
                support_type="calculation_input",
                support_role="numeric_source",
                evidence_strength="derived",
                requires_inference=False,
            )
        if obj.get("output_metric_id"):
            _add_support(
                rows,
                seen,
                valid_ids,
                ticker,
                period,
                doc_type_key,
                document_type,
                source_document_id,
                valid_objects=valid_objects,
                from_id=target_id,
                to_id=obj.get("output_metric_id"),
                support_type="calculates",
                support_role="calculation_output",
                evidence_strength="derived",
                requires_inference=False,
            )

    write_jsonl(ontology_dir / "support_links.jsonl", rows)
    logger.info(
        "generate_support_links: generated %d support links",
        len(rows),
        extra={"stage": "generate_support_links"},
    )
    return rows


def _add_support(
    rows: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    valid_ids: set[str],
    ticker: str,
    period: str,
    doc_type_key: str,
    document_type: str,
    source_document_id: str,
    *,
    valid_objects: dict[str, dict[str, Any]],
    from_id: str | None,
    to_id: str | None,
    support_type: str,
    support_role: str,
    evidence_strength: str,
    requires_inference: bool,
) -> None:
    if not from_id or not to_id or from_id not in valid_ids or to_id not in valid_ids:
        return
    key = (from_id, support_type, to_id)
    if key in seen:
        return
    seen.add(key)
    local_id = generate_edge_local_id(support_type, from_id, to_id)
    support_object = valid_objects.get(from_id) or {}
    target_object = valid_objects.get(to_id) or {}
    rows.append({
        "id": generate_scoped_id("support_link", ticker, period, doc_type_key, local_id),
        "type": "SupportLink",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "from_id": from_id,
        "to_id": to_id,
        "support_object_id": from_id,
        "support_object_type": support_object.get("type"),
        "target_object_id": to_id,
        "target_object_type": target_object.get("type"),
        "support_type": support_type,
        "support_role": support_role,
        "stance": support_type if support_type in {"derived_from", "calculation_input", "calculates"} else "supports",
        "support_strength": evidence_strength,
        "inference_level": "inferred" if requires_inference else "direct_quote",
        "evidence_grade": evidence_strength,
        "evidence_strength": evidence_strength,
        "requires_inference": requires_inference,
        "created_by": "deterministic_projection",
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    })
