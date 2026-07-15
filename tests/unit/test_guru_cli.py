from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from krw_ontology.cli.main import app


runner = CliRunner()


def test_guru_help_is_registered():
    result = runner.invoke(app, ["guru", "--help"])

    assert result.exit_code == 0
    assert "Plan standalone investor-letter ontology collection" in result.output
    assert "run" in result.output
    assert "start" in result.output


def test_guru_run_invokes_pipeline_without_agent_sdk_by_default(
    tmp_path: Path,
    monkeypatch,
):
    captured = {}

    def fake_run_guru_pipeline(root=None, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return {
            "root": str(tmp_path / "guru"),
            "running_root": str(tmp_path / "guru-running"),
            "initialized": True,
            "collection_started": True,
            "extraction_started": False,
            "execution_mode": "dry_run",
            "agent_sdk_called": False,
            "raw_documents": 2,
            "raw_errors": 0,
            "parsed_documents": 1,
            "parsed_errors": 0,
            "extraction_batches": 1,
            "files": {},
        }

    import krw_ontology.guru.cli as guru_cli

    monkeypatch.setattr(guru_cli, "run_guru_pipeline", fake_run_guru_pipeline)

    result = runner.invoke(
        app,
        [
            "guru",
            "run",
            "--root",
            str(tmp_path / "guru"),
            "--running-root",
            str(tmp_path / "guru-running"),
            "--authors",
            "buffett,marks",
            "--limit-per-author",
            "3",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["agent_sdk_called"] is False
    assert captured["root"] == tmp_path / "guru"
    assert captured["running_root"] == tmp_path / "guru-running"
    assert captured["author_keys"] == ["buffett", "marks"]
    assert captured["limit_per_author"] == 3
    assert captured["execute_agent_sdk"] is False


def test_guru_run_without_root_defaults_to_mutable_workspace(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_run_guru_pipeline(root=None, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return {
            "root": str(root),
            "running_root": str(tmp_path / "guru-data" / "runs" / "default"),
            "initialized": True,
            "collection_started": True,
            "extraction_started": False,
            "execution_mode": "dry_run",
            "agent_sdk_called": False,
            "raw_documents": 0,
            "raw_errors": 0,
            "parsed_documents": 0,
            "parsed_errors": 0,
            "extraction_batches": 0,
            "files": {},
        }

    import krw_ontology.guru.cli as guru_cli

    monkeypatch.setenv("KRW_GURU_DATA_ROOT", str(tmp_path / "guru-data"))
    monkeypatch.setattr(guru_cli, "run_guru_pipeline", fake_run_guru_pipeline)

    result = runner.invoke(app, ["guru", "run", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["root"].endswith("guru-data/workspaces/default")
    assert captured["root"] == tmp_path / "guru-data" / "workspaces" / "default"


def test_guru_run_background_starts_detached_worker(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_start_background_guru_run(root=None, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return {
            "status": "running",
            "pid": 12345,
            "root": str(tmp_path / "guru"),
            "running_root": str(tmp_path / "guru-running"),
            "started_at": "2026-07-05T00:00:00+00:00",
            "finished_at": None,
            "command": ["krw-ontology", "guru", "run-worker"],
            "log_path": str(
                tmp_path / "guru-running" / ".krw_pipeline" / "guru" / "logs" / "worker.log"
            ),
            "state_path": str(
                tmp_path / "guru-running" / ".krw_pipeline" / "guru" / "worker_state.json"
            ),
            "execute_agent_sdk": False,
        }

    import krw_ontology.guru.cli as guru_cli

    monkeypatch.setattr(guru_cli, "start_background_guru_run", fake_start_background_guru_run)

    result = runner.invoke(
        app,
        [
            "guru",
            "run",
            "--root",
            str(tmp_path / "guru"),
            "--running-root",
            str(tmp_path / "guru-running"),
            "--background",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["pid"] == 12345
    assert payload["status"] == "running"
    assert captured["root"] == tmp_path / "guru"
    assert captured["running_root"] == tmp_path / "guru-running"
    assert captured["authors"] is None
    assert captured["limit_per_author"] is None
    assert captured["execute_agent_sdk"] is False


def test_guru_start_creates_planning_workspace_without_collection(tmp_path: Path):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    result = runner.invoke(
        app,
        [
            "guru",
            "start",
            "--root",
            str(root),
            "--running-root",
            str(running_root),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["collection_started"] is False
    assert payload["extraction_started"] is False
    assert payload["author_keys"] == ["buffett", "marks", "ackman", "flatt", "terry_smith"]

    assert payload["root"] == str(root)
    assert payload["running_root"] == str(running_root)
    assert (root / "source_manifest.yaml").exists()
    assert (root / "collection_plan.json").exists()
    assert (root / "ontology_manifest.json").exists()
    assert (root / "reviewed").is_dir()
    assert (running_root / "raw").is_dir()
    assert (running_root / "parsed").is_dir()
    assert (running_root / "spans").is_dir()
    assert list((running_root / "raw").iterdir()) == []
    assert not (root / "ontology" / "schema" / "objects.yaml").exists()
    assert not (root / "indexes" / "global_spine.sqlite").exists()

    source_manifest = yaml.safe_load((root / "source_manifest.yaml").read_text(encoding="utf-8"))
    assert source_manifest["collection_started"] is False
    assert [author["author_key"] for author in source_manifest["authors"]] == [
        "buffett",
        "marks",
        "ackman",
        "flatt",
        "terry_smith",
    ]


def test_guru_start_filters_authors(tmp_path: Path):
    root = tmp_path / "guru"
    result = runner.invoke(
        app,
        ["guru", "start", "--root", str(root), "--authors", "buffett,marks", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["author_keys"] == ["buffett", "marks"]
    source_manifest = yaml.safe_load((root / "source_manifest.yaml").read_text(encoding="utf-8"))
    assert [author["author_key"] for author in source_manifest["authors"]] == ["buffett", "marks"]


def test_guru_start_dry_run_does_not_write_files(tmp_path: Path):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    result = runner.invoke(
        app,
        [
            "guru",
            "start",
            "--root",
            str(root),
            "--running-root",
            str(running_root),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["collection_started"] is False
    assert not root.exists()
    assert not running_root.exists()


def test_guru_verify_command(tmp_path: Path):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    start = runner.invoke(
        app,
        [
            "guru",
            "start",
            "--root",
            str(root),
            "--running-root",
            str(running_root),
            "--authors",
            "flatt",
        ],
    )
    assert start.exit_code == 0

    result = runner.invoke(
        app,
        [
            "guru",
            "verify",
            "--root",
            str(root),
            "--running-root",
            str(running_root),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True


def test_guru_build_index_command_outputs_json(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_build_guru_shard_index(root=None, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return {
            "schema_version": "krw-guru-shard-index/v2",
            "manifest_path": str(tmp_path / "guru" / "indexes" / "guru_shard_manifest.json"),
            "authors": {
                "buffett": {
                    "counts": {
                        "guru_objects": 1,
                        "consultation_objects": 1,
                        "data_needs": 1,
                        "relationships": 1,
                    }
                }
            },
        }

    import krw_ontology.guru.cli as guru_cli

    monkeypatch.setattr(guru_cli, "build_guru_shard_index", fake_build_guru_shard_index)

    result = runner.invoke(
        app,
        [
            "guru",
            "build-index",
            "--root",
            str(tmp_path / "guru"),
            "--index-dir",
            str(tmp_path / "guru-index"),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema_version"] == "krw-guru-shard-index/v2"
    assert captured == {
        "root": tmp_path / "guru",
        "index_dir": tmp_path / "guru-index",
    }


def test_guru_promote_and_release_status_commands(tmp_path: Path):
    source = tmp_path / "guru-source"
    (source / "reviewed").mkdir(parents=True)
    (source / "reviewed" / "guru_objects.jsonl").write_text(
        '{"reviewed_id":"guru:buffett:1","author_key":"buffett"}\n',
        encoding="utf-8",
    )

    promote = runner.invoke(
        app,
        [
            "guru",
            "promote",
            "--source-root",
            str(source),
            "--data-root",
            str(tmp_path / "guru-data"),
            "--env",
            "prod",
            "--release-id",
            "rel-cli",
            "--json",
        ],
    )

    assert promote.exit_code == 0
    promoted_payload = json.loads(promote.output)
    assert promoted_payload["promoted_release_id"] == "rel-cli"
    assert promoted_payload["current_release_id"] == "rel-cli"

    status = runner.invoke(
        app,
        [
            "guru",
            "release-status",
            "--data-root",
            str(tmp_path / "guru-data"),
            "--env",
            "prod",
            "--json",
        ],
    )

    assert status.exit_code == 0
    status_payload = json.loads(status.output)
    assert status_payload["current_release_id"] == "rel-cli"
    assert status_payload["manifest_exists"] is True


def test_guru_select_lenses_command_outputs_json(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_select_guru_lenses(**kwargs):
        captured.update(kwargs)
        return {
            "lens_selection_version": "krw-guru-lens-selection/v1",
            "selection_status": "ready_for_company_bridge",
            "question": kwargs["question"],
            "ticker": kwargs["ticker"],
            "selected_author_keys": kwargs["author_keys"],
            "intent_family": kwargs["intent_family"],
            "requires_company_evidence": True,
            "count": 1,
            "selected_lenses": [
                {
                    "label_ko": "소유주 이익 중심 사고",
                    "author_name": "Warren Buffett",
                }
            ],
            "company_bridge": {
                "use_existing_krw_ontology_mcp": True,
                "filing_evidence_requirements": ["cash_flow"],
            },
        }

    import krw_ontology.guru.cli as guru_cli

    monkeypatch.setattr(guru_cli, "select_guru_lenses", fake_select_guru_lenses)

    result = runner.invoke(
        app,
        [
            "guru",
            "select-lenses",
            "--root",
            str(tmp_path / "guru"),
            "--question",
            "AAPL을 버핏 관점에서 봐줘",
            "--authors",
            "buffett",
            "--ticker",
            "AAPL",
            "--intent-family",
            "holding_review",
            "--limit",
            "3",
            "--data-need-limit",
            "4",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["lens_selection_version"] == "krw-guru-lens-selection/v1"
    assert captured == {
        "question": "AAPL을 버핏 관점에서 봐줘",
        "root": tmp_path / "guru",
        "author_keys": ["buffett"],
        "ticker": "AAPL",
        "company_context": None,
        "intent_family": "holding_review",
        "limit": 3,
        "data_need_limit": 4,
    }


def test_guru_select_lenses_command_accepts_company_context_json(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_select_guru_lenses(**kwargs):
        captured.update(kwargs)
        return {
            "lens_selection_version": "krw-guru-lens-selection/v1",
            "selection_status": "ready_for_company_bridge",
            "selected_author_keys": kwargs["author_keys"],
            "requires_company_evidence": True,
            "count": 0,
            "selected_lenses": [],
            "company_bridge": {"filing_evidence_requirements": []},
        }

    import krw_ontology.guru.cli as guru_cli

    monkeypatch.setattr(guru_cli, "select_guru_lenses", fake_select_guru_lenses)

    context_json = json.dumps({"context_terms": ["oil and gas", "commodity price exposure"]})
    result = runner.invoke(
        app,
        [
            "guru",
            "select-lenses",
            "--root",
            str(tmp_path / "guru"),
            "--question",
            "OXY 어떠노",
            "--authors",
            "buffett",
            "--ticker",
            "OXY",
            "--company-context-json",
            context_json,
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert captured["company_context"] == {
        "context_terms": ["oil and gas", "commodity price exposure"]
    }


def test_guru_company_brief_command_outputs_bridge_payload(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_company_brief_tool(**kwargs):
        captured.update(kwargs)
        return json.dumps(
            {
                "company_brief_context_version": "krw-guru-company-brief-context/v1",
                "research_status": "needs_company_evidence",
                "requires_company_evidence": True,
                "next_step": "Pass company_research_question_ko to KRW Ontology.",
                "company_filing_brief": {
                    "format": "krw-guru-company-filing-brief/v1",
                    "company_identity": {"subject": "OXY", "ticker": "OXY", "unresolved": False},
                    "company_research_question_ko": "OXY 공시에서 현금흐름을 확인하라.",
                    "required_filing_topics": ["cash_flow"],
                },
            },
            ensure_ascii=False,
        )

    import krw_ontology.guru.cli as guru_cli

    monkeypatch.setattr(guru_cli, "guru_company_brief_tool", fake_company_brief_tool)

    context_json = json.dumps({"available_company_topics": ["commodity_price_exposure"]})
    result = runner.invoke(
        app,
        [
            "guru",
            "company-brief",
            "--root",
            str(tmp_path / "guru"),
            "--question",
            "옥시덴탈 어떠노",
            "--authors",
            "buffett",
            "--ticker",
            "OXY",
            "--company-name",
            "Occidental Petroleum",
            "--company-context-json",
            context_json,
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["company_brief_context_version"] == "krw-guru-company-brief-context/v1"
    assert captured["root"] == tmp_path / "guru"
    assert captured["author_keys"] == ["buffett"]
    assert captured["ticker"] == "OXY"
    assert captured["company_name"] == "Occidental Petroleum"
    assert captured["company_context_json"] == context_json


def test_guru_company_pack_command_outputs_render_plan(tmp_path: Path, monkeypatch):
    captured = {}
    company_payload = tmp_path / "company_payload.json"
    company_payload.write_text('{"facts":[{"topic":"cash_flow"}]}', encoding="utf-8")
    company_context = tmp_path / "company_context.json"
    company_context.write_text(
        '{"available_company_topics":["commodity_price_exposure"]}',
        encoding="utf-8",
    )

    def fake_company_pack_tool(**kwargs):
        captured.update(kwargs)
        return json.dumps(
            {
                "company_pack_context_version": "krw-guru-company-pack-context/v1",
                "research_status": "needs_company_evidence",
                "company_pack": {
                    "format": "krw-guru-company-research-pack/v1",
                    "company_identity": {"subject": "OXY", "ticker": "OXY", "unresolved": False},
                    "missing_evidence": ["balance_sheet"],
                },
                "render_plan": {
                    "format": "krw-guru-answer-render-plan/v1",
                    "opening_style": "자, 내가 먼저 묻고 싶은 건 하나입니다.",
                    "first_question": "OXY를 사업 일부로 가진다면 먼저 현금흐름을 봐야 합니다.",
                },
            },
            ensure_ascii=False,
        )

    import krw_ontology.guru.cli as guru_cli

    monkeypatch.setattr(guru_cli, "guru_company_pack_tool", fake_company_pack_tool)

    result = runner.invoke(
        app,
        [
            "guru",
            "company-pack",
            "--root",
            str(tmp_path / "guru"),
            "--question",
            "옥시덴탈 어떠노",
            "--authors",
            "buffett",
            "--ticker",
            "OXY",
            "--company-payload",
            str(company_payload),
            "--company-context",
            str(company_context),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["company_pack_context_version"] == "krw-guru-company-pack-context/v1"
    assert captured["company_payload_json"] == '{"facts":[{"topic":"cash_flow"}]}'
    assert captured["company_context_json"] == (
        '{"available_company_topics":["commodity_price_exposure"]}'
    )
    assert captured["author_keys"] == ["buffett"]


def test_guru_eval_quality_command_outputs_report(tmp_path: Path, monkeypatch):
    def fake_run_guru_quality_eval(root=None, **kwargs):
        return {
            "format": "krw-guru-quality-report/v1",
            "root": str(root),
            "cases": 1,
            "passed": 1,
            "failed": 0,
            "mean_score": 1.0,
            "quality_grade": "excellent",
            "output_path": str(tmp_path / "report.json"),
            "kwargs": {
                key: str(value) if value is not None else None for key, value in kwargs.items()
            },
        }

    import krw_ontology.guru.cli as guru_cli

    monkeypatch.setattr(guru_cli, "run_guru_quality_eval", fake_run_guru_quality_eval)

    result = runner.invoke(
        app,
        [
            "guru",
            "eval-quality",
            "--root",
            str(tmp_path / "guru"),
            "--eval-path",
            str(tmp_path / "gold.jsonl"),
            "--output-path",
            str(tmp_path / "report.json"),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["format"] == "krw-guru-quality-report/v1"
    assert payload["cases"] == 1
    assert payload["kwargs"]["eval_path"] == str(tmp_path / "gold.jsonl")


def test_guru_eval_quality_command_exits_two_when_budget_gate_fails(
    tmp_path: Path,
    monkeypatch,
):
    def fake_run_guru_quality_eval(root=None, **_kwargs):
        return {
            "format": "krw-guru-quality-report/v1",
            "root": str(root),
            "cases": 1,
            "passed": 0,
            "failed": 1,
            "mean_score": 0.5,
            "quality_grade": "needs_improvement",
            "output_path": str(tmp_path / "report.json"),
            "gate": {
                "passed": False,
                "failures": [{"name": "mean_score", "actual": 0.5}],
            },
        }

    import krw_ontology.guru.cli as guru_cli

    monkeypatch.setattr(guru_cli, "run_guru_quality_eval", fake_run_guru_quality_eval)
    budget_path = tmp_path / "budget.json"
    budget_path.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "guru",
            "eval-quality",
            "--root",
            str(tmp_path / "guru"),
            "--budget-path",
            str(budget_path),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.output)["gate"]["passed"] is False
