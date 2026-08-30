"""Release-pipeline integration tests for the observation sidecar (B3).

The observation store is collected by the standalone `observation build`
step and carried into release candidates when present; the main release
build itself must never fetch.  These tests exercise the carry path and the
CLI collection plumbing with fixture providers (no network).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from krw_ontology.observation.builder import (
    PROVIDER_REQUEST_SERIES_KEY,
    build_observations_store,
    collect_observations,
)
from krw_ontology.observation.ports import (
    ObservationProvider,
    RawObservation,
    SeriesFetchRequest,
    SeriesFetchResult,
)
from krw_ontology.observation.seed import load_series_seed
from krw_ontology.observation.store import verify_observations_schema


class _FixturePriceProvider:
    """Captures requests and serves one daily close (B1 registry keys)."""

    provider_name = "fmp"

    def __init__(self) -> None:
        self.requests: list[SeriesFetchRequest] = []

    def fetch_series(self, request: SeriesFetchRequest) -> SeriesFetchResult:
        self.requests.append(request)
        observation = RawObservation(
            series_key=request.series_key,
            phenomenon_time="2026-06-25",
            value=201.5,
            result_time=None,
            vintage=None,
            provenance={"endpoint": "historical-price-full", "fetched_at": "t"},
        )
        return SeriesFetchResult(
            series_key=request.series_key,
            provider=self.provider_name,
            observations=(observation,),
            status="available",
        )


def test_collect_observations_rekeys_per_ticker_families() -> None:
    seed = load_series_seed()
    provider = _FixturePriceProvider()

    # No FRED provider is supplied (its credential is absent in the sandbox):
    # macro families degrade to unavailable status instead of raising.
    results = collect_observations(
        seed,
        providers=[provider],
        tickers=["aapl"],
        start="2026-06-22",
        end="2026-06-26",
    )

    by_series = {result.series_key: result for result in results}
    # The FMP registry key is used on the wire; the store key is family|ticker.
    # Seed families are visited in sorted order: price_close, price_to_book_ttm,
    # trailing_pe_ttm (polygon_ohlcv is skipped: no polygon provider supplied).
    assert [request.series_key for request in provider.requests] == [
        PROVIDER_REQUEST_SERIES_KEY["price_close"],
        PROVIDER_REQUEST_SERIES_KEY["price_to_book_ttm"],
        PROVIDER_REQUEST_SERIES_KEY["trailing_pe_ttm"],
    ]
    assert all(request.provider_series_id == "AAPL" for request in provider.requests)
    price = by_series["price_close|AAPL"]
    assert price.status == "available"
    assert price.observations[0].series_key == "price_close|AAPL"
    # FRED series collapse to unavailable without the provider.
    assert by_series["macro_cpi_yoy"].status == "unavailable"
    assert by_series["macro_cpi_yoy"].observations == ()

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "indexes" / "observations.sqlite"
        build = build_observations_store(target, seed, results)
        assert build.verification["ok"] is True
        assert build.counts["series_catalog"] == 24  # 20 macro + 4 AAPL instances
        assert build.counts["observations"] == 3
        with sqlite3.connect(target) as conn:
            tickers = conn.execute(
                "SELECT DISTINCT ticker FROM series_catalog WHERE ticker IS NOT NULL"
            ).fetchall()
        assert tickers == [("AAPL",)]


def test_materialize_release_root_carries_observations_sidecar(tmp_path: Path) -> None:
    from krw_ontology.cli.main import _materialize_release_root_from_source

    source_root = tmp_path / "running"
    (source_root / "indexes").mkdir(parents=True)
    (source_root / "companies" / "AAPL").mkdir(parents=True)
    seed = load_series_seed()
    result = SeriesFetchResult(
        series_key="macro_cpi_yoy",
        provider="fred",
        observations=(RawObservation("macro_cpi_yoy", "2026-05-01", 296.4, None, None, {}),),
        status="available",
    )
    source_store = source_root / "indexes" / "observations.sqlite"
    build = build_observations_store(source_store, seed, [result])
    assert build.verification["ok"] is True
    (source_root / "companies" / "AAPL" / "notes.txt").write_text("artifact", encoding="utf-8")

    release_root = tmp_path / "releases" / "dev" / "20260830_000000"
    _materialize_release_root_from_source(source_root, release_root)

    carried = release_root / "indexes" / "observations.sqlite"
    assert carried.is_file()
    assert carried.read_bytes() == source_store.read_bytes()
    # The rest of the indexes dir is still rebuilt fresh by the release build.
    assert not (release_root / "indexes" / "source_manifest.json").exists()
    assert (release_root / "companies" / "AAPL" / "notes.txt").is_file()
    assert verify_observations_schema(carried)["ok"] is True


def test_materialize_release_root_without_sidecar_stays_clean(tmp_path: Path) -> None:
    from krw_ontology.cli.main import _materialize_release_root_from_source

    source_root = tmp_path / "running2"
    (source_root / "indexes").mkdir(parents=True)
    release_root = tmp_path / "releases2" / "dev" / "20260830_000001"
    _materialize_release_root_from_source(source_root, release_root)
    assert not (release_root / "indexes" / "observations.sqlite").exists()


def test_observation_provider_protocol_is_satisfied_by_fixture() -> None:
    provider: ObservationProvider = _FixturePriceProvider()
    assert provider.provider_name == "fmp"


# ---------------------------------------------------------------------------
# Review round 1: ticker charset guard, bridge fail-fast, release-tree refusal
# ---------------------------------------------------------------------------


def test_normalize_ticker_uppercases_then_validates() -> None:
    from krw_ontology.observation.builder import normalize_ticker

    # Lowercase input is normalized (upper-cased, then validated).
    assert normalize_ticker("aapl") == "AAPL"
    assert normalize_ticker(" brk.b ") == "BRK.B"
    assert normalize_ticker("BF-B") == "BF-B"
    # The series-key separator and every other symbol are rejected.
    for bad in ("A|B", "", "   ", "BRK B", "AAPL/BTC", "테슬라"):
        with pytest.raises(ValueError, match="invalid_ticker"):
            normalize_ticker(bad)


def test_collect_observations_rejects_malformed_ticker() -> None:
    seed = load_series_seed()
    with pytest.raises(ValueError, match="invalid_ticker"):
        collect_observations(seed, providers=[], tickers=["A|B"])


def test_collect_observations_fails_fast_on_bridge_gap() -> None:
    """A per-ticker seed family without a provider request key is a hard error."""

    from krw_ontology.observation.builder import assert_provider_bridge_covers_seed
    from krw_ontology.observation.seed import SeriesDefinition

    seed = load_series_seed()
    poisoned = dict(seed)
    poisoned["ghost_family"] = SeriesDefinition(
        series_key="ghost_family",
        domain="price",
        provider="fmp",
        provider_series_id="ticker",
        canonical_metric="last_price",
        unit="USD_per_share",
        frequency="daily",
        adjustment="none",
        factor=None,
        is_per_ticker=True,
    )

    with pytest.raises(ValueError, match="provider_bridge_missing_families:ghost_family"):
        collect_observations(poisoned, providers=[], tickers=["AAPL"])
    with pytest.raises(ValueError, match="provider_bridge_missing_families"):
        assert_provider_bridge_covers_seed(poisoned)
    # The healthy seed passes the same check.
    assert_provider_bridge_covers_seed(seed)


def test_observation_build_refuses_release_tree_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from krw_ontology.cli.main import app

    for key in ("FRED_API_KEY", "FMP_API_KEY", "POLYGON_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    runner = CliRunner()

    release_root = tmp_path / "releases" / "dev" / "20260830_000000"
    for args in (
        ["--output", str(release_root / "indexes" / "observations.sqlite")],
        ["--root", str(release_root)],
        ["--output", str(tmp_path / "prod" / "observations.sqlite")],
    ):
        result = runner.invoke(app, ["observation", "build", *args])
        assert result.exit_code == 1, args
        assert "Refusing" in result.output
        assert not (tmp_path / "prod").exists() or not any(
            (tmp_path / "prod").rglob("observations.sqlite")
        )

    # A clean running root still passes the guard (no keys → degraded build).
    clean_root = tmp_path / "running" / "workspace"
    ok = runner.invoke(app, ["observation", "build", "--root", str(clean_root)])
    assert ok.exit_code == 0, ok.output
    assert (clean_root / "indexes" / "observations.sqlite").is_file()
    assert "Refusing" not in ok.output
