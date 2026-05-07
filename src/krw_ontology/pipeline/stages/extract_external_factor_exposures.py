"""Deterministic stage: extract factor exposure channels from claims."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.factor_taxonomy import (
    canonical_factor_key,
    factor_spec,
    load_factor_taxonomy,
    matching_factor_keys,
    normalize_sector_hint,
)
from krw_ontology.schema.id_utils import generate_scoped_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import read_jsonl, write_jsonl

logger = logging.getLogger("krw_ontology")

_NEGATIVE_WORDS = (
    "adverse", "adversely", "risk", "reduce", "reduced", "decline", "declined",
    "lower", "decrease", "decreased", "pressure", "compress", "compressed",
    "delay", "delayed", "deny", "denied", "loss", "volatile", "volatility",
)
_POSITIVE_WORDS = (
    "increase", "increased", "higher", "growth", "benefit", "improve", "improved",
    "favorable", "strong", "expand", "expanded",
)
_UP_WORDS = ("higher", "increase", "increased", "rising", "rise", "elevated", "inflation")
_DOWN_WORDS = ("lower", "decrease", "decreased", "decline", "declined", "down")
_VOLATILE_WORDS = ("volatile", "volatility", "fluctuate", "fluctuation", "differences", "differ")
_MATERIALITY_WORDS = ("material", "materially", "significant", "substantial")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:90].rstrip("-")


async def extract_external_factor_exposures(
    worker: ExtractionWorker,
    ontology_dir: Path,
    ticker: str,
    period: str,
    doc_type: str,
    claims: list[dict] | None = None,
    business_activities: list[dict] | None = None,
) -> list[dict]:
    """Build document-grounded ExternalFactorExposure objects."""
    del worker
    stage_name = "extract_external_factor_exposures"
    doc_type_key = normalize_doc_type(doc_type)
    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"
    claims = claims if claims is not None else read_jsonl(ontology_dir / "claims.jsonl")
    business_activities = (
        business_activities
        if business_activities is not None
        else read_jsonl(ontology_dir / "business_activities.jsonl")
    )
    taxonomy = load_factor_taxonomy()

    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for claim in claims:
        factor_key = canonical_factor_key(claim.get("factor_hint"), taxonomy)
        if not factor_key:
            continue
        spec = factor_spec(factor_key, taxonomy)
        channels = _dedupe([
            *(claim.get("impact_channels") or []),
            *(claim.get("related_metrics") or []),
        ]) or list(spec.get("channels") or ["business_performance"])
        sector = normalize_sector_hint(claim.get("sector_hint")) or "unknown"
        for channel in channels:
            _upsert_exposure(
                by_key,
                factor_key,
                spec,
                str(channel),
                [claim],
                business_activities,
                ticker=ticker,
                period=period,
                doc_type=doc_type,
                doc_type_key=doc_type_key,
                source_document_id=source_document_id,
                sector=sector,
            )

    for claim in claims:
        if claim.get("factor_hint"):
            continue
        for factor_key in matching_factor_keys(claim.get("claim_text") or "", taxonomy):
            spec = factor_spec(factor_key, taxonomy)
            channels = _dedupe([
                *(claim.get("impact_channels") or []),
                *(claim.get("related_metrics") or []),
            ]) or list(spec.get("channels") or ["business_performance"])
            sector = normalize_sector_hint(claim.get("sector_hint")) or "unknown"
            for channel in channels:
                channel_claims = _claims_for_channel([claim], channel) or [claim]
                _upsert_exposure(
                    by_key,
                    factor_key,
                    spec,
                    str(channel),
                    channel_claims,
                    business_activities,
                    ticker=ticker,
                    period=period,
                    doc_type=doc_type,
                    doc_type_key=doc_type_key,
                    source_document_id=source_document_id,
                    sector=sector,
                )

    exposures = list(by_key.values())
    write_jsonl(ontology_dir / "external_factor_exposures.jsonl", exposures)
    logger.info(
        "%s: extracted %d external factor exposures using global_factor_taxonomy=%s",
        stage_name,
        len(exposures),
        taxonomy.path,
        extra={"stage": stage_name},
    )
    return exposures


def _claims_for_channel(claims: list[dict], channel: str) -> list[dict]:
    channel_terms = {channel, channel.replace("_", " ")}
    if channel in {"gross_margin", "operating_margin"}:
        channel_terms.update({"margin", "margins"})
    if channel == "revenue":
        channel_terms.update({"revenue", "sales", "proceeds"})
    if channel == "cost_of_revenue":
        channel_terms.update({"cost", "costs", "expense", "feed gas"})
    if channel == "cash_flow":
        channel_terms.update({"cash flow", "liquidity", "cash"})
    selected = [
        claim for claim in claims if _contains_any(claim.get("claim_text") or "", tuple(channel_terms))
    ]
    return selected


def _upsert_exposure(
    by_key: dict[tuple[str, str, str], dict[str, Any]],
    factor_key: str,
    spec: dict[str, Any],
    channel: str,
    channel_claims: list[dict],
    business_activities: list[dict],
    *,
    ticker: str,
    period: str,
    doc_type: str,
    doc_type_key: str,
    source_document_id: str,
    sector: str,
) -> None:
    claim_ids = [claim["id"] for claim in channel_claims if claim.get("id")]
    quote_ids = _dedupe(
        quote_id
        for claim in channel_claims
        for quote_id in claim.get("supported_by_quotes") or []
    )
    related_activity_ids = _related_activity_ids(channel_claims, business_activities)
    metrics = _dedupe(
        [
            *[
                metric
                for claim in channel_claims
                for metric in claim.get("related_metrics") or []
            ],
            *[
                impact_channel
                for claim in channel_claims
                for impact_channel in claim.get("impact_channels") or []
            ],
        ]
    )
    sample_text = " ".join((channel_claims[0].get("claim_text") or "").split())
    direction = _direction(sample_text)
    effect = _claim_hint(channel_claims, "effect_direction") or _effect_direction(sample_text)
    materiality = _claim_hint(channel_claims, "materiality_hint")
    time_horizon = _claim_hint(channel_claims, "time_horizon")
    benchmark = _claim_hint(channel_claims, "benchmark_hint") or _benchmark(
        sample_text,
        spec.get("benchmarks") or [],
    )
    key = (factor_key, channel, effect)
    if key in by_key:
        _merge_exposure(by_key[key], claim_ids, quote_ids, related_activity_ids, metrics)
        by_key[key]["sector_tags"] = _dedupe([*by_key[key].get("sector_tags", []), sector])
        return
    mechanism = _mechanism(
        factor_key,
        channel,
        _claim_hint(channel_claims, "theme_hint") or sample_text,
    )
    by_key[key] = {
        "id": generate_scoped_id(
            "external_factor_exposure",
            ticker,
            period,
            doc_type_key,
            _slugify(f"{factor_key}-{channel}-{effect}"),
        ),
        "type": "ExternalFactorExposure",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": doc_type,
        "period": period,
        "factor": factor_key,
        "factor_category": str(spec.get("category") or "unknown"),
        "benchmark": benchmark,
        "direction": direction,
        "impact_channel": channel,
        "effect_direction": effect,
        "mechanism": mechanism,
        "materiality": materiality or ("high" if _contains_any(sample_text, _MATERIALITY_WORDS) else "unknown"),
        "time_horizon": time_horizon or _time_horizon(sample_text),
        "related_business_activities": related_activity_ids,
        "related_metrics": metrics,
        "sector_tags": [sector],
        "supported_by_claims": claim_ids,
        "supported_by_quotes": quote_ids,
        "confidence": _confidence(channel_claims),
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    }


def _related_activity_ids(claims: list[dict], activities: list[dict]) -> list[str]:
    claim_ids = {claim.get("id") for claim in claims}
    return _dedupe(
        activity.get("id")
        for activity in activities
        if claim_ids.intersection(set(activity.get("supported_by_claims") or []))
    )


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _claim_hint(claims: list[dict], field: str) -> str | None:
    for claim in claims:
        value = claim.get(field)
        if value:
            return str(value)
    return None


def _benchmark(text: str, benchmarks: list[str]) -> str | None:
    text_l = text.lower()
    for benchmark in benchmarks:
        if str(benchmark).lower() in text_l:
            return str(benchmark)
    return str(benchmarks[0]) if benchmarks else None


def _direction(text: str) -> str:
    if _contains_any(text, _VOLATILE_WORDS):
        return "volatile"
    up = _contains_any(text, _UP_WORDS)
    down = _contains_any(text, _DOWN_WORDS)
    if up and down:
        return "mixed"
    if up:
        return "up"
    if down:
        return "down"
    return "unknown"


def _effect_direction(text: str) -> str:
    negative = _contains_any(text, _NEGATIVE_WORDS)
    positive = _contains_any(text, _POSITIVE_WORDS)
    if negative and positive:
        return "mixed"
    if negative:
        return "negative"
    if positive:
        return "positive"
    return "uncertain"


def _time_horizon(text: str) -> str:
    text_l = text.lower()
    if any(token in text_l for token in ("future", "long-term", "long term", "over time")):
        return "long"
    if any(token in text_l for token in ("current", "near-term", "near term", "recent")):
        return "short"
    return "unknown"


def _mechanism(factor: str, channel: str, sample_text: str) -> str:
    sample = " ".join(sample_text.split())
    if sample:
        return f"{factor} affects {channel} through filing-described exposure: {sample[:260]}"
    return f"{factor} affects {channel} based on filing-described exposure."


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    text_l = text.lower()
    return any(term.lower() in text_l for term in terms)


def _confidence(claims: list[dict]) -> str:
    values = {claim.get("confidence") for claim in claims}
    if values == {"high"} or len(claims) >= 3:
        return "high"
    if "high" in values or "medium" in values:
        return "medium"
    return "low"


def _merge_exposure(
    existing: dict[str, Any],
    claim_ids: list[str],
    quote_ids: list[str],
    activity_ids: list[str],
    metrics: list[str],
) -> None:
    existing["supported_by_claims"] = _dedupe([*existing.get("supported_by_claims", []), *claim_ids])
    existing["supported_by_quotes"] = _dedupe([*existing.get("supported_by_quotes", []), *quote_ids])
    existing["related_business_activities"] = _dedupe(
        [*existing.get("related_business_activities", []), *activity_ids]
    )
    existing["related_metrics"] = _dedupe([*existing.get("related_metrics", []), *metrics])


def _dedupe(values) -> list:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
