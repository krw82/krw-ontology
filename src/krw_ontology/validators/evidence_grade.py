"""Derived evidence grading for evidence-backed interpretation objects."""

from __future__ import annotations

from krw_ontology.factor_taxonomy import factor_aliases


def apply_evidence_grade(obj: dict, all_objects: dict[str, dict]) -> None:
    """Attach a conservative evidence_grade to supported ontology objects.

    This is intentionally deterministic. AI extraction may propose semantic
    hints, but the grade is derived only from accepted support objects.
    """
    if obj.get("type") != "ExternalFactorExposure":
        return

    claim_ids = obj.get("supported_by_claims") or []
    quote_ids = obj.get("supported_by_quotes") or []
    if not claim_ids and not quote_ids:
        obj["evidence_grade"] = "unsupported"
        return

    quote_text = " ".join(_object_text(all_objects.get(qid, {})) for qid in quote_ids)
    claim_text = " ".join(_object_text(all_objects.get(cid, {})) for cid in claim_ids)

    factor_terms = factor_aliases(obj.get("factor") or "")
    channel_terms = [
        str(obj.get("impact_channel") or ""),
        str(obj.get("impact_channel") or "").replace("_", " "),
        *[str(metric).replace("_", " ") for metric in obj.get("related_metrics") or []],
    ]

    if quote_text and _contains_any(quote_text, factor_terms) and _contains_any(quote_text, channel_terms):
        obj["evidence_grade"] = "direct"
        return
    if quote_text and _contains_any(quote_text, factor_terms):
        obj["evidence_grade"] = "direct"
        return
    if claim_text and _contains_any(claim_text, factor_terms):
        obj["evidence_grade"] = "indirect"
        return
    if quote_text or claim_text:
        obj["evidence_grade"] = "derived"
        return

    obj["evidence_grade"] = "unsupported"


def _object_text(obj: dict) -> str:
    return " ".join(
        str(obj.get(key) or "")
        for key in ("quote_text", "text", "claim_text", "description")
        if obj.get(key)
    )


def _contains_any(text: str, terms: list[str]) -> bool:
    text_l = text.lower()
    return any(str(term).lower() in text_l for term in terms if term)
