"""XBRL cross-validation audit core: lookup, unit conversion, compare, sampling.

Synthetic ``companyfacts`` dicts mirror the SEC shape
(``facts.us-gaap.<Tag>.units.<unit>[entries]``) and a minimal hand-built
release (shard manifest + objects-only sqlite shard) mirrors the real shard's
``MetricObservation`` json field names (``value``/``unit``/``scale``/
``fiscal_year``/``dimensions``).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from krw_ontology.eval_gold.table_audit import (
    XBRL_TAG_MAP,
    audit_shard_metrics,
    compare_value,
    fetch_companyfacts,
    select_tickers,
    xbrl_lookup,
)

# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def _fact_entry(
    *,
    end: str,
    val: float,
    fy: int,
    fp: str = "FY",
    form: str = "10-K",
    filed: str = "2024-02-21",
    frame: str | None = None,
    accn: str = "0000000000-24-000001",
    start: str | None = "2023-01-30",
) -> dict:
    entry: dict = {
        "start": start,
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }
    if frame is not None:
        entry["frame"] = frame
    return entry


def _facts_nvda_like() -> dict:
    return {
        "cik": 1045810,
        "entityName": "TEST CORP",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "units": {
                        "USD": [
                            # FY2024 annual fact (the target).
                            _fact_entry(
                                end="2024-01-28",
                                val=60_922_000_000.0,
                                fy=2024,
                                frame="CY2023",
                            ),
                            # Same fiscal year, but a 10-Q filing with a later
                            # end date — the 10-K/FY preference must outrank it.
                            _fact_entry(
                                end="2024-06-30",
                                val=55_000_000_000.0,
                                fy=2024,
                                fp="Q2",
                                form="10-Q",
                            ),
                            # Different fiscal year — must never be selected.
                            _fact_entry(
                                end="2023-01-29",
                                val=26_974_000_000.0,
                                fy=2023,
                            ),
                            # Comparative row inside a later 10-K: carries the
                            # later filing's fy AND a frame but an OLDER end
                            # date.  Latest end must outrank frame presence.
                            _fact_entry(
                                end="2022-01-30",
                                val=26_914_000_000.0,
                                fy=2024,
                                frame="CY2021",
                            ),
                            # Comparative restatement in the next 10-K: same
                            # fact period, filed later, fy of the new filing.
                            _fact_entry(
                                end="2024-01-28",
                                val=60_922_000_000.0,
                                fy=2025,
                                filed="2025-02-21",
                            ),
                        ]
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue from contract",
                    "units": {
                        "USD": [
                            # Lower-priority candidate tag; must rank after
                            # Revenues even though its end date is later.
                            _fact_entry(
                                end="2024-02-15",
                                val=61_000_000_000.0,
                                fy=2024,
                            )
                        ]
                    },
                },
            }
        },
    }


OBJECTS_DDL = """
CREATE TABLE objects (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    ticker TEXT NOT NULL,
    document_type TEXT NOT NULL,
    doc_type_key TEXT NOT NULL,
    period TEXT NOT NULL,
    source_document_id TEXT,
    section_name TEXT,
    metric_name TEXT,
    review_status TEXT,
    confidence TEXT,
    text TEXT NOT NULL,
    json TEXT NOT NULL,
    artifact_key TEXT NOT NULL,
    artifact_path TEXT NOT NULL
)
"""


def _metric_json(
    *,
    ticker: str,
    metric: str,
    value: float,
    fiscal_year: int,
    period: str,
    unit: str = "USD",
    scale: str = "ones",
    dimensions: dict | None = None,
    id_suffix: str = "",
) -> dict:
    return {
        "id": (f"metric_observation:{ticker}:{period}:10K:{metric}:{fiscal_year}{id_suffix}"),
        "type": "MetricObservation",
        "ticker": ticker,
        "document_type": "10-K",
        "period": period,
        "metric_name": metric,
        "value": value,
        "unit": unit,
        "scale": scale,
        "fiscal_year": fiscal_year,
        "dimensions": dimensions or {},
        "source_type": "xbrl",
    }


def _write_shard(
    path: Path,
    rows: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(OBJECTS_DDL)
        conn.executemany(
            """
            INSERT INTO objects (
                id, type, ticker, document_type, doc_type_key, period,
                source_document_id, section_name, metric_name, review_status,
                confidence, text, json, artifact_key, artifact_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["id"],
                    row["type"],
                    row["ticker"],
                    row["document_type"],
                    "10K",
                    row["period"],
                    f"source:{row['ticker']}:{row['period']}:10K",
                    None,
                    row.get("metric_name"),
                    row.get("review_status"),
                    None,
                    "unused",
                    json.dumps(row),
                    "unused",
                    "unused",
                )
                for row in rows
            ],
        )


