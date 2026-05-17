"""Tests for support requirement validation."""

from __future__ import annotations

from krw_ontology.validators.support_validator import validate_has_support


class TestSupportedObjects:
    def test_business_factor_with_claims(self):
        obj = {
            "type": "BusinessFactor",
            "supported_by_claims": ["claim:1"],
            "supported_by_quotes": [],
        }
        ok, reason = validate_has_support(obj)
        assert ok

    def test_business_activity_with_quotes_only(self):
        obj = {
            "type": "BusinessActivity",
            "supported_by_claims": [],
            "supported_by_quotes": ["quote:1"],
        }
        ok, reason = validate_has_support(obj)
        assert ok

    def test_external_factor_with_both(self):
        obj = {
            "type": "ExternalFactorExposure",
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
    def test_business_factor_no_support(self):
        obj = {
            "type": "BusinessFactor",
            "supported_by_claims": [],
            "supported_by_quotes": [],
        }
        ok, reason = validate_has_support(obj)
        assert not ok

    def test_external_factor_no_support(self):
        obj = {
            "type": "ExternalFactorExposure",
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
