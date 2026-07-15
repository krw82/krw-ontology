"""AI stage: Extract evidence quotes from source spans."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.errors import PipelineStageError, ProviderOverloadError, RateLimitError
from krw_ontology.extraction.prompts.quote_extraction import (
    QUOTE_EXTRACTION_PROMPT,
    QUOTE_TYPES,
    SIGNAL_TYPES,
)
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.pipeline.ai_batches import (
    batch_cache_path,
    clear_stage_batch_cache,
    is_reusable_batch_cache_metadata,
    run_limited_batches,
    transient_provider_failure_metadata,
    write_batch_cache,
)
from krw_ontology.schema.id_utils import generate_quote_local_id, generate_scoped_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import read_jsonl, write_jsonl

logger = logging.getLogger("krw_ontology")

BATCH_SIZE = 5
SPAN_PRUNING_OFF = "off"
SPAN_PRUNING_CONSERVATIVE = "conservative"
SPAN_PRUNING_PILOT = "pilot"
SPAN_PRUNING_MODES = {SPAN_PRUNING_OFF, SPAN_PRUNING_CONSERVATIVE, SPAN_PRUNING_PILOT}
PILOT_MAX_QUOTE_SPANS = 120
CACHE_VERSION = 3

_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string"},
        "quote_type": {"type": "string"},
        "section_name": {"type": "string"},
        "confidence": {"type": "string"},
        "language_signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal_text": {"type": "string"},
                    "type": {"type": "string"},
                    "signal_type": {"type": "string"},
                    "strength": {"type": "string"},
                    "direction": {"type": "string"},
                    "certainty": {"type": "string"},
                    "temporal_scope": {"type": "string"},
                    "exact_match_verified": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidate_id", "quote_type", "section_name", "confidence"],
}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _quote_dedup_key(quote_text: str, source_span_id: str) -> str:
    normalized = _normalize_text(quote_text)
    raw = f"{normalized}|{source_span_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_TARGET_SECTIONS = {
    "item1",
    "item1a",
    "item1c",
    "item7",
    "item7a",
    "item8",
    "part1_item1",
    "part1_item2",
    "part1_item3",
    "part1_item4",
    "part2_item1",
    "part2_item1a",
}
_PILOT_SECTION_PRIORITY = {
    "item1": 0,
    "part1_item1": 0,
    "item1a": 1,
    "part2_item1a": 1,
    "item7": 2,
    "item7a": 3,
    "item1c": 4,
    "item8": 5,
}
_PILOT_SECTION_QUOTAS = {
    "item1": 24,
    "part1_item1": 24,
    "item1a": 36,
    "part2_item1a": 36,
    "item7": 36,
    "item7a": 8,
    "item1c": 8,
    "item8": 8,
}
_PILOT_FALLBACK_KEYWORDS = (
    "revenue", "net sales", "gross margin", "operating margin", "data center",
    "artificial intelligence", "ai", "export control", "china", "supply",
    "supplier", "customer", "competition", "regulation", "liquidity",
    "capital expenditure", "cash flow", "risk", "adverse",
)
_QUOTE_KEYWORDS = (
    "adverse", "affect", "risk", "uncertain", "competition", "competitive",
    "regulatory", "litigation", "supply", "supplier", "manufacturing",
    "inventory", "revenue", "sales", "net sales", "margin", "gross",
    "operating", "cost", "expense", "growth", "increased", "decreased",
    "cash", "liquidity", "capital", "expected", "anticipate", "believe",
    "may", "could", "material",
)
_LOW_VALUE_EXHIBIT_SECTIONS = {"item15", "item16", "part2_item6"}
_STRUCTURAL_LABELS = {
    "table of contents",
    "part i",
    "part ii",
    "part iii",
    "part iv",
    "signatures",
    "exhibits",
    "index",
}


def _build_quote_candidates(spans: list[dict]) -> list[dict]:
    """Create exact quote candidates from SourceSpan text.

    AI selects candidate IDs; code copies candidate text into EvidenceQuote.
    This keeps quote_text canonical and exact-matchable.
    """
    candidates: list[dict] = []
    for span in spans:
        section_name = span.get("section_name", "")
        candidate_seq = 1
        for _sentence_idx, start, end, text in _iter_sentence_candidates(span.get("text", "")):
            if not _is_candidate_worth_review(text, section_name):
                continue
            candidate_id = f"{span['id']}:cand:{candidate_seq:03d}"
            candidate_seq += 1
            span_start = span.get("start_char", 0)
            candidates.append({
                "candidate_id": candidate_id,
                "source_span_id": span["id"],
                "section_name": section_name,
                "start_char": start,
                "end_char": end,
                "absolute_start_char": span_start + start,
                "absolute_end_char": span_start + end,
                "text": text,
            })
    return candidates


def _iter_sentence_candidates(text: str):
    """Yield sentence-like exact substrings with SourceSpan-relative offsets."""
    if not text:
        return
    start = 0
    sentence_idx = 1
    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        end = match.start()
        stripped_start, stripped_end, sentence = _strip_with_offsets(text, start, end)
        if sentence:
            yield sentence_idx, stripped_start, stripped_end, sentence
            sentence_idx += 1
        start = match.end()
    stripped_start, stripped_end, sentence = _strip_with_offsets(text, start, len(text))
    if sentence:
        yield sentence_idx, stripped_start, stripped_end, sentence


def _strip_with_offsets(text: str, start: int, end: int) -> tuple[int, int, str]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end, text[start:end]


def _is_candidate_worth_review(text: str, section_name: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized) < 60 or len(normalized) > 1200:
        return False
    lower = normalized.lower()
    if section_name in _TARGET_SECTIONS:
        return True
    return any(keyword in lower for keyword in _QUOTE_KEYWORDS)


def _normalize_span_pruning_mode(mode: str | None) -> str:
    normalized = (mode or SPAN_PRUNING_CONSERVATIVE).strip().lower()
    if normalized in SPAN_PRUNING_MODES:
        return normalized
    return SPAN_PRUNING_CONSERVATIVE


def _filter_spans_for_quote_extraction(
    spans: list[dict],
    *,
    span_pruning: str,
    pilot_max_quote_spans: int = PILOT_MAX_QUOTE_SPANS,
) -> tuple[list[dict], list[dict]]:
    """Return spans eligible for quote extraction and an auditable decision row per span."""
    mode = _normalize_span_pruning_mode(span_pruning)
    eligible: list[dict] = []
    audit_rows: list[dict] = []

    for span in spans:
        decision, reason, candidate_count = _classify_span_eligibility(span, mode)
        audit_rows.append({
            "span_id": span.get("id", ""),
            "section_name": span.get("section_name", ""),
            "section_key": span.get("section_key", span.get("section_name", "")),
            "span_index": span.get("span_index"),
            "char_count": span.get("char_count", len(span.get("text", ""))),
            "decision": decision,
            "reason": reason,
            "candidate_count": candidate_count,
            "span_pruning": mode,
        })
        if decision == "keep":
            eligible.append(span)

    if mode == SPAN_PRUNING_PILOT:
        selected = _select_pilot_spans(eligible, pilot_max_quote_spans)
        selected_ids = {span.get("id", "") for span in selected}
        for row in audit_rows:
            if row["decision"] != "keep":
                row["pilot_selected"] = False
                continue
            row["pilot_selected"] = row["span_id"] in selected_ids
            if row["span_id"] not in selected_ids:
                row["decision"] = "skip"
                row["reason"] = "pilot_span_cap_or_non_core_section"
        eligible = selected

    return eligible, audit_rows


def _select_pilot_spans(spans: list[dict], max_spans: int) -> list[dict]:
    """Keep a small, deterministic set of high-value spans for development runs."""
    limit = max(1, int(max_spans or PILOT_MAX_QUOTE_SPANS))

    def score(span: dict) -> tuple[int, int, int]:
        section_name = span.get("section_name", "")
        normalized = _normalize_text(span.get("text", ""))
        lowered = normalized.lower()
        section_priority = _PILOT_SECTION_PRIORITY.get(section_name, 20)
        keyword_hit = 0 if any(keyword in lowered for keyword in _PILOT_FALLBACK_KEYWORDS) else 1
        span_index = int(span.get("span_index") or 0)
        return section_priority, keyword_hit, span_index

    selected: list[dict] = []
    selected_ids: set[str] = set()
    for section_name, _priority in sorted(_PILOT_SECTION_PRIORITY.items(), key=lambda item: item[1]):
        quota = _PILOT_SECTION_QUOTAS.get(section_name, 0)
        if quota <= 0 or len(selected) >= limit:
            continue
        section_spans = [
            span for span in spans
            if span.get("section_name", "") == section_name and span.get("id", "") not in selected_ids
        ]
        for span in sorted(section_spans, key=score)[: min(quota, limit - len(selected))]:
            selected.append(span)
            selected_ids.add(span.get("id", ""))

    if len(selected) < limit:
        fallback_spans = [
            span for span in spans
            if span.get("id", "") not in selected_ids
            and (
                span.get("section_name", "") in _PILOT_SECTION_PRIORITY
                or any(keyword in _normalize_text(span.get("text", "")).lower() for keyword in _PILOT_FALLBACK_KEYWORDS)
            )
        ]
        for span in sorted(fallback_spans, key=score)[: limit - len(selected)]:
            selected.append(span)
            selected_ids.add(span.get("id", ""))

    selected_ids = {span.get("id", "") for span in selected}
    return [span for span in spans if span.get("id", "") in selected_ids]


def _classify_span_eligibility(span: dict, mode: str) -> tuple[str, str, int]:
    if mode == SPAN_PRUNING_OFF:
        return "keep", "pruning_disabled", len(_build_quote_candidates([span]))

    text = span.get("text", "")
    normalized = _normalize_text(text)
    lowered = normalized.lower()
    section_name = span.get("section_name", "")

    if not normalized:
        return "skip", "empty_span", 0
    if _is_layout_fragment(normalized):
        return "skip", "layout_fragment", 0
    if _is_toc_like_span(text):
        return "skip", "toc_like", 0
    if _is_structural_label_span(normalized):
        return "skip", "structural_label", 0
    if section_name == "cover":
        return "skip", "cover_metadata", 0
    if _is_signature_or_certification_span(lowered):
        return "skip", "signature_or_certification_boilerplate", 0
    if _is_exhibit_index_span(text, section_name):
        return "skip", "exhibit_index_or_list", 0

    candidate_count = len(_build_quote_candidates([span]))
    if candidate_count == 0:
        return "skip", "no_quote_candidates", 0
    if section_name in _TARGET_SECTIONS:
        return "keep", "core_section", candidate_count
    return "keep", "has_quote_candidates", candidate_count


def _is_layout_fragment(normalized: str) -> bool:
    alnum_count = sum(1 for ch in normalized if ch.isalnum())
    return alnum_count < 12


def _is_toc_like_span(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    lowered = _normalize_text(text).lower()
    item_page_lines = [
        line for line in lines
        if re.match(r"(?i)^(item\s+\d{1,2}[a-z]?|part\s+[ivx]+)\b.+\s+\d{1,4}$", line)
    ]
    if len(item_page_lines) >= 3 and len(item_page_lines) / len(lines) >= 0.5:
        return True
    return lowered.startswith("table of contents") and len(item_page_lines) >= 2


def _is_structural_label_span(normalized: str) -> bool:
    lowered = normalized.lower().strip(" .:-")
    if lowered in _STRUCTURAL_LABELS:
        return True
    if len(lowered) > 180:
        return False
    if re.match("(?i)^item\\s+\\d{1,2}[a-z]?[.:\\-\\u2014\\u2013]?\\s+[a-z ,&'/-]+$", lowered):
        return "." not in lowered.strip().rstrip(".")
    return False


def _is_signature_or_certification_span(lowered: str) -> bool:
    return (
        "pursuant to the requirements" in lowered
        and (
            "has duly caused this report to be signed" in lowered
            or "signed on its behalf" in lowered
            or "certifies that" in lowered
        )
    )


def _is_exhibit_index_span(text: str, section_name: str) -> bool:
    if section_name not in _LOW_VALUE_EXHIBIT_SECTIONS:
        return False
    lowered = _normalize_text(text).lower()
    if "exhibit" not in lowered and "financial statement schedule" not in lowered:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    exhibit_lines = [
        line for line in lines
        if re.match(r"(?i)^(exhibit\s+)?\d+(\.\d+)?\b", line)
        or "incorporated by reference" in line.lower()
    ]
    return lowered.count("exhibit") >= 3 or len(exhibit_lines) >= 3 or "exhibit index" in lowered[:300]


def _batch_input_hash(batch: list[dict], span_pruning: str) -> str:
    payload = [
        {
            "id": span.get("id", ""),
            "text_hash": span.get("text_hash", ""),
            "section_name": span.get("section_name", ""),
        }
        for span in batch
    ]
    raw = json.dumps(
        {"cache_version": CACHE_VERSION, "span_pruning": span_pruning, "spans": payload},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _read_quote_batch_cache(
    ontology_dir: Path,
    stage_name: str,
    batch_idx: int,
    input_hash: str,
) -> list[dict] | None:
    path = batch_cache_path(ontology_dir, stage_name, batch_idx)
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata") or {}
    if not is_reusable_batch_cache_metadata(metadata):
        return None
    if metadata.get("cache_version") != CACHE_VERSION:
        return None
    if metadata.get("input_hash") != input_hash:
        return None
    items = data.get("items")
    return items if isinstance(items, list) else None


async def extract_evidence_quotes(
    worker: ExtractionWorker,
    ontology_dir: Path,
    ticker: str,
    period: str,
    doc_type: str,
    concurrency: int = 1,
    force: bool = False,
    span_pruning: str = SPAN_PRUNING_CONSERVATIVE,
    pilot_max_quote_spans: int = PILOT_MAX_QUOTE_SPANS,
    batch_size: int = BATCH_SIZE,
) -> list[dict]:
    """Extract evidence quotes from spans in batches with split retry."""
    stage_name = "extract_evidence_quotes"
    batch_size = max(1, int(batch_size))
    doc_type_key = normalize_doc_type(doc_type)
    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"

    spans_path = ontology_dir / "spans.jsonl"
    output_path = ontology_dir / "evidence_quotes.jsonl"
    signals_output_path = ontology_dir / "language_signals.jsonl"
    span_eligibility_path = ontology_dir / "span_eligibility_audit.jsonl"
    failures_path = ontology_dir / "batch_failures.jsonl"

    spans = read_jsonl(spans_path)
    if not spans:
        logger.warning(f"{stage_name}: no spans found", extra={"stage": stage_name})
        write_jsonl(span_eligibility_path, [])
        return []

    span_pruning = _normalize_span_pruning_mode(span_pruning)
    eligible_spans, span_eligibility_audit = _filter_spans_for_quote_extraction(
        spans,
        span_pruning=span_pruning,
        pilot_max_quote_spans=pilot_max_quote_spans,
    )
    write_jsonl(span_eligibility_path, span_eligibility_audit)
    skipped_count = len(spans) - len(eligible_spans)
    if skipped_count:
        logger.info(
            "%s: span pruning kept %s/%s spans, skipped %s",
            stage_name,
            len(eligible_spans),
            len(spans),
            skipped_count,
            extra={"stage": stage_name},
        )
    if not eligible_spans:
        write_jsonl(output_path, [])
        write_jsonl(signals_output_path, [])
        logger.warning(
            "%s: no eligible spans found after span pruning",
            stage_name,
            extra={"stage": stage_name},
        )
        return []

    quotes: list[dict] = []
    seen_keys: set[str] = set()
    total_batches = (len(eligible_spans) + batch_size - 1) // batch_size
    failed_span_count = 0
    _clear_stage_failures(failures_path, stage_name)
    if force:
        clear_stage_batch_cache(ontology_dir, stage_name)

    async def run_batch(batch_idx: int) -> tuple[list[dict], int]:
        start = batch_idx * batch_size
        batch = eligible_spans[start : start + batch_size]
        candidates = _build_quote_candidates(batch)
        input_hash = _batch_input_hash(batch, span_pruning)
        if not candidates:
            write_batch_cache(
                ontology_dir,
                stage_name,
                batch_idx,
                [],
                metadata={
                    "status": "empty",
                    "cache_version": CACHE_VERSION,
                    "input_hash": input_hash,
                    "span_pruning": span_pruning,
                    "span_count": len(batch),
                    "candidate_count": 0,
                },
            )
            return [], 0

        cached = _read_quote_batch_cache(ontology_dir, stage_name, batch_idx, input_hash)
        if cached is not None:
            logger.info(
                "%s batch %s/%s loaded from cache (%s quotes)",
                stage_name,
                batch_idx + 1,
                total_batches,
                len(cached),
                extra={"stage": stage_name},
            )
            return cached, 0

        raw_items, failed_spans = await _extract_batch_with_split_retry(
            worker=worker,
            batch=batch,
            batch_index=batch_idx,
            failures_path=failures_path,
            ticker=ticker,
            doc_type=doc_type,
            doc_type_key=doc_type_key,
            period=period,
            source_document_id=source_document_id,
            stage_name=stage_name,
        )
        batch_quotes = _materialize_quote_batch(
            raw_items=raw_items,
            batch=batch,
            candidates=candidates,
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
            batch_quotes,
            metadata={
                "status": "ok" if failed_spans == 0 else "partial_failed",
                "cache_version": CACHE_VERSION,
                "input_hash": input_hash,
                "span_pruning": span_pruning,
                "input_span_ids": [span.get("id", "") for span in batch],
                "span_count": len(batch),
                "candidate_count": len(candidates),
                "failed_spans": failed_spans,
            },
        )
        return batch_quotes, failed_spans

    def log_complete(batch_idx: int, result: tuple[list[dict], int]) -> None:
        logger.info(
            "%s batch %s/%s complete: %s quotes, %s failed spans",
            stage_name,
            batch_idx + 1,
            total_batches,
            len(result[0]),
            result[1],
            extra={"stage": stage_name},
        )

    results = await run_limited_batches(
        batch_indices=list(range(total_batches)),
        concurrency=concurrency,
        run_one=run_batch,
        on_complete=log_complete,
        stage_name=stage_name,
    )

    for _batch_idx, (batch_quotes, failed_spans) in sorted(results, key=lambda row: row[0]):
        failed_span_count += failed_spans
        for quote in batch_quotes:
            dedup_key = _quote_dedup_key(quote["quote_text"], quote["source_span_id"])
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            quotes.append(quote)

    # Check failure threshold
    if eligible_spans and failed_span_count / len(eligible_spans) > 0.5:
        raise PipelineStageError(
            f"{stage_name}: {failed_span_count}/{len(eligible_spans)} spans failed after split retry (>50%)"
        )

    write_jsonl(output_path, quotes)

    # Extract standalone language signals
    standalone_signals = _extract_standalone_signals(quotes, ticker, period, doc_type, doc_type_key, source_document_id)
    write_jsonl(signals_output_path, standalone_signals)

    logger.info(
        f"{stage_name}: extracted {len(quotes)} quotes, {len(standalone_signals)} signals",
        extra={"stage": stage_name},
    )
    return quotes


def _materialize_quote_batch(
    *,
    raw_items: list[dict],
    batch: list[dict],
    candidates: list[dict],
    ticker: str,
    period: str,
    doc_type: str,
    doc_type_key: str,
    source_document_id: str,
) -> list[dict]:
    candidate_by_id = {c["candidate_id"]: c for c in candidates}
    span_by_id = {span["id"]: span for span in batch}
    quotes: list[dict] = []

    for item in raw_items:
        candidate = candidate_by_id.get(item.get("candidate_id", ""))
        if not candidate:
            continue
        source_span_id = candidate["source_span_id"]
        span = span_by_id.get(source_span_id)
        if not span:
            continue

        quote_seq = sum(1 for q in quotes if q.get("source_span_id") == source_span_id) + 1
        section_name = span.get("section_name", item.get("section_name", ""))
        section_key = span.get("section_key", section_name)
        span_seq = _extract_span_sequence(span["id"])
        quote_obj = {
            "id": generate_scoped_id(
                "quote", ticker, period, doc_type_key,
                generate_quote_local_id(section_key, span_seq, quote_seq),
            ),
            "type": "EvidenceQuote",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": doc_type,
            "period": period,
            "source_span_id": source_span_id,
            "quote_text": candidate["text"],
            "quote_type": item["quote_type"],
            "section_name": section_name,
            "start_char": candidate["start_char"],
            "end_char": candidate["end_char"],
            "absolute_start_char": candidate["absolute_start_char"],
            "absolute_end_char": candidate["absolute_end_char"],
            "confidence": item["confidence"],
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        }

        embedded_signals = []
        for sig in item.get("language_signals") or []:
            embedded_signals.append({
                "signal_text": sig.get("signal_text", ""),
                "signal_type": sig.get("signal_type", ""),
                "strength": sig.get("strength", "medium"),
                "direction": sig.get("direction", "neutral"),
                "certainty": sig.get("certainty", "uncertain"),
                "temporal_scope": sig.get("temporal_scope", "current"),
                "exact_match_verified": sig.get("exact_match_verified", False),
            })
        if embedded_signals:
            quote_obj["language_signals"] = embedded_signals
        quotes.append(quote_obj)
    return quotes


async def _extract_batch_with_split_retry(
    *,
    worker: ExtractionWorker,
    batch: list[dict],
    batch_index: int,
    split_depth: int = 0,
    failures_path: Path,
    ticker: str,
    doc_type: str,
    doc_type_key: str,
    period: str,
    source_document_id: str,
    stage_name: str,
) -> tuple[list[dict], int]:
    candidates = _build_quote_candidates(batch)
    if not candidates:
        return [], 0

    try:
        return await _extract_candidate_batch(
            worker,
            candidates,
            stage_name,
            batch_index=batch_index,
            span_count=len(batch),
            candidate_count=len(candidates),
            split_depth=split_depth,
        ), 0
    except ProviderOverloadError:
        raise
    except RateLimitError as e:
        batch_span_ids = [s["id"] for s in batch]
        logger.warning(
            "%s batch %s rate limited after same-batch retries; not splitting: %s",
            stage_name,
            batch_index,
            e,
            extra={"stage": stage_name, "rate_limited": True},
        )
        _record_batch_failure(
            failures_path, ticker, doc_type, period, source_document_id,
            doc_type_key, stage_name, batch_index, batch_span_ids, str(e),
            error_type="RateLimitError",
        )
        return [], len(batch)
    except Exception as e:
        if _is_transient_service_error(e):
            batch_span_ids = [s["id"] for s in batch]
            logger.error(
                "%s batch %s failed with transient service error; recording %s failed spans without split retry: %s",
                stage_name,
                batch_index,
                len(batch),
                e,
                extra={"stage": stage_name},
            )
            _record_batch_failure(
                failures_path, ticker, doc_type, period, source_document_id,
                doc_type_key, stage_name, batch_index, batch_span_ids, str(e),
                error_type="TransientServiceError",
            )
            return [], len(batch)
        if len(batch) <= 1:
            batch_span_ids = [s["id"] for s in batch]
            logger.error(
                f"{stage_name} leaf batch {batch_index} failed: {e}",
                extra={"stage": stage_name},
            )
            _record_batch_failure(
                failures_path, ticker, doc_type, period, source_document_id,
                doc_type_key, stage_name, batch_index, batch_span_ids, str(e),
            )
            return [], len(batch)

        midpoint = max(1, len(batch) // 2)
        logger.warning(
            "%s batch %s failed; retrying as %s and %s span sub-batches: %s",
            stage_name,
            batch_index,
            midpoint,
            len(batch) - midpoint,
            e,
            extra={"stage": stage_name},
        )
        left_items, left_failed = await _extract_batch_with_split_retry(
            worker=worker,
            batch=batch[:midpoint],
            batch_index=batch_index * 10 + 1,
            split_depth=split_depth + 1,
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
            batch=batch[midpoint:],
            batch_index=batch_index * 10 + 2,
            split_depth=split_depth + 1,
            failures_path=failures_path,
            ticker=ticker,
            doc_type=doc_type,
            doc_type_key=doc_type_key,
            period=period,
            source_document_id=source_document_id,
            stage_name=stage_name,
        )
        return [*left_items, *right_items], left_failed + right_failed


def _is_transient_service_error(error: Exception) -> bool:
    message = str(error).lower()
    return (
        "api_error_status\":500" in message
        or "api error: 500" in message
        or "network error" in message
        or "server-side issue" in message
    )


async def _extract_candidate_batch(
    worker: ExtractionWorker,
    candidates: list[dict],
    stage_name: str,
    *,
    batch_index: int,
    span_count: int,
    candidate_count: int,
    split_depth: int,
) -> list[dict]:
    input_data = {
        "candidates_json": json.dumps(
            [
                {
                    "candidate_id": c["candidate_id"],
                    "section_name": c["section_name"],
                    "text": c["text"],
                }
                for c in candidates
            ],
            ensure_ascii=False,
        ),
        "quote_types": ", ".join(QUOTE_TYPES),
        "signal_types": ", ".join(SIGNAL_TYPES),
    }
    return await worker.extract(
        QUOTE_EXTRACTION_PROMPT,
        input_data,
        _SCHEMA,
        stage_name,
        call_metadata={
            "batch_index": batch_index,
            "span_count": span_count,
            "candidate_count": candidate_count,
            "split_retry": split_depth > 0,
            "split_depth": split_depth,
        },
    )


def _resolve_span_id(item: dict, batch: list[dict]) -> str | None:
    """Resolve the source_span_id from the AI output item."""
    item_id = item.get("id", "")
    # If id looks like a span id, use it directly
    for span in batch:
        if item_id == span["id"] or item_id.startswith(span["id"]):
            return span["id"]
    # Try matching by section_name + text content
    quote_text = item.get("quote_text", "")
    for span in batch:
        if quote_text and quote_text in span.get("text", ""):
            return span["id"]
    # Fallback: first span in batch
    if batch:
        return batch[0]["id"]
    return None


def _extract_span_sequence(span_id: str) -> int:
    """Extract sequence number from span id like 'span:...:item1a:0042'."""
    parts = span_id.split(":")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0


def _extract_standalone_signals(
    quotes: list[dict], ticker: str, period: str, doc_type: str,
    doc_type_key: str, source_document_id: str,
) -> list[dict]:
    """Extract standalone LanguageSignal objects from embedded signals in quotes."""
    from krw_ontology.schema.id_utils import generate_signal_local_id

    signals: list[dict] = []
    for quote in quotes:
        embedded = quote.get("language_signals")
        if not embedded:
            continue
        source_span_id = quote.get("source_span_id", "")
        section_name = quote.get("section_name", "")
        section_key = _extract_span_section_key(source_span_id) or section_name
        span_seq = _extract_span_sequence(source_span_id)

        for sig_idx, sig in enumerate(embedded, 1):
            signal_id = generate_scoped_id(
                "signal", ticker, period, doc_type_key,
                generate_signal_local_id(section_key, span_seq, sig_idx),
            )
            signals.append({
                "id": signal_id,
                "type": "LanguageSignal",
                "ticker": ticker,
                "source_document_id": source_document_id,
                "document_type": doc_type,
                "period": period,
                "source_quote_id": quote["id"],
                "signal_text": sig["signal_text"],
                "signal_type": sig["signal_type"],
                "strength": sig["strength"],
                "direction": sig["direction"],
                "certainty": sig["certainty"],
                "temporal_scope": sig["temporal_scope"],
                "exact_match_verified": sig.get("exact_match_verified", False),
                "schema_version": SCHEMA_VERSION,
            })
    return signals


def _extract_span_section_key(span_id: str) -> str | None:
    parts = span_id.split(":")
    if len(parts) >= 6:
        return parts[-2]
    return None


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


def _clear_stage_failures(failures_path: Path, stage_name: str) -> None:
    if not failures_path.exists():
        return
    failures = [row for row in read_jsonl(failures_path) if row.get("stage") != stage_name]
    write_jsonl(failures_path, failures)
