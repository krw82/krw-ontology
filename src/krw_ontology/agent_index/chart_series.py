"""Chart-ready metric series sidecar for v3 release indexes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krw_ontology.agent_index.semantic_identity import (
    project_local_identity,
    project_object_identity,
)
from krw_ontology.observation.store import (
    OBSERVATIONS_RELATIVE_PATH,
    VINTAGE_ORDER_SQL,
)

CHART_SERIES_SCHEMA_VERSION = "krw-ontology-chart-series/v1"
CHART_SERIES_BUILDER_VERSION = "chart-series-builder/v3"
CHART_SERIES_RELATIVE_PATH = Path("indexes") / "chart_series.sqlite"
CHART_SERIES_TABLES = (
    "chart_series_metadata",
    "chart_series",
    "chart_series_points",
)

_PERIODIC_METRICS = {
    "adjusted_free_cash_flow",
    "capital_expenditures",
    "capex",
    "eps",
    "free_cash_flow",
    "gross_margin",
    "ma_cash_outflow",
    "ma_related_costs",
    "net_income",
    "net_sales",
    "operating_cash_flow",
    "operating_income",
    "operating_margin",
    "research_and_development",
    "selling_general_and_admin",
    "revenue",
    "share_repurchase",
    "stock_based_compensation",
}
_POINT_IN_TIME_METRICS = {
    "cash_and_cash_equivalents",
    "cash_and_equivalents",
    "total_assets",
    "total_debt",
    "total_liabilities",
}
# Observation canonical metrics (B4): the 20 macro families of the P1 seed
# plus the monthly-aggregated per-ticker price/valuation families.  Macro
# series are ticker-less (the pack lookup serves them metric-gated); the
# per-ticker families ride the normal ticker gate.  Values are advisory_only
# market/macro context — never filing evidence.
MACRO_OBSERVATION_CHART_METRICS = frozenset(
    {
        "breakeven_inflation_10y",
        "cpi_yoy",
        "core_cpi_yoy",
        "core_pce_yoy",
        "dgs2_yield",
        "dgs10_yield",
        "dgs30_yield",
        "fed_funds_rate",
        "fed_funds_target_upper",
        "housing_starts",
        "industrial_production_index",
        "initial_jobless_claims",
        "nominal_gdp",
        "nonfarm_payrolls",
        "real_gdp",
        "retail_sales",
        "sp500_index",
        "treasury_10y2y_spread",
        "unemployment_rate",
        "vix",
    }
)
PER_TICKER_OBSERVATION_CHART_METRICS = frozenset(
    {
        # Daily ``last_price`` closes aggregated to month-end (see
        # ``_observation_period_bucket``): the monthly aggregation gets its own
        # canonical key so a daily price feed can never masquerade as a
        # filing-period series.
        "last_price_monthly",
        "price_to_book_ttm",
        "trailing_pe_ttm",
    }
)
OBSERVATION_CHART_METRICS = frozenset(
    MACRO_OBSERVATION_CHART_METRICS | PER_TICKER_OBSERVATION_CHART_METRICS
)
OBSERVATION_SOURCE_CLASS = "observation"
# The seed's per-ticker price family (canonical_metric ``last_price``) is
# projected under the monthly-aggregated key above.
OBSERVATION_PRICE_METRIC = "last_price"
OBSERVATION_PRICE_MONTHLY_METRIC = "last_price_monthly"
CHART_SAFE_CANONICAL_METRICS = frozenset(
    _PERIODIC_METRICS | _POINT_IN_TIME_METRICS | OBSERVATION_CHART_METRICS
)

# Period grammar: FY/CY annual, CY quarter, and (B4) CY monthly —
# ``CY2026M07``.  Monthly buckets REQUIRE the CY prefix and reject the FY
# prefix (fiscal+month is semantically invalid): the writer only ever emits
# ``CY{year}M{month}``.  Zero-padded months only; anything else is garbage.
_CHART_PERIOD_PATTERN = re.compile(
    r"^(?:(?:FY|CY)?(?:19|20)\d{2}(?:Q[1-4])?|CY(?:19|20)\d{2}M(?:0[1-9]|1[0-2]))$"
)


def is_valid_chart_period(period: str) -> bool:
    """Validate one chart-period string against the FY/CY/Q/M grammar."""

    return bool(_CHART_PERIOD_PATTERN.match(str(period or "").strip()))


_DEFAULT_METRICS = (
    "revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "free_cash_flow",
    "capital_expenditures",
    "cash_and_cash_equivalents",
    "total_debt",
)


@dataclass(frozen=True)
class ChartSeriesBuildResult:
    path: Path
    counts: Mapping[str, int]
    verification: Mapping[str, Any]
    elapsed_ms: int


def build_chart_series_index(
    release_root: Path | str,
    *,
    shard_manifest_path: Path | str | None = None,
    output_path: Path | str | None = None,
    release_id: str | None = None,
    source_manifest_hash: str | None = None,
    observations_path: Path | str | None = None,
) -> ChartSeriesBuildResult:
    """Build indexes/chart_series.sqlite from company shard metric_lookup tables.

    When the release carries ``indexes/observations.sqlite`` (B3 store), its
    series are projected alongside the filing series with
    ``source_class = 'observation'``; an absent store is a no-op.
    """
    started_at = time.perf_counter()
    root = Path(release_root).expanduser().resolve()
    manifest_path = (
        Path(shard_manifest_path).expanduser().resolve()
        if shard_manifest_path is not None
        else root / "indexes" / "shard_manifest.json"
    )
    target_path = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else root / CHART_SERIES_RELATIVE_PATH
    )
    observations_store_path = (
        Path(observations_path).expanduser().resolve()
        if observations_path is not None
        else root / OBSERVATIONS_RELATIVE_PATH
    )
    shard_manifest = _read_json(manifest_path)
    shards = (
        shard_manifest.get("shards") if isinstance(shard_manifest.get("shards"), Mapping) else {}
    )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _temporary_sqlite_path(target_path)
    if tmp_path.exists():
        tmp_path.unlink()
    try:
        with sqlite3.connect(tmp_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            create_chart_series_schema(conn)
            series_points: dict[tuple[str, str], dict[str, Any]] = {}
            series_meta: dict[str, dict[str, Any]] = {}
            for ticker, entry in sorted(shards.items()):
                if not isinstance(entry, Mapping):
                    continue
                shard_path = _resolve_shard_path(root, entry)
                if not shard_path.is_file():
                    continue
                _collect_shard_chart_points(
                    shard_path,
                    ticker=str(ticker).upper(),
                    series_meta=series_meta,
                    series_points=series_points,
                )
            _collect_observation_chart_points(
                observations_store_path,
                series_meta=series_meta,
                series_points=series_points,
            )
            _write_chart_series_rows(conn, series_meta=series_meta, series_points=series_points)
            counts = {
                "series": _count(conn, "chart_series"),
                "points": _count(conn, "chart_series_points"),
                # Macro observation series carry an empty ticker sentinel;
                # they are not companies and must not inflate this count.
                "tickers": int(
                    conn.execute(
                        "SELECT COUNT(DISTINCT ticker) FROM chart_series WHERE ticker != ''"
                    ).fetchone()[0]
                ),
            }
            _write_metadata(
                conn,
                {
                    "schema_version": CHART_SERIES_SCHEMA_VERSION,
                    "builder_version": CHART_SERIES_BUILDER_VERSION,
                    "release_id": release_id,
                    "source_manifest_hash": source_manifest_hash,
                    "built_at": datetime.now(timezone.utc).isoformat(),
                    "counts": counts,
                },
            )
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.replace(tmp_path, target_path)
    finally:
        _cleanup_sqlite_sidecars(tmp_path)

    verification = verify_chart_series_index(target_path)
    if not verification.get("ok"):
        target_path.unlink(missing_ok=True)
        raise RuntimeError(
            "chart series failed verification: "
            + ", ".join(str(error) for error in verification.get("errors") or [])
        )
    return ChartSeriesBuildResult(
        path=target_path,
        counts=verification.get("counts") or {},
        verification=verification,
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
    )


def create_chart_series_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chart_series_metadata (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chart_series (
            series_key TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            canonical_metric TEXT NOT NULL,
            metric_name TEXT,
            label TEXT NOT NULL,
            unit TEXT,
            scope_kind TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            scope_label TEXT NOT NULL,
            period_type TEXT NOT NULL,
            basis TEXT NOT NULL,
            duration TEXT NOT NULL,
            source_class TEXT NOT NULL,
            statement_family TEXT NOT NULL,
            point_count INTEGER NOT NULL,
            first_period TEXT,
            last_period TEXT,
            first_sort_key INTEGER,
            last_sort_key INTEGER,
            quality_flags_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chart_series_points (
            series_key TEXT NOT NULL,
            ticker TEXT NOT NULL,
            period TEXT NOT NULL,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            period_sort_key INTEGER NOT NULL,
            document_type TEXT,
            document_period TEXT,
            source_document_id TEXT,
            source_sort_key INTEGER,
            value REAL NOT NULL,
            formatted_value TEXT,
            object_id TEXT NOT NULL,
            trace_status TEXT,
            metric_lineage_status TEXT,
            PRIMARY KEY (series_key, period),
            FOREIGN KEY (series_key) REFERENCES chart_series(series_key)
        );

        CREATE INDEX IF NOT EXISTS idx_chart_series_ticker_metric
            ON chart_series(ticker, canonical_metric, scope_kind, period_type);
        CREATE INDEX IF NOT EXISTS idx_chart_series_ticker_scope
            ON chart_series(ticker, scope_kind, scope_key);
        CREATE INDEX IF NOT EXISTS idx_chart_series_points_series_sort
            ON chart_series_points(series_key, period_sort_key);
        CREATE INDEX IF NOT EXISTS idx_chart_series_points_ticker_metric_period
            ON chart_series_points(ticker, period_sort_key);
        """
    )


