from __future__ import annotations

from krw_ontology.guru.models import (
    GuruAnswerPlaybook,
    GuruAnswerSection,
    GuruClarifyingQuestion,
    GuruDataNeed,
    GuruInvestorIntent,
    GuruOntologyCandidate,
    GuruQuestionRoute,
    GuruQuestionTemplate,
)


def test_consultation_models_cover_investor_question_routing_without_company_binding():
    intent = GuruInvestorIntent(
        intent_id="intent:already-bought-review",
        intent_family="already_bought",
        label_ko="이미 산 종목 점검",
        decision_stage="already_bought",
        intent_tags=["business_quality_check", "valuation_check", "red_flag_check"],
        trigger_examples_ko=["내가 {ticker}를 샀는데 버핏 관점에서 봐줘"],
        requires_company_data=True,
        requires_user_context=True,
    )
    template = GuruQuestionTemplate(
        template_id="template:already-bought-guru-review",
        intent_id=intent.intent_id,
        question_pattern_ko="내가 {ticker}를 샀는데 {guru} 관점에서 봐줘",
        slot_names=["ticker", "guru"],
        answer_requires_company=True,
        answer_requires_user_context=True,
    )
    data_need = GuruDataNeed(
        data_need_id="need:owner-earnings",
        need_family="future_company_metric",
        label_ko="소유자 이익",
        description_ko="기업의 현금창출력을 구루 렌즈로 검토하기 위한 미래 데이터 필요 항목",
        used_for_intent_families=["already_bought", "valuation_check"],
    )
    question = GuruClarifyingQuestion(
        clarifying_question_id="clarify:cost-basis",
        intent_id=intent.intent_id,
        question_ko="매수 단가와 투자 기간은 어떻게 되나요?",
        slot_key="cost_basis_and_horizon",
        required=True,
    )
    section = GuruAnswerSection(
        section_id="section:non-advisory-frame",
        section_type="non_advisory_decision_frame",
        title_ko="판단 프레임",
        purpose_ko="매수/매도 지시가 아니라 구루식 판단 기준으로 정리한다.",
        order=90,
    )
    playbook = GuruAnswerPlaybook(
        playbook_id="playbook:already-bought-buffett-review",
        intent_id=intent.intent_id,
        label_ko="이미 산 종목을 버핏식으로 점검",
        author_key="buffett",
        section_ids=[section.section_id],
        data_need_ids=[data_need.data_need_id],
        clarifying_question_ids=[question.clarifying_question_id],
    )
    route = GuruQuestionRoute(
        route_id="route:already-bought-buffett-review",
        intent_id=intent.intent_id,
        playbook_id=playbook.playbook_id,
        author_keys=["buffett"],
        data_need_ids=[data_need.data_need_id],
    )

    assert template.answer_requires_company is True
    assert data_need.no_live_binding is True
    assert playbook.non_advisory is True
    assert route.priority == 100


def test_ontology_candidate_accepts_consultation_objects():
    candidate = GuruOntologyCandidate(
        candidate_id="buffett:playbook:already-bought-review",
        author_key="buffett",
        object_type="answer_playbook",
        label_ko="이미 산 종목 점검 플레이북",
        summary_ko="투자자가 이미 산 종목을 버핏 렌즈로 점검하는 답변 구조",
        intent_family="already_bought",
        intent_tags=["valuation_check", "business_quality_check"],
        decision_stage="already_bought",
        answer_sections=[
            "guru_lens_summary",
            "business_quality_checks",
            "valuation_checks",
            "missing_context_questions",
            "non_advisory_decision_frame",
        ],
        data_need_family="future_company_metric",
        data_need_key="owner_earnings",
        required_context=["ticker", "cost_basis", "time_horizon"],
        requires_company_data=True,
        requires_user_context=True,
        company_data_hooks=[
            {
                "family": "future_company_metric",
                "key": "owner_earnings",
                "reason": "cash generation lens",
            }
        ],
        applicability={
            "strong_for": ["holding_review"],
            "possible_for": ["business_quality_check"],
            "weak_for": ["short_term_trading"],
            "anti_triggers": ["predict_today_price"],
            "requires_clarification_when": ["missing ticker"],
            "confidence": "high",
        },
        specificity={
            "level": "general_principle",
            "source_case_tags": [],
            "generalization_confidence": "high",
        },
        answer_role={
            "default": "checklist",
            "possible_roles": ["supporting_lens"],
            "confidence": "high",
        },
    )

    assert candidate.object_type == "answer_playbook"
    assert candidate.requires_company_data is True
    assert candidate.company_data_hooks[0]["family"] == "future_company_metric"
    assert candidate.applicability.strong_for == ["holding_review"]
    assert candidate.specificity.level == "general_principle"
    assert candidate.answer_role.default == "checklist"
