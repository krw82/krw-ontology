"""Focused tests for the model-visible MCP v2 research contract."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from krw_ontology.mcp_server import tools as mcp_tools
from krw_ontology.mcp_server.contracts import (
    EvidenceRequirement,
    MAX_RESEARCH_STATE_MODEL_BYTES,
    MAX_RESEARCH_STATE_WIRE_BYTES,
    PlanUncertainty,
    QueryClause,
    ResearchState,
    SearchPlan,
    compile_research_state,
    research_state_model_bytes,
    research_state_wire_bytes,
    validate_search_plan,
)
from krw_ontology.mcp_server.server import (
    EXPECTED_TOOL_NAMES,
    health_payload,
    krw_ontology_query_context,
    mcp,
    ready_payload,
    runtime_fingerprint_payload,
)
from krw_ontology.mcp_server.tools import _execute_search_plan, query_context_tool
from tests.unit.test_mcp_server import _build_v3_runtime, _write_fixture


@pytest.fixture(autouse=True)
def _reset_runtime_caches() -> None:
    env_names = (
        "KRW_ONTOLOGY_ENV",
        "KRW_ONTOLOGY_RELEASE_ROOT",
        "KRW_ONTOLOGY_ROOT",
        "KRW_ONTOLOGY_MANIFEST_PATH",
        "KRW_ONTOLOGY_INDEX_LAYOUT",
        "KRW_ONTOLOGY_GLOBAL_SPINE_PATH",
        "KRW_ONTOLOGY_SHARD_MANIFEST_PATH",
        "KRW_MCP_STORE_MODE",
        "KRW_MCP_EXPECTED_CONTRACT_VERSION",
        "KRW_MCP_EXPECTED_TOOL_SCHEMA_SHA256",
        "KRW_MCP_EXPECTED_BUILD_ID",
        "KRW_MCP_EXPECTED_BACKEND_GIT_SHA",
        "KRW_MCP_EXPECTED_BUILD_FINGERPRINT_SHA256",
        "KRW_MCP_EXPECTED_RELEASE_MANIFEST_SHA256",
        "KRW_MCP_EXPECTED_SERVICE_FINGERPRINT_SHA256",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    mcp_tools.reset_mcp_runtime_caches()
    try:
        yield
    finally:
        mcp_tools.reset_mcp_runtime_caches()
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _plan(*, direct: bool = False) -> SearchPlan:
    return SearchPlan(
        question="VG의 매출 성장과 규제 리스크를 확인해줘.",
        intent="risk_thesis",
        tickers=["vg", "VG"],
        document_types=["10-k"],
        clauses=[
            QueryClause(
                clause_id="revenue_growth",
                retrieval_query="VG revenue growth driven by customer demand",
                required_concepts=["revenue growth", "customer demand"],
                required_predicates=["driven by"],
            ),
            QueryClause(
                clause_id="regulatory_risk",
                retrieval_query="VG regulatory risk from project approvals",
                required_concepts=["regulatory risk", "project approvals"],
                required_predicates=["from"],
                directness=(
                    EvidenceRequirement.DIRECT_REQUIRED if direct else EvidenceRequirement.ANY
                ),
                object_types=["BusinessFactor"] if direct else [],
            ),
        ],
        limit_results=12,
    )


def _metric_observation(
    *,
    ticker: str,
    period: str,
    value: object,
    metric: str = "revenue",
    clause_id: str = "revenue",
    unit: str = "USD",
    currency: str | None = None,
    period_type: str | None = "annual",
    lineage: str = "complete",
    suffix: str = "base",
) -> dict[str, object]:
    object_id = f"metric_observation:{ticker}:{period}:{metric}:{suffix}"
    obj: dict[str, object] = {
        "metric_name": metric,
        "canonical_metric": metric,
        "value": value,
        "unit": unit,
        "is_company_total": True,
    }
    if currency:
        obj["currency"] = currency
    if period_type:
        obj["period_type"] = period_type
    return {
        "id": object_id,
        "type": "MetricObservation",
        "ticker": ticker,
        "period": period,
        "document_type": "10-K",
        "text": f"{ticker} {period} {metric} {value}",
        "trace_status": "traceable_metric_lineage",
        "metric_lineage_status": lineage,
        "object": obj,
        "_plan_clause_matches": [
            {
                "clause_id": clause_id,
                "planned_match_mode": "strict",
                "planned_evidence_terms": [metric],
                "planned_metric_terms": [metric],
                "planned_metric_scope": "company_total",
            }
        ],
    }


def test_search_plan_is_strict_normalized_and_exposes_retrieval_queries() -> None:
    plan = _plan()

    assert plan.tickers == ["VG"]
    assert plan.document_types == ["10-K"]
    assert plan.retrieval_queries == (
        "VG revenue growth driven by customer demand",
        "VG regulatory risk from project approvals",
    )
    assert plan.execution_query == " ; ".join(plan.retrieval_queries)
    assert "retrieval_queries" not in plan.model_dump(mode="json")

    with pytest.raises(ValueError, match="invalid_plan"):
        validate_search_plan(
            {
                "question": "valid question",
                "intent": "evidence_lookup",
                "clauses": [
                    {"clause_id": "one", "retrieval_query": "first query"},
                ],
                "unexpected": True,
            }
        )

    with pytest.raises(ValidationError, match="invalid_plan"):
        SearchPlan(
            question="valid question",
            intent="evidence_lookup",
            clauses=[
                QueryClause(clause_id="same", retrieval_query="first query"),
                QueryClause(clause_id="SAME", retrieval_query="second query"),
            ],
        )

    with pytest.raises(ValidationError, match="metrics entry must be named"):
        QueryClause(
            clause_id="metric",
            retrieval_query="VG revenue",
            metrics=["capital_expenditures"],
        )

    with pytest.raises(ValidationError, match="numeric comparison_axes"):
        SearchPlan(
            question="Compare values",
            intent="comparison",
            clauses=[
                QueryClause(
                    clause_id="fact",
                    retrieval_query="filing fact",
                    required_concepts=["filing fact"],
                )
            ],
            comparison_axes=["growth_rate"],
        )


def test_search_plan_rejects_mixed_metric_and_qualitative_proposition() -> None:
    with pytest.raises(ValidationError, match="must be split"):
        QueryClause(
            clause_id="mixed",
            retrieval_query="revenue growth caused by cloud demand",
            required_concepts=["cloud demand"],
            metrics=["revenue"],
        )

    with pytest.raises(ValidationError, match="required_predicates"):
        QueryClause(
            clause_id="relation",
            retrieval_query="regulation revenue growth",
            required_concepts=["regulation", "revenue growth"],
        )


def test_relational_tokens_in_separate_sentences_never_become_direct() -> None:
    plan = SearchPlan(
        question="Did regulation cause revenue growth?",
        intent="causal_check",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="cause",
                retrieval_query="regulation caused revenue growth",
                required_concepts=["regulation", "revenue growth"],
                required_predicates=["caused"],
                directness=EvidenceRequirement.DIRECT_REQUIRED,
            )
        ],
    )
    row = {
        "id": "claim:VG:cooccurrence",
        "type": "ResearchClaim",
        "ticker": "VG",
        "text": ("Regulation was discussed. Revenue growth was caused by unrelated demand."),
        "semantic_relevance": "direct",
        "trace_status": "traceable",
        "answer_candidate": True,
        "support_quote_count": 1,
        "_plan_clause_matches": [
            {
                "clause_id": "cause",
                "planned_match_mode": "strict",
                "planned_evidence_terms": ["regulation", "revenue growth"],
                "planned_predicate_terms": ["caused"],
            }
        ],
    }

    state = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"VG": [row]}},
        release_id="release",
    )

    assert state.evidence_units[0].directness == "related"
    assert state.clause_coverage[0].status == "partial"
    assert state.answerability.strong_claim_allowed is False

    positive_row = dict(row)
    positive_row["id"] = "claim:VG:atomic-cause"
    positive_row["text"] = "Regulation caused revenue growth."
    positive_row.pop("semantic_relevance")
    positive = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"VG": [positive_row]}},
        release_id="release",
    )
    assert positive.evidence_units[0].directness == "related"
    assert positive.clause_coverage[0].status == "partial"


def test_metric_clause_requires_matching_metric_lineage_not_qualitative_mentions() -> None:
    metric_plan = SearchPlan(
        question="What is AAA revenue?",
        intent="metric_value",
        tickers=["AAA"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="AAA revenue",
                metrics=["revenue"],
                directness=EvidenceRequirement.ANY,
            )
        ],
    )
    claim = {
        "id": "claim:AAA:revenue",
        "type": "ResearchClaim",
        "ticker": "AAA",
        "text": "AAA revenue increased.",
        "semantic_relevance": "direct",
        "trace_status": "traceable",
        "answer_candidate": True,
        "support_quote_count": 1,
        "_plan_clause_matches": [
            {
                "clause_id": "revenue",
                "planned_match_mode": "strict",
                "planned_evidence_terms": ["revenue"],
                "planned_metric_terms": ["revenue"],
            }
        ],
    }
    state = compile_research_state(
        search_plan=metric_plan,
        raw_payload={"results_by_ticker": {"AAA": [claim]}},
        release_id="release",
    )
    assert state.evidence_units[0].directness == "related"
    assert state.clause_coverage[0].status == "partial"
    assert state.answerability.strong_claim_allowed is False

    qualitative_plan = SearchPlan(
        question="Was revenue driven by demand?",
        intent="causal_check",
        tickers=["AAA"],
        clauses=[
            QueryClause(
                clause_id="driver",
                retrieval_query="revenue driven by demand",
                required_concepts=["revenue", "demand"],
                required_predicates=["driven by"],
            )
        ],
    )
    metric_row = _metric_observation(
        ticker="AAA",
        period="FY2025",
        value=100,
        clause_id="driver",
    )
    metric_row["text"] = "AAA revenue demand 100"
    metric_row["_plan_clause_matches"] = [
        {
            "clause_id": "driver",
            "planned_match_mode": "strict",
            "planned_evidence_terms": ["revenue", "demand"],
            "planned_predicate_terms": ["driven by"],
            "planned_metric_terms": [],
        }
    ]
    qualitative = compile_research_state(
        search_plan=qualitative_plan,
        raw_payload={"results_by_ticker": {"AAA": [metric_row]}},
        release_id="release",
    )
    assert qualitative.evidence_units[0].directness == "related"
    assert qualitative.clause_coverage[0].status == "partial"
    assert qualitative.answerability.strong_claim_allowed is False


def test_query_context_returns_only_compact_v2_research_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logging.disable(logging.CRITICAL)
    try:
        _write_fixture(tmp_path)
        _build_v3_runtime(tmp_path)
    finally:
        logging.disable(logging.NOTSET)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    state = query_context_tool(search_plan=_plan())
    payload = state.model_dump(mode="json")

    assert isinstance(state, ResearchState)
    assert payload["contract_version"] == "research-state/v2"
    assert payload["release_id"] == "test-v3-runtime"
    assert payload["plan"]["intent"] == "risk_thesis"
    assert payload["plan"]["question"] == _plan().question
    assert "question" not in payload
    assert payload["resolved_scope"]["resolved_tickers"] == ["VG"]
    assert payload["source_anchors"]
    assert {anchor["role"] for anchor in payload["source_anchors"]}.intersection(
        {"current_driver", "annual_baseline", "latest_available"}
    )
    assert payload["evidence_units"]
    assert {"revenue_growth", "regulatory_risk"} == {
        coverage["clause_id"] for coverage in payload["clause_coverage"]
    }
    assert all(unit["source"]["object_ids"] for unit in payload["evidence_units"])
    assert len(state.model_dump_json()) < 16_000
    assert not {
        "research_pack",
        "kernel",
        "routing",
        "results_by_ticker",
        "ticker_candidates",
        "search_diagnostics",
    }.intersection(payload)


def test_plan_requirement_does_not_promote_related_evidence_to_direct() -> None:
    plan = SearchPlan(
        question="Does the filing directly support the requested exposure?",
        intent="direct_evidence_check",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="direct_exposure",
                retrieval_query="VG direct exposure",
                required_concepts=["direct exposure"],
                directness=EvidenceRequirement.DIRECT_REQUIRED,
            )
        ],
    )
    state = compile_research_state(
        search_plan=plan,
        release_id="test",
        raw_payload={
            "results_by_ticker": {
                "VG": [
                    {
                        "id": "factor:VG:broad-context",
                        "type": "BusinessFactor",
                        "ticker": "VG",
                        "text": "Broad sector context with filing lineage.",
                        "trace_status": "traceable",
                        "answer_candidate": True,
                        "support_claim_count": 1,
                        "planned_match_mode": "strict",
                        "planned_evidence_terms": ["direct", "exposure"],
                        "_plan_clause_ids": ["direct_exposure"],
                    }
                ]
            }
        },
    )
    risk = state.clause_coverage[0]

    assert risk.status == "partial"
    assert state.answerability.strong_claim_allowed is False
    assert all(
        unit.directness != "direct"
        for unit in state.evidence_units
        if "direct_exposure" in unit.supports_clause_ids
    )
    assert any(part.code == "direct_evidence_missing" for part in state.missing_parts)


def test_strict_visible_match_with_traceable_support_is_direct() -> None:
    plan = SearchPlan(
        question="Does the filing directly support the requested exposure?",
        intent="direct_evidence_check",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="direct_exposure",
                retrieval_query="VG direct exposure",
                required_concepts=["direct exposure"],
                directness=EvidenceRequirement.DIRECT_REQUIRED,
            )
        ],
    )
    state = compile_research_state(
        search_plan=plan,
        release_id="test",
        raw_payload={
            "results_by_ticker": {
                "VG": [
                    {
                        "id": "claim:VG:direct-exposure",
                        "type": "ResearchClaim",
                        "ticker": "VG",
                        "text": "The filing describes direct exposure to the factor.",
                        "semantic_relevance": "direct",
                        "trace_status": "traceable",
                        "answer_candidate": True,
                        "support_claim_count": 1,
                        "planned_match_mode": "strict",
                        "planned_evidence_terms": ["direct", "exposure"],
                        "_plan_clause_ids": ["direct_exposure"],
                    }
                ]
            }
        },
    )

    assert state.clause_coverage[0].status == "covered"
    assert state.evidence_units[0].directness == "direct"
    assert state.answerability.strong_claim_allowed is True


def test_clause_directness_is_not_shared_across_same_object_matches() -> None:
    plan = SearchPlan(
        question="Check growth and direct regulatory support",
        intent="multi_clause_check",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="growth",
                retrieval_query="VG revenue growth",
                required_concepts=["revenue growth"],
            ),
            QueryClause(
                clause_id="regulation",
                retrieval_query="VG regulatory approval",
                required_concepts=["regulatory approval"],
                directness=EvidenceRequirement.DIRECT_REQUIRED,
            ),
        ],
    )
    state = compile_research_state(
        search_plan=plan,
        release_id="release",
        raw_payload={
            "results_by_ticker": {
                "VG": [
                    {
                        "id": "claim:VG:FY2025:10K:growth",
                        "type": "ResearchClaim",
                        "ticker": "VG",
                        "text": "Revenue growth accelerated with customer demand.",
                        "semantic_relevance": "direct",
                        "trace_status": "traceable",
                        "answer_candidate": True,
                        "support_quote_count": 1,
                        "_plan_clause_matches": [
                            {
                                "clause_id": "growth",
                                "planned_match_mode": "strict",
                                "planned_evidence_terms": ["revenue", "growth"],
                            },
                            {
                                "clause_id": "regulation",
                                "planned_match_mode": "strict",
                                "planned_evidence_terms": ["regulatory", "approval"],
                            },
                        ],
                    }
                ]
            }
        },
    )

    by_clause = {row.clause_id: row for row in state.clause_coverage}
    assert by_clause["growth"].best_directness == "direct"
    assert by_clause["regulation"].best_directness == "related"
    assert by_clause["regulation"].status == "partial"
    assert state.answerability.strong_claim_allowed is False


def test_traceable_factor_lexical_cooccurrence_is_related_not_direct() -> None:
    plan = SearchPlan(
        question="Did regulation cause revenue growth?",
        intent="causal_check",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="cause",
                retrieval_query="VG regulation caused revenue",
                required_concepts=["regulation", "revenue"],
                required_predicates=["caused"],
                directness=EvidenceRequirement.DIRECT_REQUIRED,
            )
        ],
    )
    state = compile_research_state(
        search_plan=plan,
        release_id="release",
        raw_payload={
            "results_by_ticker": {
                "VG": [
                    {
                        "id": "factor:VG:cooccurrence",
                        "type": "BusinessFactor",
                        "ticker": "VG",
                        "text": ("Regulation was discussed. Revenue grew for unrelated reasons."),
                        "trace_status": "traceable",
                        "answer_candidate": True,
                        "support_claim_count": 1,
                        "_plan_clause_matches": [
                            {
                                "clause_id": "cause",
                                "planned_match_mode": "strict",
                                "planned_evidence_terms": ["regulation", "revenue"],
                            }
                        ],
                    }
                ]
            }
        },
    )

    assert state.evidence_units[0].directness == "related"
    assert state.clause_coverage[0].status == "partial"
    assert state.answerability.strong_claim_allowed is False


def test_weak_related_evidence_can_answer_but_never_enable_strong_claim() -> None:
    plan = SearchPlan(
        question="Summarize the related context",
        intent="context_lookup",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="context",
                retrieval_query="VG related context",
                required_concepts=["related context"],
                directness=EvidenceRequirement.ANY,
            )
        ],
    )
    state = compile_research_state(
        search_plan=plan,
        release_id="release",
        raw_payload={
            "results_by_ticker": {
                "VG": [
                    {
                        "id": "factor:VG:FY2025:10K:weak",
                        "type": "BusinessFactor",
                        "ticker": "VG",
                        "text": "Broad industry background.",
                        "trace_status": "traceable",
                        "evidence_strength": "weak",
                        "_plan_clause_matches": [
                            {
                                "clause_id": "context",
                                "planned_match_mode": "strict",
                                "planned_evidence_terms": ["related", "context"],
                            }
                        ],
                    }
                ]
            }
        },
    )

    assert state.answerability.status == "answerable"
    assert state.clause_coverage[0].best_evidence_grade == "weak"
    assert state.answerability.strong_claim_allowed is False
    assert "strong_claim_evidence_not_ready" in state.answerability.reason_codes


def test_explicit_metric_series_produces_lineage_bound_calculations() -> None:
    plan = SearchPlan(
        question="How did VG revenue change?",
        intent="metric_series",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="VG revenue growth rate",
                required_concepts=["revenue"],
                metrics=["revenue"],
                calculation_window="period_over_period",
            )
        ],
        comparison_axes=["absolute_change", "growth_rate"],
    )

    def observation(period: str, value: float) -> dict[str, object]:
        object_id = f"metric_observation:VG:{period}:10K:revenue"
        return {
            "id": object_id,
            "type": "MetricObservation",
            "ticker": "VG",
            "period": period,
            "document_type": "10-K",
            "text": f"{period} revenue: {value}",
            "trace_status": "traceable_metric_lineage",
            "metric_lineage_status": "complete",
            "object": {
                "id": object_id,
                "metric_name": "revenue",
                "canonical_metric": "revenue",
                "value": value,
                "unit": "USD",
                "is_company_total": True,
            },
            "_plan_clause_matches": [
                {
                    "clause_id": "revenue",
                    "planned_match_mode": "strict",
                    "planned_evidence_terms": ["revenue"],
                    "planned_metric_terms": ["revenue"],
                    "planned_metric_scope": "company_total",
                }
            ],
        }

    state = compile_research_state(
        search_plan=plan,
        raw_payload={
            "results_by_ticker": {
                "VG": [observation("FY2024", 100.0), observation("FY2025", 125.0)]
            }
        },
        release_id="release",
    )

    values = {value.kind: value for value in state.computed_values}
    assert values["absolute_change"].value == 25.0
    assert values["growth_rate"].value == 0.25
    assert len(values["growth_rate"].source_object_ids) == 2
    assert all(unit.directness == "metric_lineage" for unit in state.evidence_units)
    assert all(unit.evidence_grade == "strong" for unit in state.evidence_units)


def test_metric_calculation_rejects_mismatched_dimension_series() -> None:
    plan = SearchPlan(
        question="How did regional revenue change?",
        intent="metric_series",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="VG regional revenue growth rate",
                metrics=["revenue"],
                metric_scope="any",
                calculation_window="period_over_period",
            )
        ],
        comparison_axes=["growth_rate"],
    )
    rows = []
    for period, value, region in (
        ("FY2024", 100.0, "Europe"),
        ("FY2025", 125.0, "Asia"),
    ):
        object_id = f"metric_observation:VG:{period}:10K:revenue:{region}"
        rows.append(
            {
                "id": object_id,
                "type": "MetricObservation",
                "ticker": "VG",
                "period": period,
                "document_type": "10-K",
                "text": f"{period} {region} revenue: {value}",
                "trace_status": "traceable_metric_lineage",
                "metric_lineage_status": "complete",
                "object": {
                    "metric_name": "revenue",
                    "value": value,
                    "unit": "USD",
                    "dimensions": {"region": region},
                },
                "_plan_clause_matches": [
                    {
                        "clause_id": "revenue",
                        "planned_match_mode": "strict",
                        "planned_evidence_terms": ["revenue"],
                        "planned_metric_terms": ["revenue"],
                        "planned_metric_scope": "company_total",
                    }
                ],
            }
        )

    state = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"VG": rows}},
        release_id="release",
    )

    assert state.computed_values == []
    assert state.answerability.status == "partial"
    assert "required_metric_calculation_missing" in state.answerability.reason_codes
    assert any(part.code == "metric_calculation_unavailable" for part in state.missing_parts)


def test_exact_metric_lineage_requires_planned_dimension_match() -> None:
    plan = SearchPlan(
        question="What is VG Europe revenue?",
        intent="metric_lookup",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="europe_revenue",
                retrieval_query="VG Europe revenue",
                metrics=["revenue"],
                metric_dimensions=["Europe"],
                metric_scope="dimensioned",
            )
        ],
    )

    def regional_row(region: str, value: float) -> dict[str, object]:
        object_id = f"metric_observation:VG:FY2025:10K:revenue:{region}"
        return {
            "id": object_id,
            "type": "MetricObservation",
            "ticker": "VG",
            "period": "FY2025",
            "document_type": "10-K",
            "text": f"FY2025 {region} revenue: {value}",
            "trace_status": "traceable_metric_lineage",
            "metric_lineage_status": "complete",
            "object": {
                "metric_name": "revenue",
                "value": value,
                "unit": "USD",
                "dimensions": {"region": region},
            },
            "_plan_clause_matches": [
                {
                    "clause_id": "europe_revenue",
                    "planned_match_mode": "strict",
                    "planned_evidence_terms": ["revenue", "Europe"],
                    "planned_metric_terms": ["revenue", "Europe"],
                    "planned_metric_scope": "dimensioned",
                }
            ],
        }

    state = compile_research_state(
        search_plan=plan,
        raw_payload={
            "results_by_ticker": {
                "VG": [regional_row("Asia", 200.0), regional_row("Europe", 125.0)]
            }
        },
        release_id="release",
    )

    units_by_region = {unit.dimensions["region"]: unit for unit in state.evidence_units}
    assert units_by_region["Europe"].directness == "metric_lineage"
    assert units_by_region["Asia"].directness == "related"
    assert state.clause_coverage[0].status == "covered"
    assert state.clause_coverage[0].evidence_ids == [units_by_region["Europe"].evidence_id]


def test_metric_calculations_preserve_series_identity_and_dedupe_same_value() -> None:
    plan = SearchPlan(
        question="Compare regional revenue growth",
        intent="metric_series",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="VG regional revenue growth",
                metrics=["revenue"],
                metric_scope="any",
                calculation_window="period_over_period",
            )
        ],
        comparison_axes=["growth_rate"],
    )

    def row(index: str, period: str, value: object, region: str, unit: str | None):
        object_id = f"metric_observation:VG:{period}:{region}:{index}"
        return {
            "id": object_id,
            "type": "MetricObservation",
            "ticker": "VG",
            "period": period,
            "document_type": "10-K",
            "text": f"{period} {region} revenue {value}",
            "trace_status": "traceable_metric_lineage",
            "metric_lineage_status": "complete",
            "object": {
                "metric_name": "revenue",
                "value": value,
                "unit": unit,
                "dimensions": {"region": region},
            },
            "_plan_clause_matches": [
                {
                    "clause_id": "revenue",
                    "planned_match_mode": "strict",
                    "planned_evidence_terms": ["revenue"],
                    "planned_metric_terms": ["revenue"],
                    "planned_metric_scope": "any",
                }
            ],
        }

    rows = [
        row("a", "FY2024", 100.0, "Europe", "USD"),
        row("b", "FY2024", 100.0, "Europe", "USD"),
        row("c", "FY2025", 125.0, "Europe", "USD"),
        row("d", "FY2024", 200.0, "Asia", None),
        row("e", "FY2025", 220.0, "Asia", None),
    ]
    state = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"VG": rows}},
        release_id="release",
    )

    growth = [value for value in state.computed_values if value.kind == "growth_rate"]
    assert len(growth) == 1
    assert len({value.calculation_id for value in growth}) == 1
    by_region = {value.dimensions["region"]: value for value in growth}
    assert by_region["Europe"].value == 0.25
    assert len(by_region["Europe"].source_object_ids) == 3
    assert "Asia" not in by_region


def test_metric_calculation_rejects_conflicting_duplicates_and_non_finite_values() -> None:
    plan = SearchPlan(
        question="Calculate company revenue growth",
        intent="metric_series",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="VG revenue growth",
                metrics=["revenue"],
                calculation_window="period_over_period",
            )
        ],
        comparison_axes=["growth_rate"],
    )
    rows = []
    for index, period, value in (
        ("a", "FY2024", 100.0),
        ("b", "FY2024", 101.0),
        ("c", "FY2025", 125.0),
        ("d", "FY2026", float("nan")),
    ):
        object_id = f"metric_observation:VG:{period}:{index}"
        rows.append(
            {
                "id": object_id,
                "type": "MetricObservation",
                "ticker": "VG",
                "period": period,
                "document_type": "10-K",
                "text": f"{period} revenue {value}",
                "trace_status": "traceable_metric_lineage",
                "metric_lineage_status": "complete",
                "object": {
                    "metric_name": "revenue",
                    "value": value,
                    "unit": "USD",
                    "is_company_total": True,
                },
                "_plan_clause_matches": [
                    {
                        "clause_id": "revenue",
                        "planned_match_mode": "strict",
                        "planned_evidence_terms": ["revenue"],
                        "planned_metric_terms": ["revenue"],
                        "planned_metric_scope": "company_total",
                    }
                ],
            }
        )

    state = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"VG": rows}},
        release_id="release",
    )

    assert state.computed_values == []
    assert state.answerability.status == "partial"
    assert any(part.code == "metric_calculation_unavailable" for part in state.missing_parts)


def test_metric_points_use_observation_period_without_losing_filing_lineage() -> None:
    plan = SearchPlan(
        question="Calculate annual revenue growth from one filing",
        intent="metric_series",
        tickers=["MSFT"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="MSFT annual revenue growth",
                metrics=["revenue"],
                calculation_window="period_over_period",
            )
        ],
        comparison_axes=["growth_rate"],
        limit_results=2,
    )
    rows = [
        _metric_observation(ticker="MSFT", period="FY2024", value=100),
        _metric_observation(ticker="MSFT", period="FY2025", value=125),
    ]
    for row in rows:
        observation_period = str(row["period"])
        row["filing_period"] = "CY2025"
        obj = row["object"]
        assert isinstance(obj, dict)
        obj["observation_period"] = observation_period
        obj["filing_period"] = "CY2025"

    state = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"MSFT": rows}},
        release_id="release",
    )

    assert {unit.period for unit in state.evidence_units} == {"CY2025"}
    assert {point.period for unit in state.evidence_units for point in unit.metric_points} == {
        "FY2024",
        "FY2025",
    }
    assert [value.value for value in state.computed_values] == [0.25]

    first_object = rows[0]["object"]
    assert isinstance(first_object, dict)
    first_object["metric_conflict_value_count"] = 2
    conflict = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"MSFT": rows}},
        release_id="release",
    )
    assert conflict.computed_values == []
    assert conflict.calculation_coverage[0].status == "missing"


def test_value_comparison_requires_metric_evidence_for_every_resolved_ticker() -> None:
    plan = SearchPlan(
        question="Compare VG and XOM revenue values",
        intent="metric_comparison",
        tickers=["VG", "XOM"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="VG XOM revenue value",
                metrics=["revenue"],
            )
        ],
        comparison_axes=["value"],
        limit_results=2,
    )

    def row(ticker: str, value: float) -> dict[str, object]:
        object_id = f"metric_observation:{ticker}:FY2025:revenue"
        return {
            "id": object_id,
            "type": "MetricObservation",
            "ticker": ticker,
            "period": "FY2025",
            "document_type": "10-K",
            "text": f"{ticker} FY2025 revenue {value}",
            "trace_status": "traceable_metric_lineage",
            "metric_lineage_status": "complete",
            "object": {
                "metric_name": "revenue",
                "value": value,
                "unit": "USD",
                "is_company_total": True,
            },
            "_plan_clause_matches": [
                {
                    "clause_id": "revenue",
                    "planned_match_mode": "strict",
                    "planned_evidence_terms": ["revenue"],
                    "planned_metric_terms": ["revenue"],
                    "planned_metric_scope": "company_total",
                }
            ],
        }

    complete = compile_research_state(
        search_plan=plan,
        raw_payload={
            "results_by_ticker": {
                "VG": [row("VG", 125.0)],
                "XOM": [row("XOM", 200.0)],
            },
            "routing": {"resolved_tickers": ["VG", "XOM"]},
        },
        release_id="release",
    )
    partial = compile_research_state(
        search_plan=plan,
        raw_payload={
            "results_by_ticker": {"VG": [row("VG", 125.0)]},
            "routing": {"resolved_tickers": ["VG", "XOM"]},
        },
        release_id="release",
    )

    assert {unit.ticker for unit in complete.evidence_units} == {"VG", "XOM"}
    assert complete.clause_coverage[0].status == "covered"
    assert complete.clause_coverage[0].covered_tickers == ["VG", "XOM"]
    assert partial.clause_coverage[0].status == "partial"
    assert partial.clause_coverage[0].missing_tickers == ["XOM"]
    assert partial.answerability.strong_claim_allowed is False
    assert any(
        part.code == "comparison_ticker_evidence_missing" and part.ticker == "XOM"
        for part in partial.missing_parts
    )


def test_numeric_answerability_is_complete_per_required_clause_metric() -> None:
    plan = SearchPlan(
        question="Compare VG revenue and capex growth",
        intent="multi_metric_growth",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="revenue_growth",
                retrieval_query="VG revenue growth",
                metrics=["revenue"],
                calculation_window="period_over_period",
            ),
            QueryClause(
                clause_id="capex_growth",
                retrieval_query="VG capex growth",
                metrics=["capex"],
                calculation_window="period_over_period",
            ),
        ],
        comparison_axes=["growth_rate"],
        limit_results=6,
    )
    assert plan.clauses[1].metrics == ["capital_expenditures"]

    def row(metric: str, clause_id: str, period: str, value: float):
        object_id = f"metric_observation:VG:{period}:{metric}"
        return {
            "id": object_id,
            "type": "MetricObservation",
            "ticker": "VG",
            "period": period,
            "document_type": "10-K",
            "text": f"VG {period} {metric} {value}",
            "trace_status": "traceable_metric_lineage",
            "metric_lineage_status": "complete",
            "object": {
                "metric_name": metric,
                "value": value,
                "unit": "USD",
                "is_company_total": True,
            },
            "_plan_clause_matches": [
                {
                    "clause_id": clause_id,
                    "planned_match_mode": "strict",
                    "planned_evidence_terms": [metric],
                    "planned_metric_terms": [metric],
                    "planned_metric_scope": "company_total",
                }
            ],
        }

    rows = [
        row("revenue", "revenue_growth", "FY2024", 100.0),
        row("revenue", "revenue_growth", "FY2025", 125.0),
        row("capex", "capex_growth", "FY2025", 50.0),
    ]
    state = compile_research_state(
        search_plan=plan,
        raw_payload={
            "results_by_ticker": {"VG": rows},
            "routing": {"resolved_tickers": ["VG"]},
        },
        release_id="release",
    )

    coverage = {
        (item.clause_id, item.metric, item.axis): item for item in state.calculation_coverage
    }
    assert coverage[("revenue_growth", "revenue", "growth_rate")].status == ("covered")
    assert coverage[("capex_growth", "capital_expenditures", "growth_rate")].status == "missing"
    assert state.answerability.status == "partial"
    assert state.answerability.strong_claim_allowed is False
    assert "required_metric_calculation_missing" in state.answerability.reason_codes
    assert any(
        part.code == "metric_calculation_unavailable" and part.clause_id == "capex_growth"
        for part in state.missing_parts
    )


def test_metric_arithmetic_requires_compatible_period_convention_and_lineage() -> None:
    plan = SearchPlan(
        question="Calculate revenue growth",
        intent="metric_growth",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="VG revenue growth",
                metrics=["revenue"],
                calculation_window="period_over_period",
                directness=EvidenceRequirement.ANY,
            )
        ],
        comparison_axes=["growth_rate"],
        limit_results=4,
    )
    rows = [
        _metric_observation(ticker="VG", period="FY2024", value=100),
        _metric_observation(ticker="VG", period="CY2024", value=150, suffix="cy"),
        _metric_observation(ticker="VG", period="FY2025", value=125),
    ]
    state = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"VG": rows}},
        release_id="release",
    )
    growth = [value for value in state.computed_values if value.kind == "growth_rate"]
    assert len(growth) == 1
    assert (growth[0].from_period, growth[0].period) == ("FY2024", "FY2025")
    assert growth[0].unit == "ratio"
    assert growth[0].currency is None

    incomplete = [
        _metric_observation(
            ticker="VG",
            period=period,
            value=value,
            lineage="incomplete",
        )
        for period, value in (("FY2024", 100), ("FY2025", 125))
    ]
    unsafe = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"VG": incomplete}},
        release_id="release",
    )
    assert unsafe.computed_values == []
    assert unsafe.calculation_coverage[0].status == "missing"
    assert unsafe.answerability.status != "answerable"

    truncated = compile_research_state(
        search_plan=plan,
        raw_payload={
            "results_by_ticker": {
                "VG": [
                    _metric_observation(ticker="VG", period="FY2024", value=100),
                    _metric_observation(ticker="VG", period="FY2025", value=125),
                ]
            },
            "truncation_possible": True,
            "omitted_evidence_count": 1,
        },
        release_id="release",
    )
    assert truncated.computed_values
    assert truncated.calculation_coverage[0].status == "partial"
    assert truncated.answerability.strong_claim_allowed is False

    long_rows = [
        _metric_observation(ticker="VG", period="FY2024", value=100),
        _metric_observation(ticker="VG", period="FY2025", value=125),
    ]
    long_rows[0]["object"]["period_start"] = "2023-01-01"  # type: ignore[index]
    long_rows[0]["object"]["period_end"] = "2024-12-31"  # type: ignore[index]
    long_rows[0]["object"]["period_type"] = "duration"  # type: ignore[index]
    long_duration = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"VG": long_rows}},
        release_id="release",
    )
    assert long_duration.computed_values == []
    assert long_duration.calculation_coverage[0].status == "missing"


def test_value_alignment_rejects_mixed_period_currency_and_unknown_derived_duration() -> None:
    comparison = SearchPlan(
        question="Compare AAA and BBB revenue",
        intent="metric_comparison",
        tickers=["AAA", "BBB"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="AAA BBB revenue value",
                metrics=["revenue"],
            )
        ],
        comparison_axes=["value"],
        limit_results=4,
    )
    mismatched = compile_research_state(
        search_plan=comparison,
        raw_payload={
            "results_by_ticker": {
                "AAA": [
                    _metric_observation(
                        ticker="AAA",
                        period="FY2024",
                        value=100,
                        currency="USD",
                    )
                ],
                "BBB": [
                    _metric_observation(
                        ticker="BBB",
                        period="FY2025",
                        value=120,
                        unit="EUR",
                        currency="EUR",
                    )
                ],
            },
            "routing": {"resolved_tickers": ["AAA", "BBB"]},
        },
        release_id="release",
    )
    assert mismatched.calculation_coverage[0].status == "partial"
    assert mismatched.answerability.strong_claim_allowed is False

    unknown_units = compile_research_state(
        search_plan=comparison,
        raw_payload={
            "results_by_ticker": {
                ticker: [
                    _metric_observation(
                        ticker=ticker,
                        period="FY2025",
                        value=value,
                        unit="",
                    )
                ]
                for ticker, value in (("AAA", 100), ("BBB", 10_000))
            },
            "routing": {"resolved_tickers": ["AAA", "BBB"]},
        },
        release_id="release",
    )
    assert unknown_units.computed_values == []
    assert unknown_units.calculation_coverage[0].status == "missing"
    assert unknown_units.answerability.strong_claim_allowed is False

    single = SearchPlan(
        question="What is AAA derived margin?",
        intent="metric_value",
        tickers=["AAA"],
        clauses=[
            QueryClause(
                clause_id="margin",
                retrieval_query="AAA operating_margin value",
                metrics=["operating_margin"],
            )
        ],
        comparison_axes=["value"],
    )
    derived = compile_research_state(
        search_plan=single,
        raw_payload={
            "results_by_ticker": {
                "AAA": [
                    _metric_observation(
                        ticker="AAA",
                        period="FY2025",
                        value=0.2,
                        metric="operating_margin",
                        clause_id="margin",
                        unit="ratio",
                        period_type="derived",
                    )
                ]
            }
        },
        release_id="release",
    )
    assert [value.kind for value in derived.computed_values] == ["value"]
    assert derived.calculation_coverage[0].status == "covered"


def test_temporal_windows_reject_ytd_sequential_and_sign_crossing_growth() -> None:
    ytd_plan = SearchPlan(
        question="Calculate sequential YTD revenue growth",
        intent="metric_growth",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="VG revenue growth",
                metrics=["revenue"],
                calculation_window="period_over_period",
            )
        ],
        comparison_axes=["growth_rate"],
    )
    ytd_rows = [
        _metric_observation(
            ticker="VG",
            period="FY2025Q2",
            value=100,
            period_type="year_to_date",
        ),
        _metric_observation(
            ticker="VG",
            period="FY2025Q3",
            value=160,
            period_type="year_to_date",
        ),
    ]
    ytd = compile_research_state(
        search_plan=ytd_plan,
        raw_payload={"results_by_ticker": {"VG": ytd_rows}},
        release_id="release",
    )
    assert ytd.computed_values == []
    assert ytd.calculation_coverage[0].status == "missing"

    sign_plan = ytd_plan.model_copy(update={"comparison_axes": ["absolute_change", "growth_rate"]})
    sign_rows = [
        _metric_observation(ticker="VG", period="FY2024", value=-100),
        _metric_observation(ticker="VG", period="FY2025", value=100),
    ]
    sign = compile_research_state(
        search_plan=sign_plan,
        raw_payload={"results_by_ticker": {"VG": sign_rows}},
        release_id="release",
    )
    assert [value.kind for value in sign.computed_values] == ["absolute_change"]
    coverage = {item.axis: item.status for item in sign.calculation_coverage}
    assert coverage == {"absolute_change": "covered", "growth_rate": "missing"}


def test_calculation_coverage_unions_requested_windows_and_sees_prelimit_conflicts() -> None:
    plan = SearchPlan(
        question="Calculate three annual revenue periods",
        intent="metric_growth",
        tickers=["VG"],
        periods=["FY2023", "FY2024", "FY2025"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="VG revenue growth",
                metrics=["revenue"],
                calculation_window="period_over_period",
            )
        ],
        comparison_axes=["growth_rate"],
        limit_results=6,
    )
    rows = [
        _metric_observation(ticker="VG", period=period, value=value)
        for period, value in (("FY2023", 80), ("FY2024", 100), ("FY2025", 125))
    ]
    state = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"VG": rows}},
        release_id="release",
    )
    assert len(state.computed_values) == 2
    assert state.calculation_coverage[0].status == "covered"

    conflict_plan = plan.model_copy(update={"periods": [], "limit_results": 2})
    conflicts = [
        _metric_observation(
            ticker="VG",
            period="FY2024",
            value=100,
            suffix="a",
        ),
        _metric_observation(
            ticker="VG",
            period="FY2024",
            value=101,
            suffix="b",
        ),
        _metric_observation(ticker="VG", period="FY2025", value=125),
    ]
    conflict = compile_research_state(
        search_plan=conflict_plan,
        raw_payload={"results_by_ticker": {"VG": conflicts}},
        release_id="release",
    )
    assert conflict.computed_values == []
    assert conflict.calculation_coverage[0].status == "missing"


def test_multi_ticker_clause_coverage_is_not_satisfied_crosswise() -> None:
    plan = SearchPlan(
        question="Check both companies for revenue and capex",
        intent="multi_company_check",
        tickers=["AAA", "BBB"],
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="revenue evidence",
                required_concepts=["revenue evidence"],
            ),
            QueryClause(
                clause_id="capex",
                retrieval_query="capex evidence",
                required_concepts=["capex evidence"],
            ),
        ],
    )

    def claim(ticker: str, clause_id: str) -> dict[str, object]:
        phrase = f"{clause_id} evidence"
        return {
            "id": f"claim:{ticker}:{clause_id}",
            "type": "ResearchClaim",
            "ticker": ticker,
            "text": phrase,
            "semantic_relevance": "direct",
            "trace_status": "traceable",
            "answer_candidate": True,
            "support_quote_count": 1,
            "_plan_clause_matches": [
                {
                    "clause_id": clause_id,
                    "planned_match_mode": "strict",
                    "planned_evidence_terms": [phrase],
                }
            ],
        }

    state = compile_research_state(
        search_plan=plan,
        raw_payload={
            "results_by_ticker": {
                "AAA": [claim("AAA", "revenue")],
                "BBB": [claim("BBB", "capex")],
            },
            "routing": {"resolved_tickers": ["AAA", "BBB"]},
        },
        release_id="release",
    )
    assert all(item.status == "partial" for item in state.clause_coverage)
    assert state.answerability.status == "partial"


def test_value_difference_requires_every_requested_pair_before_strong_answer() -> None:
    tickers = [f"T{index:02d}" for index in range(11)]
    plan = SearchPlan(
        question="Compare revenue for eleven companies",
        intent="large_comparison",
        tickers=tickers,
        clauses=[
            QueryClause(
                clause_id="revenue",
                retrieval_query="company revenue difference",
                metrics=["revenue"],
            )
        ],
        comparison_axes=["value_difference"],
        limit_results=20,
    )
    rows = {
        ticker: [
            _metric_observation(
                ticker=ticker,
                period="FY2025",
                value=100 + index,
            )
        ]
        for index, ticker in enumerate(tickers)
    }
    state = compile_research_state(
        search_plan=plan,
        raw_payload={
            "results_by_ticker": rows,
            "routing": {"resolved_tickers": tickers},
        },
        release_id="release",
    )
    assert len(state.computed_values) == 40
    assert state.calculation_coverage[0].status == "partial"
    assert state.answerability.strong_claim_allowed is False


def test_executor_uses_only_clause_queries_and_plan_uncertainty() -> None:
    calls: list[dict[str, object]] = []

    class FakeStore:
        def query_planned_compact_with_diagnostics(self, **kwargs):
            calls.append(kwargs)
            return [], {
                "execution_mode": "planned_fts",
                "keyword_expansion_used": False,
                "intent_reclassification_used": False,
                "warnings": [],
            }

    low = _plan()
    high = low.model_copy(update={"uncertainty": PlanUncertainty.HIGH})

    _execute_search_plan(store=FakeStore(), search_plan=low)
    _execute_search_plan(store=FakeStore(), search_plan=high)

    assert [call["retrieval_query"] for call in calls[:2]] == list(low.retrieval_queries)
    assert all(call["allow_relaxed"] is False for call in calls[:2])
    assert calls[0]["predicate_terms"] == ["driven by"]
    assert calls[1]["predicate_terms"] == ["from"]
    assert all(call["calculation_window"] is None for call in calls[:2])
    assert all(call["comparison_axes"] == [] for call in calls[:2])
    assert [call["retrieval_query"] for call in calls[2:]] == list(high.retrieval_queries)
    assert all(call["allow_relaxed"] is True for call in calls[2:])
    assert all(call["retrieval_query"] != low.question for call in calls)


def test_executor_batches_all_clauses_with_one_call_per_router() -> None:
    batch_calls: list[dict[str, object]] = []

    class FakeBatchStore:
        def route_planned_tickers(self, **_kwargs):
            return ["VG"], {
                "mode": "explicit_plan_scope",
                "resolved_tickers": ["VG"],
                "fallback_used": False,
            }

        def query_planned_batch_with_diagnostics(self, **kwargs):
            batch_calls.append(kwargs)
            return {
                clause["clause_id"]: {
                    "rows": [
                        {
                            "id": f"claim:VG:{clause['clause_id']}",
                            "type": "ResearchClaim",
                            "ticker": "VG",
                            "text": clause["retrieval_query"],
                            "trace_status": "traceable",
                            "answer_candidate": True,
                            "support_quote_count": 1,
                            "planned_match_mode": "strict",
                            "planned_evidence_terms": clause["retrieval_terms"],
                        }
                    ],
                    "diagnostics": {"execution_mode": "planned_shard_batch"},
                }
                for clause in kwargs["clauses"]
            }, {
                "execution_mode": "planned_shard_batch",
                "fallback_used": False,
            }

        def query_planned_compact_with_diagnostics(self, **_kwargs):
            raise AssertionError("per-clause router path must not run when batch is available")

    raw = _execute_search_plan(store=FakeBatchStore(), search_plan=_plan())

    assert len(batch_calls) == 1
    assert [clause["clause_id"] for clause in batch_calls[0]["clauses"]] == [
        "revenue_growth",
        "regulatory_risk",
    ]
    assert batch_calls[0]["clauses"][0]["predicate_terms"] == ["driven by"]
    assert batch_calls[0]["clauses"][0]["calculation_window"] is None
    assert batch_calls[0]["clauses"][0]["comparison_axes"] == []
    assert len(raw["results_by_ticker"]["VG"]) == 2


def test_batch_shard_failure_is_excluded_from_resolved_scope() -> None:
    plan = SearchPlan(
        question="Compare one available and one failed shard",
        intent="comparison",
        tickers=["VG", "BAD"],
        clauses=[
            QueryClause(
                clause_id="fact",
                retrieval_query="filing fact",
                required_concepts=["filing fact"],
            )
        ],
    )

    class FailingBatchStore:
        def route_planned_tickers(self, **_kwargs):
            return ["VG", "BAD"], {
                "mode": "explicit_plan_scope",
                "resolved_tickers": ["VG", "BAD"],
                "fallback_used": False,
            }

        def query_planned_batch_with_diagnostics(self, **_kwargs):
            return {"fact": {"rows": [], "diagnostics": {}}}, {
                "execution_mode": "planned_shard_batch",
                "failed_tickers": ["BAD"],
                "shard_errors": {"BAD": "database disk image is malformed"},
                "warnings": ["ticker_shard_query_failed"],
                "routing": {
                    "resolved_tickers": ["VG"],
                    "failed_tickers": ["BAD"],
                    "shard_errors": {"BAD": "database disk image is malformed"},
                },
            }

    raw = _execute_search_plan(store=FailingBatchStore(), search_plan=plan)
    state = compile_research_state(
        search_plan=plan,
        raw_payload=raw,
        release_id="release",
    )

    assert raw["routing"]["resolved_tickers"] == ["VG"]
    assert state.resolved_scope.resolved_tickers == ["VG"]
    assert state.resolved_scope.failed_tickers == ["BAD"]
    assert any(
        part.code == "ticker_shard_query_failed" and part.ticker == "BAD"
        for part in state.missing_parts
    )


def test_relaxed_match_can_never_be_compiled_as_direct() -> None:
    plan = SearchPlan(
        question="Does VG have direct exposure?",
        intent="direct_exposure",
        tickers=["VG"],
        uncertainty=PlanUncertainty.HIGH,
        clauses=[
            QueryClause(
                clause_id="exposure",
                retrieval_query="VG direct exposure",
                required_concepts=["direct exposure"],
                directness=EvidenceRequirement.DIRECT_REQUIRED,
            )
        ],
    )
    state = compile_research_state(
        search_plan=plan,
        release_id="release",
        raw_payload={
            "results_by_ticker": {
                "VG": [
                    {
                        "id": "external_factor_exposure:VG:FY2025:10K:test",
                        "type": "ExternalFactorExposure",
                        "ticker": "VG",
                        "period": "FY2025",
                        "document_type": "10-K",
                        "text": "Related relaxed result",
                        "semantic_relevance": "direct",
                        "trace_status": "traceable",
                        "planned_match_mode": "relaxed",
                        "_plan_clause_ids": ["exposure"],
                    }
                ]
            }
        },
    )

    assert state.evidence_units[0].match_mode == "relaxed"
    assert state.evidence_units[0].directness == "related"
    assert state.clause_coverage[0].status == "partial"
    assert state.answerability.strong_claim_allowed is False


def test_compiler_enforces_hard_model_payload_budget() -> None:
    plan = SearchPlan(
        question="Summarize bounded evidence",
        intent="evidence_lookup",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="facts",
                retrieval_query="VG facts",
                required_concepts=["facts"],
            )
        ],
        limit_results=50,
    )
    rows = [
        {
            "id": f"claim:VG:FY2025:10K:{index}",
            "type": "ResearchClaim",
            "ticker": "VG",
            "period": "FY2025",
            "document_type": "10-K",
            "text": "bounded fact " + ("x" * 2_000),
            "trace_status": "traceable",
            "support_quote_count": 1,
            "source_object_ids": [f"source:{index}:" + ("y" * 5_000) for _ in range(16)],
            "_plan_clause_ids": ["facts"],
        }
        for index in range(50)
    ]

    state = compile_research_state(
        search_plan=plan,
        raw_payload={"results_by_ticker": {"VG": rows}},
        release_id="release",
    )

    assert research_state_model_bytes(state) <= MAX_RESEARCH_STATE_MODEL_BYTES
    assert research_state_wire_bytes(state) <= MAX_RESEARCH_STATE_WIRE_BYTES
    assert state.continuation is not None
    assert state.continuation.has_more is True
    assert state.continuation.omitted_evidence_count > 0


def test_payload_trimming_preserves_one_unit_for_each_required_clause() -> None:
    plan = SearchPlan(
        question="Keep both required facts while trimming optional detail",
        intent="bounded_multi_clause",
        tickers=["VG"],
        clauses=[
            QueryClause(
                clause_id="alpha",
                retrieval_query="VG alpha fact",
                required_concepts=["alpha fact"],
            ),
            QueryClause(
                clause_id="beta",
                retrieval_query="VG beta fact",
                required_concepts=["beta fact"],
            ),
            QueryClause(
                clause_id="optional",
                retrieval_query="VG optional detail",
                required_concepts=["optional detail"],
                required=False,
            ),
        ],
        limit_results=3,
    )

    def row(clause_id: str, *, huge: bool = False) -> dict[str, object]:
        source_ids = [f"source:{clause_id}"]
        if huge:
            source_ids = [f"source:{index}:" + ("z" * 7_000) for index in range(16)]
        return {
            "id": f"claim:VG:{clause_id}",
            "type": "ResearchClaim",
            "ticker": "VG",
            "text": f"VG {clause_id} fact",
            "trace_status": "traceable",
            "answer_candidate": True,
            "support_quote_count": 1,
            "source_object_ids": source_ids,
            "_plan_clause_matches": [
                {
                    "clause_id": clause_id,
                    "planned_match_mode": "strict",
                    "planned_evidence_terms": [clause_id],
                }
            ],
        }

    state = compile_research_state(
        search_plan=plan,
        raw_payload={
            "results_by_ticker": {"VG": [row("alpha"), row("beta"), row("optional", huge=True)]}
        },
        release_id="release",
    )

    assert {unit.supports_clause_ids[0] for unit in state.evidence_units} == {
        "alpha",
        "beta",
    }
    assert all(
        coverage.status == "covered" for coverage in state.clause_coverage if coverage.required
    )
    assert state.continuation is not None
    assert state.continuation.omitted_evidence_count == 1


def test_declared_missing_shard_is_not_reported_as_resolved_scope() -> None:
    plan = SearchPlan(
        question="Check a declared but missing ticker shard",
        intent="scope_check",
        tickers=["MISS"],
        clauses=[
            QueryClause(
                clause_id="fact",
                retrieval_query="MISS filing fact",
                required_concepts=["filing fact"],
            )
        ],
    )
    state = compile_research_state(
        search_plan=plan,
        raw_payload={
            "routing": {
                "resolved_tickers": [],
                "missing_shards": {"MISS": "/release/indexes/companies/MISS.sqlite"},
            }
        },
        release_id="release",
    )

    assert state.resolved_scope.resolved_tickers == []
    assert state.resolved_scope.missing_tickers == ["MISS"]
    assert any(part.code == "ticker_shard_missing" for part in state.missing_parts)


def test_mcp_query_context_has_required_strict_input_and_structured_output_schema() -> None:
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}

    assert tuple(sorted(tools)) == EXPECTED_TOOL_NAMES
    query_context = tools["krw_ontology_query_context"]
    assert query_context.parameters["required"] == ["search_plan"]
    plan_schema = query_context.parameters["$defs"]["SearchPlan"]
    assert plan_schema["additionalProperties"] is False
    assert plan_schema["required"] == ["question", "intent", "clauses"]
    assert query_context.fn_metadata.output_schema["properties"]["contract_version"]["const"] == (
        "research-state/v2"
    )


def test_mcp_query_context_emits_complete_minified_model_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logging.disable(logging.CRITICAL)
    try:
        _write_fixture(tmp_path)
        _build_v3_runtime(tmp_path)
    finally:
        logging.disable(logging.NOTSET)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    result = asyncio.run(krw_ontology_query_context(_plan()))

    assert isinstance(result, CallToolResult)
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert "\n" not in result.content[0].text
    assert json.loads(result.content[0].text) == result.structuredContent
    assert result.structuredContent["contract_version"] == "research-state/v2"
    assert result.structuredContent["evidence_units"]
    state = ResearchState.model_validate(result.structuredContent)
    assert len(result.content[0].text.encode("utf-8")) == research_state_model_bytes(state)
    assert (
        len(result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"))
        <= MAX_RESEARCH_STATE_WIRE_BYTES
    )


def test_health_and_readiness_expose_exact_fingerprints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logging.disable(logging.CRITICAL)
    try:
        _write_fixture(tmp_path)
        _build_v3_runtime(tmp_path)
    finally:
        logging.disable(logging.NOTSET)

    payload, status_code = health_payload(root=str(tmp_path))
    ready, ready_status = ready_payload(root=str(tmp_path))
    deploy_fingerprint = runtime_fingerprint_payload(root=tmp_path)

    assert status_code == ready_status == 200
    assert payload["mcp_contract_version"] == "krw-ontology-mcp/v2"
    assert payload["tool_count"] == 13
    assert tuple(payload["tool_names"]) == EXPECTED_TOOL_NAMES
    assert payload["tool_names_match"] is True
    assert payload["fingerprint_match"] is True
    for field in (
        "tool_schema_sha256",
        "build_fingerprint_sha256",
        "release_manifest_sha256",
        "service_fingerprint_sha256",
    ):
        assert len(payload[field]) == 64
        assert ready[field] == payload[field]
        assert deploy_fingerprint[field] == payload[field]

    with monkeypatch.context() as fingerprint_env:
        fingerprint_env.setenv("KRW_MCP_EXPECTED_TOOL_SCHEMA_SHA256", "0" * 64)
        mismatch, mismatch_status = ready_payload(root=str(tmp_path))
    assert mismatch_status == 503
    assert mismatch["error"] == "mcp_fingerprint_mismatch"
    assert mismatch["fingerprint_match"] is False
    assert mismatch["fingerprint_mismatches"] == [
        {
            "field": "tool_schema_sha256",
            "expected": "0" * 64,
            "actual": payload["tool_schema_sha256"],
        }
    ]


def test_source_plugin_uses_stdio_entrypoint() -> None:
    plugin_path = Path(__file__).parents[2] / "plugins" / "krw-ontology" / ".mcp.json"
    payload = json.loads(plugin_path.read_text(encoding="utf-8"))
    config = payload["mcpServers"]["krw-ontology"]

    assert config["command"] == "uv"
    assert "krw-ontology-mcp-stdio" in config["args"]
    assert "krw-ontology-mcp" not in config["args"]
