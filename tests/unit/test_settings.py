"""Tests for pipeline configuration loading."""

from __future__ import annotations

from krw_ontology.config.settings import PipelineConfig


def test_fail_on_section_quality_env_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRW_FAIL_ON_SECTION_QUALITY", "true")

    config = PipelineConfig.load()

    assert config.fail_on_section_quality is True


def test_invalid_fail_on_section_quality_env_keeps_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRW_FAIL_ON_SECTION_QUALITY", "maybe")

    config = PipelineConfig.load()

    assert config.fail_on_section_quality is False


def test_span_pruning_defaults_to_conservative(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    config = PipelineConfig.load()

    assert config.span_pruning == "conservative"


def test_span_pruning_env_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRW_SPAN_PRUNING", "off")

    config = PipelineConfig.load()

    assert config.span_pruning == "off"


def test_invalid_span_pruning_env_keeps_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRW_SPAN_PRUNING", "aggressive")

    config = PipelineConfig.load()

    assert config.span_pruning == "conservative"


def test_batch_size_defaults_are_tuned_for_glm_51_throughput(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    config = PipelineConfig.load()

    assert config.batch_sizes["quote_extraction"] == 10
    assert config.batch_sizes["claim_extraction"] == 12
    assert config.batch_size_for_stage("extract_evidence_quotes", default=5) == 10
    assert config.batch_size_for_stage("extract_research_claims", default=8) == 12


def test_batch_size_for_stage_uses_safe_positive_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    config = PipelineConfig(batch_sizes={"quote_extraction": 0})

    assert config.batch_size_for_stage("extract_evidence_quotes", default=5) == 1
    assert config.batch_size_for_stage("unknown_stage", default=7) == 7


def test_batch_size_env_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRW_BATCH_SIZE_QUOTE_EXTRACTION", "9")
    monkeypatch.setenv("KRW_BATCH_SIZE_CLAIM_EXTRACTION", "11")

    config = PipelineConfig.load()

    assert config.batch_size_for_stage("extract_evidence_quotes", default=5) == 9
    assert config.batch_size_for_stage("extract_research_claims", default=8) == 11
