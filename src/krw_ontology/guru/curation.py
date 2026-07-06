"""Deterministic curation for guru ontology extraction candidates."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from krw_ontology.guru.extractor import _normalize_candidate_payload
from krw_ontology.guru.models import (
    GuruCurationReport,
    GuruOntologyCandidate,
    GuruParsedManifest,
    GuruPrivateSpanRecord,
    GuruRejectedCandidate,
    GuruReviewedConsultationObject,
    GuruReviewedCorpusMetadata,
    GuruReviewedDataNeed,
    GuruReviewedObject,
    GuruReviewedRelationship,
    utc_now_iso,
)
from krw_ontology.guru.workspace import _write_json_atomic, guru_root, guru_running_root


GURU_OBJECT_TYPES = {
    "concept",
    "principle",
    "idea",
    "heuristic",
    "anti_pattern",
    "decision_criterion",
    "risk_frame",
    "valuation_frame",
    "time_horizon_frame",
    "behavioral_warning",
    "safety_rule",
}
CONSULTATION_TYPES = {
    "investor_intent",
    "question_template",
    "question_route",
    "answer_playbook",
    "answer_section",
    "clarifying_question",
}
CLEANUP_REPLACEMENTS = {
    "기루": "구루",
    "테리 스미트": "테리 스미스",
}
UNFIXABLE_KOREAN_PATTERNS = (
    "자존적 부상",
)


def curate_guru_candidates(
    root: Path | str | None = None,
    *,
    running_root: Path | str | None = None,
    candidates_path: Path | str | None = None,
    reviewed_dir: Path | str | None = None,
) -> GuruCurationReport:
    """Validate raw candidates and write reviewed guru ontology artifacts."""
    root_path = guru_root(root)
    running_path = guru_running_root(running_root)
    input_path = (
        Path(candidates_path).expanduser().resolve()
        if candidates_path is not None
        else running_path / "generated" / "ontology_candidates.jsonl"
    )
    output_dir = (
        Path(reviewed_dir).expanduser().resolve()
        if reviewed_dir is not None
        else root_path / "reviewed"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    span_index = _load_span_index(running_path)
    candidates, rejected = _load_candidates(input_path)
    candidate_lookup = {candidate.candidate_id: candidate for candidate in candidates}
    valid_candidate_ids = set(candidate_lookup)

    accepted_by_candidate_id: dict[str, str] = {}
    seen_review_keys: dict[tuple[str, str, str], str] = {}
    guru_objects: list[GuruReviewedObject] = []
    consultation_objects: list[GuruReviewedConsultationObject] = []
    data_needs: list[GuruReviewedDataNeed] = []
    corpus_metadata: list[GuruReviewedCorpusMetadata] = []
    warnings: list[str] = []

    for candidate in candidates:
        candidate = _clean_candidate_text(candidate)
        reason = _candidate_rejection_reason(candidate, span_index, valid_candidate_ids)
        if reason:
            rejected.append(_reject(candidate, "curation_validation", reason))
            continue

        review_key = (
            candidate.author_key,
            candidate.object_type,
            _normalize_label(candidate.label_ko),
        )
        if review_key in seen_review_keys:
            rejected.append(
                _reject(
                    candidate,
                    "dedupe",
                    f"duplicate normalized label; canonical={seen_review_keys[review_key]}",
                )
            )
            continue

        reviewed_id = _reviewed_id(candidate)
        seen_review_keys[review_key] = candidate.candidate_id
        accepted_by_candidate_id[candidate.candidate_id] = reviewed_id

        if candidate.object_type in GURU_OBJECT_TYPES:
            guru_objects.append(_to_guru_object(candidate, reviewed_id))
        elif candidate.object_type in CONSULTATION_TYPES:
            consultation_objects.append(_to_consultation_object(candidate, reviewed_id))
        elif candidate.object_type == "data_need":
            data_needs.append(_to_data_need(candidate, reviewed_id))
        elif candidate.object_type == "corpus_metadata":
            corpus_metadata.append(_to_corpus_metadata(candidate, reviewed_id))
        else:
            rejected.append(
                _reject(candidate, "routing", f"unsupported reviewed object_type={candidate.object_type}")
            )

    relationships = _build_relationships(
        candidates,
        accepted_by_candidate_id=accepted_by_candidate_id,
    )
    _attach_reviewed_relationships(
        guru_objects,
        consultation_objects,
        data_needs,
        corpus_metadata,
        relationships,
    )

    output_files = {
        "guru_objects": output_dir / "guru_objects.jsonl",
        "consultation_objects": output_dir / "consultation_objects.jsonl",
        "data_needs": output_dir / "data_needs.jsonl",
        "relationships": output_dir / "relationships.jsonl",
        "corpus_metadata": output_dir / "corpus_metadata.jsonl",
        "rejected_candidates": output_dir / "rejected_candidates.jsonl",
        "curation_report": output_dir / "curation_report.json",
    }
    _write_jsonl(output_files["guru_objects"], [item.model_dump(mode="json") for item in guru_objects])
    _write_jsonl(
        output_files["consultation_objects"],
        [item.model_dump(mode="json") for item in consultation_objects],
    )
    _write_jsonl(output_files["data_needs"], [item.model_dump(mode="json") for item in data_needs])
    _write_jsonl(
        output_files["relationships"],
        [item.model_dump(mode="json") for item in relationships],
    )
    _write_jsonl(
        output_files["corpus_metadata"],
        [item.model_dump(mode="json") for item in corpus_metadata],
    )
    _write_jsonl(
        output_files["rejected_candidates"],
        [item.model_dump(mode="json") for item in rejected],
    )

    rejected_reasons = Counter(item.rejection_reason for item in rejected)
    if not guru_objects:
        warnings.append("no reviewed guru objects were accepted")
    if not consultation_objects:
        warnings.append("no reviewed consultation objects were accepted")
    if not data_needs:
        warnings.append("no reviewed data needs were accepted")

    report = GuruCurationReport(
        generated_at=utc_now_iso(),
        root=str(root_path),
        running_root=str(running_path),
        input_candidates=len(candidates) + sum(
            1 for item in rejected if item.rejection_stage == "candidate_schema"
        ),
        accepted_guru_objects=len(guru_objects),
        accepted_consultation_objects=len(consultation_objects),
        accepted_data_needs=len(data_needs),
        accepted_corpus_metadata=len(corpus_metadata),
        relationships=len(relationships),
        rejected_candidates=len(rejected),
        warnings=warnings,
        rejection_reasons=dict(sorted(rejected_reasons.items())),
        files={name: str(path) for name, path in output_files.items()},
    )
    _write_json_atomic(output_files["curation_report"], report.model_dump(mode="json"))
    return report


def _load_candidates(path: Path) -> tuple[list[GuruOntologyCandidate], list[GuruRejectedCandidate]]:
    candidates: list[GuruOntologyCandidate] = []
    rejected: list[GuruRejectedCandidate] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("candidate row is not an object")
                candidates.append(
                    GuruOntologyCandidate.model_validate(
                        _normalize_candidate_payload(payload)
                    )
                )
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                raw_payload: dict[str, Any]
                try:
                    loaded = json.loads(line)
                    raw_payload = loaded if isinstance(loaded, dict) else {"value": loaded}
                except json.JSONDecodeError:
                    raw_payload = {"line": line.strip()}
                rejected.append(
                    GuruRejectedCandidate(
                        candidate_id=str(raw_payload.get("candidate_id") or f"line:{line_number}"),
                        author_key=raw_payload.get("author_key"),
                        object_type=raw_payload.get("object_type"),
                        object_origin=raw_payload.get("object_origin"),
                        label_ko=raw_payload.get("label_ko"),
                        rejection_stage="candidate_schema",
                        rejection_reason=str(exc),
                        source_payload=raw_payload,
                    )
                )
    return candidates, rejected


def _load_span_index(running_root: Path) -> dict[str, GuruPrivateSpanRecord]:
    manifest_path = running_root / "parsed_manifest.json"
    manifest = GuruParsedManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    spans: dict[str, GuruPrivateSpanRecord] = {}
    for document in manifest.parsed_documents:
        if document.status != "parsed" or not document.spans_path:
            continue
        path = Path(document.spans_path)
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                span = GuruPrivateSpanRecord.model_validate_json(line)
                spans[span.span_id] = span
    return spans


def _candidate_rejection_reason(
    candidate: GuruOntologyCandidate,
    span_index: dict[str, GuruPrivateSpanRecord],
    valid_candidate_ids: set[str],
) -> str | None:
    if not candidate.label_ko.strip() or not candidate.summary_ko.strip():
        return "missing Korean label or summary"
    if _has_unfixable_korean(candidate):
        return "awkward Korean expression requires review"

    missing_refs = [span_id for span_id in candidate.supporting_span_ids if span_id not in span_index]
    if missing_refs:
        return f"missing supporting span ids: {', '.join(missing_refs[:3])}"

    if candidate.object_origin in {"source_grounded", "corpus_synthesized"}:
        if not candidate.supporting_span_ids:
            return "grounded object has no supporting spans"
        if candidate.object_type not in GURU_OBJECT_TYPES:
            return f"grounded object routed to unsupported object_type={candidate.object_type}"

    if candidate.object_origin == "corpus_metadata":
        if candidate.object_type != "corpus_metadata":
            return "corpus_metadata origin must use corpus_metadata object_type"
        if not candidate.supporting_span_ids:
            return "corpus metadata has no supporting spans"

    if candidate.object_type == "data_need":
        if candidate.object_origin != "data_need":
            return "data_need object_type must use data_need origin"
        if not candidate.data_need_family or not candidate.data_need_key:
            return "data_need is missing data_need_family or data_need_key"

    if candidate.object_type in CONSULTATION_TYPES:
        if candidate.object_origin != "consultation_derived":
            return "consultation object must use consultation_derived origin"
        if not candidate.related_candidate_ids and not candidate.supporting_span_ids:
            return "consultation object has neither related candidates nor direct support"
        missing_related = [
            related_id
            for related_id in candidate.related_candidate_ids
            if related_id not in valid_candidate_ids
        ]
        if len(missing_related) == len(candidate.related_candidate_ids) and not candidate.supporting_span_ids:
            return "consultation object only references unknown related candidates"

    if _uses_official_index_for_lens_object(candidate, span_index):
        return "official index span cannot support lens object"
    return None


def _has_unfixable_korean(candidate: GuruOntologyCandidate) -> bool:
    text = " ".join(
        value
        for value in (
            candidate.label_ko,
            candidate.summary_ko,
            candidate.body_ko or "",
            candidate.question_pattern_ko or "",
        )
        if value
    )
    return any(pattern in text for pattern in UNFIXABLE_KOREAN_PATTERNS)


def _clean_candidate_text(candidate: GuruOntologyCandidate) -> GuruOntologyCandidate:
    return candidate.model_copy(
        update={
            "label_ko": _clean_korean(candidate.label_ko),
            "summary_ko": _clean_korean(candidate.summary_ko),
            "body_ko": _clean_korean(candidate.body_ko) if candidate.body_ko else None,
            "question_pattern_ko": (
                _clean_korean(candidate.question_pattern_ko)
                if candidate.question_pattern_ko
                else None
            ),
            "example_questions_ko": [
                _clean_korean(value) for value in candidate.example_questions_ko
            ],
            "answer_sections": [_clean_korean(value) for value in candidate.answer_sections],
        }
    )


def _clean_korean(value: str) -> str:
    cleaned = value
    for before, after in CLEANUP_REPLACEMENTS.items():
        cleaned = cleaned.replace(before, after)
    return cleaned


def _uses_official_index_for_lens_object(
    candidate: GuruOntologyCandidate,
    span_index: dict[str, GuruPrivateSpanRecord],
) -> bool:
    if candidate.object_type not in GURU_OBJECT_TYPES:
        return False
    return any(
        "official_index" in span_index[span_id].source_id
        for span_id in candidate.supporting_span_ids
        if span_id in span_index
    )


def _to_guru_object(candidate: GuruOntologyCandidate, reviewed_id: str) -> GuruReviewedObject:
    return GuruReviewedObject(
        reviewed_id=reviewed_id,
        candidate_id=candidate.candidate_id,
        author_key=candidate.author_key,
        object_type=candidate.object_type,  # type: ignore[arg-type]
        object_origin=candidate.object_origin,  # type: ignore[arg-type]
        label_ko=candidate.label_ko.strip(),
        label_en=candidate.label_en,
        summary_ko=candidate.summary_ko.strip(),
        body_ko=candidate.body_ko,
        supporting_span_ids=sorted(set(candidate.supporting_span_ids)),
        intent_family=candidate.intent_family,
        applicability=candidate.applicability,
        specificity=candidate.specificity,
        answer_role=candidate.answer_role,
        confidence=candidate.confidence,
    )


def _to_consultation_object(
    candidate: GuruOntologyCandidate,
    reviewed_id: str,
) -> GuruReviewedConsultationObject:
    return GuruReviewedConsultationObject(
        reviewed_id=reviewed_id,
        candidate_id=candidate.candidate_id,
        author_key=candidate.author_key,
        object_type=candidate.object_type,  # type: ignore[arg-type]
        label_ko=candidate.label_ko.strip(),
        summary_ko=candidate.summary_ko.strip(),
        question_pattern_ko=candidate.question_pattern_ko,
        example_questions_ko=candidate.example_questions_ko,
        intent_family=candidate.intent_family,
        intent_tags=candidate.intent_tags,
        decision_stage=candidate.decision_stage,
        answer_section_type=candidate.answer_section_type,
        answer_sections=candidate.answer_sections,
        required_context=candidate.required_context,
        requires_company_data=candidate.requires_company_data,
        requires_portfolio_data=candidate.requires_portfolio_data,
        requires_user_context=candidate.requires_user_context,
        supporting_span_ids=sorted(set(candidate.supporting_span_ids)),
        applicability=candidate.applicability,
        specificity=candidate.specificity,
        answer_role=candidate.answer_role,
        confidence=candidate.confidence,
    )


def _to_data_need(candidate: GuruOntologyCandidate, reviewed_id: str) -> GuruReviewedDataNeed:
    return GuruReviewedDataNeed(
        reviewed_id=reviewed_id,
        candidate_id=candidate.candidate_id,
        author_key=candidate.author_key,
        label_ko=candidate.label_ko.strip(),
        summary_ko=candidate.summary_ko.strip(),
        data_need_family=candidate.data_need_family,  # type: ignore[arg-type]
        data_need_key=candidate.data_need_key or "",
        required_context=candidate.required_context,
        requires_company_data=candidate.requires_company_data,
        requires_portfolio_data=candidate.requires_portfolio_data,
        requires_user_context=candidate.requires_user_context,
        company_data_hooks=candidate.company_data_hooks,
        supporting_span_ids=sorted(set(candidate.supporting_span_ids)),
        applicability=candidate.applicability,
        specificity=candidate.specificity,
        answer_role=candidate.answer_role,
        confidence=candidate.confidence,
    )


def _to_corpus_metadata(
    candidate: GuruOntologyCandidate,
    reviewed_id: str,
) -> GuruReviewedCorpusMetadata:
    return GuruReviewedCorpusMetadata(
        reviewed_id=reviewed_id,
        candidate_id=candidate.candidate_id,
        author_key=candidate.author_key,
        label_ko=candidate.label_ko.strip(),
        summary_ko=candidate.summary_ko.strip(),
        supporting_span_ids=sorted(set(candidate.supporting_span_ids)),
    )


def _build_relationships(
    candidates: list[GuruOntologyCandidate],
    *,
    accepted_by_candidate_id: dict[str, str],
) -> list[GuruReviewedRelationship]:
    relationships: dict[str, GuruReviewedRelationship] = {}
    for candidate in candidates:
        from_id = accepted_by_candidate_id.get(candidate.candidate_id)
        if not from_id:
            continue
        for related_candidate_id in candidate.related_candidate_ids:
            to_id = accepted_by_candidate_id.get(related_candidate_id)
            if not to_id or to_id == from_id:
                continue
            relation_type = "requires_data" if candidate.object_type in CONSULTATION_TYPES and ":data_need:" in to_id else "informs"
            relationship_id = _relationship_id(from_id, to_id, relation_type)
            relationships.setdefault(
                relationship_id,
                GuruReviewedRelationship(
                    relationship_id=relationship_id,
                    from_id=from_id,
                    to_id=to_id,
                    relation_type=relation_type,  # type: ignore[arg-type]
                    explanation_ko="후보 추출 단계의 related_candidate_ids를 reviewed ID로 정규화한 연결입니다.",
                    source_candidate_id=candidate.candidate_id,
                ),
            )
    return sorted(relationships.values(), key=lambda item: item.relationship_id)


def _attach_reviewed_relationships(
    guru_objects: list[GuruReviewedObject],
    consultation_objects: list[GuruReviewedConsultationObject],
    data_needs: list[GuruReviewedDataNeed],
    corpus_metadata: list[GuruReviewedCorpusMetadata],
    relationships: list[GuruReviewedRelationship],
) -> None:
    related_by_id: dict[str, set[str]] = {}
    for relationship in relationships:
        related_by_id.setdefault(relationship.from_id, set()).add(relationship.to_id)
    for collection in (guru_objects, consultation_objects, data_needs, corpus_metadata):
        for item in collection:
            item.related_reviewed_ids = sorted(related_by_id.get(item.reviewed_id, set()))


def _reviewed_id(candidate: GuruOntologyCandidate) -> str:
    slug = _slug(candidate.label_en or candidate.label_ko)
    basis = {
        "candidate_id": candidate.candidate_id,
        "author_key": candidate.author_key,
        "object_type": candidate.object_type,
        "label_ko": candidate.label_ko,
        "supporting_span_ids": sorted(candidate.supporting_span_ids),
        "data_need_key": candidate.data_need_key,
    }
    digest = hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    return f"guru:{candidate.author_key}:{candidate.object_type}:{slug}:{digest}"


def _relationship_id(from_id: str, to_id: str, relation_type: str) -> str:
    digest = hashlib.sha256(f"{from_id}|{relation_type}|{to_id}".encode("utf-8")).hexdigest()[:12]
    return f"guru:relationship:{relation_type}:{digest}"


def _reject(
    candidate: GuruOntologyCandidate,
    stage: str,
    reason: str,
) -> GuruRejectedCandidate:
    return GuruRejectedCandidate(
        candidate_id=candidate.candidate_id,
        author_key=candidate.author_key,
        object_type=candidate.object_type,
        object_origin=candidate.object_origin,
        label_ko=candidate.label_ko,
        rejection_stage=stage,
        rejection_reason=reason,
        source_payload=candidate.model_dump(mode="json"),
    )


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _slug(value: str) -> str:
    ascii_text = value.encode("ascii", "ignore").decode("ascii").lower()
    if not ascii_text.strip():
        ascii_text = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug[:80] or "object"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp_path.replace(path)
