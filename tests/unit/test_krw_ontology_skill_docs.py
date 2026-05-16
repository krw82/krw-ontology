"""Tests for KRW ontology skill research protocols."""

from __future__ import annotations

from pathlib import Path


def test_krw_ontology_skill_documents_scenario_protocols():
    research_dir = Path("plugins/krw-ontology/skills/krw-ontology-research")
    composer_dir = Path("plugins/krw-ontology/skills/krw-ontology-answer-composer")
    skill_text = (research_dir / "SKILL.md").read_text()
    tools_text = (research_dir / "references" / "tools.md").read_text()
    synthesis_text = (research_dir / "references" / "research-synthesis-contract.md").read_text()
    composer_text = (composer_dir / "SKILL.md").read_text()
    display_plan_text = (composer_dir / "references" / "display-plan-contract.md").read_text()
    research_openai_text = (
        research_dir / "agents" / "openai.yaml"
    ).read_text()
    composer_openai_text = (
        composer_dir / "agents" / "openai.yaml"
    ).read_text()

    assert "## Scenario And Sensitivity Questions" in skill_text
    assert "revenue, cost of revenue, operating margin, cash flow, and liquidity" in skill_text
    assert "Search separately for explicit threshold terms" in skill_text
    assert "Question Type Protocols" in skill_text
    assert "## Two-Pass Product Boundary" in skill_text
    assert "references/research-synthesis-contract.md" in skill_text
    assert "canonical_answer.units" in skill_text
    assert "It should not choose visual blocks" in skill_text
    assert "do not force structured output" in skill_text
    assert "Do not assume one query is enough for scenario questions" in tools_text
    assert "coverage warnings" in tools_text
    assert "KRW Ontology Research Synthesis Contract" in synthesis_text
    assert "krw-research-synthesis/v1" in synthesis_text
    assert "`canonical_answer.units` is the source of truth" in synthesis_text
    assert '"canonical_answer"' in synthesis_text
    assert '"display_guidance"' in synthesis_text
    assert "Company overview" in synthesis_text
    assert "Do not emit `answer_blocks` from the research skill" in synthesis_text
    assert "Do not emit `display_plan` from the research skill" in synthesis_text
    assert "krw-ontology-answer-composer" in composer_text
    assert "Use this skill after `krw-ontology-research` has produced" in composer_text
    assert "this skill is a display planner" in composer_text
    assert "This skill does not own" in composer_text
    assert "Freedom Within Guardrails" in composer_text
    assert "references/display-plan-contract.md" in composer_text
    assert "KRW Ontology Display Plan Contract" in display_plan_text
    assert "ResearchSynthesis.canonical_answer -> DisplayPlan -> frontend renderer" in display_plan_text
    assert "The display planner does not write research content" in display_plan_text
    assert "## Final Response Envelope" in display_plan_text
    assert '"display_plan"' in display_plan_text
    assert "## Compact JSON Schema" in display_plan_text
    assert "Allowed Block Types" in display_plan_text
    assert "forbidden content fields" in display_plan_text
    assert '"maxItems": 12' in display_plan_text
    assert "Do not produce `answer_blocks`" in display_plan_text
    assert "Do not copy source HTML/CSS/JavaScript" in display_plan_text
    assert "The product runner should pass that synthesis" in research_openai_text
    assert "canonical_answer units" in research_openai_text
    assert "$krw-ontology-answer-composer" in research_openai_text
    assert "Return display_plan only" in composer_openai_text
