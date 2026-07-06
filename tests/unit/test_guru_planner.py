from __future__ import annotations

from pathlib import Path

from krw_ontology.guru.planner import build_collection_plan, build_source_manifest


def test_collection_plan_is_independent_from_company_ontology(tmp_path: Path):
    running_root = tmp_path / "guru-running"
    plan = build_collection_plan(tmp_path, running_root=running_root)

    assert plan.collection_started is False
    assert plan.extraction_started is False
    assert plan.ticker_required is False
    assert plan.existing_ontology_schema_changed is False
    assert plan.mcp_changed is False
    assert plan.skills_changed is False
    assert plan.author_keys == ["buffett", "marks", "ackman", "flatt", "terry_smith"]
    assert plan.workspace["root"] == str(tmp_path)
    assert plan.workspace["running_root"] == str(running_root)
    assert plan.workspace["raw_dir"] == str(running_root / "raw")
    assert "GuruConcept" in plan.ontology_object_types
    assert "GuruPrinciple" in plan.ontology_object_types
    assert "GuruQuestion" in plan.ontology_object_types
    assert "GuruSearchTarget" in plan.ontology_object_types
    assert "GuruInvestorIntent" in plan.ontology_object_types
    assert "GuruQuestionTemplate" in plan.ontology_object_types
    assert "GuruAnswerPlaybook" in plan.ontology_object_types
    assert "GuruDataNeed" in plan.ontology_object_types
    assert "GuruClarifyingQuestion" in plan.ontology_object_types


def test_collection_plan_maps_existing_ontology_without_extending_schema(tmp_path: Path):
    plan = build_collection_plan(tmp_path, ["buffett", "marks"])

    assert plan.author_keys == ["buffett", "marks"]
    mapping = {item["existing"]: item["guru"] for item in plan.existing_ontology_mapping}
    assert mapping["SourceDocument"] == "GuruSourceDocument"
    assert mapping["SourceSpan"] == "GuruSourceSpan"
    assert mapping["EvidenceQuote"] == "GuruEvidenceExcerpt"
    assert all("no objects.yaml change" in item["boundary"] or "private cache" in item["boundary"] or "not company" in item["boundary"] or "short excerpts" in item["boundary"] or "lens relationship" in item["boundary"] for item in plan.existing_ontology_mapping)


def test_source_manifest_plans_official_indexes_without_collection():
    manifest = build_source_manifest(["ackman", "flatt"])

    assert manifest.collection_started is False
    assert [author.author_key for author in manifest.authors] == ["ackman", "flatt"]
    assert [source.collection_status for source in manifest.planned_sources] == ["planned", "planned"]
    assert manifest.seed_sources == []
    assert manifest.source_policy["raw_collection_started"] is False
    assert manifest.source_policy["full_text_public_storage"] is False


def test_source_manifest_keeps_direct_seed_sources_separate():
    manifest = build_source_manifest(["terry_smith"])

    assert [source.source_id for source in manifest.planned_sources] == [
        "terry_smith:official_index"
    ]
    assert len(manifest.seed_sources) == 10
    assert manifest.seed_sources[0].source_id == "terry_smith:2025-annual-letter"
    assert manifest.seed_sources[-1].source_id == "terry_smith:2021-semiannual-letter"
