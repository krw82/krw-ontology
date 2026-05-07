"""Deterministic stage: extract company business activities from claims."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.schema.id_utils import generate_scoped_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.sector_packs import merged_pack_for_text
from krw_ontology.utils.io import read_jsonl, write_jsonl

logger = logging.getLogger("krw_ontology")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80].rstrip("-")


async def extract_business_activities(
    worker: ExtractionWorker,
    ontology_dir: Path,
    ticker: str,
    period: str,
    doc_type: str,
    claims: list[dict] | None = None,
) -> list[dict]:
    """Build document-grounded BusinessActivity objects from claim text."""
    del worker
    stage_name = "extract_business_activities"
    doc_type_key = normalize_doc_type(doc_type)
    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"
    claims = claims if claims is not None else read_jsonl(ontology_dir / "claims.jsonl")
    document_text = "\n".join(str(claim.get("claim_text") or "") for claim in claims)
    pack = merged_pack_for_text(document_text, preferred_sector=_preferred_sector(claims))

    by_key: dict[str, dict[str, Any]] = {}
    for claim in claims:
        activity_key = _canonical_activity_key(claim.get("activity_hint"), pack.business_activities)
        if not activity_key:
            continue
        spec = pack.business_activities.get(activity_key) or _activity_spec_from_hint(activity_key)
        _upsert_activity(
            by_key,
            activity_key,
            spec,
            [claim],
            ticker=ticker,
            period=period,
            doc_type=doc_type,
            doc_type_key=doc_type_key,
            source_document_id=source_document_id,
            sector=claim.get("sector_hint") or pack.sector,
        )

    for activity_key, spec in pack.business_activities.items():
        matched_claims = [
            claim for claim in claims
            if not claim.get("activity_hint")
            and _matches_alias(claim.get("claim_text") or "", spec.get("aliases") or [])
        ]
        if not matched_claims:
            continue
        _upsert_activity(
            by_key,
            activity_key,
            spec,
            matched_claims,
            ticker=ticker,
            period=period,
            doc_type=doc_type,
            doc_type_key=doc_type_key,
            source_document_id=source_document_id,
            sector=pack.sector,
        )

    activities = list(by_key.values())
    write_jsonl(ontology_dir / "business_activities.jsonl", activities)
    logger.info(
        "%s: extracted %d business activities using sector_pack=%s",
        stage_name,
        len(activities),
        pack.sector,
        extra={"stage": stage_name},
    )
    return activities


def _matches_alias(text: str, aliases: list[str]) -> bool:
    text_l = text.lower()
    return any(str(alias).lower() in text_l for alias in aliases)


def _upsert_activity(
    by_key: dict[str, dict[str, Any]],
    activity_key: str,
    spec: dict[str, Any],
    matched_claims: list[dict],
    *,
    ticker: str,
    period: str,
    doc_type: str,
    doc_type_key: str,
    source_document_id: str,
    sector: str,
) -> None:
    claim_ids = [claim["id"] for claim in matched_claims if claim.get("id")]
    quote_ids = _dedupe(
        quote_id
        for claim in matched_claims
        for quote_id in claim.get("supported_by_quotes") or []
    )
    metrics = _dedupe(
        [
            *(spec.get("related_metrics") or []),
            *[
                metric
                for claim in matched_claims
                for metric in claim.get("related_metrics") or []
            ],
        ]
    )
    if activity_key in by_key:
        existing = by_key[activity_key]
        existing["supported_by_claims"] = _dedupe([*existing.get("supported_by_claims", []), *claim_ids])
        existing["supported_by_quotes"] = _dedupe([*existing.get("supported_by_quotes", []), *quote_ids])
        existing["related_metrics"] = _dedupe([*existing.get("related_metrics", []), *metrics])
        existing["sector_tags"] = _dedupe([*existing.get("sector_tags", []), sector])
        if existing.get("confidence") != "high":
            existing["confidence"] = _confidence(matched_claims)
        return

    name = str(spec.get("name") or activity_key.replace("_", " ").title())
    by_key[activity_key] = {
        "id": generate_scoped_id("business_activity", ticker, period, doc_type_key, _slugify(name)),
        "type": "BusinessActivity",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": doc_type,
        "period": period,
        "name": name,
        "activity_type": str(spec.get("activity_type") or "business_activity"),
        "description": _description(name, matched_claims),
        "revenue_relevance": str(spec.get("revenue_relevance") or "unknown"),
        "cost_relevance": str(spec.get("cost_relevance") or "unknown"),
        "related_metrics": metrics,
        "sector_tags": [sector],
        "supported_by_claims": claim_ids,
        "supported_by_quotes": quote_ids,
        "confidence": _confidence(matched_claims),
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    }


def _canonical_activity_key(value: str | None, specs: dict[str, dict[str, Any]]) -> str:
    if not value:
        return ""
    normalized = _normalize_key(value)
    if normalized in specs:
        return normalized
    for key, spec in specs.items():
        candidates = [key, spec.get("name"), *(spec.get("aliases") or [])]
        if normalized in {_normalize_key(candidate) for candidate in candidates if candidate}:
            return key
    return normalized


def _activity_spec_from_hint(activity_key: str) -> dict[str, Any]:
    return {
        "name": activity_key.replace("_", " ").title(),
        "activity_type": "business_activity",
        "revenue_relevance": "unknown",
        "cost_relevance": "unknown",
        "related_metrics": [],
    }


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _preferred_sector(claims: list[dict]) -> str | None:
    for claim in claims:
        if claim.get("sector_hint"):
            return str(claim["sector_hint"])
    return None


def _description(name: str, claims: list[dict]) -> str:
    sample = " ".join((claims[0].get("claim_text") or "").split()) if claims else ""
    if not sample:
        return f"Evidence-backed activity related to {name}."
    return f"Evidence-backed activity related to {name}: {sample[:240]}"


def _confidence(claims: list[dict]) -> str:
    values = {claim.get("confidence") for claim in claims}
    if values == {"high"} or len(claims) >= 3:
        return "high"
    if "high" in values or "medium" in values:
        return "medium"
    return "low"


def _dedupe(values) -> list:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
