"""Stage 7.6: Build source spans from sections with windowed splitting."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from krw_ontology.config.constants import (
    SPAN_HARD_MAX_CHARS,
    SPAN_OVERLAP_CHARS,
    SPAN_TARGET_CHARS_MAX,
    SPAN_TARGET_CHARS_MIN,
)
from krw_ontology.errors import PipelineStageError
from krw_ontology.schema.id_utils import generate_scoped_id, generate_span_local_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import write_jsonl

logger = logging.getLogger("krw_ontology")


def build_spans(
    sections: list[dict],
    doc_type_key: str,
    ticker: str,
    period: str,
    source_document_id: str,
    clean_md_text: str,
    output_path: Path,
    document_type: str = "10-K",
) -> dict:
    """Split section text into overlapping source spans.

    Returns dict with: spans (list of SourceSpan dicts), output_path.
    """
    try:
        all_spans: list[dict] = []

        for section in sections:
            section_text = section["text"]
            section_name = section["name"]
            section_key = section.get("section_key", section_name)
            section_instance = section.get("section_instance", 0)
            section_start_char = section.get("start_char")
            if section_start_char is None:
                section_start_char = clean_md_text.find(section_text)
            if section_start_char == -1 or section_start_char is None:
                section_start_char = 0

            spans = _split_section(
                section_text=section_text,
                section_name=section_name,
                section_key=section_key,
                section_instance=section_instance,
                section_start_char=section_start_char,
                doc_type_key=doc_type_key,
                document_type=document_type,
                ticker=ticker,
                period=period,
                source_document_id=source_document_id,
                section_detection_confidence=section.get("section_detection_confidence", "medium"),
                section_detection_method=section.get("section_detection_method", "regex"),
            )
            all_spans.extend(spans)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(output_path, all_spans)

        logger.info(
            "build_spans: created %d spans", len(all_spans),
            extra={"stage": "build_source_spans"},
        )
        return {"spans": all_spans, "output_path": output_path}

    except PipelineStageError:
        raise
    except Exception as e:
        raise PipelineStageError(f"build_spans: {e}") from e


def _split_section(
    section_text: str,
    section_name: str,
    section_key: str,
    section_instance: int,
    section_start_char: int,
    doc_type_key: str,
    document_type: str,
    ticker: str,
    period: str,
    source_document_id: str,
    section_detection_confidence: str,
    section_detection_method: str,
) -> list[dict]:
    """Split a single section's text into overlapping spans."""
    text = section_text
    if len(text) <= SPAN_TARGET_CHARS_MAX:
        return [_make_span(
            text=text,
            section_name=section_name,
            section_key=section_key,
            section_instance=section_instance,
            seq=0,
            start_char=section_start_char,
            end_char=section_start_char + len(text),
            doc_type_key=doc_type_key,
            document_type=document_type,
            ticker=ticker,
            period=period,
            source_document_id=source_document_id,
            section_detection_confidence=section_detection_confidence,
            section_detection_method=section_detection_method,
        )]

    spans = []
    seq = 0
    pos = 0

    while pos < len(text):
        end = pos + SPAN_TARGET_CHARS_MAX

        if end >= len(text):
            chunk = text[pos:]
            if len(chunk) >= SPAN_TARGET_CHARS_MIN or not spans:
                spans.append(_make_span(
                    text=chunk,
                    section_name=section_name,
                    section_key=section_key,
                    section_instance=section_instance,
                    seq=seq,
                    start_char=section_start_char + pos,
                    end_char=section_start_char + len(text),
                    doc_type_key=doc_type_key,
                    document_type=document_type,
                    ticker=ticker,
                    period=period,
                    source_document_id=source_document_id,
                    section_detection_confidence=section_detection_confidence,
                    section_detection_method=section_detection_method,
                ))
                seq += 1
            else:
                # Merge with previous span
                prev = spans[-1]
                merged_text = prev["text"] + "\n" + chunk
                prev["text"] = merged_text
                prev["end_char"] = section_start_char + len(text)
                prev["char_count"] = len(merged_text)
                prev["text_hash"] = _compute_text_hash(merged_text)
            break

        # Try to split at sentence boundary
        split_pos = _find_sentence_boundary(text, pos, end)
        chunk = text[pos:split_pos]

        if len(chunk) > SPAN_HARD_MAX_CHARS:
            split_pos = _find_sentence_boundary(text, pos, pos + SPAN_HARD_MAX_CHARS)
            chunk = text[pos:split_pos]

        spans.append(_make_span(
            text=chunk,
            section_name=section_name,
            section_key=section_key,
            section_instance=section_instance,
            seq=seq,
            start_char=section_start_char + pos,
            end_char=section_start_char + split_pos,
            doc_type_key=doc_type_key,
            document_type=document_type,
            ticker=ticker,
            period=period,
            source_document_id=source_document_id,
            section_detection_confidence=section_detection_confidence,
            section_detection_method=section_detection_method,
        ))
        seq += 1
        pos = split_pos - SPAN_OVERLAP_CHARS
        if pos <= (split_pos - len(chunk)):
            pos = split_pos

    return spans


def _find_sentence_boundary(text: str, start: int, target_end: int) -> int:
    """Find the best sentence boundary at or before target_end."""
    search_region = text[start:target_end]
    boundaries = [i for i, ch in enumerate(search_region) if ch in ".!?"]
    if boundaries:
        return start + boundaries[-1] + 1
    return target_end


def _make_span(
    text: str,
    section_name: str,
    section_key: str,
    section_instance: int,
    seq: int,
    start_char: int,
    end_char: int,
    doc_type_key: str,
    document_type: str,
    ticker: str,
    period: str,
    source_document_id: str,
    section_detection_confidence: str,
    section_detection_method: str,
) -> dict:
    local_id = generate_span_local_id(section_key, seq)
    span_id = generate_scoped_id("span", ticker, period, doc_type_key, local_id)
    return {
        "id": span_id,
        "type": "SourceSpan",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "section_name": section_name,
        "section_number": section_name,
        "section_key": section_key,
        "section_instance": section_instance,
        "span_index": seq,
        "start_char": start_char,
        "end_char": end_char,
        "text": text,
        "text_hash": _compute_text_hash(text),
        "char_count": len(text),
        "section_detection_confidence": section_detection_confidence,
        "section_detection_method": section_detection_method,
        "schema_version": SCHEMA_VERSION,
    }


def _compute_text_hash(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode()).hexdigest()
