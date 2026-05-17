"""Metric validator — canonical metric mapping check (Section 8.5)."""

from __future__ import annotations

from pathlib import Path

import yaml


_METRIC_DICT_PATH = Path(__file__).resolve().parents[3] / "ontology" / "schema" / "metric_dictionary.yaml"


def _load_metric_names(metric_dict_path: Path | None = None) -> set[str]:
    """Load canonical metric names from metric_dictionary.yaml."""
    path = metric_dict_path or _METRIC_DICT_PATH
    with open(path) as f:
        data = yaml.safe_load(f)
    return set(data["canonical_metrics"].keys())


def validate_metric_fields(
    obj: dict, canonical_metrics: set[str]
) -> tuple[bool, str | None]:
    """Separate canonical metrics from unmapped metric mentions.

    Unknown metrics are moved to unmapped_metrics and the object is
    marked needs_review. The object is NOT rejected — only flagged.

    Returns (True, None) always since unknown metrics don't cause rejection,
    but modifies the object in place to move unknown metrics.
    """
    for field in ("related_metrics", "affects", "affected_channels"):
        values = obj.get(field)
        if not values:
            continue

        unknown = [m for m in values if m not in canonical_metrics]
        if unknown:
            obj.setdefault("unmapped_metrics", []).extend(unknown)
            obj[field] = [m for m in values if m in canonical_metrics]
            obj["review_status"] = "needs_review"

    return True, None
