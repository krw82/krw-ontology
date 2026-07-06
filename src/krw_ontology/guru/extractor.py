"""Claude Agent SDK extraction boundary for the guru ontology pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Iterable

from krw_ontology.config.settings import PipelineConfig
from krw_ontology.errors import RateLimitError
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.guru.models import (
    ANSWER_SECTION_TYPES,
    DECISION_STAGES,
    GuruExtractionBatch,
    GuruExtractionManifest,
    GuruOntologyCandidate,
    GuruParsedManifest,
    GuruPrivateSpanRecord,
    INVESTOR_INTENT_FAMILIES,
    utc_now_iso,
)
from krw_ontology.guru.workspace import _write_json_atomic, guru_root, guru_running_root


EXTRACTION_STAGE_NAME = "guru_agent_sdk_concept_induction"
EXTRACTION_MANIFEST_PATH = Path("generated") / "extraction_manifest.json"
GURU_CANDIDATES_PATH = Path("generated") / "ontology_candidates.jsonl"
DEFAULT_BATCH_SPANS = 8
DEFAULT_BATCH_CHARS = 14_000
DEFAULT_AGENT_SDK_CONCURRENCY = 5
logger = logging.getLogger("krw_ontology")

_PROMPT_TEMPLATE = """\
You are inducing a standalone investor-guru consultation ontology from official letters and memos.

Batch id: {batch_id}

Core extraction rules:
- Infer the author's lens from the provided spans. Do not use a fixed investing
  checklist unless the text supports it.
- Build objects that support both standalone guru questions and investor
  consultation questions like "I already bought X; how would this guru examine
  it?", "Should I average down?", "What red flags would this guru check?",
  "Is this a value trap?", and "What data is missing?".
- Return only JSON matching the configured schema. The root object must have an
  items array.

Hard normalization rules:
- author_key must be exactly one of: buffett, marks, ackman, flatt, terry_smith.
- object_origin must be exactly one of: source_grounded, corpus_synthesized,
  consultation_derived, data_need, corpus_metadata.
- data_need_family must be exactly one of: future_company_metric,
  future_company_text, future_company_section, portfolio_context, user_context,
  guru_corpus.
- intent_family, decision_stage, and answer_section_type must use only values
  allowed by the configured schema. If none fits, leave the field null.
- supporting_span_ids may contain only span_id values present in the input
  payload. Never invent, shorten, renumber, or repair span ids.
- Keep full source text private. Refer to supporting_span_ids instead of quoting
  long text.

Candidate id rules:
- candidate_id is provisional but must be stable and unique within this batch.
- Never use generic sequential ids such as ackman-001, principle-1, item-3, or
  concept-01.
- Use this pattern:
  guru:{{author_key}}:{{object_type}}:{batch_id}:{{primary_span_token}}:{{short_label_slug}}
- primary_span_token is the first supporting span id with non-alphanumeric
  characters replaced by hyphens. Use "no-span" only for consultation_derived
  objects that have no direct span support.
- short_label_slug should be a short lowercase English slug. If the label is
  Korean, translate the core idea into a short English slug.

Grounding and origin rules:
- source_grounded: use for concepts, principles, heuristics, risk frames,
  valuation frames, behavioral warnings, and anti-patterns that are directly
  supported by one or more input spans. These objects must have at least one
  supporting_span_id.
- corpus_synthesized: use only when the batch clearly combines repeated ideas
  across multiple spans. Include all supporting spans used for the synthesis.
- consultation_derived: use for investor_intent, question_template,
  question_route, answer_playbook, answer_section, and clarifying_question
  objects. Prefer linking them to source-grounded objects in the same batch
  through related_candidate_ids.
- data_need: use only for missing data required to answer future investor
  questions. Set object_type to data_need and object_origin to data_need.
- Every data_need candidate must set data_need_family and data_need_key.
  data_need_family must be one of the allowed values above. data_need_key must
  be a compact snake_case key such as cash_flow_quality, segment_margin_trend,
  capital_allocation_history, risk_factors, portfolio_weight, or user_time_horizon.
- corpus_metadata: use for official index/archive spans and collection metadata
  only. If source_id contains "official_index", do not emit concept, principle,
  heuristic, risk_frame, valuation_frame, or anti_pattern from that span.

