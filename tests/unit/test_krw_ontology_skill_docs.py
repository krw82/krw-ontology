"""Tests for KRW ontology skill research protocols."""

from __future__ import annotations

from pathlib import Path


def test_krw_ontology_skill_documents_match_current_mcp_contract():
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

    assert "research_context_version" in skill_text
    assert "research_status" in skill_text
    assert "research_pack.metric_series_pack" in skill_text
    assert "research_pack.projection_pack" in skill_text
    assert "research_pack.chain_pack" in skill_text
    assert "research_pack.company_topic_pack" in skill_text
    assert "research_pack.directness_guard" in skill_text
    assert "research_pack.stop_guard" in skill_text
    assert "research_pack.valuation_guard" in skill_text
    assert "agent_autonomy.mode" in skill_text
    assert "agent_autonomy.may_continue_research" in skill_text
    assert "agent_autonomy.allowed_next_tools" in skill_text
    assert "agent_autonomy.disallowed_next_tools" in skill_text
    assert "agent_autonomy.max_additional_tool_calls" in skill_text
    assert "Do not assume `metric_series_pack`, `projection_pack`, `chain_pack`, or `allowed_next_tools` are standalone top-level fields" in skill_text
    assert "sufficient_for_default_answer" in skill_text
    assert "sufficient_but_trace_recommended" in skill_text
    assert "partial_answer_possible" in skill_text
    assert "needs_targeted_followup" in skill_text
    assert "no_direct_evidence_with_related_context" in skill_text
    assert "out_of_scope_for_filing_ontology" in skill_text
    assert "not_answerable" in skill_text
    assert "krw_ontology_retrieve" in skill_text
    assert "krw_ontology_query" in skill_text
    assert "krw_ontology_compare" in skill_text
    assert "unscoped_krw_ontology_query" in skill_text
    assert "broad_unscoped_retrieve" in skill_text
    assert "### 4.5 Operational guardrails for expensive paths" in skill_text
    assert "`index_context` is debug-only" in skill_text
    assert "allow_expensive=true" in skill_text
    assert "Do not issue compound broad metric queries" in skill_text
    assert "iPhone Services Mac iPad Wearables net sales revenue" in skill_text
    assert "A single metric row cannot simultaneously be iPhone, Services, Mac, iPad, and Wearables" in skill_text
    assert "one compact query per dimension" in skill_text
    assert "Do not use `response_detail=\"full\"` for broad first-pass search" in skill_text
    assert "Broad / exploratory / multi-ticker / multi-object-type / first-pass query -> compact" in skill_text
    assert "Do not call multi-ticker `query_context` as the first deep search for broad discovery" in skill_text
    assert "candidate narrowing before deep context" in skill_text
    assert 'research_pack.metric_series_pack.mode = "metric_dimension_lookup"' in skill_text
    assert "target_dimension_metric" in skill_text
    assert "denominator_metric" in skill_text
    assert "denominator_needed" in skill_text
    assert "quality.period_alignment" in skill_text
    assert "quality.unit_consistency" in skill_text
    assert "quality.dimension_metric_not_found" in skill_text
    assert "share_of_total" in skill_text
    assert "growth_difference" in skill_text
    assert "projection_candidates_are_search_candidates_only = true" in skill_text
    assert 'research_pack.chain_pack.mode = "lazy_root_candidates"' in skill_text
    assert "revenue, cost of revenue, operating margin, cash flow, and liquidity" in skill_text
    assert "Search separately for explicit threshold terms" in skill_text
    assert "## 16. Two-Pass Product Boundary" in skill_text
    assert "references/research-synthesis-contract.md" in skill_text
    assert "canonical_answer.units" in skill_text
    assert "It should not choose visual blocks" in skill_text
    assert "do not force structured output" in skill_text
    assert "Do not include a visible \"quality note\"" in skill_text
    assert "Forbidden customer-facing terms" in skill_text
    assert "Treat quality signals as internal controls" in skill_text
    assert "Do not expose ontology object type names in normal answers" in skill_text
    assert "Do not paste source labels followed by original quote text" in skill_text
    assert "Do not add visible generic caveats" in skill_text
    assert "## 19. Final Response Sanitizer Pass" in skill_text
    assert "Remove progress leakage" in skill_text
    assert "진행 중입니다" in skill_text
    assert "검색해보겠습니다" in skill_text
    assert "tool call" in skill_text
    assert "progress" in skill_text
    assert "MCP" in skill_text
    assert "plugin" in skill_text
    assert "skill" in skill_text
    assert "ontology / 온톨로지" in skill_text
    assert "query_context" in skill_text
    assert "research_pack" in skill_text
    assert "rewrite the sentence into filing-facing language" in skill_text
    assert "Did the final sanitizer remove progress leakage" in skill_text
    assert "retrieved evidence is weak" not in skill_text
    assert "filing support is weak" not in skill_text
    assert "공시 근거가 제한적입니다" not in skill_text
    assert "회사가 이 항목을 명확히 수치화하지 않았습니다" not in skill_text
    assert "Do not assume one query is enough for scenario questions" in tools_text
    assert "coverage warnings" in tools_text
    assert "research_pack.metric_series_pack" in tools_text
    assert "research_pack.projection_pack" in tools_text
    assert "research_pack.chain_pack" in tools_text
    assert "research_pack.directness_guard" in tools_text
    assert "agent_autonomy.allowed_next_tools" in tools_text
    assert "do_not_call" in tools_text
    assert 'research_pack.metric_series_pack.mode = "metric_dimension_lookup"' in tools_text
    assert 'research_pack.chain_pack.mode = "lazy_root_candidates"' in tools_text
    assert "legacy_retrieve_skipped" in tools_text
    assert "research_context.do_not_call" in tools_text
    assert "allow_expensive" in tools_text
    assert "No-args index_context is lightweight by default" in tools_text
    assert "Expensive counts and quality summary scans are disabled unless allow_expensive=true" in tools_text
    assert "KRW Ontology Research Synthesis Contract" in synthesis_text
    assert "krw-research-synthesis/v1" in synthesis_text
    assert "`canonical_answer.units` is the source of truth" in synthesis_text
    assert '"canonical_answer"' in synthesis_text
    assert '"display_guidance"' in synthesis_text
    assert "Company overview" in synthesis_text
    assert "Do not emit `answer_blocks` from the research skill" in synthesis_text
    assert "Do not emit `display_plan` from the research skill" in synthesis_text
    assert "Do not put internal quality, coverage, index" in synthesis_text
    assert "Do not add customer-facing sections named" in synthesis_text
    assert "Do not expose ontology object type names" in synthesis_text
    assert "Do not paste original filing quote text" in synthesis_text
    assert "Do not add visible generic caveats" in synthesis_text
    assert "Do not leak progress narration" in synthesis_text
    assert "Before finalizing visible units, run a final response sanitizer pass" in synthesis_text
    assert "공시자료에서 확인되는 내용" in synthesis_text
    assert "공시 근거가 제한적입니다" not in synthesis_text
    assert "회사가 이 항목을 명확히 수치화하지 않았습니다" not in synthesis_text
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
    assert "run the final response sanitizer" in research_openai_text
    assert "remove progress narration and internal terms" in research_openai_text
    assert "$krw-ontology-answer-composer" in research_openai_text
    assert "Return display_plan only" in composer_openai_text
    assert "do not rewrite content, add facts, narrate progress, or mention internal terms" in composer_openai_text
