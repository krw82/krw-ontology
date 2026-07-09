"""Tests for the dedicated Guru Advisor MCP tool layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from krw_ontology.guru import mcp_server
from krw_ontology.guru import mcp_tools
from krw_ontology.guru import lens_selector
from krw_ontology.guru.company_bridge import (
    build_guru_company_evidence_review,
    company_filing_brief_from_guru_lens,
)
from krw_ontology.guru.index import build_guru_shard_index
from krw_ontology.guru.lens_selector import guru_select_lenses_tool, select_guru_lenses
from krw_ontology.guru.mcp_tools import (
    guru_chain_tool,
    guru_company_brief_tool,
    guru_company_pack_tool,
    guru_context_tool,
    guru_data_needs_tool,
    guru_eval_questions_tool,
    guru_evidence_tool,
    guru_index_context_tool,
    guru_query_context_tool,
    guru_review_company_evidence_tool,
    guru_search_tool,
    guru_status_tool,
    guru_trace_tool,
)
from krw_ontology.guru.renderer import build_guru_answer_render_plan
from krw_ontology.mcp_server.evidence_pack import evidence_pack_hash


def _verified_company_payload(
    *,
    root: Path,
    question: str,
    ticker: str,
    author_key: str,
    excerpt: str,
    company_context: dict | None = None,
) -> dict:
    brief_payload = json.loads(
        guru_company_brief_tool(
            root=root,
            question=question,
            ticker=ticker,
            author_keys=[author_key],
            company_context_json=company_context,
        )
    )
    plan_questions = (
        brief_payload["company_filing_brief"]["dynamic_question_plan"]["questions"]
    )
    payload = {
        "format": "krw-verified-company-evidence/v1",
        "release_id": "guru-unit-test",
        "ticker": ticker,
        "current_driver": {
            "ticker": ticker,
            "period": "CY2026Q1",
            "document_type": "10-Q",
            "role": "current_driver",
        },
        "annual_baseline": {
            "ticker": ticker,
            "period": "CY2025",
            "document_type": "10-K",
            "role": "annual_baseline",
        },
        "evidence_by_question": [
            {
                "question_id": item["question_id"],
                "evidence": [
                    {
                        "source_object_id": f"claim:{ticker}:{item['question_id']}",
                        "object_type": "ResearchClaim",
                        "trace_status": "traceable_direct",
                        "evidence_grade": "direct",
                        "usable_for_strong_claim": True,
                        "usable_for_interpretation": True,
                        "document": {
                            "ticker": ticker,
                            "document_type": "10-Q",
                            "period": "CY2026Q1",
                            "section": "MD&A",
                            "anchor_roles": ["current_driver"],
                        },
                        "claim_ids": [f"claim:{ticker}:{item['question_id']}"],
                        "quote_ids": [f"quote:{ticker}:{item['question_id']}"],
                        "span_ids": [f"span:{ticker}:{item['question_id']}"],
                        "verified_excerpt": excerpt,
                        "metric_lineage": None,
                    }
                ],
                "verified_count": 1,
                "answerability": "verified",
            }
            for item in plan_questions
        ],
        "rejected_refs": [],
        "verification_summary": {
            "question_count": len(plan_questions),
            "requested_object_count": len(plan_questions),
            "verified_object_count": len(plan_questions),
            "strong_claim_evidence_count": len(plan_questions),
            "interpretation_evidence_count": len(plan_questions),
            "all_questions_have_verified_evidence": True,
        },
        "usage_policy": {
            "strong_company_claims": "Use only verified entries.",
        },
    }
    payload["pack_hash"] = evidence_pack_hash(payload)
    return payload


def test_guru_mcp_server_registers_read_only_tool_names() -> None:
    tool_names = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}

    assert {
        "krw_guru_status",
        "krw_guru_search",
        "krw_guru_query_context",
        "krw_guru_company_brief",
        "krw_guru_company_pack",
        "krw_guru_review_company_evidence",
        "krw_guru_select_lenses",
        "krw_guru_context",
        "krw_guru_trace",
        "krw_guru_chain",
        "krw_guru_evidence",
        "krw_guru_data_needs",
        "krw_guru_eval_questions",
        "krw_guru_index_context",
    } <= tool_names


def test_guru_company_brief_tool_translates_lenses_to_filing_research(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)

    payload = json.loads(
        guru_company_brief_tool(
            root=root,
            question="옥시덴탈을 버핏 관점에서 장기 보유해도 되는지 봐줘",
            ticker="OXY",
            author_keys=["buffett"],
        )
    )

    brief = payload["company_filing_brief"]
    assert payload["company_brief_context_version"] == "krw-guru-company-brief-context/v1"
    assert payload["requires_company_evidence"] is True
    assert brief["format"] == "krw-guru-company-filing-brief/v1"
    assert brief["author_key"] == "buffett"
    assert brief["company_identity"]["ticker"] == "OXY"
    assert brief["company_identity"]["unresolved"] is False
    assert "cash_flow" in brief["required_filing_topics"]
    assert "capital_allocation" in brief["required_filing_topics"]
    assert "share_repurchases" in brief["required_filing_topics"]
    assert "operating cash flow" in brief["query_terms"]
    assert "OXY" in brief["company_research_question_en"]
    assert brief["recommended_company_mcp_call"]["tool"] == "krw_ontology_query_context"
    assert "application/orchestrator" in payload["do_not_call"][1]


def test_guru_company_brief_tool_returns_dynamic_question_plan(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)

    payload = json.loads(
        guru_company_brief_tool(
            root=root,
            question="옥시덴탈을 버핏 관점에서 장기 보유해도 되는지 봐줘",
            ticker="OXY",
            author_keys=["buffett"],
        )
    )

    plan = payload["company_filing_brief"]["dynamic_question_plan"]
    assert plan["format"] == "krw-guru-dynamic-question-plan/v1"
    assert plan["author_key"] == "buffett"
    assert plan["ticker"] == "OXY"
    assert len(plan["questions"]) >= 3
    first_question = plan["questions"][0]
    assert first_question["priority"] == "highest"
    assert first_question["answer_role"] == "main_tension"
    assert "OXY" in first_question["question_en"]
    assert "latest 10-Q" in first_question["retrieval_query_en"]
    assert first_question["do_not_overstate"]
    assert first_question["evidence_needed"]
    assert "회사별" not in first_question["question_ko_label"]


def test_company_brief_filters_credit_hooks_from_general_monopoly_risk() -> None:
    guru_payload = _synthetic_marks_credit_noise_payload(
        question="ASML은 독점력이 강하다고들 하는데 막스 관점에서 반대로 봐야 할 위험은 뭐야?"
    )

    brief = company_filing_brief_from_guru_lens(
        guru_payload,
        ticker="ASML",
        author_key="marks",
        question="ASML은 독점력이 강하다고들 하는데 막스 관점에서 반대로 봐야 할 위험은 뭐야?",
    ).model_dump(mode="json")

    assert "risk_factors" in brief["required_filing_topics"]
    assert "demand_cycle_exposure" in brief["required_filing_topics"]
    assert "전환사채 전환가격 및 만기" not in brief["required_filing_topics"]
    assert "쿠폰 금리 및 풋/콜 옵션 조건" not in brief["required_filing_topics"]
    assert "전환사채 전환가격 및 만기" not in brief["query_terms"]
    assert {
        item["topic"]
        for item in brief["filtered_out_topics"]
    } >= {
        "전환사채 전환가격 및 만기",
        "쿠폰 금리 및 풋/콜 옵션 조건",
        "발행 목적(자사주 매입, 부채 재편, 신규 투자)",
        "희석 가능 주식수 및 희석 비율",
    }


def test_company_brief_allows_credit_hooks_for_credit_instrument_questions() -> None:
    question = "막스 관점에서 이 회사 전환사채와 쿠폰, 만기 리파이낸싱 리스크를 봐줘"
    guru_payload = _synthetic_marks_credit_noise_payload(question=question)

    brief = company_filing_brief_from_guru_lens(
        guru_payload,
        ticker="XYZ",
        author_key="marks",
        question=question,
    ).model_dump(mode="json")

    assert "전환사채 전환가격 및 만기" in brief["required_filing_topics"]
    assert "쿠폰 금리 및 풋/콜 옵션 조건" in brief["required_filing_topics"]
    assert brief["filtered_out_topics"] == []


def test_company_brief_filters_insurance_float_hooks_for_non_insurance_company() -> None:
    question = "KO를 버핏 관점에서 장기 보유해도 되는지 봐줘"
    guru_payload = _synthetic_buffett_insurance_noise_payload(question=question)

    brief = company_filing_brief_from_guru_lens(
        guru_payload,
        ticker="KO",
        author_key="buffett",
        question=question,
    ).model_dump(mode="json")

    assert "cash_flow" in brief["required_filing_topics"]
    assert "pricing_power" in brief["required_filing_topics"]
    assert "사업별 플로트 규모 및 증감" not in brief["required_filing_topics"]
    assert "평균 플로트 비용(연간 인수손익/평균 플로트)" not in brief["required_filing_topics"]
    assert {
        item["topic"]
        for item in brief["filtered_out_topics"]
    } >= {
        "사업별 플로트 규모 및 증감",
        "평균 플로트 비용(연간 인수손익/평균 플로트)",
    }


def test_company_brief_allows_insurance_float_hooks_for_insurance_questions() -> None:
    question = "BRK 보험 플로트와 언더라이팅 수익성을 버핏 관점에서 봐줘"
    guru_payload = _synthetic_buffett_insurance_noise_payload(question=question)

    brief = company_filing_brief_from_guru_lens(
        guru_payload,
        ticker="BRK.B",
        author_key="buffett",
        question=question,
    ).model_dump(mode="json")

    assert "사업별 플로트 규모 및 증감" in brief["required_filing_topics"]
    assert "평균 플로트 비용(연간 인수손익/평균 플로트)" in brief["required_filing_topics"]
    assert brief["filtered_out_topics"] == []


def test_company_brief_filters_sector_specific_hooks_without_matching_context() -> None:
    question = "ADBE를 애크먼 관점에서 보면 가격 인상, 비용 구조, 자본배분 중 어디가 핵심이야?"
    guru_payload = _synthetic_sector_specific_noise_payload(question=question)

    brief = company_filing_brief_from_guru_lens(
        guru_payload,
        ticker="ADBE",
        author_key="ackman",
        question=question,
    ).model_dump(mode="json")

    assert "average_contract_renewal_price_increase" in brief["required_filing_topics"]
    assert "fuel_surcharge_mechanism" not in brief["required_filing_topics"]
    assert "carry_realization_track_record" not in brief["required_filing_topics"]
    assert "quarterly_gross_carried_interest" not in brief["required_filing_topics"]
    assert "fee_related_revenue_share" not in brief["required_filing_topics"]
    assert "target_return_assumptions_per_fund" not in brief["required_filing_topics"]
    assert {
        item["topic"]
        for item in brief["filtered_out_topics"]
    } >= {
        "fuel_surcharge_mechanism",
        "carry_realization_track_record",
        "quarterly_gross_carried_interest",
        "fee_related_revenue_share",
        "target_return_assumptions_per_fund",
    }


def test_company_brief_allows_asset_manager_hooks_for_asset_manager_questions() -> None:
    question = "BAM 같은 자산운용사를 브루스 플랫 관점에서 보면 AUM과 carried interest를 봐줘"
    guru_payload = _synthetic_sector_specific_noise_payload(question=question)

    brief = company_filing_brief_from_guru_lens(
        guru_payload,
        ticker="BAM",
        author_key="flatt",
        question=question,
    ).model_dump(mode="json")

    assert "carry_realization_track_record" in brief["required_filing_topics"]
    assert "quarterly_gross_carried_interest" in brief["required_filing_topics"]


def test_guru_company_pack_tool_returns_company_pack_and_render_plan(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)
    company_payload = {
        "facts": [
            {
                "topic": "cash_flow",
                "text": (
                    "OXY operating cash flow, free cash flow, capital allocation, "
                    "share repurchases, business model, balance sheet, risk factors, "
                    "valuation context, commodity price exposure"
                ),
            }
        ]
    }
    company_context = {
        "source": "company_mcp_topic_map",
        "business_context_terms": ["oil and gas producer"],
        "risk_context_terms": ["commodity price exposure"],
        "available_company_topics": ["commodity_price_exposure", "cash_flow"],
    }

    payload = json.loads(
        guru_company_pack_tool(
            root=root,
            question="옥시덴탈을 버핏 관점에서 장기 보유해도 되는지 봐줘",
            ticker="OXY",
            author_keys=["buffett"],
            company_payload_json=json.dumps(company_payload),
            company_context_json=json.dumps(company_context),
        )
    )

    company_pack = payload["company_pack"]
    render_plan = payload["render_plan"]
    assert payload["company_pack_context_version"] == "krw-guru-company-pack-context/v1"
    assert company_pack["format"] == "krw-guru-company-research-pack/v1"
    assert company_pack["company_identity"]["subject"] == "OXY"
    assert company_pack["company_context"]["source"] == "company_mcp_topic_map"
    assert "commodity_price_exposure" in company_pack["company_filing_brief"]["required_filing_topics"]
    assert company_pack["company_evidence_pack"] == company_payload
    assert company_pack["evidence_alignment"]
    assert company_pack["answer_contract"]["must_not_include"] == [
        "footer_disclaimer",
        "internal_tool_names",
        "ResearchPack_or_MCP_terms",
        "company_facts_not_present_in_company_evidence_pack",
    ]
    assert render_plan["format"] == "krw-guru-answer-render-plan/v1"
    assert render_plan["author_key"] == "buffett"
    assert render_plan["opening_style"] == "자, 내가 먼저 묻고 싶은 건 하나입니다."
    assert "ResearchPack" in render_plan["forbidden_output_patterns"]
    assert "데이터 한계" in render_plan["forbidden_output_patterns"]
    assert "주의:" in render_plan["forbidden_output_patterns"]
    assert "관점에서 보면" in render_plan["forbidden_output_patterns"]
    assert "애크먼이라면" in render_plan["forbidden_output_patterns"]


def test_guru_review_company_evidence_tool_returns_interpretation_guidance(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)
    question = "옥시덴탈을 버핏 관점에서 장기 보유해도 되는지 봐줘"
    company_payload = _verified_company_payload(
        root=root,
        question=question,
        ticker="OXY",
        author_key="buffett",
        excerpt=(
            "OXY operating cash flow and capital allocation remain exposed to "
            "commodity prices and reinvestment needs."
        ),
    )

    payload = json.loads(
        guru_review_company_evidence_tool(
            root=root,
            question=question,
            ticker="OXY",
            author_keys=["buffett"],
            company_payload_json=json.dumps(company_payload),
        )
    )

    review = payload["company_evidence_review"]
    assert (
        payload["company_evidence_review_context_version"]
        == "krw-guru-company-evidence-review-context/v1"
    )
    assert review["format"] == "krw-guru-company-evidence-review/v1"
    assert review["author_key"] == "buffett"
    assert review["company_identity"]["subject"] == "OXY"
    assert review["lens_alignment"] in {"strengthens", "mixed"}
    assert review["primary_interpretation_ko"]
    assert review["what_to_emphasize"]
    assert review["what_not_to_overstate"]
    assert review["answer_contract"]["purpose"] == "final_answer_guidance_only"
    assert "fixed_report_template" in review["answer_contract"]["must_not_include"]
    assert "ResearchPack_or_CompanyEvidencePack_terms" in review["answer_contract"]["must_not_include"]


def test_guru_review_company_evidence_tool_accepts_dict_payloads(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)
    question = "옥시덴탈을 버핏 관점에서 장기 보유해도 되는지 봐줘"
    company_context = {
        "source": "company_mcp_topic_map",
        "available_company_topics": ["cash_flow", "capital_allocation"],
    }
    company_payload = _verified_company_payload(
        root=root,
        question=question,
        ticker="OXY",
        author_key="buffett",
        excerpt="OXY operating cash flow and free cash flow support capital allocation.",
        company_context=company_context,
    )

    payload = json.loads(
        guru_review_company_evidence_tool(
            root=root,
            question=question,
            ticker="OXY",
            author_keys=["buffett"],
            company_payload_json=company_payload,
            company_context_json=company_context,
        )
    )

    review = payload["company_evidence_review"]
    assert review["company_identity"]["subject"] == "OXY"
    assert review["what_not_to_overstate"]


def test_guru_review_rejects_unverified_or_modified_company_payload(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)
    question = "옥시덴탈을 버핏 관점에서 장기 보유해도 되는지 봐줘"

    with pytest.raises(ValueError, match="krw-verified-company-evidence/v1"):
        guru_review_company_evidence_tool(
            root=root,
            question=question,
            ticker="OXY",
            author_keys=["buffett"],
            company_payload={"evidence_by_question": [{"finding": "invented"}]},
        )

    verified = _verified_company_payload(
        root=root,
        question=question,
        ticker="OXY",
        author_key="buffett",
        excerpt="Cash generation remains exposed to commodity prices.",
    )
    verified["evidence_by_question"][0]["evidence"][0]["verified_excerpt"] = (
        "A modified finding that was not in the verifier output."
    )
    with pytest.raises(ValueError, match="krw-verified-company-evidence/v1"):
        guru_review_company_evidence_tool(
            root=root,
            question=question,
            ticker="OXY",
            author_keys=["buffett"],
            company_payload=verified,
        )


def test_guru_review_rejects_verified_pack_for_different_question_plan(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)
    question = "옥시덴탈을 버핏 관점에서 장기 보유해도 되는지 봐줘"
    verified = _verified_company_payload(
        root=root,
        question=question,
        ticker="OXY",
        author_key="buffett",
        excerpt="Cash generation remains exposed to commodity prices.",
    )
    verified["evidence_by_question"][0]["question_id"] = "q_wrong_plan"
    verified["pack_hash"] = evidence_pack_hash(verified)

    with pytest.raises(ValueError, match="question ids do not match"):
        guru_review_company_evidence_tool(
            root=root,
            question=question,
            ticker="OXY",
            author_keys=["buffett"],
            company_payload=verified,
        )


def test_guru_review_company_evidence_uses_question_driven_memo(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)
    question = "옥시덴탈을 버핏 관점에서 장기 보유해도 되는지 봐줘"
    company_payload = _verified_company_payload(
        root=root,
        question=question,
        ticker="OXY",
        author_key="buffett",
        excerpt=(
            "Cash generation is meaningful but still tied to commodity prices "
            "and reinvestment needs."
        ),
    )

    payload = json.loads(
        guru_review_company_evidence_tool(
            root=root,
            question=question,
            ticker="OXY",
            author_keys=["buffett"],
            company_payload_json=json.dumps(company_payload),
        )
    )

    review = payload["company_evidence_review"]
    assert any(
        "commodity prices" in item
        for item in review["what_to_emphasize"]
    )
    assert review["what_not_to_overstate"]
    assert "dynamic question answers when available" in review["answer_contract"]["must_use"]


def test_guru_review_company_evidence_accepts_question_evidence_object(
    tmp_path: Path,
) -> None:
    root = _write_reviewed_fixture(tmp_path)
    question = "SLB를 버핏 관점에서 장기 보유해도 되는지 봐줘"
    company_payload = _verified_company_payload(
        root=root,
        question=question,
        ticker="SLB",
        author_key="buffett",
        excerpt=(
            "Data Center Solutions is directionally diversifying, but the core "
            "business remains tied to upstream capital spending."
        ),
    )

    payload = json.loads(
        guru_review_company_evidence_tool(
            root=root,
            question=question,
            ticker="SLB",
            author_keys=["buffett"],
            company_payload_json=json.dumps(company_payload),
        )
    )

    review = payload["company_evidence_review"]
    assert any(
        "upstream capital spending" in item
        for item in review["what_to_emphasize"]
    )
    assert review["what_not_to_overstate"]


def test_guru_review_filters_question_irrelevant_lenses() -> None:
    guru_payload = {
        "research_pack": {
            "format": "krw-guru-research-pack/v1",
            "pack_meta": {
                "guru_keys": ["buffett"],
                "question": "AAPL 서비스 매출과 아이폰 의존성을 버핏 관점에서 봐줘.",
            },
            "company_bridge": {
                "requires_company_evidence": True,
                "filing_evidence_requirements": [
                    "business_model",
                    "cash_flow",
                    "capital_allocation",
                    "risk_factors",
                ],
            },
            "selected_lenses": [
                {
                    "reviewed_id": "guru:buffett:concept:architect:test",
                    "label_ko": "설계자와 시공자 파트너십 모델",
                    "summary_ko": "멍거는 버크셔의 설계자였고 버핏은 시공자였다.",
                    "company_evidence_requirements": ["business_model"],
                },
                {
                    "reviewed_id": "guru:buffett:concept:moat:test",
                    "label_ko": "반복 수익과 해자의 질",
                    "summary_en": (
                        "Recurring services revenue can strengthen a moat "
                        "when it is not fully dependent on iPhone unit cycles."
                    ),
                    "company_evidence_requirements": ["business_model", "cash_flow"],
                },
            ],
        }
    }
    company_payload = {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "filing_supported_facts": [
            "Apple's business model and cash flow are supported by recurring services revenue tied to the iPhone installed base.",
            "Services revenue mix has expanded to roughly a quarter of total revenue.",
            "iPhone remains the central product gateway for the installed base.",
            "Services revenue is recurring but still tied to the device ecosystem.",
        ],
        "still_open_risks": [
            "iPhone concentration and App Store regulatory risk remain open.",
        ],
    }

    review = build_guru_company_evidence_review(
        guru_payload,
        company_evidence_payload=company_payload,
        ticker="AAPL",
        company_name="Apple Inc.",
        author_key="buffett",
        question="AAPL 서비스 매출과 아이폰 의존성을 버핏 관점에서 봐줘.",
    ).model_dump(mode="json", exclude_none=True)

    emphasized = " ".join(review["what_to_emphasize"])
    assert "반복 수익과 해자의 질" in emphasized
    assert "설계자와 시공자" not in emphasized
    assert "설계자와 시공자" not in (review.get("advisor_question_ko") or "")


def test_guru_answer_render_plan_keeps_missing_evidence_concise() -> None:
    plan = build_guru_answer_render_plan(
        {
            "author_key": "buffett",
            "company_identity": {"subject": "OXY"},
            "guru_pack": {
                "selected_lenses": [
                    {
                        "label_ko": "현금창출력",
                        "summary_ko": "현금이 오래 남는 사업인지 확인한다.",
                    }
                ]
            },
            "company_filing_brief": {},
            "evidence_alignment": [],
            "missing_evidence": [
                "cash_flow",
                "balance_sheet",
                "valuation_context",
                "risk_factors",
            ],
        }
    ).model_dump(mode="json")

    assert len(plan["concerns"]) == 1
    assert "영업현금흐름과 자유현금흐름" in plan["concerns"][0]
    assert "balance_sheet" not in plan["concerns"][0]
    assert "데이터 한계" in plan["forbidden_output_patterns"]
    assert "내가 더 확인할 건" in plan["next_question"]
    assert "cash_flow" not in plan["next_question"]


def test_guru_answer_render_plan_uses_first_person_advisor_posture() -> None:
    plan = build_guru_answer_render_plan(
        {
            "author_key": "ackman",
            "company_identity": {"subject": "OXY"},
            "guru_pack": {
                "selected_lenses": [
                    {
                        "label_ko": "디레버리징 가설",
                        "summary_ko": "부채 감축이 주주가치 전환의 핵심이다.",
                    }
                ]
            },
            "company_filing_brief": {},
            "evidence_alignment": [],
            "missing_evidence": [],
        }
    ).model_dump(mode="json")

    assert plan["opening_style"] == "좋습니다. 이건 감정이 아니라 논리로 쪼개야 합니다."
    assert plan["first_question"].startswith("내가 먼저 쓸 가설은")
    assert "좋습니다." in plan["reframe"]
    assert "애크먼이라면" in plan["forbidden_output_patterns"]
    assert "관점에서 분석하면" in plan["forbidden_output_patterns"]
    assert "렌즈로 보면" in plan["forbidden_output_patterns"]


def test_company_context_guides_lens_selection_without_ticker_hardcoding(
    tmp_path: Path,
) -> None:
    root = _write_company_context_ranking_fixture(tmp_path)
    company_context = {
        "source": "company_mcp_topic_map",
        "context_terms": [
            "oil and gas",
            "commodity price exposure",
            "capital expenditures",
            "cash flow cyclicality",
        ],
        "available_company_topics": [
            "commodity_price_exposure",
            "capital_intensity",
            "cash_flow",
        ],
    }

    payload = json.loads(
        guru_query_context_tool(
            root=root,
            question="OXY 어떠노. 버핏 관점에서 장기 보유할 만한지 봐줘",
            ticker="OXY",
            author_keys=["buffett"],
            company_context_json=json.dumps(company_context),
        )
    )

    assert payload["company_context"]["source"] == "company_mcp_topic_map"
    assert "commodity_price_exposure" in payload["filing_evidence_requirements"]
    assert "rate_case_filings" not in payload["filing_evidence_requirements"]
    assert "rate_case_filings" not in (
        payload["research_pack"]["company_bridge"]["filing_evidence_requirements"]
    )
    assert payload["research_pack"]["selected_lenses"][0]["label_ko"] == (
        "원자재 가격 의존도가 만드는 잔존가치 소멸 리스크"
    )
    selected_labels = [
        item["label_ko"] for item in payload["research_pack"]["selected_lenses"]
    ]
    assert "고객과 규제기관의 상호 호혜 원칙" not in selected_labels


def test_company_context_flows_into_dynamic_question_plan(tmp_path: Path) -> None:
    root = _write_company_context_ranking_fixture(tmp_path)
    company_context = {
        "source": "company_mcp_topic_map",
        "context_terms": [
            "oil and gas",
            "commodity price exposure",
            "capital expenditures",
            "cash flow cyclicality",
        ],
        "available_company_topics": [
            "commodity_price_exposure",
            "capital_intensity",
            "cash_flow",
        ],
    }

    payload = json.loads(
        guru_company_brief_tool(
            root=root,
            question="OXY 어떠노. 버핏 관점에서 장기 보유할 만한지 봐줘",
            ticker="OXY",
            author_keys=["buffett"],
            company_context_json=json.dumps(company_context),
        )
    )

    plan = payload["company_filing_brief"]["dynamic_question_plan"]
    question_text = " ".join(
        item["question_en"] + " " + item["retrieval_query_en"]
        for item in plan["questions"]
    )
    assert "commodity_price_exposure" in payload["company_filing_brief"]["required_filing_topics"]
    assert "commodity price exposure" in question_text
    assert "cash flow cyclicality" in question_text
    assert "rate_case_filings" not in question_text


def test_company_context_flows_into_company_brief_topics(tmp_path: Path) -> None:
    root = _write_company_context_ranking_fixture(tmp_path)
    company_context = {
        "source": "company_mcp_topic_map",
        "business_context_terms": ["oil and gas producer", "capital intensive upstream assets"],
        "risk_context_terms": ["commodity price exposure", "reserve replacement"],
        "available_company_topics": ["commodity_price_exposure", "capital_intensity"],
    }

    payload = json.loads(
        guru_company_brief_tool(
            root=root,
            question="OXY 어떠노. 버핏 관점에서 장기 보유할 만한지 봐줘",
            ticker="OXY",
            author_keys=["buffett"],
            company_context_json=json.dumps(company_context),
        )
    )
    brief = payload["company_filing_brief"]

    assert brief["company_context"]["source"] == "company_mcp_topic_map"
    assert "commodity_price_exposure" in brief["required_filing_topics"]
    assert "capital_intensity" in brief["required_filing_topics"]
    assert "oil price" in brief["query_terms"]


def test_guru_shard_index_is_used_for_query_context(tmp_path: Path, monkeypatch) -> None:
    root = _write_reviewed_fixture(tmp_path)
    manifest = build_guru_shard_index(root)

    assert manifest["schema_version"] == "krw-guru-shard-index/v1"
    assert (root / "indexes" / "guru_shard_manifest.json").is_file()
    assert (root / "indexes" / "shards" / "buffett.sqlite").is_file()
    assert manifest["authors"]["buffett"]["counts"]["guru_objects"] == 1

    original_read_jsonl = mcp_tools._read_jsonl

    def fail_reviewed_jsonl(path: Path):
        if "reviewed" in path.parts:
            raise AssertionError(f"reviewed JSONL fallback was used: {path}")
        return original_read_jsonl(path)

    monkeypatch.setattr(mcp_tools, "_read_jsonl", fail_reviewed_jsonl)

    index_context = json.loads(guru_index_context_tool(root=root))
    assert index_context["status"]["usable"] is True
    assert index_context["status"]["runtime_mode"] == "author_shard"

    status = json.loads(guru_status_tool(root=root))
    assert status["runtime"]["mode"] == "author_shard"
    assert status["runtime"]["index_usable"] is True

    query_context = json.loads(
        guru_query_context_tool(
            root=root,
            question="내가 AAPL을 샀는데 버핏 관점에서 장기 보유해도 되는지 봐줘",
            ticker="AAPL",
            author_keys=["buffett"],
        )
    )
    assert query_context["runtime"]["mode"] == "author_shard"
    assert query_context["runtime"]["author_keys"] == ["buffett"]
    assert query_context["research_pack"]["pack_meta"]["guru_keys"] == ["buffett"]
    assert query_context["research_pack"]["selected_lenses"][0]["reviewed_id"] == (
        "guru:buffett:principle:cash-owner-earnings:test"
    )

    search = json.loads(
        guru_search_tool(
            root=root,
            query="버핏 현금흐름",
            author_keys=["buffett"],
        )
    )
    assert search["runtime"]["mode"] == "author_shard"
    assert search["results"][0]["author_key"] == "buffett"

    selection = json.loads(
        guru_select_lenses_tool(
            root=root,
            question="AAPL을 버핏 관점에서 봐줘",
            ticker="AAPL",
            author_keys=["buffett"],
        )
    )
    assert selection["runtime"]["mode"] == "author_shard"


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


def test_guru_filing_bridge_brief_normalizes_structured_company_hooks() -> None:
    brief = mcp_tools._filing_bridge_brief(
        "OXY를 버핏 관점에서 봐줘",
        [
            {
                "company_data_hooks": [
                    {"description": "연간 영업현금흐름", "metric": "operating_cash_flow"},
                    {"description": "위험요인", "section": "risk_factors"},
                    "capital_allocation",
                ],
                "data_need_key": "cash_flow_quality",
            }
        ],
    )

    assert "operating_cash_flow" in brief
    assert "risk_factors" in brief
    assert "capital_allocation" in brief
    assert "cash_flow_quality" in brief
    assert "{'description'" not in brief
    assert '"description"' not in brief


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
        "옥시덴탈을 버핏 관점에서 장기 보유해도 되는지 봐줘",
        needs_company_data=True,
        ticker="OXY",
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


def test_context_score_downranks_case_specific_noise_for_company_questions() -> None:
    question = "옥시덴탈을 버핏 관점에서 장기 보유해도 되는지 봐줘 OXY"
    owner_earnings_lens = {
        "author_key": "buffett",
        "object_type": "decision_criterion",
        "object_origin": "source_grounded",
        "label_ko": "소유주 이익과 자본배분 점검",
        "summary_ko": "원유와 에너지 사업은 현금흐름, 부채, 자본배분, 원자재 가격 민감도를 봐야 한다.",
        "intent_family": "holding_review",
        "specificity": {"level": "general_principle", "source_case_tags": []},
        "answer_role": {"default": "core_lens", "possible_roles": ["checklist"]},
    }
    wooden_lens = {
        "author_key": "buffett",
        "object_type": "principle",
        "object_origin": "source_grounded",
        "label_ko": "우든 효과: 최고 인재에 시간과 권한을 집중하라",
        "summary_ko": "우든 코치 사례처럼 CEO와 인재에게 권한과 시간을 집중한다.",
        "intent_family": "holding_review",
        "specificity": {
            "level": "company_case_specific",
            "source_case_tags": ["wooden", "talent", "management"],
        },
        "answer_role": {"default": "supporting_lens", "possible_roles": []},
    }
    insurance_lens = {
        "author_key": "buffett",
        "object_type": "decision_criterion",
        "object_origin": "source_grounded",
        "label_ko": "보험 플로트 비용은 자본 비용이다",
        "summary_ko": "보험 플로트, 언더라이팅, 재보험 손실을 투자 자금 원천으로 평가한다.",
        "intent_family": "holding_review",
        "specificity": {"level": "sector_specific", "source_case_tags": ["insurance"]},
        "answer_role": {"default": "core_lens", "possible_roles": []},
    }

    owner_score = mcp_tools._context_score(
        question,
        owner_earnings_lens,
        intent_family="holding_review",
        row_family="guru_object",
    )

    assert owner_score > mcp_tools._context_score(
        question,
        wooden_lens,
        intent_family="holding_review",
        row_family="guru_object",
    )
    assert owner_score > mcp_tools._context_score(
        question,
        insurance_lens,
        intent_family="holding_review",
        row_family="guru_object",
    )


def test_context_score_downranks_sector_cases_for_generic_risk_questions() -> None:
    question = "막스식으로 지금 내가 놓치기 쉬운 리스크 질문 목록을 만들어줘. 특정 종목은 없어."
    general_risk_lens = {
        "author_key": "marks",
        "object_type": "risk_frame",
        "object_origin": "source_grounded",
        "label_ko": "좋은 이야기보다 하방 경로를 먼저 보라",
        "summary_ko": "리스크, 손실 경로, 사이클, 불확실성, 가격에 반영된 낙관을 점검한다.",
        "intent_family": "risk_check",
        "specificity": {"level": "general_principle", "source_case_tags": []},
        "answer_role": {"default": "core_lens", "possible_roles": ["caution"]},
    }
    cre_bank_lens = {
        "author_key": "marks",
        "object_type": "risk_frame",
        "object_origin": "source_grounded",
        "label_ko": "은행의 CRE 대출 집중 리스크",
        "summary_ko": "은행, CRE, 상업용 부동산 대출, 신용스프레드와 차환 리스크를 본다.",
        "intent_family": "risk_check",
        "specificity": {
            "level": "sector_specific",
            "source_case_tags": ["bank", "CRE", "credit"],
        },
        "answer_role": {"default": "core_lens", "possible_roles": ["caution"]},
    }

    assert mcp_tools._context_score(
        question,
        general_risk_lens,
        intent_family="risk_check",
        row_family="guru_object",
    ) > mcp_tools._context_score(
        question,
        cre_bank_lens,
        intent_family="risk_check",
        row_family="guru_object",
    )


def test_context_score_downranks_derivative_lens_for_non_derivative_company_risk() -> None:
    question = "AI 반도체 인프라 회사의 성장 리스크를 버핏과 막스 관점으로 보면 어디가 가장 불편해?"
    innovation_risk = {
        "author_key": "marks",
        "object_type": "risk_frame",
        "object_origin": "source_grounded",
        "label_ko": "새로운 기술 투자 붐의 자본 파괴 위험",
        "summary_ko": "AI, 데이터센터, 반도체 인프라 붐은 기대가 과하면 자본 파괴와 수요 사이클 위험을 만든다.",
        "intent_family": "risk_check",
        "specificity": {"level": "general_principle", "source_case_tags": ["AI", "semiconductor"]},
        "answer_role": {"default": "core_lens", "possible_roles": ["caution"]},
    }
    derivative_disclosure = {
        "author_key": "buffett",
        "object_type": "risk_frame",
        "object_origin": "source_grounded",
        "label_ko": "파생상품 공시의 불투명성",
        "summary_ko": "파생상품, 스왑, 옵션, 헤지 계약은 공시를 읽어도 위험이 불투명할 수 있다.",
        "intent_family": "risk_check",
        "specificity": {"level": "general_principle", "source_case_tags": ["derivatives"]},
        "answer_role": {"default": "core_lens", "possible_roles": ["caution"]},
    }
    governance_lens = {
        "author_key": "buffett",
        "object_type": "risk_frame",
        "object_origin": "source_grounded",
        "label_ko": "보수 의존적 이사의 독립성 역설",
        "summary_ko": "이사회, 이사 보수, 독립성, 거버넌스 구조가 주주 관점과 어긋날 수 있다.",
        "intent_family": "risk_check",
        "specificity": {"level": "general_principle", "source_case_tags": ["governance"]},
        "answer_role": {"default": "core_lens", "possible_roles": ["caution"]},
    }

    innovation_score = mcp_tools._context_score(
        question,
        innovation_risk,
        intent_family="risk_check",
        row_family="guru_object",
    )

    assert innovation_score > mcp_tools._context_score(
        question,
        derivative_disclosure,
        intent_family="risk_check",
        row_family="guru_object",
    )
    assert innovation_score > mcp_tools._context_score(
        question,
        governance_lens,
        intent_family="risk_check",
        row_family="guru_object",
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
        (
            ["buffett", "marks"],
            "현금 비중을 높게 들고 있는 게 버핏과 막스 관점에서 언제 말이 돼?",
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


def _synthetic_marks_credit_noise_payload(*, question: str) -> dict:
    return {
        "research_context_version": "krw-guru-query-context/v1",
        "research_status": "needs_company_evidence",
        "requires_company_evidence": True,
        "filing_evidence_requirements": [
            "risk_factors",
            "balance_sheet",
            "cash_flow",
            "demand_cycle_exposure",
            "margin_pressure",
            "liquidity",
        ],
        "research_pack": {
            "format": "krw-guru-research-pack/v1",
            "pack_meta": {
                "pack_id": "test-pack",
                "guru_keys": ["marks"],
                "question": question,
            },
            "company_bridge": {
                "requires_company_evidence": True,
                "filing_evidence_requirements": [
                    "risk_factors",
                    "balance_sheet",
                    "cash_flow",
                    "demand_cycle_exposure",
                    "margin_pressure",
                    "liquidity",
                ],
            },
            "selected_lenses": [
                {
                    "reviewed_id": "guru:marks:risk:test",
                    "label_ko": "프리미엄 밸류에이션의 이중 위험",
                    "summary_ko": "좋은 이야기가 가격에 반영되면 하방 위험이 커진다.",
                    "company_evidence_requirements": [
                        "risk_factors",
                        "demand_cycle_exposure",
                    ],
                }
            ],
            "data_needs": [
                {
                    "data_need_key": "convertible_debt_terms",
                    "company_data_hooks": [
                        "전환사채 전환가격 및 만기",
                        "쿠폰 금리 및 풋/콜 옵션 조건",
                        "발행 목적(자사주 매입, 부채 재편, 신규 투자)",
                        "희석 가능 주식수 및 희석 비율",
                    ],
                }
            ],
        },
    }


def _synthetic_buffett_insurance_noise_payload(*, question: str) -> dict:
    return {
        "research_context_version": "krw-guru-query-context/v1",
        "research_status": "needs_company_evidence",
        "requires_company_evidence": True,
        "filing_evidence_requirements": [
            "business_model",
            "cash_flow",
            "capital_allocation",
            "risk_factors",
        ],
        "research_pack": {
            "format": "krw-guru-research-pack/v1",
            "pack_meta": {
                "pack_id": "test-pack",
                "guru_keys": ["buffett"],
                "question": question,
            },
            "company_bridge": {
                "requires_company_evidence": True,
                "filing_evidence_requirements": [
                    "business_model",
                    "cash_flow",
                    "capital_allocation",
                    "risk_factors",
                ],
            },
            "selected_lenses": [
                {
                    "reviewed_id": "guru:buffett:quality:test",
                    "label_ko": "브랜드와 현금창출력 점검",
                    "summary_ko": "소비재 기업은 브랜드, 가격결정력, 반복 현금흐름을 본다.",
                    "company_evidence_requirements": ["cash_flow", "pricing_power"],
                }
            ],
            "data_needs": [
                {
                    "data_need_key": "insurance_float_economics",
                    "company_data_hooks": [
                        "사업별 플로트 규모 및 증감",
                        "평균 플로트 비용(연간 인수손익/평균 플로트)",
                        "pricing_power",
                    ],
                }
            ],
        },
    }


def _synthetic_sector_specific_noise_payload(*, question: str) -> dict:
    return {
        "research_context_version": "krw-guru-query-context/v1",
        "research_status": "needs_company_evidence",
        "requires_company_evidence": True,
        "filing_evidence_requirements": [
            "business_model",
            "cash_flow",
            "capital_allocation",
            "risk_factors",
        ],
        "research_pack": {
            "format": "krw-guru-research-pack/v1",
            "pack_meta": {
                "pack_id": "test-pack",
                "guru_keys": ["ackman"],
                "question": question,
            },
            "company_bridge": {
                "requires_company_evidence": True,
                "filing_evidence_requirements": [
                    "business_model",
                    "cash_flow",
                    "capital_allocation",
                    "risk_factors",
                ],
            },
            "selected_lenses": [
                {
                    "reviewed_id": "guru:ackman:pricing:test",
                    "label_ko": "가격 인상과 운영 레버 점검",
                    "summary_ko": "계약 갱신 가격, 비용 구조, 자본배분을 본다.",
                    "company_evidence_requirements": [
                        "average_contract_renewal_price_increase",
                    ],
                }
            ],
            "data_needs": [
                {
                    "data_need_key": "sector_specific_noise",
                    "company_data_hooks": [
                        "average_contract_renewal_price_increase",
                        "fuel_surcharge_mechanism",
                        "carry_realization_track_record",
                        "quarterly_gross_carried_interest",
                        "fee_related_revenue_share",
                        "target_return_assumptions_per_fund",
                    ],
                }
            ],
        },
    }


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


def _write_company_context_ranking_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "guru-company-context"
    reviewed = root / "reviewed"
    reviewed.mkdir(parents=True)
    _write_jsonl(
        reviewed / "guru_objects.jsonl",
        [
            {
                "reviewed_id": "guru:buffett:risk:commodity-residual-value:test",
                "candidate_id": "candidate:cctx1",
                "author_key": "buffett",
                "object_type": "risk_frame",
                "object_origin": "source_grounded",
                "label_ko": "원자재 가격 의존도가 만드는 잔존가치 소멸 리스크",
                "summary_ko": "원유와 가스 같은 원자재 사업은 상품가격, 자본지출, 잔존가치, 현금흐름 사이클을 함께 본다.",
                "supporting_span_ids": [],
                "intent_family": "holding_review",
                "specificity": {
                    "level": "sector_specific",
                    "source_case_tags": ["commodity", "oil", "capital intensity"],
                },
                "answer_role": {"default": "core_lens", "possible_roles": ["caution"]},
                "confidence": "high",
                "status": "reviewed",
                "related_reviewed_ids": [
                    "guru:buffett:data_need:commodity-cash-flow:test"
                ],
            },
            {
                "reviewed_id": "guru:buffett:principle:owner-manager:test",
                "candidate_id": "candidate:cctx2",
                "author_key": "buffett",
                "object_type": "principle",
                "object_origin": "source_grounded",
                "label_ko": "100% 소유자처럼 경영하라",
                "summary_ko": "경영진은 주식 가격이 아니라 장기 소유자 관점에서 회사를 운영해야 한다.",
                "supporting_span_ids": [],
                "intent_family": "holding_review",
                "specificity": {"level": "general_principle", "source_case_tags": []},
                "answer_role": {"default": "supporting_lens", "possible_roles": []},
                "confidence": "high",
                "status": "reviewed",
            },
            {
                "reviewed_id": "guru:buffett:principle:regulated-utility:test",
                "candidate_id": "candidate:cctx4",
                "author_key": "buffett",
                "object_type": "principle",
                "object_origin": "source_grounded",
                "label_ko": "고객과 규제기관의 상호 호혜 원칙",
                "summary_ko": (
                    "규제 유틸리티에서는 고객, 규제기관, 대규모 재투자, "
                    "rate case, allowed return이 장기 경제성을 좌우한다."
                ),
                "supporting_span_ids": [],
                "intent_family": "holding_review",
                "specificity": {
                    "level": "sector_specific",
                    "source_case_tags": ["regulated utility", "rate case", "allowed return"],
                },
                "answer_role": {"default": "core_lens", "possible_roles": ["checklist"]},
                "confidence": "high",
                "status": "reviewed",
                "related_reviewed_ids": [
                    "guru:buffett:data_need:regulated-utility:test"
                ],
            },
        ],
    )
    _write_jsonl(reviewed / "consultation_objects.jsonl", [])
    _write_jsonl(
        reviewed / "data_needs.jsonl",
        [
            {
                "reviewed_id": "guru:buffett:data_need:commodity-cash-flow:test",
                "candidate_id": "candidate:cctx3",
                "author_key": "buffett",
                "label_ko": "원자재 민감도와 자본지출 근거",
                "summary_ko": "회사 공시에서 commodity_price_exposure, capital_intensity, cash_flow를 확인한다.",
                "object_origin": "data_need",
                "data_need_family": "future_company_metric",
                "data_need_key": "commodity_cash_flow_context",
                "requires_company_data": True,
                "company_data_hooks": [
                    "commodity_price_exposure",
                    "capital_intensity",
                    "cash_flow",
                ],
                "supporting_span_ids": [],
                "intent_family": "holding_review",
                "specificity": {
                    "level": "sector_specific",
                    "source_case_tags": ["commodity", "oil"],
                },
                "answer_role": {"default": "data_need", "possible_roles": ["checklist"]},
                "confidence": "high",
                "status": "reviewed",
                "related_reviewed_ids": [
                    "guru:buffett:risk:commodity-residual-value:test"
                ],
            },
            {
                "reviewed_id": "guru:buffett:data_need:regulated-utility:test",
                "candidate_id": "candidate:cctx5",
                "author_key": "buffett",
                "label_ko": "요금 규제와 허용수익률 근거",
                "summary_ko": "회사 공시에서 rate_case_filings, allowed_return, regulator_decisions를 확인한다.",
                "object_origin": "data_need",
                "data_need_family": "future_company_text",
                "data_need_key": "rate_case_filings",
                "requires_company_data": True,
                "company_data_hooks": [
                    "rate_case_filings",
                    "allowed_return",
                    "regulator_decisions",
                ],
                "supporting_span_ids": [],
                "intent_family": "holding_review",
                "specificity": {
                    "level": "sector_specific",
                    "source_case_tags": ["regulated utility", "rate case", "allowed return"],
                },
                "answer_role": {"default": "data_need", "possible_roles": ["checklist"]},
                "confidence": "high",
                "status": "reviewed",
                "related_reviewed_ids": [
                    "guru:buffett:principle:regulated-utility:test"
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
                "relationship_id": "guru:relationship:cctx:test",
                "from_id": "guru:buffett:risk:commodity-residual-value:test",
                "to_id": "guru:buffett:data_need:commodity-cash-flow:test",
                "relation_type": "requires_evidence",
                "explanation_ko": "원자재 사업 렌즈는 원자재 민감도와 자본지출 근거를 요구한다.",
            },
            {
                "relationship_id": "guru:relationship:cctx-utility:test",
                "from_id": "guru:buffett:principle:regulated-utility:test",
                "to_id": "guru:buffett:data_need:regulated-utility:test",
                "relation_type": "requires_evidence",
                "explanation_ko": "규제 유틸리티 렌즈는 요금 규제와 허용수익률 근거를 요구한다.",
            }
        ],
    )
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
