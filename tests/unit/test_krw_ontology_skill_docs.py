"""Tests for KRW ontology skill research protocols."""

from __future__ import annotations

from pathlib import Path


def test_krw_ontology_skill_documents_scenario_protocols():
    skill_dir = Path("plugins/krw-ontology/skills/krw-ontology-research")
    skill_text = (skill_dir / "SKILL.md").read_text()
    tools_text = (skill_dir / "references" / "tools.md").read_text()
    artifact_text = (skill_dir / "references" / "artifact-contract.md").read_text()

    assert "## Scenario And Sensitivity Questions" in skill_text
    assert "revenue, cost of revenue, operating margin, cash flow, and liquidity" in skill_text
    assert "Search separately for explicit threshold terms" in skill_text
    assert "Question Type Protocols" in skill_text
    assert "## Web Artifact Contract" in skill_text
    assert "references/artifact-contract.md" in skill_text
    assert "Do not assume one query is enough for scenario questions" in tools_text
    assert "coverage warnings" in tools_text
    assert "Store the structure that can regenerate HTML" in artifact_text
    assert "Do not output React components from the agent" in artifact_text
