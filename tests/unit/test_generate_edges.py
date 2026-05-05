"""Tests for deterministic edge generation."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

from krw_ontology.pipeline.stages.generate_edges import generate_edges
from krw_ontology.utils.io import read_jsonl, write_jsonl


def test_generate_edges_uses_existing_references_without_sdk(tmp_path: Path):
    base = {
        "ticker": "AAPL",
        "source_document_id": "source:AAPL:FY2025:10K",
        "document_type": "10-K",
        "period": "FY2025",
        "schema_version": "0.1.0",
    }
    span_id = "span:AAPL:FY2025:10K:item1a:0000"
    quote_id = "quote:AAPL:FY2025:10K:item1a:0000:001"
    signal_id = "signal:AAPL:FY2025:10K:item1a:0000:001"
    claim_id = "claim:AAPL:FY2025:10K:test-claim"
    risk_id = "risk:AAPL:FY2025:10K:test-risk"
    assumption_id = "assumption:AAPL:FY2025:10K:test-cue"

    write_jsonl(tmp_path / "spans.jsonl", [{"id": span_id, "type": "SourceSpan", **base}])
    write_jsonl(tmp_path / "evidence_quotes.jsonl", [{
        "id": quote_id,
        "type": "EvidenceQuote",
        **base,
        "source_span_id": span_id,
    }])
    write_jsonl(tmp_path / "language_signals.jsonl", [{
        "id": signal_id,
        "type": "LanguageSignal",
        **base,
        "source_quote_id": quote_id,
    }])
    write_jsonl(tmp_path / "claims.jsonl", [{
        "id": claim_id,
        "type": "ResearchClaim",
        **base,
        "supported_by_quotes": [quote_id, span_id],
    }])
    write_jsonl(tmp_path / "risks.jsonl", [{
        "id": risk_id,
        "type": "RiskFactor",
        **base,
        "supported_by_claims": [claim_id],
        "supported_by_quotes": [quote_id],
        "affects": ["revenue"],
    }])
    write_jsonl(tmp_path / "assumption_candidates.jsonl", [{
        "id": assumption_id,
        "type": "AssumptionCandidate",
        **base,
        "supported_by_claims": [claim_id],
        "supported_by_quotes": [quote_id],
    }])
    write_jsonl(tmp_path / "batch_failures.jsonl", [{
        "id": "batch_failure:AAPL:FY2025:10K:generate_edges:0000",
        "stage": "generate_edges",
    }])

    class FakeWorker:
        async def extract(self, *args, **kwargs):
            raise AssertionError("generate_edges should not call the SDK")

    edges = asyncio.run(
        generate_edges(FakeWorker(), tmp_path, "AAPL", "FY2025", "10-K")
    )

    relations = Counter(edge["relation_id"] for edge in edges)
    assert relations == {
        "contains_quote": 1,
        "has_signal": 1,
        "supports": 1,
        "describes_risk": 1,
        "affects_risk": 1,
        "supports_assumption": 1,
        "derived_from": 1,
    }
    assert not read_jsonl(tmp_path / "batch_failures.jsonl")
