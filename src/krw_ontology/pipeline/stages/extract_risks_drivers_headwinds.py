"""Build RiskFactor/GrowthDriver/Headwind themes from evidence-backed claims."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from krw_ontology.config.constants import DOCUMENT_TYPE_KEY
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.schema.id_utils import generate_scoped_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import read_jsonl, write_jsonl

logger = logging.getLogger("krw_ontology")

_TYPE_KEY_MAP = {
    "RiskFactor": "risk",
    "GrowthDriver": "growth_driver",
    "Headwind": "headwind",
}

_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("supply_chain", ("supplier", "component", "manufacturing", "assembly", "inventory", "shortage", "rare earth")),
    ("competitive", ("competitive", "competition", "competitor", "pricing pressure", "price competition", "market share")),
    ("regulatory", ("regulation", "regulatory", "compliance", "antitrust", "privacy", "government", "trade restriction")),
    ("legal", ("legal", "litigation", "lawsuit", "intellectual property", "license", "claim")),
    ("cybersecurity", ("cybersecurity", "security", "vulnerability", "exploit", "data breach")),
    ("technology", ("technology", "innovation", "artificial intelligence", "software", "platform", "product introduction")),
    ("macroeconomic", ("foreign exchange", "currency", "macroeconomic", "inflation", "interest rate", "economic")),
    ("operational", ("quality", "defect", "warranty", "reputation", "operations", "retail", "personnel")),
    ("financial", ("revenue", "margin", "cash", "debt", "tax", "profit", "expense", "cost")),
)

_GROWTH_KEYWORDS = (
    "growth", "increase", "increased", "higher", "expanded", "momentum",
    "innovation", "new products", "services", "demand", "competitive advantage",
)

_HEADWIND_KEYWORDS = (
    "pressure", "downward", "cost", "shortage", "constrained", "decline",
    "decrease", "volatility", "competition", "tariff", "foreign exchange",
)

_RISK_KEYWORDS = (
    "risk", "adversely", "materially", "could", "may", "uncertain",
    "litigation", "regulatory", "cybersecurity", "defect", "disruption",
    "depend", "single-source", "shortage",
)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80].rstrip("-")


async def extract_risks_drivers_headwinds(
    worker: ExtractionWorker,
    ontology_dir: Path,
    ticker: str,
    period: str,
    doc_type: str,
    claims: list[dict] | None = None,
    quotes: list[dict] | None = None,
) -> dict[str, list[dict]]:
    """Build research themes from accepted claims.

    Research claims are already the AI-extracted evidence layer. This stage is
    deterministic: it groups those claims into reviewable themes so e2e runs do
    not depend on dozens of additional SDK calls.
    """
    del worker, quotes
    stage_name = "extract_risks_drivers_headwinds"
    doc_type_key = DOCUMENT_TYPE_KEY
    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"

    claims_path = ontology_dir / "claims.jsonl"
    risks_path = ontology_dir / "risks.jsonl"
    growth_path = ontology_dir / "growth_drivers.jsonl"
    headwinds_path = ontology_dir / "headwinds.jsonl"

    if claims is None:
        claims = read_jsonl(claims_path)

    if not claims:
        logger.warning(f"{stage_name}: no claims found", extra={"stage": stage_name})
        write_jsonl(risks_path, [])
        write_jsonl(growth_path, [])
        write_jsonl(headwinds_path, [])
        return {"risks": [], "growth_drivers": [], "headwinds": []}

    raw_items = [_theme_from_group(group) for group in _group_claims(claims)]

    risks: list[dict] = []
    growth_drivers: list[dict] = []
    headwinds: list[dict] = []
    by_key: dict[tuple[str, str], dict] = {}
    for item in raw_items:
        obj_type = item.get("type", "")
        type_key = _TYPE_KEY_MAP.get(obj_type)
        if not type_key:
            logger.warning(f"{stage_name}: unknown type '{obj_type}', skipping", extra={"stage": stage_name})
            continue

        slug = _slugify(item.get("name", f"{type_key}-{len(risks) + len(growth_drivers) + len(headwinds)}"))
        key = (obj_type, slug)
        if key in by_key:
            _merge_research_object(by_key[key], item)
            continue

        obj_id = generate_scoped_id(type_key, ticker, period, doc_type_key, slug)

        obj = {
            "id": obj_id,
            "type": obj_type,
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": doc_type,
            "period": period,
            "name": item["name"],
            "category": item["category"],
            "description": item["description"],
            "qualitative_impact": item["qualitative_impact"],
            "confidence": item["confidence"],
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        }
        for optional_key in ("supported_by_claims", "supported_by_quotes", "affects",
                             "unmapped_impacts", "unmapped_metrics"):
            if item.get(optional_key):
                obj[optional_key] = _dedupe_list(item[optional_key])

        by_key[key] = obj
        if obj_type == "RiskFactor":
            risks.append(obj)
        elif obj_type == "GrowthDriver":
            growth_drivers.append(obj)
        elif obj_type == "Headwind":
            headwinds.append(obj)

    write_jsonl(risks_path, risks)
    write_jsonl(growth_path, growth_drivers)
    write_jsonl(headwinds_path, headwinds)

    logger.info(
        f"{stage_name}: extracted {len(risks)} risks, {len(growth_drivers)} drivers, {len(headwinds)} headwinds",
        extra={"stage": stage_name},
    )
    return {"risks": risks, "growth_drivers": growth_drivers, "headwinds": headwinds}


def _group_claims(claims: list[dict]) -> list[list[dict]]:
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for claim in claims:
        obj_type = _classify_object_type(claim)
        if not obj_type:
            continue
        category = _classify_category(claim)
        metrics = claim.get("related_metrics") or []
        metric_key = metrics[0] if metrics else "qualitative"
        groups.setdefault((obj_type, category, metric_key), []).append(claim)
    return list(groups.values())


def _classify_object_type(claim: dict) -> str | None:
    claim_type = (claim.get("claim_type") or "").lower()
    text = (claim.get("claim_text") or "").lower()
    if claim_type == "risk_assessment":
        if any(keyword in text for keyword in _HEADWIND_KEYWORDS) and not any(
            keyword in text for keyword in ("materially adversely", "litigation", "cybersecurity", "defect")
        ):
            return "Headwind"
        return "RiskFactor"
    if claim_type in {"strategic", "forward_looking"}:
        if any(keyword in text for keyword in _GROWTH_KEYWORDS):
            return "GrowthDriver"
        if any(keyword in text for keyword in _RISK_KEYWORDS):
            return "RiskFactor"
    if claim_type == "factual":
        if any(keyword in text for keyword in _GROWTH_KEYWORDS):
            return "GrowthDriver"
        if any(keyword in text for keyword in _HEADWIND_KEYWORDS):
            return "Headwind"
    return None


def _classify_category(claim: dict) -> str:
    text = (claim.get("claim_text") or "").lower()
    for category, keywords in _CATEGORY_RULES:
        if any(keyword in text for keyword in keywords):
            return category
    return "operational"


def _theme_from_group(group: list[dict]) -> dict:
    first = group[0]
    obj_type = _classify_object_type(first) or "Headwind"
    category = _classify_category(first)
    metrics = _dedupe_list([
        metric
        for claim in group
        for metric in claim.get("related_metrics", []) or []
    ])
    claim_ids = [claim["id"] for claim in group]
    quote_ids = _dedupe_list([
        quote_id
        for claim in group
        for quote_id in claim.get("supported_by_quotes", []) or []
    ])
    metric_phrase = metrics[0] if metrics else "business performance"
    category_label = category.replace("_", " ")

    return {
        "type": obj_type,
        "name": _theme_name(obj_type, category, metrics),
        "category": category,
        "description": (
            f"Evidence-backed claims describe {category_label} themes "
            f"relevant to {metric_phrase}."
        ),
        "supported_by_claims": claim_ids,
        "supported_by_quotes": quote_ids,
        "affects": metrics,
        "qualitative_impact": _qualitative_impact(obj_type, len(group)),
        "confidence": _group_confidence(group),
    }


def _theme_name(obj_type: str, category: str, metrics: list[str]) -> str:
    category_label = category.replace("_", " ").title()
    metric_label = metrics[0].replace("_", " ") if metrics else ""
    if obj_type == "GrowthDriver":
        suffix = f" for {metric_label}" if metric_label else ""
        return f"{category_label} growth driver{suffix}"
    if obj_type == "RiskFactor":
        suffix = f" affecting {metric_label}" if metric_label else ""
        return f"{category_label} risk{suffix}"
    suffix = f" on {metric_label}" if metric_label else ""
    return f"{category_label} headwind{suffix}"


def _qualitative_impact(obj_type: str, group_size: int) -> str:
    if obj_type == "GrowthDriver":
        return "medium_positive" if group_size >= 3 else "low_positive"
    if obj_type == "RiskFactor":
        return "high_negative" if group_size >= 5 else "medium_negative"
    return "medium_negative" if group_size >= 3 else "low_negative"


def _group_confidence(group: list[dict]) -> str:
    confidences = {claim.get("confidence") for claim in group}
    if confidences == {"high"}:
        return "high"
    if "high" in confidences or "medium" in confidences:
        return "medium"
    return "low"


def _dedupe_list(values: list | None) -> list:
    seen = set()
    result = []
    for value in values or []:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _merge_research_object(existing: dict, item: dict) -> None:
    for key in ("supported_by_claims", "supported_by_quotes", "affects", "unmapped_impacts", "unmapped_metrics"):
        merged = [*existing.get(key, []), *item.get(key, [])]
        if merged:
            existing[key] = _dedupe_list(merged)
    if existing.get("confidence") != "high" and item.get("confidence") == "high":
        existing["confidence"] = "high"
