"""Schema validator — Pydantic model validation (Section 8.1 #1)."""

from __future__ import annotations

from krw_ontology.schema.objects import OBJECT_TYPE_MODELS


def validate_schema(obj: dict) -> tuple[bool, str | None]:
    """Validate object against its Pydantic model based on type field.

    Returns (is_valid, reason) where reason is None on success or
    describes the validation error on failure.
    """
    obj_type = obj.get("type")
    if not obj_type:
        return False, "Missing required field: type"

    model_cls = OBJECT_TYPE_MODELS.get(obj_type)
    if model_cls is None:
        return False, f"Unknown object type: {obj_type}"

    try:
        model_cls.model_validate(obj)
    except Exception as exc:
        return False, f"Schema validation failed: {exc}"

    return True, None