Consultation object rules:
- If supported by the spans, include a useful mix of investor_intent,
  question_template, answer_playbook, data_need, and clarifying_question
  objects. Do not force them when the span has no basis.
- For investor-facing objects, fill intent_family, intent_tags, decision_stage,
  question_pattern_ko, example_questions_ko, answer_section_type,
  answer_sections, required_context, requires_company_data,
  requires_portfolio_data, and requires_user_context where applicable.
- If a future company/ticker question would need company filing data, express
  that only as data_need or company_data_hooks. Do not assume, query, or bind
  company facts here.
- requires_company_data should be true only when the future answer needs
  company-specific filings, metrics, sections, or management commentary. It
  should be false for guru_corpus metadata or standalone guru-lens explanations.
- Do not reference existing KRW company ontology schemas or metric dictionaries
  by name.

Soft metadata rules:
- Soft metadata is for retrieval ranking and answer assembly only. It must not
  become a hard guru framework or a fixed checklist.
- Infer applicability, specificity, and answer_role from the provided spans.
  Do not fill these fields from generic knowledge about the author.
- Fill applicability.strong_for with question intents or situations where this
  object is directly useful; possible_for with weaker-but-reasonable uses;
  weak_for and anti_triggers with situations where the lens could mislead.
- Fill applicability.requires_clarification_when when the future answer should
  ask a short clarification before applying the lens, such as ambiguous asset
  wrapper, no ticker for a company judgment, or missing portfolio context.
- Fill specificity.level as general_principle only when the span supports
  reuse beyond the source case. Use sector_specific, asset_class_specific,
  company_case_specific, or document_context_specific when the idea is tightly
  tied to the source example.
- For specific source cases, set specificity.source_case_ko and compact
  source_case_tags. These are ranking hints, not exclusion filters.
- Fill answer_role.default with how the object should usually appear in a
  consultation answer: core_lens, supporting_lens, caution, checklist,
  data_need, contrast, or context. possible_roles may include alternatives.
- When a source span mixes a reusable principle with a specific company case,
  prefer separate candidates: one reusable principle with conservative
  generalization_confidence, and one case-specific/context object.
- If the span does not justify a metadata value, leave the relevant list empty
  and use confidence low or medium rather than inventing precision.

Korean quality rules:
- label_ko and summary_ko must be natural Korean for an investor-facing product.
- Avoid awkward literal translations. Do not write malformed words such as
  "기루".
- Make summaries concise, source-grounded, and non-advisory.

