"""Tests for ID generation utilities (Section 5)."""

from __future__ import annotations

from krw_ontology.config.constants import denormalize_doc_type, normalize_doc_type
from krw_ontology.schema.id_utils import (
    generate_edge_local_id,
    generate_metric_id,
    generate_scoped_id,
    generate_signal_local_id,
    generate_source_document_id,
    generate_span_local_id,
    generate_quote_local_id,
    generate_xbrl_local_id,
)


class TestDocTypeMapping:
    def test_normalize_10k(self):
        assert normalize_doc_type("10-K") == "10K"

    def test_normalize_10q(self):
        assert normalize_doc_type("10-Q") == "10Q"

    def test_denormalize_10k(self):
        assert denormalize_doc_type("10K") == "10-K"

    def test_denormalize_10q(self):
        assert denormalize_doc_type("10Q") == "10-Q"

    def test_roundtrip(self):
        assert denormalize_doc_type(normalize_doc_type("10-K")) == "10-K"


class TestSourceDocumentId:
    def test_generates_correct_id(self):
        result = generate_source_document_id("AAPL", "FY2025", "10K")
        assert result == "source:AAPL:FY2025:10K"


class TestScopedId:
    def test_generates_scoped_id(self):
        result = generate_scoped_id("risk", "AAPL", "FY2025", "10K", "supply-chain")
        assert result == "risk:AAPL:FY2025:10K:supply-chain"

    def test_span_id(self):
        result = generate_scoped_id("span", "AAPL", "FY2025", "10K", "item1a:0042")
        assert result == "span:AAPL:FY2025:10K:item1a:0042"

    def test_quote_id(self):
        result = generate_scoped_id("quote", "AAPL", "FY2025", "10K", "item7a:0089:001")
        assert result == "quote:AAPL:FY2025:10K:item7a:0089:001"


class TestMetricId:
    def test_generates_metric_id(self):
        assert generate_metric_id("gross_margin") == "metric:gross_margin"

    def test_metric_id_no_scope(self):
        result = generate_metric_id("revenue")
        assert "AAPL" not in result
        assert result.startswith("metric:")


class TestXBRLLocalId:
    def test_xbrl_local_id_format(self):
        result = generate_xbrl_local_id("GrossProfit", "ctx1", "USD", "50000000")
        assert result.startswith("GrossProfit:")
        parts = result.split(":")
        assert len(parts) == 2
        assert len(parts[1]) == 8

    def test_xbrl_local_id_deterministic(self):
        r1 = generate_xbrl_local_id("GrossProfit", "ctx1", "USD", "50000000")
        r2 = generate_xbrl_local_id("GrossProfit", "ctx1", "USD", "50000000")
        assert r1 == r2

    def test_xbrl_local_id_differs_on_context(self):
        r1 = generate_xbrl_local_id("GrossProfit", "ctx1", "USD", "50000000")
        r2 = generate_xbrl_local_id("GrossProfit", "ctx2", "USD", "50000000")
        assert r1 != r2


class TestEdgeLocalId:
    def test_edge_local_id_format(self):
        result = generate_edge_local_id("supports", "quote:1", "claim:1")
        assert result.startswith("supports:")
        parts = result.split(":")
        assert len(parts) == 2
        assert len(parts[1]) == 10

    def test_edge_local_id_deterministic(self):
        r1 = generate_edge_local_id("supports", "q1", "c1")
        r2 = generate_edge_local_id("supports", "q1", "c1")
        assert r1 == r2

    def test_edge_local_id_differs_on_endpoints(self):
        r1 = generate_edge_local_id("supports", "q1", "c1")
        r2 = generate_edge_local_id("supports", "q1", "c2")
        assert r1 != r2


class TestSpanLocalId:
    def test_span_local_id(self):
        assert generate_span_local_id("item1a", 42) == "item1a:0042"

    def test_span_local_id_zero_padded(self):
        assert generate_span_local_id("item7", 1) == "item7:0001"


class TestQuoteLocalId:
    def test_quote_local_id(self):
        result = generate_quote_local_id("item1a", 42, 1)
        assert result == "item1a:0042:001"


class TestSignalLocalId:
    def test_signal_local_id(self):
        result = generate_signal_local_id("item1a", 42, 3)
        assert result == "item1a:0042:003"
