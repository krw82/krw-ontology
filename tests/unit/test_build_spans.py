"""Tests for SourceSpan ID stability."""

from __future__ import annotations

from pathlib import Path

from krw_ontology.pipeline.stages.build_spans import build_spans


def test_repeated_section_names_produce_unique_span_ids(tmp_path: Path):
    sections = [
        {
            "name": "item1",
            "section_key": "item1_00",
            "section_instance": 0,
            "text": "Item 1. Business\n\nFirst block.",
        },
        {
            "name": "item1",
            "section_key": "item1_01",
            "section_instance": 1,
            "text": "Item 1. Business\n\nSecond block.",
        },
        {
            "name": "item1a",
            "section_key": "item1a_00",
            "section_instance": 0,
            "text": "Item 1A. Risk Factors\n\nRisk block.",
        },
    ]
    clean_md_text = "\n\n".join(section["text"] for section in sections)

    result = build_spans(
        sections=sections,
        doc_type_key="10K",
        ticker="AAPL",
        period="FY2025",
        source_document_id="source:AAPL:FY2025:10K",
        clean_md_text=clean_md_text,
        output_path=tmp_path / "spans.jsonl",
    )

    ids = [span["id"] for span in result["spans"]]
    assert len(ids) == len(set(ids))
    assert "span:AAPL:FY2025:10K:item1_00:0000" in ids
    assert "span:AAPL:FY2025:10K:item1_01:0000" in ids
    assert all(span["section_name"] in {"item1", "item1a"} for span in result["spans"])
