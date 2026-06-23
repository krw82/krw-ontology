"""Tests for claim extraction split retry."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from krw_ontology.errors import PipelineStageError, RateLimitError
from krw_ontology.pipeline.stages.extract_research_claims import extract_research_claims
from krw_ontology.utils.io import read_jsonl, write_jsonl


def _span(idx: int) -> dict:
    return {
        "id": f"span:AAPL:FY2025:10K:item1a_00:{idx:04d}",
        "type": "SourceSpan",
        "section_name": "item1a",
        "text": f"Span {idx} says revenue may be affected by market conditions.",
    }


def _quote(idx: int) -> dict:
    return {
        "id": f"quote:AAPL:FY2025:10K:item1a_00:{idx:04d}:001",
        "type": "EvidenceQuote",
        "source_span_id": f"span:AAPL:FY2025:10K:item1a_00:{idx:04d}",
        "quote_text": f"Quote {idx} says revenue may be affected by market conditions.",
        "quote_type": "risk_language",
    }


def test_extract_research_claims_splits_failed_batch(tmp_path: Path):
    write_jsonl(tmp_path / "spans.jsonl", [_span(0), _span(1)])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [_quote(0), _quote(1)])

    class FakeWorker:
        def __init__(self):
            self.calls = []

        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            spans = json.loads(input_data["spans_json"])
            quotes = json.loads(input_data["quotes_json"])
            assert "natural_gas_price" in input_data["factor_taxonomy_list"]
            assert "tariff_policy" in input_data["factor_taxonomy_list"]
            self.calls.append(len(quotes))
            if len(quotes) > 1:
                raise TimeoutError("too large")
            return [
                {
                    "id": "ignored",
                    "claim_text": (
                        "Revenue may be affected for span "
                        f"{spans[0]['source_span_id'].split(':')[-1]}."
                    ),
                    "claim_type": "risk_assessment",
                    "supported_by_quotes": [quotes[0]["id"]],
                    "related_metrics": ["revenue"],
                    "object_type_hints": ["ExternalFactorExposure"],
                    "theme_hint": "market_conditions_revenue_exposure",
                    "factor_hint": "end_market_demand",
                    "activity_hint": "product_sales",
                    "impact_channels": ["revenue"],
                    "effect_direction": "negative",
                    "materiality_hint": "medium",
                    "time_horizon": "ongoing",
                    "confidence": "high",
                }
            ]

    worker = FakeWorker()
    claims = asyncio.run(
        extract_research_claims(
            worker=worker,
            ontology_dir=tmp_path,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    assert worker.calls == [2, 1, 1]
    assert len(claims) == 2
    assert claims[0]["object_type_hints"] == ["ExternalFactorExposure"]
    assert claims[0]["factor_hint"] == "end_market_demand"
    assert claims[0]["impact_channels"] == ["revenue"]
    assert claims[0]["effect_direction"] == "negative"
    assert not read_jsonl(tmp_path / "batch_failures.jsonl")
    assert len(read_jsonl(tmp_path / "claims.jsonl")) == 2


def test_extract_research_claims_does_not_split_rate_limited_batch(tmp_path: Path):
    write_jsonl(tmp_path / "spans.jsonl", [_span(0), _span(1)])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [_quote(0), _quote(1)])

    class FakeWorker:
        def __init__(self):
            self.calls = []

        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            quotes = json.loads(input_data["quotes_json"])
            self.calls.append(len(quotes))
            raise RateLimitError("HTTP 429 too many requests")

    worker = FakeWorker()
    with pytest.raises(PipelineStageError):
        asyncio.run(
            extract_research_claims(
                worker=worker,
                ontology_dir=tmp_path,
                ticker="AAPL",
                period="FY2025",
                doc_type="10-K",
            )
        )

    failures = read_jsonl(tmp_path / "batch_failures.jsonl")
    assert worker.calls == [2]
    assert len(failures) == 1
    assert failures[0]["error_type"] == "RateLimitError"
    assert failures[0]["provider_transient"] is True
    assert failures[0]["provider_error_status"] == 429
    assert failures[0]["quality_repair_hint"] == "retry_transient_batch"
    cache_payload = json.loads(
        (tmp_path / ".ai_batches" / "extract_research_claims" / "batch_0000.json")
        .read_text(encoding="utf-8")
    )
    assert cache_payload["metadata"]["status"] == "partial_failed"


def test_extract_research_claims_records_leaf_failure(tmp_path: Path):
    write_jsonl(tmp_path / "spans.jsonl", [_span(0)])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [_quote(0)])

    class FakeWorker:
        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            raise TimeoutError("still too large")

    with pytest.raises(PipelineStageError):
        asyncio.run(
            extract_research_claims(
                worker=FakeWorker(),
                ontology_dir=tmp_path,
                ticker="AAPL",
                period="FY2025",
                doc_type="10-K",
            )
        )

    failures = read_jsonl(tmp_path / "batch_failures.jsonl")
    assert len(failures) == 1
    assert failures[0]["stage"] == "extract_research_claims"
    assert failures[0]["input_span_ids"] == ["span:AAPL:FY2025:10K:item1a_00:0000"]


def test_extract_research_claims_rejects_unknown_quote_alias(tmp_path: Path):
    write_jsonl(tmp_path / "spans.jsonl", [_span(0)])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [_quote(0)])

    class FakeWorker:
        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            quotes = json.loads(input_data["quotes_json"])
            assert quotes[0]["id"] == "q1"
            return [
                {
                    "id": "ignored",
                    "claim_text": "Revenue may be affected by market conditions.",
                    "claim_type": "risk_assessment",
                    "supported_by_quotes": ["q999"],
                    "related_metrics": ["revenue"],
                    "confidence": "high",
                }
            ]

    claims = asyncio.run(
        extract_research_claims(
            worker=FakeWorker(),
            ontology_dir=tmp_path,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    rejected = read_jsonl(tmp_path / "rejected_objects.jsonl")
    assert claims == []
    assert len(rejected) == 1
    assert rejected[0]["rejection_stage"] == "reference_alias_resolution"
    assert "q999" in rejected[0]["rejection_reason"]


def test_extract_research_claims_skips_spans_without_quotes(tmp_path: Path):
    write_jsonl(tmp_path / "spans.jsonl", [_span(0)])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [])

    class FakeWorker:
        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            raise AssertionError("claim extraction should be quote-first")

    claims = asyncio.run(
        extract_research_claims(
            worker=FakeWorker(),
            ontology_dir=tmp_path,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    assert claims == []
    assert read_jsonl(tmp_path / "claims.jsonl") == []


def test_extract_research_claims_rejects_span_reference(tmp_path: Path):
    write_jsonl(tmp_path / "spans.jsonl", [_span(0)])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [_quote(0)])

    class FakeWorker:
        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            quotes = json.loads(input_data["quotes_json"])
            assert quotes[0]["id"] == "q1"
            return [
                {
                    "id": "ignored",
                    "claim_text": "Revenue may be affected by market conditions.",
                    "claim_type": "risk_assessment",
                    "supported_by_quotes": ["span:AAPL:FY2025:10K:item1a_00:0000"],
                    "related_metrics": ["revenue"],
                    "confidence": "high",
                }
            ]

    claims = asyncio.run(
        extract_research_claims(
            worker=FakeWorker(),
            ontology_dir=tmp_path,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    rejected = read_jsonl(tmp_path / "rejected_objects.jsonl")
    assert claims == []
    assert len(rejected) == 1
    assert rejected[0]["rejection_stage"] == "reference_alias_resolution"
    assert "span:AAPL:FY2025:10K:item1a_00:0000" in rejected[0]["rejection_reason"]


def test_extract_research_claims_ignores_legacy_span_cache(tmp_path: Path):
    write_jsonl(tmp_path / "spans.jsonl", [_span(0)])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [_quote(0)])
    cache_dir = tmp_path / ".ai_batches" / "extract_research_claims"
    cache_dir.mkdir(parents=True)
    (cache_dir / "batch_0000.json").write_text(
        json.dumps({
            "stage": "extract_research_claims",
            "batch_index": 0,
            "metadata": {"status": "ok", "span_count": 1},
            "items": [
                {
                    "id": "claim:AAPL:FY2025:10K:legacy",
                    "type": "ResearchClaim",
                    "ticker": "AAPL",
                    "source_document_id": "source:AAPL:FY2025:10K",
                    "document_type": "10-K",
                    "period": "FY2025",
                    "claim_text": "Legacy span-cached claim.",
                    "claim_type": "factual",
                    "supported_by_quotes": [_quote(0)["id"]],
                    "confidence": "high",
                    "review_status": "accepted",
                    "schema_version": "0.1.0",
                }
            ],
        })
    )

    class FakeWorker:
        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            quotes = json.loads(input_data["quotes_json"])
            return [
                {
                    "id": "ignored",
                    "claim_text": "Fresh quote-first claim.",
                    "claim_type": "factual",
                    "supported_by_quotes": [quotes[0]["id"]],
                    "related_metrics": [],
                    "confidence": "high",
                }
            ]

    claims = asyncio.run(
        extract_research_claims(
            worker=FakeWorker(),
            ontology_dir=tmp_path,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    assert [claim["claim_text"] for claim in claims] == ["Fresh quote-first claim."]
