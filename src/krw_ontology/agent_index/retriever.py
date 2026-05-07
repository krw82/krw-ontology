"""Natural-language retrieval planner over the ontology store.

This layer does not let an agent write SQL or inspect raw JSONL files. It
converts a user question into a constrained QueryPlan, executes the plan
through OntologyStore, and returns evidence bundles with an audit trail.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Callable, Iterable, Protocol

from krw_ontology.agent_index.store import DEFAULT_QUERY_TYPES, OntologyStore

PlannerFn = Callable[[str, dict[str, Any]], "QueryPlan"]
RerankerFn = Callable[[list[dict[str, Any]], "QueryPlan"], list[dict[str, Any]]]

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.:-]*")
_PERIOD_RE = re.compile(r"\bFY\d{4}(?:Q[1-4?])?\b", re.IGNORECASE)


@dataclass(frozen=True)
class QueryPlan:
    """Constrained retrieval plan produced from a user question."""

    question: str
    intent: str = "evidence_search"
    tickers: list[str] = field(default_factory=list)
    document_types: list[str] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)
    period_policy: str = "all"
    topics: list[str] = field(default_factory=list)
    metric: str | None = None
    object_types: list[str] = field(default_factory=lambda: list(DEFAULT_QUERY_TYPES))
    include_rejected: bool = False
    require_trace: bool = True
    limit: int = 20

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QueryPlanner(Protocol):
    def plan(self, question: str, catalog: dict[str, Any]) -> QueryPlan:
        """Produce a constrained QueryPlan."""


class DefaultQueryPlanner:
    """Deterministic planner used before an LLM planner/reranker is wired in."""

    def plan(self, question: str, catalog: dict[str, Any]) -> QueryPlan:
        question_lower = question.lower()
        key_risk_question = _asks_key_risks(question_lower)
        tickers = _extract_tickers(question, catalog.get("companies", []))
        document_types = _extract_document_types(question_lower)
        if key_risk_question and not document_types:
            document_types = ["10-K"]
        periods = [period.upper() for period in _PERIOD_RE.findall(question)]
        period_policy = "latest" if _asks_latest(question_lower) or key_risk_question else "all"
        metric = _extract_metric(question_lower)
        intent = _extract_intent(question_lower, tickers, metric)
        topics = _extract_topics(question, question_lower, metric, key_risk_question=key_risk_question)
        object_types = _extract_object_types(question_lower, key_risk_question=key_risk_question)
        include_rejected = _asks_include_rejected(question_lower)

        return QueryPlan(
            question=question,
            intent=intent,
            tickers=tickers,
            document_types=document_types,
            periods=periods,
            period_policy=period_policy,
            topics=topics,
            metric=metric,
            object_types=object_types,
            include_rejected=include_rejected,
            limit=20,
        )


class AgentRetriever:
    """Agent-safe retrieval facade over OntologyStore."""

    def __init__(
        self,
        store: OntologyStore,
        *,
        planner: QueryPlanner | PlannerFn | None = None,
        reranker: RerankerFn | None = None,
    ):
        self.store = store
        self.planner = planner or DefaultQueryPlanner()
        self.reranker = reranker

    def plan(
        self,
        question: str,
        *,
        tickers: Iterable[str] | None = None,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        include_rejected: bool | None = None,
        limit: int | None = None,
    ) -> QueryPlan:
        """Return a QueryPlan, applying explicit caller constraints last."""
        catalog = self.catalog()
        if callable(self.planner) and not hasattr(self.planner, "plan"):
            plan = self.planner(question, catalog)
        else:
            plan = self.planner.plan(question, catalog)  # type: ignore[union-attr]

        return QueryPlan(
            question=plan.question,
            intent=plan.intent,
            tickers=[ticker.upper() for ticker in (tickers or plan.tickers)],
            document_types=list(document_types or plan.document_types),
            periods=[period.upper() for period in (periods or plan.periods)],
            period_policy=plan.period_policy,
            topics=plan.topics,
            metric=plan.metric,
            object_types=plan.object_types,
            include_rejected=plan.include_rejected if include_rejected is None else include_rejected,
            require_trace=plan.require_trace,
            limit=limit or plan.limit,
        )

    def retrieve(
        self,
        question: str,
        *,
        tickers: Iterable[str] | None = None,
        document_types: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        include_rejected: bool | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Plan and execute an agent-safe retrieval request."""
        plan = self.plan(
            question,
            tickers=tickers,
            document_types=document_types,
            periods=periods,
            include_rejected=include_rejected,
            limit=limit,
        )
        resolved_periods = self._resolve_periods(plan)
        executed_queries: list[dict[str, Any]] = []

        if plan.intent == "quality_check":
            quality = self._quality_for_plan(plan, resolved_periods)
            return {
                "answerable": bool(quality["documents"]),
                "plan": plan.to_dict(),
                "resolved_periods": resolved_periods,
                "results": [],
                "quality": quality,
                "audit": {"executed_queries": executed_queries},
            }

        if plan.intent == "compare":
            result = self._execute_compare(plan, resolved_periods, executed_queries)
            return {
                "answerable": any(result["results"].values()),
                "plan": plan.to_dict(),
                "resolved_periods": resolved_periods,
                "results": result["results"],
                "compare": result,
                "audit": {"executed_queries": executed_queries},
            }

        candidates = self._execute_search(plan, resolved_periods, executed_queries)
        if self.reranker:
            candidates = self.reranker(candidates, plan)
        candidates = _dedupe_bundles(candidates)[: plan.limit]

        return {
            "answerable": bool(candidates),
            "plan": plan.to_dict(),
            "resolved_periods": resolved_periods,
            "results": candidates,
            "audit": {"executed_queries": executed_queries},
        }

    def catalog(self) -> dict[str, Any]:
        documents = self.store.list_documents()
        return {
            "companies": self.store.list_companies(),
            "documents": documents,
            "document_types": sorted({doc["document_type"] for doc in documents}),
        }

    def _execute_search(
        self,
        plan: QueryPlan,
        resolved_periods: list[str],
        executed_queries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        topics = plan.topics or [None]
        candidates: list[dict[str, Any]] = []
        per_topic_limit = max(plan.limit, 10)
        for topic in topics:
            query = {
                "topic": topic,
                "tickers": plan.tickers or None,
                "document_types": plan.document_types or None,
                "periods": resolved_periods or None,
                "object_types": plan.object_types,
                "include_rejected": plan.include_rejected,
                "limit": per_topic_limit,
            }
            executed_queries.append(query)
            candidates.extend(self.store.query(**query))
        return candidates

    def _execute_compare(
        self,
        plan: QueryPlan,
        resolved_periods: list[str],
        executed_queries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if len(plan.tickers) < 2:
            return {
                "mode": "compare",
                "topic": plan.topics[0] if plan.topics else None,
                "metric": plan.metric,
                "tickers": plan.tickers,
                "results": {},
                "warning": "compare requires at least two tickers",
            }
        topic = plan.topics[0] if plan.topics else None
        query = {
            "tickers": plan.tickers,
            "topic": topic,
            "metric": plan.metric,
            "document_types": plan.document_types or None,
            "periods": resolved_periods or None,
            "limit_per_ticker": max(1, min(plan.limit, 10)),
        }
        executed_queries.append(query)
        return self.store.compare(**query)

    def _quality_for_plan(self, plan: QueryPlan, resolved_periods: list[str]) -> dict[str, Any]:
        documents = []
        events = []
        summaries = []
        tickers = plan.tickers or [None]
        document_types = plan.document_types or [None]
        periods = resolved_periods or plan.periods or [None]
        for ticker in tickers:
            for document_type in document_types:
                for period in periods:
                    quality = self.store.quality(
                        ticker=ticker,
                        document_type=document_type,
                        period=period,
                    )
                    documents.extend(quality["documents"])
                    events.extend(quality["events"])
                    summaries.append(quality["summary"])
        return {
            "documents": _dedupe_documents(documents),
            "events": _dedupe_quality_events(events),
            "summary": {
                "documents": len(_dedupe_documents(documents)),
                "events": len(_dedupe_quality_events(events)),
                "rejected_objects": sum(summary["rejected_objects"] for summary in summaries),
                "batch_failures": sum(summary["batch_failures"] for summary in summaries),
                "section_warnings": sum(summary["section_warnings"] for summary in summaries),
            },
        }

    def _resolve_periods(self, plan: QueryPlan) -> list[str]:
        if plan.periods:
            return plan.periods
        if plan.period_policy != "latest":
            return []
        documents = []
        if plan.tickers:
            for ticker in plan.tickers:
                documents.extend(
                    self.store.list_documents(
                        ticker=ticker,
                        document_types=plan.document_types or None,
                    )
                )
        else:
            documents = self.store.list_documents(document_types=plan.document_types or None)

        latest_by_scope: dict[tuple[str, str], dict[str, Any]] = {}
        for doc in documents:
            key = (doc["ticker"], doc["document_type"])
            current = latest_by_scope.get(key)
            if current is None or _period_sort_key(doc["period"]) > _period_sort_key(current["period"]):
                latest_by_scope[key] = doc
        return sorted({doc["period"] for doc in latest_by_scope.values()})


def _extract_tickers(question: str, companies: Iterable[str]) -> list[str]:
    company_set = {company.upper() for company in companies}
    tokens = {token.upper().strip(".,:;()[]") for token in _TOKEN_RE.findall(question)}
    return sorted(token for token in tokens if token in company_set)


def _extract_document_types(question_lower: str) -> list[str]:
    doc_types = []
    if any(token in question_lower for token in ("10-q", "10q", "quarter", "분기")):
        doc_types.append("10-Q")
    if any(token in question_lower for token in ("10-k", "10k", "annual", "연간", "장기")):
        doc_types.append("10-K")
    return doc_types


def _asks_latest(question_lower: str) -> bool:
    return any(token in question_lower for token in ("latest", "recent", "current", "최근", "이번", "현재"))


def _asks_key_risks(question_lower: str) -> bool:
    return any(
        token in question_lower
        for token in (
            "key risks",
            "main risks",
            "major risks",
            "principal risks",
            "risk overview",
            "주요 리스크",
            "핵심 리스크",
            "가장 큰 리스크",
        )
    )


def _asks_include_rejected(question_lower: str) -> bool:
    return any(token in question_lower for token in ("include rejected", "rejected 포함", "리젝트 포함"))


def _extract_intent(question_lower: str, tickers: list[str], metric: str | None) -> str:
    if any(token in question_lower for token in ("quality", "품질", "rejected", "section_quality", "실패")):
        return "quality_check"
    if any(token in question_lower for token in ("compare", "comparison", " vs ", " versus ", "비교")):
        return "compare"
    if len(tickers) >= 2 and metric:
        return "compare"
    return "evidence_search"


def _extract_metric(question_lower: str) -> str | None:
    metric_aliases = {
        "capex": ("capex", "capital expenditure", "capital expenditures", "설비투자"),
        "revenue": ("revenue", "sales", "매출"),
        "gross_margin": ("gross margin", "매출총이익률"),
        "operating_margin": ("operating margin", "영업이익률"),
        "free_cash_flow": ("free cash flow", "fcf", "잉여현금흐름"),
    }
    for metric, aliases in metric_aliases.items():
        if any(alias in question_lower for alias in aliases):
            return metric
    return None


def _extract_topics(
    question: str,
    question_lower: str,
    metric: str | None,
    *,
    key_risk_question: bool = False,
) -> list[str]:
    if key_risk_question:
        return ["risk"]
    topic_aliases = [
        (
            ("margin pressure", "pricing pressure", "마진 압박", "가격 압박"),
            [
                "margin pressure",
                "pricing pressure",
                "lower prices",
                "competitive discounts",
                "margin compression",
            ],
        ),
        (
            ("credit risk", "신용 리스크", "credit loss"),
            ["credit risk", "credit losses", "allowance for credit losses"],
        ),
        (
            ("supply chain", "공급망"),
            ["supply chain", "supplier disruption", "component shortages"],
        ),
        (
            ("demand slowdown", "수요 둔화"),
            ["demand slowdown", "lower demand", "customer demand"],
        ),
        (
            ("capex", "capital expenditure", "capital expenditures", "설비투자"),
            ["capital expenditures", "capex"],
        ),
    ]
    topics: list[str] = []
    for aliases, expansions in topic_aliases:
        if any(alias in question_lower for alias in aliases):
            topics.extend(expansions)
    if not topics and metric:
        topics.append(metric.replace("_", " "))
    if not topics:
        cleaned = _clean_question_topic(question)
        if cleaned:
            topics.append(cleaned)
    return _unique(topics)


def _extract_object_types(question_lower: str, *, key_risk_question: bool = False) -> list[str]:
    if key_risk_question:
        return ["RiskFactor", "Headwind", "ResearchClaim"]
    mapping = {
        "EvidenceQuote": ("quote", "quotes", "근거", "인용"),
        "ResearchClaim": ("claim", "claims", "주장"),
        "RiskFactor": ("risk", "리스크"),
        "GrowthDriver": ("driver", "growth", "성장"),
        "Headwind": ("headwind", "부담", "역풍"),
        "AssumptionCandidate": ("assumption", "가정"),
    }
    selected = [
        object_type
        for object_type, aliases in mapping.items()
        if any(alias in question_lower for alias in aliases)
    ]
    return selected or list(DEFAULT_QUERY_TYPES)


def _clean_question_topic(question: str) -> str:
    cleaned = question
    for pattern in (
        r"\b10-[KQ]\b",
        r"\b10[KQ]\b",
        r"\bFY\d{4}(?:Q[1-4?])?\b",
        r"\b(latest|recent|current|compare|comparison|versus)\b",
    ):
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?.,")
    return cleaned


def _dedupe_bundles(bundles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for bundle in bundles:
        bundle_id = bundle.get("id")
        if not bundle_id or bundle_id in seen:
            continue
        seen.add(bundle_id)
        output.append(bundle)
    return output


def _dedupe_documents(documents: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for document in documents:
        key = (document["ticker"], document["document_type"], document["period"])
        if key in seen:
            continue
        seen.add(key)
        output.append(document)
    return output


def _dedupe_quality_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for event in events:
        if event["id"] in seen:
            continue
        seen.add(event["id"])
        output.append(event)
    return output


def _period_sort_key(period: str) -> tuple[int, int, str]:
    match = re.match(r"FY(\d{4})(?:Q([1-4?]))?$", period, flags=re.IGNORECASE)
    if not match:
        return (0, 0, period)
    year = int(match.group(1))
    quarter_raw = match.group(2)
    quarter = int(quarter_raw) if quarter_raw and quarter_raw.isdigit() else 0
    return (year, quarter, period)


def _unique(values: Iterable[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
