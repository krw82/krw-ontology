"""AI stage: Extract research claims from spans + quotes."""

from __future__ import annotations

import json
import logging
import hashlib
from pathlib import Path

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.errors import PipelineStageError, RateLimitError
from krw_ontology.extraction.prompts.claim_extraction import (
    CLAIM_EXTRACTION_PROMPT,
    CLAIM_TYPES,
)
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.factor_taxonomy import canonical_factor_key, format_factor_taxonomy_for_prompt
from krw_ontology.pipeline.ai_batches import (
    batch_cache_path,
    clear_stage_batch_cache,
    run_limited_batches,
    write_batch_cache,
)
from krw_ontology.pipeline.reference_aliases import alias_objects, resolve_references
from krw_ontology.schema.id_utils import generate_scoped_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import find_project_root, read_jsonl, write_jsonl
from krw_ontology.validators.metric_validator import _load_metric_dictionary

logger = logging.getLogger("krw_ontology")

BATCH_SIZE = 8
CACHE_VERSION = 5

_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "claim_text": {"type": "string"},
        "claim_type": {"type": "string"},
        "supported_by_quotes": {"type": "array", "items": {"type": "string"}},
        "related_metrics": {"type": "array", "items": {"type": "string"}},
        "object_type_hints": {"type": "array", "items": {"type": "string"}},
        "theme_hint": {"type": "string"},
        "factor_hint": {"type": "string"},
        "activity_hint": {"type": "string"},
        "benchmark_hint": {"type": "string"},
        "impact_channels": {"type": "array", "items": {"type": "string"}},
        "effect_direction": {"type": "string"},
        "materiality_hint": {"type": "string"},
        "time_horizon": {"type": "string"},
        "sector_hint": {"type": "string"},
        "confidence": {"type": "string"},
    },
    "required": ["id", "claim_text", "claim_type", "supported_by_quotes", "confidence"],
}

_CLAIM_HINT_LIST_FIELDS = ("object_type_hints", "impact_channels")
_CLAIM_HINT_STRING_FIELDS = (
    "theme_hint",
    "factor_hint",
    "activity_hint",
    "benchmark_hint",
    "effect_direction",
    "materiality_hint",
    "time_horizon",
    "sector_hint",
)
_OBJECT_TYPE_HINTS = {
    "riskfactor": "BusinessFactor",
    "risk": "BusinessFactor",
    "growthdriver": "BusinessFactor",
    "growth_driver": "BusinessFactor",
    "driver": "BusinessFactor",
    "headwind": "BusinessFactor",
    "businessactivity": "BusinessActivity",
    "business_activity": "BusinessActivity",
    "activity": "BusinessActivity",
    "externalfactorexposure": "ExternalFactorExposure",
    "external_factor_exposure": "ExternalFactorExposure",
    "exposure": "ExternalFactorExposure",
    "changeevent": "ChangeEvent",
    "change_event": "ChangeEvent",
    "event": "ChangeEvent",
}
_EFFECT_DIRECTIONS = {"positive", "negative", "mixed", "uncertain"}
_MATERIALITY_HINTS = {"high", "medium", "low", "unknown"}
_TIME_HORIZONS = {"short", "medium", "long", "ongoing", "unknown"}


def _slugify(text: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80].rstrip("-")


