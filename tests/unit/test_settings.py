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
