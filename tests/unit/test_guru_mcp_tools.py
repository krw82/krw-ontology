"""Tests for the dedicated Guru Advisor MCP tool layer."""

from __future__ import annotations

import json
from pathlib import Path

from krw_ontology.guru import mcp_server
from krw_ontology.guru import mcp_tools
from krw_ontology.guru import lens_selector
from krw_ontology.guru.lens_selector import guru_select_lenses_tool, select_guru_lenses
from krw_ontology.guru.mcp_tools import (
    guru_chain_tool,
    guru_context_tool,
    guru_data_needs_tool,
    guru_eval_questions_tool,
    guru_evidence_tool,
    guru_query_context_tool,
    guru_search_tool,
    guru_status_tool,
    guru_trace_tool,
)


def test_guru_mcp_server_registers_read_only_tool_names() -> None:
    tool_names = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}

    assert {
        "krw_guru_status",
        "krw_guru_search",
        "krw_guru_query_context",
        "krw_guru_select_lenses",
        "krw_guru_context",
        "krw_guru_trace",
        "krw_guru_chain",
        "krw_guru_evidence",
        "krw_guru_data_needs",
        "krw_guru_eval_questions",
    } <= tool_names


def test_guru_status_search_context_evidence_and_data_needs(tmp_path: Path) -> None:
    root = _write_reviewed_fixture(tmp_path)

    status = json.loads(guru_status_tool(root=root))
    assert status["ok"] is True
    assert status["counts"]["guru_objects"] == 1
    assert status["counts"]["consultation_objects"] == 1
    assert status["counts"]["data_needs"] == 1

    search = json.loads(
        guru_search_tool(
            root=root,
            query="AAPL 버핏 현금흐름 자본배분",
            author_keys=["buffett"],
        )
    )
    assert search["results"]
    assert search["results"][0]["author_key"] == "buffett"
    assert search["usage"]["company_facts_policy"].startswith("Guru results")
    assert "relationships" not in search["results"][0]

    query_context = json.loads(
        guru_query_context_tool(
            root=root,
            question="내가 AAPL을 샀는데 버핏 관점에서 장기 보유해도 되는지 봐줘",
            ticker="AAPL",
            author_keys=["buffett"],
        )
    )
    assert query_context["research_context_version"] == "krw-guru-query-context/v1"
    assert query_context["research_status"] == "needs_company_evidence"
    assert query_context["answerability"]["recommended_answer_mode"] == "lens_with_company_bridge"
    assert query_context["research_pack"]["format"] == "krw-guru-research-pack/v1"
    assert query_context["research_pack"]["pack_meta"]["guru_keys"] == ["buffett"]
    assert query_context["research_pack"]["persona_profile"]["source"] == "guru_ontology"
    assert query_context["research_pack"]["selected_lenses"]
    assert query_context["research_pack"]["selected_lenses"][0]["applicability"]["strong_for"] == [
        "holding_review",
        "business_quality_check",
    ]
    assert query_context["research_pack"]["selected_lenses"][0]["answer_role"]["default"] == "core_lens"
    assert query_context["research_pack"]["company_bridge"]["requires_company_evidence"] is True
    assert "krw_guru_search for broad re-ranking" in query_context["do_not_call"]

    lens_selection = json.loads(
        guru_select_lenses_tool(
            root=root,
            question="내가 AAPL을 샀는데 버핏 관점에서 장기 보유해도 되는지 봐줘",
            ticker="AAPL",
            author_keys=["buffett"],
        )
    )
    assert lens_selection["lens_selection_version"] == "krw-guru-lens-selection/v1"
    assert lens_selection["selection_status"] == "ready_for_company_bridge"
    assert lens_selection["requires_company_evidence"] is True
    assert lens_selection["selected_lenses"][0]["reviewed_id"] == (
        "guru:buffett:principle:cash-owner-earnings:test"
    )
    assert lens_selection["selected_lenses"][0]["related_data_needs"][0]["data_need_key"] == (
        "cash_flow_capital_allocation"
    )
    assert "cash_flow" in lens_selection["company_bridge"]["filing_evidence_requirements"]
    assert lens_selection["company_bridge"]["use_existing_krw_ontology_mcp"] is True

    context = json.loads(
        guru_context_tool(
            root=root,
            question="내가 AAPL을 샀는데 버핏 관점에서 장기 보유해도 되는지 봐줘",
            ticker="AAPL",
            author_keys=["buffett"],
        )
    )
    assert context["requires_company_evidence"] is True
    assert context["filing_evidence_requirements"][:3] == [
        "business_model",
        "cash_flow",
        "capital_allocation",
    ]
    assert context["company_evidence_policy"]["company_facts_source"] == (
        "KRW Ontology filing research"
    )
    assert context["research_pack"]["format"] == "krw-guru-research-pack/v1"
    assert context["lens_objects"]
    assert context["consultation_objects"]
    assert context["data_needs"]

    trace = json.loads(
        guru_trace_tool(
            root=root,
            reviewed_id="guru:buffett:principle:cash-owner-earnings:test",
        )
    )
    assert trace["ok"] is True
    assert trace["object"]["label_ko"] == "소유주 이익 중심 사고"

    chain = json.loads(
        guru_chain_tool(
            root=root,
            reviewed_id="guru:buffett:principle:cash-owner-earnings:test",
        )
    )
    assert chain["ok"] is True
    assert chain["chain"]["semantic_neighbors"][0]["reviewed_id"] == (
        "guru:buffett:data_need:cash-flow-capital-allocation:test"
    )
    assert "relationships" not in chain

    evidence = json.loads(
        guru_evidence_tool(
            root=root,
            reviewed_id="guru:buffett:principle:cash-owner-earnings:test",
        )
    )
    assert evidence["ok"] is True
    assert evidence["object"]["label_ko"] == "소유주 이익 중심 사고"
    assert evidence["supporting_spans"][0]["official_url"] == "https://example.com/buffett"
    assert "text" not in evidence["supporting_spans"][0]
    assert "excerpt_text" not in evidence["supporting_spans"][0]

    excerpt = json.loads(
        guru_evidence_tool(
            root=root,
            reviewed_id="guru:buffett:principle:cash-owner-earnings:test",
            include_private_excerpt=True,
            max_excerpt_words=5,
        )
    )
    assert excerpt["supporting_spans"][0]["excerpt_text"] == "Owner earnings and cash generation"

    data_needs = json.loads(
        guru_data_needs_tool(
            root=root,
            question="AAPL을 버핏 관점에서 현금흐름과 자본배분으로 봐줘",
            author_keys=["buffett"],
        )
    )
    assert data_needs["data_needs"][0]["data_need_key"] == "cash_flow_capital_allocation"
    assert data_needs["requires_company_evidence"] is True
    assert data_needs["filing_research_bridge"]["use_existing_krw_ontology_mcp"] is True
    assert data_needs["filing_research_bridge"]["filing_evidence_requirements"][:3] == [
        "capital_allocation",
        "cash_flow",
        "share_repurchases",
    ]
    assert "cash_flow" in data_needs["filing_research_bridge"]["brief_en"]