def verify_chart_series_index(path: Path | str, *, deep: bool = True) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    metadata: dict[str, Any] = {}
    tables: list[str] = []
    if not resolved.exists():
        return {
            "ok": False,
            "errors": ["chart_series_missing"],
            "warnings": warnings,
            "path": str(resolved),
            "tables": tables,
            "metadata": metadata,
            "counts": counts,
            "verification_mode": "chart-series",
        }
    if not resolved.is_file():
        return {
            "ok": False,
            "errors": ["chart_series_not_file"],
            "warnings": warnings,
            "path": str(resolved),
            "tables": tables,
            "metadata": metadata,
            "counts": counts,
            "verification_mode": "chart-series",
        }
    try:
        with sqlite3.connect(resolved) as conn:
            conn.row_factory = sqlite3.Row
            tables = sorted(
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                ).fetchall()
            )
            table_set = set(tables)
            for table in CHART_SERIES_TABLES:
                if table not in table_set:
                    errors.append(f"table_missing:{table}")
            if not errors:
                metadata = read_chart_series_metadata(conn)
                if metadata.get("schema_version") != CHART_SERIES_SCHEMA_VERSION:
                    errors.append("schema_version_mismatch")
                if metadata.get("builder_version") != CHART_SERIES_BUILDER_VERSION:
                    errors.append("builder_version_mismatch")
                counts = {
                    "series": _count(conn, "chart_series"),
                    "points": _count(conn, "chart_series_points"),
                    "tickers": int(
                        conn.execute(
                            "SELECT COUNT(DISTINCT ticker) FROM chart_series WHERE ticker != ''"
                        ).fetchone()[0]
                    ),
                }
                if deep:
                    invalid_periods = sorted(
                        str(row[0])
                        for row in conn.execute(
                            "SELECT DISTINCT period FROM chart_series_points"
                        ).fetchall()
                        if not is_valid_chart_period(str(row[0]))
                    )
                    if invalid_periods:
                        errors.append(f"invalid_period:{','.join(invalid_periods[:5])}")
                    orphan_points = int(
                        conn.execute(
                            """
                            SELECT COUNT(*)
                            FROM chart_series_points AS point
                            LEFT JOIN chart_series AS series
                              ON series.series_key = point.series_key
                            WHERE series.series_key IS NULL
                            """
                        ).fetchone()[0]
                    )
                    if orphan_points:
                        errors.append(f"orphan_points:{orphan_points}")
                    mismatched_point_counts = int(
                        conn.execute(
                            """
                            SELECT COUNT(*)
                            FROM chart_series AS series
                            LEFT JOIN (
                                SELECT series_key, COUNT(*) AS point_count
                                FROM chart_series_points
                                GROUP BY series_key
                            ) AS actual
                              ON actual.series_key = series.series_key
                            WHERE series.point_count != COALESCE(actual.point_count, 0)
                            """
                        ).fetchone()[0]
                    )
                    if mismatched_point_counts:
                        errors.append(f"series_point_count_mismatch:{mismatched_point_counts}")
    except sqlite3.Error as exc:
        errors.append(f"sqlite_error:{exc}")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "path": str(resolved),
        "tables": tables,
        "metadata": metadata,
        "counts": counts,
        "verification_mode": "chart-series-deep" if deep else "chart-series-light",
    }


