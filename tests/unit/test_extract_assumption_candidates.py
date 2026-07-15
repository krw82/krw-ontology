"""Tests for claim-batched modeling cue extraction."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from krw_ontology.pipeline.stages.extract_assumption_candidates import (
    _filter_modeling_cue_claims,
    extract_assumption_candidates,
)
from krw_ontology.utils.io import read_jsonl, write_jsonl


def test_filter_modeling_cue_claims_prefers_forward_numeric_metric_claims():
    claims = [
        {
            "id": "claim:1",
            "claim_type": "factual",
            "claim_text": "The company sells phones.",
            "related_metrics": ["revenue"],
        },
        {
            "id": "claim:2",
            "claim_type": "factual",
            "claim_text": "Services gross margin increased to 75.4% in 2025.",
            "related_metrics": ["gross_margin"],
        },
        {
            "id": "claim:3",
            "claim_type": "forward_looking",
            "claim_text": "Future gross margins may be volatile.",
        },
    ]

    selected = _filter_modeling_cue_claims(claims)

    assert [claim["id"] for claim in selected] == ["claim:2", "claim:3"]


def test_extract_assumption_candidates_batches_and_merges(tmp_path: Path):
    claims = []
    quotes = []
    for idx in range(9):
        quote_id = f"quote:AAPL:FY2025:10K:item7_00:000{idx}:001"
        claim_id = f"claim:AAPL:FY2025:10K:c{idx}"
        quotes.append({
            "id": quote_id,
            "type": "EvidenceQuote",
            "quote_text": f"Quote {idx} says revenue increased by {idx + 1}%.",
            "quote_type": "assumption_support",
        })
        claims.append({
            "id": claim_id,
            "type": "ResearchClaim",
            "claim_text": f"Revenue increased by {idx + 1}% and may inform future growth assumptions.",
            "claim_type": "assumption",
            "supported_by_quotes": [quote_id],
            "related_metrics": ["revenue_growth"],
            "confidence": "high",
        })
    write_jsonl(tmp_path / "claims.jsonl", claims)
    write_jsonl(tmp_path / "evidence_quotes.jsonl", quotes)

    class FakeWorker:
        def __init__(self):
            self.calls = []

        async def extract(self, prompt_template, input_data, output_schema, stage_name, **_kwargs):
            batch_claims = json.loads(input_data["claims_json"])
            batch_quotes = json.loads(input_data["quotes_json"])
            assert batch_claims[0]["id"].startswith("c")
            assert batch_claims[0]["supported_by_quotes"][0].startswith("q")
            assert batch_quotes[0]["id"].startswith("q")
            self.calls.append([claim["id"] for claim in batch_claims])
            return [
                {
                    "id": "ignored",
                    "name": "Revenue Growth Cue",
                    "assumption_text": "Revenue growth claims may inform later modeling review.",
                    "assumption_type": "growth_rate",
                    "value_hint": "growth",
                    "supported_by_claims": [batch_claims[0]["id"]],
                    "supported_by_quotes": batch_claims[0]["supported_by_quotes"],
                    "related_metrics": ["revenue_growth"],
                    "confidence": "high",
                }
            ]

    worker = FakeWorker()
    result = asyncio.run(
        extract_assumption_candidates(
            worker=worker,
            ontology_dir=tmp_path,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    assert len(worker.calls) == 2
    assert len(result) == 1
    cue = result[0]
    assert cue["review_status"] == "needs_review"
    assert cue["supported_by_claims"] == [
        "claim:AAPL:FY2025:10K:c0",
        "claim:AAPL:FY2025:10K:c8",
    ]
    assert len(read_jsonl(tmp_path / "assumption_candidates.jsonl")) == 1


def test_extract_assumption_candidates_records_only_failed_batches(tmp_path: Path):
    claims = [
        {
            "id": f"claim:AAPL:FY2025:10K:c{idx}",
            "type": "ResearchClaim",
            "claim_text": f"Future margin may change by {idx}%.",
            "claim_type": "forward_looking",
            "supported_by_quotes": [],
            "related_metrics": ["gross_margin"],
            "confidence": "medium",
        }
        for idx in range(9)
    ]
    write_jsonl(tmp_path / "claims.jsonl", claims)
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [])

    class FakeWorker:
        async def extract(self, prompt_template, input_data, output_schema, stage_name, **_kwargs):
            batch_claims = json.loads(input_data["claims_json"])
            if len(batch_claims) == 8:
                raise TimeoutError("batch too large")
            return []

    result = asyncio.run(
        extract_assumption_candidates(
            worker=FakeWorker(),
            ontology_dir=tmp_path,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    failures = read_jsonl(tmp_path / "batch_failures.jsonl")
    assert result == []
    assert len(failures) == 1
    assert failures[0]["stage"] == "extract_assumption_candidates"
    assert failures[0]["batch_index"] == 0


def test_extract_assumption_candidates_marks_provider_transient_failure(tmp_path: Path):
    write_jsonl(tmp_path / "claims.jsonl", [{
        "id": "claim:AAPL:FY2025:10K:c0",
        "type": "ResearchClaim",
        "claim_text": "Future margins may be volatile and should inform modeling review.",
        "claim_type": "forward_looking",
        "supported_by_quotes": [],
        "related_metrics": ["gross_margin"],
        "confidence": "medium",
    }])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [])

    class FakeWorker:
        async def extract(self, prompt_template, input_data, output_schema, stage_name, **_kwargs):
            raise RuntimeError("API Error: 529 [The service may be temporarily overloaded]")

    result = asyncio.run(
        extract_assumption_candidates(
            worker=FakeWorker(),
            ontology_dir=tmp_path,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    failures = read_jsonl(tmp_path / "batch_failures.jsonl")
    assert result == []
    assert len(failures) == 1
    assert failures[0]["provider_transient"] is True
    assert failures[0]["provider_error_status"] == 529
    assert failures[0]["provider_error_kind"] == "overload"


def test_extract_assumption_candidates_rejects_unknown_aliases(tmp_path: Path):
    quote_id = "quote:AAPL:FY2025:10K:item7_00:0000:001"
    claim_id = "claim:AAPL:FY2025:10K:c0"
    write_jsonl(tmp_path / "claims.jsonl", [{
        "id": claim_id,
        "type": "ResearchClaim",
        "claim_text": "Future margin may be volatile and should inform modeling review.",
        "claim_type": "forward_looking",
        "supported_by_quotes": [quote_id],
        "related_metrics": ["gross_margin"],
        "confidence": "medium",
    }])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [{
        "id": quote_id,
        "type": "EvidenceQuote",
        "quote_text": "Future margin may be volatile.",
        "quote_type": "assumption_support",
    }])

    class FakeWorker:
        async def extract(self, prompt_template, input_data, output_schema, stage_name, **_kwargs):
            return [
                {
                    "id": "ignored",
                    "name": "Margin Cue",
                    "assumption_text": "Margin volatility should be reviewed.",
                    "assumption_type": "margin",
                    "supported_by_claims": ["c999"],
                    "supported_by_quotes": ["q999"],
                    "related_metrics": ["gross_margin"],
                    "confidence": "medium",
                }
            ]

    result = asyncio.run(
        extract_assumption_candidates(
            worker=FakeWorker(),
            ontology_dir=tmp_path,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    rejected = read_jsonl(tmp_path / "rejected_objects.jsonl")
    assert result == []
    assert len(rejected) == 1
    assert rejected[0]["rejection_stage"] == "reference_alias_resolution"
    assert "c999" in rejected[0]["rejection_reason"]
    assert "q999" in rejected[0]["rejection_reason"]
