"""Evidence-preserving recovery for missing direct XBRL MetricObservation rows.

This module deliberately has a narrow contract. It never rewrites a claim,
never invents a number, and never chooses between competing XBRL facts. A
repair is eligible only when the canonical generator produces one direct,
dimensionless observation from one exact source fact and that observation is
absent from the document artifacts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from krw_ontology.config.constants import normalize_doc_type
from krw_ontology.pipeline.stages.generate_canonical_artifacts import (
    _load_metric_specs,
    _metric_observations,
    _metric_period_type,
)
from krw_ontology.utils.io import read_jsonl, write_jsonl


DIRECT_XBRL_METRIC_GAP_CONTRACT = "krw-ontology-direct-xbrl-metric-gap/v1"
_HASHED_ARTIFACTS = (
    "source_documents.jsonl",
    "xbrl_facts.jsonl",
    "metric_observations.jsonl",
)


def document_fingerprint(ontology_dir: Path) -> str:
    """Hash the source inputs and current direct-metric state for a repair job."""
    digest = hashlib.sha256()
    for filename in _HASHED_ARTIFACTS:
        path = ontology_dir / filename
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def document_artifact_hashes(ontology_dir: Path) -> dict[str, str]:
    """Return content hashes for every document JSONL artifact."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(ontology_dir.glob("*.jsonl"))
        if path.is_file()
    }


def discover_direct_xbrl_metric_gaps(ontology_dir: Path) -> dict[str, Any]:
    """Classify direct metric observations as present, eligible, or blocked."""
    identity, identity_error = _document_identity(ontology_dir)
    base = {
        "contract": DIRECT_XBRL_METRIC_GAP_CONTRACT,
        "ontology_dir": str(ontology_dir),
        "document_fingerprint": document_fingerprint(ontology_dir),
        "eligible": [],
        "present": [],
        "blocked": [],
        "expected_count": 0,
    }
    if identity_error:
        return {
            **base,
            "identity": identity,
            "discovery_error": identity_error,
            "summary": {"eligible": 0, "present": 0, "blocked": 1},
            "blocked": [{"reason": identity_error}],
        }

    xbrl_facts = read_jsonl(ontology_dir / "xbrl_facts.jsonl")
    if not xbrl_facts:
        return {
            **base,
            "identity": identity,
            "summary": {"eligible": 0, "present": 0, "blocked": 1},
            "blocked": [{"reason": "no_xbrl_facts"}],
        }

    try:
        expected, _calculations = _metric_observations(
            ontology_dir=ontology_dir,
            ticker=identity["ticker"],
            period=identity["period"],
            document_type=identity["document_type"],
            source_document_id=identity["source_document_id"],
            doc_type_key=identity["doc_type_key"],
            xbrl_facts=xbrl_facts,
        )
    except Exception as exc:
        return {
            **base,
            "identity": identity,
            "discovery_error": f"canonical_metric_generation_failed:{exc}",
            "summary": {"eligible": 0, "present": 0, "blocked": 1},
            "blocked": [{"reason": "canonical_metric_generation_failed"}],
        }

    direct_expected = [
        row
        for row in expected
        if row.get("source_type") == "xbrl"
        and not row.get("dimensions")
        and len(row.get("source_fact_ids") or []) == 1
    ]
    existing = read_jsonl(ontology_dir / "metric_observations.jsonl")
    existing_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in existing:
        if row.get("type") != "MetricObservation":
            continue
        existing_by_key.setdefault(_observation_key(row), []).append(row)

    metric_specs = _load_metric_specs(ontology_dir)
    facts_for_metric = _facts_by_metric_key(xbrl_facts, metric_specs)
    eligible: list[dict[str, Any]] = []
    present: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for observation in direct_expected:
        key = _observation_key(observation)
        source_fact_id = str((observation.get("source_fact_ids") or [""])[0])
        same_key = existing_by_key.get(key) or []
        if same_key:
            if any(source_fact_id in (row.get("source_fact_ids") or []) for row in same_key):
                present.append(_candidate_payload(observation, source_fact_id, "already_present"))
            else:
                blocked.append(_candidate_payload(observation, source_fact_id, "conflicting_existing_observation"))
            continue

        direct_facts = facts_for_metric.get(key) or []
        if len(direct_facts) != 1:
            reason = "ambiguous_direct_xbrl_fact" if direct_facts else "source_fact_not_resolved"
            blocked.append(_candidate_payload(observation, source_fact_id, reason, direct_facts))
            continue
        if str(direct_facts[0].get("id") or "") != source_fact_id:
            blocked.append(_candidate_payload(observation, source_fact_id, "generator_source_fact_mismatch", direct_facts))
            continue
        errors = validate_direct_xbrl_metric_observation(observation, direct_facts[0])
        if errors:
            blocked.append(_candidate_payload(observation, source_fact_id, "candidate_validation_failed", errors=errors))
            continue
        eligible.append(
            _candidate_payload(
                observation,
                source_fact_id,
                "eligible",
                include_observation=observation,
            )
        )

    return {
        **base,
        "identity": identity,
        "expected_count": len(direct_expected),
        "eligible": eligible,
        "present": present,
        "blocked": blocked,
        "summary": {
            "eligible": len(eligible),
            "present": len(present),
            "blocked": len(blocked),
        },
    }


