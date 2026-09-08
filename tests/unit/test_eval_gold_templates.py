"""Deterministic template gold generator: sample shard triples into gold cases.

The release recipe mirrors ``tests/unit/test_shard_schema_v3.py`` (minimal
SO/CY2023 artifacts) extended, per the task-4 binding resolutions, with
``metric_observations.jsonl`` material for two tickers and two periods so
period sampling, the ``dimensioned`` stratum, and the ``multi_period``
stratum all have data to draw from.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest

from krw_ontology.agent_index import build_spine_shard_release_outputs
from krw_ontology.eval_gold import templates
from krw_ontology.eval_gold.schema import EvidenceGold
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.utils.io import atomic_write_json, write_jsonl

RELEASE_ID = "evidence-gold-templates-test"
DOC_TYPE_KEY = "10K"
DOCUMENT_TYPE = "10-K"
TEXT = "Server operating margin expanded because data center demand increased."

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# (ticker, period, metric_name, value, dimensions)
SO_METRICS = (
    ("SO", "CY2023", "revenue", 5_236.0, {}),
    ("SO", "CY2023", "operating_margin", 0.41, {}),
    ("SO", "CY2023", "segment_revenue", 4_001.0, {"segment": "Server"}),
    ("SO", "CY2024", "revenue", 6_102.0, {}),
    ("SO", "CY2024", "operating_margin", 0.46, {}),
    ("SO", "CY2024", "segment_revenue", 4_812.0, {"segment": "Server"}),
)
VG_METRICS = (
    ("VG", "CY2023", "revenue", 3_421.0, {}),
    ("VG", "CY2023", "net_income", 712.0, {}),
)
ALL_METRICS = SO_METRICS + VG_METRICS


def _metric_object(
    ticker: str,
    period: str,
    metric_name: str,
    value: float,
    dimensions: dict,
) -> dict:
    return {
        "id": f"metric_observation:{ticker}:{period}:{DOC_TYPE_KEY}:{metric_name}",
        "type": "MetricObservation",
        "ticker": ticker,
        "source_document_id": f"source:{ticker}:{period}:{DOC_TYPE_KEY}",
        "document_type": DOCUMENT_TYPE,
        "period": period,
        "metric_name": metric_name,
        "value": value,
        "unit": "USD",
        "fiscal_year": int(period.removeprefix("CY")),
        "fiscal_period": period,
        "period_type": "annual",
        "source_fact_ids": [f"xbrl:{ticker}:{period}:{DOC_TYPE_KEY}:{metric_name}"],
        "source_type": "reported",
        "dimensions": dimensions,
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }


def _write_document(root: Path, ticker: str, period: str, metrics: tuple) -> None:
    """One (ticker, period) document: minimal v3 artifacts + metric observations."""
    ontology_dir = root / "companies" / ticker / "ontology" / DOC_TYPE_KEY / period
    sources_dir = root / "companies" / ticker / "sources" / DOC_TYPE_KEY / period
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)
    source_document_id = f"source:{ticker}:{period}:{DOC_TYPE_KEY}"
    span_id = f"span:{ticker}:{period}:{DOC_TYPE_KEY}:item7:0001"
    quote_id = f"quote:{ticker}:{period}:{DOC_TYPE_KEY}:0001"
    claim_id = f"claim:{ticker}:{period}:{DOC_TYPE_KEY}:revenue-growth"
    support_link_id = (
        f"support_link:{ticker}:{period}:{DOC_TYPE_KEY}:direct_quote_support:36ff437050"
    )
    span = {
        "id": span_id,
        "type": "SourceSpan",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": DOCUMENT_TYPE,
        "period": period,
        "section_name": "item7",
        "section_key": "item7",
        "span_index": 1,
        "text": TEXT,
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    quote = {
        "id": quote_id,
        "type": "EvidenceQuote",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": DOCUMENT_TYPE,
        "period": period,
        "source_span_id": span_id,
        "quote_text": TEXT,
        "quote_type": "business_update",
        "section_name": "item7",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    claim = {
        "id": claim_id,
        "type": "ResearchClaim",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": DOCUMENT_TYPE,
        "period": period,
        "claim_text": "Server operating margin expanded on data center demand.",
        "claim_type": "business_update",
        "supported_by_quotes": [quote_id],
        "related_metrics": ["operating_margin"],
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    support_link = {
        "id": support_link_id,
        "type": "SupportLink",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": DOCUMENT_TYPE,
        "period": period,
        "from_id": quote_id,
        "to_id": claim_id,
        "support_object_id": quote_id,
        "support_object_type": "EvidenceQuote",
        "target_object_id": claim_id,
        "target_object_type": "ResearchClaim",
        "support_type": "direct_quote_support",
        "support_role": "quote_support",
        "stance": "supports",
        "support_strength": "direct",
        "inference_level": "direct_quote",
        "evidence_grade": "direct",
        "evidence_strength": "direct",
        "requires_inference": False,
        "created_by": "deterministic_projection",
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    write_jsonl(ontology_dir / "spans.jsonl", [span])
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", [quote])
    write_jsonl(ontology_dir / "claims.jsonl", [claim])
    write_jsonl(ontology_dir / "support_links.jsonl", [support_link])
    write_jsonl(
        ontology_dir / "metric_observations.jsonl",
        [
            _metric_object(ticker, period, metric_name, value, dimensions)
            for _t, _p, metric_name, value, dimensions in metrics
            if _t == ticker and _p == period
        ],
    )
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    build_indexes(
        ticker=ticker,
        period=period,
        doc_type_key=DOC_TYPE_KEY,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=root,
        document_type=DOCUMENT_TYPE,
    )


def _build_metric_release(root: Path) -> Path:
    """Build a two-ticker, two-period minimal v3 release; return its root."""
    documents = {
        ("SO", "CY2023"): SO_METRICS,
        ("SO", "CY2024"): SO_METRICS,
        ("VG", "CY2023"): VG_METRICS,
    }
    for (ticker, period), metrics in documents.items():
        _write_document(root, ticker, period, metrics)
    build_spine_shard_release_outputs(
        root,
        release_id=RELEASE_ID,
        workers=1,
        no_cache=True,
    )
    manifest_path = root / "indexes" / "shard_manifest.json"
    assert manifest_path.is_file()
    return root


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("template-gold-release")
    yield _build_metric_release(root)


def _shard_for(release_root: Path, ticker: str) -> Path:
    manifest = json.loads(
        (release_root / "indexes" / "shard_manifest.json").read_text(encoding="utf-8")
    )
    entry_path = Path(manifest["shards"][ticker]["path"])
    if entry_path.is_absolute():
        return entry_path
    return release_root / "indexes" / entry_path


def _cases_per_ticker(gold: EvidenceGold) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in gold.cases:
        tickers = {item.ticker for item in case.expected}
        assert len(tickers) == 1
        ticker = tickers.pop()
        counts[ticker] = counts.get(ticker, 0) + 1
    return counts


def test_generates_cases_for_every_ticker_and_respects_per_ticker(release: Path):
    gold = templates.generate_template_gold(release, seed=7, per_ticker=3)

    counts = _cases_per_ticker(gold)
    assert set(counts) == {"SO", "VG"}
    # (a) case count ~= per_ticker x tickers: at least one per ticker, never more.
    for ticker, count in counts.items():
        assert 1 <= count <= 3
    # SO has 3 metric/period triples available; VG has 2.
    assert counts == {"SO": 3, "VG": 2}


def test_ticker_filter_restricts_generation(release: Path):
    gold = templates.generate_template_gold(release, seed=7, per_ticker=2, tickers=["SO"])
    counts = _cases_per_ticker(gold)
    assert counts == {"SO": 2}


def test_unknown_ticker_rejected(release: Path):
    with pytest.raises(ValueError, match="ZZZZ"):
        templates.generate_template_gold(release, seed=7, tickers=["ZZZZ"])


def test_every_case_validates_and_roundtrips(release: Path):
    gold = templates.generate_template_gold(release, seed=11, per_ticker=3)

    assert gold.format_version == "krw-ontology-evidence-gold/v1"
    assert gold.source_release["release_id"] == RELEASE_ID
    manifest_path = release / "indexes" / "shard_manifest.json"
    import hashlib

    expected_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert gold.source_release["source_manifest_hash"] == expected_hash

    # (b) roundtrip through EvidenceGold re-validates every case.
    reloaded = EvidenceGold.model_validate(gold.model_dump())
    assert [case.id for case in reloaded.cases] == [case.id for case in gold.cases]

    for case in gold.cases:
        plan = case.search_plan
        assert plan["tickers"] == [case.expected[0].ticker]
        clause = plan["clauses"][0]
        assert len(plan["clauses"]) == 1
        assert clause["metrics"] == [clause["metrics"][0]]
        assert set(clause["metrics"][0].replace("_", " ").split()) <= set(
            clause["retrieval_query"].split()
        )


def test_same_seed_is_byte_identical(release: Path):
    first = templates.generate_template_gold(release, seed=20260908, per_ticker=3)
    second = templates.generate_template_gold(release, seed=20260908, per_ticker=3)
    # (c) whole-document determinism, not just equal counts.
    assert json.dumps(first.model_dump(), sort_keys=True) == json.dumps(
        second.model_dump(), sort_keys=True
    )


def test_expected_object_ids_exist_in_shard_objects_table(release: Path):
    gold = templates.generate_template_gold(release, seed=3, per_ticker=3)

    import sqlite3

    # (d) every expected anchor is a real row in the ticker's shard.
    for case in gold.cases:
        for item in case.expected:
            shard = _shard_for(release, item.ticker)
            with sqlite3.connect(f"file:{shard}?mode=ro", uri=True) as conn:
                placeholders = ",".join("?" for _ in item.object_ids)
                rows = conn.execute(
                    f"SELECT id, period FROM objects WHERE id IN ({placeholders})",
                    list(item.object_ids),
                ).fetchall()
            found = {row[0] for row in rows}
            assert found == set(item.object_ids)
            for row in rows:
                if item.period is not None:
                    assert row[1] == item.period


def test_dimensioned_and_multi_period_strata(release: Path):
    gold = templates.generate_template_gold(release, seed=5, per_ticker=5, tickers=["SO"])

    assert len(gold.cases) == 5
    strata_seen = {s for case in gold.cases for s in case.strata}
    assert "multi_period" in strata_seen

    for case in gold.cases:
        metric = case.search_plan["clauses"][0]["metrics"][0]
        if metric == "segment_revenue":
            assert "dimensioned" in case.strata
        if "multi_period" in case.strata:
            # Same metric across exactly 2 periods: 2 expected items, plan periods match.
            periods = sorted(item.period for item in case.expected)
            assert len(periods) == 2
            assert case.search_plan["periods"] == periods
            assert len(case.expected) == 2
        else:
            assert len(case.expected) == 1
            assert case.search_plan["periods"] == [case.expected[0].period]


def test_inspect_objects_returns_known_object_row(release: Path):
    rows = templates.inspect_objects(release, "SO", "revenue", "CY2023")

    assert rows, "expected at least one revenue object for SO CY2023"
    known = [row for row in rows if row["id"].endswith(":10K:revenue")]
    assert known, f"metric observation id missing from {rows}"
    row = known[0]
    assert row["review_status"] == "accepted"
    assert row["period"] == "CY2023"
    assert set(row) >= {
        "id",
        "period",
        "section",
        "review_status",
        "text_preview",
    }
    assert len(row["text_preview"]) <= 80


def test_cli_help_exits_zero():
    script = PROJECT_ROOT / "scripts" / "generate_evidence_gold_templates.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "generate" in proc.stdout
    assert "inspect-objects" in proc.stdout
