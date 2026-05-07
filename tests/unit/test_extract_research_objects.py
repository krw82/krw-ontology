"""Tests for claim-based research object theme building."""

from __future__ import annotations

import asyncio

from krw_ontology.pipeline.stages.extract_risks_drivers_headwinds import (
    extract_risks_drivers_headwinds,
)
from krw_ontology.utils.io import read_jsonl, write_jsonl


def test_extract_research_objects_batches_claims_and_merges(tmp_path):
    ontology_dir = tmp_path / "ontology"
    claims = [
        {
            "id": f"claim:AAPL:FY2025:10K:c{i}",
            "type": "ResearchClaim",
            "claim_text": f"Claim {i} says competition may pressure margins.",
            "claim_type": "risk_assessment",
            "supported_by_quotes": [f"quote:AAPL:FY2025:10K:q{i}"],
            "confidence": "high",
        }
        for i in range(9)
    ]
    quotes = [
        {
            "id": f"quote:AAPL:FY2025:10K:q{i}",
            "type": "EvidenceQuote",
            "quote_text": f"Quote {i} says competition may pressure margins.",
            "quote_type": "competitive_pressure",
        }
        for i in range(9)
    ]
    write_jsonl(ontology_dir / "claims.jsonl", claims)
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", quotes)

    class FakeWorker:
        def __init__(self):
            self.calls = 0

        async def extract(self, prompt_template, input_data, output_schema, stage_name):
            self.calls += 1
            raise AssertionError("research object theme building should not call the SDK")

    worker = FakeWorker()
    result = asyncio.run(
        extract_risks_drivers_headwinds(
            worker=worker,
            ontology_dir=ontology_dir,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    assert worker.calls == 0
    assert len(result["headwinds"]) == 1
    headwind = result["headwinds"][0]
    assert headwind["name"] == "Competitive headwind"
    assert headwind["supported_by_claims"] == [
        "claim:AAPL:FY2025:10K:c0",
        "claim:AAPL:FY2025:10K:c1",
        "claim:AAPL:FY2025:10K:c2",
        "claim:AAPL:FY2025:10K:c3",
        "claim:AAPL:FY2025:10K:c4",
        "claim:AAPL:FY2025:10K:c5",
        "claim:AAPL:FY2025:10K:c6",
        "claim:AAPL:FY2025:10K:c7",
        "claim:AAPL:FY2025:10K:c8",
    ]
    assert len(read_jsonl(ontology_dir / "headwinds.jsonl")) == 1


def test_extract_research_objects_prefers_claim_semantic_hints(tmp_path):
    ontology_dir = tmp_path / "ontology"
    claims = [
        {
            "id": "claim:AAPL:FY2025:10K:hinted",
            "type": "ResearchClaim",
            "claim_text": "The filing describes a customer adoption signal.",
            "claim_type": "factual",
            "object_type_hints": ["GrowthDriver"],
            "theme_hint": "services_customer_adoption",
            "factor_hint": "end_market_demand",
            "impact_channels": ["revenue"],
            "effect_direction": "positive",
            "materiality_hint": "medium",
            "supported_by_quotes": ["quote:AAPL:FY2025:10K:q1"],
            "confidence": "high",
        }
    ]
    write_jsonl(ontology_dir / "claims.jsonl", claims)
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", [])

    result = asyncio.run(
        extract_risks_drivers_headwinds(
            worker=None,
            ontology_dir=ontology_dir,
            ticker="AAPL",
            period="FY2025",
            doc_type="10-K",
        )
    )

    assert not result["risks"]
    assert result["growth_drivers"][0]["name"] == "Services Customer Adoption growth driver"
    assert result["growth_drivers"][0]["category"] == "end_market_demand"
    assert result["growth_drivers"][0]["qualitative_impact"] == "medium_positive"
