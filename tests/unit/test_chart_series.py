"""Unit tests: the chart_series sidecar joins the observation store (B4).

Observation series (macro monthly + monthly-aggregated price/valuation) are
projected into chart_series/chart_series_points rows with
``source_class = 'observation'`` and points carrying the store's
``observation_id`` as the object pointer.  Fixture-only (no network): fetch
results are built in memory exactly like the B1 adapters produce them, then
materialized by the pure observation builder, then projected by the chart
series builder.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from krw_ontology.agent_index.chart_series import (
    CHART_SAFE_CANONICAL_METRICS,
    _period_sort_key,
    build_chart_series_index,
    is_valid_chart_period,
    query_chart_series_pack,
)
from krw_ontology.observation.builder import build_observations_store
from krw_ontology.observation.ports import RawObservation, SeriesFetchResult
from krw_ontology.observation.seed import SeriesDefinition, load_series_seed

VENDOR_NAMES = ("fred", "fmp", "polygon")


def _seed_subset() -> dict[str, SeriesDefinition]:
    """3 FRED macro families + the per-ticker price/valuation families."""

    seed = load_series_seed()
    keys = (
        "macro_cpi_yoy",
        "macro_unemployment_rate",
        "macro_dgs10_yield",
        "price_close",
        "trailing_pe_ttm",
    )
    return {key: seed[key] for key in keys}


def _macro_result(
    series_key: str,
    rows: list[tuple[str, float, str | None, str | None]],
    *,
    status: str = "available",
) -> SeriesFetchResult:
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
            for phenomenon_time, value, result_time, vintage in rows
        ),
        status=status,
    )


def _price_result(
    ticker: str,
    rows: list[tuple[str, float]],
    *,
    family: str = "price_close",
    status: str = "available",
) -> SeriesFetchResult:
    series_key = f"{family}|{ticker}"
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
                provenance={
                    "endpoint": "historical-price-full",
                    "params_hash": "fixture000000",
                    "fetched_at": "2026-08-30T00:00:00Z",
                },
            )
            for phenomenon_time, value in rows
        ),
        status=status,
    )


def _release_root(tmp_path: Path, results: list[SeriesFetchResult]) -> Path:
    """A release-like root carrying indexes/observations.sqlite (no shards)."""

    root = tmp_path / "release"
    indexes = root / "indexes"
    indexes.mkdir(parents=True)
    build = build_observations_store(
        indexes / "observations.sqlite",
        _seed_subset(),
        results,
        fetched_at="2026-08-30T00:00:00Z",
    )
    assert build.verification.get("ok") is True
    (indexes / "shard_manifest.json").write_text(json.dumps({"shards": {}}))
    return root


def _observation_ids_by_date(root: Path, series_key: str) -> dict[str, str]:
    with sqlite3.connect(root / "indexes" / "observations.sqlite") as conn:
        rows = conn.execute(
            "SELECT phenomenon_time, observation_id FROM observations WHERE series_key = ?",
            (series_key,),
        ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _chart_points(result_path: Path, series_key: str) -> list[sqlite3.Row]:
    with sqlite3.connect(result_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT period, value, object_id
            FROM chart_series_points
            WHERE series_key = ?
            ORDER BY period_sort_key
            """,
            (series_key,),
        ).fetchall()