def apply_direct_xbrl_metric_gaps(
    ontology_dir: Path,
    *,
    candidate_ids: list[str],
    expected_document_fingerprint: str,
) -> dict[str, Any]:
    """Append only previously reviewed exact-XBRL metric observations."""
    discovered = discover_direct_xbrl_metric_gaps(ontology_dir)
    current_fingerprint = str(discovered["document_fingerprint"])
    if current_fingerprint != expected_document_fingerprint:
        raise RuntimeError(
            "source document changed after repair planning; create a new quality plan"
        )
    if discovered.get("discovery_error"):
        raise RuntimeError(str(discovered["discovery_error"]))

    eligible = {
        str(row["candidate_id"]): row
        for row in discovered.get("eligible") or []
        if row.get("candidate_id")
    }
    requested = list(dict.fromkeys(str(candidate_id) for candidate_id in candidate_ids))
    missing = [candidate_id for candidate_id in requested if candidate_id not in eligible]
    if missing:
        raise RuntimeError(f"metric-gap candidates are no longer eligible: {missing[:5]}")

    additions = [dict(eligible[candidate_id]["observation"]) for candidate_id in requested]
    before = read_jsonl(ontology_dir / "metric_observations.jsonl")
    before_ids = {str(row.get("id") or "") for row in before}
    duplicate_ids = [str(row.get("id") or "") for row in additions if str(row.get("id") or "") in before_ids]
    if duplicate_ids:
        raise RuntimeError(f"metric-gap candidate already exists: {duplicate_ids[:5]}")
    write_jsonl(ontology_dir / "metric_observations.jsonl", [*before, *additions])
    return {
        "candidate_ids": requested,
        "added_count": len(additions),
        "added_observations": additions,
        "document_fingerprint_before": current_fingerprint,
        "document_fingerprint_after": document_fingerprint(ontology_dir),
    }


def validate_direct_xbrl_metric_observation(
    observation: dict[str, Any], source_fact: dict[str, Any]) -> list[str]:
    """Validate the narrow automatic-repair contract without editing any claim."""
    errors: list[str] = []
    if observation.get("type") != "MetricObservation":
        errors.append("not_metric_observation")
    if observation.get("source_type") != "xbrl":
        errors.append("not_direct_xbrl")
    if observation.get("dimensions") not in ({}, None):
        errors.append("dimensions_not_empty")
    source_fact_ids = observation.get("source_fact_ids") or []
    if len(source_fact_ids) != 1 or source_fact_ids[0] != source_fact.get("id"):
        errors.append("source_fact_id_not_exact")
    if observation.get("value") != source_fact.get("value"):
        errors.append("source_value_not_exact")
    if observation.get("calculation_id") is not None:
        errors.append("calculation_not_allowed")
    if observation.get("normalization") != "reported":
        errors.append("normalization_not_reported")
    if observation.get("confidence") != 1.0:
        errors.append("confidence_not_one")
    return errors


