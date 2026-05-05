"""Relation validator — edge relation whitelist check (Section 8.7)."""

from __future__ import annotations

from pathlib import Path

import yaml


_RELATIONS_PATH = Path(__file__).resolve().parents[3] / "ontology" / "schema" / "relations.yaml"


def _load_relations(relations_path: Path | None = None) -> list[dict]:
    """Load relation whitelist from relations.yaml."""
    path = relations_path or _RELATIONS_PATH
    with open(path) as f:
        data = yaml.safe_load(f)
    return data["relations"]


def validate_edge(
    edge: dict,
    relations_whitelist: list[dict],
    all_objects: dict[str, dict],
) -> tuple[bool, str | None]:
    """Check Edge endpoints and relation fields match the whitelist.

    All four fields must match: relation_id, relation_name, from type, to type.
    """
    if edge.get("type") != "Edge":
        return True, None

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
        if (
            rel["id"] == relation_id
            and rel["name"] == relation_name
            and rel["from"] == from_type
            and rel["to"] == to_type
        ):
            return True, None

    return False, (
        f"Edge relation ({relation_id}/{relation_name}) "
        f"from {from_type} to {to_type} not in whitelist"
    )
