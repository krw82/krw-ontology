from __future__ import annotations

import copy
import json

import pytest

from krw_ontology.guru.company_bridge import (
    GuruEvidenceAnalysisValidationError,
    seal_guru_investigation_brief,
    validate_guru_agent_evidence_analysis,
)
from krw_ontology.guru.mcp_tools import _philosophy_context_from_lenses
from krw_ontology.guru import mcp_tools


def _research_pack() -> dict:
    return {
        "format": "krw-guru-research-pack/v1",
        "pack_meta": {
            "pack_id": "guru-pack-aapl",
            "guru_keys": ["buffett"],
            "question": "Assess Apple through a durable-earnings lens.",
        },
        "selected_lenses": [
            {
                "reviewed_id": "guru:buffett:principle:durable_earnings",
                "author_key": "buffett",
                "label_ko": "지속 가능한 수익력",
            }
        ],
    }


def _company_context() -> dict:
    return {
        "format": "krw-guru-light-company-context/v1",
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "sector": "Technology",
        "business_description": "Apple designs consumer devices and related services.",
        "primary_activities": ["consumer devices", "digital services"],
        "products_or_segments": ["iPhone", "Services"],
        "revenue_logic": "Device sales and recurring ecosystem services.",
        "context_anchors": [
            {
                "anchor_id": "business_description",
                "kind": "business_description",
                "text": "Apple designs consumer devices and related services.",
            },
            {
                "anchor_id": "segment_services",
                "kind": "segment",
                "text": "Services is a distinct reported segment.",
            },
        ],
        "filing_availability": {
            "has_current_filing": True,
            "has_annual_baseline": True,
        },
    }


def _draft() -> dict:
    return {
        "question": "Does Services have filing support for resilience beyond replacement demand?",
        "guru_principle_ids": ["guru:buffett:principle:durable_earnings"],
        "company_context_anchor_ids": ["business_description", "segment_services"],
        "hypothesis": "Services improves the durability of owner earnings.",
        "counter_hypothesis": "Services remains dependent on device replacement demand.",
        "evidence_needed": ["segment disclosure", "revenue driver discussion"],
        "strengthens_if": "Filings show recurring demand resilient to device cycles.",
        "weakens_if": "Filings tie Services economics mostly to device demand.",
        "why_material": "The answer changes whether the business has an independent durable engine.",
        "decision_role": "main_tension",
    }


def _company_research_context(brief_hash: str, question_id: str) -> dict:
    return {
        "format": "krw-guru-company-research-context/v1",
        "ticker": "AAPL",
        "brief_hash": brief_hash,
        "question_ids": [question_id],
        "evidence_units": [
            {
                "evidence_id": "ev-services",
                "object_id": "claim:AAPL:services",
                "ticker": "AAPL",
                "source": {"object_ids": ["claim:AAPL:services"]},
            }
        ],
        "source_object_ids": ["claim:AAPL:services"],
        "research_status": "partial",
    }


def _analysis(brief: dict) -> dict:
    question = brief["questions"][0]
    return {
        "assessments": [
            {
                "question_id": question["question_id"],
                "evidence_object_ids": ["claim:AAPL:services"],
                "verdict": "mixed",
                "reasoning": "The verified evidence supports the service engine but does not settle independence.",
            }
        ],
        "overall_judgment": "The durable-earnings case is mixed because device dependence remains material.",
    }


def test_server_seals_main_agent_question_against_real_principle_and_anchor() -> None:
    brief = seal_guru_investigation_brief(
        guru_payload=_research_pack(),
        investigation_questions=[_draft()],
        ticker="AAPL",
        company_context=_company_context(),
        author_key="buffett",
    )

    assert brief.research_pack_id == "guru-pack-aapl"
    assert brief.questions[0].question_id.startswith("q_")
    assert len(brief.brief_hash) == 64
    assert brief.questions[0].guru_principle_ids == [
        "guru:buffett:principle:durable_earnings"
    ]


def test_server_rejects_multiple_key_questions() -> None:
    with pytest.raises(ValueError, match="exactly one key question"):
        seal_guru_investigation_brief(
            guru_payload=_research_pack(),
            investigation_questions=[_draft(), _draft()],
            ticker="AAPL",
            company_context=_company_context(),
            author_key="buffett",
        )


def test_server_rejects_question_with_unselected_principle_or_unknown_anchor() -> None:
    bad_principle = _draft()
    bad_principle["guru_principle_ids"] = ["guru:marks:risk:unknown"]
    with pytest.raises(ValueError, match="outside the selected Guru ResearchPack"):
        seal_guru_investigation_brief(
            guru_payload=_research_pack(),
            investigation_questions=[bad_principle],
            ticker="AAPL",
            company_context=_company_context(),
            author_key="buffett",
        )

    bad_anchor = _draft()
    bad_anchor["company_context_anchor_ids"] = ["invented_anchor"]
    with pytest.raises(ValueError, match="unknown company context anchor"):
        seal_guru_investigation_brief(
            guru_payload=_research_pack(),
            investigation_questions=[bad_anchor],
            ticker="AAPL",
            company_context=_company_context(),
            author_key="buffett",
        )


