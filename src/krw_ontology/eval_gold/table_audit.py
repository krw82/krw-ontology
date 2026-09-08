"""XBRL cross-validation audit for shard metric observations.

Cross-validates the release shards' numeric metric extractions against the
SEC EDGAR ``companyfacts`` XBRL API:

- :data:`XBRL_TAG_MAP` — static canonical metric -> us-gaap tag candidates
  (initial scope: the six dictionary metrics the audit plan scoped).
- :func:`fetch_companyfacts` — cache-first companyfacts fetch (the SEC API
  requires an identifying ``User-Agent``; it is read from ``SEC_AUDIT_UA``
  and defaults to a non-secret identification string).
- :func:`xbrl_lookup` — select ``fy == fiscal_year`` entries from
  ``facts["facts"]["us-gaap"][tag]["units"][unit]``, preferring 10-K forms,
  ``fp == "FY"``, then the latest end date (frame presence and filed date
  only break ties — see :func:`xbrl_lookup_detailed` for why).
- :func:`compare_value` — verdict after shard-side unit conversion
  (K/M/B scale suffix on the shard side only; XBRL values are raw).
- :func:`audit_shard_metrics` — deterministic sampling of accepted
  ``MetricObservation`` rows per ticker (seeded ``random.Random`` consumed in
  sorted-ticker order, candidates sorted by object id — the same contract as
  :mod:`krw_ontology.eval_gold.templates`), then fetch/lookup/compare per row.

Shard ground truth (release 20260830_193811): ``MetricObservation`` json rows
carry the numeric value in ``value`` (already raw-scale floats), the unit in
``unit`` (``"USD"``, ``"USD_per_share"``, ``"percent"``), the scale label in
``scale`` (``"ones"``), the filing-aligned year in ``fiscal_year``, and
``dimensions == {}`` for company totals.  Shards carry **no CIK** anywhere
(objects json, ``documents``, ``metadata``, shard manifest), so a cache miss
without a caller-supplied ``cik_map`` entry is skipped with reason
``"no_cik"`` rather than guessed.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import sqlite3
from pathlib import Path
from typing import Any

import httpx

DEFAULT_SEED = 20260908
SHARD_MANIFEST_RELPATH = Path("indexes") / "shard_manifest.json"

#: SEC requires an identifying User-Agent.  It is an identification string,
#: not a credential; operators override it via ``SEC_AUDIT_UA``.
DEFAULT_USER_AGENT = "krw-ontology-audit research"
USER_AGENT_ENVVAR = "SEC_AUDIT_UA"

#: Default cache lives outside the release tree (and outside the repo).
DEFAULT_CACHE_DIR = Path.home() / "krw-ontology-data" / "cache" / "sec-companyfacts"

#: Real SEC endpoint shape: ``.../companyfacts/CIK0001045810.json``.
_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_digits_padded}.json"

#: ``match`` = within 0.1% after unit conversion; ``tolerance`` = within 1%.
MATCH_REL_TOLERANCE = 0.001
TOLERANCE_REL_TOLERANCE = 0.01
_FP_EPS = 1e-12

_ACCEPTED_OBJECT_SQL = "(o.review_status IS NULL OR o.review_status = 'accepted')"

#: Canonical metric -> us-gaap tag candidates, in priority order.  The first
#: candidate that yields facts ranks ahead of later candidates regardless of
#: end dates.  Initial scope = the audit plan's six dictionary metrics.
XBRL_TAG_MAP: dict[str, list[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "eps": ["EarningsPerShareBasic", "EarningsPerShareDiluted"],
    "research_and_development": ["ResearchAndDevelopmentExpense"],
    # Shard extractors map total_debt onto LongTermDebt-family tags (release
    # 20260830_193811 source_fact_ids use LongTermDebt); Liabilities is the
    # coarse fallback for filings that only tag the liability total.
    "total_debt": ["LongTermDebt", "LongTermDebtNoncurrent", "Liabilities"],
}

_SCALE_MULTIPLIERS: dict[str, float] = {
    "one": 1.0,
    "ones": 1.0,
    "unit": 1.0,
    "units": 1.0,
    "raw": 1.0,
    "k": 1e3,
    "thousand": 1e3,
    "thousands": 1e3,
    "m": 1e6,
    "million": 1e6,
    "millions": 1e6,
    "b": 1e9,
    "billion": 1e9,
    "billions": 1e9,
    "t": 1e12,
    "trillion": 1e12,
    "trillions": 1e12,
}

_VERDICT_KEYS = (
    "match",
    "tolerance",
    "mismatch",
    "missing_xbrl",
    "no_tag_map",
    "no_cik",
)


def resolve_user_agent(ua: str | None = None) -> str:
    """UA precedence: explicit arg > ``SEC_AUDIT_UA`` > non-secret default."""
    if ua:
        return ua
    return os.environ.get(USER_AGENT_ENVVAR) or DEFAULT_USER_AGENT


# ---------------------------------------------------------------------------
# Unit conversion + comparison
# ---------------------------------------------------------------------------


def shard_unit_multiplier(unit: str) -> float:
    """Multiplier encoded in a shard unit string (XBRL side is always raw).

    Handles composed labels such as ``"USD_millions"``, ``"USD/thousands"``,
    and bare scale tokens such as ``"thousands"``.  No scale token (e.g.
    ``"USD"``, ``"USD_per_share"``) means the shard value is already raw.
    """
    if not unit:
        return 1.0
    tokens = re.split(r"[\s_/\-]+", unit.strip().lower())
    for token in tokens:
        multiplier = _SCALE_MULTIPLIERS.get(token)
        if multiplier is not None:
            return multiplier
    return 1.0


def _relative_diff(converted: float, reference: float) -> float:
    """Relative difference against the XBRL reference value (ground truth)."""
    if converted == reference:
        return 0.0
    if not math.isfinite(converted) or not math.isfinite(reference):
        return math.inf
    if reference == 0.0:
        return math.inf
    return abs(converted - reference) / abs(reference)


def compare_value(shard_value: float, xbrl_value: float, *, unit: str) -> str:
    """Verdict for one (shard, xbrl) value pair after shard-side conversion.

    ``"match"`` within 0.1%, ``"tolerance``" within 1% (both inclusive,
    relative to the XBRL value), else ``"mismatch"``.  The scale multiplier
    applies to the shard side only; XBRL companyfacts values are raw.
    """
    converted = float(shard_value) * shard_unit_multiplier(unit)
    reference = float(xbrl_value)
    rel = _relative_diff(converted, reference)
    if rel <= MATCH_REL_TOLERANCE + _FP_EPS:
        return "match"
    if rel <= TOLERANCE_REL_TOLERANCE + _FP_EPS:
        return "tolerance"
    return "mismatch"


# ---------------------------------------------------------------------------
# companyfacts fetch (cache-first)
# ---------------------------------------------------------------------------


def fetch_companyfacts(
    ticker: str,
    *,
    cache_dir: Path | str,
    ua: str | None = None,
    cik: int | str | None = None,
) -> dict | None:
    """Load SEC companyfacts for ``ticker``, cache-first.

    Cache file: ``<cache_dir>/<TICKER>.json``.  On a hit the file is loaded
    and no network is used.  On a miss, a ``cik`` is required: shards carry
    no CIK, so a miss without one returns ``None`` (caller records
    ``"no_cik"``) instead of guessing.  Successful fetches are persisted to
    the cache.
    """
    ticker = ticker.strip().upper()
    cache_path = Path(cache_dir) / f"{ticker}.json"
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    if cik is None:
        return None
    cik_digits = str(int(str(cik).strip()))
    url = _COMPANYFACTS_URL.format(cik_digits_padded=cik_digits.zfill(10))
    agent = resolve_user_agent(ua)
    with httpx.Client(headers={"User-Agent": agent}, follow_redirects=True, timeout=30.0) as client:
        response = client.get(url)
        response.raise_for_status()
        facts = response.json()
    if not isinstance(facts, dict):
        raise ValueError(f"companyfacts response for {ticker} is not a JSON object")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    return facts


# ---------------------------------------------------------------------------
# XBRL lookup
# ---------------------------------------------------------------------------


def _descending_date_rank(value: Any) -> int:
    """ISO date string -> negative int so ascending sort puts latest first."""
    digits = str(value or "").replace("-", "")
    return -int(digits) if digits.isdigit() else 0


def _entry_sort_key(tag_priority: int, entry: dict) -> tuple:
    # companyfacts ``fy``/``fp`` describe the FILING, not the fact period:
    # comparative-period rows in later 10-Ks carry the later filing's fy and
    # can even carry frames.  End-date recency therefore ranks ahead of frame
    # presence, or a 2-year-old comparative with a frame would outrank the
    # original fact of the requested fiscal year.
    return (
        tag_priority,
        0 if entry.get("form") == "10-K" else 1,
        0 if entry.get("fp") == "FY" else 1,
        _descending_date_rank(entry.get("end")),
        0 if entry.get("frame") else 1,
        _descending_date_rank(entry.get("filed")),
        json.dumps(entry, sort_keys=True),
    )


def xbrl_lookup_detailed(
    facts: dict, canonical_metric: str, fiscal_year: int
) -> list[tuple[str, str, dict]]:
    """Preference-ranked ``(tag, unit, entry)`` triples for one metric/year.

    Selection: ``entry["fy"] == fiscal_year``.  Ranking: tag-candidate order,
    then 10-K forms, ``fp == "FY"``, latest end date, frame-bearing entries,
    then latest filed date.  Entries are returned as-is (raw companyfacts
    dicts).
    """
    tags = XBRL_TAG_MAP.get(canonical_metric)
    if not tags or not isinstance(facts, dict):
        return []
    us_gaap = facts.get("facts", {})
    if not isinstance(us_gaap, dict):
        return []
    tag_nodes = us_gaap.get("us-gaap", {})
    if not isinstance(tag_nodes, dict):
        return []
    collected: list[tuple[tuple, str, str, dict]] = []
    for priority, tag in enumerate(tags):
        tag_node = tag_nodes.get(tag)
        if not isinstance(tag_node, dict):
            continue
        units = tag_node.get("units", {})
        if not isinstance(units, dict):
            continue
        for unit_key, entries in units.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("fy") != fiscal_year:
                    continue
                collected.append((_entry_sort_key(priority, entry), tag, str(unit_key), entry))
    collected.sort(key=lambda item: item[0])
    return [(tag, unit, entry) for _, tag, unit, entry in collected]


def xbrl_lookup(facts: dict, canonical_metric: str, fiscal_year: int) -> list[dict]:
    """Raw preference-ranked companyfacts entries for one metric/year."""
    return [entry for _, _, entry in xbrl_lookup_detailed(facts, canonical_metric, fiscal_year)]


# ---------------------------------------------------------------------------
# Shard sampling + audit
# ---------------------------------------------------------------------------


def _load_shard_manifest(release_root: Path) -> dict[str, Any]:
    manifest_path = release_root / SHARD_MANIFEST_RELPATH
    if not manifest_path.is_file():
        raise FileNotFoundError(f"shard manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shards = manifest.get("shards")
    if not isinstance(shards, dict) or not shards:
        raise ValueError(f"shard manifest has no shards: {manifest_path}")
    return manifest


def _resolve_shard_path(release_root: Path, entry_path: str) -> Path:
    path = Path(entry_path)
    return path if path.is_absolute() else release_root / "indexes" / path


def select_tickers(
    all_tickers: list[str],
    *,
    tickers: list[str] | int | str | None,
    rng: random.Random,
) -> list[str]:
    """Resolve the audit ticker list deterministically.

    ``None`` -> every ticker, sorted.  A list -> uppercased, validated against
    the manifest.  An int (or numeric string) -> a seeded sample of that many
    tickers from the sorted manifest list, returned sorted so downstream RNG
    consumption stays order-stable.
    """
    sorted_all = sorted(all_tickers)
    if tickers is None:
        return sorted_all
    if isinstance(tickers, int):
        if tickers < 1:
            raise ValueError(f"ticker count must be >= 1, got {tickers}")
        count = min(tickers, len(sorted_all))
        return sorted(rng.sample(sorted_all, count))
    if isinstance(tickers, str):
        if not tickers.strip().isdigit():
            raise ValueError(
                f"--tickers must be a ticker count or comma-separated list, got {tickers!r}"
            )
        return select_tickers(sorted_all, tickers=int(tickers), rng=rng)
    wanted = sorted({value.strip().upper() for value in tickers if value.strip()})
    if not wanted:
        raise ValueError("tickers filter resolved to an empty set")
    unknown = [value for value in wanted if value not in sorted_all]
    if unknown:
        raise ValueError(f"tickers not in shard manifest: {unknown}")
    return wanted


def _read_audit_candidates(shard_path: Path, ticker: str) -> list[dict[str, Any]]:
    """Accepted, company-total, mapped-metric observations for one ticker.

    Mirrors the serving index's acceptance semantics (``review_status`` NULL
    or ``accepted``), keeps only company-total rows (``dimensions == {}``) —
    dimensioned rows have no unambiguous XBRL counterpart — and requires a
    numeric ``value`` plus an integer ``fiscal_year`` in the json payload.
    Rows are sorted by object id for seeded, deterministic sampling.
    """
    placeholders = ",".join("?" for _ in XBRL_TAG_MAP)
    sql = f"""
        SELECT o.id, o.period, o.document_type, o.json
        FROM objects o
        WHERE o.type = 'MetricObservation'
          AND o.ticker = ?
          AND o.metric_name IN ({placeholders})
          AND {_ACCEPTED_OBJECT_SQL}
        ORDER BY o.id
    """
    with sqlite3.connect(f"file:{shard_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, (ticker, *XBRL_TAG_MAP)).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["json"])
        except (TypeError, json.JSONDecodeError):
            continue
        metric = str(payload.get("metric_name") or "")
        if metric not in XBRL_TAG_MAP:
            continue
        value = payload.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        fiscal_year = payload.get("fiscal_year")
        if isinstance(fiscal_year, bool) or not isinstance(fiscal_year, int):
            continue
        if payload.get("dimensions") not in (None, {}, []):
            continue
        unit = str(payload.get("unit") or "")
        scale = str(payload.get("scale") or "")
        unit_label = f"{unit}_{scale}" if scale and scale != "ones" else unit
        candidates.append(
            {
                "object_id": str(row["id"]),
                "metric": metric,
                "period": str(row["period"]),
                "document_type": str(row["document_type"]),
                "fiscal_year": fiscal_year,
                "value": float(value),
                "unit_label": unit_label,
            }
        )
    return candidates


def audit_shard_metrics(
    release_root: Path | str,
    *,
    tickers: list[str] | int | None = None,
    seed: int = DEFAULT_SEED,
    per_ticker: int = 5,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    ua: str | None = None,
    cik_map: dict[str, int | str] | None = None,
) -> dict[str, Any]:
    """Cross-validate sampled shard metric rows against SEC XBRL companyfacts.

    Deterministic: one ``random.Random(seed)`` consumed in sorted-ticker
    order; per ticker, candidates sorted by object id are sampled without
    replacement (``per_ticker`` rows).  The same seed reproduces the same
    report byte-for-byte given the same shards and facts cache.

    Returns ``{"release_root", "seed", "per_ticker", "rows", "summary"}``
    where each row carries the sampled shard values, the best-ranked XBRL
    entry, and a verdict; ``summary`` counts
    ``match/tolerance/mismatch/missing_xbrl/no_tag_map/no_cik`` (+ ``total``,
    ``tickers``, ``sampled_rows``).
    """
    release_root = Path(release_root)
    if per_ticker < 1:
        raise ValueError(f"per_ticker must be >= 1, got {per_ticker}")
    manifest = _load_shard_manifest(release_root)
    shards: dict[str, Any] = manifest["shards"]
    normalized_cik_map = {str(key).strip().upper(): value for key, value in (cik_map or {}).items()}
    rng = random.Random(seed)
    selected = select_tickers(list(shards), tickers=tickers, rng=rng)

    rows: list[dict[str, Any]] = []
    facts_by_ticker: dict[str, dict | None] = {}
    for ticker in selected:
        shard_path = _resolve_shard_path(release_root, str(shards[ticker]["path"]))
        candidates = _read_audit_candidates(shard_path, ticker)
        sample_size = min(per_ticker, len(candidates))
        sampled = rng.sample(candidates, sample_size) if sample_size else []
        for candidate in sampled:
            if ticker not in facts_by_ticker:
                facts_by_ticker[ticker] = fetch_companyfacts(
                    ticker,
                    cache_dir=cache_dir,
                    ua=ua,
                    cik=normalized_cik_map.get(ticker),
                )
            facts: dict | None = facts_by_ticker[ticker]
            rows.append(
                _audit_row(
                    ticker=ticker,
                    candidate=candidate,
                    facts=facts,
                )
            )
    summary = {key: 0 for key in _VERDICT_KEYS}
    for row in rows:
        summary[row["verdict"]] += 1
    summary["total"] = len(rows)
    summary["tickers"] = len(selected)
    summary["sampled_rows"] = len(rows)
    return {
        "release_root": str(release_root),
        "seed": seed,
        "per_ticker": per_ticker,
        "rows": rows,
        "summary": summary,
    }


def _audit_row(
    *,
    ticker: str,
    candidate: dict[str, Any],
    facts: dict | None,
) -> dict[str, Any]:
    metric = candidate["metric"]
    row: dict[str, Any] = {
        "ticker": ticker,
        "object_id": candidate["object_id"],
        "metric": metric,
        "period": candidate["period"],
        "document_type": candidate["document_type"],
        "fiscal_year": candidate["fiscal_year"],
        "shard_value": candidate["value"],
        "shard_unit": candidate["unit_label"],
        "xbrl_tag": None,
        "xbrl_unit": None,
        "xbrl_value": None,
        "xbrl_end": None,
        "xbrl_form": None,
        "verdict": None,
        "rel_diff": None,
    }
    if metric not in XBRL_TAG_MAP:
        row["verdict"] = "no_tag_map"
        return row
    if facts is None:
        row["verdict"] = "no_cik"
        return row
    matches = xbrl_lookup_detailed(facts, metric, candidate["fiscal_year"])
    if not matches:
        row["verdict"] = "missing_xbrl"
        return row
    tag, unit, entry = matches[0]
    xbrl_value = entry.get("val")
    if isinstance(xbrl_value, bool) or not isinstance(xbrl_value, (int, float)):
        row["verdict"] = "missing_xbrl"
        return row
    converted = candidate["value"] * shard_unit_multiplier(candidate["unit_label"])
    row.update(
        {
            "xbrl_tag": tag,
            "xbrl_unit": unit,
            "xbrl_value": float(xbrl_value),
            "xbrl_end": entry.get("end"),
            "xbrl_form": entry.get("form"),
            "verdict": compare_value(
                candidate["value"], float(xbrl_value), unit=candidate["unit_label"]
            ),
            "rel_diff": _relative_diff(converted, float(xbrl_value)),
        }
    )
    return row
