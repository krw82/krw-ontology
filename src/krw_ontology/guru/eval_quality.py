"""Quality evaluation for guru ResearchPack retrieval."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from krw_ontology.guru.mcp_tools import guru_query_context_tool
from krw_ontology.guru.models import utc_now_iso
from krw_ontology.guru.workspace import _write_json_atomic, guru_root


GURU_GOLD_EVAL_FORMAT = "krw-guru-gold-eval/v1"
GURU_QUALITY_REPORT_FORMAT = "krw-guru-quality-report/v1"
GURU_ANSWER_EVAL_REPORT_FORMAT = "krw-guru-answer-eval-report/v1"
GURU_ANSWER_EVAL_BATCH_REPORT_FORMAT = "krw-guru-answer-eval-batch-report/v1"
DEFAULT_GURU_ANSWER_PASS_THRESHOLD = 0.82
NON_NEGOTIABLE_ANSWER_CHECKS = {
    "no_raw_internals",
    "no_footer_disclaimer",
    "no_real_guru_claim",
    "no_personalized_investment_order",
}
DEFAULT_GURU_GOLD_EVAL_PATH = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "krw-guru-advisor"
    / "references"
    / "gold-eval.jsonl"
)

_GURU_NAME_RE = re.compile(
    r"(버핏|워런\s*버핏|하워드\s*막스|막스|애크먼|빌\s*애크먼|"
    r"브루스\s*플랫|플랫|테리\s*스미스|Terry\s+Smith|Warren\s+Buffett|Howard\s+Marks)"
)
_REAL_GURU_CLAIM_RE = re.compile(
    r"(실제로|오늘|지금|현재).{0,24}(말했|말할|추천|사라고|팔라고)|"
    r"(말했|말할|추천|사라고|팔라고).{0,24}(실제로|오늘|지금|현재)"
)
_INVESTMENT_ORDER_RE = re.compile(
    r"(매수|추가\s*매수|매도|보유|홀딩|손절|익절|비중\s*(?:확대|축소))"
    r"\s*(?:하|해|하세요|해야|하는\s*게\s*맞|가\s*맞)"
    r"|사세요|팔(?:아야|세요|아라)|풀매수|몰빵|전량\s*매도|목표가\s*[:：]?\s*[0-9]"
)
_RAW_INTERNAL_RE = re.compile(
    r"\b(?:MCP|ResearchPack|reviewed_id|schema|curation|batch|plugin|skill)\b"
    r"|guru:[a-z_]+:",
    re.IGNORECASE,
)
_GENERIC_TEMPLATE_ESCAPE_PHRASES = (
    "프레임 점검 질문입니다",
    "렌즈 기반의 질문으로만 접근",
    "종목별 결론은 공시 데이터",
    "체크리스트를 바로 실행 가능한 형태",
    "수익성/리스크/의사결정 체계",
)
_HIGH_RISK_INVESTMENT_TERMS = (
    "선물",
    "옵션",
    "파생",
    "레버리지",
    "마진",
    "강제청산",
    "반대매매",
    "빚",
    "대출",
    "신용",
    "담보",
    "잃으면 안 되는 돈",
    "생활비",
    "전재산",
    "몰빵",
    "손실 복구",
    "복구",
    "물타기",
    "평단",
    "평균단가",
)
_GENERIC_CRISIS_TEMPLATE_PHRASES = (
    "증권사 담당자",
    "가족에게 말",
    "상담 기관",
    "상담기관",
    "신용회복위원회",
    "혼자 짊어",
    "돈은 다시 벌 수",
    "당신은 정리 대상",
    "전화 한 통",
    "믿을 수 있는 사람",
    "처음 겪는 일이 아닙니다",
    "삶의 얘기",
    "자책은 거두",
)
_IMMERSION_BREAKING_PHRASES = (
    "렌즈로 보면",
    "렌즈로 본다면",
    "이 렌즈로",
    "이 렌즈에서",
    "관점에서 보면",
    "관점에서 분석하면",
    "버핏이라면",
    "막스라면",
    "애크먼이라면",
    "플랫이라면",
    "테리 스미스라면",
    "버핏식 결론",
    "막스식 결론",
    "애크먼식 결론",
    "플랫식 결론",
    "테리 스미스식 결론",
    "말투로 바꾸면",
    "언어로 바꾸면",
    "현재 온톨로지",
    "온톨로지 근거",
    "주의:",
    "위 평가는",
)
_FOOTER_DISCLAIMER_RE = re.compile(
    r"참고\s*[:：]|"
    r"주의\s*[:：].{0,80}(?:위\s*평가|목표가|매수|매도)|"
    r"위\s*평가는.{0,80}(?:목표가|매수|매도|결론이\s*아닙니다)|"
    r"위\s*내용은.{0,40}(AI|렌즈|해석)|"
    r"AI\s*렌즈\s*해석|"
    r"실제\s*(?:워런\s*버핏|버핏|하워드\s*막스|막스|빌\s*애크먼|애크먼|브루스\s*플랫|플랫|테리\s*스미스)?.{0,40}"
    r"(?:본인의\s*조언|실제\s*의견|조언이\s*아닙니다|의견이\s*아닙니다)|"
    r"(?:목표가|매수|매도).{0,30}(?:결론이\s*아닙니다|결론은\s*아닙니다)|"
    r"(?:매수|매도|목표가).{0,24}(?:구체적\s*)?투자\s*지시.{0,12}(?:제공하지\s*않|아닙니다)",
    re.IGNORECASE,
)
_COMPANY_EVIDENCE_TERMS = (
    "공시",
    "10-k",
    "10k",
    "사업보고서",
    "연차보고서",
    "filing",
    "annual report",
    "sec",
    "회사 데이터",
)
_AUTHOR_VOICE_CUES = {
    "buffett": (
        "사업",
        "소유",
        "경제성",
        "경영",
        "자본배분",
        "현금",
        "가격",
    ),
    "marks": (
        "리스크",
        "사이클",
        "확률",
        "기대",
        "불확실",
        "방어",
        "반대",
        "가격",
    ),
    "ackman": (
        "가설",
        "thesis",
        "촉매",
        "개선",
        "경영",
        "구조",
        "하방",
        "검증",
    ),
    "flatt": (
        "현금흐름",
        "자산",
        "장기",
        "재투자",
        "사이클",
        "내구",
        "복원력",
    ),
    "terry_smith": (
        "품질",
        "roce",
        "현금전환",
        "성장",
        "오래",
        "불필요한",
        "매매",
    ),
}


def run_guru_quality_eval(
    root: Path | str | None = None,
    *,
    eval_path: Path | str | None = None,
    output_path: Path | str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run gold-set checks against krw_guru_query_context output."""
    root_path = guru_root(root)
    gold_path = Path(eval_path).expanduser().resolve() if eval_path else DEFAULT_GURU_GOLD_EVAL_PATH
    cases = _read_gold_cases(gold_path)
    if limit is not None:
        cases = cases[: max(0, limit)]

    case_results = [_evaluate_case(root_path, case) for case in cases]
    passed = [case for case in case_results if case["passed"]]
    failed = [case for case in case_results if not case["passed"]]
    total_score = sum(float(case["score"]) for case in case_results)
    max_score = sum(float(case["max_score"]) for case in case_results)
    mean_score = round(total_score / max_score, 4) if max_score else 0.0
    report = {
        "format": GURU_QUALITY_REPORT_FORMAT,
        "generated_at": utc_now_iso(),
        "root": str(root_path),
        "eval_path": str(gold_path),
        "cases": len(case_results),
        "passed": len(passed),
        "failed": len(failed),
        "mean_score": mean_score,
        "quality_grade": _quality_grade(mean_score),
        "case_results": case_results,
        "failures": [
            {
                "id": case["id"],
                "question": case["question"],
                "failed_checks": [
                    check for check in case["checks"] if not check.get("passed")
                ],
            }
            for case in failed
        ],
    }
    target_path = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else root_path / "reports" / "guru_eval_quality_report.json"
    )
    report["output_path"] = str(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(target_path, report)
    return report


def run_guru_answer_eval(
    root: Path | str | None = None,
    *,
    question: str,
    answer: str,
    research_payload_path: Path | str | None = None,
    output_path: Path | str | None = None,
    filing_evidence_provided: bool = False,
    pass_threshold: float = DEFAULT_GURU_ANSWER_PASS_THRESHOLD,
) -> dict[str, Any]:
    """Evaluate a generated investor-facing answer against the guru output contract.

    This gate intentionally does not score a single canonical answer. It checks
    contract boundaries that should hold for any autonomous answer composer.
    """
    root_path = guru_root(root)
    research_payload = (
        _read_json_file(Path(research_payload_path).expanduser().resolve())
        if research_payload_path
        else None
    )
    evaluation = evaluate_guru_answer_contract(
        question=question,
        answer=answer,
        research_payload=research_payload,
        filing_evidence_provided=filing_evidence_provided,
        pass_threshold=pass_threshold,
    )
    target_path = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else root_path / "reports" / "guru_answer_eval_report.json"
    )
    report = {
        "format": GURU_ANSWER_EVAL_REPORT_FORMAT,
        "generated_at": utc_now_iso(),
        "root": str(root_path),
        "question": question,
        "filing_evidence_provided": filing_evidence_provided,
        "research_payload_path": str(Path(research_payload_path).expanduser().resolve())
        if research_payload_path
        else None,
        "output_path": str(target_path),
        **evaluation,
    }
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(target_path, report)
    return report


