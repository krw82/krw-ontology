"""Pydantic models for validating AI extraction output (Section 18.2)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _coerce_confidence(value) -> str:
    """Normalize AI confidence variants to high/medium/low."""
    if isinstance(value, (int, float)):
        if value >= 0.8:
            return "high"
        if value >= 0.5:
            return "medium"
        return "low"
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"high", "medium", "low"}:
            return normalized
        try:
            return _coerce_confidence(float(normalized))
        except ValueError:
            return normalized
    return "medium"


class LanguageSignalOutput(BaseModel):
    """Language signal extracted alongside a quote."""

    model_config = ConfigDict(populate_by_name=True)

    signal_text: str = ""
    signal_type: str = Field(default="", alias="type")
    strength: str = "medium"
    direction: str = "neutral"
    certainty: str = "uncertain"
    temporal_scope: str = "current"
    exact_match_verified: bool = False


class QuoteExtractionOutput(BaseModel):
    """AI output model for evidence quote extraction."""

    candidate_id: str | None = None
    id: str | None = None
    quote_text: str | None = None
    quote_type: str
    section_name: str
    start_char: int | None = None
    end_char: int | None = None
    absolute_start_char: int | None = None
    absolute_end_char: int | None = None
    confidence: str
    language_signals: list[LanguageSignalOutput] | None = None

    @field_validator("language_signals", mode="before")
    @classmethod
    def _coerce_language_signals(cls, value):
        if value is None:
            return None
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [
                {"signal_type": item}
                if isinstance(item, str)
                else item
                for item in value
            ]
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value):
        return _coerce_confidence(value)


class ClaimExtractionOutput(BaseModel):
    """AI output model for research claim extraction."""

    id: str
    claim_text: str
    claim_type: str
    supported_by_quotes: list[str] = Field(min_length=1)
    related_metrics: list[str] | None = None
    confidence: str

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value):
        return _coerce_confidence(value)


class ObjectExtractionOutput(BaseModel):
    """AI output model for RiskFactor/GrowthDriver/Headwind extraction."""

    id: str
    type: str
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

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value):
        return _coerce_confidence(value)


class AssumptionExtractionOutput(BaseModel):
    """AI output model for assumption candidate extraction."""

    id: str
    name: str
    assumption_text: str
    assumption_type: str
    value_hint: str | None = None
    supported_by_claims: list[str] | None = None
    supported_by_quotes: list[str] | None = None
    related_metrics: list[str] | None = None
    unmapped_metrics: list[str] | None = None
    confidence: str

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value):
        return _coerce_confidence(value)


class EdgeGenerationOutput(BaseModel):
    """AI output model for edge generation."""

    id: str
    from_id: str
    to_id: str
    relation_name: str
    relation_id: str
    confidence: str

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value):
        return _coerce_confidence(value)


# Mapping stage names to their output models for validation
STAGE_OUTPUT_MODELS = {
    "extract_evidence_quotes": QuoteExtractionOutput,
    "extract_research_claims": ClaimExtractionOutput,
    "extract_risks_drivers_headwinds": ObjectExtractionOutput,
    "extract_assumption_candidates": AssumptionExtractionOutput,
    "generate_edges": EdgeGenerationOutput,
}
