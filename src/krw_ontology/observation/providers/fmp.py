"""FMP observation adapter: daily close history and quarterly TTM ratios.

Collection-only sibling of the market snapshot doctrine: bounded payload
(4 MB), 10 second timeout, redirects rejected, and every external failure
collapses to ``status="unavailable"`` without raising. Values are
advisory-only research context, never filing evidence or strong-claim
support.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
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

_FMP_BASE_URL = "https://financialmodelingprep.com/stable"

# Closed series registry: one series_key resolves to exactly one endpoint and
# response field. Field names reuse the /stable/ratios-ttm contract already
# exercised by the market snapshot router (krw-agnet services runtime).
_PRICE_SERIES_FIELDS = {"price_close_usd_daily": "close"}
_RATIO_SERIES_FIELDS = {
    "pe_ttm_quarterly": "priceToEarningsRatioTTM",
    "pb_ttm_quarterly": "priceToBookRatioTTM",
}


class FmpHistoryProvider:
    """Fixed FMP history adapter behind the ObservationProvider port."""

    provider_name = "fmp"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        opener: Callable[..., Any] = open_without_redirects,
        timeout_seconds: float = TIMEOUT_SECONDS,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError("FMP observation timeout must be within 0..30 seconds")
        self._api_key = (api_key if api_key is not None else os.getenv("FMP_API_KEY", "")).strip()
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
        field = _PRICE_SERIES_FIELDS.get(request.series_key)
        endpoint = "historical-price-full"
        if field is None:
            field = _RATIO_SERIES_FIELDS.get(request.series_key)
            endpoint = "ratios-ttm"
        if field is None or not self._api_key:
            return self._unavailable(request)

        params: dict[str, str] = {"symbol": request.provider_series_id, "apikey": self._api_key}
        if endpoint == "historical-price-full":
            if request.start:
                params["from"] = request.start
            if request.end:
                params["to"] = request.end
        else:
            params["period"] = "quarter"

        url = f"{_FMP_BASE_URL}/{endpoint}?{urlencode(params)}"
        http_request = Request(url, headers={"Accept": "application/json"}, method="GET")
        with self._opener(http_request, timeout=self._timeout_seconds) as response:
            body = read_bounded(response, max_bytes=MAX_RESPONSE_BYTES)
        decoded = json.loads(body.decode("utf-8"))

        provenance = {
            "endpoint": endpoint,
            "params_hash": params_hash(params),
            "fetched_at": iso_utc_now(self._utc_now),
        }
        if endpoint == "historical-price-full":
            observations = self._parse_history(decoded, request, field, provenance)
        else:
            observations = self._parse_ratios(decoded, request, field, provenance)
        if not observations:
            return self._unavailable(request)
        return SeriesFetchResult(
            series_key=request.series_key,
            provider=self.provider_name,
            observations=tuple(observations),
            status="available",
        )

    def _parse_history(
        self,
        decoded: Any,
        request: SeriesFetchRequest,
        field: str,
        provenance: dict[str, str],
    ) -> list[RawObservation]:
        if not isinstance(decoded, dict):
            return []
        symbol = decoded.get("symbol")
        if symbol is not None and symbol != request.provider_series_id:
            return []
        rows = decoded.get("historical")
        if not isinstance(rows, list):
            return []
        observations: list[RawObservation] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            date = row.get("date")
            value = safe_observation_value(row.get(field))
            if not isinstance(date, str) or not date or value is None:
                continue
            observations.append(
                RawObservation(
                    series_key=request.series_key,
                    phenomenon_time=date,
                    value=value,
                    result_time=None,
                    vintage=None,
                    provenance=dict(provenance),
                )
            )
        return observations

    def _parse_ratios(
        self,
        decoded: Any,
        request: SeriesFetchRequest,
        field: str,
        provenance: dict[str, str],
    ) -> list[RawObservation]:
        if not isinstance(decoded, list):
            return []
        observations: list[RawObservation] = []
        for row in decoded:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol")
            if symbol is not None and symbol != request.provider_series_id:
                continue
            date = row.get("date")
            value = safe_observation_value(row.get(field))
            if not isinstance(date, str) or not date or value is None:
                continue
            observations.append(
                RawObservation(
                    series_key=request.series_key,
                    phenomenon_time=date,
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
