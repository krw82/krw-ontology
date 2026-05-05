"""Tests for numeric evidence ledger build stage."""

from __future__ import annotations

from pathlib import Path

from krw_ontology.pipeline.stages.build_numeric_evidence import build_numeric_evidence
from krw_ontology.utils.io import read_jsonl, write_jsonl


def test_build_numeric_evidence_writes_quote_and_metric_rows(tmp_path: Path):
    ontology_dir = tmp_path / "ontology"
    quote_id = "quote:AAPL:FY2025:10K:item7:0001:001"
    write_jsonl(
        ontology_dir / "evidence_quotes.jsonl",
        [{
            "id": quote_id,
            "type": "EvidenceQuote",
            "quote_text": (
                "| Period | Shares Purchased (In millions) | Average Price Paid per Share | "
                "Approximate Dollar Value Remaining Under the Program (In billions) |\n"
                "| October 27 to November 23, 2025 | 5.6 | $198.89 | $61.1 |"
            ),
        }],
    )
    write_jsonl(
        ontology_dir / "derived_metric_values.jsonl",
        [{
            "id": "derived_metric:AAPL:FY2025:10K:gross_margin",
            "type": "DerivedMetricValue",
            "value": 46.9,
            "unit": "percent",
            "formula": "gross_profit / revenue",
            "input_metric_ids": [],
        }],
    )

    result = build_numeric_evidence(
        ontology_dir=ontology_dir,
        ticker="AAPL",
        period="FY2025",
        doc_type_key="10K",
        document_type="10-K",
        source_document_id="source:AAPL:FY2025:10K",
    )

    rows = read_jsonl(result["output_path"])
    assert rows
    assert any(row["source_quote_id"] == quote_id and row["value"] == 61_100_000_000 for row in rows)
    assert any(row["source_method"] == "derived_metric_value" and row["numeric_kind"] == "percent" for row in rows)


def test_build_numeric_evidence_omits_structural_regulatory_codes(tmp_path: Path):
    ontology_dir = tmp_path / "ontology"
    write_jsonl(
        ontology_dir / "evidence_quotes.jsonl",
        [{
            "id": "quote:1",
            "type": "EvidenceQuote",
            "quote_text": "Exports to Country Groups D:1, D:4, and D:5 require updated licensing.",
        }],
    )

    build_numeric_evidence(
        ontology_dir=ontology_dir,
        ticker="NVDA",
        period="FY2026",
        doc_type_key="10K",
        document_type="10-K",
        source_document_id="source:NVDA:FY2026:10K",
    )

    rows = read_jsonl(ontology_dir / "numeric_evidence.jsonl")
    assert rows == []
