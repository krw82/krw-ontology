"""Tests for KRW ontology skill research protocols."""

from __future__ import annotations

from pathlib import Path


def test_krw_ontology_skill_documents_match_current_web_chat_contract() -> None:
    research_dir = Path("plugins/krw-ontology/skills/krw-ontology-research")
    composer_dir = Path("plugins/krw-ontology/skills/krw-ontology-answer-composer")

    skill_text = (research_dir / "SKILL.md").read_text()
    tool_policy_text = (research_dir / "references" / "tool-policy.md").read_text()
    forbidden_text = (
        research_dir / "references" / "forbidden-user-facing-language.md"
    ).read_text()
    composer_text = (composer_dir / "SKILL.md").read_text()

    assert "Recommended Primary Research Pattern" in skill_text
    assert "Use this as the default research playbook, not as a rigid state machine" in skill_text
    assert "Evidence floor and overflow recovery" in skill_text
    assert "Do not write a substantive answer from catalog, diagnostic, overflow-only, or status-only evidence" in skill_text
    assert "Treat overflow as a scope problem, not as evidence" in skill_text
    assert "Company filing commentary explains why it changed and what channel matters next" in skill_text
    assert "trace = evidence verification for one selected object" in skill_text
    assert "chain = mechanism expansion around one selected object" in skill_text
    assert "Do not call broad retrieve after a sufficient query_context" in skill_text
    assert 'response_detail="full"' in skill_text

    assert "Default first call for natural-language research" in tool_policy_text
    assert "Never request `response_detail=\"full\"` in normal web chat" in tool_policy_text
    assert "selected trace/chain when stronger verification is needed" in tool_policy_text
    assert "Legacy fallback only. Do not use after sufficient query_context" in tool_policy_text

    assert "공시자료 기반 한계" in forbidden_text
    assert "참고: 위 분석은" in forbidden_text
    assert "질적 리스크 요인을 중심으로" in forbidden_text
    assert "tool_budget_exceeded" in forbidden_text
    assert "response_detail" not in forbidden_text
    assert "query_context" in forbidden_text
    assert "research_pack" in forbidden_text

    assert "This skill is optional" in composer_text
    assert "It is not part of the default web-chat Markdown runtime" in composer_text
    assert "This skill does not own" in composer_text
    assert "MCP tool calls" in composer_text
