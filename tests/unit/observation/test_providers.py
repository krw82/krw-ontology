"""Unit tests for observation provider ports and adapters.

Fixture-only (no network): openers are injected exactly like the market
snapshot router DI pattern in services/krw-ontology-runtime market/snapshot.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from krw_ontology.observation.ports import (
    SeriesFetchRequest,
)
from krw_ontology.observation.providers.fmp import FmpHistoryProvider
from krw_ontology.observation.providers.fred import FredProvider
from krw_ontology.observation.providers.polygon import (
    PolygonDailyProvider,
    build_polygon_provider,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "observation"


class _FixtureResponse:
    """Minimal context-manager response over a canned body."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, amount: int = -1) -> bytes:
        if amount is None or amount < 0:
            return self._body
        return self._body[:amount]

    def __enter__(self) -> _FixtureResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def fixture_opener(body: str | bytes, requests: list[Any] | None = None) -> Any:
    payload = body.encode("utf-8") if isinstance(body, str) else body

    def opener(request: Any, timeout: float | None = None) -> _FixtureResponse:
        if requests is not None:
            requests.append(request)
        return _FixtureResponse(payload)

    return opener


def raising_opener(request: Any, timeout: float | None = None) -> Any:
    raise OSError("simulated transport failure")


def runtime_error_opener(request: Any, timeout: float | None = None) -> Any:
    raise RuntimeError("programmer error must surface")


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _fmp_request(series_key: str = "price_close_usd_daily") -> SeriesFetchRequest:
    return SeriesFetchRequest(
        series_key=series_key,
        provider_series_id="AAPL",
        ticker="AAPL",
        start="2026-06-22",
        end="2026-06-26",
    )


def _fred_request() -> SeriesFetchRequest:
    return SeriesFetchRequest(
        series_key="cpi_yoy",
        provider_series_id="CPIAUCSL",
        ticker=None,
        start=None,
        end=None,
    )


def _polygon_request() -> SeriesFetchRequest:
    return _fmp_request()


def _make_provider(vendor: str, *, opener: Any, api_key: str | None = "test") -> Any:
    constructors = {
        "fmp": FmpHistoryProvider,
        "fred": FredProvider,
        "polygon": PolygonDailyProvider,
    }
    return constructors[vendor](api_key=api_key, opener=opener)


def _requests_by_vendor() -> dict[str, SeriesFetchRequest]:
    return {
        "fmp": _fmp_request(),
        "fred": _fred_request(),
        "polygon": _polygon_request(),
    }


def test_fmp_adapter_parses_daily_close_history():
    provider = FmpHistoryProvider(
        api_key="test", opener=fixture_opener(load_fixture("fmp_price.json"))
    )
    result = provider.fetch_series(_fmp_request())

    assert result.status == "available"
    assert result.provider == "fmp"
    assert result.series_key == "price_close_usd_daily"
    observations = result.observations
    assert len(observations) == 5
    assert [o.phenomenon_time for o in observations] == [
        "2026-06-22",
        "2026-06-23",
        "2026-06-24",
        "2026-06-25",
        "2026-06-26",
    ]
    assert [o.value for o in observations] == [201.10, 202.44, 202.71, 203.05, 202.68]
    assert all(o.result_time is None and o.vintage is None for o in observations)
    assert all(o.series_key == "price_close_usd_daily" for o in observations)
    assert all(o.provenance["endpoint"] == "historical-price-full" for o in observations)
    assert all(len(o.provenance["params_hash"]) == 12 for o in observations)
    assert all(o.provenance["fetched_at"].endswith("Z") for o in observations)


def test_fmp_adapter_parses_quarterly_ratio_rows():
    opener = fixture_opener(load_fixture("fmp_ratios.json"))
    provider = FmpHistoryProvider(api_key="test", opener=opener)

    pe = provider.fetch_series(_fmp_request("pe_ttm_quarterly"))
    assert pe.status == "available"
    assert [(o.phenomenon_time, o.value) for o in pe.observations] == [
        ("2025-09-30", 33.12),
        ("2025-12-31", 34.05),
        ("2026-03-31", 31.88),
    ]

    pb = provider.fetch_series(_fmp_request("pb_ttm_quarterly"))
    assert pb.status == "available"
    assert [o.value for o in pb.observations] == [51.40, 52.77, 49.63]
    assert all(o.provenance["endpoint"] == "ratios-ttm" for o in pb.observations)


def test_fmp_adapter_rejects_unknown_series_key():
    provider = FmpHistoryProvider(
        api_key="test", opener=fixture_opener(load_fixture("fmp_price.json"))
    )
    result = provider.fetch_series(_fmp_request("dividend_yield_daily"))

    assert result.status == "unavailable"
    assert result.observations == ()


