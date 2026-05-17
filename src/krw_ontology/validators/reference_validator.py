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

    # Business/exposure objects: supported evidence and local activity links
    if obj_type in ("BusinessActivity", "ExternalFactorExposure"):
        for cid in obj.get("supported_by_claims") or []:
            if cid not in all_objects:
                dangling.append(("supported_by_claims", cid))
        for qid in obj.get("supported_by_quotes") or []:
            if qid not in all_objects:
                dangling.append(("supported_by_quotes", qid))
        for activity_id in obj.get("related_business_activities") or []:
            if activity_id not in all_objects:
                dangling.append(("related_business_activities", activity_id))

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

    if obj_type == "SourceLocation":
        for field in ("source_span_id", "source_table_id", "source_table_cell_id"):
            ref_id = obj.get(field)
            if ref_id and ref_id not in all_objects:
                dangling.append((field, ref_id))

    if obj_type == "SourceTableCell":
        ref_id = obj.get("source_table_id")
        if ref_id and ref_id not in all_objects:
            dangling.append(("source_table_id", ref_id))

    if obj_type == "EntityMention":
        for field in ("canonical_entity_id", "source_object_id"):
            ref_id = obj.get(field)
            if ref_id and ref_id not in all_objects:
                dangling.append((field, ref_id))

    if obj_type == "MetricObservation":
        for ref_id in obj.get("source_fact_ids") or []:
            if ref_id not in all_objects:
                dangling.append(("source_fact_ids", ref_id))
        for ref_id in obj.get("source_metric_ids") or []:
            if ref_id not in all_objects:
                dangling.append(("source_metric_ids", ref_id))
        calc_id = obj.get("calculation_id")
        if calc_id and calc_id not in all_objects:
            dangling.append(("calculation_id", calc_id))

    if obj_type == "Calculation":
        for ref_id in obj.get("input_metric_ids") or []:
            if ref_id not in all_objects:
                dangling.append(("input_metric_ids", ref_id))
        output_id = obj.get("output_metric_id")
        if output_id and output_id not in all_objects:
            dangling.append(("output_metric_id", output_id))

    # CalculatedNumericSupport: every calculation input must exist
    if obj_type == "CalculatedNumericSupport":
        for input_id in obj.get("input_object_ids") or []:
            if input_id not in all_objects:
                dangling.append(("input_object_ids", input_id))

    # ChangeEvent can be document-scoped; company-level temporal events are
    # generated after document validation and are indexed separately.
    if obj_type == "ChangeEvent":
        for cid in obj.get("supported_by_claims") or []:
            if cid not in all_objects:
                dangling.append(("supported_by_claims", cid))
        for qid in obj.get("supported_by_quotes") or []:
            if qid not in all_objects:
                dangling.append(("supported_by_quotes", qid))
        for affected_id in obj.get("affected_objects") or []:
            if affected_id not in all_objects:
                dangling.append(("affected_objects", affected_id))

    if obj_type == "CompanyBusinessProfile":
        for source_id in obj.get("source_object_ids") or []:
            if source_id not in all_objects:
                dangling.append(("source_object_ids", source_id))
        for exposure_id in obj.get("key_exposures") or []:
            if exposure_id not in all_objects:
                dangling.append(("key_exposures", exposure_id))

    if obj_type in ("BusinessFactor", "AgreementTerm", "BusinessEvent"):
        for field in (
            "related_activity_ids",
            "related_entity_ids",
            "party_entity_ids",
            "supported_by_claims",
            "supported_by_quotes",
        ):
            for ref_id in obj.get(field) or []:
                if ref_id not in all_objects:
                    dangling.append((field, ref_id))
        authority_id = obj.get("authority_entity_id")
        if authority_id and authority_id not in all_objects:
            dangling.append(("authority_entity_id", authority_id))

    if obj_type == "TemporalLink":
        for field in ("from_object_id", "to_object_id"):
            ref_id = obj.get(field)
            if ref_id and ref_id not in all_objects:
                dangling.append((field, ref_id))

    if obj_type == "TrendObservation":
        for ref_id in obj.get("supported_by_objects") or []:
            if ref_id not in all_objects:
                dangling.append(("supported_by_objects", ref_id))

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

    # SupportLink: evidence source and supported target must exist
    if obj_type == "SupportLink":
        fid = obj.get("from_id")
        tid = obj.get("to_id")
        if fid and fid not in all_objects:
            dangling.append(("from_id", fid))
        if tid and tid not in all_objects:
            dangling.append(("to_id", tid))

    if dangling:
        return False, f"Dangling references: {dangling}"

    return True, None
