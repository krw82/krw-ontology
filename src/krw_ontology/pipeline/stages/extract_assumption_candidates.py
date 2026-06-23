"""AI stage: Extract modeling cues from full document.

The canonical schema type remains AssumptionCandidate for v1 compatibility, but
these objects represent reviewable modeling cues, not final investment
assumptions, recommendations, forecasts, or target prices.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.pipeline.ai_batches import (
    clear_stage_batch_cache,
    read_batch_cache,
    run_limited_batches,
    transient_provider_failure_metadata,
    write_batch_cache,
)
from krw_ontology.pipeline.reference_aliases import (
    alias_objects,
    canonical_to_alias,
    resolve_references,
)
from krw_ontology.schema.id_utils import generate_scoped_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import find_project_root, read_jsonl, write_jsonl
from krw_ontology.validators.metric_validator import _load_metric_dictionary

logger = logging.getLogger("krw_ontology")

ASSUMPTION_TYPES = ("growth_rate", "margin", "capex", "tax_rate", "wacc", "other")
BATCH_SIZE = 8

_CUE_KEYWORDS = (
    "assumption", "future", "expected", "expect", "believe", "anticipate",
    "forecast", "growth", "increase", "decrease", "decline", "margin",
    "gross margin", "operating margin", "capex", "capital expenditure",
    "tax", "effective tax rate", "wacc", "discount rate", "interest rate",
    "volatility", "downward pressure", "subject to", "within 12 months",
)

_CUE_METRICS = {
    "revenue", "revenue_growth", "segment_revenue", "gross_margin",
    "gross_profit", "operating_margin", "operating_income", "net_income",
    "net_margin", "eps", "cost_of_revenue", "operating_expense",
    "research_and_development", "selling_general_and_admin",
    "operating_cash_flow", "capital_expenditures", "free_cash_flow",
    "fcf_margin", "cash_and_equivalents", "total_debt", "roe", "roa",
}

ASSUMPTION_EXTRACTION_SYSTEM = """You are a financial analyst extracting modeling cues from SEC filings.

An AssumptionCandidate is a reviewable modeling cue, not a final assumption, investment opinion, target price, or forecast.
It highlights evidence that a human or downstream AI may want to inspect when building a model:
- Growth rate assumptions (revenue growth, unit growth)
- Margin assumptions (gross margin, operating margin targets)
- Capex assumptions (capital expenditure plans)
- Tax rate assumptions
- WACC/discount rate assumptions
- Other financial or operational assumptions

IMPORTANT RULES:
- Each modeling cue must be supported by at least one claim OR one quote
- assumption_type must be one of: {assumption_types}
- related_metrics should reference canonical metric names
- value_hint may capture numeric or qualitative values only when they appear verbatim in a supporting claim or quote
- Do NOT invent assumptions not grounded in the document
- Do NOT calculate new numbers, totals, margins, growth rates, ratios, percentages, or differences
- Do NOT add derived values such as "8.5 percentage points", "-$5,984M", "~38.7%", or "29.4%" unless that exact value appears in a supported claim or quote
- The provided claim IDs are short batch-local aliases such as c1, c2; supported_by_claims must use only those aliases
- The provided quote IDs are short batch-local aliases such as q1, q2; supported_by_quotes must use only those aliases
- Do NOT make buy/sell recommendations, target prices, or forecasts
- Treat every output as needs_review

Output format: exactly one JSON object:
{{"items": [{{"id": "...", "name": "...", "assumption_text": "...", "assumption_type": "...", "value_hint": "...", "supported_by_claims": [...], "supported_by_quotes": [...], "related_metrics": [...], "unmapped_metrics": [...], "confidence": "..."}}]}}
"""

ASSUMPTION_EXTRACTION_USER = """## Claim Batch

{claims_json}

## Supporting Quotes For This Batch

{quotes_json}

## Assumption Types

{assumption_types}

## Canonical Metrics

{metrics_list}

## Task

Analyze the claim batch and supporting quotes above. Extract reviewable modeling cues that could support later valuation or forecast work.
- Identify only cues supported by this batch
- Map related_metrics to canonical metric names where possible
- Put unmapped metric references in unmapped_metrics
- Each modeling cue must reference at least one supporting claim or quote using only the short aliases
- Preserve source numbers exactly; do not compute new derived numbers