def metric_observation_delta_errors(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    allowed_additions: list[dict[str, Any]],
) -> list[str]:
    """Reject any staged mutation other than the reviewed direct additions."""
    before_by_id = {str(row.get("id") or ""): row for row in before}
    after_by_id = {str(row.get("id") or ""): row for row in after}
    allowed_by_id = {str(row.get("id") or ""): row for row in allowed_additions}
    errors: list[str] = []
    for object_id, row in before_by_id.items():
        if object_id not in after_by_id:
            errors.append(f"existing_metric_removed:{object_id}")
        elif _canonical_json(row) != _canonical_json(after_by_id[object_id]):
            errors.append(f"existing_metric_modified:{object_id}")
    for object_id, row in after_by_id.items():
        if object_id not in before_by_id:
            expected = allowed_by_id.get(object_id)
            if expected is None:
                errors.append(f"unexpected_metric_added:{object_id}")
            elif _canonical_json(row) != _canonical_json(expected):
                errors.append(f"allowed_metric_changed:{object_id}")
    return errors


def unchanged_artifact_errors(
    before: dict[str, str],
    after: dict[str, str],
    *,
    allowed_changes: set[str],
) -> list[str]:
    names = set(before) | set(after)
    return [
        f"unexpected_artifact_change:{name}"
        for name in sorted(names)
        if before.get(name) != after.get(name) and name not in allowed_changes
    ]


def _document_identity(ontology_dir: Path) -> tuple[dict[str, str], str | None]:
    source_documents = read_jsonl(ontology_dir / "source_documents.jsonl")
    if not source_documents:
        return {}, "source_document_missing"
    source = source_documents[0]
    ticker = str(source.get("ticker") or "").upper()
    document_type = str(source.get("document_type") or "")
    period = str(source.get("period") or "")
    source_document_id = str(source.get("id") or source.get("source_document_id") or "")
    doc_type_key = str(source.get("doc_type_key") or normalize_doc_type(document_type))
    missing = [
        name
        for name, value in {
            "ticker": ticker,
            "document_type": document_type,
            "period": period,
            "source_document_id": source_document_id,
        }.items()
        if not value
    ]
    if missing:
        return {
            "ticker": ticker,
            "document_type": document_type,
            "period": period,
            "source_document_id": source_document_id,
            "doc_type_key": doc_type_key,
        }, f"source_document_identity_incomplete:{','.join(missing)}"
    return {
        "ticker": ticker,
        "document_type": document_type,
        "period": period,
        "source_document_id": source_document_id,
        "doc_type_key": doc_type_key,
    }, None


def _facts_by_metric_key(
    xbrl_facts: list[dict[str, Any]],
    metric_specs: dict[str, dict[str, Any]],
) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    tags_to_metrics: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for metric_name, spec in metric_specs.items():
        for tag in spec.get("xbrl_tags") or []:
            tags_to_metrics.setdefault(str(tag), []).append((metric_name, spec))
    found: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for fact in xbrl_facts:
        context = fact.get("context") or {}
        if context.get("has_dimensions") or context.get("dimensions"):
            continue
        tag = str(fact.get("taxonomy_tag") or "")
        for metric_name, spec in tags_to_metrics.get(tag, []):
            key = (
                metric_name,
                context.get("fiscal_year"),
                _metric_period_type(context),
                context.get("start_date"),
                context.get("end_date") or context.get("instant"),
                _dimensions_key({}),
                str(spec.get("unit") or fact.get("unit") or ""),
            )
            found.setdefault(key, []).append(fact)
    return found


def _observation_key(observation: dict[str, Any]) -> tuple[Any, ...]:
    return (
        observation.get("metric_name"),
        observation.get("fiscal_year"),
        observation.get("period_type"),
        observation.get("period_start"),
        observation.get("period_end"),
        _dimensions_key(observation.get("dimensions") or {}),
        str(observation.get("unit") or ""),
    )


def _dimensions_key(value: Any) -> str:
    return _canonical_json(value)


def _candidate_payload(
    candidate: dict[str, Any],
    source_fact_id: str,
    status: str,
    facts: list[dict[str, Any]] | None = None,
    *,
    include_observation: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "candidate_id": candidate.get("id"),
        "source_fact_id": source_fact_id,
        "status": status,
        "metric_key": list(_observation_key(candidate)),
    }
    if facts is not None:
        payload["matching_source_fact_ids"] = [fact.get("id") for fact in facts]
    if include_observation is not None:
        payload["observation"] = include_observation
    if errors:
        payload["errors"] = errors
    return payload


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
