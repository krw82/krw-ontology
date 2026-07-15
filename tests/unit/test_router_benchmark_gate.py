from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from krw_ontology.agent_index.router_sidecar import RouterSidecar, build_router_sidecar
from krw_ontology.agent_index.spine_schema import (
    verify_global_spine_schema,
    write_spine_verification_seal,
)
import scripts.benchmark_router_sidecar as benchmark_module
from scripts.benchmark_router_sidecar import (
    BASELINE_ACCEPTANCE_FORMAT,
    BENCHMARK_FORMAT,
    BUDGET_FORMAT,
    MEASUREMENT_PROTOCOL,
    PLANNED_GOLD_CONTRACT,
    PLANNED_GOLD_FORMAT,
    QUERY_GOLD_CONTRACT,
    QUERY_GOLD_FORMAT,
    BenchmarkSchemaError,
    _assert_candidate_parity,
    _benchmark_summary,
    _environment,
    _evaluate_gate,
    _measurement_summary,
    _measure_connection_cold,
    _measure_planned_query_warm,
    _measure_query_warm,
    _planned_joint_metrics,
    _ranking_metrics,
    _read_json,
    _source_binding_report,
    _validate_baseline_report,
    _validate_budget,
    _validate_gold_release,
    _validate_planned_gold,
    _validate_query_gold,
)
from tests.unit.test_router_sidecar import _write_global_spine


REPO_ROOT = Path(__file__).resolve().parents[2]


def _install_synthetic_release(tmp_path: Path) -> tuple[Path, Path]:
    spine_path = _write_global_spine(tmp_path / "release" / "indexes" / "global_spine.sqlite")
    companies_dir = spine_path.parent / "companies"
    companies_dir.mkdir(parents=True, exist_ok=True)
    (companies_dir / "AAPL.sqlite").touch()
    (companies_dir / "MSFT.sqlite").touch()
    (spine_path.parent / "shard_manifest.json").write_text(
        json.dumps(
            {
                "format": "krw-ontology-shard-manifest/v1",
                "release_id": "router-test-release",
                "shards": {
                    "AAPL": {"path": "companies/AAPL.sqlite"},
                    "MSFT": {"path": "companies/MSFT.sqlite"},
                },
            }
        ),
        encoding="utf-8",
    )
    verification = verify_global_spine_schema(spine_path, deep=True, trust_seal=False)
    assert verification["ok"] is True, verification["errors"]
    write_spine_verification_seal(spine_path, verification)
    sidecar_path = build_router_sidecar(spine_path).path
    return spine_path, sidecar_path


def _provenance(ticker: str = "AAPL") -> dict[str, Any]:
    return {
        "ticker": ticker,
        "document_types": ["10-K"],
        "periods": ["FY2025"],
        "source_note": "Synthetic release-backed benchmark fixture.",
    }


def _query_gold_payload(*, expected: str = "AAPL") -> dict[str, Any]:
    return {
        "format": QUERY_GOLD_FORMAT,
        "contract": QUERY_GOLD_CONTRACT,
        "source_release": {
            "release_id": "router-test-release",
            "source_manifest_hash": "source-hash",
        },
        "queries": [
            {
                "id": "apple-ai-capex",
                "query": "Apple AI capex revenue demand",
                "expected_tickers": [expected],
                "distractor_tickers": ["MSFT"] if expected != "MSFT" else ["AAPL"],
                "max_candidate_count": 20,
                "provenance": _provenance(expected),
            }
        ],
    }


def _planned_gold_payload() -> dict[str, Any]:
    return {
        "format": PLANNED_GOLD_FORMAT,
        "contract": PLANNED_GOLD_CONTRACT,
        "source_release": {
            "release_id": "router-test-release",
            "source_manifest_hash": "source-hash",
        },
        "plans": [
            {
                "id": "apple-ai-revenue-plan",
                "expected_tickers": ["AAPL"],
                "distractor_tickers": ["MSFT"],
                "max_candidate_count": 20,
                "provenance": _provenance(),
                "plan": {
                    "question": "Which company has both AI capex and revenue demand?",
                    "intent": "discovery",
                    "universe": "covered",
                    "limit_tickers": 20,
                    "limit_results": 12,
                    "clauses": [
                        {
                            "clause_id": "ai",
                            "retrieval_query": "AI capex demand",
                            "required_concepts": ["AI capex demand"],
                            "required": True,
                        },
                        {
                            "clause_id": "revenue",
                            "retrieval_query": "Revenue demand",
                            "required_concepts": ["Revenue demand"],
                            "required": True,
                        },
                    ],
                },
            }
        ],
    }


