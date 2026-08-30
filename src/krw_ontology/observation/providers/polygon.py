"""Polygon observation adapter: daily OHLCV aggregates (optional provider).

Registered only when ``POLYGON_API_KEY`` is configured (use
``build_polygon_provider``). Millisecond epoch timestamps are converted to
ISO calendar dates in UTC. Every external failure collapses to
``status="unavailable"`` without raising. Values are advisory-only market
context, never filing evidence.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request

from krw_ontology.observation.ports import (
    RawObservation,
    SeriesFetchRequest,
    SeriesFetchResult,
)

from ._common import (
    MAX_RESPONSE_BYTES,
    MAX_TIMEOUT_SECONDS,
    TIMEOUT_SECONDS,
    iso_utc_now,
    open_without_redirects,
    params_hash,
    read_bounded,
    safe_observation_value,
)

_POLYGON_BASE_URL = "https://api.polygon.io/v2"
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,31}$")
_DAILY_CLOSE_SERIES = "price_close_usd_daily"


class PolygonDailyProvider:
    """Fixed Polygon daily-aggregates adapter behind the ObservationProvider port."""

    provider_name = "polygon"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        opener: Callable[..., Any] = open_without_redirects,
        timeout_seconds: float = TIMEOUT_SECONDS,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError("Polygon observation timeout must be within 0..30 seconds")
        self._api_key = (
            api_key if api_key is not None else os.getenv("POLYGON_API_KEY", "")
        ).strip()
        self._opener = opener
        self._timeout_seconds = timeout_seconds
        self._utc_now = utc_now

    def fetch_series(self, request: SeriesFetchRequest) -> SeriesFetchResult:
        try:
            return self._fetch(request)
        # Transport (OSError family: URLError/HTTPError/timeout) plus bounded
        # read and JSON/decode failures (ValueError family) stay unavailable;
        # programmer errors (TypeError/NameError/...) must surface in tests.
        except (OSError, ValueError):
            return self._unavailable(request)

    def _fetch(self, request: SeriesFetchRequest) -> SeriesFetchResult:
        if request.series_key != _DAILY_CLOSE_SERIES:
            return self._unavailable(request)
        if not self._api_key:
            return self._unavailable(request)
        ticker = request.provider_series_id
        if not isinstance(ticker, str) or not _TICKER_PATTERN.match(ticker):
            return self._unavailable(request)

        clock = self._utc_now or (lambda: datetime.now(UTC))
        start = request.start or "1970-01-01"
        end = request.end or clock().astimezone(UTC).date().isoformat()
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": "50000",
            "apiKey": self._api_key,
        }
        endpoint = "aggs/ticker/range/1/day"
        url = f"{_POLYGON_BASE_URL}/aggs/ticker/{ticker}/range/1/day/{start}/{end}?{urlencode(params)}"
        http_request = Request(url, headers={"Accept": "application/json"}, method="GET")
        with self._opener(http_request, timeout=self._timeout_seconds) as response:
            body = read_bounded(response, max_bytes=MAX_RESPONSE_BYTES)
        decoded = json.loads(body.decode("utf-8"))

        provenance = {
            "endpoint": endpoint,
            "params_hash": params_hash(params),
            "fetched_at": iso_utc_now(self._utc_now),
        }
        observations = self._parse_results(decoded, request, provenance)
        if not observations:
            return self._unavailable(request)
        return SeriesFetchResult(
            series_key=request.series_key,
            provider=self.provider_name,
            observations=tuple(observations),
            status="available",
        )

    def _parse_results(
        self,
        decoded: Any,
        request: SeriesFetchRequest,
        provenance: dict[str, str],
    ) -> list[RawObservation]:
        rows = decoded.get("results") if isinstance(decoded, dict) else None
        if not isinstance(rows, list):
            return []
        observations: list[RawObservation] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            timestamp = row.get("t")
            value = safe_observation_value(row.get("c"))
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                continue
            try:
                phenomenon_date = datetime.fromtimestamp(float(timestamp) / 1000.0, UTC)
            except (OverflowError, OSError, ValueError):
                continue
            if value is None:
                continue
            observations.append(
                RawObservation(
                    series_key=request.series_key,
                    phenomenon_time=phenomenon_date.date().isoformat(),
                    value=value,
                    result_time=None,
                    vintage=None,
                    provenance=dict(provenance),
                )
            )
        return observations

    def _unavailable(self, request: SeriesFetchRequest) -> SeriesFetchResult:
        return SeriesFetchResult(
            series_key=request.series_key,
            provider=self.provider_name,
            observations=(),
            status="unavailable",
        )


def build_polygon_provider(**kwargs: Any) -> PolygonDailyProvider | None:
    """Register the Polygon adapter only when POLYGON_API_KEY is configured."""

    if not os.getenv("POLYGON_API_KEY", "").strip():
        return None
    return PolygonDailyProvider(**kwargs)