def test_guru_data_needs_for_generic_question_are_conditional_until_company_selected(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)

    data_needs = json.loads(
        guru_data_needs_tool(
            root=root,
            question="버핏식으로 좋은 사업과 좋은 투자의 차이를 설명해줘.",
            author_keys=["buffett"],
        )
    )

    assert data_needs["requires_company_evidence"] is False
    assert data_needs["filing_research_bridge"]["use_existing_krw_ontology_mcp"] is False
    assert data_needs["filing_research_bridge"]["filing_evidence_requirements"] == []
    assert data_needs["filing_research_bridge"]["conditional_filing_evidence_requirements"]
    assert "Select a company or ticker" in data_needs["filing_research_bridge"]["next_step"]


def test_select_guru_lenses_keeps_generic_questions_guru_only(tmp_path: Path) -> None:
    root = _write_reviewed_fixture(tmp_path)

    selection = select_guru_lenses(
        root=root,
        question="버핏식으로 좋은 사업과 좋은 투자의 차이를 설명해줘.",
        author_keys=["buffett"],
    )

    assert selection["selection_status"] == "ready_for_guru_only_answer"
    assert selection["requires_company_evidence"] is False
    assert selection["company_bridge"]["use_existing_krw_ontology_mcp"] is False
    assert selection["company_bridge"]["filing_evidence_requirements"] == []
    assert selection["company_bridge"]["conditional_filing_evidence_requirements"]
    assert selection["selected_lenses"][0]["company_evidence_requirements"] == []
    assert selection["selected_lenses"][0]["conditional_company_evidence_requirements"]


