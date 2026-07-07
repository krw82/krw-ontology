"""Contract tests for the separate KRW Guru Advisor plugin."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from krw_ontology.guru.skills import GURU_SKILL_MAP, guru_skill_name


PLUGIN_DIR = Path("plugins/krw-guru-advisor")
SKILLS_DIR = PLUGIN_DIR / "skills"
REFERENCE_DIR = PLUGIN_DIR / "references"
GURU_SKILLS = {
    "buffett": "krw-guru-buffett-advisor",
    "marks": "krw-guru-marks-advisor",
    "ackman": "krw-guru-ackman-advisor",
    "flatt": "krw-guru-flatt-advisor",
    "terry_smith": "krw-guru-terry-smith-advisor",
}


def test_guru_advisor_plugin_is_separate_and_has_dedicated_mcp_server() -> None:
    codex_plugin = json.loads((PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text())
    claude_plugin = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text())
    mcp_config = json.loads((PLUGIN_DIR / ".mcp.json").read_text())

    assert codex_plugin["name"] == "krw-guru-advisor"
    assert claude_plugin["name"] == "krw-guru-advisor"
    assert codex_plugin["skills"] == "./skills/"
    assert claude_plugin["skills"] == "./skills/"
    assert codex_plugin["mcpServers"] == "./.mcp.json"
    assert claude_plugin["mcpServers"] == "./.mcp.json"
    assert mcp_config["mcpServers"]["krw-guru-advisor"]["command"] == "uv"
    assert "krw-guru-advisor-mcp" in mcp_config["mcpServers"]["krw-guru-advisor"]["args"]
    guru_env = mcp_config["mcpServers"]["krw-guru-advisor"]["env"]
    assert guru_env["KRW_GURU_ENV"] == "prod"
    assert guru_env["KRW_GURU_DATA_ROOT"].endswith("krw-ontology-guru-data")
    assert guru_env["KRW_GURU_RELEASE_ROOT"].endswith(
        "krw-ontology-guru-data/releases/prod/current"
    )
    assert "KRW_GURU_ROOT" not in guru_env
    assert "KRW_GURU_RUNNING_ROOT" not in guru_env
    assert mcp_config["mcpServers"]["krw-ontology-company"] == {
        "type": "http",
        "url": "http://127.0.0.1:8765/mcp",
    }
    assert "brief-first consultation skills" in codex_plugin["interface"]["longDescription"]
    assert "shard-aware read-only Guru MCP" in codex_plugin["interface"]["longDescription"]
    assert "not added to the KRW Ontology router" in codex_plugin["interface"]["longDescription"]


def test_guru_advisor_skill_is_explicit_and_read_only() -> None:
    output_contract = (REFERENCE_DIR / "output-contract.md").read_text()
    company_contract = (REFERENCE_DIR / "company-bridge-policy.md").read_text()
    mcp_policy = (REFERENCE_DIR / "mcp-tool-policy.md").read_text()
    research_pack_contract = (REFERENCE_DIR / "research-pack-contract.md").read_text()
    skill_contract = (REFERENCE_DIR / "guru-skill-contract.md").read_text()
    answer_style_contract = (REFERENCE_DIR / "answer-style-adapters.md").read_text()
    guru_brief_policy = (REFERENCE_DIR / "guru-brief-policy.md").read_text()

    assert "Do not give personalized buy, sell, hold" in output_contract
    assert "internal guru consultation brief" in output_contract
    assert "Do not narrate execution" in output_contract
    assert "tool arguments or raw tool results" in output_contract
    assert "Do not add footer disclaimers" in output_contract
    assert "AI 렌즈 해석" in output_contract
    assert "Do not create report-style data limitation sections" in output_contract
    assert "데이터 한계" in output_contract
    assert "first-person simulated guru voice is the default" in output_contract
    assert "Do not write about the selected author in third person" in output_contract
    assert "주의:" in output_contract
    assert "not a required final-answer section" in research_pack_contract
    assert "one concise next check" in research_pack_contract
    assert "generic safety template" in output_contract
    assert "must not modify the existing KRW company ontology schema" in company_contract
    assert "Bridge Workflow" in company_contract
    assert "Company Context Prepass" in company_contract
    assert "krw_ontology_topic_map" in company_contract
    assert "company_context_json" in company_contract
    assert "never hard-coded ticker mappings" in company_contract
    assert "GuruCompanyFilingBrief" in company_contract
    assert "GuruCompanyResearchPack" in company_contract
    assert "krw_guru_query_context" in mcp_policy
    assert "krw_guru_company_brief" in mcp_policy
    assert "krw_guru_company_pack" in mcp_policy
    assert "company_context_json" in mcp_policy
    assert "orientation only, not evidence" in mcp_policy
    assert "All MCP usage is silent" in mcp_policy
    assert "krw_guru_index_context" in mcp_policy
    assert "author SQLite shards" in mcp_policy
    assert "krw_guru_context" not in mcp_policy
    assert "Skills must not hard-code guru principles" in research_pack_contract
    assert "GuruAnswerRenderPlan" in research_pack_contract
    assert "company ontology orientation" in research_pack_contract
    assert "must not be created from ticker hard-coding" in research_pack_contract
    assert "private internal guru consultation brief" in research_pack_contract
    assert "The application code owns guru selection" in skill_contract
    assert "guru-brief-policy.md" in skill_contract
    assert "narrate workflow" in skill_contract
    assert "Never print the brief" in skill_contract
    assert "do not describe the author from the outside" in skill_contract
    assert "Mandatory Composition" in answer_style_contract
    assert "Do not turn missing company evidence into a separate analyst-report section" in answer_style_contract
    assert "First-person simulated guru voice is the default" in answer_style_contract
    assert "Do not write the final answer as a third-person report" in answer_style_contract
    assert "private internal guru consultation brief" in answer_style_contract
    assert "footer disclaimers" in answer_style_contract
    assert "Author-Specific Style" in answer_style_contract
    assert "primary_intent" in guru_brief_policy
    assert "secondary_intents" in guru_brief_policy
    assert "Use `krw_guru_index_context` only for debug" in guru_brief_policy

    assert not (SKILLS_DIR / "krw-guru-advisor" / "SKILL.md").exists()
    assert GURU_SKILL_MAP == GURU_SKILLS
    for author_key, skill_name in GURU_SKILLS.items():
        skill_text = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        agent_text = (SKILLS_DIR / skill_name / "agents" / "openai.yaml").read_text()
        local_style_text = (SKILLS_DIR / skill_name / "references" / "answer-style.md").read_text()

        assert guru_skill_name(author_key) == skill_name
        assert f"name: {skill_name}" in skill_text
        assert f'Fixed `author_key`: `{author_key}`.' in skill_text
        assert "This is not a router" in skill_text
        assert "must not hard-code" in skill_text
        assert "User-facing output is final-answer only" in skill_text
        assert "first-person simulated advisor voice" in skill_text
        assert "Do not print a report-style data limitation section" in skill_text
        assert "speak directly from the selected advisor posture" in skill_text
        assert "raw tool output" in skill_text
        assert "private internal guru consultation brief" in skill_text
        assert "guru-brief-policy.md" in skill_text
        assert "references/answer-style.md" in skill_text
        assert "answer-style-adapters.md" in skill_text
        assert "krw_guru_query_context" in skill_text
        assert "krw_guru_company_brief" in skill_text
        assert "krw_guru_company_pack" in skill_text
        assert "krw_ontology_topic_map" in skill_text
        assert "company_context_json" in skill_text
        assert "Do not infer company context from memory" in skill_text
        assert "Read before answering" not in skill_text
        assert "Reference files for maintainers only" in skill_text
        assert f'author_keys=["{author_key}"]' in skill_text
        assert "ResearchPack" in skill_text
        assert "GuruCompanyResearchPack" in skill_text
        assert "private internal guru consultation brief" in agent_text
        assert "Work silently" in agent_text
        assert "return final Korean investor-facing prose only" in agent_text
        assert "raw tool results" in agent_text
        assert "brief-optimized question" in agent_text
        assert "company_context_json" in agent_text
        assert "Do not hard-code" in agent_text
        assert "ticker context" in agent_text
        assert "krw_guru_company_brief" in agent_text
        assert "krw_guru_company_pack" in agent_text
        assert "skill-local references/answer-style.md" in agent_text
        assert "voice, texture, and analogy rendering" in agent_text
        assert "immersive first-person advisor voice" in agent_text
        assert "report footer disclaimers" in agent_text
        assert "krw_guru_query_context" in agent_text
        assert f"Fixed `author_key`: `{author_key}`." in local_style_text
        assert "This file controls rendering only" in local_style_text
        assert "## Rendering Boundary" in local_style_text
        assert "## Voice" in local_style_text
        assert "## Consultation Persona" in local_style_text
        assert "Default to direct first-person consultation" in local_style_text
        assert "## Texture" in local_style_text
        assert "## Allowed Analogies" in local_style_text
        assert "## Identity Boundary" in local_style_text
        assert "## Style Transformations" in local_style_text
        assert "ResearchPack decides what to say" in local_style_text
        assert "Do not write:" in local_style_text
        assert "Prefer:" in local_style_text
        assert "말투로 바꾸면" not in local_style_text
        assert "현재 온톨로지" not in local_style_text

    with pytest.raises(ValueError, match="Unknown guru author_key"):
        guru_skill_name("unknown")


def test_guru_advisor_is_not_registered_in_existing_router() -> None:
    router_text = Path("plugins/krw-ontology/skills/krw-ontology-router/SKILL.md").read_text()
    router_agent_text = (
        Path("plugins/krw-ontology/skills/krw-ontology-router/agents/openai.yaml").read_text()
    )

    assert "guru" not in router_text.lower()
    assert "krw-guru-advisor" not in router_text
    assert "guru" not in router_agent_text.lower()
    assert "krw-guru-advisor" not in router_agent_text


def test_guru_advisor_eval_questions_cover_investor_consultation_contract() -> None:
    eval_path = REFERENCE_DIR / "eval-questions.jsonl"
    rows = [json.loads(line) for line in eval_path.read_text().splitlines() if line.strip()]

    assert 20 <= len(rows) <= 30
    assert len({row["id"] for row in rows}) == len(rows)
    assert any(row["requires_company_evidence"] for row in rows)
    assert any(not row["requires_company_evidence"] for row in rows)

    lenses = {lens for row in rows for lens in row["lenses"]}
    assert {"buffett", "marks", "ackman", "flatt", "terry_smith"} <= lenses

    for row in rows:
        assert row["id"].startswith("guru_eval_")
        assert row["question"]
        assert row["intent"]
        assert row["lenses"]
        assert isinstance(row["requires_company_evidence"], bool)
        assert isinstance(row["company_evidence_required"], list)
        assert isinstance(row["expected_behavior"], list)
        if row["requires_company_evidence"]:
            assert row["company_evidence_required"]
            assert "use_filing_evidence" in row["expected_behavior"] or any(
                "filing" in behavior for behavior in row["expected_behavior"]
            )


def test_guru_advisor_gold_eval_covers_research_pack_quality_contract() -> None:
    gold_path = REFERENCE_DIR / "gold-eval.jsonl"
    rows = [json.loads(line) for line in gold_path.read_text().splitlines() if line.strip()]

    assert len(rows) == 30
    assert len({row["id"] for row in rows}) == len(rows)
    assert any(row["requires_company_evidence"] for row in rows)
    assert any(not row["requires_company_evidence"] for row in rows)

    authors = {author for row in rows for author in row["author_keys"]}
    assert {"buffett", "marks", "ackman", "flatt", "terry_smith"} <= authors

    for row in rows:
        assert row["id"].startswith("guru_gold_")
        assert row["question"]
        assert row["expected_statuses"]
        assert row["expected_intent"]
        assert isinstance(row["requires_company_evidence"], bool)
        assert row["min_lenses"] >= 1
        assert row["must_include_any"]
        assert row["expected_answer_roles"]
        assert row["expect_soft_metadata"] is True


def test_existing_krw_ontology_plugin_metadata_is_not_repointed() -> None:
    existing_codex = json.loads(
        Path("plugins/krw-ontology/.codex-plugin/plugin.json").read_text()
    )
    existing_claude = json.loads(
        Path("plugins/krw-ontology/.claude-plugin/plugin.json").read_text()
    )

    assert existing_codex["name"] == "krw-ontology"
    assert existing_claude["name"] == "krw-ontology"
    assert existing_codex["mcpServers"] == "./.mcp.json"
    assert existing_claude["mcpServers"] == "./.mcp.json"


def test_local_marketplace_exposes_guru_advisor_as_separate_plugin() -> None:
    marketplace = json.loads(Path(".agents/plugins/marketplace.json").read_text())
    plugins = {plugin["name"]: plugin for plugin in marketplace["plugins"]}

    assert plugins["krw-ontology"]["source"]["path"] == "./plugins/krw-ontology"
    assert plugins["krw-guru-advisor"]["source"]["path"] == "./plugins/krw-guru-advisor"
    assert plugins["krw-guru-advisor"]["policy"]["installation"] == "AVAILABLE"
