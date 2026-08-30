"""Batch builder for observations.sqlite: fetch results → immutable store.

``build_observations_store`` is a pure function over B1 ``SeriesFetchResult``
objects: no provider imports, no network, no clock reads (the build timestamp
is injectable).  The CLI collection step (``krw-ontology observation build``)
lives in ``collect_observations`` + the thin CLI wrapper; the main release
build never calls it, so releases stay network-free — the store is collected
ahead of time and carried into releases when present.

Supersede semantics: for every (series_key, phenomenon_time) group with more
than one vintage, each vintage is linked to its predecessor via an
``observation_revisions`` edge with ``revision_kind='revision'``; the first
vintage has no incoming edge.  All vintages are retained as history while the
query layer resolves the latest ``result_time``/``vintage`` as current.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from krw_ontology.observation.ports import (
    ObservationProvider,
    RawObservation,
    SeriesFetchRequest,
    SeriesFetchResult,
)
from krw_ontology.observation.seed import SeriesDefinition
from krw_ontology.observation.store import (
    OBSERVATIONS_BUILDER_VERSION,
    OBSERVATIONS_SCHEMA_VERSION,
    VINTAGE_NULL_SENTINEL,
    cleanup_sqlite_sidecars,
    create_observations_schema,
    iso_utc_observation_now,
    observation_id_for,
    observation_sort_key,
    temporary_sqlite_path,
    verify_observations_schema,
    write_observation_metadata,
)

# Seed family → provider request series key.  The B1 adapters keep a closed
# per-endpoint series registry; the seed describes served families.  This is
# the only place the two vocabularies are bridged (macro/FRED families use
# their seed series_key directly and identify via provider_series_id).
PROVIDER_REQUEST_SERIES_KEY: dict[str, str] = {
    "price_close": "price_close_usd_daily",
    "trailing_pe_ttm": "pe_ttm_quarterly",
    "price_to_book_ttm": "pb_ttm_quarterly",
    "polygon_ohlcv": "price_close_usd_daily",
}

_OHLCV_PROVENANCE_FIELDS = ("open", "high", "low", "volume")
_PRICE_DOMAIN = "price"

# Canonical market-symbol charset: uppercase A-Z and 0-9, with "." and "-"
# allowed after an alphanumeric first character. Tickers are embedded in
# per-ticker store keys ("family|ticker"), so the "|" separator (and every
# other symbol) must be rejected.
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]*$")


def normalize_ticker(raw: str) -> str:
    """Normalize and validate one equity ticker for per-ticker series keys.

    The input is trimmed and upper-cased first (``aapl`` → ``AAPL``), then
    must match the canonical market-symbol charset above. Anything else —
    ``A|B`` (the series-key separator), empty strings, ``BRK B`` — raises
    ValueError so a malformed ticker can never forge or split a store key.
    """

    ticker = str(raw or "").strip().upper()
    if not _TICKER_PATTERN.match(ticker):
        raise ValueError(f"invalid_ticker:{raw!r}")
    return ticker


def assert_provider_bridge_covers_seed(seed: Mapping[str, SeriesDefinition]) -> None:
    """Fail fast when a per-ticker seed family lacks a provider request key.

    A family missing from PROVIDER_REQUEST_SERIES_KEY would silently degrade
    to unavailable on every collection run, so the mismatch is a hard build
    error listing the offending families.
    """

    missing = sorted(
        family
        for family, definition in seed.items()
        if definition.is_per_ticker and family not in PROVIDER_REQUEST_SERIES_KEY
    )
    if missing:
        raise ValueError("provider_bridge_missing_families:" + ",".join(missing))


@dataclass(frozen=True)
class ObservationsBuildResult:
    path: Path
    fetched_at: str
    counts: Mapping[str, int]
    status_by_series: Mapping[str, str]
    verification: Mapping[str, Any]


@dataclass(frozen=True)
class _SeriesRow:
    """One concrete catalog row to materialize."""

    series_key: str
    definition: SeriesDefinition
    ticker: str | None
    provider_series_id: str
    is_per_ticker: bool


def _per_ticker_series_key(family: str, ticker: str) -> str:
    return f"{family}|{ticker}"


def _resolve_series_row(series_key: str, seed: Mapping[str, SeriesDefinition]) -> _SeriesRow:
    definition = seed.get(series_key)
    if definition is not None and not definition.is_per_ticker:
        return _SeriesRow(
            series_key=series_key,
            definition=definition,
            ticker=definition.ticker,
            provider_series_id=definition.provider_series_id,
            is_per_ticker=False,
        )
    family, separator, ticker = series_key.partition("|")
    if separator and ticker:
        template = seed.get(family)
        if template is not None and template.is_per_ticker:
            return _SeriesRow(
                series_key=series_key,
                definition=template,
                ticker=ticker,
                provider_series_id=ticker,
                is_per_ticker=True,
            )
    raise ValueError(f"unknown_series:{series_key}")


def _observation_order(observation: RawObservation) -> tuple[str, str]:
    """Shared latest-vintage ordering (store.observation_sort_key).

    The SAME ordering must crown the builder's artifacts (supersede chains,
    release_events.latest_value, the OHLCV current view) and the query
    layer's latest resolution; any divergence here would let the builder and
    the served points disagree on which vintage is current.
    """

    return observation_sort_key(observation.result_time, observation.vintage)


def _ohlcv_value(provenance: Mapping[str, Any], field: str) -> float | None:
    raw = provenance.get(field)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value


def build_observations_store(
    target_path: Path | str,
    seed: Mapping[str, SeriesDefinition],
    fetch_results: Sequence[SeriesFetchResult],
    *,
    fetched_at: str | None = None,
) -> ObservationsBuildResult:
    """Materialize an immutable observations store from fetch results.

    Pure with respect to the outside world: providers are never imported or
    called, unavailable results simply contribute catalog status (never rows),
    and every series_key must resolve against the seed (macro families
    directly, per-ticker families as ``family|ticker`` instances).
    """

    resolved_target = Path(target_path).expanduser().resolve()
    built_at = fetched_at or iso_utc_observation_now()

    status_by_series: dict[str, str] = {}
    rows_by_series: dict[str, list[RawObservation]] = {}
    catalog_rows: dict[str, _SeriesRow] = {}

    # Static macro families always appear in the catalog so B4/B5 can serve
    # explicit "unavailable" status even when collection never ran.
    for series_key, definition in sorted(seed.items()):
        if not definition.is_per_ticker:
            catalog_rows[series_key] = _SeriesRow(
                series_key=series_key,
                definition=definition,
                ticker=definition.ticker,
                provider_series_id=definition.provider_series_id,
                is_per_ticker=False,
            )
            status_by_series[series_key] = "unavailable"

    for result in fetch_results:
        series_row = _resolve_series_row(result.series_key, seed)
        catalog_rows.setdefault(result.series_key, series_row)
        status_by_series[result.series_key] = result.status
        if result.status != "available":
            continue
        rows = [
            observation
            for observation in result.observations
            if observation.series_key == result.series_key
            and observation.value is not None
            and observation.phenomenon_time
        ]
        rows_by_series.setdefault(result.series_key, []).extend(rows)

    resolved_target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = temporary_sqlite_path(resolved_target)
    cleanup_sqlite_sidecars(tmp_path)
    try:
        with sqlite3.connect(tmp_path) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            create_observations_schema(conn)
            counts = _write_store_rows(
                conn,
                catalog_rows=catalog_rows,
                rows_by_series=rows_by_series,
                status_by_series=status_by_series,
                built_at=built_at,
            )
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.replace(tmp_path, resolved_target)
    finally:
        cleanup_sqlite_sidecars(tmp_path)

    verification = verify_observations_schema(resolved_target)
    if not verification.get("ok"):
        resolved_target.unlink(missing_ok=True)
        raise RuntimeError(
            "observations store failed verification: "
            + ", ".join(str(error) for error in verification.get("errors") or [])
        )
    return ObservationsBuildResult(
        path=resolved_target,
        fetched_at=built_at,
        counts=verification.get("counts") or counts,
        status_by_series=dict(status_by_series),
        verification=verification,
    )


def _write_store_rows(
    conn: sqlite3.Connection,
    *,
    catalog_rows: Mapping[str, _SeriesRow],
    rows_by_series: Mapping[str, list[RawObservation]],
    status_by_series: Mapping[str, str],
    built_at: str,
) -> dict[str, int]:
    for series_key, row in sorted(catalog_rows.items()):
        conn.execute(
            """
            INSERT OR REPLACE INTO series_catalog (
                series_key, domain, canonical_metric, unit, frequency,
                adjustment, factor, ticker, provider, provider_series_id,
                is_per_ticker, status, observation_count,
                first_phenomenon_time, last_phenomenon_time, last_result_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL)
            """,
            (
                series_key,
                row.definition.domain,
                row.definition.canonical_metric,
                row.definition.unit,
                row.definition.frequency,
                row.definition.adjustment,
                row.definition.factor,
                row.ticker,
                row.definition.provider,
                row.provider_series_id,
                1 if row.is_per_ticker else 0,
                status_by_series.get(series_key, "unavailable"),
            ),
        )

    observation_ids: dict[tuple[str, str, str], str] = {}
    revision_rows: list[tuple[str, str, str, str, str]] = []
    release_rows: list[
        tuple[str, str, str | None, str, str | None, str | None, float, float, int]
    ] = []
    ohlcv_rows: dict[tuple[str, str], tuple[Any, ...]] = {}

    for series_key in sorted(rows_by_series):
        rows = sorted(
            rows_by_series[series_key],
            key=lambda observation: (
                observation.phenomenon_time,
                _observation_order(observation),
            ),
        )
        catalog_row = catalog_rows[series_key]
        # Deduplicate the natural PK deterministically (last write wins).
        deduplicated: dict[tuple[str, str], RawObservation] = {}
        for observation in rows:
            deduplicated[
                (observation.phenomenon_time, observation.vintage or VINTAGE_NULL_SENTINEL)
            ] = observation

        grouped: dict[str, list[RawObservation]] = {}
        for (_phenomenon_time, _vintage), observation in sorted(deduplicated.items()):
            grouped.setdefault(_phenomenon_time, []).append(observation)

        first_time: str | None = None
        last_time: str | None = None
        last_result_time: str | None = None
        stored_count = 0

        for phenomenon_time in sorted(grouped):
            # Chain vintages under the shared ordering so the builder's
            # crowned "latest" (last element) is exactly the row the query
            # layer's latest resolution serves.
            vintages = sorted(grouped[phenomenon_time], key=_observation_order)
            if first_time is None:
                first_time = phenomenon_time
            last_time = phenomenon_time
            prior_observation_id: str | None = None
            first_value: float | None = None
            latest_value: float | None = None
            first_result_time: str | None = None
            latest_result_time: str | None = None
            for observation in vintages:
                vintage_sentinel = observation.vintage or VINTAGE_NULL_SENTINEL
                observation_id = observation_id_for(
                    series_key, phenomenon_time, observation.vintage
                )
                observation_ids[(series_key, phenomenon_time, vintage_sentinel)] = observation_id
                conn.execute(
                    """
                    INSERT OR REPLACE INTO observations (
                        observation_id, series_key, phenomenon_time, value,
                        result_time, vintage, provenance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation_id,
                        series_key,
                        phenomenon_time,
                        float(observation.value or 0.0),
                        observation.result_time,
                        vintage_sentinel,
                        json.dumps(
                            observation.provenance,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                    ),
                )
                stored_count += 1
                if first_value is None:
                    first_value = float(observation.value or 0.0)
                    first_result_time = observation.result_time
                latest_value = float(observation.value or 0.0)
                latest_result_time = observation.result_time
                if prior_observation_id is not None:
                    revision_rows.append(
                        (
                            series_key,
                            phenomenon_time,
                            observation_id,
                            prior_observation_id,
                            "revision",
                        )
                    )
                prior_observation_id = observation_id
            if latest_value is not None and catalog_row.definition.domain == "macro":
                # P1 release events cover macro release dates only; daily
                # price bars are not announcements.
                release_rows.append(
                    (
                        series_key,
                        catalog_row.definition.canonical_metric,
                        catalog_row.definition.factor,
                        phenomenon_time,
                        first_result_time,
                        latest_result_time,
                        float(first_value or 0.0),
                        latest_value,
                        len(vintages) - 1,
                    )
                )

            # OHLCV current view: latest vintage of price-domain series only.
            if catalog_row.definition.domain == _PRICE_DOMAIN and catalog_row.ticker:
                latest_observation = vintages[-1]
                trade_date = latest_observation.phenomenon_time
                ohlcv_rows[(catalog_row.ticker, trade_date)] = (
                    catalog_row.ticker,
                    trade_date,
                    _ohlcv_value(latest_observation.provenance, "open"),
                    _ohlcv_value(latest_observation.provenance, "high"),
                    _ohlcv_value(latest_observation.provenance, "low"),
                    float(latest_observation.value or 0.0),
                    _ohlcv_value(latest_observation.provenance, "volume"),
                    series_key,
                    observation_id_for(series_key, trade_date, latest_observation.vintage),
                )
            if latest_result_time and (
                last_result_time is None or latest_result_time > last_result_time
            ):
                last_result_time = latest_result_time

        conn.execute(
            """
            UPDATE series_catalog
            SET observation_count = ?,
                first_phenomenon_time = ?,
                last_phenomenon_time = ?,
                last_result_time = ?
            WHERE series_key = ?
            """,
            (stored_count, first_time, last_time, last_result_time, series_key),
        )

    conn.executemany(
        """
        INSERT OR REPLACE INTO observation_revisions (
            series_key, phenomenon_time, new_observation_id,
            prior_observation_id, revision_kind
        ) VALUES (?, ?, ?, ?, ?)
        """,
        revision_rows,
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO release_events (
            series_key, canonical_metric, factor, phenomenon_time,
            release_time, latest_time, first_value, latest_value,
            revision_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        release_rows,
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO ohlcv_observations (
            ticker, trade_date, open, high, low, close, volume,
            series_key, observation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [ohlcv_rows[key] for key in sorted(ohlcv_rows)],
    )

    counts = {
        "series": int(conn.execute("SELECT COUNT(*) FROM series_catalog").fetchone()[0]),
        "observations": int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]),
        "ohlcv_observations": int(
            conn.execute("SELECT COUNT(*) FROM ohlcv_observations").fetchone()[0]
        ),
        "observation_revisions": int(
            conn.execute("SELECT COUNT(*) FROM observation_revisions").fetchone()[0]
        ),
        "release_events": int(conn.execute("SELECT COUNT(*) FROM release_events").fetchone()[0]),
    }
    write_observation_metadata(
        conn,
        {
            "schema_version": OBSERVATIONS_SCHEMA_VERSION,
            "builder_version": OBSERVATIONS_BUILDER_VERSION,
            "built_at": built_at,
            "counts": counts,
            "series_status": dict(status_by_series),
        },
    )
    return counts


