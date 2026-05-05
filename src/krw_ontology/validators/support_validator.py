"""Support validator — claim/quote support requirement (Section 8.4)."""

from __future__ import annotations


def validate_has_support(obj: dict) -> tuple[bool, str | None]:
    """Check that research interpretation objects have claim or quote support.

    RiskFactor, GrowthDriver, Headwind, AssumptionCandidate must have
    at least one entry in supported_by_claims or supported_by_quotes.
    Other object types pass automatically.
    """
    obj_type = obj.get("type", "")

    if obj_type not in (
        "RiskFactor",
        "GrowthDriver",
        "Headwind",
        "AssumptionCandidate",
    ):
        return True, None

    claims = obj.get("supported_by_claims") or []
    quotes = obj.get("supported_by_quotes") or []

    if claims or quotes:
        return True, None

    return False, (
        f"{obj_type} has no support: both supported_by_claims "
        "and supported_by_quotes are empty"
    )
