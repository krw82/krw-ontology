"""FRED observation adapter: series observations and ALFRED vintages.

Fetches ``fred/series/observations``; with ``include_vintages=True`` the
request becomes the ALFRED real-time view (real-time window plus
``vintage_dates``) so first releases and revisions coexist as separate
RawObservation rows. The "." missing-value sentinel is skipped, never stored.
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
# FRED documented default real-time bounds; passing them explicitly selects
# the ALFRED all-vintages window (every known revision row, real-time tagged).
_REALTIME_EARLIEST = "1776-07-04"
_REALTIME_LATEST = "9999-12-31"


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
        except Exception:  # noqa: BLE001 - external failures never cross the port
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
        if self._include_vintages:
            # ALFRED vintage view: the real-time window spans the requested
            # observation window, plus vintage_dates at the window boundaries.
            params["realtime_start"] = request.start or _REALTIME_EARLIEST
            params["realtime_end"] = request.end or _REALTIME_LATEST
            if request.start and request.end:
                params["vintage_dates"] = f"{request.start},{request.end}"

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
