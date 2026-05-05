"""Tests for quote candidate selection extraction."""

from __future__ import annotations

import asyncio
import json

from krw_ontology.pipeline.stages.extract_evidence_quotes import (
    _build_quote_candidates,
    extract_evidence_quotes,
)
from krw_ontology.utils.io import read_jsonl, write_jsonl


def test_build_quote_candidates_preserves_exact_offsets():
    span_text = (
        "Short. The Company faces intense competition in all areas of its "
        "business, which could adversely affect sales and margins."
    )
    span = {
        "id": "span:AAPL:FY2025:10K:item1a:0001",
        "section_name": "item1a",
        "start_char": 100,
        "text": span_text,
    }

    candidates = _build_quote_candidates([span])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["text"] == span_text[candidate["start_char"]:candidate["end_char"]]
    assert candidate["absolute_start_char"] == 100 + candidate["start_char"]
    assert candidate["candidate_id"] == "span:AAPL:FY2025:10K:item1a:0001:cand:001"


def test_extract_evidence_quotes_uses_candidate_text(tmp_path):
    ontology_dir = tmp_path / "ontology"
    span_text = (
        "The Company faces intense competition in all areas of its business, "
        "which could adversely affect sales and margins."
    )
    write_jsonl(
        ontology_dir / "spans.jsonl",
        [
            {
                "id": "span:AAPL:FY2025:10K:item1a:0001",
                "type": "SourceSpan",
                "ticker": "AAPL",
                "source_document_id": "source:AAPL:FY2025:10K",
                "document_type": "10-K",
                "period": "FY2025",
                "section_name": "item1a",
                "section_number": "1A",
                "span_index": 1,
                "start_char": 100,
                "end_char": 100 + len(span_text),
                "text": span_text,
                "text_hash": "sha256:test",
                "char_count": len(span_text),
                "section_detection_confidence": "high",
                "section_detection_method": "test",
                "schema_version": "0.1.0",
            }
        ],
    )

    class FakeWorker:
        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            return [
                {
                    "candidate_id": "span:AAPL:FY2025:10K:item1a:0001:cand:001",
                    "quote_type": "competitive_pressure",
                    "section_name": "item1a",
                    "confidence": "high",
                    "language_signals": [{"signal_type": "potential_negative"}],
                }
            ]

    quotes = asyncio.run(
        extract_evidence_quotes(
            worker=FakeWorker(),
            ontology_dir=ontology_dir,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    assert quotes[0]["quote_text"] == span_text
    assert quotes[0]["start_char"] == 0
    assert quotes[0]["end_char"] == len(span_text)
    assert quotes[0]["quote_text"] in read_jsonl(ontology_dir / "spans.jsonl")[0]["text"]


def test_extract_evidence_quotes_splits_failed_batch(tmp_path):
    ontology_dir = tmp_path / "ontology"
    spans = []
    for idx in range(2):
        span_text = (
            f"The Company faces intense competition in market {idx}, which could "
            "adversely affect sales and margins over time."
        )
        spans.append({
            "id": f"span:AAPL:FY2025:10K:item1a:{idx:04d}",
            "type": "SourceSpan",
            "ticker": "AAPL",
            "source_document_id": "source:AAPL:FY2025:10K",
            "document_type": "10-K",
            "period": "FY2025",
            "section_name": "item1a",
            "section_number": "1A",
            "span_index": idx,
            "start_char": idx * 1000,
            "end_char": idx * 1000 + len(span_text),
            "text": span_text,
            "text_hash": "sha256:test",
            "char_count": len(span_text),
            "section_detection_confidence": "high",
            "section_detection_method": "test",
            "schema_version": "0.1.0",
        })
    write_jsonl(ontology_dir / "spans.jsonl", spans)

    class FakeWorker:
        def __init__(self):
            self.calls = []

        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            candidates = json.loads(input_data["candidates_json"])
            self.calls.append(len(candidates))
            if len(candidates) > 1:
                raise TimeoutError("too large")
            return [
                {
                    "candidate_id": candidates[0]["candidate_id"],
                    "quote_type": "competitive_pressure",
                    "section_name": "item1a",
                    "confidence": "high",
                }
            ]

    worker = FakeWorker()
    quotes = asyncio.run(
        extract_evidence_quotes(
            worker=worker,
            ontology_dir=ontology_dir,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    assert worker.calls == [2, 1, 1]
    assert len(quotes) == 2
    assert not read_jsonl(ontology_dir / "batch_failures.jsonl")
