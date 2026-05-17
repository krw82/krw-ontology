"""Tests for relation whitelist validation."""

from __future__ import annotations

from krw_ontology.validators.relation_validator import validate_edge


def _edge(**overrides):
    base = {
        "type": "Edge",
        "edge_class": "evidence",
        "evidence_level": "direct",
        "generation_method": "deterministic_reference",
        "rationale": "test edge",
    }
    base.update(overrides)
    return base


class TestValidRelations:
    def test_whitelisted_relation_passes(self, relations_whitelist: list):
        all_objects = {
            "quote:1": {"id": "quote:1", "type": "EvidenceQuote"},
            "claim:1": {"id": "claim:1", "type": "ResearchClaim"},
        }
        edge = _edge(
            from_id="quote:1",
            to_id="claim:1",
            relation_name="supports",
            relation_id="supports",
        )
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert ok, f"Expected pass, got: {reason}"

    def test_contains_relation(self, relations_whitelist: list):
        all_objects = {
            "source:1": {"id": "source:1", "type": "SourceDocument"},
            "span:1": {"id": "span:1", "type": "SourceSpan"},
        }
        edge = _edge(
            edge_class="source_structure",
            from_id="source:1",
            to_id="span:1",
            relation_name="contains",
            relation_id="contains",
        )
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert ok, f"Expected pass, got: {reason}"

    def test_union_relation_passes(self, relations_whitelist: list):
        all_objects = {
            "event:1": {"id": "event:1", "type": "ChangeEvent"},
            "exposure:1": {"id": "exposure:1", "type": "ExternalFactorExposure"},
        }
        edge = _edge(
            edge_class="event",
            evidence_level="derived",
            from_id="event:1",
            to_id="exposure:1",
            relation_name="affects_object",
            relation_id="affects_object",
        )
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert ok, f"Expected pass, got: {reason}"


class TestInvalidRelations:
    def test_non_whitelisted_relation_fails(self, relations_whitelist: list):
        all_objects = {
            "quote:1": {"id": "quote:1", "type": "EvidenceQuote"},
            "risk:1": {"id": "risk:1", "type": "RiskFactor"},
        }
        edge = _edge(
            from_id="quote:1",
            to_id="risk:1",
            relation_name="directly_describes",
            relation_id="directly_describes",
        )
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert not ok

    def test_wrong_from_type_fails(self, relations_whitelist: list):
        all_objects = {
            "claim:1": {"id": "claim:1", "type": "ResearchClaim"},
            "risk:1": {"id": "risk:1", "type": "RiskFactor"},
        }
        # supports is from EvidenceQuote -> ResearchClaim, not ResearchClaim -> RiskFactor
        edge = _edge(
            from_id="claim:1",
            to_id="risk:1",
            relation_name="supports",
            relation_id="supports",
        )
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert not ok

    def test_wrong_relation_name_fails(self, relations_whitelist: list):
        all_objects = {
            "quote:1": {"id": "quote:1", "type": "EvidenceQuote"},
            "claim:1": {"id": "claim:1", "type": "ResearchClaim"},
        }
        edge = _edge(
            from_id="quote:1",
            to_id="claim:1",
            relation_name="wrong_name",
            relation_id="supports",
        )
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert not ok

    def test_missing_from_id(self, relations_whitelist: list):
        edge = _edge(
            from_id="nonexistent",
            to_id="claim:1",
            relation_name="supports",
            relation_id="supports",
        )
        ok, reason = validate_edge(edge, relations_whitelist, {})
        assert not ok

    def test_missing_to_id(self, relations_whitelist: list):
        edge = _edge(
            from_id="quote:1",
            to_id="nonexistent",
            relation_name="supports",
            relation_id="supports",
        )
        all_objects = {"quote:1": {"id": "quote:1", "type": "EvidenceQuote"}}
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert not ok

    def test_missing_edge_metadata_fails(self, relations_whitelist: list):
        all_objects = {
            "quote:1": {"id": "quote:1", "type": "EvidenceQuote"},
            "claim:1": {"id": "claim:1", "type": "ResearchClaim"},
        }
        edge = {
            "type": "Edge",
            "from_id": "quote:1",
            "to_id": "claim:1",
            "relation_name": "supports",
            "relation_id": "supports",
        }
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert not ok
        assert "metadata" in reason

    def test_same_type_required_fails_for_mixed_temporal_link(self, relations_whitelist: list):
        all_objects = {
            "business_factor:1": {"id": "business_factor:1", "type": "BusinessFactor"},
            "external_factor_exposure:1": {"id": "external_factor_exposure:1", "type": "ExternalFactorExposure"},
        }
        edge = _edge(
            edge_class="temporal",
            evidence_level="inferred",
            from_id="business_factor:1",
            to_id="external_factor_exposure:1",
            relation_name="continues_as",
            relation_id="continues_as",
        )
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert not ok
        assert "same endpoint type" in reason

    def test_non_edge_passes(self, relations_whitelist: list):
        obj = {"type": "ResearchClaim"}
        ok, reason = validate_edge(obj, relations_whitelist, {})
        assert ok
