"""D13/D14 collection-eligibility policy tests.

The default `observation build` run registers ONLY weekly-or-slower macro
releases: daily-frequency macro series (yields, VIX, SP500, the DFEDTARU
target upper bound) and every per-ticker price/valuation family are opt-in
via ``--include-on-demand-series`` (future P2 event-anchored backfills).
Everything here runs against fixture providers and tmp roots: no network.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from krw_ontology.observation.builder import collect_observations
from krw_ontology.observation.ports import (
    RawObservation,
    SeriesFetchRequest,
    SeriesFetchResult,
)
from krw_ontology.observation.seed import (
    SeriesDefinition,
    default_collection_series,
    load_series_seed,
)

# The 12 weekly-or-slower macro releases the default policy registers
# (9 monthly + ICSA weekly + 2 quarterly GDP families).
ELIGIBLE_SERIES_KEYS = {
    "macro_cpi_yoy",
    "macro_core_cpi_yoy",
    "macro_core_pce_yoy",
    "macro_fed_funds_rate",  # FEDFUNDS is a MONTHLY FRED series (DFF/EFFR are daily)
    "macro_unemployment_rate",
    "macro_nonfarm_payrolls",
    "macro_initial_jobless_claims",
    "macro_nominal_gdp",
    "macro_real_gdp",
    "macro_industrial_production",
    "macro_retail_sales",
    "macro_housing_starts",
}

# Daily-frequency macro families the default policy must skip even when the
# FRED credential is present.
DAILY_MACRO_SERIES_KEYS = {
    "macro_fed_funds_target_upper",  # DFEDTARU
    "macro_dgs2_yield",
    "macro_dgs10_yield",
    "macro_dgs30_yield",
    "macro_treasury_10y2y_spread",
    "macro_breakeven_inflation_10y",
    "macro_vix",
    "macro_sp500",
}

PER_TICKER_FAMILIES = {
    "price_close",
    "trailing_pe_ttm",
    "price_to_book_ttm",
    "polygon_ohlcv",
}

FULL_SEED_SIZE = 24  # 20 macro families + 4 per-ticker family templates


class _RecordingProvider:
    """Serves one available observation per request and records every request."""

    provider_name = ""

    def __init__(self, name: str) -> None:
        self.provider_name = name
        self.requests: list[SeriesFetchRequest] = []

    def fetch_series(self, request: SeriesFetchRequest) -> SeriesFetchResult:
        self.requests.append(request)
        return SeriesFetchResult(
            series_key=request.series_key,
            provider=self.provider_name,
            observations=(
                RawObservation(
                    series_key=request.series_key,
                    phenomenon_time="2026-06-01",
                    value=1.0,
                    result_time=None,
                    vintage=None,
                    provenance={"endpoint": "fixture", "fetched_at": "t"},
                ),
            ),
            status="available",
        )


def _all_providers() -> tuple[_RecordingProvider, _RecordingProvider, _RecordingProvider]:
    return (
        _RecordingProvider("fred"),
        _RecordingProvider("fmp"),
        _RecordingProvider("polygon"),
    )


# ---------------------------------------------------------------------------
# Seed-level policy (D13): default_collection_series
# ---------------------------------------------------------------------------


def test_default_collection_series_registers_weekly_plus_macro_only() -> None:
    seed = load_series_seed()
    eligible = default_collection_series(seed)

    assert set(eligible) == ELIGIBLE_SERIES_KEYS
    assert len(eligible) == 12
    assert len(seed) == FULL_SEED_SIZE
    # Daily macro families and per-ticker price/valuation templates are out.
    assert not (set(eligible) & DAILY_MACRO_SERIES_KEYS)
    assert not (set(eligible) & PER_TICKER_FAMILIES)
    # And the eligible values are the untouched seed definitions.
    for series_key, definition in eligible.items():
        assert definition is seed[series_key]


def test_default_policy_seed_frequencies_match_policy_set() -> None:
    """Every eligible family is genuinely weekly/monthly/quarterly macro."""
    from collections import Counter

    eligible = default_collection_series(load_series_seed())
    frequencies = Counter(
        (definition.domain, definition.frequency) for definition in eligible.values()
    )
    assert frequencies == Counter(
        {
            ("macro", "monthly"): 9,
            ("macro", "weekly"): 1,  # ICSA initial jobless claims
            ("macro", "quarterly"): 2,  # GDP + real GDP
        }
    )


def test_eligibility_is_exactly_macro_domain_and_weekly_plus_frequency() -> None:
    """Quarterly non-macro, daily macro, missing frequency: all excluded."""
    seed = load_series_seed()

    quarterly_non_macro = SeriesDefinition(
        series_key="synthetic_quarterly_valuation",
        domain="valuation",
        provider="fmp",
        provider_series_id="ticker",
        canonical_metric="price_to_book_ttm",
        unit="ratio",
        frequency="quarterly",
        adjustment="none",
        factor=None,
        is_per_ticker=True,
    )
    monkeypatched_seed = dict(seed)
    monkeypatched_seed[quarterly_non_macro.series_key] = quarterly_non_macro

    eligible = default_collection_series(monkeypatched_seed)
    assert quarterly_non_macro.series_key not in eligible
    assert set(eligible) == ELIGIBLE_SERIES_KEYS  # nothing else changed

    # Case-insensitive frequency is accepted; missing/None frequency is not.
    weekly_upper = SeriesDefinition(
        series_key="synthetic_weekly_macro",
        domain="macro",
        provider="fred",
        provider_series_id="SYNTH00",
        canonical_metric="cpi_yoy",
        unit="percent",
        frequency="Weekly",
        adjustment="none",
        factor="labor",
    )
    assert weekly_upper.series_key in default_collection_series(
        {**monkeypatched_seed, weekly_upper.series_key: weekly_upper}
    )

    no_frequency = SeriesDefinition(
        series_key="synthetic_no_frequency_macro",
        domain="macro",
        provider="fred",
        provider_series_id="SYNTH01",
        canonical_metric="cpi_yoy",
        unit="percent",
        frequency="",  # type: ignore[arg-type] — malformed constructed seed
        adjustment="none",
        factor="labor",
    )
    assert no_frequency.series_key not in default_collection_series(
        {**monkeypatched_seed, no_frequency.series_key: no_frequency}
    )


# ---------------------------------------------------------------------------
# Collect-level policy: providers present, daily/per-ticker still skipped
# ---------------------------------------------------------------------------


def test_default_policy_skips_daily_and_per_ticker_even_when_providers_available() -> None:
    """All three providers can serve data; the default policy still skips
    daily macro series and per-ticker families (D13: policy, not credentials)."""

    seed = load_series_seed()
    fred, fmp, polygon = _all_providers()

    results = collect_observations(
        default_collection_series(seed),
        providers=[fred, fmp, polygon],
        tickers=["AAPL"],
        start="2026-06-01",
        end="2026-06-30",
    )

    # Only eligible macro families were requested from FRED.
    assert {request.provider_series_id for request in fred.requests} == {
        definition.provider_series_id
        for definition in default_collection_series(seed).values()
    }
    assert not (
        {request.provider_series_id for request in fred.requests}
        & {"DGS2", "DGS10", "DGS30", "T10Y2Y", "T10YIE", "VIXCLS", "SP500", "DFEDTARU"}
    )
    # Per-ticker families are outside the default policy: no FMP/Polygon calls.
    assert fmp.requests == []
    assert polygon.requests == []

    by_series = {result.series_key: result for result in results}
    assert set(by_series) == ELIGIBLE_SERIES_KEYS
    assert all(result.status == "available" for result in results)


def test_full_seed_collect_unchanged_without_policy_filter() -> None:
    """Sanity: collect_observations over the full seed still fetches everything
    (the old behavior), so the opt-in flag has a complete baseline."""

    seed = load_series_seed()
    fred, fmp, polygon = _all_providers()
    results = collect_observations(
        seed, providers=[fred, fmp, polygon], tickers=["AAPL"]
    )

    by_series = {result.series_key: result for result in results}
    macro_keys = {key for key in by_series if not key.endswith("|AAPL")}
    assert macro_keys == ELIGIBLE_SERIES_KEYS | DAILY_MACRO_SERIES_KEYS
    assert {key for key in by_series if "|" in key} == {
        f"{family}|AAPL" for family in PER_TICKER_FAMILIES
    }
    assert len(fred.requests) == 20
    assert len(fmp.requests) == 3
    assert len(polygon.requests) == 1


# ---------------------------------------------------------------------------
# CLI-level policy: `observation build` default vs --include-on-demand-series
# ---------------------------------------------------------------------------


def _install_fixture_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_RecordingProvider, _RecordingProvider, _RecordingProvider]:
    fred, fmp, polygon = _all_providers()
    for key in ("FRED_API_KEY", "FMP_API_KEY", "POLYGON_API_KEY"):
        monkeypatch.setenv(key, "fixture-key")
    monkeypatch.setattr(
        "krw_ontology.observation.providers.fred.FredProvider",
        lambda include_vintages=True: fred,
    )
    monkeypatch.setattr("krw_ontology.observation.providers.fmp.FmpHistoryProvider", lambda: fmp)
    monkeypatch.setattr(
        "krw_ontology.observation.providers.polygon.build_polygon_provider",
        lambda: polygon,
    )
    return fred, fmp, polygon


def _store_statuses(store_path: Path) -> dict[str, str]:
    with sqlite3.connect(store_path) as conn:
        rows = conn.execute("SELECT series_key, status FROM series_catalog").fetchall()
    return dict(rows)


def test_observation_build_default_policy_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default run collects only the eligible set and says which policy ran."""
    from typer.testing import CliRunner

    from krw_ontology.cli.main import app

    fred, fmp, polygon = _install_fixture_providers(monkeypatch)
    root = tmp_path / "running"

    result = CliRunner().invoke(
        app, ["observation", "build", "--root", str(root), "--ticker", "AAPL"]
    )
    assert result.exit_code == 0, result.output

    # The summary line states the policy with real computed numbers.
    assert "collecting 12/24 series (default policy: weekly+ macro only)" in result.output
    assert "Collected 12/12 series" in result.output

    # Only eligible macro series hit the (fully credentialed) providers.
    assert len(fred.requests) == 12
    assert not (
        {request.provider_series_id for request in fred.requests}
        & {"DGS2", "VIXCLS", "SP500", "DFEDTARU"}
    )
    assert fmp.requests == []
    assert polygon.requests == []

    statuses = _store_statuses(root / "indexes" / "observations.sqlite")
    # Catalog keeps the B4 doctrine: daily macro families appear as
    # unavailable, and no per-ticker instance rows exist.
    assert {key for key, status in statuses.items() if status == "available"} == (
        ELIGIBLE_SERIES_KEYS
    )
    assert all(statuses[key] == "unavailable" for key in DAILY_MACRO_SERIES_KEYS)
    assert not [key for key in statuses if "|" in key]
    assert len(statuses) == 20


