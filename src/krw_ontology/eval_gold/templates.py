"""Deterministic template gold generator for the evidence-gold harness.

Samples ``(ticker, metric, period)`` triples from the release's built company
shards (``indexes/shard_manifest.json`` + per-ticker sqlite) and emits
``EvidenceGold`` cases whose expected anchors are the shard's own accepted
metric objects.  Cases carry a single metric clause
(``metrics=[canonical]``, ``tickers=[t]``, ``periods=[p]``,
``metric_scope='any'``) so the suite probes the serving index's
metric-lookup floor; ``metric_scope='any'`` keeps dimensioned anchors
(``is_company_total = 0`` rows) reachable through the metric-lookup
channel, which otherwise filters to company totals.

Determinism contract: one ``random.Random(seed)`` instance is consumed in
sorted-ticker order, every candidate list is sorted before sampling, and
``source_release`` carries the file-sha256 of the shard manifest.  Two
invocations with the same seed produce identical ``EvidenceGold`` documents.

"Accepted" for metric objects follows the serving index's own semantics:
``_rebuild_metric_lookup`` admits objects whose ``review_status`` is NULL
(machine-projected facts) or not ``rejected``; production shards carry NULL
for every ``MetricObservation``/``XBRLFact``.  Sampling therefore accepts
``review_status IS NULL OR review_status = 'accepted'`` and never touches
``rejected``/``needs_review`` rows.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from krw_ontology.agent_index.metric_dictionary import metric_dictionary_catalog
from krw_ontology.eval_gold.schema import (
    EVIDENCE_GOLD_FORMAT_VERSION,
    EvidenceGold,
    EvidenceGoldCase,
    ExpectedEvidence,
)

DEFAULT_SEED = 20260908
SHARD_MANIFEST_RELPATH = Path("indexes") / "shard_manifest.json"

#: Every Nth generated case (1-based) samples a two-period comparison when the
#: ticker has a metric with at least two periods.
MULTI_PERIOD_EVERY = 5

#: Stratum precedence for a case's primary tag; ``template`` is the default.
STRATUM_TEMPLATE = "template"
STRATUM_DIMENSIONED = "dimensioned"
STRATUM_MULTI_PERIOD = "multi_period"

_ACCEPTED_OBJECT_SQL = "(o.review_status IS NULL OR o.review_status = 'accepted')"


@dataclass(frozen=True)
class MetricTriple:
    """One sampled (ticker, canonical metric, filing period) candidate."""

    ticker: str
    metric: str
    period: str
    object_ids: tuple[str, ...]
    document_types: tuple[str, ...]


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


def _file_sha256_hex(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_metric_triples(shard_path: Path, ticker: str) -> list[MetricTriple]:
    """Accepted, dictionary-canonical (metric, period) triples for one ticker.

    ``metric_lookup.canonical_metric`` can carry normalized unknown ids (the
    ``canonical_metric_name`` fallback), which ``validate_search_plan``
    would reject as ``metrics`` entries; those triples are skipped here so
    every generated clause validates against the same metric dictionary the
    shards were built with.
    """
    catalog = metric_dictionary_catalog()
    with sqlite3.connect(f"file:{shard_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT m.object_id, m.canonical_metric, m.period, o.document_type
            FROM metric_lookup m
            JOIN objects o ON o.id = m.object_id
            WHERE m.ticker = ? AND m.canonical_metric IS NOT NULL
              AND {_ACCEPTED_OBJECT_SQL}
            ORDER BY m.object_id
            """,
            (ticker,),
        ).fetchall()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        metric = str(row["canonical_metric"])
        period = str(row["period"] or "")
        document_type = str(row["document_type"] or "")
        if not metric or not period or not document_type:
            continue
        if catalog.canonicalize(metric) != metric:
            continue
        key = (metric, period)
        bucket = grouped.setdefault(
            key, {"object_ids": set(), "document_types": set()}
        )
        bucket["object_ids"].add(str(row["object_id"]))
        bucket["document_types"].add(document_type)
    triples = [
        MetricTriple(
            ticker=ticker,
            metric=metric,
            period=period,
            object_ids=tuple(sorted(bucket["object_ids"])),
            document_types=tuple(sorted(bucket["document_types"])),
        )
        for (metric, period), bucket in sorted(grouped.items())
    ]
    return triples


