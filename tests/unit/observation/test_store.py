"""Unit tests for the observations.sqlite store, builder, and verifier (B3).

Fixture-only (no network): fetch results are constructed in memory exactly
like the B1 adapters produce them, then materialized by the pure builder.
Observation values are advisory_only research context; served payloads must
carry no vendor names.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from krw_ontology.observation.builder import build_observations_store
from krw_ontology.observation.ports import (
    RawObservation,
    SeriesFetchResult,
)
from krw_ontology.observation.seed import load_series_seed
from krw_ontology.observation.store import (
    MARKET_SERIES_FORMAT,
    MACRO_SERIES_FORMAT,
    MAX_MARKET_PERIODS,
    OBSERVATIONS_SCHEMA_VERSION,
    ObservationsStore,
    verify_observations_schema,
)

VENDOR_NAMES = ("fred", "fmp", "polygon")


def _seed() -> dict[str, Any]:
    return load_series_seed()


def _seed_subset() -> dict[str, Any]:
    """3 FRED macro families + the per-ticker price family (small e2e seed)."""

    seed = _seed()
    keys = ["macro_cpi_yoy", "macro_core_cpi_yoy", "macro_unemployment_rate", "price_close"]
    return {key: seed[key] for key in keys}


def _macro_result(
    series_key: str,
    rows: list[tuple[str, float, str | None, str | None]],
    *,
    status: str = "available",
    provider: str = "fred",
) -> SeriesFetchResult:
    return SeriesFetchResult(
        series_key=series_key,
        provider=provider,
        observations=tuple(
            RawObservation(
                series_key=series_key,
                phenomenon_time=phenomenon_time,
                value=value,
                result_time=result_time,
                vintage=vintage,
                provenance={
                    "endpoint": "series/observations",
                    "params_hash": "fixture000000",
                    "fetched_at": "2026-08-30T00:00:00Z",
                },
            )
            for phenomenon_time, value, result_time, vintage in rows
        ),
        status=status,
    )


def vintage_fetch_result(phenomenon_time: str, *, old: float, new: float) -> SeriesFetchResult:
    """Two vintages of one CPI period: first release then one revision."""

    return _macro_result(
        "macro_cpi_yoy",
        [
            (phenomenon_time, old, "2026-06-10", "2026-06-10"),
            (phenomenon_time, new, "2026-07-15", "2026-07-15"),
        ],
    )


def _price_result(
    ticker: str,
    rows: list[tuple[str, float]],
    *,
    family: str = "price_close",
    status: str = "available",
    provenance_extra: dict[str, Any] | None = None,
) -> SeriesFetchResult:
    series_key = f"{family}|{ticker}"
    provenance = {
        "endpoint": "historical-price-full",
        "params_hash": "fixture000000",
        "fetched_at": "2026-08-30T00:00:00Z",
    }
    provenance.update(provenance_extra or {})
    return SeriesFetchResult(
        series_key=series_key,
        provider="fmp",
        observations=tuple(
            RawObservation(
                series_key=series_key,
                phenomenon_time=phenomenon_time,
                value=value,
                result_time=None,
                vintage=None,
                provenance=dict(provenance),
            )
            for phenomenon_time, value in rows
        ),
        status=status,
    )


def build_store_from_results(
    tmp_path: Path,
    results: list[SeriesFetchResult],
    *,
    seed: dict[str, Any] | None = None,
) -> ObservationsStore:
    target = tmp_path / "indexes" / "observations.sqlite"
    build = build_observations_store(target, seed or _seed_subset(), results)
    assert build.verification.get("ok") is True
    return ObservationsStore(target)


# ---------------------------------------------------------------------------
# Step 1: latest vintage wins + supersede edge (brief semantics, verbatim)
# ---------------------------------------------------------------------------


def test_latest_vintage_wins_and_supersede_edge_exists(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path, [vintage_fetch_result("2026-05-01", old=296.1, new=296.4)]
    )
    latest = store.latest_observation("macro_cpi_yoy", "2026-05-01")
    assert latest is not None
    assert latest.value == 296.4
    revisions = store.revisions_for("macro_cpi_yoy", "2026-05-01")
    assert len(revisions) == 1 and revisions[0].revision_kind == "revision"
    assert revisions[0].new_observation_id == latest.observation_id
    assert revisions[0].prior_observation_id != latest.observation_id


def test_first_vintage_is_retained_history(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path, [vintage_fetch_result("2026-05-01", old=296.1, new=296.4)]
    )
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute(
            "SELECT vintage, value FROM observations WHERE series_key = ? ORDER BY vintage",
            ("macro_cpi_yoy",),
        ).fetchall()
    assert rows == [("2026-06-10", 296.1), ("2026-07-15", 296.4)]


# ---------------------------------------------------------------------------
# Unavailable fetch results: status only, never rows
# ---------------------------------------------------------------------------


def test_unavailable_fetch_results_produce_no_rows_and_no_error(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path,
        [
            _macro_result("macro_cpi_yoy", [], status="unavailable"),
            _price_result("AAPL", [], status="unavailable"),
        ],
    )
    with sqlite3.connect(store.path) as conn:
        observation_rows = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        catalog_rows = conn.execute(
            "SELECT series_key, status, observation_count FROM series_catalog"
        ).fetchall()
    assert observation_rows == 0
    catalog = {row[0]: (row[1], row[2]) for row in catalog_rows}
    assert catalog["macro_cpi_yoy"] == ("unavailable", 0)
    assert catalog["price_close|AAPL"] == ("unavailable", 0)
    payload = store.query_macro_series("cpi_yoy", limit=24)
    assert payload["status"] == "unavailable"
    assert payload["points"] == []


# ---------------------------------------------------------------------------
# NULL vintage PK sentinel
# ---------------------------------------------------------------------------


def test_null_vintage_uses_pk_sentinel(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path,
        [
            _price_result(
                "MSFT",
                [("2026-06-24", 480.2), ("2026-06-25", 482.9)],
            )
        ],
    )
    with sqlite3.connect(store.path) as conn:
        vintages = conn.execute(
            "SELECT phenomenon_time, vintage FROM observations ORDER BY phenomenon_time"
        ).fetchall()
        null_pk_rows = conn.execute(
            "SELECT COUNT(*) FROM observations WHERE vintage IS NULL OR vintage = ''"
        ).fetchone()[0]
    assert vintages == [("2026-06-24", ""), ("2026-06-25", "")]
    assert null_pk_rows == 2
    latest = store.latest_observation("price_close|MSFT", "2026-06-25")
    assert latest is not None
    assert latest.value == 482.9
    assert latest.vintage is None
    assert store.revisions_for("price_close|MSFT", "2026-06-25") == []


# ---------------------------------------------------------------------------
# OHLCV table roundtrip
# ---------------------------------------------------------------------------


def test_ohlcv_observations_roundtrip(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path,
        [
            _price_result(
                "AAPL",
                [("2026-06-25", 201.5)],
                provenance_extra={
                    "open": 200.1,
                    "high": 202.3,
                    "low": 199.8,
                    "volume": 54_321_000.0,
                },
            )
        ],
    )
    with sqlite3.connect(store.path) as conn:
        row = conn.execute(
            """
            SELECT ticker, trade_date, open, high, low, close, volume, series_key
            FROM ohlcv_observations
            """
        ).fetchone()
    assert row == (
        "AAPL",
        "2026-06-25",
        200.1,
        202.3,
        199.8,
        201.5,
        54_321_000.0,
        "price_close|AAPL",
    )


def test_ohlcv_latest_vintage_close(tmp_path: Path) -> None:
    """Superseded closes never reach the OHLCV current view."""

    series_key = "price_close|AAPL"
    rows = [
        RawObservation(series_key, "2026-06-25", 201.5, "2026-06-26", "2026-06-26", {}),
        RawObservation(series_key, "2026-06-25", 201.9, "2026-06-27", "2026-06-27", {}),
    ]
    result = SeriesFetchResult(
        series_key=series_key, provider="fmp", observations=tuple(rows), status="available"
    )
    store = build_store_from_results(tmp_path, [result])
    with sqlite3.connect(store.path) as conn:
        close = conn.execute(
            "SELECT close FROM ohlcv_observations WHERE ticker = 'AAPL'"
        ).fetchone()[0]
    assert close == 201.9


# ---------------------------------------------------------------------------
# release_events
# ---------------------------------------------------------------------------


def test_release_events_row_per_first_publication(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path,
        [
            vintage_fetch_result("2026-05-01", old=296.1, new=296.4),
            _macro_result(
                "macro_unemployment_rate", [("2026-05-01", 4.2, "2026-06-05", "2026-06-05")]
            ),
        ],
    )
    with sqlite3.connect(store.path) as conn:
        rows = {
            row[0]: row[1:]
            for row in conn.execute(
                """
                SELECT series_key, release_time, latest_time, first_value,
                       latest_value, revision_count
                FROM release_events
                """
            ).fetchall()
        }
    assert rows["macro_cpi_yoy"] == (
        "2026-06-10",
        "2026-07-15",
        296.1,
        296.4,
        1,
    )
    assert rows["macro_unemployment_rate"] == (
        "2026-06-05",
        "2026-06-05",
        4.2,
        4.2,
        0,
    )


def test_query_recent_releases_factor_filter(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path,
        [
            vintage_fetch_result("2026-05-01", old=296.1, new=296.4),
            _macro_result(
                "macro_unemployment_rate", [("2026-05-01", 4.2, "2026-06-05", "2026-06-05")]
            ),
        ],
    )
    inflation = store.query_recent_releases(factor="inflation", limit=10)
    assert inflation["format"] == "release-events/v1"
    assert inflation["advisory_only"] is True
    assert inflation["source_usage"] == "research_only"
    assert [event["series_key"] for event in inflation["releases"]] == ["macro_cpi_yoy"]
    event = inflation["releases"][0]
    assert event["release_time"] == "2026-06-10"
    assert event["latest_value"] == 296.4
    assert event["revision_count"] == 1

    labor = store.query_recent_releases(factor="labor", limit=10)
    assert [event["series_key"] for event in labor["releases"]] == ["macro_unemployment_rate"]
    assert store.query_recent_releases(factor="housing", limit=10)["releases"] == []


# ---------------------------------------------------------------------------
# Query payloads: shapes + vendor scrub
# ---------------------------------------------------------------------------


def test_query_market_series_payload_shape_and_vendor_scrub(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path,
        [_price_result("AAPL", [("2026-06-24", 201.2), ("2026-06-25", 201.5)])],
    )
    payload = store.query_market_series("AAPL", "last_price", periods=10)
    assert payload["format"] == MARKET_SERIES_FORMAT
    assert payload["ticker"] == "AAPL"
    assert payload["canonical_metric"] == "last_price"
    assert payload["unit"] == "USD_per_share"
    assert payload["currency"] == "USD"
    assert payload["status"] == "available"
    assert payload["source_usage"] == "research_only"
    assert payload["advisory_only"] is True
    assert payload["as_of"] == "2026-06-25"
    assert payload["fetched_at"]
    assert [point["date"] for point in payload["points"]] == ["2026-06-24", "2026-06-25"]
    assert payload["points"][-1]["value"] == 201.5
    assert payload["points"][-1]["observation_id"]
    rendered = json.dumps(payload)
    for vendor in VENDOR_NAMES:
        assert vendor not in rendered


def test_query_macro_series_payload_shape_and_vendor_scrub(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path,
        [vintage_fetch_result("2026-05-01", old=296.1, new=296.4)],
    )
    payload = store.query_macro_series("cpi_yoy", limit=12)
    assert payload["format"] == MACRO_SERIES_FORMAT
    assert payload["series_key"] == "macro_cpi_yoy"
    assert payload["canonical_metric"] == "cpi_yoy"
    assert payload["unit"] == "percent"
    assert payload["frequency"] == "monthly"
    assert payload["factor"] == "inflation"
    assert payload["status"] == "available"
    assert payload["source_usage"] == "research_only"
    assert payload["advisory_only"] is True
    assert payload["as_of"] == "2026-05-01"
    assert payload["points"][0]["value"] == 296.4  # latest vintage wins
    rendered = json.dumps(payload)
    for vendor in VENDOR_NAMES:
        assert vendor not in rendered


def test_query_macro_series_unknown_metric_is_no_data(tmp_path: Path) -> None:
    store = build_store_from_results(tmp_path, [])
    payload = store.query_macro_series("no_such_metric", limit=12)
    assert payload["status"] == "no_data"
    assert payload["points"] == []
    assert payload["advisory_only"] is True
    # One stable shape: the no-data payload keeps every populated-payload key.
    assert payload["series_key"] is None
    assert payload["ticker"] is None
    assert payload["unit"] is None
    assert payload["currency"] is None
    assert payload["frequency"] is None
    assert payload["factor"] is None


def test_query_market_series_periods_bound(tmp_path: Path) -> None:
    rows = [(f"2025-{month:02d}-01", 100.0 + month) for month in range(1, 13)]
    rows += [(f"2026-{month:02d}-01", 200.0 + month) for month in range(1, 13)]
    store = build_store_from_results(tmp_path, [_price_result("AAPL", rows)])
    bounded = store.query_market_series("AAPL", "last_price", periods=10_000)
    assert len(bounded["points"]) == 24  # fewer points than the bound stays as-is
    many = [
        _price_result(
            "DAILY",
            [
                (f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}", float(index))
                for index in range(MAX_MARKET_PERIODS + 40)
            ],
        )
    ]
    store_many = build_store_from_results(tmp_path, many)
    clamped = store_many.query_market_series("DAILY", "last_price", periods=10_000)
    assert len(clamped["points"]) == MAX_MARKET_PERIODS


def test_query_market_series_prefers_latest_as_of(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path,
        [_price_result("AAPL", [("2026-06-24", 201.2), ("2026-06-25", 201.5)])],
    )
    missing = store.query_market_series("MSFT", "last_price", periods=10)
    assert missing["status"] == "no_data"
    assert missing["points"] == []
    # Same stable shape as the macro no-data payload, with the requested ticker.
    assert missing["ticker"] == "MSFT"
    assert missing["unit"] is None
    assert missing["frequency"] is None


# ---------------------------------------------------------------------------
# Shared latest-vintage ordering: builder artifacts and served points agree
# ---------------------------------------------------------------------------


def _vintage_rows_result(rows: list[tuple[str, str, str, float]]) -> SeriesFetchResult:
    """(vintage, result_time, phenomenon_time, value) → one macro CPI result."""

    series_key = "macro_cpi_yoy"
    return SeriesFetchResult(
        series_key=series_key,
        provider="fred",
        observations=tuple(
            RawObservation(
                series_key=series_key,
                phenomenon_time=phenomenon_time,
                value=value,
                result_time=result_time,
                vintage=vintage,
                provenance={
                    "endpoint": "series/observations",
                    "params_hash": "fixture000000",
                    "fetched_at": "2026-08-30T00:00:00Z",
                },
            )
            for vintage, result_time, phenomenon_time, value in rows
        ),
        status="available",
    )


def test_result_time_tie_emits_exactly_one_point_per_date(tmp_path: Path) -> None:
    """Two vintages sharing the max order key must not duplicate a date."""

    store = build_store_from_results(
        tmp_path,
        [
            _vintage_rows_result(
                [
                    ("2026-06-10", "2026-07-01", "2026-05-01", 296.1),
                    ("2026-07-15", "2026-07-01", "2026-05-01", 296.4),
                ]
            )
        ],
    )
    payload = store.query_macro_series("cpi_yoy", limit=24)
    dates = [point["date"] for point in payload["points"]]
    assert dates == ["2026-05-01"]  # one row per date, never two
    # Tie broken by vintage DESC: the 2026-07-15 vintage wins.
    assert payload["points"][0]["value"] == 296.4
    latest = store.latest_observation("macro_cpi_yoy", "2026-05-01")
    assert latest is not None and latest.value == 296.4 and latest.vintage == "2026-07-15"


def test_supersede_chain_links_consecutive_vintages(tmp_path: Path) -> None:
    """A→B→C: two edges, each vintage superseding exactly its predecessor."""

    store = build_store_from_results(
        tmp_path,
        [
            _vintage_rows_result(
                [
                    ("2026-06-10", "2026-06-10", "2026-05-01", 296.1),
                    ("2026-07-15", "2026-07-15", "2026-05-01", 296.4),
                    ("2026-08-15", "2026-08-15", "2026-05-01", 296.8),
                ]
            )
        ],
    )
    with sqlite3.connect(store.path) as conn:
        chain_ids = [
            row[0]
            for row in conn.execute(
                """
                SELECT observation_id FROM observations
                WHERE series_key = 'macro_cpi_yoy' AND phenomenon_time = '2026-05-01'
                ORDER BY vintage
                """
            ).fetchall()
        ]
    assert len(chain_ids) == 3
    revisions = store.revisions_for("macro_cpi_yoy", "2026-05-01")
    assert len(revisions) == 2
    assert all(edge.revision_kind == "revision" for edge in revisions)
    by_new = {edge.new_observation_id: edge.prior_observation_id for edge in revisions}
    assert by_new == {chain_ids[1]: chain_ids[0], chain_ids[2]: chain_ids[1]}
    # The final edge crowns the same row the query layer serves as latest.
    latest = store.latest_observation("macro_cpi_yoy", "2026-05-01")
    assert latest is not None and latest.observation_id == chain_ids[2]
    assert chain_ids[2] in by_new


def test_builder_and_serving_crown_same_latest_on_divergence(tmp_path: Path) -> None:
    """Divergent (result_time, vintage) orderings must not split the crown.

    vintage 2026-06-10 was republished at result_time 2026-09-01 (value
    300.0); vintage 2026-07-15 was published at result_time 2026-07-01
    (value 296.4). Knowledge recency (result_time first) crowns the 06-10
    vintage — on BOTH the builder's artifacts and the served points.
    """

    rows = [
        ("2026-06-10", "2026-09-01", "2026-05-01", 300.0),
        ("2026-07-15", "2026-07-01", "2026-05-01", 296.4),
    ]
    store = build_store_from_results(tmp_path, [_vintage_rows_result(rows)])

    latest = store.latest_observation("macro_cpi_yoy", "2026-05-01")
    assert latest is not None
    assert latest.value == 300.0
    assert latest.result_time == "2026-09-01"
    assert latest.vintage == "2026-06-10"

    payload = store.query_macro_series("cpi_yoy", limit=24)
    assert [point["value"] for point in payload["points"]] == [300.0]

    with sqlite3.connect(store.path) as conn:
        event = conn.execute(
            """
            SELECT release_time, latest_time, first_value, latest_value
            FROM release_events
            WHERE series_key = 'macro_cpi_yoy' AND phenomenon_time = '2026-05-01'
            """
        ).fetchone()
    assert event == ("2026-07-01", "2026-09-01", 296.4, 300.0)

    # The supersede edge crowns the same row: new == the served latest.
    revisions = store.revisions_for("macro_cpi_yoy", "2026-05-01")
    assert len(revisions) == 1
    assert revisions[0].new_observation_id == latest.observation_id


# ---------------------------------------------------------------------------
# Verifier: hard failures on doctored stores
# ---------------------------------------------------------------------------


def _doctored_copy(tmp_path: Path, store: ObservationsStore, name: str) -> Path:
    doctored = tmp_path / name
    doctored.write_bytes(store.path.read_bytes())
    return doctored


def test_verifier_rejects_dropped_table(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path, [vintage_fetch_result("2026-05-01", old=296.1, new=296.4)]
    )
    doctored = _doctored_copy(tmp_path, store, "dropped.sqlite")
    with sqlite3.connect(doctored) as conn:
        conn.execute("DROP TABLE release_events")
    verification = verify_observations_schema(doctored)
    assert verification["ok"] is False
    assert "table_missing:release_events" in verification["errors"]


def test_verifier_rejects_version_mismatch(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path, [vintage_fetch_result("2026-05-01", old=296.1, new=296.4)]
    )
    doctored = _doctored_copy(tmp_path, store, "bumped.sqlite")
    with sqlite3.connect(doctored) as conn:
        conn.execute(
            "UPDATE observation_metadata SET value_json = ? WHERE key = 'schema_version'",
            (json.dumps("krw-ontology-observations/v2"),),
        )
    verification = verify_observations_schema(doctored)
    assert verification["ok"] is False
    assert "schema_version_mismatch" in verification["errors"]


def test_verifier_rejects_extra_table(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path, [vintage_fetch_result("2026-05-01", old=296.1, new=296.4)]
    )
    doctored = _doctored_copy(tmp_path, store, "extra.sqlite")
    with sqlite3.connect(doctored) as conn:
        conn.execute("CREATE TABLE stowaway (id TEXT PRIMARY KEY)")
    verification = verify_observations_schema(doctored)
    assert verification["ok"] is False
    assert "unexpected_table:stowaway" in verification["errors"]


def test_verifier_rejects_orphan_observations(tmp_path: Path) -> None:
    store = build_store_from_results(
        tmp_path, [vintage_fetch_result("2026-05-01", old=296.1, new=296.4)]
    )
    doctored = _doctored_copy(tmp_path, store, "orphan.sqlite")
    with sqlite3.connect(doctored) as conn:
        conn.execute(
            """
            INSERT INTO observations (
                observation_id, series_key, phenomenon_time, value,
                result_time, vintage, provenance_json
            ) VALUES ('obs:x', 'macro_ghost', '2026-05-01', 1.0, NULL, '', '{}')
            """
        )
    verification = verify_observations_schema(doctored)
    assert verification["ok"] is False
    assert "orphan_observations:1" in verification["errors"]


def test_verifier_reports_missing_store(tmp_path: Path) -> None:
    verification = verify_observations_schema(tmp_path / "absent.sqlite")
    assert verification["ok"] is False
    assert "observations_store_missing" in verification["errors"]


# ---------------------------------------------------------------------------
# Small end-to-end build (brief Step 4, adapted: fixtures, tmp dir)
# ---------------------------------------------------------------------------


def test_end_to_end_build_verify_and_query(tmp_path: Path) -> None:
    results = [
        vintage_fetch_result("2026-05-01", old=296.1, new=296.4),
        _macro_result("macro_cpi_yoy", [("2026-06-01", 297.0, "2026-07-14", "2026-07-14")]),
        _macro_result(
            "macro_core_cpi_yoy",
            [("2026-05-01", 301.2, "2026-06-11", "2026-06-11")],
        ),
        _macro_result(
            "macro_unemployment_rate",
            [("2026-05-01", 4.2, "2026-06-05", "2026-06-05")],
        ),
        _price_result("AAPL", [("2026-06-24", 201.2), ("2026-06-25", 201.5)]),
    ]
    target = tmp_path / "releases" / "v2-dev" / "indexes" / "observations.sqlite"
    build = build_observations_store(
        target, _seed_subset(), results, fetched_at="2026-08-30T01:02:03Z"
    )
    assert build.verification["ok"] is True
    # both CPI vintages are retained history; the catalog holds 3 macro
    # families + the price_close|AAPL instance (templates are never stored).
    assert build.counts["observations"] == 7
    assert build.counts["series_catalog"] == 4
    assert build.counts["ohlcv_observations"] == 2
    assert build.counts["observation_revisions"] == 1
    assert build.counts["release_events"] == 4

    verification = verify_observations_schema(target)
    assert verification["ok"] is True, verification["errors"]
    assert verification["schema_version"] == OBSERVATIONS_SCHEMA_VERSION

    store = ObservationsStore(target)
    macro = store.query_macro_series("cpi_yoy", limit=12)
    assert [point["value"] for point in macro["points"]] == [296.4, 297.0]
    assert macro["fetched_at"] == "2026-08-30T01:02:03Z"
    market = store.query_market_series("AAPL", "last_price", periods=5)
    assert market["format"] == MARKET_SERIES_FORMAT
    assert market["status"] == "available"
    assert [point["date"] for point in market["points"]] == ["2026-06-24", "2026-06-25"]
    releases = store.query_recent_releases(factor=None, limit=10)
    assert len(releases["releases"]) == 4
    rendered = json.dumps(macro) + json.dumps(market) + json.dumps(releases)
    for vendor in VENDOR_NAMES:
        assert vendor not in rendered


def test_build_rejects_unknown_series_key(tmp_path: Path) -> None:
    ghost = SeriesFetchResult(
        series_key="macro_ghost",
        provider="fred",
        observations=(RawObservation("macro_ghost", "2026-05-01", 1.0, None, None, {}),),
        status="available",
    )
    with pytest.raises(ValueError, match="unknown_series"):
        build_observations_store(tmp_path / "observations.sqlite", _seed_subset(), [ghost])


def test_build_uses_per_ticker_family_template(tmp_path: Path) -> None:
    """A family|ticker instance inherits the seed family's metric metadata."""

    store = build_store_from_results(tmp_path, [_price_result("MSFT", [("2026-06-25", 482.9)])])
    with sqlite3.connect(store.path) as conn:
        row = conn.execute(
            """
            SELECT canonical_metric, unit, ticker, is_per_ticker, domain
            FROM series_catalog WHERE series_key = 'price_close|MSFT'
            """
        ).fetchone()
    assert row == ("last_price", "USD_per_share", "MSFT", 1, "price")
    market = store.query_market_series("MSFT", "last_price", periods=5)
    assert market["status"] == "available"
    assert market["points"][0]["value"] == 482.9