def test_observation_build_include_on_demand_series_collects_full_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--include-on-demand-series restores the old full-seed collection."""
    from typer.testing import CliRunner

    from krw_ontology.cli.main import app

    fred, fmp, polygon = _install_fixture_providers(monkeypatch)
    root = tmp_path / "running"

    result = CliRunner().invoke(
        app,
        [
            "observation",
            "build",
            "--root",
            str(root),
            "--ticker",
            "AAPL",
            "--include-on-demand-series",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "collecting 24/24 series" in result.output
    assert "default policy: weekly+ macro only" not in result.output

    # The exact request mix the old behavior produced.
    assert len(fred.requests) == 20
    assert len(fmp.requests) == 3
    assert len(polygon.requests) == 1

    # Equivalence against the old behavior: same statuses for every series
    # the unfiltered collect_observations path would produce.
    seed = load_series_seed()
    old_fred, old_fmp, old_polygon = _all_providers()
    old_results = collect_observations(
        seed, providers=[old_fred, old_fmp, old_polygon], tickers=["AAPL"]
    )
    expected = {item.series_key: item.status for item in old_results}
    statuses = _store_statuses(root / "indexes" / "observations.sqlite")
    assert statuses == expected
    assert statuses["macro_vix"] == "available"  # daily macro collected on demand
    assert statuses["polygon_ohlcv|AAPL"] == "available"