Span payload:
{span_payload_json}
"""


def load_parsed_manifest(running_root: Path | str | None = None) -> GuruParsedManifest:
    path = guru_running_root(running_root) / "parsed_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return GuruParsedManifest.model_validate(payload)


def extract_guru_ontology(
    root: Path | str | None = None,
    *,
    running_root: Path | str | None = None,
    execute_agent_sdk: bool = False,
    model: str | None = None,
    max_batches: int | None = None,
    batch_spans: int = DEFAULT_BATCH_SPANS,
    batch_chars: int = DEFAULT_BATCH_CHARS,
    concurrency: int = DEFAULT_AGENT_SDK_CONCURRENCY,
    worker: ExtractionWorker | None = None,
) -> GuruExtractionManifest:
    """Create Agent SDK extraction batches, optionally executing them."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    root_path = guru_root(root)
    running_path = guru_running_root(running_root)
    generated_dir = running_path / "generated"
    requests_dir = generated_dir / "requests"
    responses_dir = generated_dir / "responses"
    generated_dir.mkdir(parents=True, exist_ok=True)
    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)

    parsed_manifest = load_parsed_manifest(running_path)
    span_records = list(_load_span_records(parsed_manifest))
    batches = _build_batches(span_records, batch_spans=batch_spans, batch_chars=batch_chars)
    if max_batches is not None:
        batches = batches[:max_batches]

    extraction_batches: list[GuruExtractionBatch] = []
    for batch_index, spans in enumerate(batches, start=1):
        batch_id = f"guru-batch-{batch_index:04d}"
        prompt_path = requests_dir / f"{batch_id}.prompt.txt"
        output_path = responses_dir / f"{batch_id}.jsonl"
        prompt_text = build_extraction_prompt(spans, batch_id=batch_id)
        _write_text_atomic(prompt_path, prompt_text)
        extraction_batches.append(
            GuruExtractionBatch(
                batch_id=batch_id,
                span_ids=[span.span_id for span in spans],
                source_ids=sorted({span.source_id for span in spans}),
                prompt_path=str(prompt_path),
                output_path=str(output_path),
                status="planned",
                agent_sdk_called=False,
            )
        )

    candidates_path = running_path / GURU_CANDIDATES_PATH
    manifest = GuruExtractionManifest(
        generated_at=utc_now_iso(),
        root=str(root_path),
        running_root=str(running_path),
        execution_mode="agent_sdk" if execute_agent_sdk else "dry_run",
        agent_sdk_called=False,
        extraction_started=execute_agent_sdk,
        model=model,
        concurrency=concurrency,
        batches=extraction_batches,
        output_files={"ontology_candidates": str(candidates_path)},
    )
    if execute_agent_sdk:
        config = PipelineConfig.load()
        selected_model = model or config.model_for_stage(EXTRACTION_STAGE_NAME)
        active_worker = worker or ExtractionWorker(
            model=selected_model,
            cwd=running_path,
            max_retries=config.max_retries,
            call_timeout_s=config.call_timeout_seconds,
            max_turns=config.max_turns,
        )
        candidates = asyncio.run(
            _execute_batches(
                active_worker,
                extraction_batches,
                batches,
                selected_model,
                concurrency=concurrency,
            )
        )
        _write_jsonl_atomic(
            candidates_path,
            (candidate.model_dump(mode="json") for candidate in candidates),
        )
        manifest = manifest.model_copy(
            update={
                "agent_sdk_called": True,
                "model": selected_model,
                "batches": extraction_batches,
            }
        )
    _write_json_atomic(
        running_path / EXTRACTION_MANIFEST_PATH,
        manifest.model_dump(mode="json"),
    )
    return manifest


def build_extraction_prompt(
    spans: list[GuruPrivateSpanRecord],
    *,
    batch_id: str = "guru-batch-preview",
) -> str:
    payload = [
        {
            "span_id": span.span_id,
            "source_id": span.source_id,
            "author_key": span.author_key,
            "title": span.title,
            "section_title": span.section_title,
            "text": span.text,
        }
        for span in spans
    ]
    return _PROMPT_TEMPLATE.format(
        batch_id=batch_id,
        span_payload_json=json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    )


async def _execute_batches(
    worker: ExtractionWorker,
    extraction_batches: list[GuruExtractionBatch],
    span_batches: list[list[GuruPrivateSpanRecord]],
    model: str,
    *,
    concurrency: int,
) -> list[GuruOntologyCandidate]:
    item_schema = GuruOntologyCandidate.model_json_schema()
    semaphore = asyncio.Semaphore(concurrency)

    async def execute_one(
        batch_index: int,
        batch: GuruExtractionBatch,
        spans: list[GuruPrivateSpanRecord],
    ) -> list[GuruOntologyCandidate]:
        existing_candidates = _read_existing_batch_candidates(Path(batch.output_path))
        if existing_candidates is not None:
            batch.status = "completed"
            batch.agent_sdk_called = True
            batch.error = None
            return existing_candidates

        async with semaphore:
            existing_candidates = _read_existing_batch_candidates(Path(batch.output_path))
            if existing_candidates is not None:
                batch.status = "completed"
                batch.agent_sdk_called = True
                batch.error = None
                return existing_candidates

            try:
                batch_candidates, split_errors = await _extract_spans_with_split_retry(
                    worker=worker,
                    spans=spans,
                    item_schema=item_schema,
                    batch_index=batch_index,
                    batch_id=batch.batch_id,
                    model=model,
                )
                _write_jsonl_atomic(
                    Path(batch.output_path),
                    (candidate.model_dump(mode="json") for candidate in batch_candidates),
                )
                batch.status = "completed" if batch_candidates else "error"
                batch.agent_sdk_called = True
                batch.error = (
                    f"{len(split_errors)} split sub-batches failed: "
                    + " | ".join(split_errors[:3])
                    if split_errors
                    else None
                )
                return batch_candidates
            except Exception as exc:
                batch.status = "error"
                batch.agent_sdk_called = True
                batch.error = str(exc)
                return []

    batch_results = await asyncio.gather(
        *(
            execute_one(batch_index, batch, spans)
            for batch_index, (batch, spans) in enumerate(
                zip(extraction_batches, span_batches), start=1
            )
        )
    )
    return [candidate for candidates in batch_results for candidate in candidates]


