"""Regression tests for krw_ontology.schema.objects invariants."""

import inspect
from pathlib import Path


def test_ranking_score_field_is_removed_from_all_models():
    from krw_ontology.schema import objects as schema_objects

    source = inspect.getsource(schema_objects)
    assert "ranking_score" not in source, (
        "ranking_score is write-only at serving; ranking uses "
        "specificity_score/boilerplate_score/materiality_hint (audit 2026-08-30)"
    )


def test_retired_risk_driver_headwind_stage_is_absent():
    stage_path = (
        Path(__file__).parents[2]
        / "src"
        / "krw_ontology"
        / "pipeline"
        / "stages"
        / "extract_risks_drivers_headwinds.py"
    )
    assert not stage_path.exists()
    from krw_ontology.pipeline.orchestrator import PIPELINE_STAGES

    assert "extract_risks_drivers_headwinds" not in PIPELINE_STAGES