Return exactly {{"items": [...]}} with assumption objects."""

ASSUMPTION_EXTRACTION_PROMPT = ASSUMPTION_EXTRACTION_SYSTEM + "\n\n" + ASSUMPTION_EXTRACTION_USER

_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "assumption_text": {"type": "string"},
        "assumption_type": {"type": "string"},
        "value_hint": {"type": "string"},
        "supported_by_claims": {"type": "array"},
        "supported_by_quotes": {"type": "array"},
        "related_metrics": {"type": "array"},
        "unmapped_metrics": {"type": "array"},
        "confidence": {"type": "string"},
    },
    "required": ["id", "name", "assumption_text", "assumption_type", "confidence"],
}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80].rstrip("-")


async def extract_assumption_candidates(
    worker: ExtractionWorker,
    ontology_dir: Path,
    ticker: str,
    period: str,
    doc_type: str,
    claims: list[dict] | None = None,
    quotes: list[dict] | None = None,
    concurrency: int = 1,
    force: bool = False,
) -> list[dict]:
    """Extract modeling cues from filtered claim batches."""
    stage_name = "extract_assumption_candidates"
    doc_type_key = normalize_doc_type(doc_type)
    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"

    claims_path = ontology_dir / "claims.jsonl"
    quotes_path = ontology_dir / "evidence_quotes.jsonl"
    output_path = ontology_dir / "assumption_candidates.jsonl"
    failures_path = ontology_dir / "batch_failures.jsonl"

    if claims is None:
        claims = read_jsonl(claims_path)
    if quotes is None:
        quotes = read_jsonl(quotes_path)

    if not claims:
        logger.warning(f"{stage_name}: no claims found", extra={"stage": stage_name})
        write_jsonl(output_path, [])
        return []

    metrics_list = _load_metrics_list(ontology_dir)
    quote_lookup = {q.get("id"): q for q in quotes}
    cue_claims = _filter_modeling_cue_claims(claims)

    if not cue_claims:
        logger.info(f"{stage_name}: no modeling cue candidate claims found", extra={"stage": stage_name})
        write_jsonl(output_path, [])
        return []

    _clear_stage_failures(failures_path, stage_name)
    if force:
        clear_stage_batch_cache(ontology_dir, stage_name)
    raw_items: list[dict] = []
    rejected_items: list[dict] = []
    total_batches = (len(cue_claims) + BATCH_SIZE - 1) // BATCH_SIZE
    failed_batches = 0

    async def run_batch(batch_idx: int) -> tuple[list[dict], int, list[dict]]:
        batch_claims = cue_claims[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
        cached = read_batch_cache(ontology_dir, stage_name, batch_idx)
        if cached is not None:
            logger.info(
                "%s batch %s/%s loaded from cache (%s modeling cues)",
                stage_name,
                batch_idx + 1,
                total_batches,
                len(cached),
                extra={"stage": stage_name},
            )
            return cached, 0, []

        batch_quote_ids = _dedupe_list([
            quote_id
            for claim in batch_claims
            for quote_id in claim.get("supported_by_quotes", []) or []
            if quote_id in quote_lookup
        ])
        batch_quotes = [quote_lookup[quote_id] for quote_id in batch_quote_ids]
        aliased_claims, claim_alias_to_id = alias_objects(batch_claims, "c")
        aliased_quotes, quote_alias_to_id = alias_objects(batch_quotes, "q")
        quote_id_to_alias = canonical_to_alias(quote_alias_to_id)
        input_data = {
            "claims_json": json.dumps(
                [
                    {
                        "id": c["id"],
                        "claim_text": c["claim_text"],
                        "claim_type": c.get("claim_type", ""),
                        "related_metrics": c.get("related_metrics", []),
                        "supported_by_quotes": [
                            quote_id_to_alias[quote_id]
                            for quote_id in c.get("supported_by_quotes", []) or []
                            if quote_id in quote_id_to_alias
                        ],
                    }
                    for c in aliased_claims
                ],
                ensure_ascii=False,
            ),
            "quotes_json": json.dumps(
                [
                    {
                        "id": q["id"],
                        "quote_text": q["quote_text"],
                        "quote_type": q.get("quote_type", ""),
                    }
                    for q in aliased_quotes
                ],
                ensure_ascii=False,
            ),
            "assumption_types": ", ".join(ASSUMPTION_TYPES),
            "metrics_list": metrics_list,
        }

        try:
            items = await worker.extract(
                ASSUMPTION_EXTRACTION_PROMPT, input_data, _SCHEMA, stage_name
            )
            items = _resolve_assumption_references(
                items,
                claim_alias_to_id=claim_alias_to_id,
                quote_alias_to_id=quote_alias_to_id,
            )
            materialized_preview, rejected_preview = _materialize_assumption_items(
                raw_items=items,
                ticker=ticker,
                period=period,
                doc_type=doc_type,
                doc_type_key=doc_type_key,
                source_document_id=source_document_id,
            )
            write_batch_cache(
                ontology_dir,
                stage_name,
                batch_idx,
                materialized_preview,
                metadata={
                    "status": "ok",
                    "claim_count": len(batch_claims),
                    "quote_count": len(batch_quotes),
                },
            )
            return materialized_preview, 0, rejected_preview
        except Exception as e:
            _record_batch_failure(
                failures_path, ticker, doc_type, period, source_document_id,
                doc_type_key, stage_name, batch_idx, [c["id"] for c in batch_claims], str(e),
                error_type=type(e).__name__,
            )
            logger.error(
                "%s batch %s failed; continuing: %s",
                stage_name,
                batch_idx,
                e,
                extra={"stage": stage_name},
            )
            return [], 1, []

    def log_complete(batch_idx: int, result: tuple[list[dict], int, list[dict]]) -> None:
        status = "failed" if result[1] else "complete"
        logger.info(
            "%s batch %s/%s %s: %s modeling cues, %s rejected refs",
            stage_name,
            batch_idx + 1,
            total_batches,
            status,
            len(result[0]),
            len(result[2]),
            extra={"stage": stage_name},
        )

    results = await run_limited_batches(
        batch_indices=list(range(total_batches)),
        concurrency=concurrency,
        run_one=run_batch,
        on_complete=log_complete,
    )

    for _batch_idx, (items, failed, rejected) in sorted(results, key=lambda row: row[0]):
        raw_items.extend(items)
        rejected_items.extend(rejected)
        failed_batches += failed

    if total_batches > 0 and failed_batches / total_batches > 0.5:
        logger.error(
            "%s: %s/%s batches failed; continuing with partial modeling cues",
            stage_name,
            failed_batches,
            total_batches,
            extra={"stage": stage_name},
        )

    assumptions: list[dict] = []
    by_key: dict[str, dict] = {}
    for item in raw_items:
        slug = _slugify(item.get("name", f"assumption-{len(assumptions)}"))
        if slug in by_key:
            _merge_assumption(by_key[slug], item)
            continue
        assumptions.append(item)
        by_key[slug] = item

    write_jsonl(output_path, assumptions)
    if rejected_items:
        _append_rejected_objects(ontology_dir, rejected_items)
    logger.info(
        f"{stage_name}: extracted {len(assumptions)} assumption candidates",
        extra={"stage": stage_name},
    )
    return assumptions


def _resolve_assumption_references(
    items: list[dict],
    *,
    claim_alias_to_id: dict[str, str],
    quote_alias_to_id: dict[str, str],
) -> list[dict]:
    for item in items:
        resolved_claims, unknown_claims = resolve_references(
            item.get("supported_by_claims"), claim_alias_to_id
        )
        resolved_quotes, unknown_quotes = resolve_references(
            item.get("supported_by_quotes"), quote_alias_to_id
        )
        item["supported_by_claims"] = resolved_claims
        item["supported_by_quotes"] = resolved_quotes
        reasons: list[str] = []
        if unknown_claims:
            reasons.append(f"unknown claim aliases: {unknown_claims}")
        if unknown_quotes:
            reasons.append(f"unknown quote aliases: {unknown_quotes}")
        if not resolved_claims and not resolved_quotes:
            reasons.append("missing resolved supporting claim or quote")
        if reasons:
            item["_rejection_reason"] = "; ".join(reasons)
    return items


def _materialize_assumption_items(
    *,
    raw_items: list[dict],
    ticker: str,
    period: str,
    doc_type: str,
    doc_type_key: str,
    source_document_id: str,
) -> tuple[list[dict], list[dict]]:
    assumptions: list[dict] = []
    rejected: list[dict] = []
    for item in raw_items:
        slug = _slugify(item.get("name", f"assumption-{len(assumptions)}"))
        assumption_id = generate_scoped_id("assumption", ticker, period, doc_type_key, slug)
        obj = {
            "id": assumption_id,
            "type": "AssumptionCandidate",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": doc_type,
            "period": period,
            "name": item["name"],
            "assumption_text": item["assumption_text"],
            "assumption_type": item["assumption_type"],
            "confidence": item["confidence"],
            "review_status": "needs_review",
            "schema_version": SCHEMA_VERSION,
        }
        for optional_key in (
            "value_hint", "supported_by_claims", "supported_by_quotes",
            "related_metrics", "unmapped_metrics",
        ):
            if item.get(optional_key):
                obj[optional_key] = item[optional_key]

        if item.get("_rejection_reason"):
            rejected.append({
                **obj,
                "rejection_reason": item["_rejection_reason"],
                "rejection_stage": "reference_alias_resolution",
            })
            continue
        assumptions.append(obj)
    return assumptions, rejected


def _filter_modeling_cue_claims(claims: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for claim in claims:
        claim_type = (claim.get("claim_type") or "").lower()
        text = (claim.get("claim_text") or "").lower()
        metrics = set(claim.get("related_metrics") or [])
        has_numeric = bool(re.search(r"\d", text))
        if claim_type in {"assumption", "forward_looking"}:
            selected.append(claim)
        elif metrics & _CUE_METRICS and (has_numeric or any(k in text for k in _CUE_KEYWORDS)):
            selected.append(claim)
        elif any(k in text for k in _CUE_KEYWORDS) and has_numeric:
            selected.append(claim)
    return selected


def _dedupe_list(values: list | None) -> list:
    seen = set()
    result = []
    for value in values or []:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _merge_assumption(existing: dict, item: dict) -> None:
    for key in ("supported_by_claims", "supported_by_quotes", "related_metrics", "unmapped_metrics"):
        merged = [*existing.get(key, []), *item.get(key, [])]
        if merged:
            existing[key] = _dedupe_list(merged)
    if not existing.get("value_hint") and item.get("value_hint"):
        existing["value_hint"] = item["value_hint"]
    if existing.get("confidence") != "high" and item.get("confidence") == "high":
        existing["confidence"] = "high"


def _load_metrics_list(ontology_dir: Path) -> str:
    try:
        project_root = find_project_root(ontology_dir)
        metric_path = project_root / "ontology" / "schema" / "metric_dictionary.yaml"
        data = _load_metric_dictionary(metric_path)
        metrics = list(data.get("canonical_metrics", {}).keys())
        if metrics:
            return ", ".join(metrics)
    except Exception:
        pass
    return "revenue, gross_margin, operating_margin, net_income, eps, operating_cash_flow, free_cash_flow"


def _record_batch_failure(
    failures_path: Path, ticker: str, doc_type: str, period: str,
    source_document_id: str, doc_type_key: str, stage: str, batch_index: int,
    input_span_ids: list[str], error_message: str, error_type: str = "ExtractionError",
) -> None:
    from datetime import datetime, timezone

    failure = {
        "id": f"batch_failure:{ticker}:{period}:{doc_type_key}:{stage}:{batch_index:04d}",
        "type": "BatchFailure",
        "ticker": ticker,
        "document_type": doc_type,
        "period": period,
        "source_document_id": source_document_id,
        "stage": stage,
        "batch_index": batch_index,
        "input_span_ids": input_span_ids,
        "attempts": 3,
        "error_type": error_type,
        "error_message": error_message[:500],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }
    failure.update(transient_provider_failure_metadata(error_message, error_type=error_type))
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    with open(failures_path, "a") as f:
        f.write(json.dumps(failure, ensure_ascii=False) + "\n")


def _clear_stage_failures(failures_path: Path, stage: str) -> None:
    if not failures_path.exists():
        return
    failures = [row for row in read_jsonl(failures_path) if row.get("stage") != stage]
    write_jsonl(failures_path, failures)


def _append_rejected_objects(ontology_dir: Path, rejected: list[dict]) -> None:
    if not rejected:
        return
    rejected_path = ontology_dir / "rejected_objects.jsonl"
    write_jsonl(rejected_path, [*read_jsonl(rejected_path), *rejected])