def _dimensioned_metrics(shard_path: Path, ticker: str) -> set[str]:
    """Metrics with a real (non company-total) dimension in the shard."""
    with sqlite3.connect(f"file:{shard_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT canonical_metric
            FROM metric_dimension_lookup
            WHERE ticker = ? AND dimension_kind IS NOT NULL
              AND dimension_kind != 'company_total'
            """,
            (ticker,),
        ).fetchall()
    return {str(row[0]) for row in rows}


def _clause_id(metric: str, periods: tuple[str, ...]) -> str:
    return "-".join((metric, *periods))


def _question(metric: str, ticker: str, periods: tuple[str, ...]) -> str:
    display = metric.replace("_", " ")
    if len(periods) == 1:
        return f"What was {ticker} {display} in {periods[0]}?"
    return f"How did {ticker} {display} change from {periods[0]} to {periods[1]}?"


def _search_plan(
    *,
    question: str,
    ticker: str,
    metric: str,
    periods: tuple[str, ...],
    document_types: tuple[str, ...],
) -> dict[str, Any]:
    display = metric.replace("_", " ")
    return {
        "question": question,
        "intent": "metric_lookup",
        "tickers": [ticker],
        "document_types": list(document_types),
        "periods": list(periods),
        "clauses": [
            {
                "clause_id": _clause_id(metric, periods),
                "retrieval_query": f"{ticker} {display} {' '.join(periods)}",
                "metrics": [metric],
                "tickers": [ticker],
                # Dimensioned-stratum anchors are is_company_total = 0 rows;
                # the default company_total scope would filter them out of the
                # metric-lookup channel (store.py appends
                # metric_lookup.is_company_total = 1).  "any" imposes no
                # company-total constraint, needs no metric_dimensions, and
                # leaves company-total anchors reachable for other strata.
                "metric_scope": "any",
            }
        ],
    }


def _build_case(
    *,
    ticker: str,
    metric: str,
    triples: list[MetricTriple],
    dimensioned: set[str],
    shard_label: str,
) -> EvidenceGoldCase:
    periods = tuple(triple.period for triple in triples)
    expected = [
        ExpectedEvidence(
            ticker=ticker,
            period=triple.period,
            document_type=None,
            object_ids=list(triple.object_ids),
        )
        for triple in triples
    ]
    strata: list[str] = []
    if len(periods) > 1:
        strata.append(STRATUM_MULTI_PERIOD)
    if metric in dimensioned:
        strata.append(STRATUM_DIMENSIONED)
    if not strata:
        strata.append(STRATUM_TEMPLATE)
    document_types = tuple(
        sorted({dt for triple in triples for dt in triple.document_types})
    )
    question = _question(metric, ticker, periods)
    return EvidenceGoldCase(
        id=":".join(("template", ticker, metric, *periods)),
        question=question,
        search_plan=_search_plan(
            question=question,
            ticker=ticker,
            metric=metric,
            periods=periods,
            document_types=document_types,
        ),
        strata=strata,
        expected=expected,
        notes=(
            f"template sample from shard {shard_label}; anchors are the "
            f"accepted metric objects for ({ticker}, {metric}, "
            f"{', '.join(periods)})"
        ),
    )


def _cases_for_ticker(
    *,
    rng: random.Random,
    ticker: str,
    shard_path: Path,
    shard_label: str,
    per_ticker: int,
) -> list[EvidenceGoldCase]:
    triples = _read_metric_triples(shard_path, ticker)
    if not triples:
        return []
    dimensioned = _dimensioned_metrics(shard_path, ticker)
    by_metric: dict[str, list[MetricTriple]] = {}
    for triple in triples:
        by_metric.setdefault(triple.metric, []).append(triple)
    # Deterministic, collision-free candidate list: every (metric, period pair)
    # combination, sorted, sampled without replacement so two multi-period
    # slots can never mint the same case id.
    multi_candidates = sorted(
        (metric, periods)
        for metric in sorted(by_metric)
        for periods in itertools.combinations(
            sorted({triple.period for triple in by_metric[metric]}), 2
        )
    )
    used_multi_keys: set[tuple[str, tuple[str, str]]] = set()

    cases: list[EvidenceGoldCase] = []
    remaining = list(triples)  # already sorted by (metric, period)
    count = min(per_ticker, len(remaining))

    def _normal_pick() -> list[MetricTriple]:
        return [remaining.pop(rng.randrange(len(remaining)))]

    for case_number in range(1, count + 1):
        picked: list[MetricTriple]
        available = [
            candidate
            for candidate in multi_candidates
            if candidate not in used_multi_keys
        ]
        if case_number % MULTI_PERIOD_EVERY == 0 and available:
            metric, periods = available[rng.randrange(len(available))]
            used_multi_keys.add((metric, periods))
            picked = [
                next(t for t in by_metric[metric] if t.period == period)
                for period in periods
            ]
        else:
            if not remaining:
                break
            picked = _normal_pick()
        cases.append(
            _build_case(
                ticker=ticker,
                metric=picked[0].metric,
                triples=picked,
                dimensioned=dimensioned,
                shard_label=shard_label,
            )
        )
    return cases


def generate_template_gold(
    release_root: Path,
    *,
    seed: int = DEFAULT_SEED,
    per_ticker: int = 3,
    tickers: list[str] | None = None,
) -> EvidenceGold:
    """Generate a deterministic template ``EvidenceGold`` from a built release.

    ``source_release`` is ``{"release_id": ..., "source_manifest_hash": ...}``
    where the hash is the plain file-sha256 (hex, no prefix) of
    ``indexes/shard_manifest.json`` — self-consistent with how the harness
    re-derives it, and deliberately independent of the router gold's
    manifest-hash derivation.
    """
    release_root = Path(release_root)
    if per_ticker < 1:
        raise ValueError(f"per_ticker must be >= 1, got {per_ticker}")
    manifest = _load_shard_manifest(release_root)
    shards: dict[str, Any] = manifest["shards"]
    manifest_path = release_root / SHARD_MANIFEST_RELPATH
    release_id = str(manifest.get("release_id") or release_root.name)

    if tickers is None:
        selected = sorted(shards)
    else:
        wanted = sorted({value.strip().upper() for value in tickers if value.strip()})
        if not wanted:
            raise ValueError("tickers filter resolved to an empty set")
        unknown = [value for value in wanted if value not in shards]
        if unknown:
            raise ValueError(f"tickers not in shard manifest: {unknown}")
        selected = wanted

    rng = random.Random(seed)
    cases: list[EvidenceGoldCase] = []
    for ticker in selected:
        entry_path = str(shards[ticker]["path"])
        cases.extend(
            _cases_for_ticker(
                rng=rng,
                ticker=ticker,
                shard_path=_resolve_shard_path(release_root, entry_path),
                shard_label=entry_path,
                per_ticker=per_ticker,
            )
        )
    return EvidenceGold(
        format_version=EVIDENCE_GOLD_FORMAT_VERSION,
        source_release={
            "release_id": release_id,
            "source_manifest_hash": _file_sha256_hex(manifest_path),
        },
        cases=cases,
    )


def inspect_objects(
    release_root: Path,
    ticker: str,
    metric: str,
    period: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Curation helper: matching object rows for one (ticker, metric, period).

    Matches ``metric_lookup.canonical_metric`` (falling back to the raw
    ``objects.metric_name``) without any review-status filter so a curator
    sees rejected and needs-review rows too.  Each row carries ``id``,
    ``period``, ``section``, ``review_status``, ``metric_name``,
    ``document_type``, and ``text_preview`` (first 80 chars).
    """
    release_root = Path(release_root)
    manifest = _load_shard_manifest(release_root)
    ticker = ticker.strip().upper()
    if ticker not in manifest["shards"]:
        raise ValueError(f"ticker not in shard manifest: {ticker}")
    shard_path = _resolve_shard_path(
        release_root, str(manifest["shards"][ticker]["path"])
    )
    with sqlite3.connect(f"file:{shard_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT o.id, o.period, o.section_name, o.review_status,
                   o.metric_name, o.document_type, o.text
            FROM objects o
            LEFT JOIN metric_lookup m ON m.object_id = o.id
            WHERE o.ticker = ? AND o.period = ?
              AND (m.canonical_metric = ? OR o.metric_name = ?)
            ORDER BY o.id
            LIMIT ?
            """,
            (ticker, period, metric, metric, limit),
        ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "period": str(row["period"]),
            "section": row["section_name"],
            "review_status": row["review_status"],
            "metric_name": row["metric_name"],
            "document_type": str(row["document_type"]),
            "text_preview": str(row["text"] or "")[:80],
        }
        for row in rows
    ]
