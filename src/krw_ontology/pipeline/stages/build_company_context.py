"""Build company-level profile and temporal context artifacts."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from krw_ontology.factor_taxonomy import normalize_sector_hint
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.sector_packs import choose_sector_pack
from krw_ontology.utils.io import atomic_write_json, read_jsonl, write_jsonl

CONTEXT_DOC_TYPE = "COMPANY"
CONTEXT_DOC_TYPE_KEY = "COMPANY"
CONTEXT_PERIOD = "ALL"

_EVENT_KEYWORDS = (
    "executed", "entered into", "filed", "applied", "approved", "authorized",
    "achieved cod", "cod", "delayed", "terminated", "amended", "completed",
    "withdrew", "submitted", "appeal", "appeals",
)

_CONTEXT_RELATION_META = {
    "continues_as": ("temporal", "inferred", "deterministic_cross_period"),
    "supports_change_event": ("event", "direct", "deterministic_event_detection"),
    "affects_object": ("event", "derived", "deterministic_shared_claim"),
}

_EVIDENCE_GRADE_SCORE = {"direct": 4, "indirect": 3, "derived": 2, "unsupported": 0}
_CONFIDENCE_SCORE = {"high": 3, "medium": 2, "low": 1}
_MATERIALITY_SCORE = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
_CHANGE_EVENTS_PER_PERIOD = 80
_CHANGE_EVENTS_TOTAL = 400


def build_company_context(root: Path, ticker: str) -> dict[str, Any]:
    """Build company-level profile, temporal links, trends, and change events."""
    root = root.resolve()
    ticker = ticker.upper()
    docs = _load_document_artifacts(root, ticker)
    context_dir = root / "companies" / ticker / "context"
    context_dir.mkdir(parents=True, exist_ok=True)

    profile = _build_profile(ticker, docs)
    temporal_links = _build_temporal_links(ticker, docs)
    trend_observations = _build_trend_observations(ticker, docs)
    change_events = _build_change_events(ticker, docs)
    quality_events: list[dict[str, Any]] = []
    edges = _build_context_edges(
        ticker,
        temporal_links,
        change_events,
    )

    files_to_write = {
        "company_business_profiles": [profile],
        "temporal_links": temporal_links,
        "trend_observations": trend_observations,
        "change_events": change_events,
        "quality_events": quality_events,
        "edges": edges,
    }
    for key, rows in files_to_write.items():
        write_jsonl(context_dir / f"{key}.jsonl", rows)
    atomic_write_json(
        context_dir / "section_quality.json",
        {"status": "pass", "document_type": CONTEXT_DOC_TYPE, "fail_reasons": []},
    )

    from krw_ontology.pipeline.stages.validate_company_context import run_validate_company_context

    validation = run_validate_company_context(root, ticker)
    counts = {key: len(read_jsonl(context_dir / f"{key}.jsonl")) for key in files_to_write}
    counts["rejected_objects"] = len(read_jsonl(context_dir / "rejected_objects.jsonl"))
    artifact_index = {
        "ticker": ticker,
        "document_type": CONTEXT_DOC_TYPE,
        "doc_type_key": CONTEXT_DOC_TYPE_KEY,
        "period": CONTEXT_PERIOD,
        "files": {
            key: f"companies/{ticker}/context/{key}.jsonl"
            for key in files_to_write
        } | {
            "rejected_objects": f"companies/{ticker}/context/rejected_objects.jsonl",
            "context_validation": f"companies/{ticker}/context/context_validation.json",
        },
        "counts": counts,
        "sources": {},
        "reports": {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }
    atomic_write_json(context_dir / "artifact_index.json", artifact_index)
    return {
        "context_dir": context_dir,
        "artifact_index_path": context_dir / "artifact_index.json",
        "counts": counts,
        "validation": validation["stats"],
    }


def _load_document_artifacts(root: Path, ticker: str) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for artifact_path in sorted((root / "companies" / ticker / "ontology").glob("*/*/artifact_index.json")):
        artifact = json.loads(artifact_path.read_text())
        if artifact.get("document_type") == CONTEXT_DOC_TYPE:
            continue
        files = artifact.get("files", {})
        doc = {
            "artifact": artifact,
            "artifact_path": artifact_path,
            "claims": _read_artifact(root, files.get("claims")),
            "quotes": _read_artifact(root, files.get("evidence_quotes")),
            "business_activities": _read_artifact(root, files.get("business_activities")),
            "external_factor_exposures": _read_artifact(root, files.get("external_factor_exposures")),
            "metric_observations": _read_artifact(root, files.get("metric_observations")),
            "business_factors": _read_artifact(root, files.get("business_factors")),
            "agreement_terms": _read_artifact(root, files.get("agreement_terms")),
            "business_events": _read_artifact(root, files.get("business_events")),
        }
        docs.append(doc)
    return docs


def _read_artifact(root: Path, rel_path: str | None) -> list[dict]:
    if not rel_path:
        return []
    path = Path(rel_path)
    if not path.is_absolute():
        path = root / path
    return read_jsonl(path)


def _build_profile(ticker: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
    activities = [obj for doc in docs for obj in doc["business_activities"]]
    exposures = [obj for doc in docs for obj in doc["external_factor_exposures"]]
    claims = [obj for doc in docs for obj in doc["claims"]]
    combined_text = "\n".join(claim.get("claim_text") or "" for claim in claims)
    sector_counts = Counter(claim.get("sector_hint") for claim in claims if claim.get("sector_hint"))
    normalized_sector_counts = Counter(
        normalize_sector_hint(str(sector)) or str(sector)
        for sector, count in sector_counts.items()
        for _ in range(count)
    )
    sector = (
        normalized_sector_counts.most_common(1)[0][0]
        if normalized_sector_counts
        else normalize_sector_hint(choose_sector_pack(combined_text).sector) or "generic"
    )

    revenue_sources = _top_names(
        activity for activity in activities if activity.get("revenue_relevance") == "primary"
    )
    cost_sources = _top_names(
        activity for activity in activities if activity.get("cost_relevance") == "primary"
    )
    primary_activities = _top_names(activities)
    ranked_activities = _rank_context_objects(activities)
    ranked_exposures = _rank_exposures(exposures)
    key_factors = _ranked_factor_keys(exposures)
    metric_counts = Counter(
        metric
        for obj in [*activities, *exposures]
        for metric in obj.get("related_metrics") or []
    )
    source_object_ids = _dedupe(
        [
            *[activity.get("id") for activity in ranked_activities[:25]],
            *[exposure.get("id") for exposure in ranked_exposures[:35]],
        ]
    )
    summary = _business_model_summary(primary_activities, key_factors)
    return {
        "id": f"company_business_profile:{ticker}:ALL",
        "type": "CompanyBusinessProfile",
        "ticker": ticker,
        "source_document_id": f"source:{ticker}:ALL:COMPANY",
        "document_type": CONTEXT_DOC_TYPE,
        "period": CONTEXT_PERIOD,
        "sector": sector,
        "business_model_summary": summary,
        "primary_business_activities": primary_activities,
        "primary_revenue_sources": revenue_sources,
        "primary_cost_sources": cost_sources,
        "key_external_factors": key_factors,
        "key_exposures": [exposure.get("id") for exposure in ranked_exposures[:40] if exposure.get("id")],
        "key_metrics": [metric for metric, _count in metric_counts.most_common(12)],
        "key_uncertainties": _uncertainties(exposures),
        "source_object_ids": source_object_ids,
        "confidence": "high" if activities or exposures else "low",
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    }


def _rank_context_objects(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        objects,
        key=lambda obj: (
            -_period_recency_score(obj.get("period")),
            -_object_quality_score(obj),
            str(obj.get("id") or ""),
        ),
    )


def _rank_exposures(exposures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    factor_counts = Counter(exposure.get("factor") for exposure in exposures if exposure.get("factor"))
    return sorted(
        exposures,
        key=lambda obj: (
            -_period_recency_score(obj.get("period")),
            -_object_quality_score(obj),
            -factor_counts.get(obj.get("factor"), 0),
            str(obj.get("id") or ""),
        ),
    )


def _ranked_factor_keys(exposures: list[dict[str, Any]]) -> list[str]:
    scores: dict[str, int] = defaultdict(int)
    for exposure in exposures:
        factor = exposure.get("factor")
        if not factor:
            continue
        scores[str(factor)] += _object_quality_score(exposure)
    return [
        factor
        for factor, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:12]
    ]


def _object_quality_score(obj: dict[str, Any]) -> int:
    support_count = len(obj.get("supported_by_claims") or []) + len(obj.get("supported_by_quotes") or [])
    return (
        _EVIDENCE_GRADE_SCORE.get(str(obj.get("evidence_grade") or "").lower(), 1) * 20
        + _MATERIALITY_SCORE.get(str(obj.get("materiality") or obj.get("materiality_hint") or "").lower(), 0) * 8
        + _CONFIDENCE_SCORE.get(str(obj.get("confidence") or "").lower(), 0) * 4
        + min(support_count, 10)
    )


def _build_temporal_links(ticker: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        for obj in [
            *doc["business_activities"],
            *doc["external_factor_exposures"],
            *doc["business_factors"],
            *doc["agreement_terms"],
            *doc["business_events"],
        ]:
            key = _temporal_key(obj)
            if key:
                groups[key].append(obj)

    links: list[dict[str, Any]] = []
    for (_obj_type, _key), objects in groups.items():
        ordered = sorted(objects, key=lambda obj: _period_sort_key(obj.get("period", "")))
        for left, right in zip(ordered, ordered[1:]):
            if left.get("period") == right.get("period"):
                continue
            relation = "continues_as"
            link_id = f"temporal_link:{ticker}:ALL:{_hash(left.get('id'), right.get('id'), relation)}"
            links.append({
                "id": link_id,
                "type": "TemporalLink",
                "ticker": ticker,
                "source_document_id": f"source:{ticker}:ALL:COMPANY",
                "document_type": CONTEXT_DOC_TYPE,
                "period": CONTEXT_PERIOD,
                "from_object_id": left["id"],
                "to_object_id": right["id"],
                "from_period": left["period"],
                "to_period": right["period"],
                "relation": relation,
                "rationale": f"Both objects describe the same {_obj_type} key across periods.",
                "confidence": "high",
                "review_status": "accepted",
                "schema_version": SCHEMA_VERSION,
            })
    return links


def _build_trend_observations(ticker: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metric_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        for metric in doc["metric_observations"]:
            group_key = _trend_group_key(metric)
            if group_key and metric.get("value") is not None:
                metric_groups[group_key].append(metric)

    trends: list[dict[str, Any]] = []
    for (metric_name, _period_kind), values in metric_groups.items():
        ordered = _trend_points(values)
        for left, right in zip(ordered, ordered[1:]):
            if left.get("id") == right.get("id") or not _metrics_are_comparable(left, right):
                continue
            left_value = float(left["value"])
            right_value = float(right["value"])
            direction = "flat"
            if right_value > left_value:
                direction = "up"
            elif right_value < left_value:
                direction = "down"
            magnitude = right_value - left_value
            trend_id = f"trend_observation:{ticker}:ALL:{_hash(metric_name, left.get('id'), right.get('id'))}"
            trends.append({
                "id": trend_id,
                "type": "TrendObservation",
                "ticker": ticker,
                "source_document_id": f"source:{ticker}:ALL:COMPANY",
                "document_type": CONTEXT_DOC_TYPE,
                "period": CONTEXT_PERIOD,
                "subject": metric_name,
                "metric_or_factor": metric_name,
                "from_period": left["period"],
                "to_period": right["period"],
                "direction": direction,
                "magnitude_text": f"{magnitude:g} {right.get('unit') or left.get('unit') or ''}".strip(),
                "interpretation": (
                    f"{metric_name} moved {direction} from {left['period']} to {right['period']}."
                ),
                "supported_by_objects": [left["id"], right["id"]],
                "confidence": "high",
                "review_status": "accepted",
                "schema_version": SCHEMA_VERSION,
            })
    return trends


def _build_change_events(ticker: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    claim_objects = _claim_object_index(docs)
    for doc in docs:
        for claim in doc["claims"]:
            text = claim.get("claim_text") or ""
            if not _contains_event(text):
                continue
            event_type = _event_type(text)
            event_period = claim.get("period") or CONTEXT_PERIOD
            event_id = f"change_event:{ticker}:{event_period}:({_hash(claim.get('id'), event_type)})"
            events.append({
                "id": event_id.replace(":(", ":").replace(")", ""),
                "type": "ChangeEvent",
                "ticker": ticker,
                "source_document_id": f"source:{ticker}:ALL:COMPANY",
                "document_type": CONTEXT_DOC_TYPE,
                "period": event_period,
                "event_type": event_type,
                "event_date": _extract_date_text(text),
                "description": " ".join(text.split())[:500],
                "affected_objects": claim_objects.get(claim["id"]) or None,
                "supported_by_claims": [claim["id"]],
                "supported_by_quotes": claim.get("supported_by_quotes") or [],
                "confidence": claim.get("confidence") or "medium",
                "review_status": "accepted",
                "schema_version": SCHEMA_VERSION,
            })
    return _select_change_events(events)


def _build_context_edges(
    ticker: str,
    temporal_links: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for link in temporal_links:
        edges.append(_edge(
            ticker,
            link["from_object_id"],
            link["to_object_id"],
            link["relation"],
            link["relation"],
            rationale=link.get("rationale") or "Objects match across consecutive reporting periods.",
        ))
    for event in events:
        for claim_id in event.get("supported_by_claims") or []:
            edges.append(_edge(
                ticker,
                claim_id,
                event["id"],
                "supports_change_event",
                "supports_change_event",
                rationale="ChangeEvent.supported_by_claims includes this research claim.",
            ))
        for object_id in event.get("affected_objects") or []:
            edges.append(_edge(
                ticker,
                event["id"],
                object_id,
                "affects_object",
                "affects_object",
                rationale="ChangeEvent.affected_objects includes this business/risk context object.",
            ))
    return edges


def _edge(
    ticker: str,
    from_id: str,
    to_id: str,
    relation_id: str,
    relation_name: str,
    *,
    rationale: str,
) -> dict[str, Any]:
    edge_class, evidence_level, generation_method = _CONTEXT_RELATION_META[relation_id]
    return {
        "id": f"edge:{ticker}:ALL:COMPANY:{_hash(from_id, relation_id, to_id)}",
        "type": "Edge",
        "ticker": ticker,
        "source_document_id": f"source:{ticker}:ALL:COMPANY",
        "document_type": CONTEXT_DOC_TYPE,
        "period": CONTEXT_PERIOD,
        "from_id": from_id,
        "to_id": to_id,
        "relation_name": relation_name,
        "relation_id": relation_id,
        "edge_class": edge_class,
        "evidence_level": evidence_level,
        "generation_method": generation_method,
        "rationale": rationale,
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": SCHEMA_VERSION,
    }


def _claim_object_index(docs: list[dict[str, Any]]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for doc in docs:
        for obj in [
            *doc["business_activities"],
            *doc["external_factor_exposures"],
            *doc["business_factors"],
            *doc["agreement_terms"],
            *doc["business_events"],
        ]:
            obj_id = obj.get("id")
            if not obj_id:
                continue
            for claim_id in obj.get("supported_by_claims") or []:
                index[claim_id].append(obj_id)
    return {claim_id: _dedupe(object_ids) for claim_id, object_ids in index.items()}


def _top_names(objects) -> list[str]:
    counts = Counter(obj.get("name") for obj in objects if obj.get("name"))
    return [name for name, _count in counts.most_common(12)]


def _business_model_summary(activities: list[str], factors: list[str]) -> str:
    activity_text = ", ".join(activities[:5]) or "no extracted primary activities"
    factor_text = ", ".join(factors[:6]) or "no extracted external factors"
    return f"Primary activities: {activity_text}. Key external factors: {factor_text}."


def _uncertainties(exposures: list[dict[str, Any]]) -> list[str]:
    values = []
    for exposure in exposures:
        if exposure.get("effect_direction") in {"uncertain", "mixed"}:
            values.append(f"{exposure.get('factor')} via {exposure.get('impact_channel')}")
    return _dedupe(values)[:12]


def _temporal_key(obj: dict[str, Any]) -> tuple[str, str] | None:
    obj_type = obj.get("type")
    if obj_type == "BusinessActivity":
        return obj_type, _slug(obj.get("name") or "")
    if obj_type == "ExternalFactorExposure":
        return obj_type, "|".join([
            str(obj.get("factor") or ""),
            str(obj.get("impact_channel") or ""),
            str(obj.get("benchmark") or ""),
        ])
    if obj_type == "BusinessFactor":
        channels = ",".join(obj.get("affected_channels") or [])
        roles = ",".join(obj.get("factor_roles") or [])
        return obj_type, "|".join([str(obj.get("category") or ""), roles, channels or _slug(obj.get("name") or "")])
    if obj_type in {"AgreementTerm", "BusinessEvent"}:
        return obj_type, _slug(obj.get("name") or obj.get("description") or obj.get("event_type") or "")
    return None


def _period_sort_key(period: str) -> tuple[int, int, str]:
    match = re.match(r"FY(\d{4})(?:Q([1-4?]))?$", period or "", flags=re.IGNORECASE)
    if not match:
        return (0, 0, period or "")
    year = int(match.group(1))
    quarter_raw = match.group(2)
    quarter = int(quarter_raw) if quarter_raw and quarter_raw.isdigit() else 0
    return (year, quarter, period)


def _period_recency_score(period: str | None) -> int:
    match = re.match(r"FY(\d{4})(?:Q([1-4?]))?$", period or "", flags=re.IGNORECASE)
    if not match:
        return 0
    year = int(match.group(1))
    quarter_raw = match.group(2)
    quarter = int(quarter_raw) if quarter_raw and quarter_raw.isdigit() else 5
    return year * 10 + quarter


def _period_quarter(period: str | None) -> str | None:
    match = re.match(r"FY\d{4}(Q[1-4])$", period or "", flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _trend_group_key(metric: dict[str, Any]) -> tuple[str, str] | None:
    metric_name = metric.get("metric_name")
    if not metric_name:
        return None
    period_kind = _trend_period_kind(metric)
    if not period_kind:
        return None
    return str(metric_name), period_kind


def _trend_period_kind(metric: dict[str, Any]) -> str | None:
    period_type = str(metric.get("period_type") or "").lower()
    if period_type == "derived":
        return "derived_quarter" if _period_quarter(metric.get("period")) else "derived_annual"
    if period_type in {"annual", "quarter", "year_to_date", "instant"}:
        return period_type
    if not period_type and _period_quarter(metric.get("period")):
        return "quarter"
    if not period_type and _period_sort_key(metric.get("period") or "")[0]:
        return "annual"
    return period_type or None


def _trend_points(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_point: dict[tuple[Any, ...], dict[str, Any]] = {}
    for value in values:
        key = _trend_point_key(value)
        current = by_point.get(key)
        if current is None or _metric_point_rank(value) > _metric_point_rank(current):
            by_point[key] = value
    return sorted(by_point.values(), key=_metric_chronology_key)


def _trend_point_key(metric: dict[str, Any]) -> tuple[Any, ...]:
    period_kind = _trend_period_kind(metric)
    if period_kind and period_kind.startswith("derived_"):
        return (period_kind, metric.get("period"))
    return (
        period_kind,
        metric.get("fiscal_year"),
        metric.get("fiscal_period"),
        metric.get("start_date"),
        metric.get("end_date"),
    )


def _metric_chronology_key(metric: dict[str, Any]) -> tuple[int, int, str, str]:
    fiscal_year = metric.get("fiscal_year")
    year = int(fiscal_year) if isinstance(fiscal_year, int) else _period_sort_key(metric.get("period") or "")[0]
    fiscal_period = str(metric.get("fiscal_period") or "")
    quarter_match = re.search(r"Q([1-4])", fiscal_period or str(metric.get("period") or ""), re.IGNORECASE)
    quarter = int(quarter_match.group(1)) if quarter_match else (5 if _trend_period_kind(metric) == "annual" else 0)
    return (year, quarter, str(metric.get("end_date") or ""), str(metric.get("id") or ""))


def _metric_point_rank(metric: dict[str, Any]) -> tuple[int, int]:
    period = str(metric.get("period") or "")
    fiscal_year = metric.get("fiscal_year")
    fiscal_period = metric.get("fiscal_period")
    aligned = 0
    if isinstance(fiscal_year, int):
        expected_period = f"FY{fiscal_year}{fiscal_period or ''}"
        if period == expected_period:
            aligned = 2
        elif period.startswith(f"FY{fiscal_year}"):
            aligned = 1
    return (aligned, _object_quality_score(metric))


def _metrics_are_comparable(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _trend_group_key(left) != _trend_group_key(right):
        return False
    if _trend_point_key(left) == _trend_point_key(right):
        return False
    return True


def _select_change_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event.get("period") or CONTEXT_PERIOD)].append(event)

    selected: list[dict[str, Any]] = []
    for period in sorted(grouped, key=_period_recency_score, reverse=True):
        selected.extend(sorted(grouped[period], key=_change_event_rank)[:_CHANGE_EVENTS_PER_PERIOD])

    selected = selected[:_CHANGE_EVENTS_TOTAL]
    return sorted(
        selected,
        key=lambda event: (
            -_period_recency_score(event.get("period")),
            _change_event_rank(event),
        ),
    )


def _change_event_rank(event: dict[str, Any]) -> tuple[int, int, str]:
    support_count = len(event.get("supported_by_claims") or []) + len(event.get("supported_by_quotes") or [])
    event_type_rank = {
        "approval_update": 0,
        "project_execution_update": 1,
        "contract_update": 2,
        "business_update": 3,
    }.get(str(event.get("event_type") or ""), 4)
    confidence_rank = -_CONFIDENCE_SCORE.get(str(event.get("confidence") or "").lower(), 0)
    return (event_type_rank, confidence_rank - support_count, str(event.get("id") or ""))


def _contains_event(text: str) -> bool:
    text_l = text.lower()
    return any(keyword in text_l for keyword in _EVENT_KEYWORDS)


def _event_type(text: str) -> str:
    text_l = text.lower()
    if any(token in text_l for token in ("ferc", "doe", "approval", "authorization", "permit")):
        return "approval_update"
    if any(token in text_l for token in ("cod", "commercial operations", "deadline", "delay")):
        return "project_execution_update"
    if any(token in text_l for token in ("spa", "contract", "agreement", "executed", "entered into")):
        return "contract_update"
    return "business_update"


def _extract_date_text(text: str) -> str | None:
    match = re.search(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
        text,
    )
    return match.group(0) if match else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _hash(*values: Any) -> str:
    raw = "|".join(str(value) for value in values)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _dedupe(values) -> list:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
