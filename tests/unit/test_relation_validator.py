"""Tests for relation whitelist validation."""

from __future__ import annotations

from krw_ontology.validators.relation_validator import validate_edge


class TestValidRelations:
    def test_whitelisted_relation_passes(self, relations_whitelist: list):
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
        assert ok, f"Expected pass, got: {reason}"

    def test_contains_relation(self, relations_whitelist: list):
        all_objects = {
            "source:1": {"id": "source:1", "type": "SourceDocument"},
            "span:1": {"id": "span:1", "type": "SourceSpan"},
        }
        edge = {
            "type": "Edge",
            "from_id": "source:1",
            "to_id": "span:1",
            "relation_name": "contains",
            "relation_id": "contains",
        }
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert ok, f"Expected pass, got: {reason}"


class TestInvalidRelations:
    def test_non_whitelisted_relation_fails(self, relations_whitelist: list):
        all_objects = {
            "quote:1": {"id": "quote:1", "type": "EvidenceQuote"},
            "risk:1": {"id": "risk:1", "type": "RiskFactor"},
        }
        edge = {
            "type": "Edge",
            "from_id": "quote:1",
            "to_id": "risk:1",
            "relation_name": "directly_describes",
            "relation_id": "directly_describes",
        }
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert not ok

    def test_wrong_from_type_fails(self, relations_whitelist: list):
        all_objects = {
            "claim:1": {"id": "claim:1", "type": "ResearchClaim"},
            "risk:1": {"id": "risk:1", "type": "RiskFactor"},
        }
        # supports is from EvidenceQuote -> ResearchClaim, not ResearchClaim -> RiskFactor
        edge = {
            "type": "Edge",
            "from_id": "claim:1",
            "to_id": "risk:1",
            "relation_name": "supports",
            "relation_id": "supports",
        }
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert not ok

    def test_wrong_relation_name_fails(self, relations_whitelist: list):
        all_objects = {
            "quote:1": {"id": "quote:1", "type": "EvidenceQuote"},
            "claim:1": {"id": "claim:1", "type": "ResearchClaim"},
        }
        edge = {
            "type": "Edge",
            "from_id": "quote:1",
            "to_id": "claim:1",
            "relation_name": "wrong_name",
            "relation_id": "supports",
        }
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert not ok

    def test_missing_from_id(self, relations_whitelist: list):
        edge = {
            "type": "Edge",
            "from_id": "nonexistent",
            "to_id": "claim:1",
            "relation_name": "supports",
            "relation_id": "supports",
        }
        ok, reason = validate_edge(edge, relations_whitelist, {})
        assert not ok

    def test_missing_to_id(self, relations_whitelist: list):
        edge = {
            "type": "Edge",
            "from_id": "quote:1",
            "to_id": "nonexistent",
            "relation_name": "supports",
            "relation_id": "supports",
        }
        all_objects = {"quote:1": {"id": "quote:1", "type": "EvidenceQuote"}}
        ok, reason = validate_edge(edge, relations_whitelist, all_objects)
        assert not ok

    def test_non_edge_passes(self, relations_whitelist: list):
        obj = {"type": "ResearchClaim"}
        ok, reason = validate_edge(obj, relations_whitelist, {})
        assert ok