def _build_release(
    root: Path,
    *,
    ticker_rows: dict[str, list[dict]],
) -> Path:
    release_root = root / "release"
    shards: dict[str, dict] = {}
    for ticker, rows in ticker_rows.items():
        shard_relpath = f"companies/{ticker}.sqlite"
        _write_shard(release_root / "indexes" / shard_relpath, rows)
        shards[ticker] = {"path": shard_relpath}
    manifest_path = release_root / "indexes" / "shard_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"release_id": "table-audit-test", "shards": shards}),
        encoding="utf-8",
    )
    return release_root


# ---------------------------------------------------------------------------
# compare_value
# ---------------------------------------------------------------------------


class TestCompareValue:
    def test_match_with_scale_conversion(self) -> None:
        # 1500 (millions) vs 1.5B raw.
        assert compare_value(1500.0, 1.5e9, unit="USD_millions") == "match"
        assert compare_value(1.5, 1.5e9, unit="USD_billions") == "match"
        # Bare scale token (no currency prefix).
        assert compare_value(1000.0, 1.0e6, unit="thousands") == "match"
        assert compare_value(1.0e9, 1.0e9, unit="USD") == "match"

    def test_match_band_within_0_1_percent(self) -> None:
        assert compare_value(1000.5, 1000.0, unit="USD") == "match"
        assert compare_value(999.5, 1000.0, unit="USD") == "match"

    def test_tolerance_band_up_to_1_percent(self) -> None:
        assert compare_value(1005.0, 1000.0, unit="USD") == "tolerance"
        # Exactly at the 1% boundary is still tolerance (inclusive).
        assert compare_value(1010.0, 1000.0, unit="USD") == "tolerance"

    def test_mismatch_beyond_1_percent(self) -> None:
        assert compare_value(1010.01, 1000.0, unit="USD") == "mismatch"
        assert compare_value(2.0e9, 1.5e9, unit="USD") == "mismatch"

    def test_per_share_unit_is_multiplier_one(self) -> None:
        assert compare_value(12.05, 12.05, unit="USD_per_share") == "match"
        assert compare_value(12.17, 12.05, unit="USD_per_share") == "tolerance"

    def test_zero_values(self) -> None:
        assert compare_value(0.0, 0.0, unit="USD") == "match"
        assert compare_value(5.0, 0.0, unit="USD") == "mismatch"

    def test_scale_conversion_uses_shard_side_only(self) -> None:
        # Shard 2.5 billions vs XBRL raw 2.4e9 -> ~4.2% -> mismatch.
        assert compare_value(2.5, 2.4e9, unit="USD_billions") == "mismatch"


# ---------------------------------------------------------------------------
# xbrl_lookup
# ---------------------------------------------------------------------------


class TestXbrlLookup:
    def test_selects_requested_fiscal_year_only(self) -> None:
        facts = _facts_nvda_like()
        entries = xbrl_lookup(facts, "revenue", 2024)
        assert entries, "expected FY2024 revenue entries"
        assert all(entry["fy"] == 2024 for entry in entries)

    def test_prefers_10k_fy_latest_end(self) -> None:
        facts = _facts_nvda_like()
        entries = xbrl_lookup(facts, "revenue", 2024)
        best = entries[0]
        # 10-K annual fact wins over the later-dated 10-Q entry.
        assert best["form"] == "10-K"
        assert best["fp"] == "FY"
        assert best["end"] == "2024-01-28"
        assert best["val"] == 60_922_000_000.0

    def test_latest_end_outranks_frame_bearing_comparative(self) -> None:
        # Regression (real NVDA companyfacts shape): comparative rows inside
        # later 10-Ks carry the later filing's fy AND frames, with older end
        # dates.  End recency must decide, or a 2-year-old comparative wins.
        facts = _facts_nvda_like()
        entries = xbrl_lookup(facts, "revenue", 2024)
        assert entries[0]["end"] == "2024-01-28"
        assert entries[0]["val"] == 60_922_000_000.0
        framed_comparatives = [entry for entry in entries if entry.get("frame") == "CY2021"]
        assert framed_comparatives, "fixture must keep the framed comparative"
        assert entries.index(framed_comparatives[0]) > 0

    def test_tag_candidates_ranked_in_map_order(self) -> None:
        facts = _facts_nvda_like()
        entries = xbrl_lookup(facts, "revenue", 2024)
        # First candidate tag (Revenues) ranks before the second candidate even
        # though the second candidate's end date is later.
        assert entries[0]["val"] == 60_922_000_000.0

    def test_unmapped_metric_returns_empty(self) -> None:
        facts = _facts_nvda_like()
        assert xbrl_lookup(facts, "free_cash_flow_yield", 2024) == []

    def test_missing_fiscal_year_returns_empty(self) -> None:
        facts = _facts_nvda_like()
        assert xbrl_lookup(facts, "revenue", 1999) == []

    def test_tag_map_initial_scope(self) -> None:
        for metric in (
            "revenue",
            "operating_income",
            "net_income",
            "eps",
            "research_and_development",
            "total_debt",
        ):
            assert metric in XBRL_TAG_MAP
            assert XBRL_TAG_MAP[metric], f"{metric} has no tag candidates"
        assert "Revenues" in XBRL_TAG_MAP["revenue"]
        assert "EarningsPerShareBasic" in XBRL_TAG_MAP["eps"]


