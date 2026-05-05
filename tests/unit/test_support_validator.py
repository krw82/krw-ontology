"""Tests for support requirement validation."""

from __future__ import annotations

from krw_ontology.validators.support_validator import validate_has_support


class TestSupportedObjects:
    def test_risk_factor_with_claims(self):
        obj = {
            "type": "RiskFactor",
            "supported_by_claims": ["claim:1"],
            "supported_by_quotes": [],
        }
        ok, reason = validate_has_support(obj)
        assert ok

    def test_growth_driver_with_quotes_only(self):
        obj = {
            "type": "GrowthDriver",
            "supported_by_claims": [],
            "supported_by_quotes": ["quote:1"],
        }
        ok, reason = validate_has_support(obj)
        assert ok

    def test_headwind_with_both(self):
        obj = {
            "type": "Headwind",
            "supported_by_claims": ["claim:1"],
            "supported_by_quotes": ["quote:1"],
        }
        ok, reason = validate_has_support(obj)
        assert ok

    def test_assumption_with_claims(self):
        obj = {
            "type": "AssumptionCandidate",
            "supported_by_claims": ["claim:1"],
        }
        ok, reason = validate_has_support(obj)
        assert ok


class TestUnsupportedObjects:
    def test_risk_no_support(self):
        obj = {
            "type": "RiskFactor",
            "supported_by_claims": [],
            "supported_by_quotes": [],
        }
        ok, reason = validate_has_support(obj)
        assert not ok

    def test_headwind_no_support(self):
        obj = {
            "type": "Headwind",
            "supported_by_claims": None,
            "supported_by_quotes": None,
        }
        ok, reason = validate_has_support(obj)
        assert not ok

    def test_assumption_no_support(self):
        obj = {
            "type": "AssumptionCandidate",
            "supported_by_claims": [],
            "supported_by_quotes": [],
        }
        ok, reason = validate_has_support(obj)
        assert not ok


class TestExcludedTypes:
    def test_evidence_quote_excluded(self):
        obj = {"type": "EvidenceQuote"}
        ok, reason = validate_has_support(obj)
        assert ok

    def test_source_span_excluded(self):
        obj = {"type": "SourceSpan"}
        ok, reason = validate_has_support(obj)
        assert ok

    def test_research_claim_excluded(self):
        obj = {"type": "ResearchClaim"}
        ok, reason = validate_has_support(obj)
        assert ok

    def test_edge_excluded(self):
        obj = {"type": "Edge"}
        ok, reason = validate_has_support(obj)
        assert ok