def read_chart_series_metadata(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value_json FROM chart_series_metadata").fetchall()
    metadata: dict[str, Any] = {}
    for row in rows:
        try:
            metadata[str(row["key"])] = json.loads(str(row["value_json"]))
        except (json.JSONDecodeError, TypeError):
            metadata[str(row["key"])] = row["value_json"]
    return metadata


def query_chart_series_pack(
    path: Path | str,
    *,
    question: str,
    tickers: Sequence[str],
    limit_series: int = 20,
    limit_points: int = 12,
) -> dict[str, Any] | None:
    """Return a metric_series_pack-shaped payload from the chart sidecar."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        return None
    ticker_values = [str(ticker).upper() for ticker in tickers if str(ticker or "").strip()]
    metric_candidates = _metric_candidates_for_question(question)
    macro_metric_candidates = [
        metric for metric in metric_candidates if metric in MACRO_OBSERVATION_CHART_METRICS
    ]
    # Macro observation series carry no ticker: a chart clause naming their
    # metric is enough to serve them.  Filing series and per-ticker
    # observation series (price/valuation) still require a ticker scope.
    if not ticker_values and not macro_metric_candidates:
        return None
    scope_candidates = _scope_candidates_for_question(question)
    dimension_requested = _dimension_series_requested(question, scope_candidates)
    try:
        with sqlite3.connect(resolved) as conn:
            conn.row_factory = sqlite3.Row
            where: list[str] = []
            params: list[Any] = []
            if ticker_values:
                ticker_clause = "ticker IN (" + ",".join("?" for _ in ticker_values) + ")"
                params.extend(ticker_values)
                if macro_metric_candidates:
                    # Observation macro rows pass the ticker gate only when
                    # the question explicitly names their metric.
                    ticker_clause = (
                        "("
                        + ticker_clause
                        + " OR (source_class = 'observation' AND canonical_metric IN ("
                        + ",".join("?" for _ in macro_metric_candidates)
                        + ")))"
                    )
                    params[len(ticker_values) : len(ticker_values)] = macro_metric_candidates
                where.append(ticker_clause)
            else:
                # Ticker-less questions are clamped to macro observation rows
                # ONLY: without a ticker scope, the metric clause must never
                # fan out to filing series of every company or to per-ticker
                # observation (price/valuation) rows of every ticker.
                where.append("(source_class = 'observation' AND ticker = '')")
            if metric_candidates:
                where.append(
                    "canonical_metric IN (" + ",".join("?" for _ in metric_candidates) + ")"
                )
                params.extend(metric_candidates)
            rows = conn.execute(
                f"""
                SELECT *
                FROM chart_series
                WHERE {" AND ".join(where)}
                LIMIT ?
                """,
                [*params, max(40, min(max(int(limit_series) * 8, int(limit_series)), 160))],
            ).fetchall()
            rows = _select_chart_series_rows(
                rows,
                limit=max(1, min(int(limit_series), 20)),
                metric_candidates=metric_candidates,
                scope_candidates=scope_candidates,
                dimension_requested=dimension_requested,
            )
            if not rows and not metric_candidates:
                return None
            if not rows and metric_candidates:
                return None
            series: list[dict[str, Any]] = []
            for row in rows:
                points = _chart_series_points(
                    conn,
                    str(row["series_key"]),
                    limit=max(1, min(int(limit_points), 48)),
                )
                if not points:
                    continue
                series.append(
                    {
                        "series_key": row["series_key"],
                        "label": row["label"],
                        "ticker": row["ticker"],
                        "metric_role": "metric",
                        "metric_name": row["metric_name"],
                        "canonical_metric": row["canonical_metric"],
                        "unit": row["unit"],
                        "scope": {
                            "kind": row["scope_kind"],
                            "key": row["scope_key"],
                            "label": row["scope_label"],
                        },
                        "basis": row["basis"],
                        "duration": row["duration"],
                        "source_class": row["source_class"],
                        "statement_family": row["statement_family"],
                        "period_type": row["period_type"],
                        "points": points,
                        "periods": [point["period"] for point in points],
                        "point_count": len(points),
                    }
                )
            if not series:
                return None
            return {
                "mode": "chart_series_sidecar",
                "result_count": sum(series_item["point_count"] for series_item in series),
                "series": series,
                "roles": ["metric"],
                "quality": {
                    "chart_safe": True,
                    "source": "chart_series_sidecar",
                    "missing_parts": [],
                },
                "render_hints": {
                    "prefer_chart": True,
                    "prefer_indexed_axis": True,
                    "hide_raw_y_axis_amounts": True,
                    "do_not_requery_per_metric": True,
                },
                "diagnostics": {
                    "metric_candidates": metric_candidates,
                    "sidecar_schema_version": CHART_SERIES_SCHEMA_VERSION,
                },
            }
    except sqlite3.Error:
        return None


def chart_series_index_status(path: Path | str) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return {"available": False, "path": str(resolved), "reason": "missing"}
    verification = verify_chart_series_index(resolved, deep=False)
    return {
        "available": bool(verification.get("ok")),
        "path": str(resolved),
        "reason": None if verification.get("ok") else "verification_failed",
        "verification": verification,
    }


def names_macro_observation_metric(question: str) -> bool:
    """True when the question's metric clause names a macro observation metric.

    Macro observation series carry no ticker, so MCP-side attach paths use
    this to decide whether a ticker-less question may still serve a chart
    pack (e.g. a bare "CPI 추이" question).
    """

    return any(
        metric in MACRO_OBSERVATION_CHART_METRICS
        for metric in _metric_candidates_for_question(question)
    )


def _collect_shard_chart_points(
    shard_path: Path,
    *,
    ticker: str,
    series_meta: dict[str, dict[str, Any]],
    series_points: dict[tuple[str, str], dict[str, Any]],
) -> None:
    try:
        with sqlite3.connect(shard_path) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                ).fetchall()
            }
            if not {"metric_lookup", "objects"}.issubset(tables):
                return
            rows = conn.execute(
                """
                SELECT
                    metric_lookup.*,
                    objects.id AS object_id_from_objects,
                    objects.json AS object_json,
                    objects.text AS object_text,
                    objects.review_status AS review_status
                FROM metric_lookup
                JOIN objects ON objects.id = metric_lookup.object_id
                WHERE metric_lookup.value_text IS NOT NULL
                  AND metric_lookup.value_text != ''
                  AND metric_lookup.canonical_metric IS NOT NULL
                  AND metric_lookup.canonical_metric != ''
                  AND (objects.review_status IS NULL OR objects.review_status != 'rejected')
                ORDER BY metric_lookup.ticker, metric_lookup.period, metric_lookup.canonical_metric, metric_lookup.object_id
                """
            ).fetchall()
    except sqlite3.Error:
        return

    for row in rows:
        point = _chart_point_from_row(row, ticker=ticker)
        if point is None:
            continue
        series_key = point["series_key"]
        period = point["period"]
        series_meta.setdefault(series_key, point["series"])
        point_key = (series_key, period)
        existing = series_points.get(point_key)
        if existing is None or _point_quality_score(point) > _point_quality_score(existing):
            series_points[point_key] = point


def _collect_observation_chart_points(
    observations_path: Path,
    *,
    series_meta: dict[str, dict[str, Any]],
    series_points: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Project observations.sqlite series into chart rows (no-op when absent).

    Reads the store read-only and resolves each phenomenon time to its latest
    vintage under the store's shared ordering (``VINTAGE_ORDER_SQL``), so the
    chart projection and the store's served payloads can never crown
    different rows.  Served payloads stay vendor-scrubbed: only catalog
    metadata (canonical metric, unit, frequency, domain) and observation
    values/ids cross into the chart sidecar.
    """

    if not observations_path.is_file():
        return
    try:
        conn = sqlite3.connect(f"file:{observations_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return
    try:
        conn.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        if not {"series_catalog", "observations"}.issubset(tables):
            return
        rows = conn.execute(
            f"""
            SELECT current.series_key, current.phenomenon_time, current.value,
                   current.observation_id, catalog.domain, catalog.canonical_metric,
                   catalog.unit, catalog.frequency, catalog.ticker
            FROM observations AS current
            JOIN series_catalog AS catalog ON catalog.series_key = current.series_key
            WHERE current.observation_id = (
                SELECT prior.observation_id
                FROM observations AS prior
                WHERE prior.series_key = current.series_key
                  AND prior.phenomenon_time = current.phenomenon_time
                ORDER BY {VINTAGE_ORDER_SQL} DESC, prior.vintage DESC
                LIMIT 1
            )
            ORDER BY current.series_key, current.phenomenon_time
            """
        ).fetchall()
    except sqlite3.Error:
        return
    finally:
        conn.close()

    rows_by_series: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        rows_by_series.setdefault(str(row["series_key"]), []).append(row)
    for store_series_key, series_rows in sorted(rows_by_series.items()):
        _project_observation_series(
            store_series_key,
            series_rows,
            series_meta=series_meta,
            series_points=series_points,
        )


def _observation_period_bucket(frequency: str) -> str | None:
    """Map a catalog frequency onto a chart period bucket.

    P1 chart axis: daily and weekly phenomenon dates do not fit the filing
    period grammar (FY/CY annual + CY quarter), so they are AGGREGATED TO
    MONTHLY points — one point per calendar month carrying the month-end
    (last observed date) reading.  Monthly series map one-to-one onto the
    same monthly axis; quarterly macro series keep the existing CY<year>Q<q>
    grammar.  This is a presentation-layer choice only: the observation store
    keeps every daily bar untouched.
    """

    normalized = str(frequency or "").strip().lower()
    if normalized in {"daily", "weekly", "monthly"}:
        return "monthly"
    if normalized == "quarterly":
        return "quarterly"
    return None


def _observation_period_info(phenomenon_time: str, bucket: str) -> dict[str, Any] | None:
    match = re.match(r"^((?:19|20)\d{2})-(\d{2})", str(phenomenon_time or ""))
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    if bucket == "quarterly":
        quarter = (month - 1) // 3 + 1
        period = f"CY{year}Q{quarter}"
        return {
            "period": period,
            "fiscal_year": year,
            "fiscal_quarter": quarter,
            "sort_key": _period_sort_key(period),
        }
    period = f"CY{year}M{month:02d}"
    return {
        "period": period,
        "fiscal_year": year,
        "fiscal_quarter": None,
        "sort_key": _period_sort_key(period),
    }


def _observation_chart_metric(domain: str, canonical_metric: str) -> str:
    """Chart-side canonical metric for one observation catalog entry."""

    if domain == "price" and canonical_metric == OBSERVATION_PRICE_METRIC:
        return OBSERVATION_PRICE_MONTHLY_METRIC
    return canonical_metric


def _project_observation_series(
    store_series_key: str,
    rows: Sequence[sqlite3.Row],
    *,
    series_meta: dict[str, dict[str, Any]],
    series_points: dict[tuple[str, str], dict[str, Any]],
) -> None:
    first = rows[0]
    domain = str(first["domain"] or "")
    canonical_metric = _observation_chart_metric(domain, str(first["canonical_metric"] or ""))
    # The allow-list stays the single chart gate: a future seed family is
    # invisible to charts until it is explicitly allow-listed here.
    if canonical_metric not in OBSERVATION_CHART_METRICS:
        return
    bucket = _observation_period_bucket(str(first["frequency"] or ""))
    if bucket is None:
        return
    ticker = str(first["ticker"] or "").strip().upper()
    unit = str(first["unit"] or "").strip() or None

    # Month-end aggregation: rows arrive phenomenon-time ascending, so the
    # last write per period bucket is the month's last observed reading.
    bucketed: dict[str, tuple[sqlite3.Row, dict[str, Any]]] = {}
    for row in rows:
        info = _observation_period_info(str(row["phenomenon_time"]), bucket)
        if info is None or not is_valid_chart_period(info["period"]):
            continue
        bucketed[info["period"]] = (row, info)
    if not bucketed:
        return

    period_type = "quarterly" if bucket == "quarterly" else "monthly"
    basis = "advisory_market_data"
    duration = "point_in_time" if domain in {"price", "valuation"} else "period"
    if ticker:
        scope_kind, scope_key, scope_label = "company_total", "company_total", "Company total"
    else:
        scope_kind, scope_key, scope_label = "macro", "macro", "Macro"
    series_key = "|".join(
        [
            ticker,
            canonical_metric,
            f"{scope_kind}:{scope_key}",
            unit or "unitless",
            period_type,
            basis,
            duration,
            OBSERVATION_SOURCE_CLASS,
        ]
    )
    label = _series_label(canonical_metric, scope_kind, scope_label)
    series_row = {
        "series_key": series_key,
        "ticker": ticker,
        "canonical_metric": canonical_metric,
        "metric_name": canonical_metric,
        "label": label,
        "unit": unit,
        "scope_kind": scope_kind,
        "scope_key": scope_key,
        "scope_label": scope_label,
        "period_type": period_type,
        "basis": basis,
        "duration": duration,
        "source_class": OBSERVATION_SOURCE_CLASS,
        "statement_family": OBSERVATION_SOURCE_CLASS,
        "quality_flags": ["advisory_only"],
    }
    for period, (row, info) in sorted(bucketed.items(), key=lambda item: item[1][1]["sort_key"]):
        value = _number(row["value"])
        if value is None:
            continue
        point = {
            "series_key": series_key,
            "period": period,
            "fiscal_year": info["fiscal_year"],
            "fiscal_quarter": info["fiscal_quarter"],
            "period_sort_key": info["sort_key"],
            "document_type": None,
            "document_period": str(row["phenomenon_time"]),
            "source_document_id": None,
            "source_sort_key": _period_sort_key(str(row["phenomenon_time"])),
            "value": value,
            "formatted_value": _format_value(value, unit),
            # observation_id pointer contract: the plan's evidence pointer for
            # observation series is the store's observation_id, verbatim.
            "object_id": str(row["observation_id"]),
            "trace_status": None,
            "metric_lineage_status": None,
            "series": series_row,
        }
        series_meta.setdefault(series_key, series_row)
        point_key = (series_key, period)
        existing = series_points.get(point_key)
        if existing is None or _point_quality_score(point) > _point_quality_score(existing):
            series_points[point_key] = point


def _chart_point_from_row(row: sqlite3.Row, *, ticker: str) -> dict[str, Any] | None:
    obj = _json_object(row["object_json"])
    value = _number(row["value_text"])
    if value is None:
        value = _number(obj.get("value"))
    if value is None:
        return None
    classification = _classify_metric(
        row["canonical_metric"],
        metric_name=row["metric_name"],
        text=" ".join(
            str(part or "")
            for part in (
                row["metric_alias_text"],
                row["metric_name"],
                row["text"],
                row["object_text"],
            )
        ),
    )
    if classification is None:
        return None
    period_info = _period_info(row, obj)
    if period_info is None:
        return None
    scope_kind, scope_key, scope_label = _scope(row)
    unit = str(row["unit"] or obj.get("unit") or "").strip() or None
    basis = _basis(classification["canonical_metric"], row, obj)
    duration = _duration(classification["canonical_metric"], row, obj)
    period_type = "quarterly" if period_info["fiscal_quarter"] else "annual"
    source_class = _source_class(classification["canonical_metric"], basis, row)
    statement_family = _statement_family(classification["canonical_metric"], source_class)
    series_key = "|".join(
        [
            ticker,
            classification["canonical_metric"],
            f"{scope_kind}:{scope_key}",
            unit or "unitless",
            period_type,
            basis,
            duration,
            source_class,
        ]
    )
    label = _series_label(classification["canonical_metric"], scope_kind, scope_label)
    document_period = str(row["period"] or obj.get("period") or "")
    point = {
        "series_key": series_key,
        "period": period_info["period"],
        "fiscal_year": period_info["fiscal_year"],
        "fiscal_quarter": period_info["fiscal_quarter"],
        "period_sort_key": period_info["sort_key"],
        "document_type": row["document_type"],
        "document_period": document_period,
        "source_document_id": (
            project_local_identity(obj["source_document_id"], ticker=ticker)
            if obj.get("source_document_id")
            else None
        ),
        "source_sort_key": _period_sort_key(document_period),
        "value": value,
        "formatted_value": _format_value(value, unit),
        "object_id": project_object_identity(
            row["object_id"],
            row["object_type"],
            ticker=ticker,
            payload=obj,
        ),
        "trace_status": row["trace_status"],
        "metric_lineage_status": row["metric_lineage_status"],
        "series": {
            "series_key": series_key,
            "ticker": ticker,
            "canonical_metric": classification["canonical_metric"],
            "metric_name": row["metric_name"],
            "label": label,
            "unit": unit,
            "scope_kind": scope_kind,
            "scope_key": scope_key,
            "scope_label": scope_label,
            "period_type": period_type,
            "basis": basis,
            "duration": duration,
            "source_class": source_class,
            "statement_family": statement_family,
            "quality_flags": classification["quality_flags"],
        },
    }
    return point


def _write_chart_series_rows(
    conn: sqlite3.Connection,
    *,
    series_meta: Mapping[str, Mapping[str, Any]],
    series_points: Mapping[tuple[str, str], Mapping[str, Any]],
) -> None:
    points_by_series: dict[str, list[Mapping[str, Any]]] = {}
    for point in series_points.values():
        points_by_series.setdefault(str(point["series_key"]), []).append(point)
    for series_key, meta in sorted(series_meta.items()):
        points = sorted(
            points_by_series.get(series_key, []),
            key=lambda point: (int(point["period_sort_key"]), str(point["period"])),
        )
        if not points:
            continue
        quality_flags = list(meta.get("quality_flags") or [])
        conn.execute(
            """
            INSERT INTO chart_series (
                series_key, ticker, canonical_metric, metric_name, label, unit,
                scope_kind, scope_key, scope_label, period_type, basis, duration,
                source_class, statement_family, point_count, first_period, last_period,
                first_sort_key, last_sort_key, quality_flags_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                series_key,
                meta.get("ticker"),
                meta.get("canonical_metric"),
                meta.get("metric_name"),
                meta.get("label"),
                meta.get("unit"),
                meta.get("scope_kind"),
                meta.get("scope_key"),
                meta.get("scope_label"),
                meta.get("period_type"),
                meta.get("basis"),
                meta.get("duration"),
                meta.get("source_class"),
                meta.get("statement_family"),
                len(points),
                points[0]["period"],
                points[-1]["period"],
                points[0]["period_sort_key"],
                points[-1]["period_sort_key"],
                json.dumps(quality_flags, ensure_ascii=False, sort_keys=True),
            ),
        )
        for point in points:
            conn.execute(
                """
                INSERT INTO chart_series_points (
                    series_key, ticker, period, fiscal_year, fiscal_quarter,
                    period_sort_key, document_type, document_period, source_document_id,
                    source_sort_key, value, formatted_value, object_id, trace_status,
                    metric_lineage_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    series_key,
                    meta.get("ticker"),
                    point.get("period"),
                    point.get("fiscal_year"),
                    point.get("fiscal_quarter"),
                    point.get("period_sort_key"),
                    point.get("document_type"),
                    point.get("document_period"),
                    point.get("source_document_id"),
                    point.get("source_sort_key"),
                    point.get("value"),
                    point.get("formatted_value"),
                    point.get("object_id"),
                    point.get("trace_status"),
                    point.get("metric_lineage_status"),
                ),
            )


def _write_metadata(conn: sqlite3.Connection, metadata: Mapping[str, Any]) -> None:
    for key, value in metadata.items():
        conn.execute(
            """
            INSERT OR REPLACE INTO chart_series_metadata (key, value_json)
            VALUES (?, ?)
            """,
            (str(key), json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)),
        )


def _chart_series_points(
    conn: sqlite3.Connection, series_key: str, *, limit: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM chart_series_points
        WHERE series_key = ?
        ORDER BY period_sort_key DESC, period DESC
        LIMIT ?
        """,
        (series_key, limit),
    ).fetchall()
    points = [
        {
            "period": row["period"],
            "value": row["value"],
            "formatted_value": row["formatted_value"],
            "object_id": row["object_id"],
            "evidence_ref": row["object_id"],
        }
        for row in reversed(rows)
    ]
    return points


def _classify_metric(raw_metric: Any, *, metric_name: Any, text: str) -> dict[str, Any] | None:
    canonical = _slug(raw_metric) or _slug(metric_name)
    haystack = " ".join([canonical, _slug(metric_name), _slug(text), str(text or "").lower()])
    quality_flags: list[str] = []
    if "business_combination_and_other_related_cost" in haystack or (
        "business combination" in haystack and "related cost" in haystack
    ):
        return {
            "canonical_metric": "ma_related_costs",
            "quality_flags": ["ma_costs_not_cash_acquisition"],
        }
    if (
        "business_combination" in haystack
        or "business combinations" in haystack
        or "acquisition" in haystack
        or "acquisitions" in haystack
        or "m_and_a" in haystack
    ) and ("cash" in haystack or "net_of_cash" in haystack or "purchase" in haystack):
        return {"canonical_metric": "ma_cash_outflow", "quality_flags": []}
    if "adjusted_free_cash_flow" in haystack or "adjusted_fcf" in haystack:
        return {"canonical_metric": "adjusted_free_cash_flow", "quality_flags": ["non_gaap"]}
    aliases = (
        ("research_and_development", ("research_and_development", "r_d", "research_development")),
        (
            "stock_based_compensation",
            ("stock_based_compensation", "share_based_compensation", "sbc"),
        ),
        ("share_repurchase", ("share_repurchase", "stock_repurchase", "repurchase", "buyback")),
        ("capital_expenditures", ("capital_expenditure", "capital_expenditures", "capex")),
        ("operating_cash_flow", ("operating_cash_flow", "net_cash_provided_by_operating")),
        ("free_cash_flow", ("free_cash_flow", "fcf")),
        ("cash_and_cash_equivalents", ("cash_and_cash_equivalents", "cash_and_equivalents")),
        ("total_debt", ("total_debt", "long_term_debt", "short_term_debt")),
        ("operating_income", ("operating_income", "income_from_operations")),
        ("net_income", ("net_income", "net_earnings")),
        ("gross_margin", ("gross_margin",)),
        ("operating_margin", ("operating_margin",)),
        ("eps", ("eps", "earnings_per_share")),
        ("revenue", ("revenue",)),
        ("net_sales", ("net_sales", "sales")),
    )
    for target, needles in aliases:
        if canonical == target or any(needle in haystack for needle in needles):
            if target in CHART_SAFE_CANONICAL_METRICS:
                return {"canonical_metric": target, "quality_flags": quality_flags}
    if canonical in CHART_SAFE_CANONICAL_METRICS:
        return {"canonical_metric": canonical, "quality_flags": quality_flags}
    return None


def _period_info(row: sqlite3.Row, obj: Mapping[str, Any]) -> dict[str, Any] | None:
    context = obj.get("context") if isinstance(obj.get("context"), Mapping) else {}
    period_end = obj.get("period_end") or context.get("period_end") or context.get("end_date")
    fiscal_year = _int_or_none(obj.get("fiscal_year"))
    if fiscal_year is None:
        fiscal_year = _int_or_none(context.get("fiscal_year"))
    if fiscal_year is None:
        fiscal_year = _int_or_none(row["fiscal_year"])
    if fiscal_year is None and period_end:
        fiscal_year = _year_from_date(str(period_end))
    if fiscal_year is None:
        fiscal_year = _year_from_period(str(row["period"] or ""))
    if fiscal_year is None:
        return None

    fiscal_quarter = _int_or_none(obj.get("fiscal_quarter"))
    if fiscal_quarter is None:
        fiscal_quarter = _int_or_none(context.get("fiscal_quarter"))
    if fiscal_quarter is None:
        fiscal_quarter = _int_or_none(row["fiscal_quarter"])
    if fiscal_quarter is None and period_end and str(row["document_type"] or "").upper() == "10-Q":
        fiscal_quarter = _quarter_from_date(str(period_end))
    if fiscal_quarter is None:
        fiscal_quarter = _quarter_from_period(str(row["period"] or ""))

    if fiscal_quarter:
        period = f"CY{fiscal_year}Q{fiscal_quarter}"
    else:
        period = f"CY{fiscal_year}"
    return {
        "period": period,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "sort_key": _period_sort_key(period),
    }


def _scope(row: sqlite3.Row) -> tuple[str, str, str]:
    for kind, column in (
        ("segment", "segment_name"),
        ("product", "product_name"),
        ("geography", "geography_name"),
    ):
        value = str(row[column] or "").strip()
        if value:
            key = _slug(value) or _short_hash(value)
            return kind, key, value
    dimensions = _json_object(row["dimensions_json"])
    if dimensions:
        first_key = sorted(dimensions)[0]
        first_value = str(dimensions.get(first_key) or "").strip()
        if first_value:
            kind = _slug(first_key) or "dimension"
            return kind, _slug(first_value) or _short_hash(first_value), first_value
        encoded = json.dumps(dimensions, ensure_ascii=False, sort_keys=True)
        return "dimension", _short_hash(encoded), encoded
    if int(row["is_company_total"] or 0):
        return "company_total", "company_total", "Company total"
    return "company_total", "company_total", "Company total"


def _basis(metric: str, row: sqlite3.Row, obj: Mapping[str, Any]) -> str:
    text = " ".join(
        str(part or "").lower()
        for part in (metric, row["metric_name"], row["text"], row["object_text"])
    )
    if (
        "non-gaap" in text
        or "non gaap" in text
        or "adjusted" in text
        or metric == "adjusted_free_cash_flow"
    ):
        return "non_gaap_adjusted"
    if obj.get("type") == "XBRLFact" or "(xbrl)" in text or "xbrl" in text:
        return "gaap_xbrl"
    if metric in {"gross_margin", "operating_margin"}:
        return "company_reported_ratio"
    return "company_reported"


def _duration(metric: str, row: sqlite3.Row, obj: Mapping[str, Any]) -> str:
    context = obj.get("context") if isinstance(obj.get("context"), Mapping) else {}
    raw_period_type = str(obj.get("period_type") or context.get("period_type") or "").lower()
    text = " ".join(
        str(part or "").lower() for part in (row["metric_name"], row["text"], row["object_text"])
    )
    if "ttm" in text or "trailing twelve" in text:
        return "ttm"
    if "ytd" in text or "year to date" in text:
        return "ytd"
    if raw_period_type == "instant" or metric in _POINT_IN_TIME_METRICS:
        return "point_in_time"
    return "period"


def _source_class(metric: str, basis: str, row: sqlite3.Row) -> str:
    text = " ".join(
        str(part or "").lower()
        for part in (metric, row["metric_name"], row["text"], row["object_text"])
    )
    if metric == "ma_related_costs":
        return "fcf_reconciliation"
    if metric == "ma_cash_outflow":
        return "cash_flow_statement"
    if metric == "adjusted_free_cash_flow":
        return "company_non_gaap_metric"
    if basis == "gaap_xbrl":
        return "xbrl"
    if "cash flow" in text or metric in {
        "operating_cash_flow",
        "free_cash_flow",
        "capital_expenditures",
        "share_repurchase",
    }:
        return "cash_flow_statement"
    if metric in _POINT_IN_TIME_METRICS:
        return "balance_sheet"
    if metric in {"gross_margin", "operating_margin"}:
        return "ratio_metric"
    return "company_reported_metric"


def _statement_family(metric: str, source_class: str) -> str:
    if source_class in {"cash_flow_statement", "fcf_reconciliation", "company_non_gaap_metric"}:
        return "cash_flow"
    if source_class == "balance_sheet" or metric in _POINT_IN_TIME_METRICS:
        return "balance_sheet"
    if metric in {"gross_margin", "operating_margin"}:
        return "ratio"
    return "income_statement"


def _metric_candidates_for_question(question: str) -> list[str]:
    text = str(question or "").lower()
    candidates: list[str] = []
    rules = (
        (("매출", "revenue", "sales", "net sales"), ("revenue", "net_sales")),
        (("영업이익", "operating income"), ("operating_income",)),
        (("순이익", "net income"), ("net_income",)),
        (("현금흐름", "cash flow", "ocf"), ("operating_cash_flow", "free_cash_flow")),
        (("fcf", "잉여현금", "free cash flow"), ("free_cash_flow", "adjusted_free_cash_flow")),
        (("capex", "자본지출", "설비투자"), ("capital_expenditures", "capex")),
        (("현금", "cash"), ("cash_and_cash_equivalents", "cash_and_equivalents")),
        (("부채", "debt"), ("total_debt",)),
        (("eps", "주당", "earnings per share"), ("eps",)),
        (("마진", "margin"), ("gross_margin", "operating_margin")),
        (("자사주", "repurchase", "buyback"), ("share_repurchase",)),
        (("r&d", "연구개발", "research and development"), ("research_and_development",)),
        (
            ("sg&a", "sga", "판관비", "selling general", "administrative"),
            ("selling_general_and_admin",),
        ),
        (("sbc", "주식보상", "stock based", "share based"), ("stock_based_compensation",)),
        (
            ("m&a", "인수", "합병", "acquisition", "business combination"),
            ("ma_cash_outflow", "ma_related_costs"),
        ),
        (("adjusted", "조정"), ("adjusted_free_cash_flow",)),
        # Observation (B4) chart clauses — vendor-neutral terms only: the
        # routing vocabulary describes concepts (CPI, yields, payrolls), never
        # provider series ids.
        (("cpi", "소비자물가"), ("cpi_yoy", "core_cpi_yoy")),
        (
            ("인플레이션", "inflation", "물가", "breakeven"),
            ("cpi_yoy", "core_cpi_yoy", "core_pce_yoy", "breakeven_inflation_10y"),
        ),
        (("pce",), ("core_pce_yoy",)),
        (("기준금리", "정책금리", "fed funds"), ("fed_funds_rate", "fed_funds_target_upper")),
        (("금리", "이자율", "interest rate"), ("fed_funds_rate", "dgs10_yield")),
        (("10년물", "10년", "10-year", "10y"), ("dgs10_yield",)),
        (("2년물", "2년", "2-year", "2y"), ("dgs2_yield",)),
        (("30년물", "30년", "30-year", "30y"), ("dgs30_yield",)),
        (("장단기", "spread"), ("treasury_10y2y_spread",)),
        (("실업률", "실업", "unemployment"), ("unemployment_rate",)),
        (("고용", "payroll", "비농", "jobless"), ("nonfarm_payrolls", "initial_jobless_claims")),
        (("gdp", "국내총생산"), ("nominal_gdp", "real_gdp")),
        (
            ("산업생산", "industrial production"),
            ("industrial_production_index",),
        ),
        (("소매", "retail sales"), ("retail_sales",)),
        (("주택", "housing"), ("housing_starts",)),
        (("vix", "변동성지수"), ("vix",)),
        (("s&p 500", "sp500", "s&p500"), ("sp500_index",)),
        (
            ("주가", "주가추이", "share price", "stock price"),
            ("last_price_monthly",),
        ),
        (
            ("p/e", "pe ratio", "trailing pe", "주가수익비율", "밸류에이션", "valuation"),
            ("trailing_pe_ttm",),
        ),
        (("p/b", "pbr", "price to book", "주가순자산비율"), ("price_to_book_ttm",)),
        (
            ("거시", "macro"),
            ("cpi_yoy", "fed_funds_rate", "unemployment_rate", "dgs10_yield"),
        ),
    )
    for needles, metrics in rules:
        if any(needle in text for needle in needles):
            candidates.extend(metrics)
    if not candidates and _looks_like_chart_question(text):
        candidates.extend(_DEFAULT_METRICS)
    return _unique(candidates)


def _scope_candidates_for_question(question: str) -> list[str]:
    text = str(question or "").lower()
    slug_text = _slug(text)
    candidates: list[str] = []
    rules = (
        (("iphone", "i phone", "아이폰"), ("i_phone",)),
        (("services", "service", "서비스"), ("service",)),
        (("ipad", "i pad", "아이패드"), ("i_pad",)),
        (("mac", "맥"), ("mac",)),
        (("wearables", "wearable", "웨어러블"), ("wearables_homeand_accessories",)),
        (
            ("product", "products", "제품"),
            ("product", "i_phone", "service", "mac", "i_pad", "wearables_homeand_accessories"),
        ),
        (("americas", "america", "미주"), ("americas_segment",)),
        (("europe", "유럽"), ("europe_segment",)),
        (("greater china", "china", "중국"), ("greater_china_segment", "cn")),
        (("japan", "일본"), ("japan_segment",)),
        (
            ("rest of asia pacific", "asia pacific", "asia", "아시아", "아태"),
            ("rest_of_asia_pacific_segment",),
        ),
        (("us", "united states", "미국"), ("us",)),
        (
            ("region", "regional", "geographic", "geography", "지역"),
            (
                "americas_segment",
                "europe_segment",
                "greater_china_segment",
                "japan_segment",
                "rest_of_asia_pacific_segment",
            ),
        ),
    )
    for needles, scopes in rules:
        if any(needle in text or _slug(needle) in slug_text for needle in needles):
            candidates.extend(scopes)
    return _unique(candidates)


def _dimension_series_requested(question: str, scope_candidates: Sequence[str]) -> bool:
    if scope_candidates:
        return True
    text = str(question or "").lower()
    return any(
        term in text
        for term in (
            "product",
            "products",
            "segment",
            "segments",
            "region",
            "regional",
            "geographic",
            "geography",
            "mix",
            "breakdown",
            "제품",
            "지역",
            "부문",
            "비중",
            "구성",
        )
    )


def _chart_series_relevance_key(
    row: sqlite3.Row,
    *,
    metric_candidates: Sequence[str],
    scope_candidates: Sequence[str],
    dimension_requested: bool,
) -> tuple[int, int, int, int, int, str, str]:
    metric = str(row["canonical_metric"] or "")
    scope_key = str(row["scope_key"] or "")
    scope_kind = str(row["scope_kind"] or "")
    metric_rank = metric_candidates.index(metric) if metric in metric_candidates else 999
    if scope_candidates and scope_key in set(scope_candidates):
        scope_rank = 0
    elif dimension_requested and scope_kind != "company_total":
        scope_rank = 1
    elif scope_kind == "company_total":
        scope_rank = 0 if not dimension_requested else 2
    else:
        scope_rank = 3
    period_rank = 0 if row["period_type"] == "annual" else 1
    point_rank = -int(row["point_count"] or 0)
    recency_rank = -int(row["last_sort_key"] or 0)
    return (
        metric_rank,
        scope_rank,
        period_rank,
        point_rank,
        recency_rank,
        str(row["ticker"] or ""),
        str(row["label"] or ""),
    )


def _select_chart_series_rows(
    rows: Sequence[sqlite3.Row],
    *,
    limit: int,
    metric_candidates: Sequence[str],
    scope_candidates: Sequence[str],
    dimension_requested: bool,
) -> list[sqlite3.Row]:
    ordered = sorted(
        rows,
        key=lambda row: _chart_series_relevance_key(
            row,
            metric_candidates=metric_candidates,
            scope_candidates=scope_candidates,
            dimension_requested=dimension_requested,
        ),
    )
    if not dimension_requested:
        return _dedupe_chart_series_rows(ordered)[:limit]

    selected: list[sqlite3.Row] = []
    seen_series: set[str] = set()
    seen_scope_metric: set[tuple[str, str, str]] = set()

    def add(row: sqlite3.Row) -> bool:
        if len(selected) >= limit:
            return False
        series_key = str(row["series_key"] or "")
        if not series_key or series_key in seen_series:
            return False
        scope_metric_key = (
            str(row["canonical_metric"] or ""),
            str(row["scope_kind"] or ""),
            str(row["scope_key"] or ""),
        )
        if scope_metric_key in seen_scope_metric:
            return False
        selected.append(row)
        seen_series.add(series_key)
        seen_scope_metric.add(scope_metric_key)
        return True

    scope_set = set(scope_candidates)
    for row in ordered:
        if str(row["scope_key"] or "") in scope_set:
            add(row)

    selected_metrics = {str(row["canonical_metric"] or "") for row in selected}
    for metric in metric_candidates:
        if metric in selected_metrics:
            continue
        for row in ordered:
            if str(row["canonical_metric"] or "") != metric:
                continue
            if add(row):
                selected_metrics.add(metric)
                break

    for row in ordered:
        add(row)

    return selected


def _dedupe_chart_series_rows(rows: Sequence[sqlite3.Row]) -> list[sqlite3.Row]:
    selected: list[sqlite3.Row] = []
    seen_scope_metric: set[tuple[str, str, str]] = set()
    for row in rows:
        scope_metric_key = (
            str(row["canonical_metric"] or ""),
            str(row["scope_kind"] or ""),
            str(row["scope_key"] or ""),
        )
        if scope_metric_key in seen_scope_metric:
            continue
        selected.append(row)
        seen_scope_metric.add(scope_metric_key)
    return selected


def _looks_like_chart_question(text: str) -> bool:
    return any(
        term in text
        for term in (
            "chart",
            "graph",
            "trend",
            "series",
            "yoy",
            "qoq",
            "annual",
            "quarterly",
            "차트",
            "그래프",
            "추이",
            "추세",
            "시계열",
            "비교",
            "비중",
            "구성",
            "흐름",
            "변화",
            "연도별",
            "분기별",
            "제품별",
            "지역별",
        )
    )


def _series_label(metric: str, scope_kind: str, scope_label: str) -> str:
    metric_label = {
        "adjusted_free_cash_flow": "Adjusted FCF",
        "capital_expenditures": "CapEx",
        "cash_and_cash_equivalents": "Cash & equivalents",
        "cash_and_equivalents": "Cash & equivalents",
        "eps": "EPS",
        "free_cash_flow": "Free cash flow",
        "gross_margin": "Gross margin",
        "ma_cash_outflow": "M&A cash outflow",
        "ma_related_costs": "M&A-related costs",
        "net_income": "Net income",
        "net_sales": "Net sales",
        "operating_cash_flow": "Operating cash flow",
        "operating_income": "Operating income",
        "operating_margin": "Operating margin",
        "research_and_development": "R&D",
        "revenue": "Revenue",
        "selling_general_and_admin": "SG&A",
        "share_repurchase": "Share repurchase",
        "stock_based_compensation": "SBC",
        "total_assets": "Total assets",
        "total_debt": "Total debt",
        "total_liabilities": "Total liabilities",
        # Observation metrics (B4): advisory market/macro context.
        "breakeven_inflation_10y": "10-year breakeven inflation",
        "cpi_yoy": "CPI (YoY)",
        "core_cpi_yoy": "Core CPI (YoY)",
        "core_pce_yoy": "Core PCE (YoY)",
        "dgs2_yield": "2-year Treasury yield",
        "dgs10_yield": "10-year Treasury yield",
        "dgs30_yield": "30-year Treasury yield",
        "fed_funds_rate": "Fed funds rate",
        "fed_funds_target_upper": "Fed funds target (upper)",
        "housing_starts": "Housing starts",
        "industrial_production_index": "Industrial production",
        "initial_jobless_claims": "Initial jobless claims",
        "last_price_monthly": "Month-end share price",
        "nominal_gdp": "Nominal GDP",
        "nonfarm_payrolls": "Nonfarm payrolls",
        "price_to_book_ttm": "Price / book (TTM)",
        "real_gdp": "Real GDP",
        "retail_sales": "Retail sales",
        "sp500_index": "S&P 500 index",
        "trailing_pe_ttm": "P/E (TTM)",
        "treasury_10y2y_spread": "10Y–2Y Treasury spread",
        "unemployment_rate": "Unemployment rate",
        "vix": "Volatility index (VIX)",
    }.get(metric, metric.replace("_", " ").title())
    if scope_kind == "company_total":
        return metric_label
    if scope_kind == "macro":
        return metric_label
    return f"{scope_label} {metric_label}"


def _point_quality_score(point: Mapping[str, Any]) -> tuple[int, int, int, int, str]:
    source_sort = int(point.get("source_sort_key") or 0)
    trace_score = 0
    if point.get("metric_lineage_status") == "traceable_metric_lineage":
        trace_score += 20
    if point.get("trace_status") in {"traceable", "traceable_metric_lineage"}:
        trace_score += 10
    document_score = 5 if point.get("document_type") in {"10-K", "10-Q"} else 0
    exact_period_score = (
        5
        if _period_sort_key(str(point.get("document_period") or ""))
        == int(point.get("period_sort_key") or 0)
        else 0
    )
    return (
        source_sort,
        trace_score,
        document_score,
        exact_period_score,
        str(point.get("object_id") or ""),
    )


def _format_value(value: float, unit: str | None) -> str:
    unit_text = str(unit or "")
    if unit_text in {"USD", "usd"}:
        absolute = abs(value)
        if absolute >= 1_000_000_000:
            return f"${value / 1_000_000_000:.1f}B"
        if absolute >= 1_000_000:
            return f"${value / 1_000_000:.1f}M"
        return f"${value:,.0f}"
    if "percent" in unit_text.lower() or unit_text == "%":
        return f"{value:.1f}%"
    return f"{value:g}"


def _resolve_shard_path(root: Path, entry: Mapping[str, Any]) -> Path:
    raw = entry.get("path") or entry.get("shard_path")
    if not isinstance(raw, str) or not raw:
        return root / "indexes" / "companies" / "<missing>"
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.expanduser().resolve()
    if candidate.parts and candidate.parts[0] == "indexes":
        return (root / candidate).resolve()
    return (root / "indexes" / candidate).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _year_from_date(value: str) -> int | None:
    match = re.search(r"((?:19|20)\d{2})", value)
    return int(match.group(1)) if match else None


def _quarter_from_date(value: str) -> int | None:
    match = re.search(r"(?:19|20)\d{2}-(\d{2})-\d{2}", value)
    if not match:
        return None
    month = int(match.group(1))
    if month <= 3:
        return 1
    if month <= 6:
        return 2
    if month <= 9:
        return 3
    return 4


def _year_from_period(value: str) -> int | None:
    match = re.search(r"((?:19|20)\d{2})", value.upper())
    return int(match.group(1)) if match else None


def _quarter_from_period(value: str) -> int | None:
    match = re.search(r"Q([1-4])", value.upper())
    return int(match.group(1)) if match else None


def _period_sort_key(period: str) -> int:
    # Monthly buckets (B4) sort at year*100 + month so they order within a
    # year; FY/CY/Q keys keep their historical year*10 + quarter values so
    # existing filing series sort identically to pre-observation builds.
    match = re.search(r"(?:CY|FY)?((?:19|20)\d{2})(?:Q([1-4])|M(0[1-9]|1[0-2]))?", period.upper())
    if not match:
        return 0
    year = int(match.group(1))
    if match.group(2):
        return year * 10 + int(match.group(2))
    if match.group(3):
        return year * 100 + int(match.group(3))
    return year * 10


def _slug(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _temporary_sqlite_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.tmp")


def _cleanup_sqlite_sidecars(path: Path) -> None:
    for candidate in (
        path,
        path.with_suffix(path.suffix + "-wal"),
        path.with_suffix(path.suffix + "-shm"),
    ):
        try:
            if candidate.exists():
                candidate.unlink()
        except OSError:
            pass
