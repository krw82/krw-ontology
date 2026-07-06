from __future__ import annotations

from pathlib import Path

import yaml

from krw_ontology.guru.models import GuruSearchTarget
from krw_ontology.guru.verifier import (
    validate_search_targets,
    verify_guru_workspace,
)
from krw_ontology.guru.workspace import initialize_guru_workspace


def test_validate_search_targets_uses_standalone_future_hooks():
    targets = [
        GuruSearchTarget(
            target_id="ok",
            principle_id="buffett:p1",
            target_type="metric",
            target_key="free_cash_flow",
            source_system="future_company_metric",
        ),
        GuruSearchTarget(
            target_id="bad",
            principle_id="buffett:p1",
            target_type="metric",
            target_key="roic",
            source_system="guru_corpus",
        ),
        GuruSearchTarget(
            target_id="topic",
            principle_id="buffett:p1",
            target_type="topic",
            target_key="moat",
            source_system="guru_corpus",
        ),
    ]

    errors = validate_search_targets(targets)

    assert errors == ["bad:metric_target_source_system_invalid"]


def test_verify_workspace_accepts_planned_artifacts(tmp_path: Path):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    initialize_guru_workspace(root, running_root=running_root, author_keys=["buffett", "marks"])

    result = verify_guru_workspace(
        root,
        running_root=running_root,
    )

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["collection_started"] is False


def test_verify_workspace_flags_mutated_collection_started(tmp_path: Path):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    initialize_guru_workspace(root, running_root=running_root, author_keys=["buffett"])
    source_manifest_path = root / "source_manifest.yaml"
    payload = yaml.safe_load(source_manifest_path.read_text(encoding="utf-8"))
    payload["collection_started"] = True
    source_manifest_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    result = verify_guru_workspace(
        root,
        running_root=running_root,
    )

    assert result["ok"] is False
    assert "source_manifest_collection_started_not_false" in result["errors"]
