"""Tests for Pydantic schema validation."""

from __future__ import annotations


from krw_ontology.schema.objects import (
    SCHEMA_VERSION,
)
from krw_ontology.validators.schema_validator import validate_schema


class TestValidObjects:
    def test_valid_source_span(self, sample_source_span: dict):
        ok, reason = validate_schema(sample_source_span)
        assert ok, f"Expected valid, got reason: {reason}"

    def test_valid_evidence_quote(self, sample_evidence_quote: dict):
        ok, reason = validate_schema(sample_evidence_quote)
        assert ok, f"Expected valid, got reason: {reason}"

    def test_valid_research_claim(self, sample_research_claim: dict):
        ok, reason = validate_schema(sample_research_claim)
        assert ok, f"Expected valid, got reason: {reason}"

    def test_valid_source_document(self):
        obj = {
            "id": "source:AAPL:FY2025:10K",
            "type": "SourceDocument",
            "ticker": "AAPL",
            "document_type": "10-K",
            "period": "FY2025",
            "schema_version": SCHEMA_VERSION,
        }
        ok, reason = validate_schema(obj)
        assert ok, f"Expected valid, got reason: {reason}"

    def test_valid_calculated_numeric_support(self):
        obj = {
            "id": "calculated_numeric:AAPL:FY2025:10K:abc123def456",
            "type": "CalculatedNumericSupport",
            "ticker": "AAPL",
            "source_document_id": "source:AAPL:FY2025:10K",
            "document_type": "10-K",
            "period": "FY2025",
            "calculation_type": "difference",
            "value": 5_984_000_000,
            "unit": "USD",
            "formula": "96662000000 - 90678000000",
            "input_object_ids": [
                "financial_metric:AAPL:FY2025:10K:debt_2024",
                "financial_metric:AAPL:FY2025:10K:debt_2025",
            ],
            "display_value": "$5.984B",
            "source": "code_calculated",
            "schema_version": SCHEMA_VERSION,
        }
        ok, reason = validate_schema(obj)
        assert ok, f"Expected valid CalculatedNumericSupport, got: {reason}"

    def test_valid_numeric_evidence(self):
        obj = {
            "id": "numeric_evidence:AAPL:FY2025:10K:abc123def456",
            "type": "NumericEvidence",
            "ticker": "AAPL",
            "source_document_id": "source:AAPL:FY2025:10K",
            "document_type": "10-K",
            "period": "FY2025",
            "numeric_kind": "amount",
            "evidence_role": "financial_amount",
            "value": 61_100_000_000,
            "raw_text": "$61.1",
            "unit": "USD",
            "source_object_id": "quote:AAPL:FY2025:10K:item7:0001:001",
            "source_field": "quote_text",
            "source_method": "quote_text",
            "source_quote_id": "quote:AAPL:FY2025:10K:item7:0001:001",
            "input_object_ids": [],
            "schema_version": SCHEMA_VERSION,
        }
        ok, reason = validate_schema(obj)
        assert ok, f"Expected valid NumericEvidence, got: {reason}"


class TestMissingFields:
    def test_missing_type(self, sample_source_span: dict):
        del sample_source_span["type"]
        ok, reason = validate_schema(sample_source_span)
        assert not ok
        assert "type" in reason.lower()

    def test_missing_required_field(self):
        obj = {"type": "SourceSpan"}
        ok, reason = validate_schema(obj)
        assert not ok

    def test_missing_id(self, sample_source_span: dict):
        del sample_source_span["id"]
        ok, reason = validate_schema(sample_source_span)
        assert not ok

    def test_empty_claims_rejected(self):
        obj = {
            "id": "claim:AAPL:FY2025:10K:test",
            "type": "ResearchClaim",
            "ticker": "AAPL",
            "source_document_id": "source:AAPL:FY2025:10K",
            "document_type": "10-K",
            "period": "FY2025",
            "claim_text": "Test claim",
            "claim_type": "factual",
            "supported_by_quotes": [],
            "confidence": "high",
            "schema_version": SCHEMA_VERSION,
        }
        ok, reason = validate_schema(obj)
        assert not ok  # min_length=1 on supported_by_quotes


class TestTypeDiscrimination:
    def test_risk_factor(self):
        obj = {
            "id": "risk:AAPL:FY2025:10K:supply-chain",
            "type": "RiskFactor",
            "ticker": "AAPL",
            "source_document_id": "source:AAPL:FY2025:10K",
            "document_type": "10-K",
            "period": "FY2025",
            "name": "Supply Chain Disruption",
            "category": "operational",
            "description": "Risk of supply chain disruption.",
            "supported_by_claims": ["claim:AAPL:FY2025:10K:supply-chain-risk"],
            "qualitative_impact": "negative",
            "confidence": "high",
            "schema_version": SCHEMA_VERSION,
        }
        ok, reason = validate_schema(obj)
        assert ok, f"Expected valid RiskFactor, got: {reason}"

    def test_growth_driver(self):
        obj = {
            "id": "growth_driver:AAPL:FY2025:10K:services-growth",
            "type": "GrowthDriver",
            "ticker": "AAPL",
            "source_document_id": "source:AAPL:FY2025:10K",
            "document_type": "10-K",
            "period": "FY2025",
            "name": "Services Revenue Growth",
            "category": "revenue",
            "description": "Strong growth in services segment.",
            "supported_by_claims": ["claim:1"],
            "qualitative_impact": "positive",
            "confidence": "high",
            "schema_version": SCHEMA_VERSION,
        }
        ok, reason = validate_schema(obj)
        assert ok, f"Expected valid GrowthDriver, got: {reason}"

    def test_headwind(self):
        obj = {
            "id": "headwind:AAPL:FY2025:10K:fx-pressure",
            "type": "Headwind",
            "ticker": "AAPL",
            "source_document_id": "source:AAPL:FY2025:10K",
            "document_type": "10-K",
            "period": "FY2025",
            "name": "Foreign Exchange Pressure",
            "category": "macro_economic",
            "description": "Strong USD pressure on international revenue.",
            "supported_by_quotes": ["quote:1"],
            "qualitative_impact": "negative",
            "confidence": "medium",
            "schema_version": SCHEMA_VERSION,
        }
        ok, reason = validate_schema(obj)
        assert ok, f"Expected valid Headwind, got: {reason}"


class TestUnknownType:
    def test_unknown_type_rejected(self):
        obj = {"type": "UnknownType", "id": "foo"}
        ok, reason = validate_schema(obj)
        assert not ok
        assert "Unknown" in reason