def test_select_guru_lenses_normalizes_structured_company_hooks() -> None:
    assert (
        lens_selector._normalized_company_hook(
            {"description": "감사인의 중요 감사 사항", "section": "critical_audit_matters"}
        )
        == "critical_audit_matters"
    )
    assert (
        lens_selector._normalized_company_hook(
            {"description": "자사주 매입 금액", "metric": "share_repurchase_amount"}
        )
        == "share_repurchase_amount"
    )


def test_guru_question_parser_does_not_treat_metrics_or_etfs_as_company_tickers() -> None:
    assert not mcp_tools._has_ticker_like_token("높은 ROIC가 유지 가능한지 보려면?")
    assert not mcp_tools._question_mentions_company_need(
        "Fundsmith 관점에서 높은 ROIC가 유지 가능한지 보려면 어떤 데이터가 필요해?"
    )
    assert not mcp_tools._question_mentions_company_need(
        "SPY ETF를 장기 보유하는 건 버핏 관점에서 괜찮을까?"
    )
    assert mcp_tools._infer_intent_family(
        "COST가 너무 비싼데 좋은 회사면 그냥 사도 되는지 구루 렌즈로 봐줘"
    ) == "valuation_check"


def test_guru_clarifying_questions_respect_resolved_company_context() -> None:
    assert mcp_tools._clarifying_questions_for_question(
        "XOM 같은 원유 생산회사를 막스 관점에서 사이클 리스크로 봐줘",
        needs_company_data=True,
        ticker="XOM",
    ) == []
    assert mcp_tools._clarifying_questions_for_question(
        "삼성전자라는 종목을 버핏 렌즈로 보면 장기 보유할 수 있는지 어떤 공시 근거가 필요해?",
        needs_company_data=True,
    ) == ["공시 조회를 위해 정확한 티커와 거래소, 보통주/우선주 구분을 확인해 주세요."]
    assert mcp_tools._clarifying_questions_for_question(
        "버핏한테 묻고싶습니다. 나는 원유 투자했습니다",
        needs_company_data=False,
    ) == ["원자재 자체, ETF/선물, 생산 기업, 로열티/인프라 중 무엇에 투자한 건가요?"]


def test_select_guru_lenses_exposes_multi_intent_and_answer_evidence_plan(tmp_path: Path) -> None:
    root = _write_reviewed_fixture(tmp_path)

    selection = select_guru_lenses(
        root=root,
        question="AAPL이 너무 올랐는데 버핏 관점에서 사도 될까?",
        ticker="AAPL",
        author_keys=["buffett"],
    )

    assert selection["primary_intent"] == "valuation_check"
    assert "risk_check" in selection["secondary_intents"]
    assert "considering_buy" in selection["secondary_intents"]
    assert selection["lens_roles"]
    assert selection["answer_evidence_plan"]["mode"] == "filing_bridge_required"
    assert selection["answer_evidence_plan"]["top_evidence_requirements"]


def test_select_guru_lenses_requires_identifier_before_company_bridge(tmp_path: Path) -> None:
    root = _write_reviewed_fixture(tmp_path)

    selection = select_guru_lenses(
        root=root,
        question="삼성전자라는 종목을 버핏 렌즈로 보면 장기 보유할 수 있는지 어떤 공시 근거가 필요해?",
        author_keys=["buffett"],
    )

    assert selection["selection_status"] == "needs_identifier_clarification"
    assert selection["requires_company_evidence"] is True
    assert selection["requires_identifier_clarification"] is True
    assert selection["company_bridge"]["use_existing_krw_ontology_mcp"] is False
    assert selection["answer_evidence_plan"]["mode"] == "resolve_identifier_first"


