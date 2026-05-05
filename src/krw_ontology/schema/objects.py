"""Pydantic v2 models for all 12 object types matching objects.yaml."""

from __future__ import annotations

from pydantic import BaseModel, Field


SCHEMA_VERSION = "0.1.0"


class SourceDocument(BaseModel):
    id: str
    type: str = "SourceDocument"
    ticker: str
    source_document_id: str | None = None
    document_type: str
    period: str
    metadata_ref: str | None = None
    schema_version: str = SCHEMA_VERSION


class SourceSpan(BaseModel):
    id: str
    type: str = "SourceSpan"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    section_name: str
    section_number: str
    section_key: str | None = None
    section_instance: int | None = None
    span_index: int
    start_char: int
    end_char: int
    text: str
    text_hash: str
    char_count: int
    section_detection_confidence: str
    section_detection_method: str
    schema_version: str = SCHEMA_VERSION


class LanguageSignalEmbedded(BaseModel):
    """Embedded language signal within an EvidenceQuote."""
    signal_text: str
    signal_type: str
    strength: str
    direction: str
    certainty: str
    temporal_scope: str
    exact_match_verified: bool = False


class EvidenceQuote(BaseModel):
    id: str
    type: str = "EvidenceQuote"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    source_span_id: str
    quote_text: str
    quote_type: str
    section_name: str
    start_char: int | None = None
    end_char: int | None = None
    absolute_start_char: int | None = None
    absolute_end_char: int | None = None
    confidence: str
    review_status: str = "accepted"
    language_signals: list[LanguageSignalEmbedded] | None = None
    schema_version: str = SCHEMA_VERSION


class LanguageSignal(BaseModel):
    id: str
    type: str = "LanguageSignal"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    source_quote_id: str
    signal_text: str
    signal_type: str
    strength: str
    direction: str
    certainty: str
    temporal_scope: str
    exact_match_verified: bool
    schema_version: str = SCHEMA_VERSION


class ResearchClaim(BaseModel):
    id: str
    type: str = "ResearchClaim"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    claim_text: str
    claim_type: str
    supported_by_quotes: list[str] = Field(min_length=1)
    related_metrics: list[str] | None = None
    confidence: str
    review_status: str = "accepted"
    schema_version: str = SCHEMA_VERSION


class ResearchObject(BaseModel):
    """Shared schema for RiskFactor, GrowthDriver, Headwind. Type field discriminates."""
    id: str
    type: str
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    name: str
    category: str
    description: str
    supported_by_claims: list[str] | None = None
    supported_by_quotes: list[str] | None = None
    affects: list[str] | None = None
    unmapped_impacts: list[str] | None = None
    unmapped_metrics: list[str] | None = None
    qualitative_impact: str
    confidence: str
    review_status: str = "accepted"
    schema_version: str = SCHEMA_VERSION


class AssumptionCandidate(BaseModel):
    id: str
    type: str = "AssumptionCandidate"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    name: str
    assumption_text: str
    assumption_type: str
    value_hint: str | None = None
    supported_by_claims: list[str] | None = None
    supported_by_quotes: list[str] | None = None
    related_metrics: list[str] | None = None
    unmapped_metrics: list[str] | None = None
    confidence: str
    review_status: str = "needs_review"
    schema_version: str = SCHEMA_VERSION


class Metric(BaseModel):
    id: str
    type: str = "Metric"
    name: str
    category: str
    unit: str
    description: str
    schema_version: str = SCHEMA_VERSION


class XBRLFact(BaseModel):
    id: str
    type: str = "XBRLFact"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    taxonomy_tag: str
    safe_taxonomy_tag: str
    value: float
    unit: str
    context_ref: str
    source_filing_detail: str
    decimals: int | None = None
    schema_version: str = SCHEMA_VERSION


class FinancialMetricValue(BaseModel):
    id: str
    type: str = "FinancialMetricValue"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    metric_name: str
    value: float
    unit: str
    fiscal_year: int | None = None
    source_xbrl_fact_id: str
    source: str = "filing_inline_xbrl"
    schema_version: str = SCHEMA_VERSION


class DerivedMetricValue(BaseModel):
    id: str
    type: str = "DerivedMetricValue"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    metric_name: str
    value: float
    unit: str
    formula: str
    input_metric_ids: list[str]
    source: str = "code_calculated"
    schema_version: str = SCHEMA_VERSION


class NumericEvidence(BaseModel):
    id: str
    type: str = "NumericEvidence"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    numeric_kind: str
    evidence_role: str
    value: float
    raw_text: str
    unit: str
    source_object_id: str
    source_field: str
    source_method: str
    source_quote_id: str | None = None
    formula: str | None = None
    input_object_ids: list[str] | None = None
    schema_version: str = SCHEMA_VERSION


class CalculatedNumericSupport(BaseModel):
    id: str
    type: str = "CalculatedNumericSupport"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    calculation_type: str
    value: float
    unit: str
    formula: str
    input_object_ids: list[str]
    display_value: str | None = None
    source: str = "code_calculated"
    schema_version: str = SCHEMA_VERSION


class Edge(BaseModel):
    id: str
    type: str = "Edge"
    ticker: str
    source_document_id: str
    document_type: str
    period: str
    from_id: str
    to_id: str
    relation_name: str
    relation_id: str
    confidence: str
    review_status: str = "accepted"
    schema_version: str = SCHEMA_VERSION


# Type discriminator mapping
OBJECT_TYPE_MODELS = {
    "SourceDocument": SourceDocument,
    "SourceSpan": SourceSpan,
    "EvidenceQuote": EvidenceQuote,
    "LanguageSignal": LanguageSignal,
    "ResearchClaim": ResearchClaim,
    "RiskFactor": ResearchObject,
    "GrowthDriver": ResearchObject,
    "Headwind": ResearchObject,
    "AssumptionCandidate": AssumptionCandidate,
    "Metric": Metric,
    "XBRLFact": XBRLFact,
    "FinancialMetricValue": FinancialMetricValue,
    "DerivedMetricValue": DerivedMetricValue,
    "NumericEvidence": NumericEvidence,
    "CalculatedNumericSupport": CalculatedNumericSupport,
    "Edge": Edge,
}
