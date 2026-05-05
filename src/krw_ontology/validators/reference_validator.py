"""Reference validator — ID referential integrity (Section 8.3)."""

from __future__ import annotations


def validate_references(
    obj: dict, all_objects: dict[str, dict]
) -> tuple[bool, str | None]:
    """Check all ID reference fields point to existing objects.

    Only checks ID-based references. Does NOT validate metric names
    (that is metric_validator's responsibility).

    Dangling references cause immediate rejection (not needs_review).
    """
    obj_type = obj.get("type", "")
    dangling: list[tuple[str, str]] = []

    # EvidenceQuote: source_span_id must exist
    if obj_type == "EvidenceQuote":
        span_id = obj.get("source_span_id")
        if span_id and span_id not in all_objects:
            dangling.append(("source_span_id", span_id))

    # ResearchClaim: supported_by_quotes must exist
    if obj_type == "ResearchClaim":
        for qid in obj.get("supported_by_quotes") or []:
            if qid not in all_objects:
                dangling.append(("supported_by_quotes", qid))

    # ResearchObject types: supported_by_claims and supported_by_quotes
    if obj_type in ("RiskFactor", "GrowthDriver", "Headwind"):
        for cid in obj.get("supported_by_claims") or []:
            if cid not in all_objects:
                dangling.append(("supported_by_claims", cid))
        for qid in obj.get("supported_by_quotes") or []:
            if qid not in all_objects:
                dangling.append(("supported_by_quotes", qid))

    # AssumptionCandidate: supported_by_claims and supported_by_quotes
    if obj_type == "AssumptionCandidate":
        for cid in obj.get("supported_by_claims") or []:
            if cid not in all_objects:
                dangling.append(("supported_by_claims", cid))
        for qid in obj.get("supported_by_quotes") or []:
            if qid not in all_objects:
                dangling.append(("supported_by_quotes", qid))

    # LanguageSignal: source_quote_id must exist
    if obj_type == "LanguageSignal":
        qid = obj.get("source_quote_id")
        if qid and qid not in all_objects:
            dangling.append(("source_quote_id", qid))

    # CalculatedNumericSupport: every calculation input must exist
    if obj_type == "CalculatedNumericSupport":
        for input_id in obj.get("input_object_ids") or []:
            if input_id not in all_objects:
                dangling.append(("input_object_ids", input_id))

    # NumericEvidence: source object and optional inputs must exist
    if obj_type == "NumericEvidence":
        source_id = obj.get("source_object_id")
        if source_id and source_id not in all_objects:
            dangling.append(("source_object_id", source_id))
        quote_id = obj.get("source_quote_id")
        if quote_id and quote_id not in all_objects:
            dangling.append(("source_quote_id", quote_id))
        for input_id in obj.get("input_object_ids") or []:
            if input_id not in all_objects:
                dangling.append(("input_object_ids", input_id))

    # Edge: from_id and to_id must exist
    if obj_type == "Edge":
        fid = obj.get("from_id")
        tid = obj.get("to_id")
        if fid and fid not in all_objects:
            dangling.append(("from_id", fid))
        if tid and tid not in all_objects:
            dangling.append(("to_id", tid))

    if dangling:
        return False, f"Dangling references: {dangling}"

    return True, None