def test_guru_context_infers_generic_question_intents(tmp_path: Path) -> None:
    root = _write_reviewed_fixture(tmp_path)
    cases = {
        "현금흐름은 좋은데 성장성이 낮은 회사를 어떻게 봐야 해?": "business_quality_check",
        "좋은 뉴스가 많을 때 오히려 조심해야 할 점은?": "contrarian_check",
        "안전마진을 실무적으로 어떻게 생각해야 해?": "valuation_check",
        "한 아이디어 집중 체크리스트를 만들어줘.": "position_sizing",
        "애크먼식 activist 관점에서 개선 여지를 볼 때 어떤 질문을 해야 해?": "thesis_review",
        "가격 인상, 비용 구조, 자본배분 중 activist 관점에서 어떤 순서로 확인해야 해?": "thesis_review",
        "여러 구루 관점으로 매수 전 질문 목록을 만들어줘.": "considering_buy",
    }

    for question, expected_intent in cases.items():
        context = json.loads(
            guru_context_tool(
                root=root,
                question=question,
                author_keys=["buffett"],
            )
        )

        assert context["intent_family"] == expected_intent, question
        assert context["requires_company_evidence"] is False, question


def test_context_score_prioritizes_portfolio_context_data_need_for_position_sizing() -> None:
    question = "내 포트폴리오가 한 종목에 너무 몰려 있는데 막스 관점에서 어떤 질문을 해봐야 해?"
    portfolio_need = {
        "author_key": "marks",
        "data_need_family": "portfolio_context",
        "data_need_key": "portfolio_concentration",
        "label_ko": "포트폴리오 집중도와 비중 데이터",
        "summary_ko": "현재 비중, 집중도, 손실 방어 기준, 리스크 허용 범위가 필요하다.",
        "answer_role": {"default": "data_need", "possible_roles": ["checklist"]},
    }
    generic_need = {
        "author_key": "marks",
        "data_need_family": "future_company_metric",
        "data_need_key": "valuation_multiples",
        "label_ko": "가치평가 데이터",
        "summary_ko": "가격과 가치평가 배수가 필요하다.",
        "answer_role": {"default": "data_need", "possible_roles": []},
    }

    assert mcp_tools._context_score(
        question,
        portfolio_need,
        intent_family="position_sizing",
        row_family="data_need",
    ) > mcp_tools._context_score(
        question,
        generic_need,
        intent_family="position_sizing",
        row_family="data_need",
    )


def test_guru_context_respects_explicit_no_ticker_question(tmp_path: Path) -> None:
    root = _write_reviewed_fixture(tmp_path)

    context = json.loads(
        guru_context_tool(
            root=root,
            question="버핏이라면 장기보유할 회사를 볼 때 어떤 질문부터 할까? 종목은 아직 없어.",
            author_keys=["buffett"],
        )
    )

    assert context["requires_company_evidence"] is False
    assert context["filing_evidence_requirements"] == []
    assert context["consultation_objects"]


def test_guru_context_treats_generic_lens_questions_as_no_company_required(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)
    questions = [
        "버핏식으로 좋은 사업과 좋은 투자의 차이를 설명해줘.",
        "구루들 관점에서 좋은 주식을 고르는 체크리스트를 만들되 종목은 빼줘.",
        "포트폴리오가 한 아이디어에 몰릴 때 어떤 질문을 해야 해?",
        "하락장에서 평균단가를 낮추기 전에 뭘 확인해야 해?",
    ]

    for question in questions:
        context = json.loads(
            guru_context_tool(
                root=root,
                question=question,
                author_keys=["buffett"],
            )
        )

        assert context["requires_company_evidence"] is False, question
        assert context["filing_evidence_requirements"] == [], question