def run_guru_answer_eval_batch(
    root: Path | str | None = None,
    *,
    cases_path: Path | str,
    output_path: Path | str | None = None,
    limit: int | None = None,
    pass_threshold: float = DEFAULT_GURU_ANSWER_PASS_THRESHOLD,
) -> dict[str, Any]:
    """Evaluate generated answer artifacts from a JSONL case file."""
    root_path = guru_root(root)
    source_path = Path(cases_path).expanduser().resolve()
    cases = _read_answer_eval_cases(source_path)
    if limit is not None:
        cases = cases[: max(0, limit)]
    case_results = [
        _evaluate_answer_case(
            case=case,
            base_dir=source_path.parent,
            default_pass_threshold=pass_threshold,
        )
        for case in cases
    ]
    _apply_batch_repetition_penalty(case_results)
    passed = [case for case in case_results if case["passed"]]
    failed = [case for case in case_results if not case["passed"]]
    total_score = sum(float(case["score"]) for case in case_results)
    max_score = sum(float(case["max_score"]) for case in case_results)
    mean_score = round(total_score / max_score, 4) if max_score else 0.0
    target_path = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else root_path / "reports" / "guru_answer_eval_batch_report.json"
    )
    report = {
        "format": GURU_ANSWER_EVAL_BATCH_REPORT_FORMAT,
        "generated_at": utc_now_iso(),
        "root": str(root_path),
        "cases_path": str(source_path),
        "output_path": str(target_path),
        "cases": len(case_results),
        "passed": len(passed),
        "failed": len(failed),
        "mean_score": mean_score,
        "quality_grade": _quality_grade(mean_score),
        "case_results": case_results,
        "failures": [
            {
                "id": case["id"],
                "question": case["question"],
                "failed_checks": case["failures"],
            }
            for case in failed
        ],
    }
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(target_path, report)
    return report


