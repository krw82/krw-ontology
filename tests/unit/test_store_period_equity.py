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
# CY2025 is the own-filing bucket (filing bucket == CY-coordinate twin of the
# observed FY2025); CY2026 is an intermediate comparative bucket; CY2027 is
# the newest restating bucket outside any requested preference group below.
TWINS_FILING_PERIODS = ("CY2025", "CY2026", "CY2027")
TWINS_OBSERVATION_YEAR = 2025
TWINS_METRIC_NAME = "net_income"
TWINS_METRIC_VALUE = 101_832_000_000.0
# No FTS row in the twins shard matches every token of this query.
TWINS_RETRIEVAL_QUERY = "fiscal bottom line commentary"

TWINS_METRIC_IDS = {
    "metric_observation:TW:CY2025:10K:net_income:2025",
    "metric_observation:TW:CY2026:10K:net_income:2025",
    "metric_observation:TW:CY2027:10K:net_income:2025",
}
TWINS_FILED_IN_CY2025_ID = "metric_observation:TW:CY2025:10K:net_income:2025"
TWINS_FILED_IN_CY2026_ID = "metric_observation:TW:CY2026:10K:net_income:2025"
TWINS_FILED_IN_CY2027_ID = "metric_observation:TW:CY2027:10K:net_income:2025"


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
    """No requested periods: the dedupe keeps the newest filing bucket (CY2027
    after the fixture extension), byte-identical to the pre-change behaviour."""
    with OntologyStore(twins_shard_path) as store:
        rows, diagnostics = _twins_query(store)

    assert _twins_metric_ids(rows) == {TWINS_FILED_IN_CY2027_ID}
    assert diagnostics["metric_result_count"] == 1


def test_dedupe_prefers_own_filing_twin_over_newer_preferred_comparative(twins_shard_path):
    """The msft multi_period shape: the request spans the own-filing bucket
    (CY2025) AND one comparative bucket (CY2026), so both twins sit inside the
    requested-bucket preference group.  Within that group the survivor must be
    the own-filing row — the row whose filing bucket equals the CY-coordinate
    twin of its own observed FY2025 — not the newer CY2026 comparative copy
    that plain ``filing_period DESC`` keeps; the newest twin (CY2027, outside
    the preference group) never competes."""
    with OntologyStore(twins_shard_path) as store:
        rows, diagnostics = _twins_query(store, periods=["CY2025", "CY2026"])

    assert _twins_metric_ids(rows) == {TWINS_FILED_IN_CY2025_ID}
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


# ---------------------------------------------------------------------------
# Requested-filing-bucket contexts survive the metric bundle slice (the RMD
# shareholders_equity shape).  June-FY companies key their 10-K filings by
# calendar bucket while instant balance-sheet observations derive an
# observation_period equal to the FILING bucket, so the same FY observation
# restated across filings carries three different observation labels and three
# different observation_context_keys.  The value-identity dedupe can therefore
# never collapse the twins, and inside ``_select_planned_metric_rows`` the
# per-series context ordering ``(fiscal_year, fiscal_quarter,
# observation_period) DESC`` ranks the mislabeled newer-filing comparatives
# above the requested bucket's own filing — ``representatives[:2]`` then cuts
# the own-filing rows out of the window entirely.  Aligning the context
# ordering with the requested filing bucket (the same partition the compiler
# applies downstream) keeps the own-filing context inside the slice.
# ---------------------------------------------------------------------------

RELEASE_SHAPE_TICKER = "RC"
RELEASE_SHAPE_METRIC = "shareholders_equity"
RELEASE_SHAPE_FILING_PERIODS = ("CY2024", "CY2025", "CY2026")
RELEASE_SHAPE_FISCAL_YEAR = 2024
RELEASE_SHAPE_VALUE = 4_864_043_000.0
# No FTS row matches every token of this query.
RELEASE_SHAPE_RETRIEVAL_QUERY = "balance sheet equity commentary"

RELEASE_SHAPE_OWN_FILING_ID = f"metric_observation:RC:CY2024:10K:shareholders_equity"


def _release_shape_observation(filing_period: str) -> dict:
    """One restatement twin: the same FY2024 instant filed in ``filing_period``."""
    return {
        "id": f"metric_observation:RC:{filing_period}:10K:shareholders_equity",
        "type": "MetricObservation",
        "ticker": RELEASE_SHAPE_TICKER,
        "source_document_id": f"source:RC:{filing_period}:10K",
        "document_type": DOCUMENT_TYPE,
        "period": filing_period,
        "metric_name": RELEASE_SHAPE_METRIC,
        "value": RELEASE_SHAPE_VALUE,
        "unit": "USD",
        "fiscal_year": RELEASE_SHAPE_FISCAL_YEAR,
        "period_type": "instant",
        "period_end": "2024-06-30",
        "source_type": "reported",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }


FALLBACK_TICKER = "NK"
FALLBACK_METRIC = "total_debt"
FALLBACK_FILING_PERIOD = "CY2026Q1"
# No FTS row matches every token of this query.
FALLBACK_RETRIEVAL_QUERY = "debt maturity wall commentary"

FALLBACK_METRIC_ID = f"metric_observation:NK:CY2026Q1:10Q:total_debt"


def _fallback_observation() -> dict:
    """The NKE shape: the only metric observation filed in the CY2026Q1 10-Q
    observes the prior fiscal year-end instant (2025-05-31), so its derived
    observation_period is CY2025Q2 with fiscal coordinates (2025, Q1) and the
    observation-semantics period clause can never match a CY2026Q1 request."""
    return {
        "id": FALLBACK_METRIC_ID,
        "type": "MetricObservation",
        "ticker": FALLBACK_TICKER,
        "source_document_id": f"source:NK:{FALLBACK_FILING_PERIOD}:10Q",
        "document_type": "10-Q",
        "period": FALLBACK_FILING_PERIOD,
        "metric_name": FALLBACK_METRIC,
        "value": 5_000_000.0,
        "unit": "USD",
        "fiscal_year": 2025,
        "period_type": "instant",
        "period_end": "2025-05-31",
        "source_type": "reported",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }


def _write_observation_only_document(
    root: Path,
    *,
    ticker: str,
    doc_type_key: str,
    document_type: str,
    filing_period: str,
    observation: dict,
) -> None:
    ontology_dir = root / "companies" / ticker / "ontology" / doc_type_key / filing_period
    sources_dir = root / "companies" / ticker / "sources" / doc_type_key / filing_period
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)
    write_jsonl(ontology_dir / "spans.jsonl", [])
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", [])
    write_jsonl(ontology_dir / "claims.jsonl", [])
    write_jsonl(ontology_dir / "support_links.jsonl", [])
    write_jsonl(ontology_dir / "metric_observations.jsonl", [observation])
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    build_indexes(
        ticker=ticker,
        period=filing_period,
        doc_type_key=doc_type_key,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=root,
        document_type=document_type,
    )


@pytest.fixture(scope="module")
def release_shape_shard_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("release-shape-release")
    for filing_period in RELEASE_SHAPE_FILING_PERIODS:
        _write_observation_only_document(
            root,
            ticker=RELEASE_SHAPE_TICKER,
            doc_type_key=DOC_TYPE_KEY,
            document_type=DOCUMENT_TYPE,
            filing_period=filing_period,
            observation=_release_shape_observation(filing_period),
        )
    result = build_spine_shard_release_outputs(
        root,
        release_id="test-store-release-shape",
        workers=1,
        no_cache=True,
    )
    shard = result.global_spine_path.parent / "companies" / f"{RELEASE_SHAPE_TICKER}.sqlite"
    assert shard.is_file()
    return shard


@pytest.fixture(scope="module")
def fallback_shard_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("filing-bucket-fallback-release")
    _write_observation_only_document(
        root,
        ticker=FALLBACK_TICKER,
        doc_type_key="10Q",
        document_type="10-Q",
        filing_period=FALLBACK_FILING_PERIOD,
        observation=_fallback_observation(),
    )
    result = build_spine_shard_release_outputs(
        root,
        release_id="test-store-filing-bucket-fallback",
        workers=1,
        no_cache=True,
    )
    shard = result.global_spine_path.parent / "companies" / f"{FALLBACK_TICKER}.sqlite"
    assert shard.is_file()
    return shard


def test_release_shape_fixture_carries_filing_bucket_observation_labels(release_shape_shard_path):
    """Guard the fixture: instant observations in annual filing buckets derive
    observation_period == filing bucket (the release-data mislabel the fix
    must tolerate), so the restatement twins hold distinct contexts."""
    import sqlite3

    with sqlite3.connect(f"file:{release_shape_shard_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT filing_period, observation_period, fiscal_year, fiscal_quarter "
            "FROM metric_lookup WHERE ticker = ?",
            (RELEASE_SHAPE_TICKER,),
        ).fetchall()
    assert {row[0] for row in rows} == set(RELEASE_SHAPE_FILING_PERIODS)
    for filing_period, observation_period, fiscal_year, fiscal_quarter in rows:
        assert observation_period == filing_period
        assert fiscal_year == RELEASE_SHAPE_FISCAL_YEAR
        assert fiscal_quarter is None