def test_fred_adapter_skips_missing_sentinel_observations():
    provider = FredProvider(api_key="test", opener=fixture_opener(load_fixture("fred_cpi.json")))
    result = provider.fetch_series(_fred_request())

    assert result.status == "available"
    assert result.provider == "fred"
    observations = result.observations
    assert [o.phenomenon_time for o in observations] == ["2026-02-01", "2026-03-01", "2026-05-01"]
    assert [o.value for o in observations] == [321.7, 322.1, 322.4]
    assert all(o.result_time is None and o.vintage is None for o in observations)
    assert all(o.provenance["endpoint"] == "series/observations" for o in observations)


def test_fred_adapter_parses_vintage_observations():
    body = load_fixture("fred_cpi_vintage.json")
    provider = FredProvider(api_key="test", opener=fixture_opener(body))
    result = provider.fetch_series(
        SeriesFetchRequest(
            series_key="cpi_yoy",
            provider_series_id="CPIAUCSL",
            ticker=None,
            start=None,
            end=None,
        )
    )
    assert result.status == "available"
    obs = result.observations
    assert {o.vintage for o in obs} >= {"2026-06-01", "2026-07-01"}  # two vintages
    same_period = [o for o in obs if o.phenomenon_time.startswith("2026-05")]
    assert len({o.value for o in same_period}) >= 1  # pre/post revision values coexist
    # Both vintages of the same phenomenon_time survive as separate rows.
    assert len(same_period) == 2
    assert {o.value for o in same_period} == {322.4, 322.8}
    assert {o.result_time for o in same_period} == {"2026-06-01", "2026-07-01"}
    assert all(o.result_time == o.vintage for o in obs)


def test_fred_vintage_mode_parses_realtime_start_shaped_rows():
    provider = FredProvider(
        api_key="test",
        include_vintages=True,
        opener=fixture_opener(load_fixture("fred_cpi_vintage_realtime.json")),
    )
    result = provider.fetch_series(_fred_request())

    assert result.status == "available"
    obs = result.observations
    # Vintages are extracted from realtime_start on the live ALFRED row shape.
    assert {(o.phenomenon_time, o.vintage, o.value) for o in obs} == {
        ("2026-05-01", "2026-06-10", 322.4),
        ("2026-05-01", "2026-07-10", 322.8),
        ("2026-06-01", "2026-07-10", 323.1),
    }
    # Two vintages of the same phenomenon_time survive as separate rows.
    same_period = [o for o in obs if o.phenomenon_time == "2026-05-01"]
    assert len(same_period) == 2
    assert all(o.result_time == o.vintage for o in obs)


def test_fred_vintage_mode_requests_all_vintages_with_early_realtime_start():
    requests: list[Any] = []
    provider = FredProvider(
        api_key="test",
        include_vintages=True,
        opener=fixture_opener(load_fixture("fred_cpi_vintage.json"), requests=requests),
    )
    result = provider.fetch_series(
        SeriesFetchRequest(
            series_key="cpi_yoy",
            provider_series_id="CPIAUCSL",
            ticker=None,
            start="2026-05-01",
            end="2026-06-01",
        )
    )

    assert result.status == "available"
    assert len(requests) == 1
    url = requests[0].full_url
    assert "series_id=CPIAUCSL" in url
    assert "observation_start=2026-05-01" in url
    assert "observation_end=2026-06-01" in url
    # All-vintages view REQUIRES an explicit early realtime_start: FRED
    # defaults the real-time window to *today* when omitted, which would
    # collapse every series to a single fetch-date vintage (no revisions).
    assert "realtime_start=1776-07-04" in url
    assert "realtime_end" not in url
    assert "vintage_dates" not in url
    # Client-side window filter: 2026-04-01 dropped, both 2026-05-01 vintages kept.
    assert [o.phenomenon_time for o in result.observations] == [
        "2026-05-01",
        "2026-05-01",
        "2026-06-01",
    ]
    kept = [o for o in result.observations if o.phenomenon_time == "2026-05-01"]
    assert {o.vintage for o in kept} == {"2026-06-01", "2026-07-01"}


def test_fred_latest_mode_sends_no_realtime_bounds():
    requests: list[Any] = []
    provider = FredProvider(
        api_key="test",
        include_vintages=False,
        opener=fixture_opener(load_fixture("fred_cpi_vintage.json"), requests=requests),
    )
    provider.fetch_series(_fred_request())
    url = requests[0].full_url
    # Latest-value mode relies on FRED's realtime-default (today) by design;
    # only the vintage view pins an early realtime_start.
    assert "realtime_start" not in url
    assert "realtime_end" not in url


