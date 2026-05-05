"""Tests for reference integrity validation."""

from __future__ import annotations

from krw_ontology.validators.reference_validator import validate_references


def _make_objects(*objs: dict) -> dict[str, dict]:
    return {o["id"]: o for o in objs}


class TestValidReferences:
    def test_valid_quote_reference(self, sample_source_span: dict, sample_evidence_quote: dict):
        all_objs = _make_objects(sample_source_span, sample_evidence_quote)
        ok, reason = validate_references(sample_evidence_quote, all_objs)
        assert ok, f"Expected pass, got: {reason}"

    def test_valid_claim_reference(self, sample_evidence_quote: dict, sample_research_claim: dict):
        all_objs = _make_objects(sample_evidence_quote, sample_research_claim)
        ok, reason = validate_references(sample_research_claim, all_objs)
        assert ok, f"Expected pass, got: {reason}"


class TestDanglingReferences:
    def test_dangling_quote_id(self, sample_research_claim: dict):
        all_objs = _make_objects(sample_research_claim)
        ok, reason = validate_references(sample_research_claim, all_objs)
        assert not ok
        assert "Dangling" in reason

    def test_dangling_claim_id(self):
        risk = {
            "id": "risk:AAPL:FY2025:10K:test",
            "type": "RiskFactor",
            "supported_by_claims": ["claim:nonexistent"],
        }
        all_objs = _make_objects(risk)
        ok, reason = validate_references(risk, all_objs)
        assert not ok

    def test_dangling_from_id_in_edge(self):
        edge = {
            "id": "edge:supports:abc123",
            "type": "Edge",
            "from_id": "quote:nonexistent",
            "to_id": "claim:nonexistent",
            "relation_name": "supports",
            "relation_id": "supports",
        }
        all_objs = _make_objects(edge)
        ok, reason = validate_references(edge, all_objs)
        assert not ok

    def test_multiple_dangling_refs_reported(self):
        claim = {
            "id": "claim:AAPL:FY2025:10K:test",
            "type": "ResearchClaim",
            "supported_by_quotes": ["quote:missing1", "quote:missing2"],
        }
        all_objs = _make_objects(claim)
        ok, reason = validate_references(claim, all_objs)
        assert not ok
        assert "missing1" in reason
        assert "missing2" in reason

    def test_dangling_span_id(self):
        quote = {
            "id": "quote:AAPL:FY2025:10K:item1a:0042:001",
            "type": "EvidenceQuote",
            "source_span_id": "span:nonexistent",
        }
        all_objs = _make_objects(quote)
        ok, reason = validate_references(quote, all_objs)
        assert not ok

    def test_dangling_source_quote_id(self):
        signal = {
            "id": "signal:AAPL:FY2025:10K:item1a:0042:001",
            "type": "LanguageSignal",
            "source_quote_id": "quote:nonexistent",
        }
        all_objs = _make_objects(signal)
        ok, reason = validate_references(signal, all_objs)
        assert not ok

    def test_dangling_calculated_numeric_input(self):
        support = {
            "id": "calculated_numeric:AAPL:FY2025:10K:test",
            "type": "CalculatedNumericSupport",
            "input_object_ids": ["financial_metric:missing"],
        }
        all_objs = _make_objects(support)
        ok, reason = validate_references(support, all_objs)
        assert not ok
        assert "financial_metric:missing" in reason

    def test_dangling_numeric_evidence_source(self):
        evidence = {
            "id": "numeric_evidence:AAPL:FY2025:10K:test",
            "type": "NumericEvidence",
            "source_object_id": "quote:missing",
            "source_quote_id": "quote:missing",
            "input_object_ids": [],
        }
        all_objs = _make_objects(evidence)
        ok, reason = validate_references(evidence, all_objs)
        assert not ok
        assert "quote:missing" in reason


class TestMetricEdgeReferences:
    def test_edge_to_metric_revenue_passes(self):
        edge = {
            "id": "edge:AAPL:FY2025:10K:affects_risk:abc1234567",
            "type": "Edge",
            "from_id": "risk:AAPL:FY2025:10K:test-risk",
            "to_id": "metric:revenue",
            "relation_name": "affects",
            "relation_id": "affects_risk",
        }
        risk = {
            "id": "risk:AAPL:FY2025:10K:test-risk",
            "type": "RiskFactor",
        }
        # metric:revenue registered as virtual Metric object
        metric = {"id": "metric:revenue", "type": "Metric"}
        all_objs = _make_objects(edge, risk, metric)
        ok, reason = validate_references(edge, all_objs)
        assert ok, f"Expected pass, got: {reason}"

    def test_edge_to_metric_without_metric_registered(self):
        edge = {
            "id": "edge:AAPL:FY2025:10K:affects_risk:abc1234567",
            "type": "Edge",
            "from_id": "risk:AAPL:FY2025:10K:test-risk",
            "to_id": "metric:revenue",
            "relation_name": "affects",
            "relation_id": "affects_risk",
        }
        risk = {
            "id": "risk:AAPL:FY2025:10K:test-risk",
            "type": "RiskFactor",
        }
        all_objs = _make_objects(edge, risk)
        ok, reason = validate_references(edge, all_objs)
        assert not ok
        assert "metric:revenue" in reason

    def test_non_metric_edge_still_passes(self, sample_evidence_quote: dict, sample_research_claim: dict):
        edge = {
            "id": "edge:AAPL:FY2025:10K:supports:xyz1234567",
            "type": "Edge",
            "from_id": sample_evidence_quote["id"],
            "to_id": sample_research_claim["id"],
            "relation_name": "supports",
            "relation_id": "supports",
        }
        all_objs = _make_objects(sample_evidence_quote, sample_research_claim, edge)
        ok, reason = validate_references(edge, all_objs)
        assert ok, f"Expected pass, got: {reason}"


class TestNonReferencingTypes:
    def test_source_span_passes(self, sample_source_span: dict):
        all_objs = _make_objects(sample_source_span)
        ok, reason = validate_references(sample_source_span, all_objs)
        assert ok
