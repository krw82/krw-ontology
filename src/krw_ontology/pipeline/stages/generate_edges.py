"""Deterministic stage: generate graph edges from explicit references."""

from __future__ import annotations

import logging
from pathlib import Path

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.schema.id_utils import (
    generate_edge_local_id,
    generate_scoped_id,
)
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import read_jsonl, write_jsonl

logger = logging.getLogger("krw_ontology")

_OBJECT_RELATIONS = {
    "RiskFactor": ("describes_risk", "describes_risk"),
    "GrowthDriver": ("describes_driver", "describes_driver"),
    "Headwind": ("describes_headwind", "describes_headwind"),
    "BusinessActivity": ("describes_activity", "describes_activity"),
    "ExternalFactorExposure": ("describes_exposure", "describes_exposure"),
}

_RELATION_META = {
    "contains_quote": ("evidence", "direct", "deterministic_reference"),
    "has_signal": ("evidence", "derived", "deterministic_reference"),
    "supports": ("evidence", "direct", "deterministic_reference"),
    "describes_risk": ("interpretation", "derived", "deterministic_reference"),
    "describes_driver": ("interpretation", "derived", "deterministic_reference"),
    "describes_headwind": ("interpretation", "derived", "deterministic_reference"),
    "describes_activity": ("interpretation", "derived", "deterministic_reference"),
    "describes_exposure": ("interpretation", "derived", "deterministic_reference"),
    "manifests_as": ("exposure_link", "derived", "deterministic_shared_claim"),
    "supports_assumption": ("evidence", "direct", "deterministic_reference"),
    "derived_from": ("interpretation", "derived", "deterministic_reference"),
}


async def generate_edges(
    worker: ExtractionWorker,
    ontology_dir: Path,
    ticker: str,
    period: str,
    doc_type: str,
    risks: list[dict] | None = None,
    growth_drivers: list[dict] | None = None,
    headwinds: list[dict] | None = None,
    business_activities: list[dict] | None = None,
    external_factor_exposures: list[dict] | None = None,
    assumptions: list[dict] | None = None,
    claims: list[dict] | None = None,
    quotes: list[dict] | None = None,
) -> list[dict]:
    """Generate edges by translating object reference fields.

    This stage intentionally does not call the SDK. Upstream artifacts already
    contain explicit references such as supported_by_quotes, supported_by_claims,
    affects, and related_metrics; edge generation is a deterministic projection
    of those references into graph form.
    """
    del worker
    stage_name = "generate_edges"
    doc_type_key = normalize_doc_type(doc_type)
    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"

    spans = read_jsonl(ontology_dir / "spans.jsonl")
    signals = read_jsonl(ontology_dir / "language_signals.jsonl")
    if risks is None:
        risks = read_jsonl(ontology_dir / "risks.jsonl")
    if growth_drivers is None:
        growth_drivers = read_jsonl(ontology_dir / "growth_drivers.jsonl")
    if headwinds is None:
        headwinds = read_jsonl(ontology_dir / "headwinds.jsonl")
    if business_activities is None:
        business_activities = read_jsonl(ontology_dir / "business_activities.jsonl")
    if external_factor_exposures is None:
        external_factor_exposures = read_jsonl(ontology_dir / "external_factor_exposures.jsonl")
    if assumptions is None:
        assumptions = read_jsonl(ontology_dir / "assumption_candidates.jsonl")
    if claims is None:
        claims = read_jsonl(ontology_dir / "claims.jsonl")
    if quotes is None:
        quotes = read_jsonl(ontology_dir / "evidence_quotes.jsonl")

    valid_ids = {
        obj["id"]
        for obj in [
            *spans,
            *quotes,
            *signals,
            *claims,
            *risks,
            *growth_drivers,
            *headwinds,
            *business_activities,
            *external_factor_exposures,
            *assumptions,
        ]
        if obj.get("id")
    }
    quote_ids = {quote["id"] for quote in quotes if quote.get("id")}

    edges: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for quote in quotes:
        _add_edge(
            edges, seen, valid_ids, ticker, period, doc_type, source_document_id,
            from_id=quote.get("source_span_id"),
            to_id=quote.get("id"),
            relation_id="contains_quote",
            relation_name="contains_quote",
            rationale="EvidenceQuote.source_span_id points to the source span.",
        )

    for signal in signals:
        _add_edge(
            edges, seen, valid_ids, ticker, period, doc_type, source_document_id,
            from_id=signal.get("source_quote_id"),
            to_id=signal.get("id"),
            relation_id="has_signal",
            relation_name="has_signal",
            rationale="LanguageSignal.source_quote_id points to the quote it interprets.",
        )

    for claim in claims:
        for quote_id in claim.get("supported_by_quotes") or []:
            if quote_id not in quote_ids:
                continue
            _add_edge(
                edges, seen, valid_ids, ticker, period, doc_type, source_document_id,
                from_id=quote_id,
                to_id=claim.get("id"),
                relation_id="supports",
                relation_name="supports",
                rationale="ResearchClaim.supported_by_quotes includes this evidence quote.",
            )

    for obj in [*risks, *growth_drivers, *headwinds, *business_activities, *external_factor_exposures]:
        obj_type = obj.get("type", "")
        relation = _OBJECT_RELATIONS.get(obj_type)
        if relation:
            relation_id, relation_name = relation
            for claim_id in obj.get("supported_by_claims") or []:
                _add_edge(
                    edges, seen, valid_ids, ticker, period, doc_type, source_document_id,
                    from_id=claim_id,
                    to_id=obj.get("id"),
                    relation_id=relation_id,
                    relation_name=relation_name,
                    rationale=f"{obj_type}.supported_by_claims includes this research claim.",
                )

    for exposure in external_factor_exposures:
        _add_exposure_manifest_edges(
            edges,
            seen,
            valid_ids,
            ticker,
            period,
            doc_type,
            source_document_id,
            exposure,
            [*risks, *growth_drivers, *headwinds],
        )

    for assumption in assumptions:
        for quote_id in assumption.get("supported_by_quotes") or []:
            if quote_id not in quote_ids:
                continue
            _add_edge(
                edges, seen, valid_ids, ticker, period, doc_type, source_document_id,
                from_id=quote_id,
                to_id=assumption.get("id"),
                relation_id="supports_assumption",
                relation_name="supports_assumption",
                rationale="AssumptionCandidate.supported_by_quotes includes this evidence quote.",
            )
        for claim_id in assumption.get("supported_by_claims") or []:
            _add_edge(
                edges, seen, valid_ids, ticker, period, doc_type, source_document_id,
                from_id=assumption.get("id"),
                to_id=claim_id,
                relation_id="derived_from",
                relation_name="derived_from",
                rationale="AssumptionCandidate.supported_by_claims includes this research claim.",
            )

    write_jsonl(ontology_dir / "edges.jsonl", edges)
    _clear_stage_failures(ontology_dir / "batch_failures.jsonl", stage_name)
    logger.info(f"{stage_name}: generated {len(edges)} edges", extra={"stage": stage_name})
    return edges


