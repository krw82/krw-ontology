"""Generate final v1 canonical ontology artifacts."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import find_project_root, read_jsonl, write_jsonl
from krw_ontology.validators.metric_validator import _load_metric_dictionary

logger = logging.getLogger("krw_ontology")

_AGREEMENT_KEYWORDS = (
    "agreement", "contract", "offtake", "take-or-pay", "lease", "covenant",
    "debt", "notes", "credit facility", "loan", "maturity", "commitment",
)
_EVENT_KEYWORDS = (
    "project", "milestone", "approval", "regulatory", "guidance", "target",
    "expected", "expects", "litigation", "proceeding", "construction",
    "commercial operation", "financing", "delay",
)
_REGULATOR_ALIASES = {
    "FERC": "Federal Energy Regulatory Commission",
    "SEC": "U.S. Securities and Exchange Commission",
    "DOE": "U.S. Department of Energy",
    "EPA": "U.S. Environmental Protection Agency",
}


def generate_canonical_artifacts(
    *,
    ontology_dir: Path,
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
    clean_text_hash: str | None = None,
    raw_text_hash: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build the final schema layer from existing deterministic/LLM outputs."""
    doc_type_key = normalize_doc_type(document_type)
    spans = read_jsonl(ontology_dir / "spans.jsonl")
    quotes = read_jsonl(ontology_dir / "evidence_quotes.jsonl")
    claims = read_jsonl(ontology_dir / "claims.jsonl")
    activities = read_jsonl(ontology_dir / "business_activities.jsonl")
    exposures = read_jsonl(ontology_dir / "external_factor_exposures.jsonl")
    xbrl_facts = read_jsonl(ontology_dir / "xbrl_facts.jsonl")
    change_events = read_jsonl(ontology_dir / "change_events.jsonl")

    source_documents = [
        _source_document(
            ticker,
            period,
            document_type,
            source_document_id,
            raw_text_hash,
            clean_text_hash,
        )
    ]
    source_locations = [_source_location(span, clean_text_hash) for span in spans if span.get("id")]
    source_tables: list[dict[str, Any]] = []
    source_table_cells: list[dict[str, Any]] = []

    metric_observations, calculations = _metric_observations(
        ontology_dir, ticker, period, document_type, source_document_id, doc_type_key, xbrl_facts
    )
    taxonomy_terms = _taxonomy_terms(ticker, period, document_type, source_document_id, metric_observations, activities, exposures)
    canonical_entities = _canonical_entities(ticker, period, document_type, source_document_id, exposures, claims)
    entity_mentions = _entity_mentions(ticker, period, document_type, source_document_id, canonical_entities, claims, quotes)
    business_factors = _business_factors(
        ticker, period, document_type, source_document_id, doc_type_key, claims
    )
    agreement_terms = _agreement_terms(ticker, period, document_type, source_document_id, doc_type_key, claims)
    business_events = _business_events(ticker, period, document_type, source_document_id, doc_type_key, claims, change_events)

    artifacts = {
        "source_documents": source_documents,
        "source_locations": source_locations,
        "source_tables": source_tables,
        "source_table_cells": source_table_cells,
        "taxonomy_terms": taxonomy_terms,
        "canonical_entities": canonical_entities,
        "entity_mentions": entity_mentions,
        "metric_observations": metric_observations,
        "calculations": calculations,
        "business_factors": business_factors,
        "agreement_terms": agreement_terms,
        "business_events": business_events,
    }
    file_map = {
        "source_documents": "source_documents.jsonl",
        "source_locations": "source_locations.jsonl",
        "source_tables": "source_tables.jsonl",
        "source_table_cells": "source_table_cells.jsonl",
        "taxonomy_terms": "taxonomy_terms.jsonl",
        "canonical_entities": "canonical_entities.jsonl",
        "entity_mentions": "entity_mentions.jsonl",
        "metric_observations": "metric_observations.jsonl",
        "calculations": "calculations.jsonl",
        "business_factors": "business_factors.jsonl",
        "agreement_terms": "agreement_terms.jsonl",
        "business_events": "business_events.jsonl",
    }
    for key, filename in file_map.items():
        write_jsonl(ontology_dir / filename, artifacts[key])

    logger.info(
        "generate_canonical_artifacts: wrote %s",
        ", ".join(f"{key}={len(value)}" for key, value in artifacts.items()),
        extra={"stage": "generate_canonical_artifacts"},
    )
    return artifacts


