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


def test_build_spans_uses_explicit_section_offsets_for_duplicate_text(tmp_path: Path):
    repeated = "Item 1. Business\n\nRepeated block."
    clean_md_text = f"{repeated}\n\n{repeated}"
    second_start = clean_md_text.rfind(repeated)
    sections = [
        {
            "name": "item1",
            "section_key": "item1_00",
            "section_instance": 0,
            "start_char": 0,
            "text": repeated,
        },
        {
            "name": "item1",
            "section_key": "item1_01",
            "section_instance": 1,
            "start_char": second_start,
            "text": repeated,
        },
    ]

    result = build_spans(
        sections=sections,
        doc_type_key="10K",
        ticker="AAPL",
        period="FY2025",
        source_document_id="source:AAPL:FY2025:10K",
        clean_md_text=clean_md_text,
        output_path=tmp_path / "spans.jsonl",
    )

    starts = {span["section_key"]: span["start_char"] for span in result["spans"]}
    assert starts["item1_00"] == 0
    assert starts["item1_01"] == second_start


def test_build_spans_respects_10q_document_type(tmp_path: Path):
    section_text = "Item 2. Management's Discussion and Analysis\n\nRevenue increased during the quarter."
    result = build_spans(
        sections=[
            {
                "name": "part1_item2",
                "section_key": "part1_item2_00",
                "section_instance": 0,
                "start_char": 0,
                "text": section_text,
            }
        ],
        doc_type_key="10Q",
        ticker="AAPL",
        period="FY2025Q2",
        source_document_id="source:AAPL:FY2025Q2:10Q",
        clean_md_text=section_text,
        output_path=tmp_path / "spans.jsonl",
        document_type="10-Q",
    )

    span = result["spans"][0]
    assert span["id"] == "span:AAPL:FY2025Q2:10Q:part1_item2_00:0000"
    assert span["document_type"] == "10-Q"
    assert span["source_document_id"] == "source:AAPL:FY2025Q2:10Q"
