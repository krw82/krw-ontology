from krw_ontology.agent_index.discovery import (
    build_evidence_frame,
    build_query_frame,
    classify_topic_match,
)


def test_discovery_tier_requires_core_premise_alignment() -> None:
    query = build_query_frame("commodity volatility LNG chokepoint margin exposure")

    vg_topic = {
        "topic_id": "topic:VG:lng_feed_gas",
        "ticker": "VG",
        "primary_object_id": "exposure:VG:natural_gas_price",
        "primary_object_type": "ExternalFactorExposure",
        "topic_label": "LNG and feed gas price exposure",
        "topic_summary": "LNG sales, natural gas prices, feed gas costs, margin and cash flow channels.",
        "topic_text": "LNG sales natural gas price feed gas cost Henry Hub margin cash flow",
        "evidence_strength": "direct",
        "support_quote_count": 1,
        "support_claim_count": 1,
        "specificity_score": 0.86,
    }
    aapl_topic = {
        "topic_id": "topic:AAPL:supply_chain_margin",
        "ticker": "AAPL",
        "primary_object_id": "factor:AAPL:supply_chain",
        "primary_object_type": "BusinessFactor",
        "topic_label": "Supply chain and component cost pressure",
        "topic_summary": "Supplier disruption, component cost, gross margin and revenue risk.",
        "topic_text": "supply chain supplier component cost gross margin revenue exposure",
        "evidence_strength": "direct",
        "support_quote_count": 1,
        "support_claim_count": 1,
        "specificity_score": 0.66,
    }
    jpm_topic = {
        "topic_id": "topic:JPM:market_volatility",
        "ticker": "JPM",
        "primary_object_id": "factor:JPM:market_volatility",
        "primary_object_type": "BusinessFactor",
        "topic_label": "Macro market volatility and credit conditions",
        "topic_summary": "Market volatility, credit conditions, deposits, and revenue exposure.",
        "topic_text": "market volatility credit conditions deposit revenue exposure risk",
        "evidence_strength": "direct",
        "support_quote_count": 1,
        "support_claim_count": 1,
        "specificity_score": 0.55,
    }

    vg_match = classify_topic_match(query, build_evidence_frame(vg_topic))
    aapl_match = classify_topic_match(query, build_evidence_frame(aapl_topic))
    jpm_match = classify_topic_match(query, build_evidence_frame(jpm_topic))

    assert vg_match["tier"] == "direct"
    assert vg_match["matched_core_terms"]

    assert aapl_match["tier"] == "related"
    assert not aapl_match["matched_core_terms"]
    assert aapl_match["matched_impact_channels"] == ["gross_margin"]
    assert "lng_market_price" in aapl_match["missing_required_facets"]

    assert jpm_match["tier"] == "indirect"
    assert not jpm_match["matched_core_terms"]
