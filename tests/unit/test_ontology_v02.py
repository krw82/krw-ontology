"""Tests for ontology v0.2 business, exposure, and company context artifacts."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

from krw_ontology.agent_index.store import OntologyStore
from krw_ontology.agent_index.builder import build_agent_index as _build_legacy_agent_index
from krw_ontology.factor_taxonomy import canonical_factor_key, factor_spec, normalize_sector_hint
from krw_ontology.pipeline.stages.build_company_context import build_company_context
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.pipeline.stages.extract_business_activities import extract_business_activities
from krw_ontology.pipeline.stages.extract_external_factor_exposures import (
    extract_external_factor_exposures,
)
from krw_ontology.pipeline.stages.generate_edges import generate_edges
from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import atomic_write_json, read_jsonl, write_jsonl
from krw_ontology.validators.schema_validator import validate_schema


def build_agent_index(*args, **kwargs):
    kwargs.setdefault("allow_internal_legacy_builder", True)
    return _build_legacy_agent_index(*args, **kwargs)


def test_global_factor_taxonomy_canonicalizes_factor_hints_independent_of_sector():
    assert canonical_factor_key("oil prices") == "crude_oil_price"
    assert canonical_factor_key("Henry Hub") == "natural_gas_price"
    assert canonical_factor_key("FERC approval") == "regulatory_approval"
    assert canonical_factor_key("GHG regulation") == "environmental_regulation"
    assert canonical_factor_key("trade tariffs") == "tariff_policy"
    assert canonical_factor_key("named windstorms") == "severe_weather"
    assert factor_spec("crude_oil_price")["category"] == "commodity"
    assert factor_spec("litigation")["category"] == "legal"
    assert normalize_sector_hint("energy_oil_and_gas") == "energy_integrated_oil_gas"


def test_new_ontology_object_schemas_validate():
    base = {
        "ticker": "VG",
        "source_document_id": "source:VG:FY2025:10K",
        "document_type": "10-K",
        "period": "FY2025",
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    }
    objects = [
        {
            "id": "business_activity:VG:FY2025:10K:lng-sales",
            "type": "BusinessActivity",
            **base,
            "name": "LNG sales",
            "activity_type": "revenue_source",
            "description": "Evidence-backed LNG sales activity.",
        },
        {
            "id": "external_factor_exposure:VG:FY2025:10K:natural-gas-price-revenue",
            "type": "ExternalFactorExposure",
            **base,
            "factor": "natural_gas_price",
            "factor_category": "commodity",
            "impact_channel": "revenue",
            "mechanism": "Henry Hub-linked pricing affects variable commodity fees.",
        },
        {
            "id": "company_business_profile:VG:ALL",
            "type": "CompanyBusinessProfile",
            "ticker": "VG",
            "source_document_id": "source:VG:ALL:COMPANY",
            "document_type": "COMPANY",
            "period": "ALL",
            "sector": "energy_lng",
            "business_model_summary": "Primary activities: LNG sales.",
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        },
        {
            "id": "temporal_link:VG:ALL:abc123",
            "type": "TemporalLink",
            "ticker": "VG",
            "source_document_id": "source:VG:ALL:COMPANY",
            "document_type": "COMPANY",
            "period": "ALL",
            "from_object_id": "business_activity:VG:FY2024:10K:lng-sales",
            "to_object_id": "business_activity:VG:FY2025:10K:lng-sales",
            "from_period": "FY2024",
            "to_period": "FY2025",
            "relation": "continues_as",
            "rationale": "Same activity across periods.",
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        },
        {
            "id": "trend_observation:VG:ALL:abc123",
            "type": "TrendObservation",
            "ticker": "VG",
            "source_document_id": "source:VG:ALL:COMPANY",
            "document_type": "COMPANY",
            "period": "ALL",
            "subject": "revenue",
            "metric_or_factor": "revenue",
            "from_period": "FY2024",
            "to_period": "FY2025",
            "direction": "up",
            "interpretation": "Revenue moved up.",
            "supported_by_objects": [
                "financial_metric:VG:FY2024:10K:revenue",
                "financial_metric:VG:FY2025:10K:revenue",
            ],
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        },
        {
            "id": "change_event:VG:FY2025:abc123",
            "type": "ChangeEvent",
            "ticker": "VG",
            "source_document_id": "source:VG:ALL:COMPANY",
            "document_type": "COMPANY",
            "period": "FY2025",
            "event_type": "approval_update",
            "description": "The company filed applications with FERC and DOE.",
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        },
    ]

    for obj in objects:
        ok, reason = validate_schema(obj)
        assert ok, f"{obj['type']} should validate: {reason}"


def test_business_and_exposure_stages_validate_and_generate_edges(tmp_path: Path):
    claims = _write_vg_doc_fixture(tmp_path, period="FY2025")

    activities = asyncio.run(
        extract_business_activities(None, tmp_path, "VG", "FY2025", "10-K", claims=claims)
    )
    exposures = asyncio.run(
        extract_external_factor_exposures(
            None,
            tmp_path,
            "VG",
            "FY2025",
            "10-K",
            claims=claims,
            business_activities=activities,
        )
    )

    assert {activity["name"] for activity in activities} >= {
        "LNG sales",
        "Feed gas procurement",
        "Liquefaction project development",
    }
    assert any(
        exposure["factor"] == "natural_gas_price" and exposure["benchmark"] == "Henry Hub"
        for exposure in exposures
    )
    assert any(exposure["factor"] == "regulatory_approval" for exposure in exposures)

    write_jsonl(tmp_path / "business_factors.jsonl", [{
        "id": "business_factor:VG:FY2025:10K:feed-gas-basis-risk",
        "type": "BusinessFactor",
        "ticker": "VG",
        "source_document_id": "source:VG:FY2025:10K",
        "document_type": "10-K",
        "period": "FY2025",
        "name": "Feed gas basis risk",
        "factor_roles": ["risk"],
        "category": "financial",
        "description": "Feed gas basis differentials could compress operating margins.",
        "supported_by_claims": ["claim:VG:FY2025:10K:feed-gas-basis-risk"],
        "supported_by_quotes": ["quote:VG:FY2025:10K:item1a:0001:002"],
        "affected_channels": ["operating_margin"],
        "qualitative_impact": "negative",
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    }])

    validation = run_validate_ontology(tmp_path, include_edges=False)
    assert validation["stats"]["total_rejected"] == 0

    edges = asyncio.run(generate_edges(None, tmp_path, "VG", "FY2025", "10-K"))
    relations = Counter(edge["relation_id"] for edge in edges)
    assert relations["describes_activity"] >= 3
    assert relations["describes_exposure"] >= 2
    assert relations["manifests_as"] >= 1
    assert "affects_exposure" not in relations
    assert "generates_revenue" not in relations
    assert all(edge["rationale"] for edge in edges)

    edge_validation = run_validate_ontology(tmp_path, include_edges=True)
    assert edge_validation["stats"]["total_rejected"] == 0


def test_activity_and_exposure_can_be_built_from_claim_hints_without_keyword_aliases(tmp_path: Path):
    quote_id = "quote:VG:FY2025:10K:item1a:0001:001"
    claim = {
        "id": "claim:VG:FY2025:10K:semantic-hint-only",
        "type": "ResearchClaim",
        "ticker": "VG",
        "source_document_id": "source:VG:FY2025:10K",
        "document_type": "10-K",
        "period": "FY2025",
        "claim_text": "The pricing formula may affect margin outcomes.",
        "claim_type": "risk_assessment",
        "supported_by_quotes": [quote_id],
        "related_metrics": ["operating_margin"],
        "object_type_hints": ["ExternalFactorExposure", "BusinessActivity"],
        "theme_hint": "feed_gas_cost_basis_exposure",
        "factor_hint": "natural_gas_price",
        "activity_hint": "feed_gas_procurement",
        "benchmark_hint": "Henry Hub",
        "impact_channels": ["operating_margin"],
        "effect_direction": "negative",
        "materiality_hint": "medium",
        "time_horizon": "ongoing",
        "sector_hint": "energy_lng",
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    }

    activities = asyncio.run(
        extract_business_activities(None, tmp_path, "VG", "FY2025", "10-K", claims=[claim])
    )
    exposures = asyncio.run(
        extract_external_factor_exposures(
            None,
            tmp_path,
            "VG",
            "FY2025",
            "10-K",
            claims=[claim],
            business_activities=activities,
        )
    )

    assert activities[0]["name"] == "Feed gas procurement"
    assert exposures[0]["factor"] == "natural_gas_price"
    assert exposures[0]["benchmark"] == "Henry Hub"
    assert exposures[0]["effect_direction"] == "negative"
    assert exposures[0]["related_business_activities"] == [activities[0]["id"]]


def test_exposure_factor_category_uses_global_taxonomy_not_sector_pack(tmp_path: Path):
    claim = {
        "id": "claim:CVX:FY2023Q1:10Q:oil-price-results",
        "type": "ResearchClaim",
        "ticker": "CVX",
        "source_document_id": "source:CVX:FY2023Q1:10Q",
        "document_type": "10-Q",
        "period": "FY2023Q1",
        "claim_text": "Lower crude oil prices reduced upstream earnings and operating cash flow.",
        "claim_type": "risk_assessment",
        "supported_by_quotes": ["quote:CVX:FY2023Q1:10Q:item2:0001:001"],
        "related_metrics": ["operating_income", "operating_cash_flow"],
        "object_type_hints": ["ExternalFactorExposure"],
        "theme_hint": "crude_oil_price_exposure",
        "factor_hint": "oil prices",
        "impact_channels": ["operating_income"],
        "effect_direction": "negative",
        "sector_hint": "energy_lng",
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    }

    exposures = asyncio.run(
        extract_external_factor_exposures(
            None,
            tmp_path,
            "CVX",
            "FY2023Q1",
            "10-Q",
            claims=[claim],
            business_activities=[],
        )
    )

    assert exposures[0]["factor"] == "crude_oil_price"
    assert exposures[0]["factor_category"] == "commodity"
    assert exposures[0]["sector_tags"] == ["energy_lng"]


def test_company_context_and_agent_index_include_high_level_artifacts(tmp_path: Path):
    _write_indexed_company_doc(tmp_path, period="FY2024", revenue=100.0)
    _write_indexed_company_doc(tmp_path, period="FY2025", revenue=125.0)

    result = build_company_context(tmp_path, "VG")

    assert result["counts"]["company_business_profiles"] == 1
    assert result["counts"]["temporal_links"] >= 2
    assert result["counts"]["trend_observations"] == 1
    assert result["counts"]["change_events"] >= 1
    assert result["counts"]["quality_events"] >= 1
    assert result["counts"]["edges"] >= 1
    assert result["validation"]["total_rejected"] == 0

    index = build_agent_index(tmp_path)
    with OntologyStore(index["index_path"]) as store:
        profile = store.query(tickers=["VG"], object_types=["CompanyBusinessProfile"], limit=5)
        trends = store.query(tickers=["VG"], object_types=["TrendObservation"], limit=5)
        exposures = store.query(
            topic="Henry Hub",
            tickers=["VG"],
            object_types=["ExternalFactorExposure"],
            limit=5,
        )
        quality = store.quality(ticker="VG", document_type="COMPANY")

    assert profile
    assert profile[0]["object"]["sector"] == "energy_lng"
    assert profile[0]["text"].startswith("Primary activities:")
    assert profile[0]["evidence"]["claims"]
    assert trends and trends[0]["object"]["direction"] == "up"
    assert any(bundle["object"]["factor"] == "natural_gas_price" for bundle in exposures)
    assert any(event["category"] == "coverage_gap" for event in quality["events"])


def test_company_context_does_not_compare_annual_metrics_to_quarterly_metrics(tmp_path: Path):
    _write_context_source_doc(
        tmp_path,
        period="FY2025",
        doc_type="10-K",
        doc_type_key="10K",
        metric_observations=[
            _metric("VG", "FY2025", "10-K", "10K", "revenue", 100.0, fiscal_year=2025, period_type="annual")
        ],
    )
    _write_context_source_doc(
        tmp_path,
        period="FY2025Q1",
        doc_type="10-Q",
        doc_type_key="10Q",
        metric_observations=[
            _metric("VG", "FY2025Q1", "10-Q", "10Q", "revenue", 20.0, fiscal_year=2025, fiscal_period="Q1", period_type="quarter")
        ],
    )
    _write_context_source_doc(
        tmp_path,
        period="FY2025Q2",
        doc_type="10-Q",
        doc_type_key="10Q",
        metric_observations=[
            _metric("VG", "FY2025Q2", "10-Q", "10Q", "revenue", 30.0, fiscal_year=2025, fiscal_period="Q2", period_type="quarter")
        ],
    )

    build_company_context(tmp_path, "VG")
    trends = _read_context_jsonl(tmp_path, "VG", "trend_observations")
    period_pairs = {(trend["from_period"], trend["to_period"]) for trend in trends}

    assert ("FY2025", "FY2025Q1") not in period_pairs
    assert ("FY2025", "FY2025Q2") not in period_pairs
    assert ("FY2025Q1", "FY2025Q2") in period_pairs


def test_company_context_change_events_are_not_truncated_to_first_period(tmp_path: Path):
    for period in ("FY2024", "FY2025", "FY2025Q1"):
        claims = [_event_claim("VG", period, idx) for idx in range(90)]
        _write_context_source_doc(
            tmp_path,
            period=period,
            doc_type="10-Q" if "Q" in period else "10-K",
            doc_type_key="10Q" if "Q" in period else "10K",
            claims=claims,
        )

    build_company_context(tmp_path, "VG")
    events = _read_context_jsonl(tmp_path, "VG", "change_events")
    periods = {event["period"] for event in events}

    assert {"FY2024", "FY2025", "FY2025Q1"} <= periods
    assert Counter(event["period"] for event in events)["FY2024"] <= 80


def test_company_profile_key_exposures_prefer_recent_direct_material_evidence(tmp_path: Path):
    old_exposure = _exposure(
        "VG",
        "FY2024",
        "10-K",
        "10K",
        "macroeconomic_conditions",
        evidence_grade="derived",
        materiality="low",
    )
    latest_exposure = _exposure(
        "VG",
        "FY2025",
        "10-K",
        "10K",
        "regulatory_approval",
        evidence_grade="direct",
        materiality="high",
    )
    _write_context_source_doc(
        tmp_path,
        period="FY2024",
        doc_type="10-K",
        doc_type_key="10K",
        exposures=[old_exposure],
    )
    _write_context_source_doc(
        tmp_path,
        period="FY2025",
        doc_type="10-K",
        doc_type_key="10K",
        exposures=[latest_exposure],
    )

    build_company_context(tmp_path, "VG")
    profile = _read_context_jsonl(tmp_path, "VG", "company_business_profiles")[0]

    assert profile["key_exposures"][0] == latest_exposure["id"]


def _write_vg_doc_fixture(ontology_dir: Path, *, period: str) -> list[dict]:
    ticker = "VG"
    doc_type = "10-K"
    doc_type_key = "10K"
    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"
    quote_texts = [
        "The variable commodity fee under LNG sales contracts is equal to at least 115% of Henry Hub per MMBtu of LNG.",
        "Differences between actual feed gas costs, basis differentials, and the Henry Hub gas price could compress operating margins and increase cost of revenue.",
        "In November 2025, the company filed FERC and DOE regulatory approval applications for CP2 construction that could delay revenue and capital expenditures.",
        "Commissioning cargos generated proceeds and operating cash flow.",
    ]
    span_text = "\n".join(quote_texts)
    span = {
        "id": f"span:{ticker}:{period}:{doc_type_key}:item1a:0001",
        "type": "SourceSpan",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": doc_type,
        "period": period,
        "section_name": "item1a",
        "section_number": "1A",
        "section_key": "item1a",
        "section_instance": 0,
        "span_index": 1,
        "start_char": 0,
        "end_char": len(span_text),
        "text": span_text,
        "text_hash": "sha256:test",
        "char_count": len(span_text),
        "section_detection_confidence": "high",
        "section_detection_method": "test",
        "schema_version": SCHEMA_VERSION,
    }
    quotes = [
        {
            "id": f"quote:{ticker}:{period}:{doc_type_key}:item1a:0001:{idx:03d}",
            "type": "EvidenceQuote",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": doc_type,
            "period": period,
            "source_span_id": span["id"],
            "quote_text": text,
            "quote_type": "business_update",
            "section_name": "item1a",
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        }
        for idx, text in enumerate(quote_texts, start=1)
    ]
    claims = [
        {
            "id": f"claim:{ticker}:{period}:{doc_type_key}:henry-hub-pricing",
            "type": "ResearchClaim",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": doc_type,
            "period": period,
            "claim_text": quote_texts[0],
            "claim_type": "factual",
            "supported_by_quotes": [quotes[0]["id"]],
            "related_metrics": ["revenue"],
            "object_type_hints": ["ExternalFactorExposure", "BusinessActivity"],
            "theme_hint": "henry_hub_linked_lng_pricing",
            "factor_hint": "natural_gas_price",
            "activity_hint": "lng_sales",
            "benchmark_hint": "Henry Hub",
            "impact_channels": ["revenue"],
            "effect_direction": "mixed",
            "materiality_hint": "medium",
            "time_horizon": "ongoing",
            "sector_hint": "energy_lng",
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        },
        {
            "id": f"claim:{ticker}:{period}:{doc_type_key}:feed-gas-basis-risk",
            "type": "ResearchClaim",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": doc_type,
            "period": period,
            "claim_text": quote_texts[1],
            "claim_type": "risk_assessment",
            "supported_by_quotes": [quotes[1]["id"]],
            "related_metrics": ["cost_of_revenue", "operating_margin"],
            "object_type_hints": ["BusinessFactor", "ExternalFactorExposure", "BusinessActivity"],
            "theme_hint": "feed_gas_cost_basis_exposure",
            "factor_hint": "basis_differential",
            "activity_hint": "feed_gas_procurement",
            "benchmark_hint": "Henry Hub",
            "impact_channels": ["cost_of_revenue", "operating_margin"],
            "effect_direction": "negative",
            "materiality_hint": "medium",
            "time_horizon": "ongoing",
            "sector_hint": "energy_lng",
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        },
        {
            "id": f"claim:{ticker}:{period}:{doc_type_key}:ferc-doe-approvals",
            "type": "ResearchClaim",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": doc_type,
            "period": period,
            "claim_text": quote_texts[2],
            "claim_type": "risk_assessment",
            "supported_by_quotes": [quotes[2]["id"]],
            "related_metrics": ["capital_expenditures", "revenue"],
            "object_type_hints": ["BusinessFactor", "ExternalFactorExposure", "ChangeEvent"],
            "theme_hint": "project_regulatory_approval_risk",
            "factor_hint": "regulatory_approval",
            "activity_hint": "liquefaction_projects",
            "impact_channels": ["revenue", "capital_expenditures", "cash_flow"],
            "effect_direction": "negative",
            "materiality_hint": "high",
            "time_horizon": "medium",
            "sector_hint": "energy_lng",
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        },
        {
            "id": f"claim:{ticker}:{period}:{doc_type_key}:commissioning-cargo-proceeds",
            "type": "ResearchClaim",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": doc_type,
            "period": period,
            "claim_text": quote_texts[3],
            "claim_type": "period_update",
            "supported_by_quotes": [quotes[3]["id"]],
            "related_metrics": ["revenue", "operating_cash_flow"],
            "object_type_hints": ["BusinessActivity", "ChangeEvent"],
            "theme_hint": "commissioning_cargo_proceeds",
            "activity_hint": "commissioning_cargo_sales",
            "impact_channels": ["revenue", "operating_cash_flow"],
            "effect_direction": "positive",
            "materiality_hint": "medium",
            "time_horizon": "short",
            "sector_hint": "energy_lng",
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": SCHEMA_VERSION,
        },
    ]
    write_jsonl(ontology_dir / "spans.jsonl", [span])
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", quotes)
    write_jsonl(ontology_dir / "claims.jsonl", claims)
    return claims


def _write_indexed_company_doc(root: Path, *, period: str, revenue: float) -> None:
    ticker = "VG"
    doc_type = "10-K"
    doc_type_key = "10K"
    ontology_dir = root / "companies" / ticker / "ontology" / doc_type_key / period
    sources_dir = root / "companies" / ticker / "sources" / doc_type_key / period
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)
    claims = _write_vg_doc_fixture(ontology_dir, period=period)
    activities = asyncio.run(
        extract_business_activities(None, ontology_dir, ticker, period, doc_type, claims=claims)
    )
    exposures = asyncio.run(
        extract_external_factor_exposures(
            None,
            ontology_dir,
            ticker,
            period,
            doc_type,
            claims=claims,
            business_activities=activities,
        )
    )
    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"
    write_jsonl(
        ontology_dir / "metric_observations.jsonl",
        [
            {
                "id": f"metric_observation:{ticker}:{period}:{doc_type_key}:revenue",
                "type": "MetricObservation",
                "ticker": ticker,
                "source_document_id": source_document_id,
                "document_type": doc_type,
                "period": period,
                "metric_name": "revenue",
                "value": revenue,
                "unit": "USD",
                "fiscal_year": int(period.removeprefix("FY")),
                "fiscal_period": period,
                "period_type": "annual",
                "source_fact_ids": [f"xbrl:{ticker}:{period}:{doc_type_key}:revenue"],
                "source_type": "reported",
                "schema_version": SCHEMA_VERSION,
            }
        ],
    )
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    asyncio.run(generate_edges(None, ontology_dir, ticker, period, doc_type))
    build_indexes(
        ticker=ticker,
        period=period,
        doc_type_key=doc_type_key,
        document_type=doc_type,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=root,
    )
    assert activities
    assert exposures


def _write_context_source_doc(
    root: Path,
    *,
    period: str,
    doc_type: str,
    doc_type_key: str,
    claims: list[dict] | None = None,
    activities: list[dict] | None = None,
    exposures: list[dict] | None = None,
    metric_observations: list[dict] | None = None,
) -> None:
    ticker = "VG"
    ontology_dir = root / "companies" / ticker / "ontology" / doc_type_key / period
    ontology_dir.mkdir(parents=True, exist_ok=True)
    sources_dir = root / "companies" / ticker / "sources" / doc_type_key / period
    sources_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "claims": claims or [],
        "evidence_quotes": [],
        "business_activities": activities or [],
        "external_factor_exposures": exposures or [],
        "metric_observations": metric_observations or [],
        "business_factors": [],
        "agreement_terms": [],
        "business_events": [],
        "rejected_objects": [],
    }
    for key, rows in files.items():
        write_jsonl(ontology_dir / f"{key}.jsonl", rows)
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    rel_base = f"companies/{ticker}/ontology/{doc_type_key}/{period}"
    atomic_write_json(
        ontology_dir / "artifact_index.json",
        {
            "ticker": ticker,
            "document_type": doc_type,
            "doc_type_key": doc_type_key,
            "period": period,
            "files": {key: f"{rel_base}/{key}.jsonl" for key in files},
            "counts": {key: len(rows) for key, rows in files.items()},
            "sources": {},
            "reports": {},
            "schema_version": SCHEMA_VERSION,
        },
    )


def _metric(
    ticker: str,
    period: str,
    doc_type: str,
    doc_type_key: str,
    metric_name: str,
    value: float,
    *,
    fiscal_year: int,
    fiscal_period: str | None = None,
    period_type: str,
) -> dict:
    return {
        "id": f"metric_observation:{ticker}:{period}:{doc_type_key}:{metric_name}:{fiscal_year}:{fiscal_period or 'annual'}",
        "type": "MetricObservation",
        "ticker": ticker,
        "source_document_id": f"source:{ticker}:{period}:{doc_type_key}",
        "document_type": doc_type,
        "period": period,
        "metric_name": metric_name,
        "value": value,
        "unit": "USD",
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "period_type": period_type,
        "source_fact_ids": [f"xbrl:{ticker}:{period}:{doc_type_key}:{metric_name}"],
        "source_type": "reported",
        "schema_version": SCHEMA_VERSION,
    }


def _event_claim(ticker: str, period: str, idx: int) -> dict:
    doc_type_key = "10Q" if "Q" in period else "10K"
    doc_type = "10-Q" if "Q" in period else "10-K"
    return {
        "id": f"claim:{ticker}:{period}:{doc_type_key}:event-{idx}",
        "type": "ResearchClaim",
        "ticker": ticker,
        "source_document_id": f"source:{ticker}:{period}:{doc_type_key}",
        "document_type": doc_type,
        "period": period,
        "claim_text": f"In November 2025, the company filed approval application update number {idx}.",
        "claim_type": "period_update",
        "supported_by_quotes": [],
        "related_metrics": ["revenue"],
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    }


def _exposure(
    ticker: str,
    period: str,
    doc_type: str,
    doc_type_key: str,
    factor: str,
    *,
    evidence_grade: str,
    materiality: str,
) -> dict:
    return {
        "id": f"external_factor_exposure:{ticker}:{period}:{doc_type_key}:{factor}",
        "type": "ExternalFactorExposure",
        "ticker": ticker,
        "source_document_id": f"source:{ticker}:{period}:{doc_type_key}",
        "document_type": doc_type,
        "period": period,
        "factor": factor,
        "factor_category": factor_spec(factor)["category"],
        "impact_channel": "revenue",
        "effect_direction": "negative",
        "mechanism": f"{factor} may affect revenue.",
        "materiality": materiality,
        "evidence_grade": evidence_grade,
        "supported_by_claims": [],
        "supported_by_quotes": [],
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    }


def _read_context_jsonl(root: Path, ticker: str, key: str) -> list[dict]:
    return read_jsonl(root / "companies" / ticker / "context" / f"{key}.jsonl")