def test_analysis_returns_a_decision_frame_and_rejects_cross_question_evidence() -> None:
    brief_model = seal_guru_investigation_brief(
        guru_payload=_research_pack(),
        investigation_questions=[_draft()],
        ticker="AAPL",
        company_context=_company_context(),
        author_key="buffett",
    )
    brief = brief_model.model_dump(mode="json")
    context = _company_research_context(
        brief["brief_hash"], brief["questions"][0]["question_id"]
    )
    analysis = _analysis(brief)

    validated = validate_guru_agent_evidence_analysis(
        investigation_brief=brief,
        company_research_context=context,
        agent_analysis=analysis,
    )
    assert validated.decision_frame["main_tension_question_id"] == brief["questions"][0]["question_id"]

    invalid = copy.deepcopy(analysis)
    invalid["assessments"][0]["evidence_object_ids"] = ["claim:AAPL:invented"]
    with pytest.raises(GuruEvidenceAnalysisValidationError) as exc_info:
        validate_guru_agent_evidence_analysis(
            investigation_brief=brief,
            company_research_context=context,
            agent_analysis=invalid,
        )
    assert exc_info.value.code == "question_scoped_evidence_required"
    assert exc_info.value.violations[0]["question_id"] == brief["questions"][0]["question_id"]
    assert exc_info.value.violations[0]["allowed_evidence_object_ids"] == [
        "claim:AAPL:services"
    ]


def test_analysis_rejects_a_model_supplied_or_malformed_runtime_context() -> None:
    brief_model = seal_guru_investigation_brief(
        guru_payload=_research_pack(),
        investigation_questions=[_draft()],
        ticker="AAPL",
        company_context=_company_context(),
        author_key="buffett",
    )
    brief = brief_model.model_dump(mode="json")
    malformed_context = _company_research_context(
        brief["brief_hash"], brief["questions"][0]["question_id"]
    )
    malformed_context["format"] = "model-created-context/v1"

    with pytest.raises(GuruEvidenceAnalysisValidationError) as exc_info:
        validate_guru_agent_evidence_analysis(
            investigation_brief=brief,
            company_research_context=malformed_context,
            agent_analysis=_analysis(brief),
        )

    assert exc_info.value.code == "invalid_company_research_context"
    assert exc_info.value.invalid_fields == ["company_research_context"]
    assert "Attach the exact krw-guru-company-research-context/v1" in (
        exc_info.value.required_change
    )


def test_philosophy_context_exposes_selected_source_rule_for_virtual_advisor() -> None:
    context = _philosophy_context_from_lenses(
        selected_authors=["buffett"],
        lens_rows=[
            {
                "reviewed_id": "guru:buffett:principle:durable_earnings",
                "author_key": "buffett",
                "label_ko": "지속 가능한 수익력",
                "summary_ko": "반복 가능한 경제성을 먼저 확인한다.",
                "object_type": "principle",
                "company_data_hooks": ["segment disclosure"],
                "supporting_span_ids": ["span:durable"],
                "applicability": {
                    "strong_for": ["business_quality"],
                    "anti_triggers": ["unresolved cyclicality"],
                },
            }
        ],
        compact_lenses=[
            {
                "reviewed_id": "guru:buffett:principle:durable_earnings",
                "company_data_hooks": ["segment disclosure"],
            }
        ],
    )

    principle = context["principles"][0]
    assert principle["principle_id"] == "guru:buffett:principle:durable_earnings"
    assert principle["statement"] == "반복 가능한 경제성을 먼저 확인한다."
    assert principle["source_anchor_ids"] == ["span:durable"]
    assert "persona" not in context
    assert "selected-author-inspired virtual advisor" in context["boundary"]
    assert "Do not claim to be or speak as the real person" in context["boundary"]


def test_company_brief_seals_the_original_query_context_without_reranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_context = {
        "research_status": "needs_company_evidence",
        "answerability": {},
        "selected_author_keys": ["buffett"],
        "research_pack": {
            **_research_pack(),
            "company_bridge": {"requires_company_evidence": True},
            "intent": {"ticker": "AAPL"},
        },
    }

    def unexpected_requery(**_: object) -> str:
        raise AssertionError("company brief must not re-query a supplied context")

    monkeypatch.setattr(mcp_tools, "guru_query_context_tool", unexpected_requery)
    result = json.loads(
        mcp_tools.guru_company_brief_tool(
            question="Assess Apple through a durable-earnings lens.",
            author_keys=["buffett"],
            ticker="AAPL",
            company_context=_company_context(),
            guru_query_context=query_context,
            investigation_questions=[_draft()],
        )
    )

    assert result["investigation_brief"]["research_pack_id"] == "guru-pack-aapl"