def test_requested_bucket_own_filing_context_survives_the_bundle_slice(release_shape_shard_path):
    """periods=[CY2024]: the metric window must keep the CY2024 filing's own
    row; the mislabeled CY2026/CY2025 comparative twins must not displace it
    from the per-series representative slice."""
    with OntologyStore(release_shape_shard_path) as store:
        rows, diagnostics = store.query_planned_compact_with_diagnostics(
            clause_id="rc_equity",
            retrieval_query=RELEASE_SHAPE_RETRIEVAL_QUERY,
            metrics=[RELEASE_SHAPE_METRIC],
            tickers=[RELEASE_SHAPE_TICKER],
            document_types=[DOCUMENT_TYPE],
            periods=["CY2024"],
            limit=6,
        )

    metric_ids = {row["id"] for row in rows if row["type"] == "MetricObservation"}
    assert RELEASE_SHAPE_OWN_FILING_ID in metric_ids
    assert diagnostics["metric_result_count"] >= 1


def test_no_requested_periods_keeps_pre_change_release_shape_window(release_shape_shard_path):
    """Without requested periods the behaviour is unchanged: the per-series
    representative slice keeps the two newest-labelled contexts (CY2026 and
    CY2025 filing buckets), never the requested-bucket alignment."""
    with OntologyStore(release_shape_shard_path) as store:
        rows, _diagnostics = store.query_planned_compact_with_diagnostics(
            clause_id="rc_equity",
            retrieval_query=RELEASE_SHAPE_RETRIEVAL_QUERY,
            metrics=[RELEASE_SHAPE_METRIC],
            tickers=[RELEASE_SHAPE_TICKER],
            limit=6,
        )

    metric_ids = {row["id"] for row in rows if row["type"] == "MetricObservation"}
    assert metric_ids == {
        "metric_observation:RC:CY2026:10K:shareholders_equity",
        "metric_observation:RC:CY2025:10K:shareholders_equity",
    }


def test_fallback_fixture_observation_never_matches_requested_period(fallback_shard_path):
    """Guard the fixture: the CY2026Q1 filing's observation carries CY2025Q2
    with fiscal coordinates (2025, 1), so the observation-semantics clause for
    CY2026Q1 cannot match it."""
    import sqlite3

    with sqlite3.connect(f"file:{fallback_shard_path}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT observation_period, fiscal_year, fiscal_quarter FROM metric_lookup "
            "WHERE ticker = ?",
            (FALLBACK_TICKER,),
        ).fetchone()
    assert row == ("CY2025Q2", 2025, 1)


def test_empty_metric_window_falls_back_to_requested_filing_bucket(fallback_shard_path):
    """periods=[CY2026Q1]: the observation-semantics window is empty, so the
    filing-bucket fallback must surface the metric observation filed in the
    requested bucket, with its true observation period exposed on the unit."""
    with OntologyStore(fallback_shard_path) as store:
        rows, diagnostics = store.query_planned_compact_with_diagnostics(
            clause_id="nk_debt",
            retrieval_query=FALLBACK_RETRIEVAL_QUERY,
            metrics=[FALLBACK_METRIC],
            tickers=[FALLBACK_TICKER],
            document_types=["10-Q"],
            periods=[FALLBACK_FILING_PERIOD],
            limit=6,
        )

    by_id = {row["id"]: row for row in rows}
    assert FALLBACK_METRIC_ID in by_id
    assert by_id[FALLBACK_METRIC_ID]["period"] == "CY2025Q2"
    assert by_id[FALLBACK_METRIC_ID]["object"]["filing_period"] == FALLBACK_FILING_PERIOD
    assert diagnostics["metric_result_count"] == 1


def test_observation_matched_window_skips_the_filing_bucket_fallback(fallback_shard_path):
    """A request the observation semantics already answers (CY2025Q2) returns
    the same rows with or without the fallback: it only fires on an empty
    observation-semantics window."""
    with OntologyStore(fallback_shard_path) as store:
        rows, diagnostics = store.query_planned_compact_with_diagnostics(
            clause_id="nk_debt",
            retrieval_query=FALLBACK_RETRIEVAL_QUERY,
            metrics=[FALLBACK_METRIC],
            tickers=[FALLBACK_TICKER],
            document_types=["10-Q"],
            periods=["CY2025Q2"],
            limit=6,
        )

    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == {FALLBACK_METRIC_ID}
    assert diagnostics["metric_result_count"] == 1
