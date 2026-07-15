from __future__ import annotations

from scripts.benchmark_mcp_candidate import (
    _contains_text,
    _has_error,
    _model_payload_bytes,
    _percentile,
)
from scripts.qualify_release_candidate import QUALIFICATION_FORMAT


def test_mcp_candidate_helpers_find_nested_ticker_and_identity() -> None:
    payload = {
        "root": {"ticker": "AAPL", "object_id": "research_claim:aapl:1"},
        "nodes": [{"label": "App Store risk"}],
    }

    assert _contains_text(payload, "aapl") is True
    assert _contains_text(payload, "research_claim:aapl:1") is True
    assert _contains_text(payload, "NVDA") is False


def test_mcp_candidate_error_detection_is_conservative() -> None:
    assert _has_error({"ok": False}) is True
    assert _has_error({"error": {"code": "not_found"}}) is True
    assert _has_error({"ok": True, "nodes": []}) is False


def test_mcp_candidate_percentile_uses_observed_sample() -> None:
    assert _percentile([100.0, 200.0, 300.0], 0.5) == 200.0
    assert _percentile([100.0, 200.0, 300.0], 0.95) == 300.0
    assert _percentile([], 0.95) is None


def test_mcp_candidate_model_payload_bytes_use_compact_utf8_json() -> None:
    payload = {"ticker": "삼성", "paths": [{"id": "a"}, {"id": "b"}]}

    assert _model_payload_bytes(payload) == len(
        '{"paths":[{"id":"a"},{"id":"b"}],"ticker":"삼성"}'.encode("utf-8")
    )


def test_qualification_format_is_versioned() -> None:
    assert QUALIFICATION_FORMAT == "krw-ontology-release-qualification/v1"