def evaluate_guru_answer_contract(
    *,
    question: str,
    answer: str,
    research_payload: Mapping[str, Any] | None = None,
    filing_evidence_provided: bool = False,
    pass_threshold: float = DEFAULT_GURU_ANSWER_PASS_THRESHOLD,
) -> dict[str, Any]:
    """Score one generated answer against non-negotiable advisor contracts."""
    context = _answer_eval_context(research_payload)
    checks = [
        _check_answer_has_substance(answer),
        _check_no_raw_internals(answer),
        _check_no_footer_disclaimer(answer),
        _check_no_real_guru_claim(answer),
        _check_no_investment_order(answer),
        _check_company_evidence_boundary(answer, context, filing_evidence_provided),
        _check_clarification_handling(answer, context),
        _check_direct_source_boundary(answer, context),
        _check_practical_next_checks(answer),
        _check_selected_lens_usage(answer, context),
        _check_guru_voice_distinctiveness(answer, context),
        _check_non_ticker_company_data_priority(answer, context),
        _check_generic_template_escape(answer, context),
        _check_immersive_voice(answer, context),
        _check_generic_crisis_template_escape(question, answer, context),
    ]
    score = sum(check["weight"] for check in checks if check["passed"])
    max_score = sum(check["weight"] for check in checks)
    ratio = round(score / max_score, 4) if max_score else 0.0
    hard_failures = [
        check
        for check in checks
        if not check["passed"] and check["name"] in NON_NEGOTIABLE_ANSWER_CHECKS
    ]
    return {
        "answer_eval_version": "krw-guru-answer-contract/v1",
        "passed": ratio >= pass_threshold and not hard_failures,
        "score": score,
        "max_score": max_score,
        "score_ratio": ratio,
        "pass_threshold": pass_threshold,
        "context": context,
        "checks": checks,
        "hard_failures": hard_failures,
        "failures": [check for check in checks if not check["passed"]],
        "question": question,
    }


