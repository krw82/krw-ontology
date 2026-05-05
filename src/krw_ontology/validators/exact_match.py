"""Exact match validator — quote text substring check (Section 8.2)."""

from __future__ import annotations

import re


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs to a single space and strip."""
    return re.sub(r"\s+", " ", text).strip()


def validate_exact_match(
    quote: dict, all_spans: dict[str, dict]
) -> tuple[bool, str | None]:
    """Check quote_text is an exact substring of the referenced SourceSpan text.

    Only applies to EvidenceQuote objects. All other types pass automatically.
    Tries raw match first, then whitespace-normalized match on failure.
    """
    if quote.get("type") != "EvidenceQuote":
        return True, None

    source_span_id = quote.get("source_span_id")
    if not source_span_id:
        return False, "EvidenceQuote missing source_span_id"

    span = all_spans.get(source_span_id)
    if span is None:
        return False, f"Source span not found: {source_span_id}"

    quote_text = quote.get("quote_text", "")
    span_text = span.get("text", "")

    if not quote_text:
        return False, "EvidenceQuote has empty quote_text"

    # Raw match attempt
    if quote_text in span_text:
        return True, None

    # Whitespace-normalized match
    if _normalize_whitespace(quote_text) in _normalize_whitespace(span_text):
        return True, None

    return False, "quote_text not found in source span text (exact match failed)"
