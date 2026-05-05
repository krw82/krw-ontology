"""Regression tests for metric edges through the full validator chain."""

from __future__ import annotations

from pathlib import Path
import logging

from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology
from krw_ontology.utils.io import read_jsonl, write_jsonl


def test_metric_edge_survives_full_validation(tmp_path: Path):
    base = {
        "ticker": "AAPL",
        "source_document_id": "source:AAPL:FY2025:10K",
        "document_type": "10-K",
        "period": "FY2025",
        "schema_version": "0.1.0",
    }
    span_id = "span:AAPL:FY2025:10K:item1a:0000"
    quote_id = "quote:AAPL:FY2025:10K:item1a:0000:001"
    risk_id = "risk:AAPL:FY2025:10K:test-risk"
    edge_id = "edge:AAPL:FY2025:10K:affects_risk:abc1234567"

    write_jsonl(tmp_path / "spans.jsonl", [{
        "id": span_id,
        "type": "SourceSpan",
        **base,
        "section_name": "item1a",
        "section_number": "item1a",
        "span_index": 0,
        "start_char": 0,
        "end_char": 40,
        "text": "Revenue risk could affect performance.",
        "text_hash": "sha256:test",
        "char_count": 38,
        "section_detection_confidence": "high",
        "section_detection_method": "regex",
    }])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [{
        "id": quote_id,
        "type": "EvidenceQuote",
        **base,
        "source_span_id": span_id,
        "quote_text": "Revenue risk could affect performance.",
        "quote_type": "risk_language",
        "section_name": "item1a",
        "confidence": "high",
        "review_status": "accepted",
    }])
    write_jsonl(tmp_path / "risks.jsonl", [{
        "id": risk_id,
        "type": "RiskFactor",
        **base,
        "name": "Revenue Risk",
        "category": "competitive",
        "description": "Revenue risk could affect performance.",
        "supported_by_quotes": [quote_id],
        "affects": ["revenue"],
        "qualitative_impact": "negative",
        "confidence": "high",
        "review_status": "accepted",
    }])
    write_jsonl(tmp_path / "edges.jsonl", [{
        "id": edge_id,
        "type": "Edge",
        **base,
        "from_id": risk_id,
        "to_id": "metric:revenue",
        "relation_name": "affects",
        "relation_id": "affects_risk",
        "confidence": "high",
        "review_status": "accepted",
    }])

    result = run_validate_ontology(tmp_path)

    assert result["accepted"].get("Edge") == 1
    assert result["stats"]["total_rejected"] == 0
    assert read_jsonl(tmp_path / "edges.jsonl")[0]["id"] == edge_id
    assert not read_jsonl(tmp_path / "rejected_objects.jsonl")


def test_pre_edge_validation_ignores_and_clears_stale_edges(tmp_path: Path):
    base = {
        "ticker": "AAPL",
        "source_document_id": "source:AAPL:FY2025:10K",
        "document_type": "10-K",
        "period": "FY2025",
        "schema_version": "0.1.0",
    }
    write_jsonl(tmp_path / "edges.jsonl", [{
        "id": "edge:AAPL:FY2025:10K:supports:deadbeef00",
        "type": "Edge",
        **base,
        "from_id": "quote:missing",
        "to_id": "claim:missing",
        "relation_name": "supports",
        "relation_id": "supports",
        "confidence": "high",
        "review_status": "accepted",
    }])

    result = run_validate_ontology(tmp_path, include_edges=False)

    assert result["stats"]["total_rejected"] == 0
    assert read_jsonl(tmp_path / "edges.jsonl") == []
    assert read_jsonl(tmp_path / "rejected_objects.jsonl") == []


def test_validation_logs_schema_counts_and_stage(tmp_path: Path):
    records: list[logging.LogRecord] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    base = {
        "ticker": "AAPL",
        "source_document_id": "source:AAPL:FY2025:10K",
        "document_type": "10-K",
        "period": "FY2025",
        "schema_version": "0.1.0",
    }
    write_jsonl(tmp_path / "spans.jsonl", [{
        "id": "span:AAPL:FY2025:10K:item1a:0000",
        "type": "SourceSpan",
        **base,
        "section_name": "item1a",
        "section_number": "item1a",
        "span_index": 0,
        "start_char": 0,
        "end_char": 40,
        "text": "Revenue risk could affect performance.",
        "text_hash": "sha256:test",
        "char_count": 38,
        "section_detection_confidence": "high",
        "section_detection_method": "document_node_boundary_scoring",
    }])

    logger = logging.getLogger("krw_ontology")
    original_level = logger.level
    logger.setLevel(logging.INFO)
    handler = ListHandler()
    logger.addHandler(handler)
    try:
        run_validate_ontology(tmp_path, include_edges=False)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)

    schema_records = [
        record for record in records
        if record.getMessage().startswith("Schema validation:")
    ]
    assert schema_records
    assert "0 passed, 0 rejected" not in schema_records[0].getMessage()
    assert all(getattr(record, "stage") == "validate_ontology" for record in schema_records)
    assert any(
        record.getMessage().startswith("Loaded 1 objects")
        and getattr(record, "stage") == "validate_ontology"
        for record in records
    )
