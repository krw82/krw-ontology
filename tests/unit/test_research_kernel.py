"""Unit tests for the deterministic ResearchKernel routing layer."""

from __future__ import annotations

import time

from krw_ontology.agent_index.research_kernel import ResearchKernel, request_from_query_context_args
from krw_ontology.agent_index.research_router import context_policy_for_route, route_research
from krw_ontology.agent_index.research_types import ResearchIntent


def test_route_research_metric_series_prefers_metric_context() -> None:
    route = route_research(
        "AAPL의 iPhone과 Services 매출 비중은 2021~2025년에 어떻게 달라졌는지 비교해줘.",
        tickers=["AAPL"],
    )

    assert route.intent == ResearchIntent.METRIC_SERIES
    assert route.primary_context == "metric_context"
    policy = context_policy_for_route(route)
    assert policy["run_metric_series"] is True
    assert policy["run_typed_projection"] is False
    assert "broad_retrieve" in policy["do_not_call"]


def test_route_research_risk_thesis_blocks_deep_metric_series() -> None:
    route = route_research(
        "AMZN의 사이버 보안 관련 비용 증가가 매출성장률을 갉아먹는지 점검해줘.",
        tickers=["AMZN"],
    )

    assert route.intent == ResearchIntent.RISK_THESIS
    assert route.primary_context == "risk_context"
    policy = context_policy_for_route(route)
    assert policy["run_metric_series"] is False
    assert "deep_metric_series" in policy["do_not_call"]


def test_route_research_company_overview_blocks_metric_first() -> None:
    route = route_research(
        "AAPL은 iPhone, Services, Mac으로 어떻게 돈을 벌고 최근 매출 동인은 무엇인지 정리해줘.",
        tickers=["AAPL"],
    )

    assert route.intent == ResearchIntent.COMPANY_OVERVIEW
    assert route.primary_context == "company_overview_context"
    policy = context_policy_for_route(route)
    assert policy["run_metric_series"] is False


def test_route_research_frontend_overview_prompt_is_not_metric_series() -> None:
    route = route_research(
        "아마존에서 North America·International 리테일, AWS 클라우드 서비스, 광고·구독 서비스 중 어디에서 성장과 매출이 나오는지 공시 기준으로 정리해줘.",
        tickers=["AMZN"],
    )

    assert route.intent == ResearchIntent.COMPANY_OVERVIEW
    assert route.primary_context == "company_overview_context"
    policy = context_policy_for_route(route)
    assert policy["run_metric_series"] is False


def test_route_research_growth_contribution_overview_prompt_is_not_metric_series() -> None:
    route = route_research(
        "마스터카드의 결제 네트워크, cross-border volume, switched transactions가 매출 성장에 어떻게 기여하는지 공시 기준으로 정리해줘.",
        tickers=["MA"],
    )

    assert route.intent == ResearchIntent.COMPANY_OVERVIEW
    assert route.primary_context == "company_overview_context"
    policy = context_policy_for_route(route)
    assert policy["run_metric_series"] is False


def test_route_research_security_product_overview_is_not_risk_thesis() -> None:
    route = route_research(
        "시스코의 네트워킹 제품, 보안·구독, 서비스 매출이 성장과 마진에 어떻게 연결되는지 공시 기준으로 정리해줘.",
        tickers=["CSCO"],
    )

    assert route.intent == ResearchIntent.COMPANY_OVERVIEW
    assert route.primary_context == "company_overview_context"
    policy = context_policy_for_route(route)
    assert policy["run_metric_series"] is False


def test_route_research_direct_exposure_uses_direct_context() -> None:
    route = route_research("AAPL이 LNG/Henry Hub 가격에 직접 노출되어 있나?", tickers=["AAPL"])

    assert route.intent == ResearchIntent.DIRECT_EXPOSURE
    assert route.primary_context == "direct_exposure_context"
    policy = context_policy_for_route(route)
    assert "broad_direct_promotion" in policy["do_not_call"]


def test_kernel_envelope_preserves_bounded_autonomy_state() -> None:
    request = request_from_query_context_args(
        question="AMZN의 사이버 보안 관련 비용 증가가 매출성장률을 갉아먹는지 점검해줘.",
        tickers=["AMZN"],
        document_types=None,
        periods=["CY2025"],
        universe=None,
        limit_results=5,
        limit_tickers=1,
    )
    kernel = ResearchKernel()
    route = kernel.route(request)
    envelope = kernel.build_envelope(
        request,
        route=route,
        research_status="sufficient_for_default_answer",
        answerability={"related_context_available": True},
        research_pack={"company_topic_pack": {"top_candidates": [{"ticker": "AMZN"}]}},
        missing_parts=[],
        recommended_tools=[],
        agent_autonomy={
            "allowed_next_tools": ["krw_ontology_trace"],
            "max_additional_tool_calls": 1,
        },
        do_not_call=["krw_ontology_retrieve"],
        started_at=time.perf_counter(),
    )

    assert envelope["version"] == "research_kernel_v0.1"
    assert envelope["contract_version"] == "kernel.v1alpha"
    assert envelope["intent"] == "risk_thesis"
    assert envelope["primary_context"] == "risk_context"
    assert envelope["status"] == "sufficient_for_default_answer"
    assert envelope["answer_mode"] == "risk_mechanism_answer"
    assert envelope["allowed_next_tools"] == ["krw_ontology_trace"]
    assert envelope["max_additional_tool_calls"] == 1
    assert "krw_ontology_retrieve" in envelope["do_not_call"]
    assert "deep_metric_series" in envelope["do_not_call"]
    assert envelope["budget"]["budget_ms"] == 5000
