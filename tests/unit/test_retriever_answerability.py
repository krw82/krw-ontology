from krw_ontology.agent_index.retriever import (
    QueryPlan,
    _annotate_retrieval_answerability,
    _split_retrieval_context,
)


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