def _source_document(
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
    raw_text_hash: str | None,
    clean_text_hash: str | None,
) -> dict[str, Any]:
    doc_type_key = normalize_doc_type(document_type)
    document = {
        "id": source_document_id,
        "type": "SourceDocument",
        "object_type": "SourceDocument",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "raw_text_hash": raw_text_hash,
        "clean_text_hash": clean_text_hash,
        "schema_version": SCHEMA_VERSION,
    }
    return document


def _source_location(span: dict[str, Any], clean_text_hash: str | None) -> dict[str, Any]:
    return {
        "id": f"source_location:{span['id']}",
        "type": "SourceLocation",
        "object_type": "SourceLocation",
        "ticker": span["ticker"],
        "source_document_id": span["source_document_id"],
        "document_type": span["document_type"],
        "period": span["period"],
        "source_boundary": "sec_filing",
        "source_span_id": span["id"],
        "section_name": span.get("section_name"),
        "section_path": span.get("section_key") or span.get("section_name"),
        "start_char": span.get("start_char"),
        "end_char": span.get("end_char"),
        "text_hash": span.get("text_hash"),
        "clean_text_hash_at_extraction": clean_text_hash,
        "schema_version": SCHEMA_VERSION,
    }


def _taxonomy_terms(
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
    metric_observations: list[dict[str, Any]],
    activities: list[dict[str, Any]],
    exposures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    terms: dict[str, dict[str, Any]] = {}

    def add(term_type: str, name: str | None, taxonomy: str = "local") -> None:
        if not name:
            return
        canonical_name = _slug(name)
        if not canonical_name:
            return
        term_id = f"term:{term_type}:{canonical_name}"
        terms.setdefault(term_id, {
            "id": term_id,
            "type": "TaxonomyTerm",
            "object_type": "TaxonomyTerm",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "taxonomy": taxonomy,
            "term_type": term_type,
            "canonical_name": canonical_name,
            "display_name": str(name),
            "aliases": [str(name)],
            "schema_version": SCHEMA_VERSION,
        })

    for metric in metric_observations:
        add("metric", metric.get("metric_name"), "metric")
    for activity in activities:
        add("activity", activity.get("activity_type") or activity.get("name"), "business_activity")
    for exposure in exposures:
        add("factor", exposure.get("factor"), "external_factor")
        add("factor", exposure.get("benchmark"), "external_factor")
        add("channel", exposure.get("impact_channel"), "impact_channel")
    return sorted(terms.values(), key=lambda row: row["id"])


def _canonical_entities(
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
    exposures: list[dict[str, Any]],
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}

    def add(entity_type: str, name: str | None, aliases: list[str] | None = None) -> str | None:
        if not name:
            return None
        canonical_name = str(name).strip()
        slug = _slug(canonical_name)
        if not slug:
            return None
        entity_id = f"entity:{entity_type}:{ticker}:{slug}" if entity_type not in {"factor", "benchmark", "regulator"} else f"entity:{entity_type}:{slug}"
        entities.setdefault(entity_id, {
            "id": entity_id,
            "type": "CanonicalEntity",
            "object_type": "CanonicalEntity",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "entity_type": entity_type,
            "canonical_name": canonical_name,
            "aliases": sorted(set([canonical_name, *(aliases or [])])),
            "ticker_scope": ticker,
            "status": "active",
            "schema_version": SCHEMA_VERSION,
        })
        return entity_id

    add("company", ticker, [ticker])
    for exposure in exposures:
        add("factor", exposure.get("factor"))
        add("benchmark", exposure.get("benchmark"))
    for claim in claims:
        text = claim.get("claim_text") or ""
        for acronym, full_name in _REGULATOR_ALIASES.items():
            if acronym in text:
                add("regulator", acronym, [full_name])
        for project_name in _project_mentions(text):
            add("project", project_name)
    return sorted(entities.values(), key=lambda row: row["id"])


def _entity_mentions(
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
    entities: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mention_sources = [
        *({"id": claim.get("id"), "type": "ResearchClaim", "text": claim.get("claim_text", "")} for claim in claims),
        *({"id": quote.get("id"), "type": "EvidenceQuote", "text": quote.get("quote_text", "")} for quote in quotes),
    ]
    mentions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for entity in entities:
        aliases = entity.get("aliases") or [entity.get("canonical_name", "")]
        for source in mention_sources:
            source_id = source.get("id")
            text = source.get("text") or ""
            if not source_id or not text:
                continue
            for alias in aliases:
                if not alias or alias.lower() not in text.lower():
                    continue
                key = (entity["id"], source_id, alias.lower())
                if key in seen:
                    continue
                seen.add(key)
                mentions.append({
                    "id": f"entity_mention:{_hash(entity['id'], source_id, alias)}",
                    "type": "EntityMention",
                    "object_type": "EntityMention",
                    "ticker": ticker,
                    "source_document_id": source_document_id,
                    "document_type": document_type,
                    "period": period,
                    "canonical_entity_id": entity["id"],
                    "mention_text": alias,
                    "source_object_id": source_id,
                    "source_object_type": source["type"],
                    "confidence": "medium",
                    "schema_version": SCHEMA_VERSION,
                })
                break
    return mentions


def _metric_observations(
    ontology_dir: Path,
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
    doc_type_key: str,
    xbrl_facts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observations: list[dict[str, Any]] = []
    calculations: list[dict[str, Any]] = []
    metric_specs = _load_metric_specs(ontology_dir)
    selected: set[tuple[str, int | None, str, str | None, str | None]] = set()

    for metric_name, spec in metric_specs.items():
        tags = set(spec.get("xbrl_tags") or [])
        if not tags:
            continue
        candidates = [fact for fact in xbrl_facts if fact.get("taxonomy_tag") in tags]
        candidates.sort(key=_fact_rank)
        for fact in candidates:
            context = fact.get("context") or {}
            if context.get("has_dimensions"):
                continue
            fiscal_year = context.get("fiscal_year")
            period_type = _metric_period_type(context)
            start_date = context.get("start_date")
            end_date = context.get("end_date") or context.get("instant")
            key = (metric_name, fiscal_year, period_type, start_date, end_date)
            if key in selected:
                continue
            selected.add(key)
            obs_id = f"metric_observation:{ticker}:{period}:{doc_type_key}:{_hash(metric_name, fiscal_year, fact.get('id'))}"
            observations.append({
                "id": obs_id,
                "type": "MetricObservation",
                "object_type": "MetricObservation",
                "ticker": ticker,
                "source_document_id": source_document_id,
                "document_type": document_type,
                "period": period,
                "metric_term_id": f"term:metric:{_slug(metric_name)}",
                "metric_name": metric_name,
                "value": fact.get("value"),
                "unit": spec.get("unit") or fact.get("unit") or "",
                "scale": "ones",
                "fiscal_year": fiscal_year,
                "fiscal_period": _period_quarter(period),
                "period_start": start_date,
                "period_end": end_date,
                "period_type": period_type,
                "source_type": "xbrl",
                "source_fact_ids": [fact["id"]],
                "source_metric_ids": None,
                "normalization": "reported",
                "dimensions": {},
                "calculation_id": None,
                "confidence": 1.0,
                "schema_version": SCHEMA_VERSION,
            })

    _append_derived_observations(
        observations,
        calculations,
        ticker=ticker,
        period=period,
        doc_type_key=doc_type_key,
        document_type=document_type,
        source_document_id=source_document_id,
    )
    return observations, calculations


def _business_factors(
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
    doc_type_key: str,
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for claim in claims:
        role = _business_factor_role(claim)
        name = _business_factor_name(claim)
        if not role or not name:
            continue
        category = _business_factor_category(claim, role)
        key = (_slug(name), _slug(category))
        if key not in grouped:
            grouped[key] = {
                "id": f"business_factor:{ticker}:{period}:{doc_type_key}:{_hash(*key)}",
                "type": "BusinessFactor",
                "object_type": "BusinessFactor",
                "ticker": ticker,
                "source_document_id": source_document_id,
                "document_type": document_type,
                "period": period,
                "name": name,
                "description": f"Evidence-backed claims describe {name}.",
                "factor_roles": [],
                "category": category,
                "occurrence_status": "potential" if role == "risk" else "current_or_potential",
                "company_polarity": "positive" if role == "growth_driver" else "negative",
                "modality": "conditional" if role == "risk" else "observed",
                "time_horizon": claim.get("time_horizon") or "unknown",
                "affected_channels": [],
                "source_object_ids": [],
                "supported_by_claims": [],
                "supported_by_quotes": [],
                "materiality_hint": claim.get("materiality_hint"),
                "materiality_basis": claim.get("materiality_basis") or ["direct_claim_support"],
                "confidence": claim.get("confidence", "medium"),
                "review_status": "accepted",
                "schema_version": SCHEMA_VERSION,
            }
        target = grouped[key]
        if role not in target["factor_roles"]:
            target["factor_roles"].append(role)
        _extend_unique(target["source_object_ids"], [claim.get("id")])
        _extend_unique(target["affected_channels"], claim.get("impact_channels") or claim.get("related_metrics") or [])
        _extend_unique(target["supported_by_claims"], [claim.get("id")])
        _extend_unique(target["supported_by_quotes"], claim.get("supported_by_quotes") or [])
        _extend_unique(target["materiality_basis"], claim.get("materiality_basis") or ["direct_claim_support"])
    return list(grouped.values())


def _load_metric_specs(ontology_dir: Path) -> dict[str, dict[str, Any]]:
    metric_path = find_project_root(ontology_dir) / "ontology" / "schema" / "metric_dictionary.yaml"
    data = _load_metric_dictionary(metric_path)
    return data.get("canonical_metrics") or {}


def _fact_rank(fact: dict[str, Any]) -> tuple[int, int, int]:
    context = fact.get("context") or {}
    no_dimensions = 0 if not context.get("has_dimensions") else 1
    unit_rank = 0 if fact.get("unit") in {"USD", "shares", "usdPerShare", "number", "pure"} else 1
    year = context.get("fiscal_year") or 0
    return no_dimensions, unit_rank, -year


def _metric_period_type(context: dict[str, Any]) -> str:
    if context.get("instant"):
        return "instant"
    days = context.get("duration_days")
    if isinstance(days, int):
        if 70 <= days <= 115:
            return "quarter"
        if 160 <= days <= 300:
            return "year_to_date"
        if days >= 330:
            return "annual"
    return context.get("period_type") or "duration"


def _period_year(period: str) -> int | None:
    match = re.match(r"^FY(\d{4})", period)
    return int(match.group(1)) if match else None


def _period_quarter(period: str) -> str | None:
    match = re.match(r"^FY\d{4}(Q[1-4])$", period)
    return match.group(1) if match else None


def _append_derived_observations(
    observations: list[dict[str, Any]],
    calculations: list[dict[str, Any]],
    *,
    ticker: str,
    period: str,
    doc_type_key: str,
    document_type: str,
    source_document_id: str,
) -> None:
    by_metric_year: dict[tuple[str, int | None], dict[str, Any]] = {
        (row["metric_name"], row.get("fiscal_year")): row
        for row in observations
    }
    years = sorted({row.get("fiscal_year") for row in observations if row.get("fiscal_year")})
    current_year = _period_year(period) or (years[-1] if years else None)
    prior_year = current_year - 1 if current_year else None

    def add_derived(metric_name: str, value: float, unit: str, formula: str, inputs: list[dict[str, Any]]) -> None:
        input_ids = [item["id"] for item in inputs]
        calc_id = f"calculation:{ticker}:{period}:{doc_type_key}:{_hash(metric_name, formula, *input_ids)}"
        obs_id = f"metric_observation:{ticker}:{period}:{doc_type_key}:{_hash(metric_name, calc_id)}"
        observations.append({
            "id": obs_id,
            "type": "MetricObservation",
            "object_type": "MetricObservation",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "metric_term_id": f"term:metric:{_slug(metric_name)}",
            "metric_name": metric_name,
            "value": value,
            "unit": unit,
            "scale": "ones",
            "fiscal_year": current_year,
            "fiscal_period": _period_quarter(period),
            "period_start": None,
            "period_end": None,
            "period_type": "derived",
            "source_type": "derived",
            "source_fact_ids": [],
            "source_metric_ids": input_ids,
            "normalization": "derived",
            "dimensions": {},
            "calculation_id": calc_id,
            "confidence": 1.0,
            "schema_version": SCHEMA_VERSION,
        })
        calculations.append({
            "id": calc_id,
            "type": "Calculation",
            "object_type": "Calculation",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "calculation_type": "derived_metric",
            "formula": formula,
            "input_metric_ids": input_ids,
            "output_metric_id": obs_id,
            "calculation_method": "deterministic_code",
            "rounding_policy": "full_precision_then_display",
            "validation_status": "passed",
            "schema_version": SCHEMA_VERSION,
        })
        by_metric_year[(metric_name, current_year)] = observations[-1]

    def add_ratio(metric_name: str, numerator_name: str, denominator_name: str, formula: str) -> None:
        numerator = by_metric_year.get((numerator_name, current_year))
        denominator = by_metric_year.get((denominator_name, current_year))
        if not numerator or not denominator or not denominator.get("value"):
            return
        add_derived(metric_name, (numerator["value"] / denominator["value"]) * 100, "percent", formula, [numerator, denominator])

    def add_growth(metric_name: str, base_name: str) -> None:
        current = by_metric_year.get((base_name, current_year))
        prior = by_metric_year.get((base_name, prior_year))
        if not current or not prior or not prior.get("value"):
            return
        add_derived(metric_name, ((current["value"] - prior["value"]) / prior["value"]) * 100, "percent", f"({base_name}_{current_year} - {base_name}_{prior_year}) / {base_name}_{prior_year}", [current, prior])

    def add_difference(metric_name: str, left_name: str, right_name: str, formula: str) -> None:
        left = by_metric_year.get((left_name, current_year))
        right = by_metric_year.get((right_name, current_year))
        if not left or not right:
            return
        add_derived(metric_name, left["value"] - abs(right["value"]), "USD", formula, [left, right])

    add_growth("revenue_growth", "revenue")
    add_ratio("gross_margin", "gross_profit", "revenue", "gross_profit / revenue")
    add_ratio("operating_margin", "operating_income", "revenue", "operating_income / revenue")
    add_ratio("net_margin", "net_income", "revenue", "net_income / revenue")
    add_difference("free_cash_flow", "operating_cash_flow", "capital_expenditures", "operating_cash_flow - capital_expenditures")
    add_ratio("fcf_margin", "free_cash_flow", "revenue", "free_cash_flow / revenue")


def _business_factor_role(claim: dict[str, Any]) -> str | None:
    hints = {str(value).lower().replace("_", "") for value in claim.get("object_type_hints") or []}
    claim_type = str(claim.get("claim_type") or "").lower()
    direction = str(claim.get("effect_direction") or "").lower()
    text = str(claim.get("claim_text") or "").lower()
    if "growthdriver" in hints or "growth" in claim_type or direction == "positive":
        return "growth_driver"
    if "headwind" in hints or "headwind" in claim_type or "pressure" in text or "decrease" in text or "decline" in text:
        return "headwind"
    if "riskfactor" in hints or "risk" in claim_type or direction == "negative" or "risk" in text or "may adversely" in text:
        return "risk"
    if claim.get("factor_hint") or claim.get("theme_hint") or claim.get("impact_channels"):
        return "risk" if direction in {"negative", "mixed", "uncertain"} else "growth_driver"
    return None


def _business_factor_name(claim: dict[str, Any]) -> str | None:
    for field in ("factor_hint", "theme_hint", "activity_hint"):
        value = claim.get(field)
        if value:
            return str(value).replace("_", " ").strip().title()
    text = re.sub(r"\s+", " ", str(claim.get("claim_text") or "")).strip()
    if not text:
        return None
    return text[:96]


def _business_factor_category(claim: dict[str, Any], role: str) -> str:
    if claim.get("factor_hint"):
        return _slug(claim.get("factor_hint"))
    if claim.get("theme_hint"):
        return _slug(claim.get("theme_hint"))
    return role


def _agreement_terms(
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
    doc_type_key: str,
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for claim in claims:
        text = claim.get("claim_text", "")
        lower = text.lower()
        if not any(keyword in lower for keyword in _AGREEMENT_KEYWORDS):
            continue
        agreement_type = _agreement_type(lower)
        rows.append({
            "id": f"agreement_term:{ticker}:{period}:{doc_type_key}:{_hash(claim.get('id'), agreement_type)}",
            "type": "AgreementTerm",
            "object_type": "AgreementTerm",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "agreement_type": agreement_type,
            "agreement_subtype": None,
            "name": _short_name(text, "Agreement term"),
            "party_entity_ids": [f"entity:company:{ticker}:{_slug(ticker)}"],
            "economic_role": "financing" if agreement_type in {"debt_instrument", "credit_facility", "covenant"} else "operating",
            "affected_channels": claim.get("impact_channels") or [],
            "related_entity_ids": [f"entity:company:{ticker}:{_slug(ticker)}"],
            "supported_by_claims": [claim["id"]],
            "supported_by_quotes": claim.get("supported_by_quotes") or [],
            "materiality_hint": claim.get("materiality_hint"),
            "materiality_basis": claim.get("materiality_basis") or ["agreement_or_obligation_disclosure"],
            "confidence": claim.get("confidence", "medium"),
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        })
    return rows


def _business_events(
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
    doc_type_key: str,
    claims: list[dict[str, Any]],
    change_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for claim in claims:
        text = claim.get("claim_text", "")
        lower = text.lower()
        if not any(keyword in lower for keyword in _EVENT_KEYWORDS):
            continue
        event_type = _event_type(lower)
        rows.append({
            "id": f"business_event:{ticker}:{period}:{doc_type_key}:{_hash(claim.get('id'), event_type)}",
            "type": "BusinessEvent",
            "object_type": "BusinessEvent",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "event_type": event_type,
            "event_subtype": None,
            "name": _short_name(text, "Business event"),
            "event_status": _event_status(lower),
            "date_expression": _date_expression(text),
            "date_type": "period_expression" if _date_expression(text) else None,
            "related_entity_ids": [f"entity:company:{ticker}:{_slug(ticker)}"],
            "affected_channels": claim.get("impact_channels") or [],
            "source_object_ids": [claim["id"]],
            "supported_by_claims": [claim["id"]],
            "supported_by_quotes": claim.get("supported_by_quotes") or [],
            "materiality_hint": claim.get("materiality_hint"),
            "materiality_basis": claim.get("materiality_basis") or ["business_event_disclosure"],
            "confidence": claim.get("confidence", "medium"),
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        })
    for event in change_events:
        rows.append({
            "id": f"business_event:{ticker}:{period}:{doc_type_key}:{_hash(event.get('id'), event.get('event_type'))}",
            "type": "BusinessEvent",
            "object_type": "BusinessEvent",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "event_type": "disclosure_change",
            "event_subtype": event.get("event_type"),
            "name": _short_name(event.get("description", ""), "Disclosure change"),
            "event_status": "observed",
            "date_expression": event.get("event_date"),
            "date_type": "date" if event.get("event_date") else None,
            "related_entity_ids": [f"entity:company:{ticker}:{_slug(ticker)}"],
            "affected_channels": [],
            "source_object_ids": event.get("affected_objects") or [],
            "supported_by_claims": event.get("supported_by_claims") or [],
            "supported_by_quotes": event.get("supported_by_quotes") or [],
            "materiality_hint": "medium",
            "materiality_basis": ["change_event_projection"],
            "confidence": event.get("confidence", "medium"),
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        })
    return rows


def _agreement_type(text: str) -> str:
    if "lease" in text:
        return "lease"
    if "covenant" in text:
        return "covenant"
    if "debt" in text or "notes" in text or "loan" in text:
        return "debt_instrument"
    if "credit facility" in text:
        return "credit_facility"
    if "offtake" in text or "take-or-pay" in text:
        return "offtake_contract"
    return "contract"


def _event_type(text: str) -> str:
    if "approval" in text or "regulatory" in text:
        return "regulatory_proceeding"
    if "guidance" in text:
        return "guidance_update"
    if "litigation" in text or "proceeding" in text:
        return "litigation_event"
    if "financing" in text:
        return "financing_event"
    if "project" in text or "construction" in text or "commercial operation" in text:
        return "project_milestone"
    return "business_update"


def _event_status(text: str) -> str:
    if "completed" in text or "received" in text:
        return "completed"
    if "pending" in text or "subject to" in text:
        return "pending"
    if "expected" in text or "target" in text or "expects" in text:
        return "targeted"
    return "mentioned"


def _date_expression(text: str) -> str | None:
    match = re.search(r"\b(20[0-9]{2}|FY20[0-9]{2}|Q[1-4]\s+20[0-9]{2}|late\s+20[0-9]{2}|early\s+20[0-9]{2})\b", text, re.I)
    return match.group(0) if match else None


def _project_mentions(text: str) -> list[str]:
    mentions = set(re.findall(r"\b[A-Z]{2,}[0-9]+\b", text))
    if "CP2" in text:
        mentions.add("CP2 LNG")
    return sorted(mentions)


def _short_name(text: str, fallback: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return fallback
    return normalized[:120]


def _extend_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _hash(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:14]
