from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.benchmark_agent_sdk_plugin import (
    GOLD_CONTRACT,
    GOLD_FORMAT,
    BenchmarkInputError,
    _evaluate_case,
    _prepare_plugin_copy,
    _release_binding,
    _safe_error,
    _summary,
    _validate_gold,
)


def _case() -> dict[str, object]:
    return {
        "id": "apple-regulation",
        "user_question_ko": "앱스토어 규제 위험을 설명해줘.",
        "expected_tickers": ["AAPL"],
        "expected_company_aliases": ["Apple", "애플"],
        "review_focus": ["규제"],
    }


def _gold() -> dict[str, object]:
    return {
        "format": GOLD_FORMAT,
        "contract": GOLD_CONTRACT,
        "source_release": {
            "release_id": "old-release",
            "source_manifest_hash": "sha256:source",
        },
        "cases": [_case()],
    }


def test_validate_gold_normalizes_tickers_and_allows_new_candidate_release_id() -> None:
    payload = _gold()
    payload["cases"][0]["expected_tickers"] = ["aapl"]  # type: ignore[index]

    validated = _validate_gold(payload)

    assert validated["cases"][0]["expected_tickers"] == ["AAPL"]
    assert validated["source_release"]["release_id"] == "old-release"


def test_validate_gold_rejects_duplicate_case_id() -> None:
    payload = _gold()
    payload["cases"] = [_case(), _case()]

    with pytest.raises(BenchmarkInputError, match="invalid id"):
        _validate_gold(payload)


def test_release_binding_requires_source_hash_but_not_same_release_id(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps({"release_id": "new-candidate"}), encoding="utf-8"
    )
    (root / "source_manifest.json").write_text(
        json.dumps({"manifest_hash": "sha256:source"}), encoding="utf-8"
    )
    gold = _validate_gold(_gold())

    binding = _release_binding(root, gold)

    assert binding["release_id"] == "new-candidate"
    assert binding["source_manifest_hash"] == "sha256:source"


def test_prepare_plugin_copy_changes_only_mcp_configuration(tmp_path: Path) -> None:
    plugin = tmp_path / "source-plugin"
    (plugin / "skills" / "research").mkdir(parents=True)
    (plugin / "skills" / "research" / "SKILL.md").write_text("unchanged", encoding="utf-8")
    (plugin / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "krw-ontology": {
                        "command": "uv",
                        "args": ["run", "krw-ontology-mcp-stdio"],
                        "env": {"KRW_ONTOLOGY_ENV": "prod"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    release = tmp_path / "release"
    release.mkdir()
    destination = tmp_path / "ephemeral-plugin"

    report = _prepare_plugin_copy(plugin, release, destination)

    assert report["ephemeral_non_mcp_files_identical"] is True
    assert (destination / "skills" / "research" / "SKILL.md").read_text() == "unchanged"
    mcp = json.loads((destination / ".mcp.json").read_text())
    env = mcp["mcpServers"]["krw-ontology"]["env"]
    assert env["KRW_ONTOLOGY_RELEASE_ROOT"] == str(release.resolve())
    assert env["KRW_ONTOLOGY_GLOBAL_SPINE_PATH"].endswith("indexes/global_spine.sqlite")


def test_evaluate_case_requires_grounded_tool_and_hides_internal_terms() -> None:
    case = _case()
    good = {
        "status": "ok",
        "answer": "애플은 앱스토어 규제 변화가 판매와 매출에 영향을 줄 수 있다고 공시했습니다. "
        "실제 영향은 규제 범위와 시행 방식에 따라 달라질 수 있습니다.\n\n이어서 볼 질문",
        "tool_names": ["mcp__krw-ontology__krw_ontology_query_context"],
    }

    passed = _evaluate_case(case, good)
    leaked = _evaluate_case(case, {**good, "answer": good["answer"] + " SQLite router"})
    ungrounded = _evaluate_case(case, {**good, "tool_names": []})

    assert passed["passed"] is True
    assert leaked["passed"] is False
    assert ungrounded["passed"] is False


def test_summary_counts_passes_and_reports_latency() -> None:
    records = [
        {
            "worker": {"wall_duration_ms": 100.0},
            "evaluation": {"passed": True, "ontology_tool_names": ["a"], "answer_chars": 100},
        },
        {
            "worker": {"wall_duration_ms": 300.0},
            "evaluation": {"passed": False, "ontology_tool_names": [], "answer_chars": 0},
        },
    ]

    summary = _summary(records)

    assert summary["pass_rate"] == 0.5
    assert summary["latency_ms"] == {"p50": 100.0, "p95": 300.0}


def test_summary_uses_hard_timeout_wall_duration() -> None:
    summary = _summary(
        [
            {
                "worker": {"wall_duration_ms": 180_000.0},
                "evaluation": {
                    "passed": False,
                    "ontology_tool_names": [],
                    "answer_chars": 0,
                },
            }
        ]
    )

    assert summary["latency_ms"] == {"p50": 180_000.0, "p95": 180_000.0}


def test_safe_error_redacts_auth_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "secret-token")

    assert "secret-token" not in _safe_error("failure secret-token")
