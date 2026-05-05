"""Batch-local reference aliases for AI extraction stages."""

from __future__ import annotations

from typing import Iterable


def alias_objects(objects: Iterable[dict], prefix: str) -> tuple[list[dict], dict[str, str]]:
    """Return shallow-copied objects with short IDs and alias->canonical map."""
    aliased: list[dict] = []
    alias_to_id: dict[str, str] = {}
    for index, obj in enumerate(objects, start=1):
        canonical_id = obj.get("id")
        if not canonical_id:
            continue
        alias = f"{prefix}{index}"
        alias_to_id[alias] = canonical_id
        copied = dict(obj)
        copied["id"] = alias
        aliased.append(copied)
    return aliased, alias_to_id


def canonical_to_alias(alias_to_id: dict[str, str]) -> dict[str, str]:
    """Invert an alias map for canonical->alias lookup."""
    return {canonical_id: alias for alias, canonical_id in alias_to_id.items()}


def resolve_references(
    values: list[str] | None,
    alias_to_id: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Resolve alias or already-canonical references.

    Returns (resolved_ids, unknown_refs). Unknown references should be rejected,
    not silently dropped, because dangling evidence support corrupts the graph.
    """
    if not values:
        return [], []
    canonical_ids = set(alias_to_id.values())
    resolved: list[str] = []
    unknown: list[str] = []
    for value in values:
        if value in alias_to_id:
            target = alias_to_id[value]
        elif value in canonical_ids:
            target = value
        else:
            unknown.append(value)
            continue
        if target not in resolved:
            resolved.append(target)
    return resolved, unknown

