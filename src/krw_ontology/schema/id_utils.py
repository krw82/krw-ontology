"""ID generation utilities following Section 5 conventions."""

from __future__ import annotations

import hashlib


def generate_source_document_id(ticker: str, period: str, doc_type_key: str) -> str:
    """SourceDocument has no local_id suffix."""
    return f"source:{ticker}:{period}:{doc_type_key}"


def generate_scoped_id(
    object_type: str,
    ticker: str,
    period: str,
    doc_type_key: str,
    local_id: str,
) -> str:
    """Generate IDs for objects that require a local_id."""
    return f"{object_type}:{ticker}:{period}:{doc_type_key}:{local_id}"


def generate_metric_id(canonical_name: str) -> str:
    """Metrics are canonical, not ticker- or period-scoped."""
    return f"metric:{canonical_name}"


def generate_xbrl_local_id(safe_taxonomy_tag: str, context_ref: str, unit: str, value: str) -> str:
    """XBRLFact local_id: {safe_taxonomy_tag}:{hash8}."""
    normalized_value = str(value).strip()
    raw = f"{context_ref}|{unit}|{normalized_value}"
    hash8 = hashlib.sha1(raw.encode()).hexdigest()[:8]
    return f"{safe_taxonomy_tag}:{hash8}"


def generate_edge_local_id(relation_id: str, from_id: str, to_id: str) -> str:
    """Edge local_id: {relation_id}:{hash10}."""
    raw = f"{from_id}|{relation_id}|{to_id}"
    hash10 = hashlib.sha1(raw.encode()).hexdigest()[:10]
    return f"{relation_id}:{hash10}"


def generate_numeric_evidence_local_id(*parts: str) -> str:
    """NumericEvidence local_id: stable hash of source + value identity."""
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def generate_span_local_id(section_name: str, sequence: int) -> str:
    """SourceSpan local_id: {section_name}:{sequence:04d}."""
    return f"{section_name}:{sequence:04d}"


def generate_quote_local_id(section_name: str, span_sequence: int, quote_seq: int) -> str:
    """EvidenceQuote local_id: {section_name}:{sequence:04d}:{quote_seq:03d}."""
    return f"{section_name}:{span_sequence:04d}:{quote_seq:03d}"


def generate_signal_local_id(section_name: str, span_sequence: int, signal_seq: int) -> str:
    """LanguageSignal local_id: {section_name}:{sequence:04d}:{signal_seq:03d}."""
    return f"{section_name}:{span_sequence:04d}:{signal_seq:03d}"