def test_guru_query_context_clarifies_asset_wrapper_before_company_bridge(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)

    query_context = json.loads(
        guru_query_context_tool(
            root=root,
            question="버핏한테 묻고싶습니다. 나는 원유 투자했습니다",
            author_keys=["buffett"],
        )
    )

    assert query_context["research_status"] == "needs_clarification"
    assert query_context["requires_company_evidence"] is False
    assert query_context["filing_evidence_requirements"] == []
    assert query_context["research_pack"]["clarifying_questions"] == [
        "원자재 자체, ETF/선물, 생산 기업, 로열티/인프라 중 무엇에 투자한 건가요?"
    ]


def test_guru_query_context_does_not_treat_now_phrase_as_gold_commodity(
    tmp_path: Path,
) -> None:
    root = _write_quality_ranking_fixture(tmp_path)

    query_context = json.loads(
        guru_query_context_tool(
            root=root,
            question="하워드 막스식으로 지금 내가 놓치기 쉬운 리스크 질문 목록을 만들어줘. 특정 종목은 없어.",
            author_keys=["marks"],
        )
    )

    assert query_context["research_status"] != "needs_clarification"
    assert query_context["research_pack"]["clarifying_questions"] == []


def test_guru_query_context_respects_no_ticker_and_no_company_name_phrasing(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)
    cases = [
        (
            ["buffett"],
            "내가 이미 산 종목을 더 살지 말지 고민할 때 버핏식으로 어떤 증거가 더 필요해? 아직 티커는 없어.",
        ),
        (
            ["buffett"],
            "가격 인상, 비용 구조, 자본배분 중 어떤 순서로 확인해야 해? 회사명 없이.",
        ),
    ]

    for author_keys, question in cases:
        query_context = json.loads(
            guru_query_context_tool(
                root=root,
                question=question,
                author_keys=author_keys,
            )
        )

        assert query_context["requires_company_evidence"] is False, question
        assert query_context["filing_evidence_requirements"] == [], question
        assert query_context["research_status"] != "needs_company_evidence", question


def test_guru_data_needs_prioritizes_generic_risk_bridge_for_named_company(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)

    data_needs = json.loads(
        guru_data_needs_tool(
            root=root,
            question="TSLA를 하워드 막스 관점에서 보면 가장 먼저 봐야 할 리스크가 뭐야?",
            author_keys=["buffett"],
        )
    )

    assert data_needs["filing_research_bridge"]["filing_evidence_requirements"][:5] == [
        "risk_factors",
        "balance_sheet",
        "cash_flow",
        "demand_cycle_exposure",
        "margin_pressure",
    ]
    assert data_needs["filing_research_bridge"]["brief_en"].index("risk_factors") < (
        data_needs["filing_research_bridge"]["brief_en"].index("cash_flow")
    )


def test_guru_eval_questions_tool_reads_plugin_eval_set() -> None:
    payload = json.loads(guru_eval_questions_tool(lens="buffett", limit=50))

    assert payload["questions"]
    assert payload["coverage"]["lenses"]["buffett"] >= 1
    assert all("buffett" in row["lenses"] for row in payload["questions"])


def test_guru_evidence_returns_actionable_not_found_error(tmp_path: Path) -> None:
    root = _write_reviewed_fixture(tmp_path)

    payload = json.loads(guru_evidence_tool(root=root, reviewed_id="missing"))

    assert payload["ok"] is False
    assert payload["error"] == "reviewed_id_not_found"
    assert "krw_guru_search" in payload["suggestion"]


def test_guru_query_context_prefers_intent_relevant_lenses_over_theme_overfit(
    tmp_path: Path,
) -> None:
    root = _write_quality_ranking_fixture(tmp_path)
    cases = [
        (
            ["buffett"],
            "버핏식으로 훌륭한 사업과 훌륭한 투자 사이의 차이를 설명해줘. 특정 종목은 없어.",
            "좋은 사업과 좋은 투자의 구분",
            "미국의 경제적 테일윈드",
        ),
        (
            ["marks"],
            "하락장에서 평균단가를 낮추기 전에 막스 렌즈로 뭘 확인해야 해? 특정 회사는 없어.",
            "평균단가 낮추기 전 손실 경로 점검",
            "AI 인프라의 민스키 모멘트 경고 신호",
        ),
        (
            ["ackman"],
            "가격 인상, 비용 구조, 자본배분 중 activist 관점에서 어떤 순서로 확인해야 해? 회사명 없이.",
            "운영 개선 레버 우선순위",
            "BN 부분합(SOTP) 할인 프레임",
        ),
    ]

    for author_keys, question, expected_top_label, disallowed_top_label in cases:
        query_context = json.loads(
            guru_query_context_tool(
                root=root,
                question=question,
                author_keys=author_keys,
            )
        )
        top_lens = query_context["research_pack"]["selected_lenses"][0]

        assert top_lens["label_ko"] == expected_top_label, question
        assert top_lens["label_ko"] != disallowed_top_label, question
        assert query_context["requires_company_evidence"] is False, question
        assert query_context["filing_evidence_requirements"] == [], question