# ---------------------------------------------------------------------------
# Collection step (CLI-only; the release build itself never collects).
# ---------------------------------------------------------------------------


def _rekey_result(result: SeriesFetchResult, series_key: str) -> SeriesFetchResult:
    observations = tuple(
        replace(observation, series_key=series_key) for observation in result.observations
    )
    return replace(result, series_key=series_key, observations=observations)


def collect_observations(
    seed: Mapping[str, SeriesDefinition],
    *,
    providers: Sequence[ObservationProvider],
    tickers: Sequence[str] = (),
    start: str | None = None,
    end: str | None = None,
) -> list[SeriesFetchResult]:
    """Fetch every seed series from injected providers (never network-free code).

    Providers are supplied by the caller (CLI constructs them from ambient
    credentials; tests inject fixtures).  Series whose provider is missing
    collapse to ``status='unavailable'`` results so their catalog rows exist
    without observations, matching the B1 degradation doctrine.  Malformed
    tickers and seed/bridge mismatches fail fast instead of degrading.
    """

    assert_provider_bridge_covers_seed(seed)
    providers_by_name = {provider.provider_name: provider for provider in providers}
    normalized_tickers = [normalize_ticker(ticker) for ticker in tickers]

    results: list[SeriesFetchResult] = []
    for series_key, definition in sorted(seed.items()):
        if definition.is_per_ticker:
            continue
        provider = providers_by_name.get(definition.provider)
        if provider is None:
            results.append(
                SeriesFetchResult(
                    series_key=series_key,
                    provider=definition.provider,
                    observations=(),
                    status="unavailable",
                )
            )
            continue
        request = SeriesFetchRequest(
            series_key=series_key,
            provider_series_id=definition.provider_series_id,
            ticker=None,
            start=start,
            end=end,
        )
        results.append(provider.fetch_series(request))

    for ticker in normalized_tickers:
        for family, definition in sorted(seed.items()):
            if not definition.is_per_ticker:
                continue
            provider = providers_by_name.get(definition.provider)
            store_series_key = _per_ticker_series_key(family, ticker)
            if provider is None:
                results.append(
                    SeriesFetchResult(
                        series_key=store_series_key,
                        provider=definition.provider,
                        observations=(),
                        status="unavailable",
                    )
                )
                continue
            request_key = PROVIDER_REQUEST_SERIES_KEY.get(family, family)
            request = SeriesFetchRequest(
                series_key=request_key,
                provider_series_id=ticker,
                ticker=ticker,
                start=start,
                end=end,
            )
            results.append(_rekey_result(provider.fetch_series(request), store_series_key))
    return results


__all__ = [
    "ObservationsBuildResult",
    "PROVIDER_REQUEST_SERIES_KEY",
    "assert_provider_bridge_covers_seed",
    "build_observations_store",
    "collect_observations",
    "normalize_ticker",
]
