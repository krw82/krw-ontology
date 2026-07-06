"""Verification for guru ontology planning artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from krw_ontology.guru.models import (
    AUTHOR_KEYS,
    GURU_COLLECTION_PLAN_FORMAT,
    GURU_ONTOLOGY_SCHEMA_VERSION,
    GURU_SOURCE_MANIFEST_FORMAT,
    GuruSearchTarget,
)
from krw_ontology.guru.workspace import guru_root, guru_running_root


def validate_search_targets(targets: list[GuruSearchTarget]) -> list[str]:
    """Return validation errors for standalone guru search-target hooks."""
    errors: list[str] = []
    for target in targets:
        if target.target_type == "metric" and target.source_system != "future_company_metric":
            errors.append(f"{target.target_id}:metric_target_source_system_invalid")
        elif target.target_type == "topic" and target.source_system not in {
            "future_company_text",
            "guru_corpus",
        }:
            errors.append(f"{target.target_id}:topic_target_source_system_invalid")
        elif target.target_type == "section" and target.source_system != "future_company_section":
            errors.append(f"{target.target_id}:section_target_source_system_invalid")
    return errors


def verify_guru_workspace(
    root: Path | str | None = None,
    *,
    running_root: Path | str | None = None,
) -> dict[str, Any]:
    """Verify planned guru workspace artifacts without reading raw source text."""
    target = guru_root(root)
    running_path = guru_running_root(running_root)
    errors: list[str] = []
    warnings: list[str] = []
    source_manifest_path = target / "source_manifest.yaml"
    collection_plan_path = target / "collection_plan.json"
    ontology_manifest_path = target / "ontology_manifest.json"

    source_manifest = _load_yaml_object(source_manifest_path)
    collection_plan = _load_json_object(collection_plan_path)
    ontology_manifest = _load_json_object(ontology_manifest_path)

    if source_manifest is None:
        errors.append("source_manifest_missing_or_invalid")
    else:
        errors.extend(_source_manifest_errors(source_manifest))

    if collection_plan is None:
        errors.append("collection_plan_missing_or_invalid")
    else:
        errors.extend(_collection_plan_errors(collection_plan))

    if ontology_manifest is None:
        errors.append("ontology_manifest_missing_or_invalid")
    else:
        if ontology_manifest.get("schema_version") != GURU_ONTOLOGY_SCHEMA_VERSION:
            errors.append("ontology_manifest_schema_version_mismatch")
        if ontology_manifest.get("collection_started") is not False:
            errors.append("ontology_manifest_collection_started_not_false")

    if not running_path.exists():
        warnings.append("running_root_missing")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "root": str(target),
        "running_root": str(running_path),
        "collection_started": False,
    }


def _source_manifest_errors(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("format") != GURU_SOURCE_MANIFEST_FORMAT:
        errors.append("source_manifest_format_mismatch")
    if payload.get("schema_version") != GURU_ONTOLOGY_SCHEMA_VERSION:
        errors.append("source_manifest_schema_version_mismatch")
    if payload.get("collection_started") is not False:
        errors.append("source_manifest_collection_started_not_false")
    authors = payload.get("authors")
    if not isinstance(authors, list) or not authors:
        errors.append("source_manifest_authors_missing")
        return errors
    seen: set[str] = set()
    for author in authors:
        if not isinstance(author, Mapping):
            errors.append("source_manifest_author_invalid")
            continue
        key = str(author.get("author_key") or "")
        if key in seen:
            errors.append(f"source_manifest_author_duplicate:{key}")
        seen.add(key)
        if key not in AUTHOR_KEYS:
            errors.append(f"source_manifest_author_unknown:{key}")
        for field in ("display_name", "organization", "official_index_url", "default_rights_policy"):
            if not author.get(field):
                errors.append(f"source_manifest_author_{key}_{field}_missing")
    planned_sources = payload.get("planned_sources")
    if not isinstance(planned_sources, list) or not planned_sources:
        errors.append("source_manifest_planned_sources_missing")
    else:
        for source in planned_sources:
            if not isinstance(source, Mapping):
                errors.append("source_manifest_planned_source_invalid")
                continue
            if not source.get("official_url"):
                errors.append(f"source_manifest_source_{source.get('source_id')}_official_url_missing")
            if not source.get("rights_policy"):
                errors.append(f"source_manifest_source_{source.get('source_id')}_rights_policy_missing")
            if source.get("collection_status") != "planned":
                errors.append(f"source_manifest_source_{source.get('source_id')}_not_planned")
    return errors


def _collection_plan_errors(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("format") != GURU_COLLECTION_PLAN_FORMAT:
        errors.append("collection_plan_format_mismatch")
    if payload.get("schema_version") != GURU_ONTOLOGY_SCHEMA_VERSION:
        errors.append("collection_plan_schema_version_mismatch")
    for flag in (
        "collection_started",
        "extraction_started",
        "ticker_required",
        "existing_ontology_schema_changed",
        "mcp_changed",
        "skills_changed",
    ):
        if payload.get(flag) is not False:
            errors.append(f"collection_plan_{flag}_not_false")
    object_types = payload.get("ontology_object_types")
    for required in (
        "GuruConcept",
        "GuruPrinciple",
        "GuruQuestion",
        "GuruSearchTarget",
        "GuruInvestorIntent",
        "GuruQuestionTemplate",
        "GuruAnswerPlaybook",
        "GuruDataNeed",
        "GuruClarifyingQuestion",
    ):
        if not isinstance(object_types, list) or required not in object_types:
            errors.append(f"collection_plan_object_type_missing:{required}")
    return errors


def _load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _load_yaml_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None