def _bootstrap_budget(*, min_gold_queries: int = 1) -> dict[str, Any]:
    return {
        "format": BUDGET_FORMAT,
        "mode": "bootstrap",
        "require_all_queries_gold": True,
        "require_baseline": False,
        "require_candidate_parity": True,
        "require_production_layout": True,
        "require_row_constraints": True,
        "require_source_binding": True,
        "min_gold_queries": min_gold_queries,
        "min_macro_recall_at_k": 1.0,
        "min_macro_ndcg_at_k": 0.0,
        "min_per_query_recall_at_k": 1.0,
        "min_per_query_ndcg_at_k": 0.0,
        "min_macro_hit_at_1": 0.0,
        "min_macro_hit_at_5": 0.0,
        "min_macro_mrr": 0.0,
        "min_expected_before_distractor_rate": 0.0,
        "min_joint_required_clause_coverage": 0.0,
        "max_mean_expected_rank_at_k": 21.0,
        "max_expected_rank_at_k": 21.0,
        "max_candidate_count": 20,
        "max_connection_cold_p95_ms": 10_000.0,
        "max_query_warm_p95_ms": 10_000.0,
    }


def _release_budget(*, min_gold_queries: int = 1) -> dict[str, Any]:
    budget = _bootstrap_budget(min_gold_queries=min_gold_queries)
    budget.update(
        {
            "mode": "release",
            "require_baseline": True,
            "require_matching_environment": True,
            "require_matching_inputs": True,
            "max_macro_recall_drop": 0.0,
            "max_macro_ndcg_drop": 0.0,
            "max_connection_cold_p95_regression_ratio": 10.0,
            "max_query_warm_p95_regression_ratio": 10.0,
            "max_sidecar_size_regression_ratio": 1.0,
        }
    )
    return budget


def test_gold_binding_allows_new_release_id_for_identical_source_manifest() -> None:
    _validate_gold_release(
        {
            "release_id": "gold-provenance-release",
            "source_manifest_hash": "sha256:same-source",
        },
        {
            "release_id": "new-candidate-release",
            "source_manifest_hash": "sha256:same-source",
        },
        label="query gold",
    )

    with pytest.raises(BenchmarkSchemaError, match="source_manifest_hash mismatch"):
        _validate_gold_release(
            {
                "release_id": "gold-provenance-release",
                "source_manifest_hash": "sha256:old-source",
            },
            {
                "release_id": "new-candidate-release",
                "source_manifest_hash": "sha256:new-source",
            },
            label="query gold",
        )