def _add_edge(
    edges: list[dict],
    seen: set[tuple[str, str, str]],
    valid_ids: set[str],
    ticker: str,
    period: str,
    doc_type: str,
    source_document_id: str,
    *,
    from_id: str | None,
    to_id: str | None,
    relation_id: str,
    relation_name: str,
    rationale: str,
) -> None:
    if not from_id or not to_id or from_id not in valid_ids or to_id not in valid_ids:
        return
    key = (from_id, relation_id, to_id)
    if key in seen:
        return
    seen.add(key)

    doc_type_key = normalize_doc_type(doc_type)
    local_id = generate_edge_local_id(relation_id, from_id, to_id)
    edge_class, evidence_level, generation_method = _RELATION_META[relation_id]
    edges.append({
        "id": generate_scoped_id("edge", ticker, period, doc_type_key, local_id),
        "type": "Edge",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": doc_type,
        "period": period,
        "from_id": from_id,
        "to_id": to_id,
        "relation_name": relation_name,
        "relation_id": relation_id,
        "edge_class": edge_class,
        "evidence_level": evidence_level,
        "generation_method": generation_method,
        "rationale": rationale,
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    })


def _add_exposure_manifest_edges(
    edges: list[dict],
    seen: set[tuple[str, str, str]],
    valid_ids: set[str],
    ticker: str,
    period: str,
    doc_type: str,
    source_document_id: str,
    exposure: dict,
    research_objects: list[dict],
) -> None:
    exposure_claims = set(exposure.get("supported_by_claims") or [])
    if not exposure_claims:
        return
    for obj in research_objects:
        shared_claims = exposure_claims.intersection(obj.get("supported_by_claims") or [])
        if not shared_claims:
            continue
        _add_edge(
            edges, seen, valid_ids, ticker, period, doc_type, source_document_id,
            from_id=exposure.get("id"),
            to_id=obj.get("id"),
            relation_id="manifests_as",
            relation_name="manifests_as",
            rationale=(
                "ExternalFactorExposure and research object share supporting "
                f"claim(s): {', '.join(sorted(shared_claims)[:3])}."
            ),
        )


def _clear_stage_failures(failures_path: Path, stage_name: str) -> None:
    if not failures_path.exists():
        return
    failures = [row for row in read_jsonl(failures_path) if row.get("stage") != stage_name]
    write_jsonl(failures_path, failures)
