"""Serving-layer bridge between guru lenses and company filing research.

This module does not import or mutate the KRW company ontology. It only turns
Guru ResearchPack evidence needs into bounded payloads that an application
orchestrator can pass to the existing company filing MCP.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
import re
from typing import Any

from krw_ontology.guru.context_taxonomy import (
    context_tags_for_text,
    filing_topic_filter_reason,
)
from krw_ontology.guru.company_context import (
    coerce_company_context,
    company_context_topics,
)
from krw_ontology.guru.models import (
    AUTHOR_KEYS,
    GuruCompanyEvidenceAlignment,
    GuruCompanyFilingBrief,
    GuruCompanyIdentity,
    GuruCompanyOntologyContext,
    GuruCompanyResearchPack,
)


TOPIC_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "business_model": (
        "business model",
        "segments",
        "revenue drivers",
        "competitive position",
        "사업 모델",
    ),
    "cash_flow": (
        "operating cash flow",
        "free cash flow",
        "capital expenditures",
        "working capital",
        "현금흐름",
    ),
    "cash_generation": (
        "cash generation",
        "operating cash flow",
        "free cash flow",
        "cash conversion",
        "현금창출",
    ),
    "capital_allocation": (
        "capital allocation",
        "reinvestment",
        "acquisitions",
        "dividends",
        "share repurchases",
        "자본배분",
    ),
    "share_repurchases": ("share repurchases", "buybacks", "treasury stock", "자사주"),
    "dividends": ("dividends", "payout", "배당"),
    "reinvestment": ("reinvestment", "capex", "growth investment", "재투자"),
    "balance_sheet": (
        "debt",
        "liquidity",
        "debt maturity",
        "interest expense",
        "leverage",
        "대차대조표",
    ),
    "risk_factors": (
        "risk factors",
        "regulatory risk",
        "commodity price risk",
        "competition",
        "리스크 요인",
    ),
    "valuation_context": (
        "valuation drivers",
        "earnings power",
        "margin durability",
        "growth duration",
        "밸류에이션",
    ),
    "margin_structure": ("gross margin", "operating margin", "cost structure", "마진"),
    "growth_duration": ("growth durability", "long-term growth", "market demand", "성장 지속성"),
    "demand_cycle_exposure": ("demand cycle", "cyclical demand", "end-market demand", "수요 사이클"),
    "margin_pressure": ("margin pressure", "pricing", "input costs", "cost inflation", "마진 압박"),
    "liquidity": ("liquidity", "cash and equivalents", "credit facility", "유동성"),
    "capital_intensity": ("capital intensity", "capex", "maintenance capital", "자본집약도"),
    "services_mix": ("services mix", "segment mix", "recurring revenue", "서비스 믹스"),
    "customer_demand": ("customer demand", "unit sales", "retention", "고객 수요"),
    "commodity_price_exposure": (
        "commodity price exposure",
        "oil price",
        "gas price",
        "hedging",
        "원자재 가격",
    ),
}

TOPIC_KO_LABELS: dict[str, str] = {
    "business_model": "사업 모델",
    "cash_flow": "영업현금흐름과 자유현금흐름",
    "cash_generation": "현금창출력",
    "capital_allocation": "자본배분",
    "share_repurchases": "자사주",
    "dividends": "배당",
    "reinvestment": "재투자",
    "balance_sheet": "부채와 유동성",
    "risk_factors": "리스크 요인",
    "valuation_context": "가격 판단에 필요한 사업 근거",
    "margin_structure": "마진 구조",
    "growth_duration": "성장 지속성",
    "demand_cycle_exposure": "수요 사이클 노출",
    "margin_pressure": "마진 압박",
    "liquidity": "유동성",
    "capital_intensity": "자본집약도",
    "services_mix": "서비스 믹스",
    "customer_demand": "고객 수요",
    "commodity_price_exposure": "원자재 가격 민감도",
}

def company_filing_brief_from_guru_lens(
    guru_payload: Mapping[str, Any],
    *,
    ticker: str | None = None,
    company_name: str | None = None,
    company_context: GuruCompanyOntologyContext | Mapping[str, Any] | None = None,
    author_key: str | None = None,
    question: str | None = None,
) -> GuruCompanyFilingBrief:
    """Translate a Guru ResearchPack or query-context payload into a filing brief."""

    payload = _mapping(guru_payload)
    pack = _research_pack(payload)
    selected_author = _author_key(author_key, pack)
    original_question = (
        _clean_optional(question)
        or _clean_optional(_get_nested(pack, ("pack_meta", "question")))
        or _clean_optional(payload.get("question"))
        or ""
    )
    bridge = _mapping(pack.get("company_bridge") or payload.get("company_bridge"))
    resolved_ticker = _clean_optional(ticker) or _clean_optional(bridge.get("ticker"))
    resolved_company_name = _clean_optional(company_name)
    subject = _company_subject(resolved_ticker, resolved_company_name)
    context_model = coerce_company_context(
        company_context or pack.get("company_context") or payload.get("company_context") or None,
        ticker=resolved_ticker,
        company_name=resolved_company_name,
    )
    requires_company = bool(
        bridge.get("requires_company_evidence")
        or payload.get("requires_company_evidence")
        or resolved_ticker
        or resolved_company_name
    )
    requires_identifier = bool(bridge.get("requires_identifier_clarification"))
    generic_requirements = _unique_strings(
        bridge.get("filing_evidence_requirements")
        or payload.get("filing_evidence_requirements")
        or []
    )
    conditional_requirements = _unique_strings(
        bridge.get("conditional_filing_evidence_requirements") or []
    )
    if not generic_requirements and requires_company:
        generic_requirements = conditional_requirements
    lens_specific = _lens_specific_evidence_requests(pack)
    context_topics = company_context_topics(context_model)
    candidate_topics = _unique_strings([*context_topics, *generic_requirements, *lens_specific])
    if "energy" in _context_tags(original_question, subject, context_model):
        candidate_topics = _unique_strings([*candidate_topics, "commodity_price_exposure"])
    topic_filter = _filter_filing_topics(
        topics=candidate_topics,
        question=original_question,
        subject=subject,
        company_context=context_model,
        generic_requirements=generic_requirements,
    )
    required_topics = topic_filter["required"]
    filtered_topics = topic_filter["filtered"]
    data_need_keys = _data_need_keys(pack)
    query_data_need_keys = _filter_query_data_need_keys(
        data_need_keys,
        question=original_question,
        subject=subject,
        company_context=context_model,
    )
    query_terms = _query_terms(
        subject=subject,
        ticker=resolved_ticker,
        company_name=resolved_company_name,
        topics=required_topics,
        data_need_keys=query_data_need_keys,
    )
    missing_inputs = _missing_inputs(
        requires_company=requires_company,
        requires_identifier=requires_identifier,
        ticker=resolved_ticker,
        company_name=resolved_company_name,
    )
    question_ko = _company_question_ko(
        subject=subject,
        original_question=original_question,
        topics=required_topics or conditional_requirements,
    )
    question_en = _company_question_en(
        subject=subject,
        original_question=original_question,
        topics=required_topics or conditional_requirements,
        query_terms=query_terms,
    )
    recommended_call = {
        "tool": "krw_ontology_query_context",
        "when": (
            "after_identifier_resolution"
            if requires_identifier
            else "now"
            if requires_company and not missing_inputs
            else "only_after_company_or_ticker_is_selected"
        ),
        "arguments": {
            "question": question_ko,
            "ticker": resolved_ticker,
        },
        "policy": "Application/orchestrator calls the company MCP; Guru MCP remains read-only lens context.",
    }
    return GuruCompanyFilingBrief(
        author_key=selected_author,
        original_question=original_question,
        company_identity=GuruCompanyIdentity(
            ticker=resolved_ticker,
            company_name=resolved_company_name,
            subject=subject,
            unresolved=requires_identifier or bool(missing_inputs),
        ),
        requires_company_evidence=requires_company,
        requires_identifier_clarification=requires_identifier,
        company_context=(
            context_model.model_dump(mode="json", exclude_none=True)
            if context_model is not None
            else {}
        ),
        company_research_question_ko=question_ko,
        company_research_question_en=question_en,
        query_terms=query_terms,
        required_filing_topics=required_topics if requires_company else [],
        candidate_filing_topics=candidate_topics if requires_company else [],
        filtered_out_topics=filtered_topics if requires_company else [],
        lens_specific_evidence_requests=lens_specific,
        generic_filing_requirements=generic_requirements,
        data_need_keys=data_need_keys,
        missing_inputs=missing_inputs,
        recommended_company_mcp_call=recommended_call,
    )


def build_guru_company_research_pack(
    guru_payload: Mapping[str, Any],
    *,
    company_evidence_payload: Mapping[str, Any] | None = None,
    company_filing_brief: GuruCompanyFilingBrief | Mapping[str, Any] | None = None,
    ticker: str | None = None,
    company_name: str | None = None,
    company_context: GuruCompanyOntologyContext | Mapping[str, Any] | None = None,
    author_key: str | None = None,
    question: str | None = None,
) -> GuruCompanyResearchPack:
    """Join a Guru ResearchPack with optional opaque company evidence."""

    payload = _mapping(guru_payload)
    pack = _research_pack(payload)
    brief = _coerce_brief(
        company_filing_brief,
        guru_payload=payload,
        ticker=ticker,
        company_name=company_name,
        company_context=company_context,
        author_key=author_key,
        question=question,
    )
    company_pack = dict(company_evidence_payload or {})
    evidence_text = _json_text(company_pack)
    selected_lenses = _list_of_mappings(pack.get("selected_lenses"))
    required_topics = list(brief.required_filing_topics)
    alignments = [
        _alignment_for_lens(lens, required_topics=required_topics, evidence_text=evidence_text)
        for lens in selected_lenses
    ]
    if not selected_lenses and required_topics:
        alignments.append(
            GuruCompanyEvidenceAlignment(
                required_company_evidence=required_topics,
                status="pending_company_research" if not company_pack else "missing",
                reason_ko="선택된 렌즈는 없지만 회사 공시 확인 항목은 남아 있습니다.",
            )
        )
    missing_evidence = _missing_evidence(
        required_topics,
        evidence_text,
        has_company_pack=bool(company_pack),
    )
    labels = [str(lens.get("label_ko")) for lens in selected_lenses if lens.get("label_ko")]
    return GuruCompanyResearchPack(
        user_question=brief.original_question,
        author_key=brief.author_key,
        company_identity=brief.company_identity,
        guru_pack=dict(pack),
        company_context=dict(brief.company_context),
        company_filing_brief=brief.model_dump(mode="json", exclude_none=True),
        company_evidence_pack=company_pack,
        evidence_alignment=alignments,
        missing_evidence=missing_evidence,
        judgment_conditions={
            "can_discuss_now": labels,
            "needs_company_evidence_before_company_judgment": missing_evidence,
            "decision_change_triggers": _decision_change_triggers(missing_evidence),
        },
        render_hints={
            "author_key": brief.author_key,
            "voice_source": "skill-local answer-style.md plus Guru ResearchPack persona_profile",
            "use_first_person_simulated_voice": True,
            "avoid_footer_disclaimer": True,
            "separate_lens_from_company_facts": True,
        },
        answer_contract={
            "must_include": [
                "selected_guru_material",
                "filing_supported_company_facts_when_available",
                "missing_evidence_when_company_judgment_is_incomplete",
                "one_practical_next_question",
            ],
            "must_not_include": [
                "footer_disclaimer",
                "internal_tool_names",
                "ResearchPack_or_MCP_terms",
                "company_facts_not_present_in_company_evidence_pack",
            ],
        },
        boundaries=[
            "Guru pack supplies lens context only.",
            "Company evidence pack is opaque read-only filing evidence from the company research path.",
            "This pack does not mutate or extend the company ontology schema.",
        ],
    )


def _coerce_brief(
    value: GuruCompanyFilingBrief | Mapping[str, Any] | None,
    *,
    guru_payload: Mapping[str, Any],
    ticker: str | None,
    company_name: str | None,
    company_context: GuruCompanyOntologyContext | Mapping[str, Any] | None,
    author_key: str | None,
    question: str | None,
) -> GuruCompanyFilingBrief:
    if isinstance(value, GuruCompanyFilingBrief):
        return value
    if isinstance(value, Mapping):
        return GuruCompanyFilingBrief.model_validate(value)
    return company_filing_brief_from_guru_lens(
        guru_payload,
        ticker=ticker,
        company_name=company_name,
        company_context=company_context,
        author_key=author_key,
        question=question,
    )


def _alignment_for_lens(
    lens: Mapping[str, Any],
    *,
    required_topics: Sequence[str],
    evidence_text: str,
) -> GuruCompanyEvidenceAlignment:
    lens_requirements = _unique_strings(
        lens.get("company_evidence_requirements")
        or lens.get("conditional_company_evidence_requirements")
        or required_topics
    )
    if not lens_requirements:
        return GuruCompanyEvidenceAlignment(
            lens_id=_clean_optional(lens.get("reviewed_id")),
            lens_label_ko=_clean_optional(lens.get("label_ko")),
            status="not_required",
            reason_ko="이 렌즈는 현재 회사 공시 근거를 직접 요구하지 않습니다.",
        )
    if not evidence_text:
        status = "pending_company_research"
        reason = "회사 공시 근거가 아직 조립되지 않았습니다."
    else:
        supported = [topic for topic in lens_requirements if _topic_supported(topic, evidence_text)]
        if len(supported) == len(lens_requirements):
            status = "supported"
            reason = "필요한 회사 공시 근거가 모두 연결되었습니다."
        elif supported:
            status = "partial"
            reason = "일부 회사 공시 근거만 연결되었습니다."
        else:
            status = "missing"
            reason = "필요한 회사 공시 근거를 현재 payload에서 찾지 못했습니다."
    return GuruCompanyEvidenceAlignment(
        lens_id=_clean_optional(lens.get("reviewed_id")),
        lens_label_ko=_clean_optional(lens.get("label_ko")),
        required_company_evidence=list(lens_requirements),
        status=status,
        reason_ko=reason,
    )


def _missing_evidence(
    required_topics: Sequence[str],
    evidence_text: str,
    *,
    has_company_pack: bool,
) -> list[str]:
    if not has_company_pack:
        return list(required_topics)
    return [topic for topic in required_topics if not _topic_supported(topic, evidence_text)]


def _filter_filing_topics(
    *,
    topics: Sequence[str],
    question: str,
    subject: str,
    company_context: GuruCompanyOntologyContext | Mapping[str, Any] | None,
    generic_requirements: Sequence[str],
) -> dict[str, Any]:
    required: list[str] = []
    filtered: list[dict[str, str]] = []
    generic_set = set(generic_requirements)
    context_tags = _context_tags(question, subject, company_context)
    for topic in topics:
        if topic in generic_set:
            required.append(topic)
            continue
        reason = _topic_filter_reason(topic, context_tags=context_tags)
        if reason:
            filtered.append({"topic": topic, "reason": reason})
            continue
        required.append(topic)
    return {
        "required": _unique_strings(required),
        "filtered": filtered,
    }


def _filter_query_data_need_keys(
    keys: Sequence[str],
    *,
    question: str,
    subject: str,
    company_context: GuruCompanyOntologyContext | Mapping[str, Any] | None,
) -> list[str]:
    context_tags = _context_tags(question, subject, company_context)
    return [
        key
        for key in keys
        if not _topic_filter_reason(key, context_tags=context_tags)
    ]


def _topic_filter_reason(topic: str, *, context_tags: set[str]) -> str | None:
    reason = filing_topic_filter_reason(topic, context_tags=context_tags)
    if reason:
        return reason
    if len(topic) > 48 and re.search(r"[가-힣]", topic):
        return "topic is a long natural-language hook; keep it as candidate context, not a required filing topic"
    return None


def _context_tags(
    question: str,
    subject: str,
    company_context: GuruCompanyOntologyContext | Mapping[str, Any] | None,
) -> set[str]:
    context_model = (
        company_context
        if isinstance(company_context, GuruCompanyOntologyContext)
        else coerce_company_context(company_context)
    )
    context_text = ""
    if context_model is not None:
        context_text = " ".join(
            [
                *context_model.context_terms,
                *context_model.business_context_terms,
                *context_model.risk_context_terms,
                *context_model.available_company_topics,
                *context_model.context_tags,
            ]
        )
    return context_tags_for_text(f"{question} {subject} {context_text}")


def _decision_change_triggers(missing_evidence: Sequence[str]) -> list[str]:
    return [
        f"{_topic_ko(topic)} 근거가 확인되거나 반대로 악화될 때 판단을 다시 봅니다."
        for topic in missing_evidence[:6]
    ]


def _research_pack(payload: Mapping[str, Any]) -> dict[str, Any]:
    pack = payload.get("research_pack")
    if isinstance(pack, Mapping):
        return dict(pack)
    if payload.get("format") == "krw-guru-research-pack/v1":
        return dict(payload)
    return dict(payload)


def _author_key(author_key: str | None, pack: Mapping[str, Any]) -> str:
    if author_key in AUTHOR_KEYS:
        return str(author_key)
    guru_keys = _get_nested(pack, ("pack_meta", "guru_keys"))
    if isinstance(guru_keys, Sequence) and not isinstance(guru_keys, (str, bytes)):
        for key in guru_keys:
            if key in AUTHOR_KEYS:
                return str(key)
    return "buffett"


def _company_subject(ticker: str | None, company_name: str | None) -> str:
    if ticker and company_name:
        return f"{ticker} {company_name}"
    if ticker:
        return ticker
    if company_name:
        return company_name
    return "selected company"


def _lens_specific_evidence_requests(pack: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for row in _list_of_mappings(pack.get("data_needs")):
        values.extend(_normalized_hooks(row.get("company_data_hooks") or []))
        key = _clean_optional(row.get("data_need_key"))
        if key:
            values.append(key)
    for lens in _list_of_mappings(pack.get("selected_lenses")):
        values.extend(_unique_strings(lens.get("company_evidence_requirements") or []))
        values.extend(_unique_strings(lens.get("conditional_company_evidence_requirements") or []))
        for need in _list_of_mappings(lens.get("related_data_needs")):
            values.extend(_normalized_hooks(need.get("company_data_hooks") or []))
            key = _clean_optional(need.get("data_need_key"))
            if key:
                values.append(key)
    return _unique_strings(values)


def _normalized_hooks(values: Any) -> list[str]:
    hooks: list[str] = []
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        return _unique_strings(values)
    for value in values:
        if isinstance(value, Mapping):
            hook = value.get("key") or value.get("metric") or value.get("section") or value.get("topic")
            if hook:
                hooks.append(str(hook))
        else:
            hooks.append(str(value))
    return _unique_strings(hooks)


def _data_need_keys(pack: Mapping[str, Any]) -> list[str]:
    keys = [_clean_optional(row.get("data_need_key")) for row in _list_of_mappings(pack.get("data_needs"))]
    for lens in _list_of_mappings(pack.get("selected_lenses")):
        keys.extend(
            _clean_optional(row.get("data_need_key"))
            for row in _list_of_mappings(lens.get("related_data_needs"))
        )
    return _unique_strings(key for key in keys if key)


def _query_terms(
    *,
    subject: str,
    ticker: str | None,
    company_name: str | None,
    topics: Sequence[str],
    data_need_keys: Sequence[str],
) -> list[str]:
    terms: list[str] = [subject]
    if ticker:
        terms.append(ticker)
    if company_name:
        terms.append(company_name)
    for topic in topics:
        terms.append(topic)
        terms.extend(TOPIC_QUERY_EXPANSIONS.get(topic, ()))
    terms.extend(data_need_keys)
    return _unique_strings(term for term in terms if term)[:40]


def _company_question_ko(*, subject: str, original_question: str, topics: Sequence[str]) -> str:
    topic_text = ", ".join(_topic_ko(topic) for topic in topics[:12]) or "사업 모델, 현금흐름, 자본배분, 재무 안정성, 리스크 요인"
    return (
        f"{subject} 공시에서 {topic_text}를 확인하라. "
        f"구루 상담 질문에 필요한 회사 사실만 가져오고, 목표가나 매수/매도 결론은 만들지 않는다. "
        f"사용자 질문: {original_question}"
    )


def _company_question_en(
    *,
    subject: str,
    original_question: str,
    topics: Sequence[str],
    query_terms: Sequence[str],
) -> str:
    topic_text = ", ".join(topics[:12]) or "business_model, cash_flow, capital_allocation, balance_sheet, risk_factors"
    query_text = ", ".join(query_terms[:24])
    return (
        f"For {subject}, retrieve filing-grounded evidence for: {topic_text}. "
        f"Useful query terms: {query_text}. "
        f"Return company facts only; do not produce a price target or buy/sell conclusion. "
        f"User question: {original_question}"
    )


def _missing_inputs(
    *,
    requires_company: bool,
    requires_identifier: bool,
    ticker: str | None,
    company_name: str | None,
) -> list[str]:
    if requires_identifier:
        return ["exact_ticker_exchange_share_class"]
    if requires_company and not ticker and not company_name:
        return ["ticker_or_company_name"]
    return []


def _topic_supported(topic: str, evidence_text: str) -> bool:
    if not evidence_text:
        return False
    normalized_topic = topic.replace("_", " ").lower()
    keywords = [normalized_topic, topic.lower(), *TOPIC_QUERY_EXPANSIONS.get(topic, ())]
    return any(str(keyword).lower() in evidence_text for keyword in keywords if keyword)


def _topic_ko(topic: str) -> str:
    return TOPIC_KO_LABELS.get(topic, topic.replace("_", " "))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _get_nested(value: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    if isinstance(values, (str, bytes)):
        values = [values]
    if not isinstance(values, Iterable):
        return result
    for value in values:
        text = _clean_optional(value)
        if text and text not in result:
            result.append(text)
    return result


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _json_text(value: Any) -> str:
    if not value:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