def _summary(
    *,
    recall: float = 1.0,
    ndcg: float = 0.95,
    inputs: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    combined = {
        "query_count": 10,
        "gold_query_count": 10,
        "candidate_parity": True,
        "quality": {
            "macro_recall_at_k": recall,
            "macro_ndcg_at_k": ndcg,
            "minimum_recall_at_k": recall,
            "minimum_ndcg_at_k": ndcg,
            "macro_hit_at_1": recall,
            "macro_hit_at_5": recall,
            "macro_mrr": ndcg,
            "mean_expected_rank_at_k": 1.0,
            "maximum_expected_rank_at_k": 1.0,
            "maximum_candidate_count": 5,
            "total_distractor_hits_at_k": 0.0,
            "minimum_row_constraints_passed": 1.0,
        },
        "latency": {
            "connection_cold": {"p95_ms": 100.0},
            "query_warm": {"p95_ms": 20.0},
        },
    }
    planned = deepcopy(combined)
    planned["quality"]["minimum_joint_required_clause_coverage"] = 1.0
    return {
        "queries": combined,
        "planned_queries": planned,
        "combined": combined,
        "_inputs": inputs or {"protocol": "v2"},
        "_environment": environment or {"cpu_model": "test-cpu"},
    }


def _accepted_baseline_report() -> dict[str, Any]:
    return {
        "format": BENCHMARK_FORMAT,
        "measurement_protocol": dict(MEASUREMENT_PROTOCOL),
        "global_spine": "/tmp/global_spine.sqlite",
        "sidecar": "/tmp/router_sidecar.sqlite",
        "limit": 20,
        "iterations": 7,
        "cold_iterations": 3,
        "inputs": {"compatibility": {"protocol": "v2"}},
        "artifacts": {"sidecar_size_bytes": 1_000},
        "environment": {"cpu_model": "test-cpu"},
        "source_binding": {"ok": True, "production_layout": True},
        "ranking_profile": {},
        "queries": [],
        "planned_queries": [],
        "ablations": {},
        "summary": _summary(),
        "gate": {
            "passed": True,
            "budget": _bootstrap_budget(),
            "budget_sha256": "abc123",
        },
        "baseline_acceptance": {
            "format": BASELINE_ACCEPTANCE_FORMAT,
            "requested": True,
            "accepted": True,
            "budget_mode": "bootstrap",
            "budget_sha256": "abc123",
        },
    }


def _write_cli_inputs(
    tmp_path: Path,
    *,
    expected: str = "AAPL",
    include_plan: bool = False,
) -> tuple[Path, Path | None, Path]:
    queries_path = tmp_path / "queries.json"
    queries_path.write_text(json.dumps(_query_gold_payload(expected=expected)), encoding="utf-8")
    planned_path: Path | None = None
    if include_plan:
        planned_path = tmp_path / "planned.json"
        planned_path.write_text(json.dumps(_planned_gold_payload()), encoding="utf-8")
    budget_path = tmp_path / "budget.json"
    budget_path.write_text(
        json.dumps(_bootstrap_budget(min_gold_queries=2 if include_plan else 1)),
        encoding="utf-8",
    )
    return queries_path, planned_path, budget_path


def _run_cli(
    *,
    spine_path: Path,
    sidecar_path: Path,
    queries_path: Path,
    budget_path: Path | None,
    output_path: Path | None = None,
    baseline_path: Path | None = None,
    comparison_baseline_path: Path | None = None,
    planned_path: Path | None = None,
    include_ablations: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "scripts/benchmark_router_sidecar.py",
        "--global-spine",
        str(spine_path),
        "--sidecar",
        str(sidecar_path),
        "--queries",
        str(queries_path),
        "--iterations",
        "2",
        "--cold-iterations",
        "1",
    ]
    if budget_path:
        command.extend(["--budget", str(budget_path)])
    if output_path:
        command.extend(["--output", str(output_path)])
    if baseline_path:
        command.extend(["--accept-baseline", str(baseline_path)])
    if comparison_baseline_path:
        command.extend(["--baseline", str(comparison_baseline_path)])
    if planned_path:
        command.extend(["--planned-queries", str(planned_path)])
    if include_ablations:
        command.append("--include-ablations")
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_ranking_metrics_deduplicate_and_measure_distractor_order() -> None:
    metrics = _ranking_metrics(
        ["AAPL", "AAPL", "MSFT"],
        ["AAPL"],
        distractors=["MSFT"],
        evaluation_limit=5,
    )

    assert metrics is not None
    assert metrics["candidate_count"] == 2
    assert metrics["ndcg_at_k"] == 1.0
    assert metrics["expected_rank"] == 1
    assert metrics["hit_at_1"] == 1.0
    assert metrics["hit_at_5"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["best_distractor_rank"] == 2
    assert metrics["expected_before_distractors"] == 1.0


def test_measurement_summary_uses_explicit_connection_cold_and_query_warm() -> None:
    summary = _measurement_summary(
        connection_cold_values=[90.0, 110.0],
        query_warm_values=[10.0, 20.0, 15.0],
    )

    assert summary["connection_cold"]["p50_ms"] == 100.0
    assert summary["query_warm"]["p50_ms"] == 15.0
    assert summary["query_warm"]["untimed_warmup_count"] == 1


def test_query_warm_uses_one_new_connection_and_one_untimed_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSidecar:
        instances: list[FakeSidecar] = []

        def __init__(self, _path: Path):
            self.calls = 0
            self._profile: dict[str, Any] = {}
            self.instances.append(self)

        def __enter__(self) -> FakeSidecar:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(benchmark_module, "RouterSidecar", FakeSidecar)

    def query(sidecar: FakeSidecar) -> list[str]:
        sidecar.calls += 1
        return ["AAPL"]

    candidates, durations = _measure_query_warm(
        Path("unused.sqlite"),
        iterations=3,
        ranking_profile={"profile_id": "test"},
        fn=query,
    )

    assert candidates == ["AAPL"]
    assert len(durations) == 3
    assert len(FakeSidecar.instances) == 1
    assert FakeSidecar.instances[0].calls == 4


def test_connection_cold_uses_a_fresh_connection_per_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSidecar:
        instances: list[FakeSidecar] = []

        def __init__(self, _path: Path):
            self.calls = 0
            self._profile: dict[str, Any] = {}
            self.instances.append(self)

        def __enter__(self) -> FakeSidecar:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(benchmark_module, "RouterSidecar", FakeSidecar)

    def query(sidecar: FakeSidecar) -> list[str]:
        sidecar.calls += 1
        return ["AAPL"]

    candidates, durations = _measure_connection_cold(
        Path("unused.sqlite"),
        iterations=3,
        ranking_profile={"profile_id": "test"},
        fn=query,
    )

    assert candidates == ["AAPL"]
    assert len(durations) == 3
    assert len(FakeSidecar.instances) == 3
    assert [instance.calls for instance in FakeSidecar.instances] == [1, 1, 1]


def test_candidate_parity_rejects_ranking_drift() -> None:
    with pytest.raises(AssertionError, match="candidate parity failed"):
        _assert_candidate_parity("fixture", [["AAPL", "MSFT"], ["MSFT", "AAPL"]])


def test_benchmark_summary_aggregates_rank_distractor_joint_and_latency() -> None:
    rows = [
        {
            "sidecar": {
                "candidate_parity": True,
                "quality": {
                    "recall_at_k": 1.0,
                    "ndcg_at_k": 1.0,
                    "hit_at_1": 1.0,
                    "hit_at_5": 1.0,
                    "mrr": 1.0,
                    "expected_rank_penalized": 1,
                    "candidate_count": 3,
                    "distractor_count_at_k": 1,
                    "expected_before_distractors": 1.0,
                    "row_constraints_passed": 1.0,
                    "joint_required_clause_coverage": 1.0,
                },
            }
        },
        {
            "sidecar": {
                "candidate_parity": True,
                "quality": {
                    "recall_at_k": 1.0,
                    "ndcg_at_k": 0.5,
                    "hit_at_1": 0.0,
                    "hit_at_5": 1.0,
                    "mrr": 0.5,
                    "expected_rank_penalized": 2,
                    "candidate_count": 5,
                    "distractor_count_at_k": 0,
                    "expected_before_distractors": 1.0,
                    "row_constraints_passed": 1.0,
                    "joint_required_clause_coverage": 0.5,
                },
            }
        },
    ]

    summary = _benchmark_summary(
        rows,
        [[10.0, 20.0], [30.0, 40.0]],
        [[100.0], [80.0]],
    )

    assert summary["quality"]["macro_hit_at_1"] == 0.5
    assert summary["quality"]["macro_hit_at_5"] == 1.0
    assert summary["quality"]["macro_mrr"] == 0.75
    assert summary["quality"]["mean_expected_rank_at_k"] == 1.5
    assert summary["quality"]["maximum_candidate_count"] == 5
    assert summary["quality"]["total_distractor_hits_at_k"] == 1
    assert summary["quality"]["minimum_joint_required_clause_coverage"] == 0.5
    assert summary["latency"]["connection_cold"]["p95_ms"] == 100.0
    assert summary["latency"]["query_warm"]["p95_ms"] == 40.0


def test_budget_schema_rejects_unknown_keys_and_invalid_modes() -> None:
    unknown = _bootstrap_budget()
    unknown["max_warm_p59_ms"] = 10
    with pytest.raises(BenchmarkSchemaError, match="unknown keys"):
        _validate_budget(unknown)

    invalid_release = _bootstrap_budget()
    invalid_release["mode"] = "release"
    with pytest.raises(BenchmarkSchemaError, match="require_baseline=true"):
        _validate_budget(invalid_release)


def test_query_gold_rejects_raw_korean_user_language() -> None:
    payload = _query_gold_payload()
    payload["queries"][0]["query"] = "애플 앱스토어 규제 위험"

    with pytest.raises(BenchmarkSchemaError, match="agent-to-plan E2E"):
        _validate_query_gold(payload)


def test_checked_in_router_contracts_are_strict_and_versioned() -> None:
    queries, query_source = _validate_query_gold(
        _read_json(REPO_ROOT / "benchmarks" / "router_gold_v2.json")
    )
    plans, planned_source = _validate_planned_gold(
        _read_json(REPO_ROOT / "benchmarks" / "router_planned_gold_v2.json")
    )
    bootstrap = _validate_budget(
        _read_json(REPO_ROOT / "benchmarks" / "router_bootstrap_budget_v2.json")
    )
    release = _validate_budget(
        _read_json(REPO_ROOT / "benchmarks" / "router_release_budget_v2.json")
    )
    korean_e2e = _read_json(REPO_ROOT / "benchmarks" / "router_agent_plan_e2e_ko_v1.json")

    assert len(queries) == 20
    assert len(plans) == 5
    assert query_source == planned_source
    assert bootstrap["mode"] == "bootstrap"
    assert release["mode"] == "release"
    assert korean_e2e["contract"] == ("raw-korean-user-question-to-agent-authored-search-plan")


def test_joint_coverage_requires_one_expected_ticker_across_all_required_clauses() -> None:
    diagnostics = {
        "queries": [
            {
                "clause_id": "first",
                "required": True,
                "resolved_tickers": ["AAPL"],
            },
            {
                "clause_id": "second",
                "required": True,
                "resolved_tickers": ["MSFT"],
            },
        ]
    }

    joint = _planned_joint_metrics(diagnostics, ["AAPL", "MSFT"])

    assert joint["joint_required_clause_coverage"] == 0.5
    assert joint["joint_expected_tickers"] == []


def test_baseline_schema_rejects_unknown_or_unaccepted_reports() -> None:
    valid = _accepted_baseline_report()
    assert _validate_baseline_report(valid) is valid

    unknown = deepcopy(valid)
    unknown["surprise"] = True
    with pytest.raises(BenchmarkSchemaError, match="unknown keys"):
        _validate_baseline_report(unknown)

    unaccepted = deepcopy(valid)
    unaccepted["baseline_acceptance"]["accepted"] = False
    with pytest.raises(BenchmarkSchemaError, match="not explicitly accepted"):
        _validate_baseline_report(unaccepted)

    malformed = deepcopy(valid)
    malformed["summary"] = "not-an-object"
    with pytest.raises(BenchmarkSchemaError, match="baseline summary must be a JSON object"):
        _validate_baseline_report(malformed)


def test_gate_missing_baseline_metrics_and_artifacts_fail_instead_of_skipping() -> None:
    budget = {
        "require_baseline": True,
        "max_macro_recall_drop": 0.0,
        "max_macro_ndcg_drop": 0.0,
        "max_connection_cold_p95_regression_ratio": 1.2,
        "max_query_warm_p95_regression_ratio": 1.2,
        "max_sidecar_size_regression_ratio": 1.2,
    }
    gate = _evaluate_gate(
        summary=_summary(),
        artifacts={"sidecar_size_bytes": 1_000},
        source_binding={"ok": True, "production_layout": True},
        budget=budget,
        baseline={"summary": {}, "artifacts": "invalid"},
    )

    assert gate["passed"] is False
    assert {failure["name"] for failure in gate["failures"]} == {
        "macro_recall_drop",
        "macro_ndcg_drop",
        "connection_cold_p95_regression_ratio",
        "query_warm_p95_regression_ratio",
        "sidecar_size_regression_ratio",
    }


def test_gate_environment_compatibility_includes_cpu_model() -> None:
    current_environment = {"cpu_model": "Apple M4 Max", "logical_cpu_count": 16}
    baseline_environment = {"cpu_model": "Apple M3 Max", "logical_cpu_count": 16}
    gate = _evaluate_gate(
        summary=_summary(environment=current_environment),
        artifacts={"sidecar_size_bytes": 1_000},
        source_binding={"ok": True, "production_layout": True},
        budget={"require_baseline": True, "require_matching_environment": True},
        baseline={
            "summary": _summary(environment=baseline_environment),
            "artifacts": {"sidecar_size_bytes": 1_000},
        },
    )

    assert gate["passed"] is False
    assert gate["failures"][0]["name"] == "accepted_baseline_environment_matches"
    assert _environment()["cpu_model"]


def test_gate_rejects_baseline_with_different_input_fingerprint() -> None:
    gate = _evaluate_gate(
        summary=_summary(inputs={"queries_sha256": "current"}),
        artifacts={"sidecar_size_bytes": 1_000},
        source_binding={"ok": True, "production_layout": True},
        budget={"require_baseline": True, "require_matching_inputs": True},
        baseline={
            "summary": _summary(inputs={"queries_sha256": "baseline"}),
            "artifacts": {"sidecar_size_bytes": 1_000},
        },
    )

    assert gate["passed"] is False
    assert gate["failures"][0]["name"] == "accepted_baseline_inputs_match"


def test_source_binding_uses_metadata_without_rehash_and_detects_mismatch(
    tmp_path: Path,
) -> None:
    spine_path, sidecar_path = _install_synthetic_release(tmp_path)
    binding = _source_binding_report(spine_path, sidecar_path, expected_global_spine_sha256=None)
    assert binding["ok"] is True
    assert binding["verification_mode"] == "light-sealed-sha256-no-rehash"
    assert binding["sealed_global_spine_sha256"]
    assert binding["production_layout"] is True

    with sqlite3.connect(spine_path) as conn:
        conn.execute(
            "UPDATE metadata SET value_json = ? WHERE key = 'release_id'",
            (json.dumps("other-release"),),
        )
    mismatched = _source_binding_report(spine_path, sidecar_path, expected_global_spine_sha256=None)
    assert mismatched["ok"] is False
    assert "router_sidecar_release_id_mismatch" in mismatched["errors"]


def test_planned_benchmark_calls_production_router_and_measures_joint_coverage(
    tmp_path: Path,
) -> None:
    spine_path, sidecar_path = _install_synthetic_release(tmp_path)
    rows, _source = _validate_planned_gold(_planned_gold_payload())
    row, plan = rows[0]
    with RouterSidecar(sidecar_path) as sidecar:
        profile = dict(sidecar.ranking_profile)

    candidates, durations, diagnostics = _measure_planned_query_warm(
        spine_path,
        sidecar_path,
        plan=plan,
        limit=20,
        iterations=2,
        ranking_profile=profile,
    )
    joint = _planned_joint_metrics(diagnostics, row["expected_tickers"])

    assert candidates == ["AAPL"]
    assert len(durations) == 2
    assert diagnostics["mode"] == "planned_router_sidecar"
    assert joint["joint_required_clause_coverage"] == 1.0
    assert joint["joint_expected_tickers"] == ["AAPL"]


def test_cli_accepts_only_a_passing_absolute_baseline_and_reports_legacy_ablation(
    tmp_path: Path,
) -> None:
    spine_path, sidecar_path = _install_synthetic_release(tmp_path)
    queries_path, planned_path, budget_path = _write_cli_inputs(tmp_path, include_plan=True)
    report_path = tmp_path / "report.json"
    baseline_path = tmp_path / "accepted-baseline.json"

    completed = _run_cli(
        spine_path=spine_path,
        sidecar_path=sidecar_path,
        queries_path=queries_path,
        planned_path=planned_path,
        budget_path=budget_path,
        output_path=report_path,
        baseline_path=baseline_path,
        include_ablations=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert report["format"] == BENCHMARK_FORMAT
    assert report["baseline_acceptance"]["accepted"] is True
    assert (
        report["summary"]["planned_queries"]["quality"]["minimum_joint_required_clause_coverage"]
        == 1.0
    )
    assert report["ablations"]["legacy_primary_scale"]["primary_rrf_scale"] == 61.0
    assert (
        report["ablations"]["legacy_primary_scale"]["summary"]["planned_queries"]["query_count"]
        == 1
    )
    assert _validate_baseline_report(baseline) is baseline


def test_cli_failed_gate_writes_report_exits_two_and_does_not_accept_baseline(
    tmp_path: Path,
) -> None:
    spine_path, sidecar_path = _install_synthetic_release(tmp_path)
    queries_path, _planned_path, budget_path = _write_cli_inputs(tmp_path, expected="MSFT")
    report_path = tmp_path / "failed-report.json"
    baseline_path = tmp_path / "must-not-exist.json"

    completed = _run_cli(
        spine_path=spine_path,
        sidecar_path=sidecar_path,
        queries_path=queries_path,
        budget_path=budget_path,
        output_path=report_path,
        baseline_path=baseline_path,
    )

    assert completed.returncode == 2
    assert report_path.is_file()
    assert not baseline_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["gate"]["passed"] is False
    assert report["baseline_acceptance"]["accepted"] is False
    assert "refusing to accept" in completed.stderr


def test_cli_release_gate_consumes_a_strict_compatible_baseline(tmp_path: Path) -> None:
    spine_path, sidecar_path = _install_synthetic_release(tmp_path)
    queries_path, planned_path, bootstrap_budget_path = _write_cli_inputs(
        tmp_path, include_plan=True
    )
    baseline_path = tmp_path / "accepted-baseline.json"
    accepted = _run_cli(
        spine_path=spine_path,
        sidecar_path=sidecar_path,
        queries_path=queries_path,
        planned_path=planned_path,
        budget_path=bootstrap_budget_path,
        baseline_path=baseline_path,
    )
    assert accepted.returncode == 0, accepted.stderr

    release_budget_path = tmp_path / "release-budget.json"
    release_budget_path.write_text(
        json.dumps(_release_budget(min_gold_queries=2)), encoding="utf-8"
    )
    report_path = tmp_path / "candidate-report.json"
    candidate = _run_cli(
        spine_path=spine_path,
        sidecar_path=sidecar_path,
        queries_path=queries_path,
        planned_path=planned_path,
        budget_path=release_budget_path,
        output_path=report_path,
        comparison_baseline_path=baseline_path,
    )

    assert candidate.returncode == 0, candidate.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["gate"]["passed"] is True
    assert report["gate"]["baseline_used"] is True
    passed_checks = {check["name"] for check in report["gate"]["checks"] if check["passed"]}
    assert "accepted_baseline_inputs_match" in passed_checks
    assert "accepted_baseline_environment_matches" in passed_checks


def test_cli_rejects_baseline_acceptance_without_an_absolute_budget(
    tmp_path: Path,
) -> None:
    spine_path, sidecar_path = _install_synthetic_release(tmp_path)
    queries_path, _planned_path, _budget_path = _write_cli_inputs(tmp_path)
    baseline_path = tmp_path / "must-not-exist.json"

    completed = _run_cli(
        spine_path=spine_path,
        sidecar_path=sidecar_path,
        queries_path=queries_path,
        budget_path=None,
        baseline_path=baseline_path,
    )

    assert completed.returncode == 2
    assert not baseline_path.exists()
    assert "--accept-baseline requires --budget" in completed.stderr
