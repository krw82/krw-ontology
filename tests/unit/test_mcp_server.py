"""Tests for the krw-ontology MCP tool layer."""

from __future__ import annotations

import json
from pathlib import Path

from krw_ontology.agent_index import OntologyStore, build_agent_index
from krw_ontology.mcp_server.server import health_payload, mcp
from krw_ontology.mcp_server.tools import (
    _normalize_object_types,
    catalog_tool,
    chain_tool,
    compare_tool,
    plan_query_tool,
    quality_tool,
    query_tool,
    trace_tool,
    topic_map_tool,
)
from krw_ontology.utils.io import atomic_write_json, write_jsonl


def test_mcp_tools_query_trace_quality_and_compare(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    index = build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    catalog = json.loads(catalog_tool())
    assert catalog["index_path"] == str(index["index_path"])
    assert catalog["companies"] == ["VG"]
    assert catalog["documents"][0]["period"] == "FY2025"

    query = json.loads(
        query_tool(
            topic="revenue growth",
            tickers=["VG"],
            document_types=["10-K"],
            object_types=["ResearchClaim", "BusinessFactor"],
            limit=5,
        )
    )
    assert query["results"]
    assert query["results"][0]["ticker"] == "VG"
    assert query["results"][0]["evidence"]["quotes"] == []
    assert "events" in query["results"][0]["quality"]
    assert "evidence_grade" in query["results"][0]["quality"]
    assert query["search_diagnostics"]["normalized_terms"] == ["revenue", "growth"]
    assert query["search_diagnostics"]["fts_query"] == "revenue* growth*"
    assert query["response_detail"] == "compact"
    assert "object" not in query["results"][0]
    assert "document" not in query["results"][0]

    trace = json.loads(trace_tool(object_id="claim:VG:FY2025:10K:revenue-growth"))
    assert trace["object"]["type"] == "ResearchClaim"
    assert trace["evidence"]["quotes"][0]["id"] == "quote:VG:FY2025:10K:0001"

    chain = json.loads(chain_tool(object_id="business_factor:VG:FY2025:10K:revenue-growth"))
    assert chain["object"]["type"] == "BusinessFactor"
    assert chain["chain"]["evidence_chain"]["claims"][0]["id"] == "claim:VG:FY2025:10K:revenue-growth"
    assert chain["chain"]["evidence_chain"]["quotes"][0]["id"] == "quote:VG:FY2025:10K:0001"
    assert "text" not in chain["chain"]["evidence_chain"]["quotes"][0]
    assert {
        neighbor["object"]["id"] for neighbor in chain["chain"]["semantic_neighbors"]
    } >= {
        "business_activity:VG:FY2025:10K:lng-sales",
        "external_factor_exposure:VG:FY2025:10K:natural-gas-price-operating-margin",
    }

    quality = json.loads(quality_tool(ticker="VG"))
    assert quality["summary"]["documents"] == 2
    assert quality["summary"]["section_warnings"] == 0

    compare = json.loads(
        compare_tool(
            tickers=["VG", "XOM"],
            topic="revenue growth",
            document_types=["10-K"],
            limit_per_ticker=2,
        )
    )
    assert compare["results"]["VG"]
    assert compare["results"]["XOM"] == []
    assert {row["comparison_key"] for row in compare["comparison_rows"]} == {"VG", "XOM"}
    vg_row = next(row for row in compare["comparison_rows"] if row["comparison_key"] == "VG")
    xom_row = next(row for row in compare["comparison_rows"] if row["comparison_key"] == "XOM")
    assert vg_row["missing"] is False
    assert vg_row["ticker"] == "VG"
    assert vg_row["source_label"] == "VG FY2025 10-K"
    assert vg_row["object_id"]
    assert xom_row["missing"] is True
    assert xom_row["missing_reason"] == "no_matching_ontology_objects"

    plan = json.loads(plan_query_tool(question="VG 최근 10-K revenue growth 근거 찾아줘"))
    assert plan["plan"]["tickers"] == ["VG"]
    assert plan["plan"]["document_types"] == ["10-K"]


def test_mcp_query_normalizes_object_type_aliases(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    query = json.loads(
        query_tool(
            topic="regulatory risk",
            tickers=["VG"],
            object_types=["Risk"],
            limit=5,
        )
    )

    assert query["query"]["object_types_requested"] == ["Risk"]
    assert query["query"]["object_types"] == ["BusinessFactor"]
    assert query["results"][0]["type"] == "BusinessFactor"
    assert query["results"][0]["evidence"]["quotes"] == []

    metric_query = json.loads(
        query_tool(
            tickers=["VG"],
            object_types=["FinancialMetric"],
            limit=1,
        )
    )
    assert metric_query["query"]["object_types_requested"] == ["FinancialMetric"]
    assert metric_query["query"]["object_types"] == ["MetricObservation"]


def test_mcp_event_aliases_are_not_overwritten():
    event_types, invalid = _normalize_object_types(["event"])
    assert invalid == []
    assert event_types == ["BusinessEvent", "ChangeEvent"]

    business_event_types, invalid = _normalize_object_types(["business_event"])
    assert invalid == []
    assert business_event_types == ["BusinessEvent"]

    change_event_types, invalid = _normalize_object_types(["change_event", "disclosure_change"])
    assert invalid == []
    assert change_event_types == ["ChangeEvent"]


def test_mcp_query_falls_back_for_korean_topic(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    query = json.loads(
        query_tool(
            topic="유럽 가스 비축 부족",
            tickers=["VG"],
            object_types=["ExternalFactorExposure", "BusinessActivity", "ResearchClaim"],
            limit=5,
        )
    )

    assert query["results"]
    assert query["search_diagnostics"]["normalized_terms"] == []
    assert "natural_gas_price" in query["search_diagnostics"]["expanded_terms"]
    assert query["search_diagnostics"]["search_strategy"]["selected_mode"] in {
        "split_and",
        "relaxed_or",
    }
    assert "topic_rewritten_for_search" in query["search_diagnostics"]["warnings"]


def test_mcp_topic_map_repackages_company_vocabulary(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    topic_map = json.loads(topic_map_tool(ticker="VG", limit=10))

    assert topic_map["ticker"] == "VG"
    assert topic_map["source"]["company_business_profile_ids"] == [
        "company_business_profile:VG:ALL"
    ]
    assert topic_map["source"]["fallback_used"] is False
    factor_terms = {
        entry["term"] for entry in topic_map["topics"]["external_factors"]
    }
    activity_terms = {
        entry["term"] for entry in topic_map["topics"]["business_activities"]
    }
    metric_terms = {entry["term"] for entry in topic_map["topics"]["metrics"]}
    assert "natural_gas_price" in factor_terms
    assert "lng_sales" in activity_terms
    assert "revenue" in metric_terms
    assert any(
        suggestion["topic"] == "natural gas price"
        for suggestion in topic_map["suggested_first_queries"]
    )


def test_mcp_chain_returns_object_specific_chains(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    exposure_chain = json.loads(
        chain_tool(
            object_id="external_factor_exposure:VG:FY2025:10K:natural-gas-price-operating-margin"
        )
    )
    assert exposure_chain["object"]["type"] == "ExternalFactorExposure"
    assert exposure_chain["quality"]["evidence_grade"] == "direct"
    assert exposure_chain["quality"]["warnings"] == []
    assert exposure_chain["chain"]["evidence_chain"]["claims"][0]["type"] == "ResearchClaim"
    assert "text" not in exposure_chain["chain"]["evidence_chain"]["quotes"][0]

    activity_chain = json.loads(chain_tool(object_id="business_activity:VG:FY2025:10K:lng-sales"))
    assert activity_chain["object"]["type"] == "BusinessActivity"
    assert {
        item["id"] for item in activity_chain["chain"]["temporal_context"]
    } >= {
        "trend:VG:ALL:lng-sales-revenue",
        "change_event:VG:ALL:revenue-growth",
    }

    claim_chain = json.loads(chain_tool(object_id="claim:VG:FY2025:10K:revenue-growth"))
    assert claim_chain["object"]["type"] == "ResearchClaim"
    assert claim_chain["chain"]["evidence_chain"]["quotes"][0]["id"] == "quote:VG:FY2025:10K:0001"

    quote_chain = json.loads(chain_tool(object_id="quote:VG:FY2025:10K:0001"))
    assert quote_chain["object"]["type"] == "EvidenceQuote"
    assert quote_chain["chain"]["evidence_chain"]["claims"][0]["id"] == "claim:VG:FY2025:10K:revenue-growth"


def test_mcp_chain_respects_depth_and_quote_text_option(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    shallow = json.loads(
        chain_tool(
            object_id="business_factor:VG:FY2025:10K:revenue-growth",
            max_depth=1,
            direction="incoming",
        )
    )
    shallow_path_ids = [
        step["object"]["id"]
        for path in shallow["chain"]["edge_paths"]
        for step in path["steps"]
    ]
    assert "claim:VG:FY2025:10K:revenue-growth" in shallow_path_ids
    assert "quote:VG:FY2025:10K:0001" not in shallow_path_ids

    deeper = json.loads(
        chain_tool(
            object_id="business_factor:VG:FY2025:10K:revenue-growth",
            max_depth=2,
            direction="incoming",
            include_quote_text=True,
        )
    )
    deep_path_ids = [
        step["object"]["id"]
        for path in deeper["chain"]["edge_paths"]
        for step in path["steps"]
    ]
    assert "quote:VG:FY2025:10K:0001" in deep_path_ids
    assert deeper["chain"]["evidence_chain"]["quotes"][0]["text"]


def test_mcp_chain_reports_missing_ambiguous_and_unsupported_objects(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    missing = json.loads(chain_tool(object_id="business_factor:VG:missing"))
    assert missing["error"]["code"] == "not_found"

    ambiguous = json.loads(chain_tool(object_id="claim:VG"))
    assert ambiguous["error"]["code"] == "ambiguous_object_id"

    unsupported = json.loads(chain_tool(object_id="business_factor:VG:FY2025:10K:unsupported-risk"))
    assert "no_supporting_evidence_found" in unsupported["quality"]["warnings"]


def test_mcp_compare_allows_single_ticker_period_comparison(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path, period="FY2024", text="Revenue growth faced operational risk.")
    _write_fixture(tmp_path, period="FY2025", text="Revenue growth faced regulatory risk.")
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    compare = json.loads(
        compare_tool(
            tickers=["VG"],
            topic="revenue growth",
            document_types=["10-K"],
            periods=["FY2024", "FY2025"],
            limit_per_ticker=2,
        )
    )

    assert compare["mode"] == "period_topic"
    assert set(compare["results"]) == {"FY2024", "FY2025"}
    assert compare["results"]["FY2024"]
    assert compare["results"]["FY2025"]
    assert {row["comparison_key"] for row in compare["comparison_rows"]} == {"FY2024", "FY2025"}
    assert all(row["ticker"] == "VG" for row in compare["comparison_rows"])
    assert all(row["missing"] is False for row in compare["comparison_rows"])


def test_mcp_trace_accepts_unique_id_prefix(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    trace = json.loads(trace_tool(object_id="claim:VG:FY2025:10K:revenue"))

    assert trace["resolved_from_prefix"] == "claim:VG:FY2025:10K:revenue"
    assert trace["object"]["id"] == "claim:VG:FY2025:10K:revenue-growth"


def test_mcp_trace_returns_ambiguous_prefix_candidates(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    trace = json.loads(trace_tool(object_id="claim:VG"))

    assert trace["error"]["code"] == "ambiguous_object_id"
    assert {candidate["id"] for candidate in trace["candidates"]} >= {
        "claim:VG:FY2025:10K:revenue-growth",
        "claim:VG:FY2025:10K:regulatory-risk",
    }


def test_mcp_server_registers_expected_tools():
    tool_names = {tool.name for tool in mcp._tool_manager.list_tools()}

    assert {
        "krw_ontology_catalog",
        "krw_ontology_query",
        "krw_ontology_topic_map",
        "krw_ontology_retrieve",
        "krw_ontology_trace",
        "krw_ontology_chain",
        "krw_ontology_quality",
        "krw_ontology_compare",
        "krw_ontology_plan_query",
    }.issubset(tool_names)


def test_mcp_health_payload_reports_index_counts(tmp_path: Path):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)

    payload, status_code = health_payload(root=str(tmp_path))

    assert status_code == 200
    assert payload["ok"] is True
    assert payload["root"] == str(tmp_path.resolve())
    assert payload["documents"] == 2
    assert payload["objects"] >= 1
    assert "krw_ontology_topic_map" in payload["tools"]


def test_mcp_health_payload_reports_missing_index(tmp_path: Path):
    payload, status_code = health_payload(root=str(tmp_path))

    assert status_code == 503
    assert payload["ok"] is False
    assert payload["error"] == "agent_index_not_found"


def _write_fixture(
    root: Path,
    *,
    period: str = "FY2025",
    text: str = "Revenue growth accelerated because customer demand increased for LNG volumes.",
) -> None:
    ontology_dir = root / "companies" / "VG" / "ontology" / "10K" / period
    sources_dir = root / "companies" / "VG" / "sources" / "10K" / period
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    source_document_id = f"source:VG:{period}:10K"
    span_id = f"span:VG:{period}:10K:0001"
    quote_id = f"quote:VG:{period}:10K:0001"
    claim_id = f"claim:VG:{period}:10K:revenue-growth"
    risk_claim_id = f"claim:VG:{period}:10K:regulatory-risk"
    driver_id = f"business_factor:VG:{period}:10K:revenue-growth"
    risk_id = f"business_factor:VG:{period}:10K:regulatory-risk"
    unsupported_risk_id = f"business_factor:VG:{period}:10K:unsupported-risk"
    activity_id = f"business_activity:VG:{period}:10K:lng-sales"
    exposure_id = f"external_factor_exposure:VG:{period}:10K:natural-gas-price-operating-margin"
    agreement_id = f"agreement:VG:{period}:10K:spa-termination"
    span = {
        "id": span_id,
        "type": "SourceSpan",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": period,
        "section_name": "item7",
        "section_key": "item7",
        "span_index": 1,
        "text": text,
        "review_status": "accepted",
    }
    quote = {
        "id": quote_id,
        "type": "EvidenceQuote",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": period,
        "source_span_id": span_id,
        "quote_text": text,
        "quote_type": "business_update",
        "section_name": "item7",
        "review_status": "accepted",
    }
    claim = {
        "id": claim_id,
        "type": "ResearchClaim",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": period,
        "claim_text": "Revenue growth accelerated because customer demand increased.",
        "claim_type": "business_update",
        "supported_by_quotes": [quote_id],
        "related_metrics": ["revenue"],
        "review_status": "accepted",
    }
    risk_claim = {
        "id": risk_claim_id,
        "type": "ResearchClaim",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": period,
        "claim_text": "Regulatory risk could delay project approvals.",
        "claim_type": "risk_assessment",
        "supported_by_quotes": [quote_id],
        "related_metrics": ["revenue"],
        "review_status": "accepted",
    }
    driver = {
        "id": driver_id,
        "type": "BusinessFactor",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": period,
        "name": "Revenue growth",
        "factor_roles": ["growth_driver"],
        "description": "Customer demand increased and supported revenue growth.",
        "category": "demand",
        "supported_by_claims": [claim_id],
        "review_status": "accepted",
    }
    risk = {
        "id": risk_id,
        "type": "BusinessFactor",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": period,
        "name": "Regulatory risk",
        "factor_roles": ["risk"],
        "description": "Regulatory risk could delay approvals and pressure revenue growth.",
        "category": "regulatory",
        "supported_by_claims": [risk_claim_id],
        "review_status": "accepted",
    }
    unsupported_risk = {
        "id": unsupported_risk_id,
        "type": "BusinessFactor",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": period,
        "name": "Unsupported risk",
        "factor_roles": ["risk"],
        "description": "Unsupported risk has no supporting claim.",
        "category": "operational",
        "supported_by_claims": [],
        "review_status": "accepted",
    }
    activity = {
        "id": activity_id,
        "type": "BusinessActivity",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": period,
        "name": "LNG sales",
        "activity_type": "lng_sales",
        "description": "VG sells LNG under long-term SPAs and spot cargoes.",
        "related_metrics": ["revenue", "cash_flow"],
        "supported_by_claims": [claim_id],
        "review_status": "accepted",
    }
    exposure = {
        "id": exposure_id,
        "type": "ExternalFactorExposure",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": period,
        "factor": "natural_gas_price",
        "factor_category": "commodity_price",
        "benchmark": "Henry Hub",
        "impact_channel": "operating_margin",
        "effect_direction": "negative",
        "mechanism": "Feed gas costs can affect operating margin.",
        "evidence_grade": "direct",
        "supported_by_claims": [claim_id],
        "review_status": "accepted",
    }
    agreement = {
        "id": agreement_id,
        "type": "AgreementTerm",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
        "period": period,
        "name": "SPA termination and debt acceleration",
        "agreement_type": "sale and purchase agreement",
        "agreement_subtype": "SPA",
        "counterparty": "LNG customer",
        "termination_terms": "Termination of the SPA may create project financing or debt acceleration risk.",
        "covenant_terms": "Project financing covenants may be affected by contract termination.",
        "affected_channels": ["liquidity", "project_timing"],
        "supported_by_claims": [risk_claim_id],
        "review_status": "accepted",
    }
    edges = [
        {
            "id": "edge:quote-claim",
            "type": "Edge",
            "ticker": "VG",
            "source_document_id": source_document_id,
            "document_type": "10-K",
            "period": period,
            "from_id": quote_id,
            "to_id": claim_id,
            "relation_id": "supports",
            "relation_name": "supports",
            "review_status": "accepted",
        },
        {
            "id": "edge:claim-driver",
            "type": "Edge",
            "ticker": "VG",
            "source_document_id": source_document_id,
            "document_type": "10-K",
            "period": period,
            "from_id": claim_id,
            "to_id": driver_id,
            "relation_id": "supports",
            "relation_name": "supports",
            "review_status": "accepted",
        },
        {
            "id": "edge:claim-risk",
            "type": "Edge",
            "ticker": "VG",
            "source_document_id": source_document_id,
            "document_type": "10-K",
            "period": period,
            "from_id": risk_claim_id,
            "to_id": risk_id,
            "relation_id": "supports",
            "relation_name": "supports",
            "review_status": "accepted",
        },
    ]

    write_jsonl(ontology_dir / "spans.jsonl", [span])
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", [quote])
    write_jsonl(ontology_dir / "claims.jsonl", [claim, risk_claim])
    write_jsonl(ontology_dir / "business_factors.jsonl", [risk, unsupported_risk, driver])
    write_jsonl(ontology_dir / "business_activities.jsonl", [activity])
    write_jsonl(ontology_dir / "external_factor_exposures.jsonl", [exposure])
    write_jsonl(ontology_dir / "agreement_terms.jsonl", [agreement])
    write_jsonl(ontology_dir / "edges.jsonl", edges)
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    atomic_write_json(
        ontology_dir / "artifact_index.json",
        {
            "ticker": "VG",
            "document_type": "10-K",
            "doc_type_key": "10K",
            "period": period,
            "files": {
                "spans": f"companies/VG/ontology/10K/{period}/spans.jsonl",
                "evidence_quotes": f"companies/VG/ontology/10K/{period}/evidence_quotes.jsonl",
                "claims": f"companies/VG/ontology/10K/{period}/claims.jsonl",
                "business_factors": f"companies/VG/ontology/10K/{period}/business_factors.jsonl",
                "business_activities": f"companies/VG/ontology/10K/{period}/business_activities.jsonl",
                "external_factor_exposures": f"companies/VG/ontology/10K/{period}/external_factor_exposures.jsonl",
                "agreement_terms": f"companies/VG/ontology/10K/{period}/agreement_terms.jsonl",
                "edges": f"companies/VG/ontology/10K/{period}/edges.jsonl",
            },
            "counts": {
                "spans": 1,
                "evidence_quotes": 1,
                "claims": 2,
                "business_factors": 3,
                "business_activities": 1,
                "external_factor_exposures": 1,
                "agreement_terms": 1,
                "edges": 3,
            },
        },
    )
    _write_context_fixture(
        root,
        period=period,
        activity_id=activity_id,
        exposure_id=exposure_id,
        claim_id=claim_id,
        quote_id=quote_id,
    )


def _write_context_fixture(
    root: Path,
    *,
    period: str,
    activity_id: str,
    exposure_id: str,
    claim_id: str,
    quote_id: str,
) -> None:
    context_dir = root / "companies" / "VG" / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    profile = {
        "id": "company_business_profile:VG:ALL",
        "type": "CompanyBusinessProfile",
        "ticker": "VG",
        "source_document_id": "source:VG:ALL:COMPANY",
        "document_type": "COMPANY",
        "period": "ALL",
        "sector": "energy_lng",
        "business_model_summary": "Primary activities: LNG sales. Key external factors: natural_gas_price.",
        "primary_business_activities": ["LNG sales"],
        "primary_revenue_sources": ["LNG sales"],
        "primary_cost_sources": ["Feed gas procurement"],
        "key_external_factors": ["natural_gas_price"],
        "key_metrics": ["revenue", "operating_margin"],
        "key_uncertainties": ["natural_gas_price via operating_margin"],
        "source_object_ids": [activity_id, exposure_id],
        "review_status": "accepted",
    }
    trend = {
        "id": "trend:VG:ALL:lng-sales-revenue",
        "type": "TrendObservation",
        "ticker": "VG",
        "source_document_id": "source:VG:ALL:COMPANY",
        "document_type": "COMPANY",
        "period": "ALL",
        "subject": "LNG sales",
        "metric_or_factor": "revenue",
        "from_period": "FY2024",
        "to_period": period,
        "direction": "increased",
        "magnitude_text": None,
        "interpretation": "LNG sales activity and natural gas exposure are context for revenue growth.",
        "supported_by_objects": [activity_id, exposure_id],
        "confidence": "medium",
        "review_status": "accepted",
    }
    change = {
        "id": "change_event:VG:ALL:revenue-growth",
        "type": "ChangeEvent",
        "ticker": "VG",
        "source_document_id": "source:VG:ALL:COMPANY",
        "document_type": "COMPANY",
        "period": "ALL",
        "event_type": "growth_signal",
        "event_date": None,
        "description": "Revenue growth was tied to customer demand and LNG sales context.",
        "affected_objects": [activity_id],
        "supported_by_claims": [claim_id],
        "supported_by_quotes": [quote_id],
        "confidence": "medium",
        "review_status": "accepted",
    }
    edges = [
        {
            "id": "edge:VG:ALL:claim-change",
            "type": "Edge",
            "ticker": "VG",
            "source_document_id": "source:VG:ALL:COMPANY",
            "document_type": "COMPANY",
            "period": "ALL",
            "from_id": claim_id,
            "to_id": change["id"],
            "relation_id": "supports_change_event",
            "relation_name": "supports_change_event",
            "edge_class": "event",
            "evidence_level": "direct",
            "generation_method": "test_fixture",
            "rationale": "The claim supports this change event.",
            "confidence": "high",
            "review_status": "accepted",
        },
        {
            "id": "edge:VG:ALL:change-activity",
            "type": "Edge",
            "ticker": "VG",
            "source_document_id": "source:VG:ALL:COMPANY",
            "document_type": "COMPANY",
            "period": "ALL",
            "from_id": change["id"],
            "to_id": activity_id,
            "relation_id": "affects_object",
            "relation_name": "affects_object",
            "edge_class": "event",
            "evidence_level": "inferred",
            "generation_method": "test_fixture",
            "rationale": "The change event affects this business activity.",
            "confidence": "medium",
            "review_status": "accepted",
        },
    ]
    write_jsonl(context_dir / "company_business_profiles.jsonl", [profile])
    write_jsonl(context_dir / "trend_observations.jsonl", [trend])
    write_jsonl(context_dir / "change_events.jsonl", [change])
    write_jsonl(context_dir / "edges.jsonl", edges)
    atomic_write_json(
        context_dir / "artifact_index.json",
        {
            "ticker": "VG",
            "document_type": "COMPANY",
            "doc_type_key": "COMPANY",
            "period": "ALL",
            "files": {
                "company_business_profiles": "companies/VG/context/company_business_profiles.jsonl",
                "trend_observations": "companies/VG/context/trend_observations.jsonl",
                "change_events": "companies/VG/context/change_events.jsonl",
                "edges": "companies/VG/context/edges.jsonl",
            },
            "counts": {
                "company_business_profiles": 1,
                "trend_observations": 1,
                "change_events": 1,
                "edges": 2,
            },
            "schema_version": "0.1.0",
            "source_period": period,
        },
    )


def test_query_ticker_summary_discovery_contract(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)

    payload = json.loads(
        query_tool(
            root=str(tmp_path),
            topic="commodity volatility natural gas price revenue exposure",
            object_types=["ExternalFactorExposure", "ResearchClaim", "EvidenceQuote", "SupportLink"],
            response_detail="ticker_summary",
            group_by="ticker",
            limit=20,
            limit_groups=5,
            limit_per_group=2,
            answer_candidate_only=True,
        )
    )

    assert payload["response_detail"] == "ticker_summary"
    assert payload["query"]["group_by"] == "ticker"
    assert payload["query"]["answer_candidate_only"] is True
    assert payload["query_frame"]["core_domain_terms"]
    assert payload["ticker_candidates"]
    assert payload["ticker_candidates"][0]["ticker"] == "VG"
    assert payload["ticker_candidates"][0]["tier"] == "traceable_direct"
    assert payload["ticker_candidates"][0]["trace_status"] == "traceable"
    assert payload["ticker_candidates"][0]["trace_counts"]["evidence_chains"] > 0
    assert payload["ticker_candidates"][0]["top_object_ids"]
    assert payload["ticker_candidates"][0]["matched_topics"]
    reason = payload["ticker_candidates"][0]["top_reasons"][0]
    assert isinstance(reason, dict)
    assert reason["semantic_relevance"] == "direct"
    assert reason["trace_status"] == "traceable"
    assert reason["matched_core_terms"]
    assert "missing_required_facets" in reason
    assert "SupportLink" not in payload["ticker_candidates"][0]["matched_object_counts"]
    assert "VG" in payload["results_by_ticker"]
    assert len(payload["results_by_ticker"]["VG"]) <= 2


def test_query_ids_only_response_detail_contract(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)

    payload = json.loads(
        query_tool(
            root=str(tmp_path),
            topic="revenue growth",
            response_detail="ids_only",
            limit=3,
        )
    )

    assert payload["response_detail"] == "ids_only"
    assert payload["results"]
    assert set(payload["results"][0]) == {"id", "type", "ticker", "document_id"}


def test_retrieve_ticker_summary_discovery_contract(tmp_path: Path) -> None:
    from krw_ontology.mcp_server.tools import retrieve_tool

    _write_fixture(tmp_path)
    build_agent_index(tmp_path)

    payload = json.loads(
        retrieve_tool(
            root=str(tmp_path),
            question="commodity volatility and natural gas price exposure",
            response_detail="ticker_summary",
            group_by="ticker",
            limit=20,
            limit_groups=5,
            limit_per_group=2,
            answer_candidate_only=True,
        )
    )

    assert payload["response_detail"] == "ticker_summary"
    assert payload["query"]["group_by"] == "ticker"
    assert payload["query"]["answer_candidate_only"] is True
    assert payload["query_frame"]["core_domain_terms"]
    assert payload["ticker_candidates"]
    assert payload["ticker_candidates"][0]["ticker"] == "VG"
    assert payload["ticker_candidates"][0]["tier"] in {
        "traceable_direct",
        "untraced_direct_candidate",
        "traceable_related",
        "untraced_related",
    }
    assert payload["ticker_candidates"][0]["top_reasons"][0]["matched_core_terms"]
    assert len(payload["results_by_ticker"]["VG"]) <= 2


def test_ticker_summary_demotes_untraced_direct_candidate(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    exposure_path = (
        tmp_path
        / "companies"
        / "VG"
        / "ontology"
        / "10K"
        / "FY2025"
        / "external_factor_exposures.jsonl"
    )
    exposures = json.loads(f"[{exposure_path.read_text().strip().replace(chr(10), ',')}]")
    exposures[0]["supported_by_claims"] = []
    exposures[0]["supported_by_quotes"] = []
    write_jsonl(exposure_path, exposures)
    build_agent_index(tmp_path)

    payload = json.loads(
        query_tool(
            root=str(tmp_path),
            topic="feed gas costs Henry Hub operating margin",
            response_detail="ticker_summary",
            group_by="ticker",
            tickers=["VG"],
            limit=20,
            limit_groups=5,
            limit_per_group=2,
        )
    )

    candidate = payload["ticker_candidates"][0]
    assert candidate["tier"] == "untraced_direct_candidate"
    assert candidate["top_reasons"][0]["semantic_relevance"] == "direct"
    assert candidate["top_reasons"][0]["trace_status"] == "orphan"
    assert candidate["untraced_object_ids"]


def test_typed_projection_lookup_tables_and_query_routing(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    index = build_agent_index(tmp_path)

    with OntologyStore(index["index_path"]) as store:
        context = store.index_context()
        serving_counts = context["serving_counts"]
        assert serving_counts["exposure_lookup"] == 1
        assert serving_counts["agreement_lookup"] == 1
        assert serving_counts["event_lookup"] >= 1
        assert serving_counts["factor_lookup"] >= 2
        assert context["capabilities"]["typed_projection_lookup"] is True

        agreement_results, agreement_diagnostics = store.query_compact_with_diagnostics(
            topic="SPA termination debt acceleration covenant",
            tickers=["VG"],
            object_types=["AgreementTerm"],
            limit=5,
        )
        assert agreement_results
        assert agreement_results[0]["type"] == "AgreementTerm"
        assert agreement_diagnostics["typed_projection_fast_path"] is True
        assert agreement_diagnostics["projection"]["projection_used"] == "agreement_lookup"
        assert agreement_diagnostics["projection"]["fallback_used"] is False

        exposure_results, exposure_diagnostics = store.query_compact_with_diagnostics(
            topic="Henry Hub natural gas price exposure operating margin",
            tickers=["VG"],
            object_types=["ExternalFactorExposure"],
            limit=5,
        )
        assert exposure_results
        assert exposure_results[0]["type"] == "ExternalFactorExposure"
        assert exposure_diagnostics["projection"]["projection_used"] == "exposure_lookup"

        event_context = store.query_context(
            question="VG regulatory approval delay timeline",
            ticker="VG",
            limit_results=5,
            limit_tickers=3,
        )
        typed_projection = event_context["search_diagnostics"].get("typed_projection")
        assert typed_projection is not None
        assert typed_projection["projection_used"] == "event_lookup"