async def extract_research_claims(
    worker: ExtractionWorker,
    ontology_dir: Path,
    ticker: str,
    period: str,
    doc_type: str,
    quotes: list[dict] | None = None,
    concurrency: int = 1,
    force: bool = False,
    batch_size: int = BATCH_SIZE,
) -> list[dict]:
    """Extract research claims from evidence quotes in batches with split retry."""
    stage_name = "extract_research_claims"
    batch_size = max(1, int(batch_size))
    doc_type_key = normalize_doc_type(doc_type)
    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"

    spans_path = ontology_dir / "spans.jsonl"
    quotes_path = ontology_dir / "evidence_quotes.jsonl"
    output_path = ontology_dir / "claims.jsonl"
    failures_path = ontology_dir / "batch_failures.jsonl"

    spans = read_jsonl(spans_path)
    if quotes is None:
        quotes = read_jsonl(quotes_path)

    quotes = [
        quote for quote in quotes
        if quote.get("id") and quote.get("quote_text")
    ]

    if not quotes:
        logger.warning(f"{stage_name}: no evidence quotes found", extra={"stage": stage_name})
        write_jsonl(output_path, [])
        return []

    all_claims: list[dict] = []
    total_batches = (len(quotes) + batch_size - 1) // batch_size
    failed_quote_count = 0
    metrics_list = _load_metrics_list(ontology_dir)
    factor_taxonomy_list = format_factor_taxonomy_for_prompt()
    _clear_stage_failures(failures_path, stage_name)
    if force:
        clear_stage_batch_cache(ontology_dir, stage_name)

    spans_by_id = {span["id"]: span for span in spans if span.get("id")}

    async def run_batch(batch_idx: int) -> tuple[list[dict], int, list[dict]]:
        start = batch_idx * batch_size
        batch_quotes = quotes[start : start + batch_size]
        input_hash = _claim_batch_input_hash(batch_quotes, batch_size)
        cached = _read_quote_first_batch_cache(ontology_dir, stage_name, batch_idx, input_hash)
        if cached is not None:
            logger.info(
                "%s batch %s/%s loaded from cache (%s claims)",
                stage_name,
                batch_idx + 1,
                total_batches,
                len(cached),
                extra={"stage": stage_name},
            )
            return cached, 0, []
        raw_items, failed_quotes = await _extract_batch_with_split_retry(
            worker=worker,
            batch_quotes=batch_quotes,
            spans_by_id=spans_by_id,
            metrics_list=metrics_list,
            factor_taxonomy_list=factor_taxonomy_list,
            batch_index=batch_idx,
            failures_path=failures_path,
            ticker=ticker,
            doc_type=doc_type,
            doc_type_key=doc_type_key,
            period=period,
            source_document_id=source_document_id,
            stage_name=stage_name,
        )
        batch_claims, rejected = _materialize_claim_batch(
            raw_items=raw_items,
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
            batch_claims,
            metadata={
                "status": "ok",
                "input_mode": "evidence_quotes",
                "cache_version": CACHE_VERSION,
                "input_hash": input_hash,
                "batch_size": batch_size,
                "quote_count": len(batch_quotes),
                "failed_quotes": failed_quotes,
            },
        )
        return batch_claims, failed_quotes, rejected

    def log_complete(batch_idx: int, result: tuple[list[dict], int, list[dict]]) -> None:
        logger.info(
            "%s batch %s/%s complete: %s claims, %s failed quotes, %s rejected refs",
            stage_name,
            batch_idx + 1,
            total_batches,
            len(result[0]),
            result[1],
            len(result[2]),
            extra={"stage": stage_name},
        )

    results = await run_limited_batches(
        batch_indices=list(range(total_batches)),
        concurrency=concurrency,
        run_one=run_batch,
        on_complete=log_complete,
    )

    seen_claim_ids: set[str] = set()
    rejected_items: list[dict] = []
    for _batch_idx, (batch_claims, failed_quotes, rejected) in sorted(results, key=lambda row: row[0]):
        failed_quote_count += failed_quotes
        rejected_items.extend(rejected)
        for claim in batch_claims:
            claim_id = claim["id"]
            if claim_id in seen_claim_ids:
                continue
            seen_claim_ids.add(claim_id)
            all_claims.append(claim)

    if quotes and failed_quote_count / len(quotes) > 0.5:
        raise PipelineStageError(
            f"{stage_name}: {failed_quote_count}/{len(quotes)} quotes failed after split retry (>50%)"
        )

    write_jsonl(output_path, all_claims)
    if rejected_items:
        _append_rejected_objects(ontology_dir, rejected_items)
    logger.info(
        f"{stage_name}: extracted {len(all_claims)} claims",
        extra={"stage": stage_name},
    )
    return all_claims


def _materialize_claim_batch(
    *,
    raw_items: list[dict],
    ticker: str,
    period: str,
    doc_type: str,
    doc_type_key: str,
    source_document_id: str,
) -> tuple[list[dict], list[dict]]:
    claims: list[dict] = []
    rejected: list[dict] = []
    for item in raw_items:
        claim_slug = _slugify(item.get("claim_text", f"claim-{len(claims)}"))
        claim_obj = {
            "id": generate_scoped_id("claim", ticker, period, doc_type_key, claim_slug),
            "type": "ResearchClaim",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": doc_type,
            "period": period,
            "claim_text": item["claim_text"],
            "claim_type": item["claim_type"],
            "supported_by_quotes": item["supported_by_quotes"],
            "confidence": item["confidence"],
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        }
        if item.get("_rejection_reason"):
            rejected.append({
                **claim_obj,
                "rejection_reason": item["_rejection_reason"],
                "rejection_stage": "reference_alias_resolution",
            })
            continue
        if item.get("related_metrics"):
            claim_obj["related_metrics"] = _list_strings(item["related_metrics"])
        _attach_semantic_hints(claim_obj, item)
        claims.append(claim_obj)
    return claims, rejected