def _chart_series_row(result_path: Path, where: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
    with sqlite3.connect(result_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            f"SELECT * FROM chart_series WHERE {where}",
            params,
        ).fetchone()


# ---------------------------------------------------------------------------
# Step 1: monthly macro series with observation_id pointers
# ---------------------------------------------------------------------------


def test_cpi_monthly_series_uses_monthly_periods_and_observation_pointers(
    tmp_path: Path,
) -> None:
    cpi_rows = [
        ("2026-05-01", 296.1, "2026-06-10", "2026-06-10"),
        ("2026-06-01", 297.0, "2026-07-14", "2026-07-14"),
        ("2026-07-01", 297.8, "2026-08-12", "2026-08-12"),
    ]
    root = _release_root(tmp_path, [_macro_result("macro_cpi_yoy", cpi_rows)])
    result = build_chart_series_index(root, release_id="obs-test")
    assert result.verification["ok"] is True, result.verification["errors"]

    series = _chart_series_row(
        result.path, "canonical_metric = ? AND source_class = ?", ("cpi_yoy", "observation")
    )
    assert series is not None
    assert series["period_type"] == "monthly"
    assert series["unit"] == "percent"
    assert series["statement_family"] == "observation"
    assert series["point_count"] == 3

    points = _chart_points(result.path, str(series["series_key"]))
    assert [point["period"] for point in points] == ["CY2026M05", "CY2026M06", "CY2026M07"]
    assert [point["value"] for point in points] == [296.1, 297.0, 297.8]

    # object_id == the store's observation_id (the plan's pointer contract).
    ids_by_date = _observation_ids_by_date(root, "macro_cpi_yoy")
    assert {point["object_id"] for point in points} == set(ids_by_date.values())
    rendered = json.dumps({key: series[key] for key in series.keys()})
    for vendor in VENDOR_NAMES:
        assert vendor not in rendered


def test_daily_macro_series_aggregates_to_month_end(tmp_path: Path) -> None:
    """Daily yields ride the monthly axis as month-end last readings."""

    rows = [
        ("2026-06-28", 4.21, None, None),
        ("2026-06-29", 4.25, None, None),
        ("2026-06-30", 4.31, None, None),
        ("2026-07-31", 4.40, None, None),
    ]
    root = _release_root(tmp_path, [_macro_result("macro_dgs10_yield", rows)])
    result = build_chart_series_index(root)
    assert result.verification["ok"] is True

    series = _chart_series_row(result.path, "canonical_metric = ?", ("dgs10_yield",))
    assert series is not None
    assert series["source_class"] == "observation"
    points = _chart_points(result.path, str(series["series_key"]))
    assert [point["period"] for point in points] == ["CY2026M06", "CY2026M07"]
    assert [point["value"] for point in points] == [4.31, 4.40]  # month-end last


def test_quarterly_macro_series_rides_quarterly_grammar(tmp_path: Path) -> None:
    """Quarterly macro readings keep the existing CY<year>Q<q> grammar."""

    seed = _seed_subset()
    seed["macro_nominal_gdp"] = load_series_seed()["macro_nominal_gdp"]
    root = tmp_path / "release"
    indexes = root / "indexes"
    indexes.mkdir(parents=True)
    build = build_observations_store(
        indexes / "observations.sqlite",
        seed,
        [_macro_result("macro_nominal_gdp", [("2026-04-01", 29_000.0, None, None)])],
        fetched_at="2026-08-30T00:00:00Z",
    )
    assert build.verification.get("ok") is True
    (indexes / "shard_manifest.json").write_text(json.dumps({"shards": {}}))

    result = build_chart_series_index(root)
    series = _chart_series_row(result.path, "canonical_metric = ?", ("nominal_gdp",))
    assert series is not None
    assert series["period_type"] == "quarterly"
    points = _chart_points(result.path, str(series["series_key"]))
    assert [point["period"] for point in points] == ["CY2026Q2"]


# ---------------------------------------------------------------------------
# Step 2: daily price/valuation → month-end aggregation
# ---------------------------------------------------------------------------


def test_daily_price_aggregates_to_month_end_last_close(tmp_path: Path) -> None:
    """A month with 3 daily closes yields ONE point: the last close."""

    rows = [
        ("2026-06-10", 100.0),
        ("2026-06-20", 110.0),
        ("2026-06-30", 120.0),
        ("2026-07-15", 130.0),
    ]
    root = _release_root(tmp_path, [_price_result("AAPL", rows)])
    result = build_chart_series_index(root)
    assert result.verification["ok"] is True

    series = _chart_series_row(
        result.path, "ticker = ? AND canonical_metric = ?", ("AAPL", "last_price_monthly")
    )
    assert series is not None
    assert series["source_class"] == "observation"
    assert series["period_type"] == "monthly"
    assert series["unit"] == "USD_per_share"

    points = _chart_points(result.path, str(series["series_key"]))
    assert [point["period"] for point in points] == ["CY2026M06", "CY2026M07"]
    assert [point["value"] for point in points] == [120.0, 130.0]

    # The June point points at the observation of the month-end close date.
    ids_by_date = _observation_ids_by_date(root, "price_close|AAPL")
    june = points[0]
    assert june["object_id"] == ids_by_date["2026-06-30"]


def test_daily_valuation_series_aggregates_to_month_end(tmp_path: Path) -> None:
    rows = [
        ("2026-06-05", 28.4),
        ("2026-06-30", 29.1),
        ("2026-07-31", 30.2),
    ]
    root = _release_root(tmp_path, [_price_result("AAPL", rows, family="trailing_pe_ttm")])
    result = build_chart_series_index(root)
    series = _chart_series_row(result.path, "canonical_metric = ?", ("trailing_pe_ttm",))
    assert series is not None
    assert series["source_class"] == "observation"
    points = _chart_points(result.path, str(series["series_key"]))
    assert [point["period"] for point in points] == ["CY2026M06", "CY2026M07"]
    assert [point["value"] for point in points] == [29.1, 30.2]


def test_superseded_daily_close_never_wins_the_month_bucket(tmp_path: Path) -> None:
    """Latest-vintage resolution happens BEFORE the month bucketing."""

    series_key = "price_close|AAPL"
    raw = [
        RawObservation(series_key, "2026-06-30", 120.0, "2026-07-01", "2026-07-01", {}),
        RawObservation(series_key, "2026-06-30", 121.5, "2026-07-02", "2026-07-02", {}),
    ]
    result_fetch = SeriesFetchResult(
        series_key=series_key, provider="fmp", observations=tuple(raw), status="available"
    )
    root = _release_root(tmp_path, [result_fetch])
    result = build_chart_series_index(root)
    series = _chart_series_row(result.path, "canonical_metric = ?", ("last_price_monthly",))
    points = _chart_points(result.path, str(series["series_key"]))
    assert [point["value"] for point in points] == [121.5]


# ---------------------------------------------------------------------------
# Step 3: chart-safe allow-list gate
# ---------------------------------------------------------------------------


def test_observation_seed_metrics_are_chart_safe() -> None:
    for definition in load_series_seed().values():
        if definition.domain == "price" and definition.canonical_metric == "last_price":
            metric = "last_price_monthly"
        else:
            metric = definition.canonical_metric
        assert metric in CHART_SAFE_CANONICAL_METRICS, metric
    for metric in (
        "cpi_yoy",
        "fed_funds_rate",
        "unemployment_rate",
        "dgs10_yield",
        "last_price_monthly",
        "trailing_pe_ttm",
        "price_to_book_ttm",
    ):
        assert metric in CHART_SAFE_CANONICAL_METRICS


def test_projection_gates_on_chart_safe_allow_list(tmp_path: Path) -> None:
    """A catalog metric outside the allow-list is skipped, not emitted."""

    root = _release_root(
        tmp_path,
        [_macro_result("macro_cpi_yoy", [("2026-07-01", 297.8, None, None)])],
    )
    with sqlite3.connect(root / "indexes" / "observations.sqlite") as conn:
        conn.execute(
            "UPDATE series_catalog SET canonical_metric = 'not_chart_safe' "
            "WHERE series_key = 'macro_cpi_yoy'"
        )
        conn.commit()
    result = build_chart_series_index(root)
    assert result.verification["ok"] is True
    assert result.counts["series"] == 0
    assert result.counts["points"] == 0


# ---------------------------------------------------------------------------
# Step 4: absent observations.sqlite is a no-op
# ---------------------------------------------------------------------------


def test_absent_observations_store_build_is_noop(tmp_path: Path) -> None:
    root = tmp_path / "release"
    indexes = root / "indexes"
    indexes.mkdir(parents=True)
    (indexes / "shard_manifest.json").write_text(json.dumps({"shards": {}}))

    result = build_chart_series_index(root)
    assert result.verification["ok"] is True
    assert result.counts["series"] == 0
    assert result.counts["points"] == 0
    assert result.counts["tickers"] == 0

    with sqlite3.connect(result.path) as conn:
        observation_rows = conn.execute(
            "SELECT COUNT(*) FROM chart_series WHERE source_class = 'observation'"
        ).fetchone()[0]
    assert observation_rows == 0


# ---------------------------------------------------------------------------
# Step 5: period grammar — monthly accepted, garbage rejected
# ---------------------------------------------------------------------------


def test_period_validation_accepts_monthly_and_rejects_garbage() -> None:
    for period in ("CY2026M07", "CY2026M01", "CY2026M12", "CY2025Q3", "FY2025", "CY2026"):
        assert is_valid_chart_period(period), period
    for period in (
        "CY2026X07",  # unknown bucket letter
        "CY2026M13",  # month out of range
        "CY2026M0",
        "CY2026M7",  # zero-padded months only
        "2026-Q3!",
        "",
        "CY2026Q5",
    ):
        assert not is_valid_chart_period(period), period


def test_period_sort_key_orders_months_within_and_across_years() -> None:
    assert _period_sort_key("CY2026M01") < _period_sort_key("CY2026M07")
    assert _period_sort_key("CY2026M12") < _period_sort_key("CY2027M01")
    # Existing FY/CY/Q keys keep their exact values (no behavior change).
    assert _period_sort_key("CY2025Q3") == 20253
    assert _period_sort_key("FY2025") == 20250


# ---------------------------------------------------------------------------
# Pack serving: metric → series lookup includes observation rows
# ---------------------------------------------------------------------------


def _build_observation_chart(tmp_path: Path) -> Path:
    cpi_rows = [
        ("2026-05-01", 296.1, None, None),
        ("2026-06-01", 297.0, None, None),
        ("2026-07-01", 297.8, None, None),
    ]
    price_rows = [
        ("2026-06-10", 100.0),
        ("2026-06-30", 120.0),
        ("2026-07-31", 130.0),
    ]
    root = _release_root(
        tmp_path,
        [
            _macro_result("macro_cpi_yoy", cpi_rows),
            _price_result("AAPL", price_rows),
        ],
    )
    result = build_chart_series_index(root)
    assert result.verification["ok"] is True
    return result.path


def test_pack_serves_macro_observation_series_with_tickers(tmp_path: Path) -> None:
    chart_path = _build_observation_chart(tmp_path)
    pack = query_chart_series_pack(
        chart_path,
        question="CPI 추이 차트와 AAPL 매출",
        tickers=["AAPL"],
        limit_series=8,
        limit_points=12,
    )
    assert pack is not None
    cpi = next(series for series in pack["series"] if series["canonical_metric"] == "cpi_yoy")
    assert cpi["source_class"] == "observation"
    assert cpi["period_type"] == "monthly"
    assert cpi["points"][-1]["period"] == "CY2026M07"
    # observation_id pointer contract on served points
    assert cpi["points"][-1]["object_id"].startswith("obs:")
    assert cpi["points"][-1]["evidence_ref"] == cpi["points"][-1]["object_id"]
    assert pack["quality"]["chart_safe"] is True
    rendered = json.dumps(pack)
    for vendor in VENDOR_NAMES:
        assert vendor not in rendered


def test_pack_serves_macro_series_without_tickers(tmp_path: Path) -> None:
    chart_path = _build_observation_chart(tmp_path)
    pack = query_chart_series_pack(
        chart_path,
        question="CPI 추이 차트",
        tickers=[],
    )
    assert pack is not None
    assert [series["canonical_metric"] for series in pack["series"]] == ["cpi_yoy"]


def test_pack_serves_monthly_price_series_for_ticker(tmp_path: Path) -> None:
    chart_path = _build_observation_chart(tmp_path)
    pack = query_chart_series_pack(
        chart_path,
        question="AAPL 주가 추이 차트",
        tickers=["AAPL"],
    )
    assert pack is not None
    price = next(
        series for series in pack["series"] if series["canonical_metric"] == "last_price_monthly"
    )
    assert price["ticker"] == "AAPL"
    assert [point["period"] for point in price["points"]] == ["CY2026M06", "CY2026M07"]
    assert [point["value"] for point in price["points"]] == [120.0, 130.0]
