"""Headless evidence-gold harness: run gold cases through query_context_tool.

The runtime recipe mirrors ``tests/unit/test_shard_schema_v3.py`` (minimal
SO/CY2023 release) combined with the env wiring of
``tests/unit/test_mcp_server.py:_build_v3_runtime``; the env isolation fixture
mirrors ``test_mcp_server.py:_isolate_mcp_runtime_env``.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from krw_ontology.agent_index import build_spine_shard_release_outputs
from krw_ontology.eval_gold import harness
from krw_ontology.mcp_server import tools as mcp_tools
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.release import write_release_manifest_v3
from krw_ontology.utils.io import atomic_write_json, write_jsonl

RELEASE_ID = "evidence-gold-harness-test"
TICKER = "SO"
DOC_TYPE_KEY = "10K"
DOCUMENT_TYPE = "10-K"
PERIOD = "CY2023"
TEXT = "Server operating margin expanded because data center demand increased."
CLAIM_ID = f"claim:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:revenue-growth"
QUOTE_ID = f"quote:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:0001"
SPAN_ID = f"span:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:item7:0001"
SOURCE_DOCUMENT_ID = f"source:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}"
SUPPORT_LINK_ID = "support_link:SO:CY2023:10K:direct_quote_support:36ff437050"

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate_mcp_runtime_env():
    env_names = (
        "KRW_ONTOLOGY_ENV",
        "KRW_ONTOLOGY_RELEASE_ROOT",
        "KRW_ONTOLOGY_ROOT",
        "KRW_ONTOLOGY_MANIFEST_PATH",
        "KRW_ONTOLOGY_INDEX_LAYOUT",
        "KRW_ONTOLOGY_GLOBAL_SPINE_PATH",
        "KRW_ONTOLOGY_SHARD_MANIFEST_PATH",
        "KRW_MCP_STORE_MODE",
        "KRW_MCP_EXPECTED_CONTRACT_VERSION",
        "KRW_MCP_EXPECTED_TOOL_SCHEMA_SHA256",
        "KRW_MCP_EXPECTED_BUILD_ID",
        "KRW_MCP_EXPECTED_BACKEND_GIT_SHA",
        "KRW_MCP_EXPECTED_BUILD_FINGERPRINT_SHA256",
        "KRW_MCP_EXPECTED_RELEASE_MANIFEST_SHA256",
        "KRW_MCP_EXPECTED_SERVICE_FINGERPRINT_SHA256",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    for name in env_names:
        os.environ.pop(name, None)
    mcp_tools.reset_mcp_runtime_caches()
    try:
        yield
    finally:
        mcp_tools.reset_mcp_runtime_caches()
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _write_minimal_artifacts(root: Path) -> None:
    """Recipe from tests/unit/test_shard_schema_v3.py:_write_minimal_artifacts."""
    ontology_dir = root / "companies" / TICKER / "ontology" / DOC_TYPE_KEY / PERIOD
    sources_dir = root / "companies" / TICKER / "sources" / DOC_TYPE_KEY / PERIOD
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    span = {
        "id": SPAN_ID,
        "type": "SourceSpan",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "section_name": "item7",
        "section_key": "item7",
        "span_index": 1,
        "text": TEXT,
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    quote = {
        "id": QUOTE_ID,
        "type": "EvidenceQuote",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "source_span_id": SPAN_ID,
        "quote_text": TEXT,
        "quote_type": "business_update",
        "section_name": "item7",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    claim = {
        "id": CLAIM_ID,
        "type": "ResearchClaim",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "claim_text": "Server operating margin expanded on data center demand.",
        "claim_type": "business_update",
        "supported_by_quotes": [QUOTE_ID],
        "related_metrics": ["operating_margin"],
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    support_link = {
        "id": SUPPORT_LINK_ID,
        "type": "SupportLink",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "from_id": QUOTE_ID,
        "to_id": CLAIM_ID,
        "support_object_id": QUOTE_ID,
        "support_object_type": "EvidenceQuote",
        "target_object_id": CLAIM_ID,
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
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    build_indexes(
        ticker=TICKER,
        period=PERIOD,
        doc_type_key=DOC_TYPE_KEY,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=root,
        document_type=DOCUMENT_TYPE,
    )


def _build_runtime(root: Path) -> None:
    """Env wiring from tests/unit/test_mcp_server.py:_build_v3_runtime."""
    _write_minimal_artifacts(root)
    result = build_spine_shard_release_outputs(
        root,
        release_id=RELEASE_ID,
        workers=1,
        no_cache=True,
    )
    write_release_manifest_v3(root, release_id=RELEASE_ID, env="dev")
    os.environ["KRW_ONTOLOGY_ENV"] = "dev"
    os.environ["KRW_ONTOLOGY_RELEASE_ROOT"] = str(root.resolve())
    os.environ["KRW_ONTOLOGY_GLOBAL_SPINE_PATH"] = str(result.global_spine_path.resolve())
    os.environ["KRW_ONTOLOGY_SHARD_MANIFEST_PATH"] = str(result.shard_manifest_path.resolve())


def _margin_plan() -> dict:
    return {
        "question": "What drove SO server operating margin?",
        "intent": "evidence_lookup",
        "tickers": [TICKER],
        "document_types": [DOCUMENT_TYPE],
        "periods": [PERIOD],
        "clauses": [
            {
                "clause_id": "margin_driver",
                "retrieval_query": "SO server operating margin data center demand",
                "required_concepts": ["operating margin"],
            }
        ],
    }


def _buyback_plan() -> dict:
    return {
        "question": "Does SO disclose a share buyback program?",
        "intent": "evidence_lookup",
        "tickers": [TICKER],
        "document_types": [DOCUMENT_TYPE],
        "periods": [PERIOD],
        "clauses": [
            {
                "clause_id": "buyback",
                "retrieval_query": "SO share buyback repurchase program",
                "required_concepts": ["share buyback"],
            }
        ],
    }


def _gold_payload() -> dict:
    return {
        "format_version": "krw-ontology-evidence-gold/v1",
        "source_release": {"release_id": RELEASE_ID},
        "cases": [
            {
                "id": "c-positive",
                "question": "What drove SO server operating margin in CY2023?",
                "search_plan": _margin_plan(),
                "strata": ["template"],
                "expected": [
                    {
                        "ticker": TICKER,
                        "period": PERIOD,
                        "object_ids": [CLAIM_ID],
                    },
                    {
                        "ticker": TICKER,
                        "period": PERIOD,
                        "text_fragments": ["data center demand"],
                    },
                ],
            },
            {
                "id": "c-partial",
                "question": "Margin driver plus an anchor the corpus cannot satisfy.",
                "search_plan": _margin_plan(),
                "strata": ["vocabulary_mismatch"],
                "expected": [
                    {
                        "ticker": TICKER,
                        "object_ids": [CLAIM_ID],
                    },
                    {
                        "ticker": TICKER,
                        "text_fragments": ["capital return program"],
                    },
                ],
            },
            {
                "id": "c-negative",
                "question": "Does SO disclose a share buyback program?",
                "search_plan": _buyback_plan(),
                "strata": ["not_disclosed"],
                "expected": [],
                "expect_not_disclosed": True,
                "forbidden_fragments": ["share buyback", "repurchase program"],
            },
        ],
    }


def _write_gold(tmp_path: Path) -> Path:
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(_gold_payload(), indent=2), encoding="utf-8")
    return gold_path


def test_harness_end_to_end_on_minimal_release(tmp_path: Path):
    _build_runtime(tmp_path)
    gold_path = _write_gold(tmp_path)

    report = harness.run_harness(gold_path, label="test")

    assert report["label"] == "test"
    assert report["gold_path"] == str(gold_path)
    assert report["source_release"] == {"release_id": RELEASE_ID}
    assert report["overall"]["cases"] == 3
    assert report["overall"]["pass_rate"] == pytest.approx(2 / 3)
    assert report["overall"]["mean_recall"] == pytest.approx(5 / 6)
    assert report["overall"]["zero_hit_rate"] == pytest.approx(1 / 3)

    assert set(report["strata"]) == {"template", "vocabulary_mismatch", "not_disclosed"}
    assert report["strata"]["template"]["cases"] == 1
    assert report["strata"]["not_disclosed"]["pass_rate"] == 1.0

    positive = report["cases"]["c-positive"]
    assert positive["passed"] is True
    assert positive["recall"] == 1.0
    assert positive["zero_hit"] is False
    assert positive["not_disclosed_violation"] is False
    assert CLAIM_ID in positive["matched_object_ids"]

    partial = report["cases"]["c-partial"]
    assert partial["passed"] is False
    assert partial["recall"] == 0.5
    assert partial["zero_hit"] is False

    negative = report["cases"]["c-negative"]
    assert negative["passed"] is True
    assert negative["not_disclosed_violation"] is False
    assert negative["zero_hit"] is True


def test_harness_report_is_deterministic_across_runs(tmp_path: Path):
    _build_runtime(tmp_path)
    gold_path = _write_gold(tmp_path)

    first = harness.run_harness(gold_path, label="same")
    second = harness.run_harness(gold_path, label="same")

    for report in (first, second):
        for case in report["cases"].values():
            case["detail"].pop("elapsed_s", None)
    assert first == second


def test_compare_to_baseline_flags_only_regressions():
    report = {
        "overall": {"pass_rate": 0.9, "mean_recall": 0.8},
        "strata": {"template": {"pass_rate": 1.0, "mean_recall": 0.9}},
    }

    ok, regressions = harness.compare_to_baseline(report, copy.deepcopy(report))
    assert ok is True
    assert regressions == []

    improved = {
        "overall": {"pass_rate": 1.0, "mean_recall": 0.95},
        "strata": {"template": {"pass_rate": 1.0, "mean_recall": 1.0}},
    }
    ok, regressions = harness.compare_to_baseline(improved, report)
    assert ok is True
    assert regressions == []

    degraded = {
        "overall": {"pass_rate": 0.8, "mean_recall": 0.85},
        "strata": {"template": {"pass_rate": 0.5, "mean_recall": 0.95}},
    }
    ok, regressions = harness.compare_to_baseline(degraded, report)
    assert ok is False
    assert any("overall.pass_rate" in item for item in regressions)
    assert any("strata[template].pass_rate" in item for item in regressions)
    assert not any("overall.mean_recall" in item for item in regressions)

    ok, regressions = harness.compare_to_baseline(degraded, report, tolerance=0.6)
    assert ok is True
    assert regressions == []


def test_compare_to_baseline_handles_new_strata_and_missing_keys():
    baseline = {
        "overall": {"pass_rate": 1.0, "mean_recall": 1.0},
        "strata": {"template": {"pass_rate": 1.0, "mean_recall": 1.0}},
    }
    report = {
        "overall": {"pass_rate": 1.0, "mean_recall": 1.0, "cases": 2},
        "strata": {"curated": {"pass_rate": 1.0, "mean_recall": 1.0}},
    }
    ok, regressions = harness.compare_to_baseline(report, baseline)
    assert ok is True
    assert regressions == []


def test_render_report_markdown_sections(tmp_path: Path):
    _build_runtime(tmp_path)
    gold_path = _write_gold(tmp_path)
    report = harness.run_harness(gold_path, label="md-test")

    markdown = harness.render_report_markdown(report)
    assert "# Evidence Gold Benchmark" in markdown
    assert "md-test" in markdown
    assert "## Overall" in markdown
    assert "## Strata" in markdown
    assert "## Cases" in markdown
    assert "| c-positive | PASS |" in markdown
    assert "| c-partial | FAIL |" in markdown
    assert "template" in markdown
    assert "not_disclosed" in markdown


def test_cli_help_exits_zero():
    script = PROJECT_ROOT / "scripts" / "benchmark_evidence_gold.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "--gold" in proc.stdout
    assert "--baseline" in proc.stdout