def test_fred_vintage_mode_keeps_first_release_published_before_window_start():
    provider = FredProvider(
        api_key="test",
        include_vintages=True,
        opener=fixture_opener(load_fixture("fred_cpi_vintage_window.json")),
    )
    result = provider.fetch_series(
        SeriesFetchRequest(
            series_key="cpi_yoy",
            provider_series_id="CPIAUCSL",
            ticker=None,
            start="2026-06-01",
            end="2026-06-30",
        )
    )

    assert result.status == "available"
    obs = result.observations
    # Only the in-window phenomenon_time survives; out-of-window periods drop.
    assert {o.phenomenon_time for o in obs} == {"2026-06-01"}
    # BOTH vintages of the in-window period are kept — including the first
    # release published (2026-05-20) BEFORE request.start (2026-06-01).
    assert {o.vintage for o in obs} == {"2026-05-20", "2026-07-10"}
    assert {o.value for o in obs} == {322.9, 323.1}


def test_polygon_adapter_converts_ms_epoch_to_iso_dates():
    provider = PolygonDailyProvider(
        api_key="test", opener=fixture_opener(load_fixture("polygon_daily.json"))
    )
    result = provider.fetch_series(_polygon_request())

    assert result.status == "available"
    assert result.provider == "polygon"
    observations = result.observations
    assert len(observations) == 5
    assert [o.phenomenon_time for o in observations] == [
        "2026-06-22",
        "2026-06-23",
        "2026-06-24",
        "2026-06-25",
        "2026-06-26",
    ]
    assert [o.value for o in observations] == [201.10, 202.44, 202.71, 203.05, 202.68]
    assert all(o.result_time is None and o.vintage is None for o in observations)
    assert all(o.provenance["endpoint"] == "aggs/ticker/range/1/day" for o in observations)


@pytest.mark.parametrize("vendor", ["fmp", "fred", "polygon"])
def test_provider_reports_unavailable_when_transport_fails(vendor: str):
    provider = _make_provider(vendor, opener=raising_opener)
    result = provider.fetch_series(_requests_by_vendor()[vendor])

    assert result.status == "unavailable"
    assert result.provider == vendor
    assert result.observations == ()


@pytest.mark.parametrize("vendor", ["fmp", "fred", "polygon"])
def test_provider_propagates_programmer_errors_from_opener(vendor: str):
    # Only transport/HTTP/timeout/JSON/oversize failures collapse to
    # "unavailable"; adapter bugs (non-OSError/ValueError) must surface.
    provider = _make_provider(vendor, opener=runtime_error_opener)

    with pytest.raises(RuntimeError, match="programmer error must surface"):
        provider.fetch_series(_requests_by_vendor()[vendor])


@pytest.mark.parametrize("vendor", ["fmp", "fred", "polygon"])
def test_provider_reports_unavailable_on_malformed_payload(vendor: str):
    provider = _make_provider(vendor, opener=fixture_opener("<html>not json</html>"))
    result = provider.fetch_series(_requests_by_vendor()[vendor])

    assert result.status == "unavailable"
    assert result.observations == ()


@pytest.mark.parametrize("vendor", ["fmp", "fred", "polygon"])
def test_provider_reports_unavailable_without_api_key(vendor: str, monkeypatch: pytest.MonkeyPatch):
    for env in ("FMP_API_KEY", "FRED_API_KEY", "POLYGON_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    fixture_name = {
        "fmp": "fmp_price.json",
        "fred": "fred_cpi.json",
        "polygon": "polygon_daily.json",
    }[vendor]
    # A working opener proves the key check fires before any request is made.
    provider = _make_provider(
        vendor, opener=fixture_opener(load_fixture(fixture_name)), api_key=None
    )

    result = provider.fetch_series(_requests_by_vendor()[vendor])

    assert result.status == "unavailable"
    assert result.observations == ()


@pytest.mark.parametrize("vendor", ["fmp", "fred", "polygon"])
def test_provider_reports_unavailable_with_blank_api_key(vendor: str):
    fixture_name = {
        "fmp": "fmp_price.json",
        "fred": "fred_cpi.json",
        "polygon": "polygon_daily.json",
    }[vendor]
    provider = _make_provider(
        vendor,
        opener=fixture_opener(load_fixture(fixture_name)),
        api_key="   ",
    )

    result = provider.fetch_series(_requests_by_vendor()[vendor])

    assert result.status == "unavailable"
    assert result.observations == ()


def test_polygon_provider_registers_only_with_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    assert build_polygon_provider() is None

    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    provider = build_polygon_provider()
    assert provider is not None
    assert provider.provider_name == "polygon"
