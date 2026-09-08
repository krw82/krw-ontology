"""Per-requested-period metric reservation in planned-query fusion.

When a clause explicitly requests two or more periods, the metric channel
ranks its units latest-period-first.  Qualitative strict-FTS units fill the
fusion window first for metric-less (alias-expanded) clauses, so the
older requested period's metric observations could lose the window cut
entirely -- the Task-6 "multi_period" starvation.  The fusion layer now
reserves up to two metric slots per explicitly requested period before the
final ``result_limit`` cut and displaces only the lowest-ranked
non-metric rows.

Shard recipe mirrors ``tests/unit/test_shard_schema_v3.py`` (minimal SO
release) plus six qualitative quotes that match the clause's lexical terms
and two company-total revenue observations (CY2022, CY2023).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from krw_ontology.agent_index.spine_builder import build_spine_shard_release_outputs
from krw_ontology.agent_index.store import OntologyStore
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.utils.io import atomic_write_json, write_jsonl

TICKER = "SO"
DOCUMENT_TYPE = "10-K"
DOC_TYPE_KEY = "10K"
PERIOD = "CY2023"

SOURCE_DOCUMENT_ID = f"source:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}"

QUOTE_COUNT = 6
QUOTE_IDS = [
    f"quote:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:{index:04d}" for index in range(QUOTE_COUNT)
]
SPAN_IDS = [
    f"span:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:item7:{index:04d}"
    for index in range(QUOTE_COUNT)
]
REVENUE_CY2022_ID = f"metric_observation:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:revenue:2022"
REVENUE_CY2023_ID = f"metric_observation:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:revenue:2023"
METRIC_IDS = {REVENUE_CY2022_ID, REVENUE_CY2023_ID}

# The retrieval query resolves the dictionary alias ``top-line sales`` to the
# canonical ``revenue`` while its lexical terms (top/line/sales/growth) match
# every qualitative quote below and nothing in the metric observations.  The
# shard intentionally carries no claims: claim retrieval text joins its
# supported quotes, which would add extra lexical matches beyond QUOTE_COUNT.
RETRIEVAL_QUERY = "top line sales growth"


def _quote_text(index: int) -> str:
    return f"Top line sales growth widened sequentially in outlook note {index + 1}."


def _span(object_id: str, index: int) -> dict:
    return {
        "id": object_id,
        "type": "SourceSpan",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "section_name": "item7",
        "section_key": "item7",
        "span_index": index + 1,
        "text": _quote_text(index),
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }


def _quote(object_id: str, span_id: str, index: int) -> dict:
    return {
        "id": object_id,
        "type": "EvidenceQuote",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "source_span_id": span_id,
        "quote_text": _quote_text(index),
        "quote_type": "business_update",
        "section_name": "item7",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }


def _metric_observation(object_id: str, *, year: int, value: float) -> dict:
    return {
        "id": object_id,
        "type": "MetricObservation",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "metric_name": "revenue",
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

    claim = None
    support_link = None

    write_jsonl(
        ontology_dir / "spans.jsonl",
        [_span(span_id, index) for index, span_id in enumerate(SPAN_IDS)],
    )
    write_jsonl(
        ontology_dir / "evidence_quotes.jsonl",
        [
            _quote(quote_id, span_id, index)
            for index, (quote_id, span_id) in enumerate(zip(QUOTE_IDS, SPAN_IDS))
        ],
    )
    write_jsonl(ontology_dir / "claims.jsonl", [])
    write_jsonl(ontology_dir / "support_links.jsonl", [])
    write_jsonl(
        ontology_dir / "metric_observations.jsonl",
        [
            _metric_observation(REVENUE_CY2022_ID, year=2022, value=100.0),
            _metric_observation(REVENUE_CY2023_ID, year=2023, value=125.0),
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
def shard_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("period-equity-release")
    _write_minimal_artifacts(root)
    result = build_spine_shard_release_outputs(
        root,
        release_id="test-store-period-equity",
        workers=1,
        no_cache=True,
    )
    shard = result.global_spine_path.parent / "companies" / f"{TICKER}.sqlite"
    assert shard.is_file()
    return shard


def _query(store: OntologyStore, **overrides):
    kwargs = {
        "clause_id": "rev_growth",
        "retrieval_query": RETRIEVAL_QUERY,
        "tickers": [TICKER],
        "limit": 6,
    }
    kwargs.update(overrides)
    return store.query_planned_compact_with_diagnostics(**kwargs)


def _metric_rows(rows):
    return [row for row in rows if row["type"] == "MetricObservation"]


def _metric_periods(rows):
    return {row["period"] for row in _metric_rows(rows)}


def test_starved_window_keeps_both_requested_periods_metric_units(shard_path):
    """Six qualitative rows fill the window, so the alias metric channel would
    otherwise append nothing and BOTH requested periods lose their metric
    observations (the multi_period starvation)."""
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store, periods=["CY2022", "CY2023"])

    assert diagnostics["fts_strict_result_count"] == QUOTE_COUNT
    assert _metric_periods(rows) == {"CY2022", "CY2023"}
    assert len(rows) == 6
    reserved_metric_ids = {row["id"] for row in _metric_rows(rows)}
    assert reserved_metric_ids == METRIC_IDS
    # Reservations are recorded per explicitly requested period.
    assert diagnostics["period_reservations"] == [
        {"period": "CY2022", "reserved": 1},
        {"period": "CY2023", "reserved": 1},
    ]


def test_reservation_displaces_only_lowest_ranked_qualitative_rows(shard_path):
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store, periods=["CY2022", "CY2023"])

    metric_positions = [index for index, row in enumerate(rows) if row["id"] in METRIC_IDS]
    assert metric_positions == [0, 1]
    kept_quotes = [row for row in rows if row["type"] == "EvidenceQuote"]
    assert len(kept_quotes) == QUOTE_COUNT - 2
    by_id = {row["id"]: row for row in rows}
    assert {by_id[mid]["planned_match_mode"] for mid in METRIC_IDS} == {"alias_expanded"}
    assert by_id[REVENUE_CY2022_ID]["object"]["observation_period"] == "CY2022"
    assert by_id[REVENUE_CY2023_ID]["object"]["observation_period"] == "CY2023"
    assert diagnostics["result_count"] == 6


def test_partial_starvation_reserves_only_the_missing_period(shard_path):
    """With one free slot the old fusion appended only the rank-first (latest)
    metric row; the older requested period stays starved until reserved."""
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store, periods=["CY2022", "CY2023"], limit=7)

    assert _metric_periods(rows) == {"CY2022", "CY2023"}
    assert len(rows) == 7
    assert diagnostics["period_reservations"] == [{"period": "CY2022", "reserved": 1}]


def test_single_period_clause_is_unchanged_and_unreserved(shard_path):
    """A single-period clause keeps the pre-reservation behaviour exactly:
    six strict qualitative rows plus the one alias metric unit that fits."""
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store, periods=["CY2023"], limit=7)

    assert len(rows) == 7
    assert {row["id"] for row in _metric_rows(rows)} == {REVENUE_CY2023_ID}
    by_id = {row["id"]: row for row in rows}
    assert by_id[REVENUE_CY2023_ID]["planned_match_mode"] == "alias_expanded"
    assert "period_reservations" not in diagnostics


def test_period_without_metric_rows_reserves_nothing(shard_path):
    """CY2021 has no metric units, so only one requested period carries metric
    rows, the reservation trigger never fires, and nothing errors."""
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store, periods=["CY2023", "CY2021"], limit=7)

    assert {row["id"] for row in _metric_rows(rows)} == {REVENUE_CY2023_ID}
    assert len(rows) == 7
    assert "period_reservations" not in diagnostics


def test_wide_limit_holds_everything_and_never_reserves(shard_path):
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store, periods=["CY2022", "CY2023"], limit=8)

    assert len(rows) == 8
    assert {row["id"] for row in _metric_rows(rows)} == METRIC_IDS
    assert "period_reservations" not in diagnostics


# ---------------------------------------------------------------------------
# Value-identity dedupe vs the requested filing bucket (the msft_bottom_line
# recovery).  Two value-identical twins observe FY2025: one filed in the
# CY2025 bucket, its comparative copy filed in the CY2026 bucket.  They share
# observation_context_key and value_identity, so the dedupe window inside
# ``_query_metrics`` keeps exactly one of them.  Plain ``filing_period DESC``
# always keeps the newest filing's comparative copy; a clause that requests a
# period must instead keep the twin filed in that period's bucket.
#
# The retrieval query below deliberately does not lexically match the metric
# observation text, so the fused window carries metric-channel rows only and
# the assertions observe the dedupe decision itself.
# ---------------------------------------------------------------------------

TWINS_TICKER = "TW"
TWINS_FILING_PERIODS = ("CY2025", "CY2026")
TWINS_OBSERVATION_YEAR = 2025
TWINS_METRIC_NAME = "net_income"
TWINS_METRIC_VALUE = 101_832_000_000.0
# No FTS row in the twins shard matches every token of this query.
TWINS_RETRIEVAL_QUERY = "fiscal bottom line commentary"

TWINS_METRIC_IDS = {
    "metric_observation:TW:CY2025:10K:net_income:2025",
    "metric_observation:TW:CY2026:10K:net_income:2025",
}
TWINS_FILED_IN_CY2025_ID = "metric_observation:TW:CY2025:10K:net_income:2025"
TWINS_FILED_IN_CY2026_ID = "metric_observation:TW:CY2026:10K:net_income:2025"


def _twins_metric_observation(*, filing_period: str) -> dict:
    """One twin: same FY2025 observation (dates, value, context) filed in ``filing_period``."""
    return {
        "id": f"metric_observation:TW:{filing_period}:10K:net_income:2025",
        "type": "MetricObservation",
        "ticker": TWINS_TICKER,
        "source_document_id": f"source:TW:{filing_period}:10K",
        "document_type": DOCUMENT_TYPE,
        "period": filing_period,
        "metric_name": TWINS_METRIC_NAME,
        "value": TWINS_METRIC_VALUE,
        "unit": "USD",
        "fiscal_year": TWINS_OBSERVATION_YEAR,
        "period_type": "annual",
        "period_start": f"{TWINS_OBSERVATION_YEAR}-01-01",
        "period_end": f"{TWINS_OBSERVATION_YEAR}-12-31",
        "source_type": "reported",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }


def _write_twins_artifacts(root: Path) -> None:
    for filing_period in TWINS_FILING_PERIODS:
        ontology_dir = root / "companies" / TWINS_TICKER / "ontology" / DOC_TYPE_KEY / filing_period
        sources_dir = root / "companies" / TWINS_TICKER / "sources" / DOC_TYPE_KEY / filing_period
        ontology_dir.mkdir(parents=True)
        sources_dir.mkdir(parents=True)
        write_jsonl(ontology_dir / "spans.jsonl", [])
        write_jsonl(ontology_dir / "evidence_quotes.jsonl", [])
        write_jsonl(ontology_dir / "claims.jsonl", [])
        write_jsonl(ontology_dir / "support_links.jsonl", [])
        write_jsonl(
            ontology_dir / "metric_observations.jsonl",
            [_twins_metric_observation(filing_period=filing_period)],
        )
        atomic_write_json(
            ontology_dir / "section_quality.json",
            {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        )
        build_indexes(
            ticker=TWINS_TICKER,
            period=filing_period,
            doc_type_key=DOC_TYPE_KEY,
            ontology_dir=ontology_dir,
            sources_dir=sources_dir,
            output_dir=root,
            document_type=DOCUMENT_TYPE,
        )


@pytest.fixture(scope="module")
def twins_shard_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("twins-dedupe-release")
    _write_twins_artifacts(root)
    result = build_spine_shard_release_outputs(
        root,
        release_id="test-store-twins-dedupe",
        workers=1,
        no_cache=True,
    )
    shard = result.global_spine_path.parent / "companies" / f"{TWINS_TICKER}.sqlite"
    assert shard.is_file()
    return shard


def _twins_query(store: OntologyStore, **overrides):
    kwargs = {
        "clause_id": "twins_net_income",
        "retrieval_query": TWINS_RETRIEVAL_QUERY,
        "metrics": [TWINS_METRIC_NAME],
        "tickers": [TWINS_TICKER],
        "limit": 6,
    }
    kwargs.update(overrides)
    return store.query_planned_compact_with_diagnostics(**kwargs)


def _twins_metric_ids(rows):
    return {row["id"] for row in rows if row["type"] == "MetricObservation"}


def test_dedupe_prefers_requested_filing_bucket_twin(twins_shard_path):
    """periods=[CY2025]: the dedupe survivor must be the twin filed in the
    requested CY2025 bucket, not the CY2026 comparative copy that plain
    ``filing_period DESC`` always picks."""
    with OntologyStore(twins_shard_path) as store:
        rows, diagnostics = _twins_query(store, periods=["CY2025"])

    assert _twins_metric_ids(rows) == {TWINS_FILED_IN_CY2025_ID}
    assert diagnostics["metric_result_count"] == 1


def test_dedupe_without_requested_periods_keeps_newest_twin(twins_shard_path):
    """No requested periods: the dedupe keeps the newest filing bucket,
    byte-identical to the pre-change behaviour."""
    with OntologyStore(twins_shard_path) as store:
        rows, diagnostics = _twins_query(store)

    assert _twins_metric_ids(rows) == {TWINS_FILED_IN_CY2026_ID}
    assert diagnostics["metric_result_count"] == 1


def test_dedupe_fy_request_prefers_matching_cy_filing_bucket(twins_shard_path):
    """A fiscal-labeled request (FY2025) resolves through the fiscal-coordinate
    equivalence to the CY2025 filing bucket, so the CY2025 twin wins over the
    CY2026 comparative copy."""
    with OntologyStore(twins_shard_path) as store:
        rows, diagnostics = _twins_query(store, periods=["FY2025"])

    assert _twins_metric_ids(rows) == {TWINS_FILED_IN_CY2025_ID}
    assert diagnostics["metric_result_count"] == 1


def test_alias_channel_dedupe_prefers_requested_filing_bucket_twin(twins_shard_path):
    """The msft_bottom_line shape itself: a metric-less clause whose retrieval
    query names the ``bottom line`` alias expands to net_income through the
    alias metric channel, which shares ``_query_metrics`` and therefore the
    same requested-bucket preference."""
    with OntologyStore(twins_shard_path) as store:
        rows, diagnostics = _twins_query(
            store,
            retrieval_query="bottom line",
            metrics=None,
            periods=["CY2025"],
        )

    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == {TWINS_FILED_IN_CY2025_ID}
    assert by_id[TWINS_FILED_IN_CY2025_ID]["planned_match_mode"] == "alias_expanded"
    assert diagnostics["alias_expansion_used"] is True
