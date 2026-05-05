"""AI stage: Extract evidence quotes from source spans."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from krw_ontology.config.constants import DOCUMENT_TYPE_KEY
from krw_ontology.errors import PipelineStageError
from krw_ontology.extraction.prompts.quote_extraction import (
    QUOTE_EXTRACTION_PROMPT,
    QUOTE_TYPES,
    SIGNAL_TYPES,
)
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.pipeline.ai_batches import (
    clear_stage_batch_cache,
    read_batch_cache,
    run_limited_batches,
    write_batch_cache,
)
from krw_ontology.schema.id_utils import generate_quote_local_id, generate_scoped_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import read_jsonl, write_jsonl

logger = logging.getLogger("krw_ontology")

BATCH_SIZE = 5

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
}
_QUOTE_KEYWORDS = (
    "adverse", "affect", "risk", "uncertain", "competition", "competitive",
    "regulatory", "litigation", "supply", "supplier", "manufacturing",
    "inventory", "revenue", "sales", "net sales", "margin", "gross",
    "operating", "cost", "expense", "growth", "increased", "decreased",
    "cash", "liquidity", "capital", "expected", "anticipate", "believe",
    "may", "could", "material",
)


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


async def extract_evidence_quotes(
    worker: ExtractionWorker,
    ontology_dir: Path,
    ticker: str,
    period: str,
    doc_type: str,
    concurrency: int = 1,
    force: bool = False,
) -> list[dict]:
    """Extract evidence quotes from spans in batches with split retry."""
    stage_name = "extract_evidence_quotes"
    doc_type_key = DOCUMENT_TYPE_KEY
    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"

    spans_path = ontology_dir / "spans.jsonl"
    output_path = ontology_dir / "evidence_quotes.jsonl"
    signals_output_path = ontology_dir / "language_signals.jsonl"
    failures_path = ontology_dir / "batch_failures.jsonl"

    spans = read_jsonl(spans_path)
    if not spans:
        logger.warning(f"{stage_name}: no spans found", extra={"stage": stage_name})
        return []

    quotes: list[dict] = []
    seen_keys: set[str] = set()
    total_batches = (len(spans) + BATCH_SIZE - 1) // BATCH_SIZE
    failed_span_count = 0
    _clear_stage_failures(failures_path, stage_name)
    if force:
        clear_stage_batch_cache(ontology_dir, stage_name)

    async def run_batch(batch_idx: int) -> tuple[list[dict], int]:
        start = batch_idx * BATCH_SIZE
        batch = spans[start : start + BATCH_SIZE]
        candidates = _build_quote_candidates(batch)
        if not candidates:
            write_batch_cache(
                ontology_dir,
                stage_name,
                batch_idx,
                [],
                metadata={"status": "empty", "span_count": len(batch), "candidate_count": 0},
            )
            return [], 0

        cached = read_batch_cache(ontology_dir, stage_name, batch_idx)
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
                "status": "ok",
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
    if spans and failed_span_count / len(spans) > 0.5:
        raise PipelineStageError(
            f"{stage_name}: {failed_span_count}/{len(spans)} spans failed after split retry (>50%)"
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
    failures_path: Path,
    ticker: str,
    doc_type: str,
    period: str,
    source_document_id: str,
    stage_name: str,
) -> tuple[list[dict], int]:
    candidates = _build_quote_candidates(batch)
    if not candidates:
        return [], 0

    try:
        return await _extract_candidate_batch(worker, candidates, stage_name), 0
    except Exception as e:
        if len(batch) <= 1:
            batch_span_ids = [s["id"] for s in batch]
            logger.error(
                f"{stage_name} leaf batch {batch_index} failed: {e}",
                extra={"stage": stage_name},
            )
            _record_batch_failure(
                failures_path, ticker, doc_type, period, source_document_id,
                stage_name, batch_index, batch_span_ids, str(e),
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
            failures_path=failures_path,
            ticker=ticker,
            doc_type=doc_type,
            period=period,
            source_document_id=source_document_id,
            stage_name=stage_name,
        )
        right_items, right_failed = await _extract_batch_with_split_retry(
            worker=worker,
            batch=batch[midpoint:],
            batch_index=batch_index * 10 + 2,
            failures_path=failures_path,
            ticker=ticker,
            doc_type=doc_type,
            period=period,
            source_document_id=source_document_id,
            stage_name=stage_name,
        )
        return [*left_items, *right_items], left_failed + right_failed


async def _extract_candidate_batch(
    worker: ExtractionWorker,
    candidates: list[dict],
    stage_name: str,
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
        QUOTE_EXTRACTION_PROMPT, input_data, _SCHEMA, stage_name
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
    source_document_id: str, stage: str, batch_index: int,
    input_span_ids: list[str], error_message: str,
) -> None:
    from datetime import datetime, timezone

    failure = {
        "id": f"batch_failure:{ticker}:{period}:{DOCUMENT_TYPE_KEY}:{stage}:{batch_index:04d}",
        "type": "BatchFailure",
        "ticker": ticker,
        "document_type": doc_type,
        "period": period,
        "source_document_id": source_document_id,
        "stage": stage,
        "batch_index": batch_index,
        "input_span_ids": input_span_ids,
        "attempts": 3,
        "error_type": "ExtractionError",
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
