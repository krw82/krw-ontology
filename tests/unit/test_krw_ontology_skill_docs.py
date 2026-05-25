"""Tests for KRW ontology skill research protocols."""

from __future__ import annotations

from pathlib import Path


def test_krw_ontology_skill_documents_match_current_web_chat_contract() -> None:
    research_dir = Path("plugins/krw-ontology/skills/krw-ontology-research")
    composer_dir = Path("plugins/krw-ontology/skills/krw-ontology-answer-composer")

    skill_text = (research_dir / "SKILL.md").read_text()
    tool_policy_text = (research_dir / "references" / "tool-policy.md").read_text()
    mode_policy_text = (research_dir / "references" / "research-mode-policy.md").read_text()
    forbidden_text = (
        research_dir / "references" / "forbidden-user-facing-language.md"
    ).read_text()
    composer_text = (composer_dir / "SKILL.md").read_text()

    assert "Korean Markdown answer only" in skill_text
    assert "V1 web-chat operation is deep-first" in skill_text
    assert "normal path = one agent run researches and writes the final Markdown answer" in skill_text
    assert "composer fallback = emergency recovery only" in skill_text
    assert "Call krw_ontology_query_context first" in skill_text
    assert "End with exactly 3 related follow-up questions" in skill_text
    assert "Do not call broad retrieve after a sufficient query_context" in skill_text
    assert 'never request `response_detail="full"`' in skill_text
    assert "use selected trace/chain for verification instead of full query output" in skill_text

    assert "For V1 web chat, never request `response_detail=\"full\"`" in tool_policy_text
    assert "selected trace/chain when stronger verification is needed" in tool_policy_text
    assert "Legacy fallback only. Do not use after sufficient query_context" in tool_policy_text
    assert "All V1 web-chat research modes forbid `response_detail=\"full\"`" in mode_policy_text
    assert "response_detail=\"full\": forbidden" in mode_policy_text
    assert "Deep mode means more careful selected verification" in mode_policy_text
    assert "unscoped retrieve still discouraged" in mode_policy_text

    assert "공시자료 기반 한계" in forbidden_text
    assert "tool_budget_exceeded" in forbidden_text
    assert "response_detail" not in forbidden_text
    assert "query_context" in forbidden_text
    assert "research_pack" in forbidden_text

    assert "This skill is optional" in composer_text
    assert "It is not part of the default web-chat Markdown runtime" in composer_text
    assert "This skill does not own" in composer_text
    assert "MCP tool calls" in composer_text
