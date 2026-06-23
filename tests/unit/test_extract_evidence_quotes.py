"""Tests for quote candidate selection extraction."""

from __future__ import annotations

import asyncio
import json

import pytest

from krw_ontology.errors import PipelineStageError, RateLimitError
from krw_ontology.pipeline.stages.extract_evidence_quotes import (
    _build_quote_candidates,
    _filter_spans_for_quote_extraction,
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


def test_conservative_span_pruning_skips_only_low_value_spans():
    kept_text = (
        "The Company faces intense competition in all areas of its business, "
        "which could adversely affect sales and margins."
    )
    spans = [
        {
            "id": "span:AAPL:FY2025:10K:cover:0000",
            "section_name": "cover",
            "section_key": "cover_00",
            "span_index": 0,
            "text": "Apple Inc. Form 10-K Cover Page",
            "char_count": 30,
        },
        {
            "id": "span:AAPL:FY2025:10K:item15:0000",
            "section_name": "item15",
            "section_key": "item15_00",
            "span_index": 0,
            "text": (
                "Exhibit Index\n"
                "3.1 Articles of Incorporation incorporated by reference\n"
                "10.1 Material contract incorporated by reference\n"
                "21.1 Subsidiaries incorporated by reference\n"
            ),
            "char_count": 160,
        },
        {
            "id": "span:AAPL:FY2025:10K:item1a:0001",
            "section_name": "item1a",
            "section_key": "item1a_00",
            "span_index": 1,
            "text": kept_text,
            "char_count": len(kept_text),
        },
    ]

    eligible, audit = _filter_spans_for_quote_extraction(spans, span_pruning="conservative")

    assert [span["id"] for span in eligible] == ["span:AAPL:FY2025:10K:item1a:0001"]
    decisions = {row["span_id"]: row for row in audit}
    assert decisions["span:AAPL:FY2025:10K:cover:0000"]["decision"] == "skip"
    assert decisions["span:AAPL:FY2025:10K:item15:0000"]["reason"] == "exhibit_index_or_list"
    assert decisions["span:AAPL:FY2025:10K:item1a:0001"]["reason"] == "core_section"


def test_span_pruning_off_keeps_all_spans():
    spans = [
        {
            "id": "span:AAPL:FY2025:10K:cover:0000",
            "section_name": "cover",
            "section_key": "cover_00",
            "span_index": 0,
            "text": "Table of Contents",
            "char_count": 17,
        }
    ]

    eligible, audit = _filter_spans_for_quote_extraction(spans, span_pruning="off")

    assert eligible == spans
    assert audit[0]["decision"] == "keep"
    assert audit[0]["reason"] == "pruning_disabled"


def test_10q_legal_proceedings_section_is_core_quote_source():
    text = (
        "The Company is a party to ordinary course legal proceedings and records "
        "accruals when losses are probable and reasonably estimable."
    )
    spans = [
        {
            "id": "span:AAPL:FY2025Q2:10Q:part2_item1:0000",
            "section_name": "part2_item1",
            "section_key": "part2_item1_00",
            "span_index": 0,
            "text": text,
            "char_count": len(text),
        }
    ]

    eligible, audit = _filter_spans_for_quote_extraction(spans, span_pruning="conservative")

    assert eligible == spans
    assert audit[0]["decision"] == "keep"
    assert audit[0]["reason"] == "core_section"


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


def test_extract_evidence_quotes_writes_span_eligibility_audit(tmp_path):
    ontology_dir = tmp_path / "ontology"
    kept_text = (
        "The Company faces intense competition in all areas of its business, "
        "which could adversely affect sales and margins."
    )
    write_jsonl(
        ontology_dir / "spans.jsonl",
        [
            {
                "id": "span:AAPL:FY2025:10K:cover:0000",
                "type": "SourceSpan",
                "ticker": "AAPL",
                "source_document_id": "source:AAPL:FY2025:10K",
                "document_type": "10-K",
                "period": "FY2025",
                "section_name": "cover",
                "section_key": "cover_00",
                "section_number": "cover",
                "span_index": 0,
                "start_char": 0,
                "end_char": 17,
                "text": "Table of Contents",
                "text_hash": "sha256:cover",
                "char_count": 17,
                "section_detection_confidence": "high",
                "section_detection_method": "test",
                "schema_version": "0.1.0",
            },
            {
                "id": "span:AAPL:FY2025:10K:item1a:0001",
                "type": "SourceSpan",
                "ticker": "AAPL",
                "source_document_id": "source:AAPL:FY2025:10K",
                "document_type": "10-K",
                "period": "FY2025",
                "section_name": "item1a",
                "section_key": "item1a_00",
                "section_number": "1A",
                "span_index": 1,
                "start_char": 100,
                "end_char": 100 + len(kept_text),
                "text": kept_text,
                "text_hash": "sha256:item1a",
                "char_count": len(kept_text),
                "section_detection_confidence": "high",
                "section_detection_method": "test",
                "schema_version": "0.1.0",
            },
        ],
    )

    class FakeWorker:
        def __init__(self):
            self.candidate_counts = []

        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            candidates = json.loads(input_data["candidates_json"])
            self.candidate_counts.append(len(candidates))
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

    audit = read_jsonl(ontology_dir / "span_eligibility_audit.jsonl")
    assert worker.candidate_counts == [1]
    assert len(quotes) == 1
    assert [row["decision"] for row in audit] == ["skip", "keep"]
    assert audit[0]["reason"] in {"structural_label", "cover_metadata", "no_quote_candidates"}


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


def test_extract_evidence_quotes_does_not_split_rate_limited_batch(tmp_path):
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
            raise RateLimitError("HTTP 429 too many requests")

    worker = FakeWorker()
    with pytest.raises(PipelineStageError):
        asyncio.run(
            extract_evidence_quotes(
                worker=worker,
                ontology_dir=ontology_dir,
                ticker="AAPL",
                period="FY2025",
                doc_type="10-K",
            )
        )

    failures = read_jsonl(ontology_dir / "batch_failures.jsonl")
    assert worker.calls == [2]
    assert len(failures) == 1
    assert failures[0]["error_type"] == "RateLimitError"
    assert failures[0]["provider_transient"] is True
    assert failures[0]["provider_error_status"] == 429
    assert failures[0]["quality_repair_hint"] == "retry_transient_batch"
    cache_payload = json.loads(
        (ontology_dir / ".ai_batches" / "extract_evidence_quotes" / "batch_0000.json")
        .read_text(encoding="utf-8")
    )
    assert cache_payload["metadata"]["status"] == "partial_failed"

    class RecoveringWorker:
        def __init__(self):
            self.calls = []

        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            candidates = json.loads(input_data["candidates_json"])
            self.calls.append(len(candidates))
            return [
                {
                    "candidate_id": candidate["candidate_id"],
                    "quote_type": "risk_language",
                    "section_name": candidate["section_name"],
                    "confidence": "high",
                }
                for candidate in candidates
            ]

    recovered_worker = RecoveringWorker()
    quotes = asyncio.run(
        extract_evidence_quotes(
            worker=recovered_worker,
            ontology_dir=ontology_dir,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )
    assert recovered_worker.calls == [2]
    assert len(quotes) == 2
    assert not read_jsonl(ontology_dir / "batch_failures.jsonl")
