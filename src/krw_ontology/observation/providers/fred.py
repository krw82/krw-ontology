"""FRED observation adapter: series observations and ALFRED vintages.

Fetches ``fred/series/observations``; with ``include_vintages=True`` the
adapter requests the ALFRED all-vintages view (no real-time or
``vintage_dates`` bounds are sent) and applies the requested observation
window client-side, so first releases and revisions coexist as separate
RawObservation rows — even when a first release was published before
``request.start``. The "." missing-value sentinel is skipped, never stored.
Every external failure collapses to ``status="unavailable"`` without raising.
Values are advisory-only macro context, never filing evidence.
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

_FRED_BASE_URL = "https://api.stlouisfed.org/fred"
_MISSING_VALUE_SENTINELS = frozenset({".", ""})


class FredProvider:
    """Fixed FRED/ALFRED adapter behind the ObservationProvider port."""

    provider_name = "fred"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        include_vintages: bool = False,
        opener: Callable[..., Any] = open_without_redirects,
        timeout_seconds: float = TIMEOUT_SECONDS,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError("FRED observation timeout must be within 0..30 seconds")
        self._api_key = (api_key if api_key is not None else os.getenv("FRED_API_KEY", "")).strip()
        self._include_vintages = include_vintages
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
        if not self._api_key:
            return self._unavailable(request)

        params: dict[str, str] = {
            "series_id": request.provider_series_id,
            "api_key": self._api_key,
            "file_type": "json",
        }
        if request.start:
            params["observation_start"] = request.start
        if request.end:
            params["observation_end"] = request.end
        # Vintage mode deliberately sends NO realtime_start/realtime_end and
        # NO vintage_dates: the real-time window is not the observation
        # window, so bounding it by request.start would silently exclude first
        # releases published before the window, and vintage_dates boundary
        # values would drop intermediate revisions. All vintages are fetched
        # and the observation window is applied client-side below.

        endpoint = "series/observations"
        url = f"{_FRED_BASE_URL}/{endpoint}?{urlencode(params)}"
        http_request = Request(url, headers={"Accept": "application/json"}, method="GET")
        with self._opener(http_request, timeout=self._timeout_seconds) as response:
            body = read_bounded(response, max_bytes=MAX_RESPONSE_BYTES)
        decoded = json.loads(body.decode("utf-8"))

        provenance = {
            "endpoint": endpoint,
            "params_hash": params_hash(params),
            "fetched_at": iso_utc_now(self._utc_now),
        }
        observations = self._parse_observations(decoded, request, provenance)
        if not observations:
            return self._unavailable(request)
        return SeriesFetchResult(
            series_key=request.series_key,
            provider=self.provider_name,
            observations=tuple(observations),
            status="available",
        )

    def _parse_observations(
        self,
        decoded: Any,
        request: SeriesFetchRequest,
        provenance: dict[str, str],
    ) -> list[RawObservation]:
        rows = decoded.get("observations") if isinstance(decoded, dict) else None
        if not isinstance(rows, list):
            return []
        observations: list[RawObservation] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            date = row.get("date")
            raw_value = row.get("value")
            if not isinstance(date, str) or not date:
                continue
            if isinstance(raw_value, str) and raw_value.strip() in _MISSING_VALUE_SENTINELS:
                continue  # FRED missing sentinel: skipped observation, never stored
            value = safe_observation_value(raw_value)
            if value is None:
                continue
            if self._include_vintages and not self._in_observation_window(date, request):
                # Vintage mode filters client-side on the phenomenon_time only;
                # a first release published before request.start is KEPT
                # because its phenomenon_time is inside the window.
                continue
            vintage = self._vintage_of(row)
            observations.append(
                RawObservation(
                    series_key=request.series_key,
                    phenomenon_time=date,
                    value=value,
                    result_time=vintage,
                    vintage=vintage,
                    provenance=dict(provenance),
                )
            )
        return observations

    @staticmethod
    def _in_observation_window(phenomenon_time: str, request: SeriesFetchRequest) -> bool:
        """ISO date window check on the phenomenon_time only (never vintages)."""

        if request.start and phenomenon_time < request.start:
            return False
        if request.end and phenomenon_time > request.end:
            return False
        return True

    def _vintage_of(self, row: dict[str, Any]) -> str | None:
        """Resolve the vintage date: explicit ``vintage`` key first, else the
        ALFRED ``realtime_start`` field when running the vintage view."""

        vintage = row.get("vintage")
        if isinstance(vintage, str) and vintage:
            return vintage
        if self._include_vintages:
            realtime_start = row.get("realtime_start")
            if isinstance(realtime_start, str) and realtime_start:
                return realtime_start
        return None

    def _unavailable(self, request: SeriesFetchRequest) -> SeriesFetchResult:
        return SeriesFetchResult(
            series_key=request.series_key,
            provider=self.provider_name,
            observations=(),
            status="unavailable",
        )
