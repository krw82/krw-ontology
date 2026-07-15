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
    # The main Guru plugin owns only its read-only philosophy MCP. Company
    # filing evidence is delegated by the app-configured evidence Agent, not
    # through a second direct plugin MCP connection.
    assert set(mcp_config["mcpServers"]) == {"krw-guru-advisor"}
    assert "brief-first consultation skills" in codex_plugin["interface"]["longDescription"]
    assert "shard-aware read-only Guru MCP" in codex_plugin["interface"]["longDescription"]
    assert "not added to the KRW Ontology router" in codex_plugin["interface"]["longDescription"]


def test_guru_advisor_skill_is_explicit_and_read_only() -> None:
    output_contract = (REFERENCE_DIR / "output-contract.md").read_text()
    company_contract = (REFERENCE_DIR / "company-bridge-policy.md").read_text()
    mcp_policy = (REFERENCE_DIR / "mcp-tool-policy.md").read_text()
    research_pack_contract = (REFERENCE_DIR / "research-pack-contract.md").read_text()
    skill_contract = (REFERENCE_DIR / "guru-skill-contract.md").read_text()
    normalized_skill_contract = " ".join(skill_contract.split())
    answer_style_contract = (REFERENCE_DIR / "answer-style-adapters.md").read_text()
    guru_brief_policy = (REFERENCE_DIR / "guru-brief-policy.md").read_text()

    assert "Do not give personalized buy, sell, hold" in output_contract
    assert "Never expose skills, plugins, tools, internal briefs" in output_contract
    assert "Do not present the selected author as speaking" in output_contract
    assert "conditional interpretation must retain the material assumption" in output_contract
    assert "strongest supported qualitative view" in output_contract
    assert "when they appear in the runtime filing context" in output_contract
    assert "Do not create a separate no-tools rewrite pass" in output_contract
    assert "not a real-person identity simulation" in answer_style_contract
    assert "distinct author-inspired virtual\nadvisor" in answer_style_contract
    assert "not a neutral expert voice" in answer_style_contract
    assert "GuruLightCompanyContext" in company_contract
    assert "not a thesis and cannot be used as evidence" in company_contract
    assert "sealed question ID" in company_contract
    assert "brief_hash" in company_contract
    assert "legacy dynamic plan is a feature-flag rollback path" in company_contract
    assert "krw_guru_query_context" in mcp_policy
    assert "krw_guru_company_brief" in mcp_policy
    assert "exactly one company-specific key question" in mcp_policy
    assert "exactly_one_key_question_required" in mcp_policy
    assert "company_evidence_researcher" in mcp_policy
    assert "does not call `krw_ontology_verify_evidence`" in mcp_policy
    assert "krw-guru-company-research-context/v1" in mcp_policy
    assert "krw_guru_review_company_evidence" in mcp_policy
    assert "main Guru does not call the\nKRW filing tools directly" in mcp_policy
    assert "philosophy_context" in research_pack_contract
    assert "must not add a remembered\nprinciple" in research_pack_contract
    assert "private agent_analysis" in research_pack_contract
    assert "must not be rendered as the\nreal author speaking" in research_pack_contract
    assert "The application owns selection" in normalized_skill_contract
    assert "must not route to another author" in normalized_skill_contract
    assert "It must not use a generic company checklist" in normalized_skill_contract
    assert "facts tied to validated evidence" in normalized_skill_contract
    assert "strongest supported reading" in normalized_skill_contract
    assert "decision_role" in normalized_skill_contract
    assert "English-first" in guru_brief_policy
    assert "does not create a philosophy" in guru_brief_policy

    assert not (SKILLS_DIR / "krw-guru-advisor" / "SKILL.md").exists()
    assert GURU_SKILL_MAP == GURU_SKILLS
    for author_key, skill_name in GURU_SKILLS.items():
        skill_text = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        agent_text = (SKILLS_DIR / skill_name / "agents" / "openai.yaml").read_text()
        local_style_text = (SKILLS_DIR / skill_name / "references" / "answer-style.md").read_text()
        normalized_skill_text = " ".join(skill_text.split())

        assert guru_skill_name(author_key) == skill_name
        assert f"name: {skill_name}" in skill_text
        assert f"Fixed `author_key`: `{author_key}`." in skill_text
        assert "This is not a router" in skill_text
        assert "must not hard-code" in skill_text
        assert "User-facing output is final-answer only" in skill_text
        assert "not a generic analyst" in normalized_skill_text
        assert "first-person simulation" in skill_text
        assert "light company context" in skill_text
        assert "investigation_questions" in skill_text
        assert "exactly one philosophy-shaped company key question" in skill_text
        assert "one to three complementary atomic SearchPlan v2 clauses" in skill_text
        assert "investigation_brief" in skill_text
        assert "company_evidence_researcher" in skill_text
        assert "does not call krw_ontology_verify_evidence or create a pack" in skill_text
        assert "krw-guru-company-research-context/v1" in skill_text
        assert "agent_analysis" in skill_text
        assert "Internal quantitative reasoning is allowed" in skill_text
        assert "documented historical episode or prior cycle" in skill_text
        assert "references/answer-style.md" in skill_text
        assert "answer-style-adapters.md" in skill_text
        assert "krw_guru_query_context" in skill_text
        assert "krw_guru_company_brief" in skill_text
        assert "krw_guru_review_company_evidence" in skill_text
        assert "does not call krw_ontology_* tools directly" in skill_text
        assert "Read before answering" not in skill_text
        assert "Reference files for maintainers/debug only" in skill_text
        assert f'author_keys=["{author_key}"]' in skill_text
        assert "ResearchPack" in skill_text
        assert "Use only the returned Guru ResearchPack" in agent_text
        assert "Work silently" in agent_text
        assert "return final Korean investor-facing prose only" in agent_text
        assert "exactly one philosophy-shaped company key question" in agent_text
        assert "company_evidence_researcher" in agent_text
        assert "krw_guru_company_brief" in agent_text
        assert "krw_guru_review_company_evidence" in agent_text
        assert "not a generic analyst summary" in agent_text
        assert "first-person simulation" in agent_text
        assert "runtime builds company research context" in agent_text
        assert "documented historical episode or prior cycle" in agent_text
        assert "Do not expose process narration or internal tool, brief, payload, retry, or status terms" in agent_text
        assert f"Fixed `author_key`: `{author_key}`." in local_style_text
        assert "It controls readability only" in local_style_text
        assert "controls readability only" in local_style_text
        assert "Do not write in the author's first" in local_style_text
        assert "imply identity" in local_style_text
        assert "only material validated quantitative evidence" in local_style_text

    with pytest.raises(ValueError, match="Unknown guru author_key"):
        guru_skill_name("unknown")


def test_guru_advisor_is_not_registered_in_existing_router() -> None:
    router_text = Path("plugins/krw-ontology/skills/krw-ontology-router/SKILL.md").read_text()
    router_agent_text = Path(
        "plugins/krw-ontology/skills/krw-ontology-router/agents/openai.yaml"
    ).read_text()

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
    existing_codex = json.loads(Path("plugins/krw-ontology/.codex-plugin/plugin.json").read_text())
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
