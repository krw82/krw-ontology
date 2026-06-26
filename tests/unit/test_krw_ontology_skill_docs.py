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


def test_krw_ontology_market_move_skill_is_separate_from_news_research() -> None:
    news_dir = Path("plugins/krw-ontology/skills/krw-ontology-news-research")
    discovery_dir = Path("plugins/krw-ontology/skills/krw-ontology-news-discovery")
    market_move_dir = Path("plugins/krw-ontology/skills/krw-ontology-market-move-research")

    skill_text = (news_dir / "SKILL.md").read_text()
    news_policy_text = (news_dir / "references" / "news-event-policy.md").read_text()
    tool_policy_text = (news_dir / "references" / "tool-policy.md").read_text()
    market_move_policy_text = (
        market_move_dir / "references" / "market-move-context-policy.md"
    ).read_text()
    market_move_skill_text = (market_move_dir / "SKILL.md").read_text()
    market_move_agent_text = (market_move_dir / "agents" / "openai.yaml").read_text()
    market_move_output_text = (
        market_move_dir / "references" / "output-contract.md"
    ).read_text()
    market_move_bridge_text = (
        market_move_dir / "references" / "ontology-bridge-policy.md"
    ).read_text()
    discovery_text = (discovery_dir / "SKILL.md").read_text()

    assert "use `krw-ontology-market-move-research` instead" in skill_text
    assert "recent stock moves" not in skill_text
    assert "app-provided market context" not in skill_text
    assert "route to `krw-ontology-market-move-research`" in news_policy_text
    assert "Use stock-news event tools as the current-news discovery layer" in tool_policy_text
    assert "name: krw-ontology-market-move-research" in market_move_skill_text
    assert "selected_news_event_context.version = market-move-context/v1" in market_move_skill_text
    assert "references/market-move-context-policy.md" in market_move_skill_text
    assert "이어서 볼 질문" in market_move_skill_text
    assert "lightweight market-move answer" in market_move_skill_text
    assert "Use KRW ontology only when" in market_move_skill_text
    assert "KRW Ontology Market Move Research" in market_move_agent_text
    assert "follow the skill's research workflow and reference files" in market_move_agent_text
    assert "market move context = observed stock move" in market_move_policy_text
    assert "Price Move Is Not The Cause" in market_move_policy_text
    assert "default answer is lightweight" in market_move_output_text
    assert "Follow-ups must bridge the observed move/news candidate into filing-based research" in market_move_output_text
    assert "Do not invoke the bridge automatically" in market_move_bridge_text
    assert "market-news discovery" in discovery_text
    assert "app-normalized provider results" in discovery_text


def test_krw_ontology_router_skill_is_thin_and_market_move_aware() -> None:
    router_dir = Path("plugins/krw-ontology/skills/krw-ontology-router")

    router_text = (router_dir / "SKILL.md").read_text()
    router_agent_text = (router_dir / "agents" / "openai.yaml").read_text()

    assert "name: krw-ontology-router" in router_text
    assert "thin routing skill" in router_text
    assert "It does not research, browse, call MCP tools" in router_text
    assert "market_move_research" in router_text
    assert "오늘 가격 변동이 왜 이래?" in router_text
    assert "Return only `run_kind`" in router_text
    assert "Generic public-equity routers often avoid simple share-price questions" in router_text
    assert "not default company research" in router_text
    assert "KRW Ontology Router" in router_agent_text
    assert "return anything except JSON with run_kind" in router_agent_text
