#!/usr/bin/env python3
"""Run a local KRW Ontology agent E2E answer-quality harness.

This script exercises the production MCP workflow at the tool layer and then
generates user-facing Korean final answers with a small deterministic analyst
composer. It is intentionally reproducible: no external LLM API is required.

For each prompt it records two answer variants:

1. compact_only: query_context research state only.
2. trace_augmented: query_context plus the first recommended trace root.

The quality rubric checks relevance, grounding, directness discipline,
answer structure, caveats, and internal implementation leakage.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import time
from typing import Any, Mapping

from krw_ontology.mcp_server.tools import query_context_tool, query_tool, trace_tool


HEADER_RE = re.compile(r"^##\s+(?P<ticker>[A-Z][A-Z0-9.\-]*)\s+-\s+(?P<label>.+?)\s*$")
QUESTION_RE = re.compile(r"^(?P<index>\d+)\.\s+(?P<question>.+?)\s*$")
INTERNAL_LEAK_RE = re.compile(
    r"\b("
    r"MCP|ResearchKernel|kernel|query_context|research_pack|trace_candidates|"
    r"object_id|SourceSpan|EvidenceQuote|ResearchClaim|MetricObservation|"
    r"traceable_related|traceable_direct|related_context_only|not_answerable|"
    r"JSON|SQLite|FTS|ontology|온톨로지|트레이스|쿼리"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PromptCase:
    case_id: str
    ticker: str
    company_name: str
    question_index: int
    question: str


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", default="/tmp/krw_company_suggested_prompts_by_ticker.md")
    parser.add_argument("--index-path", default="~/krw-ontology-data/indexes/agent_index.sqlite")
    parser.add_argument("--root", default=None)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit-results", type=int, default=10)
    parser.add_argument("--max-traces", type=int, default=3)
    parser.add_argument("--research-mode", choices=["fast", "standard", "deep"], default="standard")
    parser.add_argument("--output-prefix", default=None)
    args = parser.parse_args()

    cases = parse_prompt_cases(Path(args.prompts))[args.offset :]
    if args.limit is not None:
        cases = cases[: args.limit]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_prefix = Path(args.output_prefix or f"/tmp/krw_research_kernel_agent_e2e_{timestamp}")
    jsonl_path = output_prefix.with_suffix(".jsonl")
    json_path = output_prefix.with_suffix(".json")
    summary_path = output_prefix.with_suffix(".summary.json")
    md_path = output_prefix.with_suffix(".md")

    records: list[dict[str, Any]] = []
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for ordinal, case in enumerate(cases, start=1):
            record = run_case(
                case,
                ordinal=ordinal,
                total=len(cases),
                root=args.root,
                index_path=args.index_path,
                limit_results=args.limit_results,
                max_traces=args.max_traces,
                research_mode=args.research_mode,
            )
            records.append(record)
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()

    summary = summarize(records)
    json_path.write_text(
        json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(to_markdown(summary, records), encoding="utf-8")
    print(
        json.dumps(
            {
                "jsonl": str(jsonl_path),
                "json": str(json_path),
                "summary": str(summary_path),
                "markdown": str(md_path),
                "summary_data": summary,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def parse_prompt_cases(path: Path) -> list[PromptCase]:
    cases: list[PromptCase] = []
    ticker: str | None = None
    company_name: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = HEADER_RE.match(line)
        if header:
            ticker = header.group("ticker").strip()
            company_name = header.group("label").strip()
            continue
        question = QUESTION_RE.match(line)
        if question and ticker:
            question_index = int(question.group("index"))
            cases.append(
                PromptCase(
                    case_id=f"{ticker}-{question_index}",
                    ticker=ticker,
                    company_name=company_name or ticker,
                    question_index=question_index,
                    question=question.group("question").strip(),
                )
            )
    return cases


def run_case(
    case: PromptCase,
    *,
    ordinal: int,
    total: int,
    root: str | None,
    index_path: str,
    limit_results: int,
    max_traces: int,
    research_mode: str,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "question": case.question,
        "ticker": case.ticker,
        "limit_results": limit_results,
        "index_path": index_path,
    }
    if root:
        kwargs["root"] = root

    started = time.perf_counter()
    tool_calls: list[dict[str, Any]] = []
    status = "ok"
    error: str | None = None
    context: dict[str, Any] = {}
    traces: list[dict[str, Any]] = []
    selected_trace: dict[str, Any] | None = None
    try:
        call_start = time.perf_counter()
        context_raw = query_context_tool(**kwargs)
        tool_calls.append(
            {
                "tool": "krw_ontology_query_context",
                "duration_sec": round(time.perf_counter() - call_start, 3),
                "result_chars": len(context_raw),
            }
        )
        context = json.loads(context_raw)

        trace_budget = trace_budget_for_mode(research_mode, max_traces, context)
        trace_object_ids = trace_object_candidates(context)[:trace_budget]
        latest_requested = bool(re.search(r"최근|latest|recent|최근\s*분기|latest quarter", case.question, re.I))
        needs_latest_query = latest_requested and not has_latest_object_id(trace_object_ids)
        if (not trace_object_ids or needs_latest_query) and should_use_targeted_query(context, research_mode):
            call_start = time.perf_counter()
            query_raw = query_tool(
                topic=targeted_query_topic(case.question, latest_requested=latest_requested),
                ticker=case.ticker,
                document_type="10-Q" if latest_requested else None,
                index_path=index_path,
                limit=min(limit_results, 5),
                response_detail="compact",
            )
            tool_calls.append(
                {
                    "tool": "krw_ontology_query",
                    "duration_sec": round(time.perf_counter() - call_start, 3),
                    "result_chars": len(query_raw),
                }
            )
            query_payload = json.loads(query_raw)
            if isinstance(query_payload, Mapping):
                context.setdefault("research_pack", {})["follow_up_query_pack"] = query_payload
                follow_up_trace_ids = trace_object_candidates_from_query(query_payload)
                if needs_latest_query:
                    trace_object_ids = follow_up_trace_ids[:trace_budget]
                else:
                    trace_object_ids = follow_up_trace_ids[:trace_budget_for_mode("standard", max_traces, context)]
        for trace_object_id in trace_object_ids:
            call_start = time.perf_counter()
            trace_raw = trace_tool(object_id=trace_object_id, index_path=index_path)
            tool_calls.append(
                {
                    "tool": "krw_ontology_trace",
                    "duration_sec": round(time.perf_counter() - call_start, 3),
                    "result_chars": len(trace_raw),
                    "object_id_hash": stable_short(trace_object_id),
                }
            )
            traces.append(json.loads(trace_raw))
        selected_trace = select_best_trace(case.question, traces)
    except Exception as exc:  # pragma: no cover - harness should keep going
        status = "error"
        error = f"{type(exc).__name__}: {exc}"

    elapsed_sec = round(time.perf_counter() - started, 3)
    compact_answer = compose_answer(case, context=context, trace=None)
    trace_answer = compose_answer(case, context=context, trace=selected_trace)
    compact_score = score_answer(case, compact_answer, context=context, trace=None)
    trace_score = score_answer(case, trace_answer, context=context, trace=selected_trace)
    kernel = context.get("kernel") if isinstance(context.get("kernel"), dict) else {}
    research_pack = context.get("research_pack") if isinstance(context.get("research_pack"), Mapping) else {}
    answer_diagnostics = {
        "compact_only": answer_diagnostics_for(case.question, compact_answer, tool_calls, research_mode),
        "trace_augmented": answer_diagnostics_for(case.question, trace_answer, tool_calls, research_mode),
    }

    return {
        "ordinal": ordinal,
        "total": total,
        "case_id": case.case_id,
        "ticker": case.ticker,
        "company_name": case.company_name,
        "question_index": case.question_index,
        "question": case.question,
        "status": status,
        "error": error,
        "elapsed_sec": elapsed_sec,
        "research_mode": research_mode,
        "tool_calls": tool_calls,
        "tool_metrics": tool_metrics(tool_calls),
        "kernel": {
            "intent": kernel.get("intent"),
            "primary_context": kernel.get("primary_context"),
            "status": kernel.get("status"),
            "answer_mode": kernel.get("answer_mode"),
            "allowed_next_tools": kernel.get("allowed_next_tools"),
            "do_not_call": kernel.get("do_not_call"),
        },
        "packs_present": sorted(str(key) for key, value in research_pack.items() if value),
        "primary_pack": primary_pack_name(kernel, research_pack),
        "research_status": context.get("research_status"),
        "trace_used": selected_trace is not None,
        "trace_count": len(traces),
        "trace_object_type": ((selected_trace or {}).get("object") or {}).get("type"),
        "trace_document": trace_document_label(selected_trace),
        "answers": {
            "compact_only": compact_answer,
            "trace_augmented": trace_answer,
        },
        "quality": {
            "compact_only": compact_score,
            "trace_augmented": trace_score,
            "delta": round(trace_score["score"] - compact_score["score"], 1),
        },
        "answer_diagnostics": answer_diagnostics,
    }


def trace_object_candidates(context: Mapping[str, Any]) -> list[str]:
    pack = context.get("research_pack") if isinstance(context.get("research_pack"), Mapping) else {}
    output: list[str] = []
    for item in pack.get("trace_candidates") or []:
        if isinstance(item, Mapping) and item.get("object_id"):
            output.append(str(item["object_id"]))
    for item in context.get("recommended_tools") or []:
        if isinstance(item, Mapping) and item.get("object_id"):
            output.append(str(item["object_id"]))
    return dedupe(output)


def should_use_targeted_query(context: Mapping[str, Any], research_mode: str) -> bool:
    if research_mode == "fast":
        return False
    kernel = context.get("kernel") if isinstance(context.get("kernel"), Mapping) else {}
    agent_autonomy = context.get("agent_autonomy") if isinstance(context.get("agent_autonomy"), Mapping) else {}
    allowed = set(str(item) for item in (kernel.get("allowed_next_tools") or agent_autonomy.get("allowed_next_tools") or []))
    do_not_call = set(str(item) for item in (kernel.get("do_not_call") or context.get("do_not_call") or []))
    if "krw_ontology_query" in do_not_call or "query" in do_not_call:
        return False
    if allowed:
        return "krw_ontology_query" in allowed or "query" in allowed
    return kernel.get("status") in {"partial_answer_possible", "needs_targeted_followup"}


def targeted_query_topic(question: str, *, latest_requested: bool) -> str:
    if latest_requested:
        return f"{question} latest 10-Q quarterly revenue segment net sales recent drivers"
    q = question.lower()
    anchors: list[str] = []
    if any(term in q for term in ("헬스케어 비용", "health care", "sg&a", "r&d", "매출총이익률", "제품 원가")):
        anchors.extend(["health care cost trend", "gross margin", "cost of products", "R&D expenses", "SG&A expenses"])
    if any(term in q for term in ("노동시장", "인건비", "운송", "에너지", "포장재", "리테일 경쟁", "labor", "transportation", "packaging")):
        anchors.extend(["labor costs", "transportation costs", "energy costs", "packaging costs", "retail competition"])
    if any(term in q for term in ("반도체 솔루션", "인프라 소프트웨어", "semiconductor", "infrastructure software")):
        anchors.extend(["semiconductor solutions revenue", "infrastructure software revenue", "margin", "cash flow", "investment"])
    if any(term in q for term in ("ira", "medicare", "특허", "바이오시밀러", "인재", "인건비")):
        anchors.extend(["IRA Medicare price setting", "patent expiration", "biosimilar competition", "talent compensation costs"])
    if anchors:
        return " ".join(dict.fromkeys(anchors))
    return question


def has_latest_object_id(object_ids: list[str]) -> bool:
    if not object_ids:
        return False
    return max((object_id_recency_key(object_id) for object_id in object_ids), default=(0, 0, 0)) >= (2025, 1, 2)


def object_id_recency_key(object_id: str) -> tuple[int, int, int]:
    text = str(object_id or "").upper()
    match = re.search(r"(?:CY|FY)?(20\d{2}|19\d{2})(?:Q([1-4]))?", text)
    year = int(match.group(1)) if match else 0
    quarter = int(match.group(2) or 0) if match else 0
    doc_score = 2 if "10-Q" in text or "10Q" in text else 1 if "10-K" in text or "10K" in text else 0
    return (year, quarter, doc_score)


def trace_object_candidates_from_query(query_payload: Mapping[str, Any]) -> list[str]:
    output: list[str] = []
    for item in query_payload_items(query_payload):
        obj = item.get("object") if isinstance(item.get("object"), Mapping) else {}
        object_id = item.get("id") or item.get("object_id") or obj.get("id")
        if object_id:
            output.append(str(object_id))
    return dedupe(output)


def query_payload_items(query_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    output: list[Mapping[str, Any]] = []
    for key in ("results", "bundles", "items", "candidates"):
        values = query_payload.get(key)
        if isinstance(values, list):
            output.extend(item for item in values if isinstance(item, Mapping))
    results_by_ticker = query_payload.get("results_by_ticker")
    if isinstance(results_by_ticker, Mapping):
        for values in results_by_ticker.values():
            if isinstance(values, list):
                output.extend(item for item in values if isinstance(item, Mapping))
    return output


def trace_budget_for_mode(
    research_mode: str,
    max_traces: int,
    context: Mapping[str, Any],
) -> int:
    kernel = context.get("kernel") if isinstance(context.get("kernel"), Mapping) else {}
    agent_autonomy = context.get("agent_autonomy") if isinstance(context.get("agent_autonomy"), Mapping) else {}
    allowed = set(str(item) for item in (kernel.get("allowed_next_tools") or agent_autonomy.get("allowed_next_tools") or []))
    do_not_call = set(str(item) for item in (kernel.get("do_not_call") or context.get("do_not_call") or []))
    may_continue = agent_autonomy.get("may_continue_research")
    if kernel.get("status") == "sufficient_for_default_answer" and not allowed:
        return 0
    if may_continue is False:
        return 0
    if "krw_ontology_trace" in do_not_call or "trace" in do_not_call:
        return 0
    if allowed and "krw_ontology_trace" not in allowed and "trace" not in allowed:
        return 0
    if research_mode == "fast":
        return 0
    if research_mode == "deep":
        return max(0, max_traces)
    return min(max(0, max_traces), 2)


def tool_metrics(tool_calls: list[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(call.get("tool") or "unknown") for call in tool_calls)
    return {
        "tool_call_count": len(tool_calls),
        "query_context_count": counts.get("krw_ontology_query_context", 0),
        "query_count": counts.get("krw_ontology_query", 0),
        "retrieve_count": counts.get("krw_ontology_retrieve", 0),
        "trace_count": counts.get("krw_ontology_trace", 0),
        "chain_count": counts.get("krw_ontology_chain", 0),
        "compare_count": counts.get("krw_ontology_compare", 0),
    }


def primary_pack_name(kernel: Mapping[str, Any], research_pack: Mapping[str, Any]) -> str | None:
    intent = str(kernel.get("intent") or "")
    candidates = {
        "metric_series": "metric_series_pack",
        "company_overview": "business_profile_pack",
        "risk_thesis": "risk_mechanism_pack",
        "comparison": "comparison_view",
        "direct_exposure": "direct_exposure_pack",
        "valuation_or_price_target": "scope_guard_pack",
    }
    preferred = candidates.get(intent)
    if preferred and research_pack.get(preferred):
        return preferred
    for key in (
        "metric_series_pack",
        "business_profile_pack",
        "risk_mechanism_pack",
        "comparison_view",
        "direct_exposure_pack",
        "scope_guard_pack",
        "projection_pack",
        "company_topic_pack",
    ):
        if research_pack.get(key):
            return key
    return None


def answer_diagnostics_for(
    question: str,
    answer: str,
    tool_calls: list[Mapping[str, Any]],
    research_mode: str,
) -> dict[str, Any]:
    flags: list[str] = []
    latest_requested = bool(re.search(r"최근|latest|recent|최근\s*분기|latest quarter", question, re.I))
    fy_label_used = bool(re.search(r"\bFY\s?20\d{2}\b", answer, re.I))
    internal_leak_terms = internal_leak_terms_for(question, answer)
    latest_quarter_mentioned = bool(re.search(r"\b(10-q|quarter|q[1-4]|분기|3개월|six months|three months)\b", answer, re.I))
    metrics = tool_metrics(tool_calls)
    if fy_label_used:
        flags.append("fy_label_used")
    if internal_leak_terms:
        flags.append("internal_term_leak")
    if latest_requested and not latest_quarter_mentioned:
        flags.append("latest_question_without_quarter_context")
    if research_mode != "deep" and metrics["retrieve_count"] > 0:
        flags.append("retrieve_used_outside_deep")
    if research_mode == "fast" and (metrics["trace_count"] + metrics["chain_count"]) > 0:
        flags.append("fast_mode_trace_or_chain_used")
    return {
        "flags": flags,
        "fy_label_used": fy_label_used,
        "latest_requested": latest_requested,
        "latest_quarter_mentioned": latest_quarter_mentioned,
        "internal_leak_terms": internal_leak_terms,
        **metrics,
    }


def select_best_trace(question: str, traces: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not traces:
        return None
    if re.search(r"최근|latest|recent|최근\s*분기|latest quarter", question, re.I):
        return max(traces, key=trace_recency_key)
    q_terms = content_terms(question)
    return max(traces, key=lambda trace: trace_relevance_score(q_terms, trace))


def trace_recency_key(trace: Mapping[str, Any]) -> tuple[int, int, int, float]:
    doc = trace.get("document") if isinstance(trace.get("document"), Mapping) else {}
    period = str(doc.get("period") or "")
    doc_type = str(doc.get("document_type") or "").upper()
    match = re.search(r"(?:CY|FY)?(20\d{2}|19\d{2})(?:Q([1-4]))?", period.upper())
    year = int(match.group(1)) if match else 0
    quarter = int(match.group(2) or 0) if match else 0
    doc_score = 2 if doc_type == "10-Q" else 1 if doc_type == "10-K" else 0
    return (year, quarter, doc_score, trace_relevance_score(content_terms(str(doc)), trace))


def compose_answer(case: PromptCase, *, context: Mapping[str, Any], trace: Mapping[str, Any] | None) -> str:
    kernel = context.get("kernel") if isinstance(context.get("kernel"), Mapping) else {}
    intent = str(kernel.get("intent") or "general_research")
    answerability = context.get("answerability") if isinstance(context.get("answerability"), Mapping) else {}
    evidence = evidence_summary(context=context, trace=trace)
    doc_label = trace_document_label(trace)
    ticker_label = f"{case.ticker}"
    caveat = caveat_for(intent, answerability, trace)
    mechanism = mechanism_sentence(case, intent=intent, evidence=evidence)

    lines = [
        f"결론: {ticker_label}에 대해서는 공시자료 기준으로 {short_conclusion(case, intent=intent, evidence=evidence)}",
        "",
        "공시에서 확인되는 내용:",
        f"- 질문 초점: {question_focus_summary(case.question)}",
    ]
    for bullet in evidence[:3]:
        lines.append(f"- {bullet}")
    if not evidence:
        lines.append("- 현재 선택된 근거만으로는 세부 항목을 강하게 단정하기 어렵습니다.")
    if doc_label:
        lines.append(f"- 확인 근거는 {doc_label}에서 나온 내용입니다.")
    lines.extend(
        [
            "",
            "해석:",
            f"- {mechanism}",
            f"- {caveat}",
            "",
            "주의할 점:",
            "- 위 내용은 회사 공시에서 확인되는 사업·재무 경로를 정리한 것이며, 시장가격이나 외부 전망 자체를 예측한 것은 아닙니다.",
        ]
    )
    return normalize_user_period_labels("\n".join(lines))


def normalize_user_period_labels(answer: str) -> str:
    return re.sub(r"\bFY\s?(20\d{2})(?:\s?Q([1-4]))?\b", lambda match: f"CY{match.group(1)}" + (f"Q{match.group(2)}" if match.group(2) else ""), answer)


def question_focus_summary(question: str) -> str:
    terms = sorted(content_terms(question))
    if not terms:
        return "사용자가 요청한 사업·재무 축을 같은 기준으로 확인합니다."
    return ", ".join(terms[:10])


def evidence_summary(*, context: Mapping[str, Any], trace: Mapping[str, Any] | None) -> list[str]:
    output: list[str] = []
    if trace:
        obj = trace.get("object") if isinstance(trace.get("object"), Mapping) else {}
        for key in ("assumption_text", "text", "description", "summary", "name"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                output.append(clean_sentence(value))
                break
        evidence = trace.get("evidence") if isinstance(trace.get("evidence"), Mapping) else {}
        for claim in evidence.get("claims") or []:
            if isinstance(claim, Mapping) and isinstance(claim.get("text"), str):
                output.append(clean_sentence(claim["text"]))
                break
        for quote in evidence.get("quotes") or []:
            if isinstance(quote, Mapping) and isinstance(quote.get("text"), str):
                quote_text = compact_quote_text(quote["text"])
                if quote_text:
                    output.append(f"원문 표/문장에는 {quote_text} 등이 확인됩니다.")
                break
    if output:
        return dedupe(output)[:4]

    pack = context.get("research_pack") if isinstance(context.get("research_pack"), Mapping) else {}
    follow_up_pack = pack.get("follow_up_query_pack") if isinstance(pack.get("follow_up_query_pack"), Mapping) else {}
    for item in query_payload_items(follow_up_pack):
        summary = item.get("text") or item.get("summary") or item.get("name")
        obj = item.get("object") if isinstance(item.get("object"), Mapping) else {}
        if not summary:
            summary = obj.get("text") or obj.get("summary") or obj.get("description") or obj.get("name")
        if summary:
            output.append(clean_sentence(str(summary)))
            break
    if output:
        return dedupe(output)[:4]

    risk_pack = pack.get("risk_mechanism_pack") if isinstance(pack.get("risk_mechanism_pack"), Mapping) else {}
    for item in risk_pack.get("risk_channels") or []:
        if isinstance(item, Mapping):
            support = item.get("support_summary")
            path = item.get("financial_path")
            implication = item.get("implication")
            if support:
                output.append(clean_sentence(str(support)))
            if path:
                output.append("재무 경로: " + " -> ".join(str(part) for part in path))
            if implication:
                output.append(clean_sentence(str(implication)))
            if output:
                return dedupe(output)[:4]

    business_pack = pack.get("business_profile_pack") if isinstance(pack.get("business_profile_pack"), Mapping) else {}
    for item in business_pack.get("current_drivers") or business_pack.get("business_segments") or []:
        if isinstance(item, Mapping):
            summary = item.get("driver_summary") or item.get("segment_or_topic")
            if summary:
                output.append(clean_sentence(str(summary)))
                break
    if output:
        return dedupe(output)[:4]

    metric_pack = pack.get("metric_series_pack") if isinstance(pack.get("metric_series_pack"), Mapping) else {}
    for series in metric_pack.get("series") or []:
        if isinstance(series, Mapping):
            label = series.get("label")
            periods = series.get("periods") or []
            if label:
                output.append(f"{label} metric series is available across {len(periods)} periods.")
                break

    company_topic = pack.get("company_topic_pack") if isinstance(pack.get("company_topic_pack"), Mapping) else {}
    for item in company_topic.get("top_candidates") or []:
        if isinstance(item, Mapping):
            label = item.get("topic_label") or item.get("topic_summary") or item.get("tier")
            if label:
                if str(label).startswith("traceable_"):
                    output.append("질문과 관련된 회사 공시 후보가 확인됩니다.")
                else:
                    output.append(f"질문과 관련된 공시 후보는 {clean_sentence(str(label))} 성격입니다.")
                break
    projection = pack.get("projection_pack") if isinstance(pack.get("projection_pack"), Mapping) else {}
    for item in projection.get("candidates") or []:
        if isinstance(item, Mapping):
            label = item.get("summary") or item.get("name") or item.get("label")
            if label:
                output.append(clean_sentence(str(label)))
                break
    return dedupe(output)[:4]


def short_conclusion(case: PromptCase, *, intent: str, evidence: list[str]) -> str:
    if intent == "company_overview":
        return "사업 부문별 매출·마진 기여를 설명할 수 있는 관련 근거가 있습니다."
    if intent == "risk_thesis":
        return "성장 thesis를 흔들 수 있는 비용·수요·규제 채널을 관련 맥락으로 설명할 수 있습니다."
    if intent == "comparison":
        return "비교 대상 항목들이 마진, 현금흐름, 투자 부담으로 연결되는 경로를 구분해 설명할 수 있습니다."
    if intent == "direct_exposure":
        return "직접 근거와 관련 맥락을 분리해 판단해야 합니다."
    if evidence:
        return "질문과 관련된 공시 근거가 확인됩니다."
    return "일부 관련 맥락은 있으나 강한 단정에는 추가 확인이 필요합니다."


def mechanism_sentence(case: PromptCase, *, intent: str, evidence: list[str]) -> str:
    if intent == "company_overview":
        return "매출원이 여러 사업·제품·서비스로 나뉘는 경우, 성장성은 어느 부문의 매출 기여가 커지는지와 해당 부문의 마진 구조가 함께 움직이는지로 봐야 합니다."
    if intent == "risk_thesis":
        return "리스크는 매출을 바로 낮춘다고 단정하기보다, 비용 증가·수요 둔화·규제 지연·공급 제약 같은 중간 경로를 통해 마진과 현금흐름을 압박하는지로 해석하는 편이 안전합니다."
    if intent == "comparison":
        return "비교형 질문에서는 한 항목이 더 많이 언급됐다는 사실보다, 동일한 재무 채널에서 어떤 항목이 더 직접적으로 연결되는지가 중요합니다."
    if intent == "direct_exposure":
        return "직접 노출은 질문의 특정 변수와 회사 공시가 명시적으로 연결될 때만 인정하고, 넓은 비용·공급망 맥락은 별도로 분리해야 합니다."
    return "공시 근거는 결론의 방향을 제한하는 역할을 하며, 인과관계는 회사가 직접 연결한 범위 안에서만 강하게 말할 수 있습니다."


def caveat_for(intent: str, answerability: Mapping[str, Any], trace: Mapping[str, Any] | None) -> str:
    if intent == "direct_exposure" and not answerability.get("direct_answerable"):
        return "직접 노출 근거가 확인되지 않는 경우에는 관련 비용·공급망 맥락을 직접 노출로 바꿔 말하지 않아야 합니다."
    if trace is None:
        return "현재 답변은 압축된 공시 후보 기준이므로, 강한 수치나 원문 인용이 필요한 경우 선택 근거를 추가 확인하는 것이 좋습니다."
    return "다만 한 개의 대표 근거만 확장했으므로, 최종 투자 판단에는 같은 경로의 추가 기간·부문 근거를 함께 확인하는 것이 안전합니다."


def score_answer(
    case: PromptCase,
    answer: str,
    *,
    context: Mapping[str, Any],
    trace: Mapping[str, Any] | None,
) -> dict[str, Any]:
    kernel = context.get("kernel") if isinstance(context.get("kernel"), Mapping) else {}
    answerability = context.get("answerability") if isinstance(context.get("answerability"), Mapping) else {}
    leak_terms = internal_leak_terms_for(case.question, answer)
    checks: dict[str, float] = {
        "has_conclusion": 8.0 if "결론:" in answer else 0.0,
        "mentions_ticker": 6.0 if case.ticker in answer else 0.0,
        "has_evidence_section": 10.0 if "공시에서 확인되는 내용:" in answer else 0.0,
        "has_interpretation_section": 10.0 if "해석:" in answer else 0.0,
        "has_caveat": 8.0 if "주의할 점:" in answer or "다만" in answer else 0.0,
        "evidence_specificity": evidence_specificity_score(answer, trace),
        "question_relevance": question_relevance_score(case.question, answer, trace),
        "intent_alignment": 10.0 if intent_keyword_present(str(kernel.get("intent")), answer) else 4.0,
        "directness_discipline": 10.0 if directness_ok(answer, answerability) else 0.0,
        "no_internal_leak": 10.0 if not leak_terms else 0.0,
    }
    score = round(min(100.0, sum(checks.values())), 1)
    return {
        "score": score,
        "grade": grade(score),
        "checks": checks,
        "internal_leak_terms": leak_terms,
        "answer_chars": len(answer),
    }


def intent_keyword_present(intent: str, answer: str) -> bool:
    if intent == "company_overview":
        return "매출" in answer or "사업" in answer
    if intent == "risk_thesis":
        return "리스크" in answer or "압박" in answer or "비용" in answer
    if intent == "comparison":
        return "비교" in answer or "동일한" in answer
    if intent == "direct_exposure":
        return "직접" in answer
    return True


def directness_ok(answer: str, answerability: Mapping[str, Any]) -> bool:
    if answerability.get("direct_answerable") is False:
        bad_phrases = ("직접 노출되어 있습니다", "직접 수혜가 확인됩니다", "직접 영향이 확인됩니다")
        return not any(phrase in answer for phrase in bad_phrases)
    return True


def grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def trace_document_label(trace: Mapping[str, Any] | None) -> str | None:
    if not trace:
        return None
    doc = trace.get("document") if isinstance(trace.get("document"), Mapping) else {}
    ticker = doc.get("ticker")
    doc_type = doc.get("document_type")
    period = doc.get("period")
    parts = [str(part) for part in (ticker, period, doc_type) if part]
    return " ".join(parts) if parts else None


def evidence_specificity_score(answer: str, trace: Mapping[str, Any] | None) -> float:
    if not trace:
        if "질문과 관련된 회사 공시 후보가 확인됩니다" in answer:
            return 5.0
        return 8.0
    obj = trace.get("object") if isinstance(trace.get("object"), Mapping) else {}
    evidence = trace.get("evidence") if isinstance(trace.get("evidence"), Mapping) else {}
    has_claim = bool(evidence.get("claims"))
    has_quote = bool(evidence.get("quotes"))
    has_specific_obj = any(isinstance(obj.get(key), str) and obj.get(key) for key in ("name", "assumption_text", "text", "description"))
    return min(20.0, 8.0 + (5.0 if has_specific_obj else 0.0) + (4.0 if has_claim else 0.0) + (3.0 if has_quote else 0.0))


def question_relevance_score(question: str, answer: str, trace: Mapping[str, Any] | None) -> float:
    q_terms = content_terms(question)
    if not q_terms:
        return 10.0
    answer_terms = content_terms(answer)
    overlap = len(q_terms & answer_terms)
    ratio = overlap / max(1, min(len(q_terms), 8))
    trace_bonus = 0.0
    if trace:
        trace_ratio = trace_relevance_score(q_terms, trace)
        trace_bonus = min(4.0, trace_ratio * 4.0)
    return round(min(18.0, ratio * 14.0 + trace_bonus), 1)


def trace_relevance_score(q_terms: set[str], trace: Mapping[str, Any]) -> float:
    if not q_terms:
        return 0.0
    text = trace_text(trace)
    t_terms = content_terms(text)
    return len(q_terms & t_terms) / max(1, min(len(q_terms), 8))


def trace_text(trace: Mapping[str, Any]) -> str:
    parts: list[str] = []
    obj = trace.get("object") if isinstance(trace.get("object"), Mapping) else {}
    for value in obj.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value if isinstance(item, str))
    evidence = trace.get("evidence") if isinstance(trace.get("evidence"), Mapping) else {}
    for key in ("claims", "quotes"):
        for item in evidence.get(key) or []:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
    return " ".join(parts)


def content_terms(text: str) -> set[str]:
    lowered = text.casefold()
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+&.-]{2,}|[가-힣]{2,}", lowered)
    stop = {
        "공시",
        "기준",
        "정리",
        "비교",
        "어떻게",
        "어떤",
        "의미",
        "에서",
        "으로",
        "하고",
        "있는",
        "관련",
        "내용",
        "확인",
        "growth",
        "sales",
        "revenue",
        "margin",
        "risk",
        "thesis",
        "매출",
        "성장",
        "마진",
        "리스크",
    }
    return {token for token in tokens if token not in stop and len(token) >= 2}


def internal_leak_terms_for(question: str, answer: str) -> list[str]:
    question_terms = {match.group(0).casefold() for match in INTERNAL_LEAK_RE.finditer(question)}
    return sorted(
        {
            match.group(0)
            for match in INTERNAL_LEAK_RE.finditer(answer)
            if match.group(0).casefold() not in question_terms
        }
    )


def compact_quote_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value.replace("|", " ")).strip()
    text = re.sub(r"\s{2,}", " ", text)
    if len(text) > 220:
        text = text[:217].rstrip() + "..."
    return text


def clean_sentence(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text[:320].rstrip() + ("..." if len(text) > 320 else "")


def dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.casefold()
        if normalized not in seen:
            seen.add(normalized)
            output.append(value)
    return output


def stable_short(value: str) -> str:
    return str(abs(hash(value)) % 10_000_000)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    compact_scores = [float(record["quality"]["compact_only"]["score"]) for record in records if record["status"] == "ok"]
    trace_scores = [float(record["quality"]["trace_augmented"]["score"]) for record in records if record["status"] == "ok"]
    elapsed = [float(record["elapsed_sec"]) for record in records]
    tool_call_counts = [float((record.get("tool_metrics") or {}).get("tool_call_count") or 0) for record in records]
    trace_counts = [float((record.get("tool_metrics") or {}).get("trace_count") or 0) for record in records]
    intent_counts = Counter((record.get("kernel") or {}).get("intent") or "missing" for record in records)
    by_intent = summarize_by(records, lambda record: (record.get("kernel") or {}).get("intent") or "missing")
    diagnostic_flags = Counter(
        flag
        for record in records
        for diag in (record.get("answer_diagnostics") or {}).values()
        for flag in diag.get("flags", [])
    )
    return {
        "case_count": len(records),
        "ok_count": sum(1 for record in records if record["status"] == "ok"),
        "error_count": sum(1 for record in records if record["status"] != "ok"),
        "avg_elapsed_sec": mean(elapsed),
        "median_elapsed_sec": median(elapsed),
        "p95_elapsed_sec": percentile(elapsed, 95),
        "max_elapsed_sec": round(max(elapsed), 3) if elapsed else 0,
        "avg_tool_call_count": mean(tool_call_counts),
        "max_tool_call_count": round(max(tool_call_counts), 1) if tool_call_counts else 0,
        "avg_trace_count": mean(trace_counts),
        "avg_compact_score": mean(compact_scores),
        "avg_trace_score": mean(trace_scores),
        "avg_trace_delta": round(mean(trace_scores) - mean(compact_scores), 3) if compact_scores and trace_scores else 0,
        "min_trace_score": round(min(trace_scores), 1) if trace_scores else 0,
        "compact_grade_counts": dict(Counter(record["quality"]["compact_only"]["grade"] for record in records)),
        "trace_grade_counts": dict(Counter(record["quality"]["trace_augmented"]["grade"] for record in records)),
        "intent_counts": dict(sorted(intent_counts.items())),
        "by_intent": by_intent,
        "diagnostic_flag_counts": dict(sorted(diagnostic_flags.items())),
        "mode_violation_cases": [
            {
                "case_id": record["case_id"],
                "research_mode": record.get("research_mode"),
                "tool_metrics": record.get("tool_metrics"),
                "answer_flags": {
                    key: value.get("flags", [])
                    for key, value in (record.get("answer_diagnostics") or {}).items()
                },
            }
            for record in records
            if any((value.get("flags") or []) for value in (record.get("answer_diagnostics") or {}).values())
        ],
        "internal_leak_cases": [
            {
                "case_id": record["case_id"],
                "compact": record["quality"]["compact_only"]["internal_leak_terms"],
                "trace": record["quality"]["trace_augmented"]["internal_leak_terms"],
            }
            for record in records
            if record["quality"]["compact_only"]["internal_leak_terms"]
            or record["quality"]["trace_augmented"]["internal_leak_terms"]
        ],
        "lowest_trace_scores": [
            {
                "case_id": record["case_id"],
                "score": record["quality"]["trace_augmented"]["score"],
                "grade": record["quality"]["trace_augmented"]["grade"],
                "intent": (record.get("kernel") or {}).get("intent"),
                "question": record["question"],
            }
            for record in sorted(records, key=lambda item: float(item["quality"]["trace_augmented"]["score"]))[:10]
        ],
    }


def summarize_by(records: list[dict[str, Any]], key_fn: Any) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(key_fn(record))].append(record)
    output: dict[str, dict[str, Any]] = {}
    for key, rows in sorted(grouped.items()):
        output[key] = {
            "count": len(rows),
            "avg_elapsed_sec": mean([float(row["elapsed_sec"]) for row in rows]),
            "avg_tool_call_count": mean([float((row.get("tool_metrics") or {}).get("tool_call_count") or 0) for row in rows]),
            "avg_compact_score": mean([float(row["quality"]["compact_only"]["score"]) for row in rows]),
            "avg_trace_score": mean([float(row["quality"]["trace_augmented"]["score"]) for row in rows]),
        }
    return output


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0


def median(values: list[float]) -> float:
    return round(statistics.median(values), 3) if values else 0


def percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return round(sorted_values[0], 3)
    rank = (len(sorted_values) - 1) * (pct / 100)
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return round(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight, 3)


def to_markdown(summary: Mapping[str, Any], records: list[dict[str, Any]]) -> str:
    lines = [
        "# KRW ResearchKernel Agent E2E 30Q",
        "",
        f"- cases: {summary['case_count']}",
        f"- ok: {summary['ok_count']}",
        f"- errors: {summary['error_count']}",
        f"- avg_elapsed_sec: {summary['avg_elapsed_sec']}",
        f"- p95_elapsed_sec: {summary['p95_elapsed_sec']}",
        f"- max_elapsed_sec: {summary['max_elapsed_sec']}",
        f"- avg_tool_call_count: {summary['avg_tool_call_count']}",
        f"- max_tool_call_count: {summary['max_tool_call_count']}",
        f"- avg_trace_count: {summary['avg_trace_count']}",
        f"- avg_compact_score: {summary['avg_compact_score']}",
        f"- avg_trace_score: {summary['avg_trace_score']}",
        f"- avg_trace_delta: {summary['avg_trace_delta']}",
        f"- min_trace_score: {summary['min_trace_score']}",
        f"- compact_grade_counts: `{json.dumps(summary['compact_grade_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- trace_grade_counts: `{json.dumps(summary['trace_grade_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- internal_leak_cases: {len(summary['internal_leak_cases'])}",
        f"- diagnostic_flag_counts: `{json.dumps(summary['diagnostic_flag_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Cases",
        "",
        "| case | sec | intent | primary pack | tools | trace | compact | trace score | delta |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for record in records:
        kernel = record.get("kernel") or {}
        lines.append(
            "| {case} | {sec} | {intent} | {primary_pack} | {tools} | {trace} | {compact} | {trace_score} | {delta} |".format(
                case=record["case_id"],
                sec=record["elapsed_sec"],
                intent=kernel.get("intent") or "",
                primary_pack=record.get("primary_pack") or "",
                tools=(record.get("tool_metrics") or {}).get("tool_call_count") or 0,
                trace=(record.get("tool_metrics") or {}).get("trace_count") or 0,
                compact=record["quality"]["compact_only"]["score"],
                trace_score=record["quality"]["trace_augmented"]["score"],
                delta=record["quality"]["delta"],
            )
        )
    lines.extend(["", "## Final answers", ""])
    for record in records:
        lines.extend(
            [
                f"### {record['case_id']} compact_only",
                "",
                record["answers"]["compact_only"],
                "",
                f"### {record['case_id']} trace_augmented",
                "",
                record["answers"]["trace_augmented"],
                "",
            ]
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
