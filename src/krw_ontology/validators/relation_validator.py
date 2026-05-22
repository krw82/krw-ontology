"""Relation validator — edge relation whitelist check (Section 8.7)."""

from __future__ import annotations

from pathlib import Path

import yaml


_RELATIONS_PATH = Path(__file__).resolve().parents[3] / "ontology" / "schema" / "relations.yaml"


def _relations_schema_path(relations_path: Path | None = None) -> Path:
    candidates = []
    if relations_path is not None:
        candidates.append(relations_path)
    candidates.extend(
        [
            _RELATIONS_PATH,
            Path(__file__).resolve().parents[1] / "resources" / "schema" / "relations.yaml",
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _load_relations(relations_path: Path | None = None) -> list[dict]:
    """Load relation whitelist from relations.yaml."""
    path = _relations_schema_path(relations_path)
    with open(path) as f:
        data = yaml.safe_load(f)
    return data["relations"]


def validate_edge(
    edge: dict,
    relations_whitelist: list[dict],
    all_objects: dict[str, dict],
) -> tuple[bool, str | None]:
    """Check Edge endpoints and relation fields match the whitelist.

    Relation identity, endpoint types, and edge metadata must match the
    whitelist. Endpoint specs can be a single type or a list of allowed types.
    """
    if edge.get("type") != "Edge":
        return True, None

    missing_metadata = [
        field
        for field in ("edge_class", "evidence_level", "generation_method", "rationale")
        if not edge.get(field)
    ]
    if missing_metadata:
        return False, f"Edge missing required metadata fields: {missing_metadata}"

    from_id = edge.get("from_id", "")
    to_id = edge.get("to_id", "")

    from_obj = all_objects.get(from_id)
    to_obj = all_objects.get(to_id)

    if from_obj is None:
        return False, f"Edge from_id not found in objects: {from_id}"
    if to_obj is None:
        return False, f"Edge to_id not found in objects: {to_id}"

    from_type = from_obj.get("type", "")
    to_type = to_obj.get("type", "")
    relation_name = edge.get("relation_name", "")
    relation_id = edge.get("relation_id", "")

    for rel in relations_whitelist:
        if rel.get("id") != relation_id or rel.get("name") != relation_name:
            continue
        if from_type not in _allowed_types(rel.get("from")):
            continue
        if to_type not in _allowed_types(rel.get("to")):
            continue
        if rel.get("same_type_required") and from_type != to_type:
            return False, (
                f"Edge relation ({relation_id}/{relation_name}) requires same endpoint type, "
                f"got {from_type} -> {to_type}"
            )
        for field in ("edge_class", "evidence_level"):
            expected = rel.get(field)
            if expected and edge.get(field) != expected:
                return False, (
                    f"Edge relation ({relation_id}/{relation_name}) has {field}="
                    f"{edge.get(field)!r}; expected {expected!r}"
                )
            return True, None

    return False, (
        f"Edge relation ({relation_id}/{relation_name}) "
        f"from {from_type} to {to_type} not in whitelist"
    )


def _allowed_types(spec: object) -> set[str]:
    if isinstance(spec, str):
        return {spec}
    if isinstance(spec, list):
        return {str(item) for item in spec}
    return set()
