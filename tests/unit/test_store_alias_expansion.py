"""Alias-expanded metric channel for metric-less SearchPlan clauses.

When a clause carries no ``metrics`` but its ``retrieval_query`` names a
dictionary alias (``top line``, ``매출`` ...), the store additionally runs the
exact metric-lookup channel for the mapped canonical metric and merges the
result under ``planned_match_mode="alias_expanded"``.  The server never
rewrites the plan: the expansion is retrieval-side only.

Shard recipe mirrors ``tests/unit/test_shard_schema_v3.py`` (minimal SO/CY2023
release) plus two company-total revenue observations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from krw_ontology.agent_index.router import open_ontology_store
from krw_ontology.agent_index.spine_builder import build_spine_shard_release_outputs
from krw_ontology.agent_index.store import OntologyStore
from krw_ontology.mcp_server.contracts import QueryClause, SearchPlan
from krw_ontology.mcp_server.tools import _execute_search_plan
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.utils.io import atomic_write_json, write_jsonl

TICKER = "SO"
DOCUMENT_TYPE = "10-K"
DOC_TYPE_KEY = "10K"
PERIOD = "CY2023"

SOURCE_DOCUMENT_ID = f"source:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}"
SPAN_ID = f"span:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:item7:0001"
QUOTE_ID = f"quote:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:0001"
CLAIM_ID = f"claim:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:margin-expansion"
SUPPORT_LINK_ID = "support_link:SO:CY2023:10K:direct_quote_support:36ff437050"
# ``revenue`` is both the metric_name and the canonical id; ``net_sales`` is a
# dictionary alias whose canonical the index builder resolves to ``revenue``.
REVENUE_FY2022_ID = f"metric_observation:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:revenue:2022"
NET_SALES_FY2023_ID = f"metric_observation:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:net_sales:2023"
METRIC_IDS = {REVENUE_FY2022_ID, NET_SALES_FY2023_ID}

TEXT = "Server operating margin expanded because data center demand increased."


def _metric_observation(object_id: str, *, metric_name: str, year: int, value: float) -> dict:
    return {
        "id": object_id,
        "type": "MetricObservation",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "metric_name": metric_name,
        "value": value,
        "unit": "USD",
        "fiscal_year": year,
        "period_type": "annual",
        "period_start": f"{year}-01-01",
        "period_end": f"{year}-12-31",
        "source_type": "reported",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }


def _write_minimal_artifacts(root: Path) -> None:
    ontology_dir = root / "companies" / TICKER / "ontology" / DOC_TYPE_KEY / PERIOD
    sources_dir = root / "companies" / TICKER / "sources" / DOC_TYPE_KEY / PERIOD
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    span = {
        "id": SPAN_ID,
        "type": "SourceSpan",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "section_name": "item7",
        "section_key": "item7",
        "span_index": 1,
        "text": TEXT,
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    quote = {
        "id": QUOTE_ID,
        "type": "EvidenceQuote",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "source_span_id": SPAN_ID,
        "quote_text": TEXT,
        "quote_type": "business_update",
        "section_name": "item7",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    claim = {
        "id": CLAIM_ID,
        "type": "ResearchClaim",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "claim_text": "Server operating margin expanded on data center demand.",
        "claim_type": "business_update",
        "supported_by_quotes": [QUOTE_ID],
        "related_metrics": ["operating_margin"],
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    support_link = {
        "id": SUPPORT_LINK_ID,
        "type": "SupportLink",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "from_id": QUOTE_ID,
        "to_id": CLAIM_ID,
        "support_object_id": QUOTE_ID,
        "support_object_type": "EvidenceQuote",
        "target_object_id": CLAIM_ID,
        "target_object_type": "ResearchClaim",
        "support_type": "direct_quote_support",
        "support_role": "quote_support",
        "stance": "supports",
        "support_strength": "direct",
        "inference_level": "direct_quote",
        "evidence_grade": "direct",
        "evidence_strength": "direct",
        "requires_inference": False,
        "created_by": "deterministic_projection",
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }

    write_jsonl(ontology_dir / "spans.jsonl", [span])
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", [quote])
    write_jsonl(ontology_dir / "claims.jsonl", [claim])
    write_jsonl(ontology_dir / "support_links.jsonl", [support_link])
    write_jsonl(
        ontology_dir / "metric_observations.jsonl",
        [
            _metric_observation(REVENUE_FY2022_ID, metric_name="revenue", year=2022, value=100.0),
            _metric_observation(
                NET_SALES_FY2023_ID, metric_name="net_sales", year=2023, value=125.0
            ),
        ],
    )
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    build_indexes(
        ticker=TICKER,
        period=PERIOD,
        doc_type_key=DOC_TYPE_KEY,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=root,
        document_type=DOCUMENT_TYPE,
    )


@pytest.fixture(scope="module")
def release_paths(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("alias-expansion-release")
    _write_minimal_artifacts(root)
    result = build_spine_shard_release_outputs(
        root,
        release_id="test-store-alias-expansion",
        workers=1,
        no_cache=True,
    )
    shard_path = result.global_spine_path.parent / "companies" / f"{TICKER}.sqlite"
    assert shard_path.is_file()
    return shard_path, result.global_spine_path


def _query(store: OntologyStore, **overrides):
    kwargs = {
        "clause_id": "rev_growth",
        "retrieval_query": "top line sales growth",
        "tickers": [TICKER],
        "limit": 5,
    }
    kwargs.update(overrides)
    return store.query_planned_compact_with_diagnostics(**kwargs)


def _metric_rows(rows):
    return [row for row in rows if row["type"] == "MetricObservation"]


def test_metric_less_clause_with_alias_returns_alias_expanded_metric_units(release_paths):
    shard_path, _ = release_paths
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store)

    metric_rows = _metric_rows(rows)
    assert {row["id"] for row in metric_rows} == METRIC_IDS
    assert {row["planned_match_mode"] for row in metric_rows} == {"alias_expanded"}
    # Metric metadata is applied exactly as for the ``metrics`` channel.
    assert {row["object"]["canonical_metric"] for row in metric_rows} == {"revenue"}
    assert all(row["object"]["is_company_total"] is True for row in metric_rows)
    assert {row["period"] for row in metric_rows} == {"CY2022", "CY2023"}
    assert all(row["object"]["observation_context_key"] for row in metric_rows)
    # Clause attribution fields are identical to normal units.
    assert all("planned_evidence_terms" in row for row in metric_rows)
    assert all(row["planned_lexical_terms"] == diagnostics["lexical_terms"] for row in rows)

    assert diagnostics["alias_expansion_used"] is True
    assert diagnostics["alias_expansions"] == [
        {
            "clause_id": "rev_growth",
            "alias": "top-line sales",
            "canonical_metric": "revenue",
            "added_units": 2,
        }
    ]
    assert diagnostics["alias_expanded_result_count"] == 2
    # The plan itself is untouched: no requested metrics, primary channel idle.
    assert diagnostics["metric_lookup_used"] is False
    assert diagnostics["requested_metrics"] == []
    assert diagnostics["keyword_expansion_used"] is False
    assert diagnostics["intent_reclassification_used"] is False


def test_clause_without_alias_terms_is_unchanged(release_paths):
    shard_path, _ = release_paths
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(
            store,
            clause_id="margin",
            retrieval_query="server margin data center demand",
        )

    assert rows
    assert {row["planned_match_mode"] for row in rows} == {"strict"}
    assert not _metric_rows(rows)
    assert "alias_expansions" not in diagnostics
    assert diagnostics["alias_expansion_used"] is False
    assert diagnostics["alias_expanded_result_count"] == 0


def test_clause_with_metrics_never_alias_expands(release_paths):
    shard_path, _ = release_paths
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store, metrics=["revenue"])

    metric_rows = _metric_rows(rows)
    assert {row["id"] for row in metric_rows} == METRIC_IDS
    assert {row["planned_match_mode"] for row in metric_rows} == {"strict"}
    assert diagnostics["metric_lookup_used"] is True
    assert diagnostics["metric_result_count"] == 2
    assert "alias_expansions" not in diagnostics
    assert diagnostics["alias_expansion_used"] is False
    assert diagnostics["alias_expanded_result_count"] == 0


def test_alias_units_dedupe_against_fts_hits_and_count_only_new_units(release_paths):
    shard_path, _ = release_paths
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store, clause_id="rev", retrieval_query="revenue")

    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    by_id = {row["id"]: row for row in rows}
    # ``revenue`` is in the FY2022 observation's indexed text, so strict FTS
    # already returned it; the alias run must not append it again.
    assert by_id[REVENUE_FY2022_ID]["planned_match_mode"] == "strict"
    # ``net_sales`` never matched ``revenue*`` lexically but shares the
    # canonical, so it is the only genuinely new unit.
    assert by_id[NET_SALES_FY2023_ID]["planned_match_mode"] == "alias_expanded"
    assert diagnostics["alias_expansions"] == [
        {
            "clause_id": "rev",
            "alias": "revenue",
            "canonical_metric": "revenue",
            "added_units": 1,
        }
    ]
    assert diagnostics["alias_expanded_result_count"] == 1
    assert diagnostics["fts_strict_result_count"] >= 1


def test_korean_alias_expands_to_canonical_metric(release_paths):
    shard_path, _ = release_paths
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store, clause_id="kr", retrieval_query="매출 성장률 추이")

    metric_rows = _metric_rows(rows)
    assert {row["id"] for row in metric_rows} == METRIC_IDS
    assert {row["planned_match_mode"] for row in metric_rows} == {"alias_expanded"}
    assert diagnostics["alias_expansions"] == [
        {
            "clause_id": "kr",
            "alias": "매출",
            "canonical_metric": "revenue",
            "added_units": 2,
        }
    ]


def test_alias_units_respect_clause_limit_budget(release_paths):
    shard_path, _ = release_paths
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store, limit=1)

    assert len(rows) == 1
    assert rows[0]["planned_match_mode"] == "alias_expanded"
    assert diagnostics["alias_expansions"][0]["added_units"] == 1
    assert diagnostics["result_count"] == 1


def test_alias_expansion_respects_clause_scope_filters(release_paths):
    shard_path, _ = release_paths
    with OntologyStore(shard_path) as store:
        scoped_rows, scoped_diagnostics = _query(store, periods=["FY2023"])
        dimensioned_rows, dimensioned_diagnostics = _query(store, metric_scope="dimensioned")

    assert [row["id"] for row in _metric_rows(scoped_rows)] == [NET_SALES_FY2023_ID]
    assert scoped_diagnostics["alias_expansions"][0]["added_units"] == 1
    # Both observations are company totals, so a dimensioned clause scope
    # yields nothing from the alias channel and the diagnostics say so.
    assert not _metric_rows(dimensioned_rows)
    assert dimensioned_diagnostics["alias_expansions"] == [
        {
            "clause_id": "rev_growth",
            "alias": "top-line sales",
            "canonical_metric": "revenue",
            "added_units": 0,
        }
    ]


def test_duplicate_aliases_for_one_canonical_run_once(release_paths):
    shard_path, _ = release_paths
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(
            store,
            clause_id="dup",
            retrieval_query="top line and total revenue trajectory",
        )

    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    assert {row["id"] for row in _metric_rows(rows)} == METRIC_IDS
    assert diagnostics["alias_expansions"] == [
        {
            "clause_id": "dup",
            "alias": "top line",
            "canonical_metric": "revenue",
            "added_units": 2,
        },
        {
            "clause_id": "dup",
            "alias": "total_revenue",
            "canonical_metric": "revenue",
            "added_units": 0,
        },
    ]
    assert diagnostics["alias_expanded_result_count"] == 2


# ---------------------------------------------------------------------------
# Alias metric floor (full-window metric-less clauses)
# ---------------------------------------------------------------------------

FLOOR_QUOTE_COUNT = 4
FLOOR_QUOTE_IDS = [
    f"quote:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:floor:{index:04d}"
    for index in range(FLOOR_QUOTE_COUNT)
]
FLOOR_SPAN_IDS = [
    f"span:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:floor:{index:04d}" for index in range(FLOOR_QUOTE_COUNT)
]
FLOOR_REVENUE_CY2022_ID = f"metric_observation:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:revenue:2022"
FLOOR_REVENUE_CY2023_ID = f"metric_observation:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:revenue:2023"
FLOOR_METRIC_IDS = {FLOOR_REVENUE_CY2022_ID, FLOOR_REVENUE_CY2023_ID}

# The floor recipe mirrors the period-equity shard: several qualitative quotes
# whose text matches the alias-bearing query's lexical terms, so strict FTS
# alone fills the window, plus two company-total revenue observations that only
# the alias channel can reach.
FLOOR_RETRIEVAL_QUERY = "top line sales growth"
FLOOR_NO_ALIAS_QUERY = "sequential outlook widened note"


def _floor_quote_text(index: int) -> str:
    return f"Top line sales growth widened sequentially in outlook note {index + 1}."


def _write_floor_artifacts(root: Path) -> None:
    ontology_dir = root / "companies" / TICKER / "ontology" / DOC_TYPE_KEY / PERIOD
    sources_dir = root / "companies" / TICKER / "sources" / DOC_TYPE_KEY / PERIOD
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    spans = []
    quotes = []
    for index, (span_id, quote_id) in enumerate(zip(FLOOR_SPAN_IDS, FLOOR_QUOTE_IDS)):
        spans.append(
            {
                "id": span_id,
                "type": "SourceSpan",
                "ticker": TICKER,
                "source_document_id": SOURCE_DOCUMENT_ID,
                "document_type": DOCUMENT_TYPE,
                "period": PERIOD,
                "section_name": "item7",
                "section_key": "item7",
                "span_index": index + 1,
                "text": _floor_quote_text(index),
                "review_status": "accepted",
                "schema_version": "0.1.0",
            }
        )
        quotes.append(
            {
                "id": quote_id,
                "type": "EvidenceQuote",
                "ticker": TICKER,
                "source_document_id": SOURCE_DOCUMENT_ID,
                "document_type": DOCUMENT_TYPE,
                "period": PERIOD,
                "source_span_id": span_id,
                "quote_text": _floor_quote_text(index),
                "quote_type": "business_update",
                "section_name": "item7",
                "review_status": "accepted",
                "schema_version": "0.1.0",
            }
        )

    write_jsonl(ontology_dir / "spans.jsonl", spans)
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", quotes)
    write_jsonl(ontology_dir / "claims.jsonl", [])
    write_jsonl(ontology_dir / "support_links.jsonl", [])
    write_jsonl(
        ontology_dir / "metric_observations.jsonl",
        [
            _metric_observation(
                FLOOR_REVENUE_CY2022_ID, metric_name="revenue", year=2022, value=100.0
            ),
            _metric_observation(
                FLOOR_REVENUE_CY2023_ID, metric_name="revenue", year=2023, value=125.0
            ),
        ],
    )
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    build_indexes(
        ticker=TICKER,
        period=PERIOD,
        doc_type_key=DOC_TYPE_KEY,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=root,
        document_type=DOCUMENT_TYPE,
    )


@pytest.fixture(scope="module")
def floor_shard_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("alias-floor-release")
    _write_floor_artifacts(root)
    result = build_spine_shard_release_outputs(
        root,
        release_id="test-store-alias-floor",
        workers=1,
        no_cache=True,
    )
    shard_path = result.global_spine_path.parent / "companies" / f"{TICKER}.sqlite"
    assert shard_path.is_file()
    return shard_path


def _floor_query(store: OntologyStore, **overrides):
    kwargs = {
        "clause_id": "rev_floor",
        "retrieval_query": FLOOR_RETRIEVAL_QUERY,
        "tickers": [TICKER],
        "limit": FLOOR_QUOTE_COUNT,
    }
    kwargs.update(overrides)
    return store.query_planned_compact_with_diagnostics(**kwargs)


def test_full_window_metric_less_alias_clause_applies_alias_floor(floor_shard_path):
    """Strict FTS alone fills the window, so pre-floor the alias channel could
    only watch from ``alias_candidate_rows`` and ``added_units`` stayed 0 (the
    wmt/amzn/aapl starvation).  The floor must guarantee both alias units."""
    with OntologyStore(floor_shard_path) as store:
        rows, diagnostics = _floor_query(store)

    assert diagnostics["fts_strict_result_count"] == FLOOR_QUOTE_COUNT
    assert len(rows) == FLOOR_QUOTE_COUNT
    metric_rows = _metric_rows(rows)
    assert {row["id"] for row in metric_rows} == FLOOR_METRIC_IDS
    assert {row["planned_match_mode"] for row in metric_rows} == {"alias_expanded"}
    # Floor units take the head positions in existing candidate order
    # (latest observation first), exactly like Task-3 reservations.
    assert [row["id"] for row in rows[:2]] == [FLOOR_REVENUE_CY2023_ID, FLOOR_REVENUE_CY2022_ID]
    assert len([row for row in rows if row["type"] == "EvidenceQuote"]) == FLOOR_QUOTE_COUNT - 2

    assert diagnostics["alias_floor_applied"] is True
    assert diagnostics["alias_expansions"] == [
        {
            "clause_id": "rev_floor",
            "alias": "top-line sales",
            "canonical_metric": "revenue",
            "added_units": 2,
        }
    ]
    assert diagnostics["alias_expanded_result_count"] == 2
    assert diagnostics["result_count"] == FLOOR_QUOTE_COUNT
    # The floor is the single-period fallback: no period reservation fired.
    assert "period_reservations" not in diagnostics


def test_single_period_full_window_floor_keeps_scoped_alias_units(floor_shard_path):
    """The wmt/amzn/aapl shape: one explicitly requested period, a full strict
    window, and alias units that only cover that period."""
    with OntologyStore(floor_shard_path) as store:
        rows, diagnostics = _floor_query(store, periods=["CY2023"])

    assert diagnostics["fts_strict_result_count"] == FLOOR_QUOTE_COUNT
    assert len(rows) == FLOOR_QUOTE_COUNT
    assert {row["id"] for row in _metric_rows(rows)} == {FLOOR_REVENUE_CY2023_ID}
    assert diagnostics["alias_floor_applied"] is True
    assert diagnostics["alias_expansions"][0]["added_units"] == 1
    assert diagnostics["alias_expanded_result_count"] == 1
    # Both metric rows of this shard live in the single CY2023 filing bucket,
    # so filing-bucket alignment is a no-op and must not raise its flag.
    assert "filing_bucket_aligned" not in diagnostics


def test_window_not_full_keeps_append_behaviour_without_floor_flag(floor_shard_path):
    with OntologyStore(floor_shard_path) as store:
        rows, diagnostics = _floor_query(store, limit=FLOOR_QUOTE_COUNT + 4)

    assert len(rows) == FLOOR_QUOTE_COUNT + 2
    assert {row["id"] for row in _metric_rows(rows)} == FLOOR_METRIC_IDS
    assert "alias_floor_applied" not in diagnostics
    assert diagnostics["alias_expansions"][0]["added_units"] == 2


def test_partial_append_after_room_does_not_apply_floor(floor_shard_path):
    """One free slot let the alias channel append its rank-first unit; the rest
    became candidates only after the window filled.  The floor trigger is the
    window state after strict+relaxed assembly, so nothing changes here."""
    with OntologyStore(floor_shard_path) as store:
        rows, diagnostics = _floor_query(store, limit=FLOOR_QUOTE_COUNT + 1)

    assert len(rows) == FLOOR_QUOTE_COUNT + 1
    assert {row["id"] for row in _metric_rows(rows)} == {FLOOR_REVENUE_CY2023_ID}
    assert "alias_floor_applied" not in diagnostics
    assert diagnostics["alias_expansions"][0]["added_units"] == 1


def test_no_alias_metric_less_full_window_is_unchanged(floor_shard_path):
    with OntologyStore(floor_shard_path) as store:
        rows, diagnostics = _floor_query(
            store,
            clause_id="outlook",
            retrieval_query=FLOOR_NO_ALIAS_QUERY,
        )

    assert len(rows) == FLOOR_QUOTE_COUNT
    assert not _metric_rows(rows)
    assert {row["planned_match_mode"] for row in rows} == {"strict"}
    assert diagnostics["alias_expansion_used"] is False
    assert "alias_expansions" not in diagnostics
    assert "alias_floor_applied" not in diagnostics


def test_clause_with_metrics_keeps_full_window_behaviour(floor_shard_path):
    with OntologyStore(floor_shard_path) as store:
        rows, diagnostics = _floor_query(store, metrics=["revenue"])

    assert len(rows) == FLOOR_QUOTE_COUNT
    metric_rows = _metric_rows(rows)
    assert {row["id"] for row in metric_rows} == FLOOR_METRIC_IDS
    assert {row["planned_match_mode"] for row in metric_rows} == {"strict"}
    assert diagnostics["metric_lookup_used"] is True
    assert diagnostics["alias_expansion_used"] is False
    assert "alias_floor_applied" not in diagnostics


# ---------------------------------------------------------------------------
# Filing-bucket alignment for metric-channel rows (Task 6)
# ---------------------------------------------------------------------------

# Two filing buckets for one ticker (the msft bottom-line shape).  The CY2025
# bucket holds the as-originally-reported calendar-2025 net income; the CY2026
# bucket holds the June-fiscal FY2025 comparative, which also observes the
# requested CY2025 coordinate but lives in a later filing.  Pre-change the
# metric channel ranked the FY-labeled comparative row first (its period key
# sorts above CY2025), so the CY2026-bucket row won the slot even though the
# clause explicitly requested CY2025.
ALIGNMENT_FILING_CY2025 = "CY2025"
ALIGNMENT_FILING_CY2026 = "CY2026"

ALIGNMENT_NET_INCOME_CY2025_ID = (
    f"metric_observation:{TICKER}:{ALIGNMENT_FILING_CY2025}:{DOC_TYPE_KEY}:net_income:reported"
)
ALIGNMENT_NET_INCOME_FY2025_ID = (
    f"metric_observation:{TICKER}:{ALIGNMENT_FILING_CY2026}:{DOC_TYPE_KEY}:net_income:comparative"
)
ALIGNMENT_REVENUE_FY2025_ID = (
    f"metric_observation:{TICKER}:{ALIGNMENT_FILING_CY2025}:{DOC_TYPE_KEY}:revenue:fiscal"
)
ALIGNMENT_REVENUE_CY2025_ID = (
    f"metric_observation:{TICKER}:{ALIGNMENT_FILING_CY2026}:{DOC_TYPE_KEY}:revenue:calendar"
)

ALIGNMENT_RETRIEVAL_QUERY = "bottom line"


def _bucket_metric_observation(
    object_id: str,
    *,
    filing_period: str,
    metric_name: str,
    observation_start: str,
    observation_end: str,
    fiscal_year: int,
    value: float,
) -> dict:
    """One company-total annual observation inside an explicit filing bucket.

    Calendar-year dates derive a ``CY`` observation label; July--June dates
    derive ``FY`` — the two label conventions that make the comparative row a
    separate observation context instead of a duplicate value.
    """
    return {
        "id": object_id,
        "type": "MetricObservation",
        "ticker": TICKER,
        "source_document_id": f"source:{TICKER}:{filing_period}:{DOC_TYPE_KEY}",
        "document_type": DOCUMENT_TYPE,
        "period": filing_period,
        "metric_name": metric_name,
        "value": value,
        "unit": "USD",
        "fiscal_year": fiscal_year,
        "period_type": "annual",
        "period_start": observation_start,
        "period_end": observation_end,
        "source_type": "reported",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }


def _write_alignment_artifacts(root: Path) -> None:
    for filing_period in (ALIGNMENT_FILING_CY2025, ALIGNMENT_FILING_CY2026):
        ontology_dir = root / "companies" / TICKER / "ontology" / DOC_TYPE_KEY / filing_period
        sources_dir = root / "companies" / TICKER / "sources" / DOC_TYPE_KEY / filing_period
        ontology_dir.mkdir(parents=True)
        sources_dir.mkdir(parents=True)
        atomic_write_json(
            ontology_dir / "section_quality.json",
            {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        )
    cy2025_dir = root / "companies" / TICKER / "ontology" / DOC_TYPE_KEY / ALIGNMENT_FILING_CY2025
    cy2026_dir = root / "companies" / TICKER / "ontology" / DOC_TYPE_KEY / ALIGNMENT_FILING_CY2026
    write_jsonl(
        cy2025_dir / "metric_observations.jsonl",
        [
            _bucket_metric_observation(
                ALIGNMENT_NET_INCOME_CY2025_ID,
                filing_period=ALIGNMENT_FILING_CY2025,
                metric_name="net_income",
                observation_start="2025-01-01",
                observation_end="2025-12-31",
                fiscal_year=2025,
                value=100.0,
            ),
            _bucket_metric_observation(
                ALIGNMENT_REVENUE_FY2025_ID,
                filing_period=ALIGNMENT_FILING_CY2025,
                metric_name="revenue",
                observation_start="2024-07-01",
                observation_end="2025-06-30",
                fiscal_year=2025,
                value=200.0,
            ),
        ],
    )
    write_jsonl(
        cy2026_dir / "metric_observations.jsonl",
        [
            _bucket_metric_observation(
                ALIGNMENT_NET_INCOME_FY2025_ID,
                filing_period=ALIGNMENT_FILING_CY2026,
                metric_name="net_income",
                observation_start="2024-07-01",
                observation_end="2025-06-30",
                fiscal_year=2025,
                value=110.0,
            ),
            _bucket_metric_observation(
                ALIGNMENT_REVENUE_CY2025_ID,
                filing_period=ALIGNMENT_FILING_CY2026,
                metric_name="revenue",
                observation_start="2025-01-01",
                observation_end="2025-12-31",
                fiscal_year=2025,
                value=210.0,
            ),
        ],
    )
    for ontology_dir in (cy2025_dir, cy2026_dir):
        build_indexes(
            ticker=TICKER,
            period=ontology_dir.name,
            doc_type_key=DOC_TYPE_KEY,
            ontology_dir=ontology_dir,
            sources_dir=root / "companies" / TICKER / "sources" / DOC_TYPE_KEY / ontology_dir.name,
            output_dir=root,
            document_type=DOCUMENT_TYPE,
        )


@pytest.fixture(scope="module")
def alignment_shard_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("filing-bucket-alignment-release")
    _write_alignment_artifacts(root)
    result = build_spine_shard_release_outputs(
        root,
        release_id="test-store-filing-bucket-alignment",
        workers=1,
        no_cache=True,
    )
    shard_path = result.global_spine_path.parent / "companies" / f"{TICKER}.sqlite"
    assert shard_path.is_file()
    return shard_path


def _alignment_query(store: OntologyStore, **overrides):
    kwargs = {
        "clause_id": "bottom_line",
        "retrieval_query": ALIGNMENT_RETRIEVAL_QUERY,
        "tickers": [TICKER],
        "limit": 2,
    }
    kwargs.update(overrides)
    return store.query_planned_compact_with_diagnostics(**kwargs)


def test_requested_period_prefers_same_filing_bucket_alias_row(alignment_shard_path):
    """Two filing buckets both observe net income for the requested CY2025;
    pre-change the CY2026-bucket comparative row ranked first and won the
    slot, post-change the CY2025-bucket row ranks first."""
    with OntologyStore(alignment_shard_path) as store:
        rows, diagnostics = _alignment_query(store, periods=[ALIGNMENT_FILING_CY2025])

    metric_rows = _metric_rows(rows)
    assert len(metric_rows) == 2
    assert {row["planned_match_mode"] for row in metric_rows} == {"alias_expanded"}
    # The same-filing-bucket row ranks first; the comparative row follows.
    assert [row["id"] for row in metric_rows] == [
        ALIGNMENT_NET_INCOME_CY2025_ID,
        ALIGNMENT_NET_INCOME_FY2025_ID,
    ]
    assert metric_rows[0]["filing_period"] == ALIGNMENT_FILING_CY2025
    assert metric_rows[0]["period"] == "CY2025"
    assert metric_rows[1]["filing_period"] == ALIGNMENT_FILING_CY2026
    assert diagnostics["filing_bucket_aligned"] is True
    assert diagnostics["alias_expansions"][0]["canonical_metric"] == "net_income"
    assert diagnostics["alias_expansions"][0]["added_units"] == 2


def test_metrics_channel_prefers_same_filing_bucket_rows_in_stable_order(alignment_shard_path):
    """Stability: within the matching-bucket group the original relative order
    is preserved (revenue-fiscal before net_income-reported, exactly their
    pre-partition order), and the comparative CY2026-bucket rows follow.  The
    existing per-metric round-robin then interleaves the two groups as
    before."""
    with OntologyStore(alignment_shard_path) as store:
        rows, diagnostics = _alignment_query(
            store,
            clause_id="bottoms_up",
            metrics=["net_income", "revenue"],
            limit=6,
            periods=[ALIGNMENT_FILING_CY2025],
        )

    metric_rows = _metric_rows(rows)
    assert len(metric_rows) == 4
    assert [row["id"] for row in metric_rows] == [
        ALIGNMENT_REVENUE_FY2025_ID,
        ALIGNMENT_NET_INCOME_CY2025_ID,
        ALIGNMENT_REVENUE_CY2025_ID,
        ALIGNMENT_NET_INCOME_FY2025_ID,
    ]
    assert [row["filing_period"] for row in metric_rows] == [
        ALIGNMENT_FILING_CY2025,
        ALIGNMENT_FILING_CY2025,
        ALIGNMENT_FILING_CY2026,
        ALIGNMENT_FILING_CY2026,
    ]
    assert {row["planned_match_mode"] for row in metric_rows} == {"strict"}
    assert diagnostics["filing_bucket_aligned"] is True


def test_clause_without_periods_keeps_latest_filing_rank(alignment_shard_path):
    """A clause that names no period is untouched: the comparative FY-labeled
    row keeps its existing first rank and no alignment flag is recorded."""
    with OntologyStore(alignment_shard_path) as store:
        rows, diagnostics = _alignment_query(store)

    metric_rows = _metric_rows(rows)
    assert [row["id"] for row in metric_rows] == [
        ALIGNMENT_NET_INCOME_FY2025_ID,
        ALIGNMENT_NET_INCOME_CY2025_ID,
    ]
    assert metric_rows[0]["filing_period"] == ALIGNMENT_FILING_CY2026
    assert "filing_bucket_aligned" not in diagnostics


def test_single_bucket_metric_rows_keep_order_without_alignment_flag(floor_shard_path):
    """Single-bucket regression: every metric row lives in the CY2023 filing,
    so the partition cannot change the sequence and no flag is recorded —
    even with two explicitly requested periods."""
    with OntologyStore(floor_shard_path) as store:
        rows, diagnostics = _floor_query(
            store, limit=FLOOR_QUOTE_COUNT + 4, periods=["CY2022", "CY2023"]
        )

    assert [row["id"] for row in _metric_rows(rows)] == [
        FLOOR_REVENUE_CY2023_ID,
        FLOOR_REVENUE_CY2022_ID,
    ]
    assert "filing_bucket_aligned" not in diagnostics
    assert "alias_floor_applied" not in diagnostics
    assert "period_reservations" not in diagnostics


def test_concept_or_rung_leaves_alias_channel_unchanged(release_paths):
    """Integration: singularized prefixes plus the concept-OR rung must not
    disturb the alias channel.  ``sales`` keeps its integral ``s`` (the query
    still emits ``sales*``), the concept-OR rung finds no qualitative hit in
    this corpus, it must not capture the FTS-visible ``net_sales`` metric row
    (metric attribution belongs to the exact/alias channels), and both alias
    units still land under ``alias_expanded``."""
    shard_path, _ = release_paths
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store)

    assert diagnostics["lexical_terms"] == ["top", "line", "sales", "growth"]
    assert diagnostics["concept_or_attempted"] is True
    assert diagnostics["concept_or_result_count"] == 0
    assert {row["id"] for row in _metric_rows(rows)} == METRIC_IDS
    assert {row["planned_match_mode"] for row in _metric_rows(rows)} == {"alias_expanded"}
    assert diagnostics["alias_expansions"] == [
        {
            "clause_id": "rev_growth",
            "alias": "top-line sales",
            "canonical_metric": "revenue",
            "added_units": 2,
        }
    ]


def test_router_batch_threads_clause_id_into_shard_diagnostics(release_paths):
    _, global_spine_path = release_paths
    with open_ontology_store(global_spine_path) as router:
        by_clause, _batch_diagnostics = router.query_planned_batch_with_diagnostics(
            clauses=[
                {
                    "clause_id": "rev_growth",
                    "retrieval_query": "top line sales growth",
                    "metrics": [],
                    "metric_scope": "company_total",
                },
                {
                    "clause_id": "margin",
                    "retrieval_query": "server margin data center demand",
                    "metrics": [],
                    "metric_scope": "company_total",
                },
            ],
            tickers=[TICKER],
            limit=5,
        )

    expanded = by_clause["rev_growth"]
    assert {row["planned_match_mode"] for row in expanded["rows"]} == {"alias_expanded"}
    shard = expanded["diagnostics"]["shard_diagnostics"][TICKER]
    assert shard["alias_expansions"] == [
        {
            "clause_id": "rev_growth",
            "alias": "top-line sales",
            "canonical_metric": "revenue",
            "added_units": 2,
        }
    ]
    plain = by_clause["margin"]
    assert "alias_expansions" not in plain["diagnostics"]["shard_diagnostics"][TICKER]


def _plan() -> SearchPlan:
    return SearchPlan(
        question="How did the top line move?",
        intent="metric_lookup",
        tickers=["SO", "VG"],
        clauses=[
            QueryClause(
                clause_id="rev_growth",
                retrieval_query="top line sales growth",
                required_concepts=["top line sales growth"],
            ),
            QueryClause(
                clause_id="margin",
                retrieval_query="server margin demand",
                required_concepts=["server margin demand"],
            ),
        ],
    )


def test_search_plan_diagnostics_aggregate_alias_expansions_across_shards() -> None:
    class FakeBatchStore:
        def route_planned_tickers(self, **_kwargs):
            return ["SO", "VG"], {"mode": "explicit_plan_scope", "resolved_tickers": ["SO", "VG"]}

        def query_planned_batch_with_diagnostics(self, **kwargs):
            payload = {}
            for clause in kwargs["clauses"]:
                clause_id = clause["clause_id"]
                if clause_id == "rev_growth":
                    shard_diagnostics = {
                        ticker: {
                            "execution_mode": "planned_fts",
                            "alias_expansions": [
                                {
                                    "clause_id": clause_id,
                                    "alias": "top-line sales",
                                    "canonical_metric": "revenue",
                                    "added_units": added,
                                }
                            ],
                        }
                        for ticker, added in (("SO", 2), ("VG", 1))
                    }
                else:
                    shard_diagnostics = {
                        ticker: {"execution_mode": "planned_fts"} for ticker in ("SO", "VG")
                    }
                payload[clause_id] = {
                    "rows": [
                        {
                            "id": f"metric:{ticker}:{clause_id}",
                            "type": "MetricObservation",
                            "ticker": ticker,
                            "planned_match_mode": "alias_expanded",
                        }
                        for ticker in ("SO", "VG")
                    ],
                    "diagnostics": {
                        "execution_mode": "planned_shard_batch",
                        "shard_diagnostics": shard_diagnostics,
                    },
                }
            return payload, {"execution_mode": "planned_shard_batch"}

    raw = _execute_search_plan(store=FakeBatchStore(), search_plan=_plan())

    assert raw["search_diagnostics"]["alias_expansions"] == [
        {
            "clause_id": "rev_growth",
            "alias": "top-line sales",
            "canonical_metric": "revenue",
            "added_units": 3,
        }
    ]
    # Clause attribution keeps the labelled match mode for alias units.
    so_rows = {row["id"]: row for row in raw["results_by_ticker"]["SO"]}
    assert so_rows["metric:SO:rev_growth"]["_plan_clause_matches"][0]["planned_match_mode"] == (
        "alias_expanded"
    )


def test_search_plan_diagnostics_omit_alias_expansions_when_nothing_fired() -> None:
    class FakeBatchStore:
        def route_planned_tickers(self, **_kwargs):
            return ["SO"], {"mode": "explicit_plan_scope", "resolved_tickers": ["SO"]}

        def query_planned_batch_with_diagnostics(self, **kwargs):
            return {
                clause["clause_id"]: {
                    "rows": [],
                    "diagnostics": {
                        "execution_mode": "planned_shard_batch",
                        "shard_diagnostics": {"SO": {"execution_mode": "planned_fts"}},
                    },
                }
                for clause in kwargs["clauses"]
            }, {"execution_mode": "planned_shard_batch"}

    raw = _execute_search_plan(store=FakeBatchStore(), search_plan=_plan())

    assert "alias_expansions" not in raw["search_diagnostics"]
