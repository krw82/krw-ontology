"""Tests for metric mapping validation."""

from __future__ import annotations

from krw_ontology.validators.metric_validator import validate_metric_fields


class TestCanonicalMetrics:
    def test_canonical_metric_passes(self, metric_dictionary: dict):
        canonical = set(metric_dictionary["canonical_metrics"].keys())
        obj = {
            "type": "RiskFactor",
            "affects": ["revenue", "gross_margin"],
            "related_metrics": ["revenue_growth"],
        }
        ok, reason = validate_metric_fields(obj, canonical)
        assert ok
        assert obj["affects"] == ["revenue", "gross_margin"]
        assert obj["related_metrics"] == ["revenue_growth"]

    def test_unknown_metric_moved_to_unmapped(self, metric_dictionary: dict):
        canonical = set(metric_dictionary["canonical_metrics"].keys())
        obj = {
            "type": "RiskFactor",
            "affects": ["revenue", "debt_to_equity"],
            "related_metrics": None,
        }
        ok, reason = validate_metric_fields(obj, canonical)
        assert ok  # Never rejects, only flags
        assert "debt_to_equity" in obj.get("unmapped_metrics", [])
        assert "revenue" in obj["affects"]
        assert "debt_to_equity" not in obj["affects"]
        assert obj["review_status"] == "needs_review"

    def test_multiple_unknown_metrics(self, metric_dictionary: dict):
        canonical = set(metric_dictionary["canonical_metrics"].keys())
        obj = {
            "type": "GrowthDriver",
            "affects": ["current_ratio", "net_debt", "debt_to_equity"],
            "related_metrics": ["enterprise_value"],
        }
        ok, reason = validate_metric_fields(obj, canonical)
        assert ok
        assert len(obj["unmapped_metrics"]) == 4
        assert obj["review_status"] == "needs_review"

    def test_empty_metrics_fields(self, metric_dictionary: dict):
        canonical = set(metric_dictionary["canonical_metrics"].keys())
        obj = {
            "type": "RiskFactor",
            "affects": None,
            "related_metrics": None,
        }
        ok, reason = validate_metric_fields(obj, canonical)
        assert ok
        assert "unmapped_metrics" not in obj

    def test_no_metric_fields(self, metric_dictionary: dict):
        canonical = set(metric_dictionary["canonical_metrics"].keys())
        obj = {"type": "EvidenceQuote"}
        ok, reason = validate_metric_fields(obj, canonical)
        assert ok