def _write_reviewed_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "guru"
    reviewed = root / "reviewed"
    running_root = tmp_path / "guru-running"
    reviewed.mkdir(parents=True)
    spans_path = running_root / "spans" / "buffett.jsonl"
    spans_path.parent.mkdir(parents=True)

    _write_jsonl(
        reviewed / "guru_objects.jsonl",
        [
            {
                "reviewed_id": "guru:buffett:principle:cash-owner-earnings:test",
                "candidate_id": "candidate:1",
                "author_key": "buffett",
                "object_type": "principle",
                "object_origin": "source_grounded",
                "label_ko": "소유주 이익 중심 사고",
                "label_en": "Owner earnings focus",
                "summary_ko": "버핏 렌즈에서는 회계 이익보다 현금 창출과 자본배분이 중요하다.",
                "supporting_span_ids": ["buffett:2024-letter:span:0001"],
                "intent_family": "holding_review",
                "applicability": {
                    "strong_for": ["holding_review", "business_quality_check"],
                    "possible_for": ["capital_allocation_check"],
                    "weak_for": ["short_term_price_prediction"],
                    "anti_triggers": ["commodity_wrapper_without_company"],
                    "requires_clarification_when": ["missing ticker for company judgment"],
                    "confidence": "high",
                },
                "specificity": {
                    "level": "general_principle",
                    "source_case_tags": [],
                    "generalization_confidence": "high",
                },
                "answer_role": {
                    "default": "core_lens",
                    "possible_roles": ["checklist"],
                    "confidence": "high",
                },
                "confidence": "high",
                "status": "reviewed",
                "related_reviewed_ids": [
                    "guru:buffett:data_need:cash-flow-capital-allocation:test"
                ],
            }
        ],
    )
    _write_jsonl(
        reviewed / "consultation_objects.jsonl",
        [
            {
                "reviewed_id": "guru:buffett:question_template:holding-review:test",
                "candidate_id": "candidate:2",
                "author_key": "buffett",
                "object_type": "question_template",
                "object_origin": "consultation_derived",
                "label_ko": "보유 종목 버핏식 점검 질문",
                "summary_ko": "보유 종목을 현금흐름, 자본배분, 장기 경쟁력으로 점검한다.",
                "question_pattern_ko": "{ticker}는 장기 현금창출력이 충분한가?",
                "intent_family": "holding_review",
                "intent_tags": ["holding", "cash_flow"],
                "decision_stage": "holding",
                "requires_company_data": True,
                "requires_portfolio_data": False,
                "requires_user_context": False,
                "supporting_span_ids": [],
                "applicability": {
                    "strong_for": ["holding_review"],
                    "possible_for": ["business_quality_check"],
                    "weak_for": [],
                    "anti_triggers": [],
                    "requires_clarification_when": ["missing ticker for company judgment"],
                    "confidence": "medium",
                },
                "specificity": {
                    "level": "general_principle",
                    "source_case_tags": [],
                    "generalization_confidence": "medium",
                },
                "answer_role": {
                    "default": "checklist",
                    "possible_roles": ["supporting_lens"],
                    "confidence": "medium",
                },
                "confidence": "medium",
                "status": "reviewed",
                "related_reviewed_ids": [
                    "guru:buffett:principle:cash-owner-earnings:test"
                ],
            }
        ],
    )
    _write_jsonl(
        reviewed / "data_needs.jsonl",
        [
            {
                "reviewed_id": "guru:buffett:data_need:cash-flow-capital-allocation:test",
                "candidate_id": "candidate:3",
                "author_key": "buffett",
                "label_ko": "현금흐름과 자본배분 근거",
                "summary_ko": "회사 공시에서 현금흐름, 자사주, 배당, 재투자 근거가 필요하다.",
                "object_origin": "data_need",
                "data_need_family": "future_company_metric",
                "data_need_key": "cash_flow_capital_allocation",
                "requires_company_data": True,
                "requires_portfolio_data": False,
                "requires_user_context": False,
                "company_data_hooks": ["cash_flow", "share_repurchases", "capital_allocation"],
                "supporting_span_ids": [],
                "applicability": {
                    "strong_for": ["holding_review", "capital_allocation_check"],
                    "possible_for": ["business_quality_check"],
                    "weak_for": [],
                    "anti_triggers": [],
                    "requires_clarification_when": ["missing ticker for company judgment"],
                    "confidence": "high",
                },
                "specificity": {
                    "level": "general_principle",
                    "source_case_tags": [],
                    "generalization_confidence": "high",
                },
                "answer_role": {
                    "default": "data_need",
                    "possible_roles": ["checklist"],
                    "confidence": "high",
                },
                "confidence": "high",
                "status": "reviewed",
                "related_reviewed_ids": [
                    "guru:buffett:principle:cash-owner-earnings:test"
                ],
            }
        ],
    )
    _write_jsonl(reviewed / "corpus_metadata.jsonl", [])
    _write_jsonl(reviewed / "rejected_candidates.jsonl", [])
    _write_jsonl(
        reviewed / "relationships.jsonl",
        [
            {
                "relationship_id": "guru:relationship:informs:test",
                "from_id": "guru:buffett:principle:cash-owner-earnings:test",
                "to_id": "guru:buffett:data_need:cash-flow-capital-allocation:test",
                "relation_type": "informs",
                "explanation_ko": "버핏식 현금흐름 렌즈는 공시 데이터 필요를 만든다.",
            }
        ],
    )
    (reviewed / "curation_report.json").write_text(
        json.dumps(
            {
                "schema_version": "krw-guru-ontology/v1",
                "generated_at": "2026-07-05T00:00:00+00:00",
                "root": str(root),
                "running_root": str(running_root),
                "warnings": [],
                "rejection_reasons": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        spans_path,
        [
            {
                "span_id": "buffett:2024-letter:span:0001",
                "source_id": "buffett:2024-letter",
                "span_type": "paragraph",
                "position": 1,
                "section_title": "Owner Earnings",
                "text_hash": "hash",
                "author_key": "buffett",
                "title": "2024 Berkshire Letter",
                "official_url": "https://example.com/buffett",
                "text": "Owner earnings and cash generation matter more than accounting optics.",
                "char_count": 72,
            }
        ],
    )
    (running_root / "parsed_manifest.json").write_text(
        json.dumps(
            {
                "format": "krw-guru-parsed-manifest/v1",
                "schema_version": "krw-guru-ontology/v1",
                "generated_at": "2026-07-05T00:00:00+00:00",
                "root": str(root),
                "running_root": str(running_root),
                "parsed_documents": [
                    {
                        "source_id": "buffett:2024-letter",
                        "author_key": "buffett",
                        "title": "2024 Berkshire Letter",
                        "source_type": "shareholder_letter",
                        "official_url": "https://example.com/buffett",
                        "spans_path": str(spans_path),
                        "span_count": 1,
                        "status": "parsed",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def _write_quality_ranking_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "guru-quality"
    reviewed = root / "reviewed"
    reviewed.mkdir(parents=True)
    _write_jsonl(
        reviewed / "guru_objects.jsonl",
        [
            {
                "reviewed_id": "guru:buffett:decision:business-vs-investment:test",
                "candidate_id": "candidate:q1",
                "author_key": "buffett",
                "object_type": "decision_criterion",
                "object_origin": "source_grounded",
                "label_ko": "좋은 사업과 좋은 투자의 구분",
                "summary_ko": "좋은 사업은 지속 가능한 경제성과 현금 창출력이고, 좋은 투자는 가격과 안전마진까지 함께 맞아야 한다.",
                "supporting_span_ids": ["buffett:test:span:business-investment"],
                "intent_family": "learn_guru_view",
                "confidence": "high",
                "status": "reviewed",
            },
            {
                "reviewed_id": "guru:buffett:concept:american-tailwind:test",
                "candidate_id": "candidate:q2",
                "author_key": "buffett",
                "object_type": "concept",
                "object_origin": "source_grounded",
                "label_ko": "미국의 경제적 테일윈드",
                "summary_ko": "미국의 순풍과 American Tailwind는 장기 투자 성과를 도운 거시적 배경이다.",
                "supporting_span_ids": ["buffett:test:span:tailwind"],
                "intent_family": "learn_guru_view",
                "confidence": "high",
                "status": "reviewed",
            },
            {
                "reviewed_id": "guru:marks:risk:average-down:test",
                "candidate_id": "candidate:q3",
                "author_key": "marks",
                "object_type": "risk_frame",
                "object_origin": "source_grounded",
                "label_ko": "평균단가 낮추기 전 손실 경로 점검",
                "summary_ko": "하락장에서 평균단가를 낮추기 전에는 손실 경로, 사이클, 불확실성, thesis 훼손 여부를 먼저 점검한다.",
                "supporting_span_ids": ["marks:test:span:average-down"],
                "intent_family": "cyclical_risk_check",
                "confidence": "high",
                "status": "reviewed",
            },
            {
                "reviewed_id": "guru:marks:risk:ai-minsky:test",
                "candidate_id": "candidate:q4",
                "author_key": "marks",
                "object_type": "risk_frame",
                "object_origin": "source_grounded",
                "label_ko": "AI 인프라의 민스키 모멘트 경고 신호",
                "summary_ko": "AI 데이터센터와 소프트웨어 부채 투자에서 과열과 민스키 모멘트를 경계한다.",
                "supporting_span_ids": ["marks:test:span:ai"],
                "intent_family": "cyclical_risk_check",
                "confidence": "high",
                "status": "reviewed",
            },
            {
                "reviewed_id": "guru:ackman:decision:operating-levers:test",
                "candidate_id": "candidate:q5",
                "author_key": "ackman",
                "object_type": "decision_criterion",
                "object_origin": "source_grounded",
                "label_ko": "운영 개선 레버 우선순위",
                "summary_ko": "activist 관점에서는 가격 인상, 비용 구조, 마진, 자본배분, 촉매를 순서대로 확인해 운영 개선 여지를 판단한다.",
                "supporting_span_ids": ["ackman:test:span:operating-levers"],
                "intent_family": "thesis_review",
                "confidence": "high",
                "status": "reviewed",
            },
            {
                "reviewed_id": "guru:ackman:valuation:sotp:test",
                "candidate_id": "candidate:q6",
                "author_key": "ackman",
                "object_type": "valuation_frame",
                "object_origin": "source_grounded",
                "label_ko": "BN 부분합(SOTP) 할인 프레임",
                "summary_ko": "BN, NAV, SOTP, peer multiple, 동종 프랜차이즈 대비 할인거래를 내재가치 평가에 활용한다.",
                "supporting_span_ids": ["ackman:test:span:sotp"],
                "intent_family": "thesis_review",
                "confidence": "high",
                "status": "reviewed",
            },
        ],
    )
    _write_jsonl(reviewed / "consultation_objects.jsonl", [])
    _write_jsonl(reviewed / "data_needs.jsonl", [])
    _write_jsonl(reviewed / "corpus_metadata.jsonl", [])
    _write_jsonl(reviewed / "rejected_candidates.jsonl", [])
    _write_jsonl(reviewed / "relationships.jsonl", [])
    (reviewed / "curation_report.json").write_text(
        json.dumps(
            {
                "schema_version": "krw-guru-ontology/v1",
                "generated_at": "2026-07-05T00:00:00+00:00",
                "root": str(root),
                "running_root": str(tmp_path / "missing-running-root"),
                "warnings": [],
                "rejection_reasons": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