# ---------------------------------------------------------------------------
# fetch_companyfacts (cache path only)
# ---------------------------------------------------------------------------


class TestFetchCompanyfacts:
    def test_cache_hit_loads_file(self, tmp_path: Path) -> None:
        facts = _facts_nvda_like()
        cache_dir = tmp_path / "sec-companyfacts"
        cache_dir.mkdir()
        (cache_dir / "NVDA.json").write_text(json.dumps(facts), encoding="utf-8")

        loaded = fetch_companyfacts("nvda", cache_dir=cache_dir, ua="test-agent")
        assert loaded == facts

    def test_cache_miss_without_cik_returns_none(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "empty-cache"
        cache_dir.mkdir()
        assert fetch_companyfacts("NVDA", cache_dir=cache_dir, ua="test-agent") is None


# ---------------------------------------------------------------------------
# select_tickers
# ---------------------------------------------------------------------------


class TestSelectTickers:
    def test_none_returns_sorted_all(self) -> None:
        import random

        assert select_tickers(["ZULU", "ALFA", "MIKE"], tickers=None, rng=random.Random(7)) == [
            "ALFA",
            "MIKE",
            "ZULU",
        ]

    def test_explicit_list_uppercased_and_validated(self) -> None:
        import random

        assert select_tickers(["ZULU", "ALFA"], tickers=["alfa"], rng=random.Random(7)) == ["ALFA"]

    def test_count_sampling_deterministic(self) -> None:
        import random

        all_tickers = [f"T{i:03d}" for i in range(50)]
        first = select_tickers(all_tickers, tickers=10, rng=random.Random(42))
        second = select_tickers(all_tickers, tickers=10, rng=random.Random(42))
        assert first == second
        assert len(first) == 10
        assert first == sorted(first)

    def test_unknown_ticker_raises(self) -> None:
        import random

        try:
            select_tickers(["ALFA"], tickers=["NOPE"], rng=random.Random(1))
        except ValueError as exc:
            assert "NOPE" in str(exc)
        else:  # pragma: no cover - branch guard
            raise AssertionError("expected ValueError for unknown ticker")


# ---------------------------------------------------------------------------
# audit_shard_metrics (cache-hit integration + determinism)
# ---------------------------------------------------------------------------


def _audit_release_rows() -> dict[str, list[dict]]:
    return {
        "TST": [
            _metric_json(
                ticker="TST",
                metric="revenue",
                value=60_922_000_000.0,
                fiscal_year=2024,
                period="CY2024",
            ),
            _metric_json(
                ticker="TST",
                metric="revenue",
                value=26_974_000_000.0,
                fiscal_year=2023,
                period="CY2024",
            ),
            _metric_json(
                ticker="TST",
                metric="eps",
                value=12.05,
                fiscal_year=2024,
                period="CY2024",
                unit="USD_per_share",
            ),
        ],
        "NOC": [
            _metric_json(
                ticker="NOC",
                metric="revenue",
                value=1_000_000_000.0,
                fiscal_year=2024,
                period="CY2024",
            ),
        ],
    }


class TestAuditShardMetrics:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path]:
        facts = _facts_nvda_like()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "TST.json").write_text(json.dumps(facts), encoding="utf-8")
        release_root = _build_release(tmp_path, ticker_rows=_audit_release_rows())
        return release_root, cache_dir

    def test_cache_hit_rows_get_verdicts(self, tmp_path: Path) -> None:
        release_root, cache_dir = self._setup(tmp_path)
        report = audit_shard_metrics(
            release_root,
            tickers=["TST", "NOC"],
            seed=1,
            per_ticker=3,
            cache_dir=cache_dir,
            ua="test-agent",
        )
        rows = report["rows"]
        assert len(rows) == 4  # 3 TST + 1 NOC
        by_key = {(row["ticker"], row["metric"], row["fiscal_year"]): row for row in rows}

        revenue_2024 = by_key[("TST", "revenue", 2024)]
        assert revenue_2024["verdict"] == "match"
        assert revenue_2024["xbrl_value"] == 60_922_000_000.0
        assert revenue_2024["shard_value"] == 60_922_000_000.0

        revenue_2023 = by_key[("TST", "revenue", 2023)]
        # The synthetic facts carry one fy=2023 entry whose value equals the
        # shard row -> match.
        assert revenue_2023["verdict"] == "match"
        assert revenue_2023["xbrl_value"] == 26_974_000_000.0

        eps_row = by_key[("TST", "eps", 2024)]
        assert eps_row["verdict"] == "missing_xbrl"

        noc_row = by_key[("NOC", "revenue", 2024)]
        assert noc_row["verdict"] == "no_cik"

        summary = report["summary"]
        assert summary["match"] == 2
        assert summary["missing_xbrl"] == 1
        assert summary["no_cik"] == 1
        assert summary["mismatch"] == 0
        assert summary["tolerance"] == 0
        assert summary["no_tag_map"] == 0

    def test_cik_map_enables_fetch_path_without_cache(self, tmp_path: Path, monkeypatch) -> None:
        release_root, cache_dir = self._setup(tmp_path)
        captured: dict = {}

        class _FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return _facts_nvda_like()

        class _FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self) -> "_FakeClient":
                return self

            def __exit__(self, *args) -> None:
                return None

            def get(self, url: str) -> _FakeResponse:
                captured["url"] = url
                return _FakeResponse()

        import krw_ontology.eval_gold.table_audit as table_audit

        monkeypatch.setattr(table_audit.httpx, "Client", _FakeClient)
        report = audit_shard_metrics(
            release_root,
            tickers=["NOC"],
            seed=1,
            per_ticker=1,
            cache_dir=cache_dir,
            ua="test-agent",
            cik_map={"NOC": "0001045810"},
        )
        assert captured["url"].endswith("/CIK0001045810.json")
        assert report["rows"][0]["verdict"] in ("match", "tolerance", "mismatch")
        # Fetch result was persisted to the cache for the next run.
        assert (cache_dir / "NOC.json").is_file()

    def test_same_seed_same_sample(self, tmp_path: Path) -> None:
        release_root, cache_dir = self._setup(tmp_path)
        kwargs = {
            "tickers": ["TST"],
            "seed": 5,
            "per_ticker": 2,
            "cache_dir": cache_dir,
            "ua": "test-agent",
        }
        first = audit_shard_metrics(release_root, **kwargs)
        second = audit_shard_metrics(release_root, **kwargs)
        assert first["rows"] == second["rows"]
        assert first["summary"] == second["summary"]

    def test_per_ticker_caps_rows(self, tmp_path: Path) -> None:
        release_root, cache_dir = self._setup(tmp_path)
        report = audit_shard_metrics(
            release_root,
            tickers=["TST"],
            seed=5,
            per_ticker=1,
            cache_dir=cache_dir,
            ua="test-agent",
        )
        assert len(report["rows"]) == 1

    def test_dimensioned_and_rejected_rows_excluded(self, tmp_path: Path) -> None:
        release_root, cache_dir = self._setup(tmp_path)
        facts = _facts_nvda_like()
        (cache_dir / "DIM.json").write_text(json.dumps(facts), encoding="utf-8")
        _write_shard(
            release_root / "indexes" / "companies" / "DIM.sqlite",
            [
                _metric_json(
                    ticker="DIM",
                    metric="revenue",
                    value=1.0,
                    fiscal_year=2024,
                    period="CY2024",
                    dimensions={"segment": "Data Center"},
                    id_suffix=":dim",
                ),
                dict(
                    _metric_json(
                        ticker="DIM",
                        metric="revenue",
                        value=2.0,
                        fiscal_year=2024,
                        period="CY2024",
                        id_suffix=":rejected",
                    ),
                    review_status="rejected",
                ),
                _metric_json(
                    ticker="DIM",
                    metric="free_cash_flow_yield",
                    value=0.1,
                    fiscal_year=2024,
                    period="CY2024",
                    unit="percent",
                ),
            ],
        )
        # manifest needs the DIM entry
        manifest_path = release_root / "indexes" / "shard_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["shards"]["DIM"] = {"path": "companies/DIM.sqlite"}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = audit_shard_metrics(
            release_root,
            tickers=["DIM"],
            seed=3,
            per_ticker=5,
            cache_dir=cache_dir,
            ua="test-agent",
        )
        assert report["rows"] == []
        assert report["summary"]["total"] == 0
