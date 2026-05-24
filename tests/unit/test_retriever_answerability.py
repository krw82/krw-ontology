from krw_ontology.agent_index.retriever import (
    QueryPlan,
    _annotate_retrieval_answerability,
    _split_retrieval_context,
)
from krw_ontology.agent_index.discovery import build_evidence_frame, build_query_frame, classify_topic_match


def test_direct_exposure_negative_demotes_broad_commodity_context():
    plan = QueryPlan(
        question="VG는 semiconductor memory price cycle 또는 GPU HBM 가격 변동에 직접 노출되어 있나?",
        intent="evidence_search",
        tickers=["VG"],
        document_types=["10-K"],
        periods=["FY2025"],
        period_policy="latest",
        topics=["semiconductor memory price cycle GPU HBM"],
        metric=None,
        object_types=["ExternalFactorExposure"],
        include_rejected=False,
        require_trace=True,
        limit=5,
    )
    candidates = [
        {
            "id": "external_factor_exposure:VG:commodity-price-capex",
            "type": "ExternalFactorExposure",
            "ticker": "VG",
            "text": "Commodity price volatility may affect capital expenditures.",
            "object": {
                "external_factor": "commodity_price",
                "impact_channel": "capital_expenditures",
                "text": "Commodity price volatility may affect capital expenditures.",
            },
            "evidence": {
                "quotes": [
                    {
                        "id": "quote:VG:commodity",
                        "text": "The company may face higher construction commodity costs, including steel and fuel.",
                    }
                ],
                "claims": [],
                "spans": [],
            },
            "quality": {},
        }
    ]

    annotated, answerability = _annotate_retrieval_answerability(plan, candidates)

    assert answerability["direct_answerable"] is False
    assert answerability["negative_answer_supported"] is True
    assert answerability["recommended_answer_mode"] == "no_direct_evidence_with_related_context"
    assert annotated[0]["tier"] == "broad_related_candidate"
    assert "technology_hardware" in annotated[0]["missing_required_facets"]


def test_direct_exposure_requires_all_requested_direct_facets():
    query = build_query_frame("MSFT가 HBM spot price 또는 GPU 가격에 직접 노출되어 있나?")
    evidence = build_evidence_frame(
        {
            "topic_id": "topic:msft:ai-capex",
            "ticker": "MSFT",
            "topic_label": "AI infrastructure and GPU capacity",
            "topic_summary": "AI infrastructure and GPU supply can increase data center capital expenditures.",
            "topic_text": "AI infrastructure GPU supply data center capex hardware supply chain",
            "facet_text": "GPU data center capex",
            "impact_channels": ["capex"],
            "primary_object_id": "factor:MSFT:ai-capex",
            "primary_object_type": "BusinessFactor",
            "evidence_strength": "direct",
            "support_quote_count": 1,
            "support_claim_count": 1,
            "specificity_score": 0.8,
        }
    )

    match = classify_topic_match(query, evidence)

    assert match["tier"] != "direct"
    assert "hbm" in match["missing_required_facets"]


def test_direct_exposure_release_gate_examples_remain_related_not_direct():
    cases = [
        (
            "AAPL이 LNG 가격이나 Henry Hub 가격에 직접 노출되어 있나?",
            "Apple supply chain component costs and freight costs may pressure gross margin.",
            {"lng", "henry_hub"},
        ),
        (
            "V가 원유 가격에 직접 노출되어 있나?",
            "Consumer spending and cross-border payment volume may change with macro conditions.",
            {"crude_oil_price"},
        ),
        (
            "MSFT가 HBM spot price에 직접 노출되어 있나?",
            "AI infrastructure and data center capital expenditures may increase hardware costs.",
            {"hbm"},
        ),
        (
            "AMZN은 Hormuz Strait 봉쇄에 직접 노출되어 있나?",
            "Fuel costs, shipping cost and supply chain disruption may pressure fulfillment costs.",
            {"shipping_chokepoint"},
        ),
    ]

    for question, evidence_text, expected_missing in cases:
        query = build_query_frame(question)
        evidence = build_evidence_frame(
            {
                "topic_id": "topic:test",
                "ticker": "TEST",
                "topic_label": evidence_text,
                "topic_summary": evidence_text,
                "topic_text": evidence_text,
                "facet_text": evidence_text,
                "impact_channels": [],
                "primary_object_id": "factor:test",
                "primary_object_type": "BusinessFactor",
                "evidence_strength": "direct",
                "support_quote_count": 1,
                "support_claim_count": 1,
                "specificity_score": 0.8,
            }
        )
        match = classify_topic_match(query, evidence)

        assert match["tier"] != "direct", question
        assert expected_missing & set(match["missing_required_facets"]), question