async def _extract_batch_with_split_retry(
    *,
    worker: ExtractionWorker,
    batch_quotes: list[dict],
    spans_by_id: dict[str, dict],
    metrics_list: str,
    factor_taxonomy_list: str,
    batch_index: int,
    failures_path: Path,
    ticker: str,
    doc_type: str,
    doc_type_key: str,
    period: str,
    source_document_id: str,
    stage_name: str,
) -> tuple[list[dict], int]:
    if not batch_quotes:
        return [], 0
    try:
        return await _extract_quote_batch(
            worker=worker,
            batch_quotes=batch_quotes,
            spans_by_id=spans_by_id,
            metrics_list=metrics_list,
            factor_taxonomy_list=factor_taxonomy_list,
            stage_name=stage_name,
        ), 0
    except RateLimitError as e:
        span_ids = _quote_source_span_ids(batch_quotes)
        logger.warning(
            "%s batch %s rate limited after same-batch retries; not splitting: %s",
            stage_name,
            batch_index,
            e,
            extra={"stage": stage_name, "rate_limited": True},
        )
        _record_batch_failure(
            failures_path, ticker, doc_type, period, source_document_id,
            doc_type_key, stage_name, batch_index, span_ids, str(e),
            error_type="RateLimitError",
        )
        return [], len(batch_quotes)
    except Exception as e:
        if len(batch_quotes) <= 1:
            span_ids = _quote_source_span_ids(batch_quotes)
            logger.error(
                f"{stage_name} leaf batch {batch_index} failed: {e}",
                extra={"stage": stage_name},
            )
            _record_batch_failure(
                failures_path, ticker, doc_type, period, source_document_id,
                doc_type_key, stage_name, batch_index, span_ids, str(e),
            )
            return [], len(batch_quotes)

        midpoint = max(1, len(batch_quotes) // 2)
        logger.warning(
            "%s batch %s failed; retrying as %s and %s quote sub-batches: %s",
            stage_name,
            batch_index,
            midpoint,
            len(batch_quotes) - midpoint,
            e,
            extra={"stage": stage_name},
        )
        left_items, left_failed = await _extract_batch_with_split_retry(
            worker=worker,
            batch_quotes=batch_quotes[:midpoint],
            spans_by_id=spans_by_id,
            metrics_list=metrics_list,
            factor_taxonomy_list=factor_taxonomy_list,
            batch_index=batch_index * 10 + 1,
            failures_path=failures_path,
            ticker=ticker,
            doc_type=doc_type,
            doc_type_key=doc_type_key,
            period=period,
            source_document_id=source_document_id,
            stage_name=stage_name,
        )
        right_items, right_failed = await _extract_batch_with_split_retry(
            worker=worker,
            batch_quotes=batch_quotes[midpoint:],
            spans_by_id=spans_by_id,
            metrics_list=metrics_list,
            factor_taxonomy_list=factor_taxonomy_list,
            batch_index=batch_index * 10 + 2,
            failures_path=failures_path,
            ticker=ticker,
            doc_type=doc_type,
            doc_type_key=doc_type_key,
            period=period,
            source_document_id=source_document_id,
            stage_name=stage_name,
        )
        return [*left_items, *right_items], left_failed + right_failed


async def _extract_quote_batch(
    *,
    worker: ExtractionWorker,
    batch_quotes: list[dict],
    spans_by_id: dict[str, dict],
    metrics_list: str,
    factor_taxonomy_list: str,
    stage_name: str,
) -> list[dict]:
    source_context = _source_context_for_quotes(batch_quotes, spans_by_id)
    aliased_quotes, quote_alias_to_id = alias_objects(batch_quotes, "q")

    input_data = {
        "spans_json": json.dumps(
            source_context,
            ensure_ascii=False,
        ),
        "quotes_json": json.dumps(
            [
                {
                    "id": q["id"],
                    "quote_text": q["quote_text"],
                    "quote_type": q.get("quote_type", ""),
                }
                for q in batch_quotes
            ],
            ensure_ascii=False,
        ),
        "claim_types": ", ".join(CLAIM_TYPES),
        "metrics_list": metrics_list,
        "factor_taxonomy_list": factor_taxonomy_list,
    }
    input_data["quotes_json"] = json.dumps(
        [
            {
                "id": q["id"],
                "source_span_id": q.get("source_span_id", ""),
                "section_name": q.get("section_name", ""),
                "quote_text": q["quote_text"],
                "quote_type": q.get("quote_type", ""),
            }
            for q in aliased_quotes
        ],
        ensure_ascii=False,
    )
    items = await worker.extract(CLAIM_EXTRACTION_PROMPT, input_data, _SCHEMA, stage_name)
    for item in items:
        resolved, unknown = resolve_references(item.get("supported_by_quotes"), quote_alias_to_id)
        item["supported_by_quotes"] = resolved
        if unknown or not resolved:
            item["_rejection_reason"] = (
                "Unknown quote aliases in supported_by_quotes: "
                f"{unknown or item.get('supported_by_quotes') or []}"
            )
    return items


def _quote_source_span_ids(quotes: list[dict]) -> list[str]:
    seen: set[str] = set()
    span_ids: list[str] = []
    for quote in quotes:
        source_span_id = quote.get("source_span_id")
        if source_span_id and source_span_id not in seen:
            seen.add(source_span_id)
            span_ids.append(source_span_id)
    return span_ids


def _source_context_for_quotes(quotes: list[dict], spans_by_id: dict[str, dict]) -> list[dict]:
    context: list[dict] = []
    seen: set[str] = set()
    for quote in quotes:
        source_span_id = quote.get("source_span_id")
        if not source_span_id or source_span_id in seen:
            continue
        seen.add(source_span_id)
        span = spans_by_id.get(source_span_id, {})
        context.append({
            "source_span_id": source_span_id,
            "section_name": span.get("section_name") or quote.get("section_name", ""),
            "note": "non-citable context; use quote aliases only in supported_by_quotes",
        })
    return context


def _claim_batch_input_hash(batch_quotes: list[dict], batch_size: int) -> str:
    payload = [
        {
            "id": quote.get("id", ""),
            "source_span_id": quote.get("source_span_id", ""),
            "quote_text": quote.get("quote_text", ""),
        }
        for quote in batch_quotes
    ]
    raw = json.dumps(
        {"cache_version": CACHE_VERSION, "batch_size": batch_size, "quotes": payload},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _read_quote_first_batch_cache(
    ontology_dir: Path, stage_name: str, batch_index: int, input_hash: str
) -> list[dict] | None:
    path = batch_cache_path(ontology_dir, stage_name, batch_index)
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata") or {}
    if metadata.get("input_mode") != "evidence_quotes" or metadata.get("cache_version") != CACHE_VERSION:
        return None
    if metadata.get("input_hash") != input_hash:
        return None
    items = data.get("items")
    if isinstance(items, list):
        return items
    return None


def _attach_semantic_hints(claim_obj: dict, item: dict) -> None:
    object_type_hints = _normalize_object_type_hints(item.get("object_type_hints"))
    if object_type_hints:
        claim_obj["object_type_hints"] = object_type_hints
    impact_channels = _list_strings(item.get("impact_channels"))
    if impact_channels:
        claim_obj["impact_channels"] = impact_channels
    for field in _CLAIM_HINT_STRING_FIELDS:
        value = _optional_string(item.get(field))
        if not value:
            continue
        if field == "effect_direction":
            value = value.lower()
            if value not in _EFFECT_DIRECTIONS:
                value = "uncertain"
        elif field == "materiality_hint":
            value = value.lower()
            if value not in _MATERIALITY_HINTS:
                value = "unknown"
        elif field == "time_horizon":
            value = value.lower()
            if value not in _TIME_HORIZONS:
                value = "unknown"
        elif field == "factor_hint":
            value = canonical_factor_key(value)
        claim_obj[field] = value


def _normalize_object_type_hints(value) -> list[str]:
    hints = []
    for raw in _list_strings(value):
        key = raw.replace("-", "_").replace(" ", "_").lower()
        key = key.replace("_", "")
        canonical = _OBJECT_TYPE_HINTS.get(key) or _OBJECT_TYPE_HINTS.get(
            raw.replace("-", "_").replace(" ", "_").lower()
        )
        if canonical and canonical not in hints:
            hints.append(canonical)
    return hints


def _optional_string(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _list_strings(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []
    result = []
    seen = set()
    for item in values:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _load_metrics_list(ontology_dir: Path) -> str:
    """Load canonical metric names from the YAML schema."""
    try:
        project_root = find_project_root(ontology_dir)
        metric_path = project_root / "ontology" / "schema" / "metric_dictionary.yaml"
        data = _load_metric_dictionary(metric_path)
        metrics = list(data.get("canonical_metrics", {}).keys())
        if metrics:
            return ", ".join(metrics)
    except Exception:
        pass
    return "revenue, revenue_growth, gross_margin, operating_margin, net_income, eps, operating_cash_flow, free_cash_flow"


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
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    with open(failures_path, "a") as f:
        f.write(json.dumps(failure, ensure_ascii=False) + "\n")


def _clear_stage_failures(failures_path: Path, stage_name: str) -> None:
    if not failures_path.exists():
        return
    failures = [row for row in read_jsonl(failures_path) if row.get("stage") != stage_name]
    write_jsonl(failures_path, failures)


def _append_rejected_objects(ontology_dir: Path, rejected: list[dict]) -> None:
    if not rejected:
        return
    rejected_path = ontology_dir / "rejected_objects.jsonl"
    write_jsonl(rejected_path, [*read_jsonl(rejected_path), *rejected])