async def _extract_spans_with_split_retry(
    *,
    worker: ExtractionWorker,
    spans: list[GuruPrivateSpanRecord],
    item_schema: dict,
    batch_index: int,
    batch_id: str,
    model: str,
    split_depth: int = 0,
) -> tuple[list[GuruOntologyCandidate], list[str]]:
    if not spans:
        return [], []
    try:
        items = await worker.extract(
            _PROMPT_TEMPLATE,
            {
                "batch_id": batch_id,
                "span_payload_json": _span_payload_json(spans),
            },
            item_schema,
            EXTRACTION_STAGE_NAME,
            call_metadata={
                "job_id": "guru-ontology",
                "batch_index": batch_index,
                "batch_id": batch_id,
                "model": model,
                "split_retry": split_depth > 0,
                "split_depth": split_depth,
                "span_count": len(spans),
            },
        )
        return _candidate_models_from_items(items, batch_id), []
    except RateLimitError:
        # Provider capacity failures are already retried by ExtractionWorker.
        # Splitting them would increase load without improving recovery.
        raise
    except Exception as exc:
        if len(spans) <= 1:
            logger.error(
                "%s leaf guru batch %s failed after split retry: %s",
                EXTRACTION_STAGE_NAME,
                batch_id,
                exc,
                extra={"stage": EXTRACTION_STAGE_NAME, "batch_id": batch_id},
            )
            return [], [f"{batch_id}: {exc}"]

        midpoint = max(1, len(spans) // 2)
        logger.warning(
            "%s guru batch %s failed; retrying as %s and %s span sub-batches: %s",
            EXTRACTION_STAGE_NAME,
            batch_id,
            midpoint,
            len(spans) - midpoint,
            exc,
            extra={"stage": EXTRACTION_STAGE_NAME, "batch_id": batch_id},
        )
        left_candidates, left_errors = await _extract_spans_with_split_retry(
            worker=worker,
            spans=spans[:midpoint],
            item_schema=item_schema,
            batch_index=batch_index * 10 + 1,
            batch_id=f"{batch_id}-s{split_depth + 1}a",
            model=model,
            split_depth=split_depth + 1,
        )
        right_candidates, right_errors = await _extract_spans_with_split_retry(
            worker=worker,
            spans=spans[midpoint:],
            item_schema=item_schema,
            batch_index=batch_index * 10 + 2,
            batch_id=f"{batch_id}-s{split_depth + 1}b",
            model=model,
            split_depth=split_depth + 1,
        )
        return [*left_candidates, *right_candidates], [*left_errors, *right_errors]


def _candidate_models_from_items(
    items: Iterable[object],
    batch_id: str,
) -> list[GuruOntologyCandidate]:
    candidates: list[GuruOntologyCandidate] = []
    skipped = 0
    saw_item = False
    for item in items:
        saw_item = True
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            candidates.append(
                GuruOntologyCandidate.model_validate(_normalize_candidate_payload(item))
            )
        except Exception as exc:
            skipped += 1
            logger.warning(
                "guru extraction skipped invalid candidate",
                extra={
                    "stage": EXTRACTION_STAGE_NAME,
                    "batch_id": batch_id,
                    "candidate_id": item.get("candidate_id"),
                    "error": str(exc),
                },
            )
    if saw_item and not candidates:
        raise ValueError(f"{batch_id}: all extracted candidates were invalid")
    if skipped:
        logger.warning(
            "guru extraction skipped %s invalid candidates in %s",
            skipped,
            batch_id,
            extra={"stage": EXTRACTION_STAGE_NAME, "batch_id": batch_id},
        )
    return candidates


def _read_existing_batch_candidates(path: Path) -> list[GuruOntologyCandidate] | None:
    if not path.is_file():
        return None
    try:
        candidates: list[GuruOntologyCandidate] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    return None
                candidates.append(
                    GuruOntologyCandidate.model_validate(
                        _normalize_candidate_payload(payload)
                    )
                )
        return candidates
    except Exception:
        return None


def _load_span_records(parsed_manifest: GuruParsedManifest) -> Iterable[GuruPrivateSpanRecord]:
    for document in parsed_manifest.parsed_documents:
        if document.status != "parsed" or not document.spans_path:
            continue
        spans_path = Path(document.spans_path)
        if not spans_path.is_file():
            continue
        with spans_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                yield GuruPrivateSpanRecord.model_validate_json(line)


def _is_corpus_metadata_span(span: GuruPrivateSpanRecord) -> bool:
    """Index/archive spans carry link listings, not substantive guru content."""
    return "official_index" in span.source_id


def _build_batches(
    spans: list[GuruPrivateSpanRecord],
    *,
    batch_spans: int,
    batch_chars: int,
) -> list[list[GuruPrivateSpanRecord]]:
    # Index/archive spans (official_index) are link listings that consume batch
    # budget without yielding lenses, principles, or consultation objects.
    # Exclude them from extraction batches; corpus metadata is recorded in the
    # raw/parsed manifests and can be synthesized during curation if needed.
    substantive = [span for span in spans if not _is_corpus_metadata_span(span)]
    batches: list[list[GuruPrivateSpanRecord]] = []
    current: list[GuruPrivateSpanRecord] = []
    current_chars = 0
    for span in substantive:
        span_chars = len(span.text)
        if current and (len(current) >= batch_spans or current_chars + span_chars > batch_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(span)
        current_chars += span_chars
    if current:
        batches.append(current)
    return batches


def _span_payload_json(spans: list[GuruPrivateSpanRecord]) -> str:
    return json.dumps(
        [
            {
                "span_id": span.span_id,
                "source_id": span.source_id,
                "author_key": span.author_key,
                "title": span.title,
                "section_title": span.section_title,
                "text": span.text,
            }
            for span in spans
        ],
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


_CANDIDATE_FIELDS = set(GuruOntologyCandidate.model_fields)
_OBJECT_TYPE_ALIASES = {
    "thesis": "decision_criterion",
    "investment_thesis": "decision_criterion",
    "checklist": "question_template",
    "warning": "behavioral_warning",
    "risk": "risk_frame",
    "valuation": "valuation_frame",
}
_LIST_FIELDS = {
    "supporting_span_ids",
    "related_candidate_ids",
    "intent_tags",
    "example_questions_ko",
    "answer_sections",
    "required_context",
}
_INTENT_FAMILY_ALIASES = {
    "buy_decision": "considering_buy",
    "buy_review": "considering_buy",
    "pre_buy_review": "considering_buy",
    "quality_screen": "business_quality_check",
    "quality_stock_selection": "business_quality_check",
    "business_quality": "business_quality_check",
    "risk_assessment": "risk_check",
    "risk_review": "risk_check",
    "downside_review": "risk_check",
    "capital_allocation": "capital_allocation_check",
    "valuation": "valuation_check",
    "valuation_review": "valuation_check",
    "holding_discipline": "holding_review",
    "holding_review_question": "holding_review",
    "average_down_decision": "average_down",
    "position_size": "position_sizing",
    "position_sizing_review": "position_sizing",
    "thesis_check": "thesis_review",
    "activist_review": "thesis_review",
    "contrarian_review": "contrarian_check",
    "red_flag_review": "red_flag_check",
    "learning": "learn_guru_view",
    "principle_explanation": "learn_guru_view",
}
_DECISION_STAGE_ALIASES = {
    "buy": "pre_buy",
    "pre_purchase": "pre_buy",
    "purchase": "pre_buy",
    "owning": "holding",
    "hold": "holding",
    "long_term_holding": "holding",
    "add": "add_or_average_down",
    "average_down": "add_or_average_down",
    "sell": "trim_or_sell",
    "trim": "trim_or_sell",
}
_ANSWER_SECTION_ALIASES = {
    "business_quality": "business_quality_checks",
    "valuation": "valuation_checks",
    "risk": "risk_checks",
    "capital_allocation": "capital_allocation_checks",
    "management": "management_checks",
    "positive": "positive_signals",
    "negative": "negative_signals",
    "missing_context": "missing_context_questions",
    "decision_frame": "non_advisory_decision_frame",
}
_CONFIDENCE_ALIASES = {
    "very_high": "high",
    "strong": "high",
    "med": "medium",
    "moderate": "medium",
    "weak": "low",
}
_ANSWER_ROLE_ALIASES = {
    "primary": "core_lens",
    "main": "core_lens",
    "lens": "core_lens",
    "secondary": "supporting_lens",
    "warning": "caution",
    "risk_warning": "caution",
    "question": "checklist",
    "questions": "checklist",
    "evidence_need": "data_need",
    "data": "data_need",
}


def _normalize_candidate_payload(payload: dict) -> dict:
    cleaned = {key: value for key, value in payload.items() if key in _CANDIDATE_FIELDS}
    object_type = str(cleaned.get("object_type") or "").strip()
    if object_type:
        normalized_object_type = object_type.lower().replace("-", "_").replace(" ", "_")
        cleaned["object_type"] = _OBJECT_TYPE_ALIASES.get(
            normalized_object_type,
            normalized_object_type,
        )
    for field in _LIST_FIELDS:
        cleaned[field] = _as_string_list(cleaned.get(field))
    cleaned["company_data_hooks"] = _normalize_company_data_hooks(
        cleaned.get("company_data_hooks")
    )
    cleaned["intent_family"] = _canonical_optional(
        cleaned.get("intent_family"),
        _INTENT_FAMILY_ALIASES,
        set(INVESTOR_INTENT_FAMILIES),
    )
    cleaned["decision_stage"] = _canonical_optional(
        cleaned.get("decision_stage"),
        _DECISION_STAGE_ALIASES,
        set(DECISION_STAGES),
    )
    cleaned["answer_section_type"] = _canonical_optional(
        cleaned.get("answer_section_type"),
        _ANSWER_SECTION_ALIASES,
        set(ANSWER_SECTION_TYPES),
    )
    cleaned["confidence"] = _canonical_confidence(cleaned.get("confidence"))
    cleaned["applicability"] = _normalize_applicability(cleaned.get("applicability"))
    cleaned["specificity"] = _normalize_specificity(cleaned.get("specificity"))
    cleaned["answer_role"] = _normalize_answer_role(cleaned.get("answer_role"))
    if cleaned.get("object_type") == "data_need":
        _normalize_data_need_fields(cleaned)
    return cleaned


def _normalize_data_need_fields(cleaned: dict) -> None:
    family = _canonical_data_need_family(cleaned.get("data_need_family"), cleaned)
    cleaned["data_need_family"] = family
    key = cleaned.get("data_need_key")
    if not key:
        key = _data_need_key_from_payload(cleaned)
    cleaned["data_need_key"] = _snake_key(str(key or "missing_data"))
    cleaned["object_origin"] = "data_need"
    answer_role = dict(cleaned.get("answer_role") or {})
    if not answer_role.get("default") or answer_role.get("default") == "supporting_lens":
        answer_role["default"] = "data_need"
    possible_roles = set(_as_string_list(answer_role.get("possible_roles")))
    possible_roles.add("checklist")
    answer_role["possible_roles"] = sorted(possible_roles)
    cleaned["answer_role"] = answer_role


def _canonical_data_need_family(value: object, payload: dict) -> str:
    allowed = {
        "future_company_metric",
        "future_company_text",
        "future_company_section",
        "portfolio_context",
        "user_context",
        "guru_corpus",
    }
    raw = str(value or "").strip()
    if raw in allowed:
        return raw
    text = " ".join(
        str(payload.get(key) or "")
        for key in (
            "label_ko",
            "label_en",
            "summary_ko",
            "body_ko",
            "data_need_key",
        )
    ).lower()
    text += " " + " ".join(_as_string_list(payload.get("required_context"))).lower()
    text += " " + " ".join(str(item) for item in payload.get("company_data_hooks") or []).lower()
    if any(token in text for token in ("portfolio", "포트폴리오", "비중", "weight")):
        return "portfolio_context"
    if any(token in text for token in ("user", "사용자", "매수", "평균단가", "기간", "horizon")):
        return "user_context"
    if payload.get("requires_company_data") or payload.get("company_data_hooks"):
        return "future_company_metric"
    if any(token in text for token in ("section", "risk_factors", "md&a", "공시 섹션", "위험요인")):
        return "future_company_section"
    if any(token in text for token in ("management", "letter", "commentary", "주석", "서술")):
        return "future_company_text"
    return "guru_corpus"


def _data_need_key_from_payload(payload: dict) -> str:
    hooks = payload.get("company_data_hooks") or []
    for hook in hooks:
        if isinstance(hook, dict):
            for key in ("key", "metric", "section", "family"):
                value = hook.get(key)
                if value:
                    return str(value)
    for key in ("label_en", "label_ko", "summary_ko"):
        value = payload.get(key)
        if value:
            return str(value)
    return "missing_data"


def _snake_key(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "_" for char in value)
    parts = [part for part in normalized.split("_") if part]
    return "_".join(parts)[:80] or "missing_data"


def _normalize_applicability(value: object) -> dict:
    metadata = value if isinstance(value, dict) else {}
    return {
        "strong_for": _as_string_list(metadata.get("strong_for")),
        "possible_for": _as_string_list(metadata.get("possible_for")),
        "weak_for": _as_string_list(metadata.get("weak_for")),
        "anti_triggers": _as_string_list(metadata.get("anti_triggers")),
        "requires_clarification_when": _as_string_list(
            metadata.get("requires_clarification_when")
        ),
        "confidence": _canonical_confidence(metadata.get("confidence")),
    }


def _normalize_specificity(value: object) -> dict:
    metadata = value if isinstance(value, dict) else {}
    level = str(metadata.get("level") or "").strip()
    valid_levels = {
        "general_principle",
        "sector_specific",
        "asset_class_specific",
        "company_case_specific",
        "document_context_specific",
    }
    if level not in valid_levels:
        level = "general_principle"
    source_case_ko = metadata.get("source_case_ko")
    return {
        "level": level,
        "source_case_ko": str(source_case_ko).strip() if source_case_ko else None,
        "source_case_tags": _as_string_list(metadata.get("source_case_tags")),
        "generalization_confidence": _canonical_confidence(
            metadata.get("generalization_confidence")
        ),
    }


def _normalize_answer_role(value: object) -> dict:
    metadata = value if isinstance(value, dict) else {}
    default = _canonical_answer_role(metadata.get("default"))
    possible_roles = [
        role
        for role in (_canonical_answer_role(item) for item in _as_string_list(metadata.get("possible_roles")))
        if role
    ]
    return {
        "default": default or "supporting_lens",
        "possible_roles": sorted(set(possible_roles)),
        "confidence": _canonical_confidence(metadata.get("confidence")),
    }


def _normalize_company_data_hooks(value: object) -> list[dict[str, str]]:
    hooks = []
    for item in _as_list(value):
        if isinstance(item, dict):
            cleaned = {str(key): str(val) for key, val in item.items() if val is not None}
            if cleaned:
                hooks.append(cleaned)
        elif item is not None:
            hooks.append({"key": str(item), "reason": "agent_sdk_string_hook"})
    return hooks


def _as_string_list(value: object) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _as_list(value: object) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _canonical_optional(
    value: object,
    aliases: dict[str, str],
    valid_values: set[str],
) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip()
    canonical = aliases.get(normalized, aliases.get(normalized.lower(), normalized))
    return canonical if canonical in valid_values else None


def _canonical_confidence(value: object) -> str:
    if value in (None, ""):
        return "medium"
    if isinstance(value, (int, float)):
        if float(value) >= 0.75:
            return "high"
        if float(value) <= 0.35:
            return "low"
        return "medium"
    normalized = str(value).strip().lower()
    return _CONFIDENCE_ALIASES.get(normalized, normalized if normalized in {"low", "medium", "high"} else "medium")


def _canonical_answer_role(value: object) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip()
    lowered = normalized.lower()
    valid_roles = {
        "core_lens",
        "supporting_lens",
        "caution",
        "checklist",
        "data_need",
        "contrast",
        "context",
    }
    return _ANSWER_ROLE_ALIASES.get(lowered, normalized if normalized in valid_roles else None)


def _write_text_atomic(path: Path, text: str) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _write_jsonl_atomic(path: Path, rows: Iterable[dict]) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp_path.replace(path)