def test_direct_exposure_does_not_treat_lng_project_as_henry_hub_price_exposure():
    query = build_query_frame("AAPL이 LNG 가격이나 Henry Hub 가격에 직접 노출되어 있나?")
    evidence = build_evidence_frame(
        {
            "topic_id": "topic:aapl:liquefaction-project",
            "ticker": "AAPL",
            "topic_label": "Project Execution",
            "topic_summary": "liquefaction project development may affect operating expense and segment revenue",
            "topic_text": "project_execution liquefaction_project_development operating_expense revenue",
            "facet_text": "liquefaction project development operating expense revenue",
            "impact_channels": ["operating_expense", "revenue"],
            "primary_object_id": "external_factor_exposure:AAPL:project-execution",
            "primary_object_type": "ExternalFactorExposure",
            "evidence_strength": "direct",
            "support_quote_count": 1,
            "support_claim_count": 1,
            "specificity_score": 0.9,
        }
    )

    match = classify_topic_match(query, evidence)

    assert match["tier"] != "direct"
    assert "henry_hub" in match["missing_required_facets"]


def test_direct_exposure_positive_promotes_traceable_direct_evidence():
    plan = QueryPlan(
        question="VG는 Henry Hub feed gas 가격이 operating margin에 직접 영향을 주나?",
        intent="evidence_search",
        tickers=["VG"],
        document_types=["10-K"],
        periods=["FY2025"],
        period_policy="latest",
        topics=["Henry Hub feed gas operating margin"],
        metric=None,
        object_types=["ExternalFactorExposure"],
        include_rejected=False,
        require_trace=True,
        limit=5,
    )
    candidates = [
        {
            "id": "external_factor_exposure:VG:natural-gas-price-operating-margin",
            "type": "ExternalFactorExposure",
            "ticker": "VG",
            "text": "Natural gas price and feed gas procurement costs affect operating margin.",
            "object": {
                "external_factor": "natural_gas_price",
                "impact_channel": "operating_margin",
                "text": "Henry Hub natural gas and feed gas procurement costs affect operating margin.",
            },
            "evidence": {
                "quotes": [
                    {
                        "id": "quote:VG:feed-gas",
                        "text": "Feed gas prices can increase operating costs and reduce margins.",
                    }
                ],
                "claims": [],
                "spans": [],
            },
            "quality": {},
        }
    ]

    annotated, answerability = _annotate_retrieval_answerability(plan, candidates)

    assert answerability["direct_answerable"] is True
    assert answerability["recommended_answer_mode"] == "direct_evidence"
    assert annotated[0]["tier"] == "traceable_direct"


def test_product_anchor_is_hard_gate_but_channel_is_soft_score():
    plan = QueryPlan(
        question="Was iPhone a revenue growth driver for AAPL?",
        intent="evidence_search",
        tickers=["AAPL"],
        document_types=["10-K"],
        periods=["FY2025"],
        topics=["iPhone revenue growth driver"],
        object_types=["ResearchClaim", "EvidenceQuote"],
        limit=5,
    )
    candidates = [
        {
            "id": "claim:AAPL:services-net-sales",
            "type": "ResearchClaim",
            "ticker": "AAPL",
            "text": "Services net sales increased year over year.",
            "object": {"text": "Services net sales increased year over year."},
            "evidence": {"quotes": [{"id": "quote:AAPL:services", "text": "Services net sales increased."}], "claims": [], "spans": []},
            "quality": {},
        },
        {
            "id": "claim:AAPL:iphone-demand",
            "type": "ResearchClaim",
            "ticker": "AAPL",
            "text": "iPhone demand was strong.",
            "object": {"text": "iPhone demand was strong."},
            "evidence": {"quotes": [{"id": "quote:AAPL:iphone-demand", "text": "iPhone demand was strong."}], "claims": [], "spans": []},
            "quality": {},
        },
        {
            "id": "claim:AAPL:iphone-net-sales",
            "type": "ResearchClaim",
            "ticker": "AAPL",
            "text": "Net sales increased due to higher iPhone sales.",
            "object": {"text": "Net sales increased due to higher iPhone sales."},
            "evidence": {"quotes": [{"id": "quote:AAPL:iphone-sales", "text": "Net sales increased due to higher iPhone sales."}], "claims": [], "spans": []},
            "quality": {},
        },
    ]

    annotated, answerability = _annotate_retrieval_answerability(plan, candidates)
    by_id = {item["id"]: item for item in annotated}

    assert answerability["direct_answerable"] is True
    assert answerability["query_frame"]["must_for_direct"] == ["iphone"]
    assert by_id["claim:AAPL:services-net-sales"]["tier"] == "traceable_related"
    assert by_id["claim:AAPL:services-net-sales"]["anchor_score"] == 0.0
    assert by_id["claim:AAPL:iphone-demand"]["tier"] == "traceable_direct"
    assert by_id["claim:AAPL:iphone-demand"]["channel_score"] < 1.0
    assert by_id["claim:AAPL:iphone-net-sales"]["tier"] == "traceable_direct"
    assert by_id["claim:AAPL:iphone-net-sales"]["channel_score"] == 1.0


def test_split_retrieval_context_exposes_new_contract_buckets():
    context = _split_retrieval_context(
        [
            {"id": "direct", "tier": "traceable_direct"},
            {"id": "related", "tier": "traceable_related"},
            {"id": "candidate", "tier": "untraced_direct_candidate"},
            {"id": "rejected", "tier": "not_answerable"},
        ]
    )

    assert [item["id"] for item in context["direct_evidence"]] == ["direct"]
    assert [item["id"] for item in context["related_context"]] == ["related", "candidate"]
    assert [item["id"] for item in context["rejected_context"]] == ["rejected"]
