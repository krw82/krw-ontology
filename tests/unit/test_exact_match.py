"""Tests for quote text exact match validation."""

from __future__ import annotations

from krw_ontology.validators.exact_match import validate_exact_match


class TestExactMatch:
    def test_exact_substring_passes(self, sample_evidence_quote: dict, sample_source_span: dict):
        all_spans = {sample_source_span["id"]: sample_source_span}
        ok, reason = validate_exact_match(sample_evidence_quote, all_spans)
        assert ok, f"Expected pass, got: {reason}"

    def test_whitespace_normalization_passes(self, sample_source_span: dict):
        quote = {
            "type": "EvidenceQuote",
            "source_span_id": sample_source_span["id"],
            "quote_text": "Any  disruption  in  the  supply  chain  could  materially  adversely  affect  the  Company's  business.",
        }
        all_spans = {sample_source_span["id"]: sample_source_span}
        ok, reason = validate_exact_match(quote, all_spans)
        assert ok, f"Expected pass with whitespace normalization, got: {reason}"

    def test_nonexistent_text_fails(self, sample_source_span: dict):
        quote = {
            "type": "EvidenceQuote",
            "source_span_id": sample_source_span["id"],
            "quote_text": "This text does not exist anywhere in the source span.",
        }
        all_spans = {sample_source_span["id"]: sample_source_span}
        ok, reason = validate_exact_match(quote, all_spans)
        assert not ok

    def test_nonexistent_span_id_fails(self):
        quote = {
            "type": "EvidenceQuote",
            "source_span_id": "span:nonexistent:9999",
            "quote_text": "Some text",
        }
        ok, reason = validate_exact_match(quote, {})
        assert not ok

    def test_non_quote_type_passes(self):
        obj = {"type": "ResearchClaim", "claim_text": "Something"}
        ok, reason = validate_exact_match(obj, {})
        assert ok

    def test_empty_quote_text_fails(self, sample_source_span: dict):
        quote = {
            "type": "EvidenceQuote",
            "source_span_id": sample_source_span["id"],
            "quote_text": "",
        }
        all_spans = {sample_source_span["id"]: sample_source_span}
        ok, reason = validate_exact_match(quote, all_spans)
        assert not ok

    def test_missing_span_id_fails(self):
        quote = {
            "type": "EvidenceQuote",
            "quote_text": "Some text",
        }
        ok, reason = validate_exact_match(quote, {})
        assert not ok