def _answer_eval_context(research_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = _mapping(research_payload)
    pack = _mapping(payload.get("research_pack"))
    answerability = _mapping(payload.get("answerability") or pack.get("answerability"))
    intent = _mapping(payload.get("intent") or pack.get("intent"))
    company_bridge = _mapping(payload.get("company_bridge") or pack.get("company_bridge"))
    pack_meta = _mapping(pack.get("pack_meta"))
    selected_lenses = _list_of_mappings(
        pack.get("selected_lenses") or payload.get("selected_lenses")
    )
    consultation_moves = _list_of_mappings(
        pack.get("consultation_moves") or payload.get("consultation_moves")
    )
    data_needs = _list_of_mappings(pack.get("data_needs") or payload.get("data_needs"))
    selected_author_keys = _string_list(
        payload.get("selected_author_keys")
        or pack_meta.get("guru_keys")
        or [row.get("author_key") for row in selected_lenses if row.get("author_key")]
    )
    research_status = str(
        payload.get("research_status")
        or pack.get("research_status")
        or payload.get("selection_status")
        or ""
    )
    requires_identifier_clarification = bool(
        payload.get("requires_identifier_clarification")
        or research_status == "needs_identifier_clarification"
    )
    requires_company_evidence = bool(
        payload.get("requires_company_evidence")
        or intent.get("requires_company_evidence")
        or company_bridge.get("requires_company_evidence")
    )
    ticker = _clean_optional(payload.get("ticker") or intent.get("ticker"))
    direct_source_match = answerability.get("direct_source_match")
    return {
        "research_status": research_status or None,
        "recommended_answer_mode": answerability.get("recommended_answer_mode"),
        "source_match_strength": answerability.get("source_match_strength"),
        "direct_source_match": direct_source_match
        if isinstance(direct_source_match, bool)
        else None,
        "requires_company_evidence": requires_company_evidence,
        "requires_identifier_clarification": requires_identifier_clarification,
        "ticker": ticker,
        "selected_author_keys": selected_author_keys,
        "selected_lenses": _answer_materials(selected_lenses),
        "consultation_moves": _answer_materials(consultation_moves),
        "data_needs": _answer_materials(data_needs),
        "persona_profile": _mapping(pack.get("persona_profile") or payload.get("persona_profile")),
        "clarifying_questions": _string_list(
            payload.get("clarifying_questions") or pack.get("clarifying_questions")
        ),
    }


def _check_answer_has_substance(answer: str) -> dict[str, Any]:
    stripped = answer.strip()
    hangul_count = len(re.findall(r"[가-힣]", stripped))
    return _weighted_check(
        "answer_has_substance",
        len(stripped) >= 40 and hangul_count >= 12,
        "at least 40 chars and Korean investor-facing content",
        {"chars": len(stripped), "hangul_chars": hangul_count},
        1,
    )


def _check_no_raw_internals(answer: str) -> dict[str, Any]:
    matches = sorted({match.group(0) for match in _RAW_INTERNAL_RE.finditer(answer)})
    return _weighted_check(
        "no_raw_internals",
        not matches,
        "no MCP/plugin/schema/raw reviewed IDs in investor answer",
        {"matched": matches},
        1,
    )


def _check_no_footer_disclaimer(answer: str) -> dict[str, Any]:
    matches = sorted({match.group(0) for match in _FOOTER_DISCLAIMER_RE.finditer(answer)})
    return _weighted_check(
        "no_footer_disclaimer",
        not matches,
        "no source/footer disclaimer or AI-lens explanation in immersive advisor answer",
        {"matched": matches},
        2,
    )


def _check_no_real_guru_claim(answer: str) -> dict[str, Any]:
    matched_guru = bool(_GURU_NAME_RE.search(answer))
    matched_claim = bool(_REAL_GURU_CLAIM_RE.search(answer))
    return _weighted_check(
        "no_real_guru_claim",
        not (matched_guru and matched_claim),
        "do not present a real investor as advising today",
        {"matched_guru": matched_guru, "matched_current_claim": matched_claim},
        2,
    )


def _check_no_investment_order(answer: str) -> dict[str, Any]:
    matches = sorted({match.group(0) for match in _INVESTMENT_ORDER_RE.finditer(answer)})
    return _weighted_check(
        "no_personalized_investment_order",
        not matches,
        "no buy/sell/hold/target-price instruction",
        {"matched": matches},
        2,
    )


def _check_company_evidence_boundary(
    answer: str,
    context: Mapping[str, Any],
    filing_evidence_provided: bool,
) -> dict[str, Any]:
    if not context.get("requires_company_evidence"):
        return _weighted_check(
            "company_evidence_boundary",
            True,
            "company evidence not required",
            "not required",
            2,
        )
    if filing_evidence_provided:
        passed = _answer_mentions_filing_basis(answer)
        expected = "answer references filing/company evidence basis"
    else:
        passed = _answer_acknowledges_missing_company_evidence(answer)
        expected = "answer acknowledges missing filing/company evidence before judgment"
    return _weighted_check(
        "company_evidence_boundary",
        passed,
        expected,
        {
            "filing_evidence_provided": filing_evidence_provided,
            "mentions_filing_basis": _answer_mentions_filing_basis(answer),
            "acknowledges_missing_evidence": _answer_acknowledges_missing_company_evidence(answer),
        },
        2,
    )


def _check_clarification_handling(
    answer: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    status = str(context.get("research_status") or "")
    needs_clarification = status in {"needs_clarification", "needs_identifier_clarification"} or bool(
        context.get("requires_identifier_clarification")
    )
    if not needs_clarification:
        return _weighted_check("clarification_handling", True, "not required", "not required", 2)
    asks_question = "?" in answer or _contains_any(answer, ("확인", "알려", "구분", "티커", "거래소"))
    gives_order = bool(_INVESTMENT_ORDER_RE.search(answer))
    return _weighted_check(
        "clarification_handling",
        asks_question and not gives_order,
        "ask for missing instrument/company details before a strong answer",
        {"asks_question_or_confirmation": asks_question, "gives_order": gives_order},
        2,
    )


def _check_direct_source_boundary(answer: str, context: Mapping[str, Any]) -> dict[str, Any]:
    direct_source_match = context.get("direct_source_match")
    if direct_source_match is not False:
        return _weighted_check(
            "direct_source_boundary",
            True,
            "direct source match is true or unknown",
            direct_source_match,
            1,
        )
    passed = _contains_any(
        answer,
        (
            "내가 지금 볼 수 있는 자료",
            "지금 볼 수 있는 자료",
            "자료상",
            "손에 있는 자료",
            "아직 단정하면 안",
            "아직 말할 수 없",
            "여기까지 말할 수",
            "더 봐야 할",
            "결론을 내리기 어렵",
            "판단을 내리기 어렵",
            "공시 근거 없이",
            "근거 확인이 필요",
        ),
    )
    return _weighted_check(
        "direct_source_boundary",
        passed,
        "weak matches must show natural confidence limits without source/disclaimer footers",
        {"direct_source_match": direct_source_match},
        1,
    )


def _check_practical_next_checks(answer: str) -> dict[str, Any]:
    passed = _contains_any(
        answer,
        ("확인", "질문", "체크", "점검", "봐야", "필요", "조건", "근거"),
    )
    return _weighted_check(
        "practical_next_checks",
        passed,
        "answer gives investor-useful checks or missing evidence",
        {"matched": passed},
        1,
    )


def _check_selected_lens_usage(answer: str, context: Mapping[str, Any]) -> dict[str, Any]:
    materials = _list_of_mappings(context.get("selected_lenses"))
    if not materials:
        return _weighted_check(
            "selected_lens_usage",
            True,
            "selected lenses not provided",
            "not required",
            2,
        )
    matches = _matched_material_terms(answer, materials[:3])
    passed = bool(matches)
    return _weighted_check(
        "selected_lens_usage",
        passed,
        "answer visibly uses at least one selected lens label or core phrase",
        {
            "matched": matches,
            "candidate_labels": [row.get("label_ko") for row in materials[:3]],
        },
        2,
    )


def _check_guru_voice_distinctiveness(answer: str, context: Mapping[str, Any]) -> dict[str, Any]:
    author_keys = _string_list(context.get("selected_author_keys"))
    if not author_keys:
        return _weighted_check(
            "guru_voice_distinctiveness",
            True,
            "selected authors not provided",
            "not required",
            2,
        )
    matched_by_author: dict[str, list[str]] = {}
    for author_key in author_keys:
        cues = _AUTHOR_VOICE_CUES.get(author_key, ())
        matched = [cue for cue in cues if cue.lower() in answer.lower()]
        if matched:
            matched_by_author[author_key] = matched[:5]
    persona_matches = _matched_persona_terms(answer, _mapping(context.get("persona_profile")))
    passed = bool(matched_by_author) or bool(persona_matches)
    return _weighted_check(
        "guru_voice_distinctiveness",
        passed,
        "answer carries the selected author's voice or texture without impersonation",
        {
            "matched_author_cues": matched_by_author,
            "matched_persona_terms": persona_matches,
            "selected_author_keys": author_keys,
        },
        2,
    )


def _check_non_ticker_company_data_priority(answer: str, context: Mapping[str, Any]) -> dict[str, Any]:
    if context.get("requires_company_evidence") or context.get("ticker"):
        return _weighted_check(
            "non_ticker_company_data_priority",
            True,
            "company evidence is required or ticker is present",
            "not required",
            1,
        )
    if not context.get("selected_lenses") and not context.get("consultation_moves"):
        return _weighted_check(
            "non_ticker_company_data_priority",
            True,
            "no guru answer materials provided",
            "not required",
            1,
        )
    opening = answer.strip()[:240]
    first_company_pos = _first_term_position(opening, _COMPANY_EVIDENCE_TERMS)
    first_lens_pos = _first_term_position(
        opening,
        (
            "관점",
            "먼저 물을",
            "먼저 묻",
            "내가 먼저",
            "자,",
            "이 질문",
            "핵심",
        ),
    )
    company_caveat_first = first_company_pos is not None
    lens_first = first_lens_pos is not None and (
        first_company_pos is None or first_lens_pos < first_company_pos
    )
    return _weighted_check(
        "non_ticker_company_data_priority",
        not company_caveat_first or lens_first,
        "non-ticker answers should lead with guru lens, not filing caveats",
        {
            "company_caveat_in_opening": company_caveat_first,
            "lens_language_in_opening": lens_first,
            "first_company_pos": first_company_pos,
            "first_lens_pos": first_lens_pos,
        },
        1,
    )


def _check_generic_template_escape(answer: str, context: Mapping[str, Any]) -> dict[str, Any]:
    if not context.get("selected_lenses"):
        return _weighted_check(
            "generic_template_escape",
            True,
            "selected lenses not provided",
            "not required",
            8,
        )
    matched = [
        phrase for phrase in _GENERIC_TEMPLATE_ESCAPE_PHRASES if phrase.lower() in answer.lower()
    ]
    lens_matches = _matched_material_terms(answer, _list_of_mappings(context.get("selected_lenses"))[:3])
    return _weighted_check(
        "generic_template_escape",
        not matched or bool(lens_matches),
        "avoid generic safety boilerplate that ignores selected lenses",
        {"matched_generic_phrases": matched, "matched_lens_terms": lens_matches},
        8,
    )


def _check_immersive_voice(answer: str, context: Mapping[str, Any]) -> dict[str, Any]:
    if not context.get("selected_lenses") and not context.get("selected_author_keys"):
        return _weighted_check(
            "immersive_voice",
            True,
            "guru voice materials not provided",
            "not required",
            8,
        )
    matched = [
        phrase for phrase in _IMMERSION_BREAKING_PHRASES if phrase.lower() in answer.lower()
    ]
    return _weighted_check(
        "immersive_voice",
        not matched,
        "answer should start as advisor speech, not meta lens/rendering commentary",
        {"matched_immersion_breakers": matched},
        8,
    )


def _check_generic_crisis_template_escape(
    question: str,
    answer: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    high_risk_question = _contains_any(question, _HIGH_RISK_INVESTMENT_TERMS)
    if not high_risk_question or (
        not context.get("selected_lenses") and not context.get("selected_author_keys")
    ):
        return _weighted_check(
            "generic_crisis_template_escape",
            True,
            "not a high-risk guru answer with selected materials",
            "not required",
            8,
        )
    matched = [
        phrase for phrase in _GENERIC_CRISIS_TEMPLATE_PHRASES if phrase.lower() in answer.lower()
    ]
    return _weighted_check(
        "generic_crisis_template_escape",
        not matched,
        "high-risk investment answers should use selected guru ontology, not generic crisis boilerplate",
        {"matched_generic_crisis_phrases": matched},
        8,
    )


def _answer_mentions_filing_basis(answer: str) -> bool:
    return _contains_any(
        answer,
        _COMPANY_EVIDENCE_TERMS,
    )


def _answer_acknowledges_missing_company_evidence(answer: str) -> bool:
    mentions_basis = _answer_mentions_filing_basis(answer) or _contains_any(
        answer,
        ("회사 데이터", "재무", "현금흐름", "리스크 요인", "자본배분", "밸류에이션"),
    )
    mentions_missing = _contains_any(
        answer,
        ("필요", "확인", "없이는", "부족", "아직", "먼저", "전에는", "근거 없이"),
    )
    return mentions_basis and mentions_missing


def _contains_any(text: str, needles: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _first_term_position(text: str, needles: Sequence[str]) -> int | None:
    lowered = text.lower()
    positions = [lowered.find(needle.lower()) for needle in needles if needle.lower() in lowered]
    positions = [position for position in positions if position >= 0]
    return min(positions) if positions else None


def _answer_materials(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    for row in rows:
        materials.append(
            {
                "author_key": row.get("author_key"),
                "author_name": row.get("author_name"),
                "label_ko": row.get("label_ko"),
                "label_en": row.get("label_en"),
                "summary_ko": row.get("summary_ko"),
                "lens_role": row.get("lens_role"),
                "answer_role": row.get("answer_role"),
                "object_type": row.get("object_type"),
            }
        )
    return materials


def _matched_material_terms(
    answer: str,
    materials: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    answer_lower = answer.lower()
    matches: list[dict[str, Any]] = []
    for material in materials:
        label = str(material.get("label_ko") or material.get("label_en") or "")
        if label and label.lower() in answer_lower:
            matches.append({"label": label, "term": label})
            continue
        terms = _material_terms(material)
        matched_terms = [term for term in terms if term.lower() in answer_lower]
        if len(matched_terms) >= 2 or (matched_terms and len(matched_terms[0]) >= 4):
            matches.append({"label": label, "terms": matched_terms[:4]})
    return matches


def _material_terms(material: Mapping[str, Any]) -> list[str]:
    text = " ".join(
        str(value or "")
        for value in (
            material.get("label_ko"),
            material.get("label_en"),
            material.get("summary_ko"),
        )
    )
    return _salient_terms(text)


def _matched_persona_terms(answer: str, persona_profile: Mapping[str, Any]) -> list[str]:
    rows: list[Mapping[str, Any]] = []
    for key in ("derived_traits", "reasoning_style", "caution_patterns"):
        rows.extend(_list_of_mappings(persona_profile.get(key)))
    terms: list[str] = []
    for row in rows:
        terms.extend(_salient_terms(str(row.get("label_ko") or "")))
    answer_lower = answer.lower()
    matched: list[str] = []
    for term in terms:
        if term.lower() in answer_lower and term not in matched:
            matched.append(term)
    return matched[:6]


def _salient_terms(text: str) -> list[str]:
    raw_terms = re.findall(r"[가-힣A-Za-z0-9]{2,}", text)
    stop_terms = {
        "것",
        "수",
        "때",
        "및",
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "company",
        "investment",
        "answer",
        "lens",
    }
    terms: list[str] = []
    for term in raw_terms:
        normalized = term.lower()
        if normalized in stop_terms:
            continue
        if len(term) < 2:
            continue
        if term not in terms:
            terms.append(term)
    return terms[:16]


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _read_answer_eval_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"answer eval row {line_number} is not an object")
            if not payload.get("question"):
                raise ValueError(f"answer eval row {line_number} is missing question")
            if not payload.get("answer") and not payload.get("answer_path"):
                raise ValueError(f"answer eval row {line_number} is missing answer or answer_path")
            cases.append(payload)
    return cases


def _evaluate_answer_case(
    *,
    case: Mapping[str, Any],
    base_dir: Path,
    default_pass_threshold: float,
) -> dict[str, Any]:
    answer_path = _case_path(case.get("answer_path"), base_dir)
    research_payload_path = _case_path(
        case.get("research_payload_path") or case.get("research_payload_file"),
        base_dir,
    )
    answer = (
        answer_path.read_text(encoding="utf-8")
        if answer_path is not None
        else str(case.get("answer") or "")
    )
    research_payload = _mapping(case.get("research_payload"))
    if research_payload_path is not None:
        research_payload = _read_json_file(research_payload_path)
    evaluation = evaluate_guru_answer_contract(
        question=str(case.get("question") or ""),
        answer=answer,
        research_payload=research_payload,
        filing_evidence_provided=bool(case.get("filing_evidence_provided")),
        pass_threshold=float(case.get("pass_threshold") or default_pass_threshold),
    )
    return {
        "id": str(case.get("id") or ""),
        "question": str(case.get("question") or ""),
        "answer_path": str(answer_path) if answer_path else None,
        "research_payload_path": str(research_payload_path) if research_payload_path else None,
        "filing_evidence_provided": bool(case.get("filing_evidence_provided")),
        "answer_fingerprint": _answer_fingerprint(answer),
        **evaluation,
    }


def _apply_batch_repetition_penalty(case_results: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for result in case_results:
        fingerprint = str(result.get("answer_fingerprint") or "")
        if fingerprint:
            groups.setdefault(fingerprint, []).append(result)
    for duplicate_group in groups.values():
        if len(duplicate_group) < 2:
            continue
        ids = [str(result.get("id") or "") for result in duplicate_group]
        for result in duplicate_group:
            check = _weighted_check(
                "batch_repetition_penalty",
                False,
                "different eval questions should not receive the same substantive answer",
                {"duplicate_case_ids": ids},
                16,
            )
            result["checks"].append(check)
            result["failures"].append(check)
            result["max_score"] += check["weight"]
            result["score_ratio"] = round(result["score"] / result["max_score"], 4)
            result["passed"] = result["score_ratio"] >= float(result.get("pass_threshold") or 0.0)


def _answer_fingerprint(answer: str) -> str:
    normalized = re.sub(r"\s+", "", answer.lower())
    normalized = re.sub(r"[0-9a-f]{6,}", "", normalized)
    if len(normalized) < 80:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _case_path(value: Any, base_dir: Path) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _read_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _read_gold_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"gold eval row {line_number} is not an object")
            if payload.get("format") and payload.get("format") != GURU_GOLD_EVAL_FORMAT:
                raise ValueError(f"gold eval row {line_number} has unsupported format")
            cases.append(payload)
    return cases


def _evaluate_case(root_path: Path, case: Mapping[str, Any]) -> dict[str, Any]:
    question = str(case.get("question") or "")
    author_keys = _string_list(case.get("author_keys") or case.get("lenses"))
    ticker = case.get("ticker")
    try:
        payload = json.loads(
            guru_query_context_tool(
                root=root_path,
                question=question,
                author_keys=author_keys,
                ticker=str(ticker) if ticker else None,
            )
        )
    except Exception as exc:  # pragma: no cover - defensive report path
        return {
            "id": case.get("id"),
            "question": question,
            "passed": False,
            "score": 0,
            "max_score": 1,
            "checks": [
                {
                    "name": "tool_call",
                    "passed": False,
                    "expected": "valid JSON response",
                    "actual": str(exc),
                    "weight": 1,
                }
            ],
            "selected_lens_labels": [],
            "research_status": "tool_error",
            "intent_family": None,
            "requires_company_evidence": None,
        }

    pack = _mapping(payload.get("research_pack"))
    selected_lenses = _list_of_mappings(pack.get("selected_lenses"))
    consultation_moves = _list_of_mappings(pack.get("consultation_moves"))
    data_needs = _list_of_mappings(pack.get("data_needs"))
    selected = [*selected_lenses, *consultation_moves, *data_needs]
    selected_text = json.dumps(
        {
            "selected": selected,
            "company_bridge": pack.get("company_bridge"),
            "clarifying_questions": pack.get("clarifying_questions"),
            "top_level_filing_evidence_requirements": payload.get("filing_evidence_requirements"),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).lower()
    checks = [
        _check_status(payload, case),
        _check_intent(payload, case),
        _check_company_evidence(payload, case),
        _check_min_lenses(selected_lenses, case),
        _check_author_coverage(selected_lenses, author_keys),
        _check_include_terms(selected_text, case),
        _check_excluded_terms(selected_text, case),
        _check_answer_roles(selected, case),
        _check_soft_metadata(selected, case),
    ]
    score = sum(check["weight"] for check in checks if check["passed"])
    max_score = sum(check["weight"] for check in checks)
    pass_threshold = float(case.get("pass_threshold") or 0.78)
    ratio = score / max_score if max_score else 0.0
    return {
        "id": case.get("id"),
        "question": question,
        "author_keys": author_keys,
        "passed": ratio >= pass_threshold,
        "score": score,
        "max_score": max_score,
        "score_ratio": round(ratio, 4),
        "pass_threshold": pass_threshold,
        "checks": checks,
        "selected_lens_labels": [row.get("label_ko") for row in selected_lenses],
        "research_status": payload.get("research_status"),
        "intent_family": payload.get("intent", {}).get("family") or payload.get("intent_family"),
        "requires_company_evidence": payload.get("requires_company_evidence"),
    }


def _check_status(payload: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    expected = _string_list(case.get("expected_statuses"))
    actual = str(payload.get("research_status") or "")
    return _weighted_check(
        "research_status",
        not expected or actual in expected,
        expected or "any",
        actual,
        2,
    )


def _check_intent(payload: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    expected = str(case.get("expected_intent") or "")
    actual = str(payload.get("intent", {}).get("family") or payload.get("intent_family") or "")
    return _weighted_check("intent_family", not expected or actual == expected, expected or "any", actual, 1)


def _check_company_evidence(payload: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    if "requires_company_evidence" not in case:
        return _weighted_check("requires_company_evidence", True, "any", payload.get("requires_company_evidence"), 1)
    expected = bool(case.get("requires_company_evidence"))
    actual = bool(payload.get("requires_company_evidence"))
    return _weighted_check("requires_company_evidence", actual == expected, expected, actual, 2)


def _check_min_lenses(
    selected_lenses: Sequence[Mapping[str, Any]],
    case: Mapping[str, Any],
) -> dict[str, Any]:
    expected = int(case.get("min_lenses") or 1)
    actual = len(selected_lenses)
    return _weighted_check("min_lenses", actual >= expected, expected, actual, 1)


def _check_author_coverage(
    selected_lenses: Sequence[Mapping[str, Any]],
    expected_authors: Sequence[str],
) -> dict[str, Any]:
    if not expected_authors:
        return _weighted_check("author_coverage", True, "any", [], 1)
    actual = sorted({str(row.get("author_key")) for row in selected_lenses if row.get("author_key")})
    missing = [author for author in expected_authors if author not in actual]
    return _weighted_check("author_coverage", not missing, expected_authors, actual, 1)


def _check_include_terms(selected_text: str, case: Mapping[str, Any]) -> dict[str, Any]:
    groups = _term_groups(case.get("must_include_any"))
    if not groups:
        return _weighted_check("must_include_any", True, "none", "not configured", 1)
    missing_groups = [
        group for group in groups if not any(term.lower() in selected_text for term in group)
    ]
    return _weighted_check("must_include_any", not missing_groups, groups, {"missing": missing_groups}, 2)


def _check_excluded_terms(selected_text: str, case: Mapping[str, Any]) -> dict[str, Any]:
    terms = [term.lower() for term in _string_list(case.get("must_not_include_any"))]
    if not terms:
        return _weighted_check("must_not_include_any", True, "none", "not configured", 1)
    matched = [term for term in terms if term in selected_text]
    return _weighted_check("must_not_include_any", not matched, terms, {"matched": matched}, 1)


def _check_answer_roles(
    selected: Sequence[Mapping[str, Any]],
    case: Mapping[str, Any],
) -> dict[str, Any]:
    expected_roles = set(_string_list(case.get("expected_answer_roles")))
    if not expected_roles:
        return _weighted_check("answer_roles", True, "any", [], 1)
    actual: set[str] = set()
    for row in selected:
        answer_role = _mapping(row.get("answer_role"))
        default = answer_role.get("default")
        if default:
            actual.add(str(default))
        actual.update(_string_list(answer_role.get("possible_roles")))
    return _weighted_check("answer_roles", bool(expected_roles & actual), sorted(expected_roles), sorted(actual), 1)


def _check_soft_metadata(
    selected: Sequence[Mapping[str, Any]],
    case: Mapping[str, Any],
) -> dict[str, Any]:
    expected = bool(case.get("expect_soft_metadata", True))
    if not expected:
        return _weighted_check("soft_metadata", True, "not required", "not required", 1)
    actual = any(
        row.get("applicability") or row.get("specificity") or row.get("answer_role")
        for row in selected
    )
    return _weighted_check("soft_metadata", actual, True, actual, 1)


def _weighted_check(
    name: str,
    passed: bool,
    expected: Any,
    actual: Any,
    weight: int,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "expected": expected,
        "actual": actual,
        "weight": weight,
    }


def _term_groups(value: Any) -> list[list[str]]:
    if not value:
        return []
    if not isinstance(value, list):
        return [[str(value)]]
    if all(isinstance(item, str) for item in value):
        return [[str(item) for item in value]]
    groups: list[list[str]] = []
    for item in value:
        if isinstance(item, list):
            group = [str(term) for term in item if str(term)]
            if group:
                groups.append(group)
        elif item:
            groups.append([str(item)])
    return groups


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _quality_grade(score: float) -> str:
    if score >= 0.9:
        return "excellent"
    if score >= 0.8:
        return "good"
    if score >= 0.7:
        return "needs_review"
    return "poor"
