"""Tests for the krw-ontology MCP tool layer."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from krw_ontology.agent_index import builder as agent_index_builder
from krw_ontology.agent_index import store as agent_index_store
from krw_ontology.agent_index import OntologySpineRouter, build_spine_shard_release_outputs
from krw_ontology.agent_index.chart_series import query_chart_series_pack
from krw_ontology.agent_index.builder import build_agent_index as _build_legacy_agent_index
from krw_ontology.agent_index.store import OntologyStore
from krw_ontology.mcp_server.http_server import prepare_mcp_runtime
from krw_ontology.mcp_server import server as mcp_server
from krw_ontology.mcp_server import tools as mcp_tools
from krw_ontology.mcp_server.server import (
    diagnostics_payload,
    health_payload,
    live_payload,
    metrics_payload,
    mcp,
    ready_payload,
)
from krw_ontology.mcp_server.tools import (
    _normalize_object_types,
    catalog_tool,
    chain_tool,
    company_context_tool,
    compare_tool,
    index_context_tool,
    plan_query_tool,
    quality_tool,
    query_context_tool,
    query_tool,
    retrieve_tool,
    ResponseDetail,
    trace_tool,
    topic_map_tool,
    verify_evidence_tool,
    ResponseFormat,
)
from krw_ontology.release import write_release_manifest_v3
from krw_ontology.utils.io import atomic_write_json, write_jsonl


def build_agent_index(*args, **kwargs):
    kwargs.setdefault("allow_internal_legacy_builder", True)
    return _build_legacy_agent_index(*args, **kwargs)


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
    )
    old_env = {name: os.environ.get(name) for name in env_names}
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


def test_mcp_tools_query_trace_quality_and_compare(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    index = _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    catalog = json.loads(catalog_tool())
    assert catalog["global_spine_path"] == str(index["index_path"])
    assert catalog["companies"] == ["VG"]
    assert catalog["documents"][0]["period"] == "FY2025"

    query = json.loads(
        query_tool(
            topic="revenue growth",
            tickers=["VG"],
            document_types=["10-K"],
            object_types=["ResearchClaim", "BusinessFactor"],
            limit=5,
        )
    )
    assert query["results"]
    assert query["results"][0]["ticker"] == "VG"
    assert query["results"][0]["evidence"]["quotes"] == []
    assert "events" in query["results"][0]["quality"]
    assert "evidence_grade" in query["results"][0]["quality"]
    assert query["search_diagnostics"]["normalized_terms"] == ["revenue", "growth"]
    assert query["search_diagnostics"]["fts_query"] == "revenue* growth*"
    assert query["response_detail"] == "compact"
    assert "object" not in query["results"][0]
    assert "document" not in query["results"][0]

    trace = json.loads(trace_tool(object_id="claim:VG:FY2025:10K:revenue-growth"))
    assert trace["object"]["type"] == "ResearchClaim"
    assert trace["evidence"]["quotes"][0]["id"] == "quote:VG:FY2025:10K:0001"
    assert trace["routing"]["mode"] == "object_locator"
    assert trace["routing"]["fallback"] is False
    assert trace["object_locator"]["object_id"] == "claim:VG:FY2025:10K:revenue-growth"
    assert trace["object_locator"]["ticker"] == "VG"
    assert trace["object_locator"]["shard_available"] is True
    assert trace["object_locator"]["shard_missing"] is False

    chain = json.loads(chain_tool(object_id="business_factor:VG:FY2025:10K:revenue-growth"))
    assert chain["object"]["type"] == "BusinessFactor"
    assert chain["object_locator"]["object_id"] == "business_factor:VG:FY2025:10K:revenue-growth"
    assert chain["object_locator"]["ticker"] == "VG"
    assert chain["object_locator"]["shard_available"] is True
    assert chain["chain"]["evidence_chain"]["claims"][0]["id"] == "claim:VG:FY2025:10K:revenue-growth"
    assert chain["chain"]["evidence_chain"]["quotes"][0]["id"] == "quote:VG:FY2025:10K:0001"
    assert "text" not in chain["chain"]["evidence_chain"]["quotes"][0]
    assert {
        neighbor["object"]["id"] for neighbor in chain["chain"]["semantic_neighbors"]
    } >= {
        "business_activity:VG:FY2025:10K:lng-sales",
        "external_factor_exposure:VG:FY2025:10K:natural-gas-price-operating-margin",
    }

    quality = json.loads(quality_tool(ticker="VG"))
    assert quality["summary"]["documents"] == 2
    assert quality["summary"]["section_warnings"] == 0

    compare = json.loads(
        compare_tool(
            tickers=["VG", "XOM"],
            topic="revenue growth",
            document_types=["10-K"],
            limit_per_ticker=2,
        )
    )
    assert compare["results"]["VG"]
    assert compare["results"]["XOM"] == []
    assert compare["comparison_contexts"]["VG"]["research_status"] in {
        "sufficient_for_default_answer",
        "sufficient_but_trace_recommended",
        "partial_answer_possible",
    }
    assert compare["comparison_contexts"]["VG"]["agent_autonomy"]["mode"] == "bounded"
    assert "strong_claim_allowed" in compare["comparison_contexts"]["VG"]["directness_guard"]
    summary = compare["comparison_contexts"]["VG"]["research_pack_summary"]
    assert "metric_series_count" in summary
    assert "metric_growth_difference_count" in summary
    assert "metric_period_alignment" in summary
    assert {row["comparison_key"] for row in compare["comparison_rows"]} == {"VG", "XOM"}
    vg_row = next(row for row in compare["comparison_rows"] if row["comparison_key"] == "VG")
    xom_row = next(row for row in compare["comparison_rows"] if row["comparison_key"] == "XOM")
    assert vg_row["missing"] is False
    assert vg_row["ticker"] == "VG"
    assert vg_row["source_label"] == "VG FY2025 10-K"
    assert vg_row["object_id"]
    assert xom_row["missing"] is True
    assert xom_row["missing_reason"] == "no_matching_ontology_objects"

    plan = json.loads(plan_query_tool(question="VG 최근 10-K revenue growth 근거 찾아줘"))
    assert plan["plan"]["tickers"] == ["VG"]
    assert plan["plan"]["document_types"] == ["10-K"]


def test_mcp_verify_evidence_returns_hash_stable_source_lineage(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path, release_id="verified-evidence-release")
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    questions = [
        {
            "question_id": "q_business_quality",
            "object_ids": [
                "claim:VG:FY2025:10K:revenue-growth",
                "business_factor:VG:FY2025:10K:revenue-growth",
                "business_factor:VG:FY2025:10K:unsupported-risk",
            ],
        }
    ]
    first = json.loads(verify_evidence_tool(ticker="VG", questions=questions))
    second = json.loads(verify_evidence_tool(ticker="VG", questions=questions))

    assert first == second
    assert first["format"] == "krw-verified-company-evidence/v1"
    assert first["release_id"] == "verified-evidence-release"
    assert len(first["pack_hash"]) == 64
    assert first["current_driver"]["period"] == "FY2025"
    assert first["annual_baseline"]["period"] == "FY2025"
    evidence = first["evidence_by_question"][0]["evidence"]
    assert evidence[0]["trace_status"] == "traceable_direct"
    assert evidence[0]["usable_for_strong_claim"] is True
    assert evidence[0]["quote_ids"] == ["quote:VG:FY2025:10K:0001"]
    assert evidence[0]["span_ids"] == ["span:VG:FY2025:10K:0001"]
    assert evidence[1]["trace_status"] == "traceable_related"
    assert evidence[1]["usable_for_strong_claim"] is False
    assert first["rejected_refs"] == [
        {
            "question_id": "q_business_quality",
            "object_id": "business_factor:VG:FY2025:10K:unsupported-risk",
            "reason": "missing_source_lineage",
        }
    ]


def test_mcp_verify_evidence_rejects_wrong_ticker_and_limits_ids(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    wrong_ticker = json.loads(
        verify_evidence_tool(
            ticker="XOM",
            questions=[
                {
                    "question_id": "q_wrong_ticker",
                    "object_ids": ["claim:VG:FY2025:10K:revenue-growth"],
                }
            ],
        )
    )
    assert wrong_ticker["verification_summary"]["verified_object_count"] == 0
    assert wrong_ticker["rejected_refs"][0]["reason"] == "ticker_mismatch"

    with pytest.raises(ValueError, match="at most 8 unique object ids"):
        verify_evidence_tool(
            ticker="VG",
            questions=[
                {
                    "question_id": f"q_{index}",
                    "object_ids": [f"object:{index}", f"object:{index + 10}"],
                }
                for index in range(5)
            ],
        )


def test_mcp_health_reports_release_manifest(tmp_path: Path, monkeypatch):
    current = _write_v3_current_release(tmp_path, release_id="20260612_010000")
    release = current.resolve()
    monkeypatch.setenv("KRW_ONTOLOGY_ENV", "prod")
    monkeypatch.setenv("KRW_ONTOLOGY_RELEASE_ROOT", str(current))
    monkeypatch.delenv("KRW_ONTOLOGY_GLOBAL_SPINE_PATH", raising=False)

    payload, status_code = health_payload()

    assert status_code == 200
    assert payload["ok"] is True
    assert payload["env"] == "prod"
    assert payload["release_id"] == "20260612_010000"
    assert payload["root"] == str(release.resolve())
    assert payload["supplied_root"] == str(current.absolute())
    assert payload["root_is_current_symlink"] is True
    assert payload["current_symlink"] is True
    assert payload["current_symlink_target"] == release.name
    assert payload["current_release_id"] == release.name
    assert payload["manifest_valid"] is True
    assert payload["manifest_path"] == str(release / "manifest.json")
    assert payload["documents"] == 2
    assert payload["objects"] >= 1
    assert payload["index_layout"] == "global-spine-and-company-shards"
    assert payload["global_spine_path"] == str(release / "indexes" / "global_spine.sqlite")
    assert payload["global_spine_manifest_path"] == "indexes/global_spine.sqlite"
    assert payload["global_spine_present"] is True
    assert payload["company_shards_present"] is True
    assert payload["company_shard_count"] == 1
    assert payload["global_topic_spine_present"] is True
    assert payload["global_topic_spine_count"] > 0
    assert "global_topics_present" not in payload
    assert "global_topic_count" not in payload
    assert payload["company_shards_dir"] == "indexes/companies"
    assert payload["mcp_store_hot_swap"]["rotations"] == 0
    assert payload["mcp_store_hot_swap"]["retired_leased"] == 0


def test_mcp_health_rejects_configured_prod_non_current_root(tmp_path: Path, monkeypatch):
    current = _write_v3_current_release(tmp_path, release_id="20260612_010000")
    release = current.resolve()
    monkeypatch.setenv("KRW_ONTOLOGY_ENV", "prod")
    monkeypatch.setenv("KRW_ONTOLOGY_RELEASE_ROOT", str(release))
    monkeypatch.delenv("KRW_ONTOLOGY_GLOBAL_SPINE_PATH", raising=False)

    payload, status_code = health_payload()

    assert status_code == 503
    assert payload["ok"] is False
    assert payload["error"] == "prod_current_symlink_required"
    assert payload["root_is_current_symlink"] is False
    assert payload["current_symlink"] is True
    assert payload["current_symlink_target"] == release.name


def test_mcp_prepare_runtime_requires_prod_current_symlink(tmp_path: Path):
    current = _write_v3_current_release(tmp_path, release_id="20260612_010000")
    env_names = (
        "KRW_ONTOLOGY_ENV",
        "KRW_ONTOLOGY_RELEASE_ROOT",
        "KRW_ONTOLOGY_ROOT",
        "KRW_ONTOLOGY_MANIFEST_PATH",
        "KRW_ONTOLOGY_GLOBAL_SPINE_PATH",
        "KRW_ONTOLOGY_SHARD_MANIFEST_PATH",
        "KRW_MCP_STORE_MODE",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    for name in env_names:
        os.environ.pop(name, None)

    mcp_tools.reset_mcp_runtime_caches()
    try:
        verification = prepare_mcp_runtime(root=current, env="prod")
        status = mcp_tools.mcp_runtime_cache_status()
    finally:
        mcp_tools.reset_mcp_runtime_caches()
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert verification["ok"] is True
    assert verification["release_id"] == "20260612_010000"
    assert verification["current_symlink"] is True
    assert verification["runtime_store_opened"] is False
    assert status["store"]["mode"] == "persistent"
    assert status["store"]["stores"] == 0
    assert status["store"]["idle"] == 0


def test_mcp_prepare_runtime_and_health_accept_v3_without_opening_monolith_store(
    tmp_path: Path,
    monkeypatch,
):
    release = tmp_path / "releases" / "prod" / "20260612_010000"
    release.mkdir(parents=True)
    _write_fixture(release)
    build_spine_shard_release_outputs(
        release,
        release_id=release.name,
        workers=1,
        no_cache=True,
    )
    write_release_manifest_v3(release, release_id=release.name, env="prod")
    current = release.parent / "current"
    current.symlink_to(release.name)

    monkeypatch.setenv("KRW_ONTOLOGY_ENV", "prod")
    monkeypatch.setenv("KRW_ONTOLOGY_RELEASE_ROOT", str(current))
    monkeypatch.delenv("KRW_ONTOLOGY_GLOBAL_SPINE_PATH", raising=False)

    verification = prepare_mcp_runtime(root=current, env="prod")
    payload, status_code = health_payload()

    assert verification["ok"] is True, verification["errors"]
    assert verification["verification_mode"] == "startup-v3"
    assert verification["runtime_store_opened"] is False
    assert verification["runtime_global_spine_path"] == str(current.absolute() / "indexes" / "global_spine.sqlite")
    assert os.environ["KRW_ONTOLOGY_GLOBAL_SPINE_PATH"] == str(current.absolute() / "indexes" / "global_spine.sqlite")
    assert os.environ["KRW_ONTOLOGY_SHARD_MANIFEST_PATH"] == str(current.absolute() / "indexes" / "shard_manifest.json")
    assert os.environ["KRW_ONTOLOGY_INDEX_LAYOUT"] == "global-spine-and-company-shards"
    assert status_code == 200
    assert payload["ok"] is True
    assert payload["release_id"] == release.name
    assert payload["index_layout"] == "global-spine-and-company-shards"
    assert payload["global_spine_present"] is True
    assert payload["company_shard_count"] == 1
    assert "error" not in payload


def test_mcp_tools_use_spine_router_for_v3_release(tmp_path: Path, monkeypatch):
    current = _write_v3_current_release(tmp_path)
    monkeypatch.setenv("KRW_MCP_STORE_MODE", "per_call")
    prepare_mcp_runtime(root=current, env="prod", store_mode="per_call")

    index_path = Path(os.environ["KRW_ONTOLOGY_GLOBAL_SPINE_PATH"])
    with mcp_tools._store(index_path) as store:
        assert isinstance(store, OntologySpineRouter)
        assert store.routing_status()["mode"] == "global_spine"
        assert store.routing_status()["fallback_enabled"] is False
        assert "monolith_open" not in store.routing_status()
        documents = store.list_documents()
        rows, diagnostics = store.query_compact_with_diagnostics(
            topic="revenue demand",
            tickers=["VG"],
            limit=5,
        )

    catalog = json.loads(catalog_tool())
    query = json.loads(query_tool(topic="revenue demand", ticker="VG", limit=5))
    no_ticker_query = json.loads(query_tool(topic="revenue demand", limit=5))
    topic_map = json.loads(topic_map_tool(ticker="VG", limit=5))
    trace = json.loads(trace_tool(object_id="claim:VG:FY2025:10K:revenue-growth"))
    quality = json.loads(quality_tool(ticker="VG"))

    assert documents
    assert rows
    assert diagnostics["routing"]["fallback"] is False
    assert catalog["companies"] == ["VG"]
    assert query["results"]
    assert query["search_diagnostics"]["routing"]["fallback"] is False
    assert no_ticker_query["results"]
    assert no_ticker_query["search_diagnostics"]["routing"]["mode"] == "global_spine_fanout"
    assert topic_map["ticker"] == "VG"
    assert topic_map["routing"]["mode"] == "company_shard"
    assert topic_map["routing"]["fallback"] is False
    assert topic_map["routing"]["fallback_used"] is False
    assert trace["object"]["id"] == "claim:VG:FY2025:10K:revenue-growth"
    assert trace["routing"]["mode"] == "object_locator"
    assert trace["routing"]["fallback"] is False
    assert quality["summary"]["documents"] >= 1
    assert quality["routing"]["mode"] == "company_shard"
    assert quality["routing"]["fallback"] is False


def test_spine_router_tickerless_candidates_use_global_signal_tables(tmp_path: Path):
    _write_fixture(tmp_path)
    _clone_fixture_company(tmp_path, source_ticker="VG", target_ticker="XOM")
    index = _build_v3_runtime(tmp_path)
    with sqlite3.connect(index["index_path"]) as conn:
        conn.execute(
            """
            INSERT INTO global_topic_spine(
                topic_id, topic_key, topic_label, topic_summary,
                ticker, shard_id, materiality
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "topic:XOM:hydrogen-roadmap",
                "hydrogen_roadmap",
                "Hydrogen roadmap",
                "Hydrogen infrastructure roadmap and electrolyzer capacity",
                "XOM",
                "XOM",
                9.0,
            ),
        )
        conn.commit()

    with OntologySpineRouter(index["index_path"]) as router:
        assert router._candidate_tickers(
            "hydrogen electrolyzer roadmap",
            explicit_tickers=None,
            limit=1,
        ) == ["XOM"]


def test_mcp_tools_report_declared_but_missing_company_shard_without_fallback(
    tmp_path: Path,
    monkeypatch,
):
    current = _write_v3_current_release(tmp_path)
    release = current.resolve()
    missing_shard = release / "indexes" / "companies" / "VG.sqlite"
    missing_shard.unlink()
    monkeypatch.setenv("KRW_MCP_STORE_MODE", "per_call")
    prepare_mcp_runtime(root=current, env="prod", store_mode="per_call")

    index_path = Path(os.environ["KRW_ONTOLOGY_GLOBAL_SPINE_PATH"])
    with mcp_tools._store(index_path) as store:
        assert isinstance(store, OntologySpineRouter)
        status = store.routing_status()
        assert status["ticker_count"] == 1
        assert status["available_ticker_count"] == 0
        assert status["missing_shard_count"] == 1
        assert status["missing_shards"] == {"VG": str(missing_shard)}
        assert status["fallback_enabled"] is False
        assert "monolith_open" not in status
        assert status["fallback"] is False

    catalog = json.loads(catalog_tool())
    query = json.loads(query_tool(topic="revenue demand", ticker="VG", limit=5))
    summary_query = json.loads(
        query_tool(
            topic="revenue demand",
            ticker="VG",
            limit=5,
            response_detail=ResponseDetail.TICKER_SUMMARY,
        )
    )
    topic_map = json.loads(topic_map_tool(ticker="VG", limit=5))
    query_context = json.loads(query_context_tool(question="VG revenue demand", ticker="VG", limit_results=5))
    retrieve = json.loads(retrieve_tool(question="VG revenue demand", ticker="VG", limit=5))
    trace = json.loads(trace_tool(object_id="claim:VG:FY2025:10K:revenue-growth"))
    chain = json.loads(chain_tool(object_id="claim:VG:FY2025:10K:revenue-growth"))
    quality = json.loads(quality_tool(ticker="VG"))
    compare = json.loads(compare_tool(tickers=["VG", "XOM"], topic="revenue demand", limit_per_ticker=2))

    assert catalog["companies"] == ["VG"]
    assert query["results"] == []
    assert query["search_diagnostics"]["routing"]["fallback"] is False
    assert query["search_diagnostics"]["missing_shards"] == {"VG": str(missing_shard)}
    assert query["search_diagnostics"]["fallback_used"] is False
    assert "monolith_fallback_used" not in query["search_diagnostics"]
    assert summary_query["search_diagnostics"]["routing"]["missing_shards"] == {"VG": str(missing_shard)}
    assert summary_query["search_diagnostics"]["missing_shards"] == {"VG": str(missing_shard)}
    assert topic_map["error"]["code"] == "ticker_shard_missing"
    assert topic_map["routing"]["fallback"] is False
    assert query_context["research_status"] == "not_answerable_from_current_release"
    assert query_context["missing_parts"] == ["ticker_shard_missing"]
    assert query_context["missing_shards"] == {"VG": str(missing_shard)}
    assert query_context["routing"]["fallback"] is False
    assert retrieve["research_status"] == "not_answerable_from_current_release"
    assert retrieve["missing_parts"] == ["ticker_shard_missing"]
    assert retrieve["missing_shards"] == {"VG": str(missing_shard)}
    assert retrieve["direct_evidence"] == []
    assert retrieve["related_context"] == []
    assert trace["error"]["code"] == "ticker_shard_missing"
    assert trace["missing_shards"] == {"VG": str(missing_shard)}
    assert trace["fallback_used"] is False
    assert trace["object_locator"]["ticker"] == "VG"
    assert trace["object_locator"]["shard_available"] is False
    assert trace["object_locator"]["shard_missing"] is True
    assert trace["object_locator"]["resolved_shard_path"] == str(missing_shard)
    assert "monolith_fallback_used" not in trace
    assert chain["error"]["code"] == "ticker_shard_missing"
    assert chain["object_locator"]["shard_missing"] is True
    assert quality["summary"]["missing_parts"] == ["ticker_shard_missing"]
    assert quality["summary"]["missing_shards"] == {"VG": str(missing_shard)}
    assert quality["routing"]["mode"] == "company_shard_missing"
    assert quality["routing"]["fallback"] is False
    vg_row = next(row for row in compare["comparison_rows"] if row["comparison_key"] == "VG")
    assert vg_row["missing"] is True
    assert vg_row["missing_reason"] == "ticker_shard_missing"
    assert compare["routing"]["mode"] == "compare_fanout"
    assert compare["routing"]["missing_shards"] == {"VG": str(missing_shard)}


def test_mcp_prepare_runtime_preserves_current_symlink_for_hot_swap(tmp_path: Path):
    releases_root = tmp_path / "releases" / "prod"
    first_release = releases_root / "20260529_010000"
    second_release = releases_root / "20260529_020000"
    _write_fixture(first_release, period="FY2025")
    _write_fixture(second_release, period="FY2026")
    build_spine_shard_release_outputs(first_release, release_id=first_release.name, workers=1, no_cache=True)
    build_spine_shard_release_outputs(second_release, release_id=second_release.name, workers=1, no_cache=True)
    write_release_manifest_v3(first_release, release_id=first_release.name, env="prod")
    write_release_manifest_v3(second_release, release_id=second_release.name, env="prod")
    current = releases_root / "current"
    current.symlink_to(first_release.name)
    env_names = (
        "KRW_ONTOLOGY_ENV",
        "KRW_ONTOLOGY_RELEASE_ROOT",
        "KRW_ONTOLOGY_ROOT",
        "KRW_ONTOLOGY_MANIFEST_PATH",
        "KRW_ONTOLOGY_GLOBAL_SPINE_PATH",
        "KRW_ONTOLOGY_SHARD_MANIFEST_PATH",
        "KRW_MCP_STORE_MODE",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    for name in env_names:
        os.environ.pop(name, None)

    mcp_tools.reset_mcp_runtime_caches()
    try:
        verification = prepare_mcp_runtime(root=current, env="prod")
        runtime_global_spine_path = Path(os.environ["KRW_ONTOLOGY_GLOBAL_SPINE_PATH"])
        assert verification["runtime_root"] == str(current.absolute())
        assert verification["runtime_global_spine_path"] == str(current.absolute() / "indexes" / "global_spine.sqlite")
        assert runtime_global_spine_path.parent.parent.name == "current"

        with mcp_tools._store(mcp_tools._runtime_global_spine_path()) as first_store:
            assert first_store.list_documents()[0]["period"] == "FY2025"
        first_trace = json.loads(trace_tool(object_id="claim:VG:FY2025:10K:revenue-growth"))
        assert first_trace["object_locator"]["period"] == "FY2025"

        current.unlink()
        current.symlink_to(second_release.name)

        with mcp_tools._store(mcp_tools._runtime_global_spine_path()) as second_store:
            assert second_store.list_documents()[0]["period"] == "FY2026"
        stale_trace = json.loads(trace_tool(object_id="claim:VG:FY2025:10K:revenue-growth"))
        assert stale_trace["error"]["code"] == "not_found"
        second_trace = json.loads(trace_tool(object_id="claim:VG:FY2026:10K:revenue-growth"))
        assert second_trace["object_locator"]["period"] == "FY2026"

        status = mcp_tools.mcp_runtime_cache_status()
        assert status["store"]["rotations"] == 1
    finally:
        mcp_tools.reset_mcp_runtime_caches()
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_mcp_prepare_runtime_rejects_non_symlink_prod_root(tmp_path: Path):
    release = tmp_path / "releases" / "prod" / "20260529_010000"
    release.mkdir(parents=True)
    _write_fixture(release)
    build_spine_shard_release_outputs(release, release_id=release.name, workers=1, no_cache=True)
    write_release_manifest_v3(release, release_id=release.name, env="prod")

    with pytest.raises(RuntimeError, match="current_symlink_required"):
        prepare_mcp_runtime(root=release, env="prod")


def test_mcp_prepare_runtime_rejects_legacy_v1_current_without_sqlite_open(
    tmp_path: Path,
    monkeypatch,
):
    release = tmp_path / "releases" / "prod" / "20260529_legacy"
    index_path = release / "indexes" / "agent_index.sqlite"
    index_path.parent.mkdir(parents=True)
    index_path.write_bytes(b"not a sqlite database")
    (release / "manifest.json").write_text(
        json.dumps(
            {
                "format": "krw-ontology-release/v1",
                "env": "prod",
                "release_id": release.name,
                "index_path": "indexes/agent_index.sqlite",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    current = release.parent / "current"
    current.symlink_to(release.name)

    def fail_connect(*_args, **_kwargs):
        raise AssertionError("legacy manifest rejection must not open SQLite")

    monkeypatch.setattr("krw_ontology.release.sqlite3.connect", fail_connect)

    with pytest.raises(RuntimeError, match="manifest_format_unsupported"):
        prepare_mcp_runtime(root=current, env="prod")


def test_mcp_persistent_store_reuses_sqlite_connection(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    index = _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_MCP_STORE_MODE", "persistent")
    mcp_tools.reset_mcp_runtime_caches()

    try:
        with mcp_tools._store(index["index_path"]) as first:
            first_conn = first.conn
        with mcp_tools._store(index["index_path"]) as second:
            assert second.conn is first_conn
        status = mcp_tools.mcp_runtime_cache_status()
    finally:
        mcp_tools.reset_mcp_runtime_caches()

    assert status["store"]["stores"] == 1
    assert status["store"]["hits"] == 1
    assert status["store"]["misses"] == 1
    assert status["store"]["global_spine_stores"][0]["release_id"] == "test-v3-runtime"
    assert status["store"]["global_spine_stores"][0]["global_spine_sha256"]
    assert status["store"]["global_spine_stores"][0]["shard_manifest_sha256"]


def test_mcp_persistent_store_pins_inflight_release_and_rotates_after_current_switch(
    tmp_path: Path,
    monkeypatch,
):
    releases_root = tmp_path / "releases" / "prod"
    first_release = releases_root / "20260529_010000"
    second_release = releases_root / "20260529_020000"
    _write_fixture(first_release, period="FY2025")
    _write_fixture(second_release, period="FY2026")
    build_spine_shard_release_outputs(first_release, release_id=first_release.name, workers=1, no_cache=True)
    build_spine_shard_release_outputs(second_release, release_id=second_release.name, workers=1, no_cache=True)
    write_release_manifest_v3(first_release, release_id=first_release.name, env="prod")
    write_release_manifest_v3(second_release, release_id=second_release.name, env="prod")
    current = releases_root / "current"
    current.symlink_to(first_release.name)
    index_path = current / "indexes" / "global_spine.sqlite"

    monkeypatch.setenv("KRW_MCP_STORE_MODE", "persistent")
    mcp_tools.reset_mcp_runtime_caches()

    try:
        with mcp_tools._store(index_path) as old_store:
            assert old_store.list_documents()[0]["period"] == "FY2025"
            current.unlink()
            current.symlink_to(second_release.name)

            with mcp_tools._store(index_path) as new_store:
                assert new_store.list_documents()[0]["period"] == "FY2026"
                assert new_store.routing_status()["mode"] == "global_spine"

            inflight_status = mcp_tools.mcp_runtime_cache_status()
            assert inflight_status["store"]["rotations"] == 1
            assert inflight_status["store"]["active_stores"] == 1
            assert inflight_status["store"]["retired_stores"] == 1
            assert inflight_status["store"]["retired_leased"] == 1
            assert inflight_status["store"]["rotation_pending"] is True
            assert inflight_status["store"]["retired_oldest_age_sec"] >= 0
            assert len(inflight_status["store"]["retired_global_spine_stores"]) == 1
            assert inflight_status["store"]["retired_global_spine_stores"][0]["retired"] is True
            assert inflight_status["store"]["last_rotation"]["previous_resolved_global_spine_path"].endswith(
                "20260529_010000/indexes/global_spine.sqlite"
            )
            assert inflight_status["store"]["last_rotation"]["previous_release_id"] == "20260529_010000"
            assert inflight_status["store"]["last_rotation"]["previous_global_spine_sha256"]
            assert inflight_status["store"]["last_rotation"]["previous_shard_manifest_sha256"]
            assert inflight_status["store"]["last_rotation"]["new_resolved_global_spine_path"].endswith(
                "20260529_020000/indexes/global_spine.sqlite"
            )
            assert inflight_status["store"]["last_rotation"]["new_release_id"] == "20260529_020000"
            assert inflight_status["store"]["last_rotation"]["new_global_spine_sha256"]
            assert inflight_status["store"]["last_rotation"]["new_shard_manifest_sha256"]
            assert inflight_status["store"]["global_spine_stores"][0]["release_id"] == "20260529_020000"
            assert inflight_status["store"]["retired_global_spine_stores"][0]["release_id"] == "20260529_010000"
            health, health_status = health_payload(root=str(current))
            assert health_status == 200
            assert health["release_id"] == "20260529_020000"
            assert health["mcp_store_hot_swap"]["rotations"] == 1
            assert health["mcp_store_hot_swap"]["retired_leased"] == 1
            assert health["mcp_store_hot_swap"]["rotation_pending"] is True
            assert health["mcp_store_hot_swap"]["retired_oldest_age_sec"] >= 0
            assert health["mcp_store_hot_swap"]["last_rotation"]["previous_resolved_global_spine_path"].endswith(
                "20260529_010000/indexes/global_spine.sqlite"
            )
            metrics, metrics_status = metrics_payload(root=str(current))
            assert metrics_status == 200
            assert 'krw_ontology_mcp_store_rotation_pending{env="prod",release_id="20260529_020000"} 1' in metrics
            assert 'krw_ontology_mcp_store_retired_leased{env="prod",release_id="20260529_020000"} 1' in metrics

            current.unlink()
            current.symlink_to(first_release.name)
            with mcp_tools._store(index_path) as restored_store:
                assert restored_store.list_documents()[0]["period"] == "FY2025"

            aba_status = mcp_tools.mcp_runtime_cache_status()
            assert aba_status["store"]["rotations"] == 2
            assert aba_status["store"]["retired_leased"] == 1
            assert len(aba_status["store"]["retired_global_spine_stores"]) == 1
            assert len(aba_status["store"]["global_spine_stores"]) == 1
            assert aba_status["store"]["retired_global_spine_stores"][0]["resolved_global_spine_path"].endswith(
                "20260529_010000/indexes/global_spine.sqlite"
            )
            assert aba_status["store"]["global_spine_stores"][0]["resolved_global_spine_path"].endswith(
                "20260529_010000/indexes/global_spine.sqlite"
            )
            assert (
                aba_status["store"]["retired_global_spine_stores"][0]["generation"]
                != aba_status["store"]["global_spine_stores"][0]["generation"]
            )
            assert old_store.list_documents()[0]["period"] == "FY2025"
        status = mcp_tools.mcp_runtime_cache_status()
    finally:
        mcp_tools.reset_mcp_runtime_caches()

    assert status["store"]["rotations"] == 2
    assert status["store"]["opened"] == 3
    assert status["store"]["retired_leased"] == 0
    assert status["store"]["rotation_pending"] is False
    assert status["store"]["retired_global_spine_stores"] == []


def test_mcp_query_normalizes_object_type_aliases(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    query = json.loads(
        query_tool(
            topic="regulatory risk",
            tickers=["VG"],
            object_types=["Risk"],
            limit=5,
        )
    )

    assert query["query"]["object_types_requested"] == ["Risk"]
    assert query["query"]["object_types"] == ["BusinessFactor"]
    assert query["results"][0]["type"] == "BusinessFactor"
    assert query["results"][0]["evidence"]["quotes"] == []

    metric_query = json.loads(
        query_tool(
            tickers=["VG"],
            object_types=["FinancialMetric"],
            limit=1,
        )
    )
    assert metric_query["query"]["object_types_requested"] == ["FinancialMetric"]
    assert metric_query["query"]["object_types"] == ["MetricObservation"]


def test_mcp_event_aliases_are_not_overwritten():
    event_types, invalid = _normalize_object_types(["event"])
    assert invalid == []
    assert event_types == ["BusinessEvent", "ChangeEvent"]

    business_event_types, invalid = _normalize_object_types(["business_event"])
    assert invalid == []
    assert business_event_types == ["BusinessEvent"]

    change_event_types, invalid = _normalize_object_types(["change_event", "disclosure_change"])
    assert invalid == []
    assert change_event_types == ["ChangeEvent"]


def test_mcp_query_falls_back_for_korean_topic(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    query = json.loads(
        query_tool(
            topic="유럽 가스 비축 부족",
            tickers=["VG"],
            object_types=["ExternalFactorExposure", "BusinessActivity", "ResearchClaim"],
            limit=5,
        )
    )

    assert query["results"]
    assert query["search_diagnostics"]["normalized_terms"] == []
    assert "natural_gas_price" in query["search_diagnostics"]["expanded_terms"]
    assert query["search_diagnostics"]["search_strategy"]["selected_mode"] in {
        "split_and",
        "relaxed_or",
    }
    assert "topic_rewritten_for_search" in query["search_diagnostics"]["warnings"]


def test_mcp_topic_map_repackages_company_vocabulary(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    topic_map = json.loads(topic_map_tool(ticker="VG", limit=10))

    assert topic_map["ticker"] == "VG"
    assert topic_map["source"]["company_business_profile_ids"] == [
        "company_business_profile:VG:ALL"
    ]
    assert topic_map["source"]["fallback_used"] is False
    assert topic_map["routing"]["mode"] == "company_shard"
    assert topic_map["routing"]["fallback"] is False
    assert topic_map["routing"]["fallback_used"] is False
    factor_terms = {
        entry["term"] for entry in topic_map["topics"]["external_factors"]
    }
    activity_terms = {
        entry["term"] for entry in topic_map["topics"]["business_activities"]
    }
    metric_terms = {entry["term"] for entry in topic_map["topics"]["metrics"]}
    assert "natural_gas_price" in factor_terms
    assert "lng_sales" in activity_terms
    assert "revenue" in metric_terms
    assert any(
        suggestion["topic"] == "natural gas price"
        for suggestion in topic_map["suggested_first_queries"]
    )


def test_mcp_compare_quality_topic_and_company_context_report_v3_routing(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _clone_fixture_company(tmp_path, source_ticker="VG", target_ticker="XOM")
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))
    monkeypatch.setenv("KRW_ROUTER_FANOUT_WORKERS", "2")

    compare = json.loads(
        compare_tool(
            tickers=["VG", "XOM"],
            topic="revenue growth customer demand",
            limit_per_ticker=2,
        )
    )
    quality = json.loads(quality_tool())
    topic_map = json.loads(topic_map_tool(ticker="VG", limit=5))
    company_context = json.loads(company_context_tool(ticker="XOM", limit_topics=5))

    assert compare["routing"]["mode"] == "compare_fanout"
    assert compare["routing"]["route_tickers"] == ["VG", "XOM"]
    assert compare["routing"]["fallback"] is False
    assert compare["routing"]["fallback_used"] is False
    assert compare["routing"]["fanout_parallel"] is True
    assert compare["routing"]["fanout_workers"] == 2
    assert compare["fanout_parallel"] is True
    assert compare["fanout_workers"] == 2
    assert compare["missing_shards"] == {}
    assert compare["unknown_tickers"] == []
    assert set(compare["results"]) == {"VG", "XOM"}
    assert compare["results"]["VG"]
    assert compare["results"]["XOM"]
    assert {
        row["comparison_key"] for row in compare["comparison_rows"] if not row["missing"]
    } == {"VG", "XOM"}
    assert compare["comparison_contexts"]["VG"]["routing"]["mode"] == "company_shard"
    assert compare["comparison_contexts"]["XOM"]["routing"]["mode"] == "company_shard"

    assert quality["routing"]["mode"] == "quality_release_scan"
    assert quality["routing"]["route_tickers"] == ["VG", "XOM"]
    assert quality["routing"]["fallback"] is False
    assert quality["routing"]["fanout_parallel"] is True
    assert quality["routing"]["fanout_workers"] == 2
    assert quality["summary"]["fanout_workers"] == 2
    assert quality["topology"]["mode"] == "global_spine"
    assert quality["topology"]["fallback"] is False
    assert {document["ticker"] for document in quality["documents"]} == {"VG", "XOM"}

    assert topic_map["routing"]["mode"] == "company_shard"
    assert topic_map["routing"]["route_tickers"] == ["VG"]
    assert topic_map["routing"]["fallback"] is False
    assert company_context["routing"]["mode"] == "company_shard"
    assert company_context["routing"]["route_tickers"] == ["XOM"]
    assert company_context["routing"]["fallback"] is False


def test_mcp_chain_returns_object_specific_chains(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    exposure_chain = json.loads(
        chain_tool(
            object_id="external_factor_exposure:VG:FY2025:10K:natural-gas-price-operating-margin"
        )
    )
    assert exposure_chain["object"]["type"] == "ExternalFactorExposure"
    assert exposure_chain["quality"]["evidence_grade"] == "direct"
    assert exposure_chain["quality"]["warnings"] == []
    assert exposure_chain["chain"]["evidence_chain"]["claims"][0]["type"] == "ResearchClaim"
    assert "text" not in exposure_chain["chain"]["evidence_chain"]["quotes"][0]

    activity_chain = json.loads(chain_tool(object_id="business_activity:VG:FY2025:10K:lng-sales"))
    assert activity_chain["object"]["type"] == "BusinessActivity"
    assert {
        item["id"] for item in activity_chain["chain"]["temporal_context"]
    } >= {
        "trend:VG:ALL:lng-sales-revenue",
        "change_event:VG:ALL:revenue-growth",
    }

    claim_chain = json.loads(chain_tool(object_id="claim:VG:FY2025:10K:revenue-growth"))
    assert claim_chain["object"]["type"] == "ResearchClaim"
    assert claim_chain["chain"]["evidence_chain"]["quotes"][0]["id"] == "quote:VG:FY2025:10K:0001"

    quote_chain = json.loads(chain_tool(object_id="quote:VG:FY2025:10K:0001"))
    assert quote_chain["object"]["type"] == "EvidenceQuote"
    assert quote_chain["chain"]["evidence_chain"]["claims"][0]["id"] == "claim:VG:FY2025:10K:revenue-growth"


def test_mcp_chain_includes_global_spine_cross_company_neighbors(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _clone_fixture_company(tmp_path, source_ticker="VG", target_ticker="XOM")
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    chain = json.loads(chain_tool(object_id="business_factor:VG:FY2025:10K:revenue-growth"))

    assert chain["routing"]["mode"] == "object_locator"
    assert chain["routing"]["fallback"] is False
    assert chain["object_locator"]["object_id"] == "business_factor:VG:FY2025:10K:revenue-growth"
    assert chain["object_locator"]["ticker"] == "VG"
    assert chain["object_locator"]["shard_available"] is True
    neighbors = chain["global_spine_neighbors"]
    assert neighbors
    assert any(
        {neighbor["from_ticker"], neighbor["to_ticker"]} == {"VG", "XOM"}
        and neighbor["shared_key_type"] in {"factor", "topic", "metric"}
        for neighbor in neighbors
    )
    assert any(
        str(neighbor["from_object_id"]).startswith("business_factor:XOM")
        or str(neighbor["to_object_id"]).startswith("business_factor:XOM")
        for neighbor in neighbors
    )


def test_mcp_chain_respects_depth_and_quote_text_option(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    shallow = json.loads(
        chain_tool(
            object_id="business_factor:VG:FY2025:10K:revenue-growth",
            max_depth=1,
            direction="incoming",
        )
    )
    shallow_path_ids = [
        step["object"]["id"]
        for path in shallow["chain"]["edge_paths"]
        for step in path["steps"]
    ]
    assert "claim:VG:FY2025:10K:revenue-growth" in shallow_path_ids
    assert "quote:VG:FY2025:10K:0001" not in shallow_path_ids

    deeper = json.loads(
        chain_tool(
            object_id="business_factor:VG:FY2025:10K:revenue-growth",
            max_depth=2,
            direction="incoming",
            include_quote_text=True,
        )
    )
    deep_path_ids = [
        step["object"]["id"]
        for path in deeper["chain"]["edge_paths"]
        for step in path["steps"]
    ]
    assert "quote:VG:FY2025:10K:0001" in deep_path_ids
    assert deeper["chain"]["evidence_chain"]["quotes"][0]["text"]


def test_mcp_chain_reports_missing_ambiguous_and_unsupported_objects(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    missing = json.loads(chain_tool(object_id="business_factor:VG:missing"))
    assert missing["error"]["code"] == "not_found"

    ambiguous = json.loads(chain_tool(object_id="claim:VG"))
    assert ambiguous["error"]["code"] == "ambiguous_object_id"

    unsupported = json.loads(chain_tool(object_id="business_factor:VG:FY2025:10K:unsupported-risk"))
    assert "no_supporting_evidence_found" in unsupported["quality"]["warnings"]


def test_mcp_compare_allows_single_ticker_period_comparison(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path, period="FY2024", text="Revenue growth faced operational risk.")
    _write_fixture(tmp_path, period="FY2025", text="Revenue growth faced regulatory risk.")
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    compare = json.loads(
        compare_tool(
            tickers=["VG"],
            topic="revenue growth",
            document_types=["10-K"],
            periods=["FY2024", "FY2025"],
            limit_per_ticker=2,
        )
    )

    assert compare["mode"] == "period_topic"
    assert set(compare["results"]) == {"FY2024", "FY2025"}
    assert compare["results"]["FY2024"]
    assert compare["results"]["FY2025"]
    assert {row["comparison_key"] for row in compare["comparison_rows"]} == {"FY2024", "FY2025"}
    assert all(row["ticker"] == "VG" for row in compare["comparison_rows"])
    assert all(row["missing"] is False for row in compare["comparison_rows"])


def test_mcp_compare_markdown_includes_directness_guard(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    markdown = compare_tool(
        tickers=["VG", "XOM"],
        topic="direct revenue growth exposure",
        document_types=["10-K"],
        limit_per_ticker=2,
        response_format=ResponseFormat.MARKDOWN,
    )

    assert "## Directness Guards" in markdown
    assert "strong_claim_allowed=" in markdown
    assert "traceable_direct,traceable_metric_lineage" in markdown


def test_mcp_trace_accepts_unique_id_prefix(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    trace = json.loads(trace_tool(object_id="claim:VG:FY2025:10K:revenue"))

    assert trace["resolved_from_prefix"] == "claim:VG:FY2025:10K:revenue"
    assert trace["object"]["id"] == "claim:VG:FY2025:10K:revenue-growth"


def test_mcp_trace_returns_ambiguous_prefix_candidates(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    trace = json.loads(trace_tool(object_id="claim:VG"))

    assert trace["error"]["code"] == "ambiguous_object_id"
    assert {candidate["id"] for candidate in trace["candidates"]} >= {
        "claim:VG:FY2025:10K:revenue-growth",
        "claim:VG:FY2025:10K:regulatory-risk",
    }


def test_mcp_server_registers_expected_tools():
    tool_names = {tool.name for tool in mcp._tool_manager.list_tools()}

    assert {
        "krw_ontology_catalog",
        "krw_ontology_query",
        "krw_ontology_topic_map",
        "krw_ontology_retrieve",
        "krw_ontology_trace",
        "krw_ontology_verify_evidence",
        "krw_ontology_chain",
        "krw_ontology_quality",
        "krw_ontology_compare",
        "krw_ontology_plan_query",
    }.issubset(tool_names)


def test_mcp_index_context_defaults_to_lightweight_guard(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(index_context_tool())

    assert payload["index_status"] == "ready"
    assert "capabilities" in payload
    assert "object_counts" not in payload
    assert "serving_counts" not in payload
    assert "quality_summary" not in payload
    assert payload["index_context_guard"] == {
        "mode": "lightweight_by_default",
        "diagnostic_only": True,
        "expensive_counts_requested": False,
        "expensive_quality_summary_requested": False,
        "allow_expensive": False,
            "counts_returned": False,
            "quality_summary_returned": False,
            "reason": (
                "index_context is an operational/debug capability card for the current v3 ontology release. "
                "Expensive table counts and quality summary scans are disabled by default; "
                "use query_context for normal research questions."
            ),
        "how_to_enable_expensive": (
            "Pass allow_expensive=true with include_counts and/or include_quality_summary "
            "only for explicit audit/debug operations."
        ),
        "recommended_normal_research_tool": "krw_ontology_query_context",
    }


def test_mcp_index_context_requires_explicit_allow_expensive_for_counts(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    guarded = json.loads(
        index_context_tool(
            include_counts=True,
            include_quality_summary=True,
        )
    )
    assert "serving_counts" not in guarded
    assert "quality_summary" not in guarded
    assert guarded["index_context_guard"]["expensive_counts_requested"] is True
    assert guarded["index_context_guard"]["expensive_quality_summary_requested"] is True
    assert guarded["index_context_guard"]["counts_returned"] is False
    assert guarded["index_context_guard"]["quality_summary_returned"] is False

    audit = json.loads(
        index_context_tool(
            include_counts=True,
            include_quality_summary=True,
            allow_expensive=True,
        )
    )
    assert audit["serving_counts"]["objects"] > 0
    assert audit["quality_summary"]["critical_errors"] == 0
    assert audit["index_context_guard"]["counts_returned"] is True
    assert audit["index_context_guard"]["quality_summary_returned"] is True


def test_mcp_index_context_logs_guard_timing(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    with caplog.at_level("INFO", logger=mcp_tools.LOGGER.name):
        index_context_tool(include_counts=True)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "[krw-ontology:mcp-slow-path]" in messages
    assert '"tool_name": "krw_ontology_index_context"' in messages
    assert '"include_counts_requested": true' in messages
    assert '"counts_returned": false' in messages


def test_mcp_query_logs_slow_timing_without_raw_topic(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))
    monkeypatch.setattr(mcp_tools, "SLOW_MCP_TOOL_LOG_THRESHOLD_MS", 0)

    raw_topic = "revenue growth"
    with caplog.at_level("INFO", logger=mcp_tools.LOGGER.name):
        query_tool(topic=raw_topic, ticker="VG", limit=5)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "[krw-ontology:mcp-slow-path]" in messages
    assert '"tool_name": "krw_ontology_query"' in messages
    assert '"topic_chars": 14' in messages
    assert '"response_detail": "compact"' in messages
    assert raw_topic not in messages


def test_mcp_query_context_logs_slow_timing_without_raw_question(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))
    monkeypatch.setattr(mcp_tools, "SLOW_MCP_TOOL_LOG_THRESHOLD_MS", 0)

    raw_question = "VG revenue growth and regulatory risk mechanism"
    with caplog.at_level("INFO", logger=mcp_tools.LOGGER.name):
        query_context_tool(question=raw_question, ticker="VG", limit_results=5)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "[krw-ontology:mcp-slow-path]" in messages
    assert '"tool_name": "krw_ontology_query_context"' in messages
    assert '"question_chars": 47' in messages
    assert '"ticker_count": 1' in messages
    assert raw_question not in messages


def test_mcp_query_context_returns_research_pack_and_bounded_chain(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_context_tool(
            question="VG revenue growth and regulatory risk mechanism",
            ticker="VG",
            limit_results=5,
        )
    )

    assert payload["research_context_version"] == "v1"
    assert payload["research_status"] in {
        "sufficient_for_default_answer",
        "sufficient_but_trace_recommended",
        "partial_answer_possible",
    }
    assert payload["agent_autonomy"]["mode"] == "bounded"
    assert payload["agent_autonomy"]["max_additional_tool_calls"] <= 3
    assert "krw_ontology_retrieve" in payload["do_not_call"]
    assert payload["research_pack"]["company_topic_pack"]["top_candidates"]
    assert payload["research_pack"]["chain_pack"]["max_roots"] == 2
    assert len(payload["research_pack"]["chain_pack"]["primary_chains"]) <= 2


def test_mcp_query_context_includes_cross_company_signal_pack(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_context_tool(
            question=(
                "Across VG and XOM, what do latest company filing commentary and metrics say "
                "about revenue growth, demand, natural gas pricing, and operating margin?"
            ),
            tickers=["VG", "XOM"],
            limit_results=5,
        )
    )

    signal_pack = payload["research_pack"]["cross_company_signal_pack"]
    assert signal_pack["mode"] == "cross_company_signal_synthesis"
    assert signal_pack["answer_policy"].startswith("Use this as compact cross-company evidence")
    assert signal_pack["ticker_basket"][:2] == ["VG", "XOM"]
    assert signal_pack["company_evidence_rows"]
    assert signal_pack["signals"]
    assert signal_pack["quality"]["missing_parts"] == []
    assert {
        row["evidence_strength"] for row in signal_pack["company_evidence_rows"]
    } <= {"strong", "medium", "weak"}
    assert any(
        "revenue" in row["commentary_summary"].lower()
        or "margin" in row["commentary_summary"].lower()
        for row in signal_pack["company_evidence_rows"]
    )

    markdown = query_context_tool(
        question=(
            "Across VG and XOM, what do latest company filing commentary and metrics say "
            "about revenue growth, demand, natural gas pricing, and operating margin?"
        ),
        tickers=["VG", "XOM"],
        limit_results=5,
        response_format=ResponseFormat.MARKDOWN,
    )
    assert "cross_company_signal_pack: available" in markdown
    assert "signal:" in markdown
    assert "evidence:" in markdown


def test_mcp_query_context_uses_latest_document_anchors_for_cross_company_pack(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path, period="FY2024", text="Revenue growth was slower on softer customer demand.")
    _write_fixture(tmp_path, period="FY2026", text="Revenue growth accelerated on current customer demand.")
    _clone_fixture_company(tmp_path, source_ticker="VG", target_ticker="XOM")
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_context_tool(
            question="Across VG and XOM, what does latest filing commentary say about revenue growth?",
            tickers=["VG", "XOM"],
            limit_results=5,
        )
    )

    anchors = payload["current_document_anchors"]
    assert anchors["VG"]["period"] == "FY2026"
    assert anchors["XOM"]["period"] == "FY2026"
    signal_pack = payload["research_pack"]["cross_company_signal_pack"]
    assert signal_pack["current_document_anchors"]["VG"]["period"] == "FY2026"
    assert {row["period"] for row in signal_pack["company_evidence_rows"]} == {"FY2026"}

    markdown = query_context_tool(
        question="Across VG and XOM, what does latest filing commentary say about revenue growth?",
        tickers=["VG", "XOM"],
        limit_results=5,
        response_format=ResponseFormat.MARKDOWN,
    )
    assert "current_document_anchors: VG FY2026 10-K, XOM FY2026 10-K" in markdown


def test_mcp_query_context_exposes_10q_current_driver_and_10k_annual_baseline(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path, period="CY2025", text="Revenue growth reflected annual customer demand.")
    _write_fixture(
        tmp_path,
        period="CY2026Q1",
        document_type="10-Q",
        text="Revenue growth reflected current quarter customer demand.",
    )
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_context_tool(
            question="VG 공시상 최근 매출 성장 흐름을 확인해줘.",
            ticker="VG",
            limit_results=5,
        )
    )

    roles = payload["filing_document_roles"]["VG"]
    assert roles["current_driver"]["period"] == "CY2026Q1"
    assert roles["current_driver"]["document_type"] == "10-Q"
    assert roles["annual_baseline"]["period"] == "CY2025"
    assert roles["annual_baseline"]["document_type"] == "10-K"
    assert roles["latest_available"]["period"] == "CY2026Q1"
    assert payload["current_document_anchors"]["VG"] == roles["current_driver"]
    assert payload["research_pack"]["filing_document_roles"]["VG"] == roles

    markdown = query_context_tool(
        question="VG 공시상 최근 매출 성장 흐름을 확인해줘.",
        ticker="VG",
        limit_results=5,
        response_format=ResponseFormat.MARKDOWN,
    )
    assert "filing_document_roles: VG current_driver=VG CY2026Q1 10-Q" in markdown
    assert "annual_baseline=VG CY2025 10-K" in markdown


def test_mcp_query_context_partial_answerable_when_one_requested_ticker_is_unknown(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_context_tool(
            question="Across VG and WMT, check latest filing commentary on revenue demand.",
            tickers=["VG", "WMT"],
            limit_results=5,
        )
    )

    assert payload["research_status"] == "partial_answerable_from_current_release"
    assert payload["answerability"]["recommended_answer_mode"] == "partial_answerable_from_current_release"
    assert payload["unknown_tickers"] == ["WMT"]
    assert payload["partial_answerability"]["available_tickers"] == ["VG"]
    assert payload["partial_answerability"]["missing_tickers"] == ["WMT"]
    assert payload["current_document_anchors"]["VG"]["period"] == "FY2025"


def test_mcp_query_prioritizes_latest_filing_when_current_intent_is_present(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path, period="FY2024", text="Revenue growth reflected older customer demand.")
    _write_fixture(tmp_path, period="FY2026", text="Revenue growth reflects current customer demand.")
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(query_tool(topic="latest revenue growth", ticker="VG", limit=1))

    assert payload["results"][0]["period"] == "FY2026"
    prior = payload["search_diagnostics"]["current_document_prior"]
    assert prior["enabled"] is True
    assert prior["latest_documents"]["VG"]["period"] == "FY2026"


def test_mcp_query_current_prior_uses_10q_current_driver(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path, period="CY2025", text="Revenue growth reflected annual customer demand.")
    _write_fixture(
        tmp_path,
        period="CY2026Q1",
        document_type="10-Q",
        text="Revenue growth reflected current quarter customer demand.",
    )
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(query_tool(topic="latest revenue growth", ticker="VG", limit=1))

    assert payload["results"][0]["period"] == "CY2026Q1"
    assert payload["results"][0]["document_type"] == "10-Q"
    prior = payload["search_diagnostics"]["current_document_prior"]
    assert prior["latest_documents"]["VG"]["period"] == "CY2026Q1"
    assert prior["filing_document_roles"]["VG"]["annual_baseline"]["period"] == "CY2025"


def test_mcp_query_does_not_override_explicit_period_with_latest_prior(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path, period="FY2024", text="Revenue growth reflected older customer demand.")
    _write_fixture(tmp_path, period="FY2026", text="Revenue growth reflects current customer demand.")
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_tool(
            topic="latest revenue growth",
            ticker="VG",
            periods=["FY2024"],
            limit=1,
        )
    )

    assert payload["results"][0]["period"] == "FY2024"
    assert "current_document_prior" not in payload["search_diagnostics"]


def test_mcp_query_context_stops_out_of_scope_valuation(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_context_tool(
            question="VG의 매출 성장 지속성 가정 하에 12개월 목표치를 추정해줘.",
            ticker="VG",
            limit_results=5,
        )
    )

    assert payload["research_status"] == "out_of_scope_for_filing_ontology"
    assert payload["agent_autonomy"]["may_continue_research"] is False
    assert payload["agent_autonomy"]["max_additional_tool_calls"] == 0
    assert "krw_ontology_query" in payload["do_not_call"]
    assert payload["stop_guard"]["cannot_answer_reason"]
    assert payload["research_pack"]["stop_guard"]["cannot_answer_reason"]
    assert payload["research_pack"]["valuation_guard"]["cannot_answer_reason"]
    assert payload["ticker_candidates"] == []
    markdown = query_context_tool(
        question="VG의 매출 성장 지속성 가정 하에 12개월 목표치를 추정해줘.",
        ticker="VG",
        limit_results=5,
        response_format=ResponseFormat.MARKDOWN,
    )
    assert "cannot_answer_reason:" in markdown
    assert "valuation model inputs" in markdown


def test_mcp_query_context_includes_metric_series_research_pack(
    tmp_path: Path,
    monkeypatch,
):
    _write_metric_dimension_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_context_tool(
            question="AAPL의 iPhone과 Services 매출 비중은 2021~2025년에 어떻게 달라졌는지 비교해줘.",
            ticker="AAPL",
            periods=["CY2021", "CY2022", "CY2023", "CY2024", "CY2025"],
            limit_results=10,
        )
    )

    metric_pack = payload["research_pack"]["metric_series_pack"]
    assert payload["research_context_version"] == "v1"
    assert payload["kernel"]["version"] == "research_kernel_v0.1"
    assert payload["kernel"]["contract_version"] == "kernel.v1alpha"
    assert payload["kernel"]["intent"] == "metric_series"
    assert payload["kernel"]["primary_context"] == "metric_context"
    assert payload["research_pack"]["intent_router"]["intent"] == "metric_series"
    assert payload["research_pack"]["context_policy"]["run_metric_series"] is True
    assert payload["research_status"] == "sufficient_for_default_answer"
    assert payload["search_diagnostics"]["discovery_skipped"] is True
    assert payload["search_diagnostics"]["metric_series"]["mode"] == "metric_dimension_lookup"
    assert metric_pack["mode"] == "metric_dimension_lookup"
    assert metric_pack["result_count"] > 0
    assert "target_dimension_metric" in metric_pack["roles"]
    assert "denominator_metric" in metric_pack["roles"]
    assert metric_pack["denominator_needed"] is True
    assert {series["metric_role"] for series in metric_pack["series"]} >= {
        "target_dimension_metric",
        "denominator_metric",
    }
    shares = metric_pack["calculations"]["share_of_total"]
    assert {share["label"] for share in shares} >= {"iPhone", "Services"}
    iphone_share = next(share for share in shares if share["label"] == "iPhone")
    assert iphone_share["share"] == pytest.approx(201200000000 / 416200000000)
    assert metric_pack["quality"]["period_alignment"] is True
    assert metric_pack["quality"]["unit_consistency"] is True
    assert metric_pack["quality"]["dimension_metric_not_found"] is False
    assert payload["research_pack"]["chain_pack"]["mode"] == "lazy_root_candidates"


def test_mcp_query_context_attaches_chart_series_sidecar_pack(
    tmp_path: Path,
    monkeypatch,
):
    _write_chart_metric_fixture(tmp_path)
    index = _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))
    monkeypatch.setenv("KRW_CHART_SERIES_ENABLED", "1")

    assert index["build_result"].chart_series_path is not None
    assert index["build_result"].chart_series_path.exists()

    payload = json.loads(
        query_context_tool(
            question="AAPL 매출 추이 차트로 보여줘.",
            ticker="AAPL",
            limit_results=5,
        )
    )

    metric_pack = payload["research_pack"]["metric_series_pack"]
    assert metric_pack["mode"] == "chart_series_sidecar"
    assert payload["search_diagnostics"]["chart_series"]["matched"] is True
    revenue = next(series for series in metric_pack["series"] if series["canonical_metric"] == "revenue")
    assert revenue["series_key"].startswith("AAPL|revenue|company_total:company_total|USD|annual|")
    assert [point["period"] for point in revenue["points"]] == ["CY2024", "CY2025"]
    assert [point["value"] for point in revenue["points"]] == [110.0, 130.0]
    assert revenue["basis"] == "company_reported"
    assert revenue["duration"] == "period"


def test_mcp_query_context_does_not_attach_chart_series_sidecar_by_default(
    tmp_path: Path,
    monkeypatch,
):
    _write_chart_metric_fixture(tmp_path)
    index = _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))
    monkeypatch.delenv("KRW_CHART_SERIES_ENABLED", raising=False)

    assert index["build_result"].chart_series_path is not None
    assert index["build_result"].chart_series_path.exists()

    payload = json.loads(
        query_context_tool(
            question="AAPL 매출 추이 차트로 보여줘.",
            ticker="AAPL",
            limit_results=5,
        )
    )

    metric_pack = payload["research_pack"]["metric_series_pack"]
    assert metric_pack is None or metric_pack.get("mode") != "chart_series_sidecar"
    assert "chart_series" not in payload.get("search_diagnostics", {})
    assert "chart_series_pack" not in payload["research_pack"]


def test_mcp_health_reports_chart_series_sidecar_status(
    tmp_path: Path,
    monkeypatch,
):
    _write_chart_metric_fixture(tmp_path)
    index = _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload, status_code = health_payload(root=str(tmp_path))

    assert status_code == 200
    assert payload["ok"] is True
    assert payload["chart_series_present"] is True
    assert payload["chart_series_path"] == str(index["build_result"].chart_series_path.resolve())
    assert payload["chart_series_verification_ok"] is True


def test_chart_series_sidecar_opens_extended_metrics_from_start(tmp_path: Path):
    _write_chart_metric_fixture(tmp_path)
    index = _build_v3_runtime(tmp_path)
    chart_series_path = index["build_result"].chart_series_path
    assert chart_series_path is not None

    pack = query_chart_series_pack(
        chart_series_path,
        question="AAPL EPS 마진 자사주 R&D SBC M&A adjusted FCF 추이 차트",
        tickers=["AAPL"],
        limit_series=20,
    )

    assert pack is not None
    assert pack["render_hints"]["hide_raw_y_axis_amounts"] is True
    assert pack["render_hints"]["prefer_indexed_axis"] is True
    metrics = {series["canonical_metric"] for series in pack["series"]}
    assert {
        "adjusted_free_cash_flow",
        "eps",
        "gross_margin",
        "ma_cash_outflow",
        "ma_related_costs",
        "operating_margin",
        "research_and_development",
        "share_repurchase",
        "stock_based_compensation",
    }.issubset(metrics)
    ma_series = [series for series in pack["series"] if series["canonical_metric"].startswith("ma_")]
    assert {series["source_class"] for series in ma_series} == {
        "cash_flow_statement",
        "fcf_reconciliation",
    }


def test_mcp_query_context_risk_thesis_router_skips_metric_deep_path(
    tmp_path: Path,
    monkeypatch,
):
    _write_metric_dimension_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_context_tool(
            question="AAPL의 사이버 보안 리스크가 매출 성장률을 갉아먹는지 점검해줘.",
            ticker="AAPL",
            periods=["CY2024", "CY2025"],
            limit_results=10,
        )
    )

    research_pack = payload["research_pack"]
    assert payload["kernel"]["intent"] == "risk_thesis"
    assert payload["kernel"]["primary_context"] == "risk_context"
    assert "deep_metric_series" in payload["kernel"]["do_not_call"]
    assert research_pack["intent_router"]["intent"] == "risk_thesis"
    assert research_pack["intent_router"]["primary_context"] == "risk_context"
    assert research_pack["context_policy"]["run_metric_series"] is False
    assert research_pack["metric_series_pack"] is None
    assert "deep_metric_series" in research_pack["context_policy"]["do_not_call"]


def test_mcp_query_context_company_overview_router_avoids_metric_first(
    tmp_path: Path,
    monkeypatch,
):
    _write_metric_dimension_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_context_tool(
            question="AAPL은 iPhone, Services, Mac으로 어떻게 돈을 벌고 최근 매출 동인은 무엇인지 정리해줘.",
            ticker="AAPL",
            periods=["CY2024", "CY2025"],
            limit_results=10,
        )
    )

    research_pack = payload["research_pack"]
    assert payload["kernel"]["intent"] == "company_overview"
    assert payload["kernel"]["primary_context"] == "company_overview_context"
    assert research_pack["intent_router"]["intent"] == "company_overview"
    assert research_pack["intent_router"]["primary_context"] == "company_overview_context"
    assert research_pack["context_policy"]["run_metric_series"] is False
    assert research_pack["metric_series_pack"] is None


def test_mcp_query_context_projection_pack_marks_candidates_search_only_for_direct_question(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_context_tool(
            question="VG는 GPU HBM 가격 변동에 직접 노출되어 있나?",
            ticker="VG",
            limit_results=5,
        )
    )

    projection_pack = payload["research_pack"]["projection_pack"]
    assert payload["kernel"]["intent"] == "direct_exposure"
    assert payload["research_pack"]["intent_router"]["intent"] == "direct_exposure"
    assert payload["research_pack"]["metric_series_pack"] is None
    top_level_guard = payload["research_pack"]["directness_guard"]
    directness = projection_pack["directness"]
    assert top_level_guard["requires_direct_match"] is True
    assert top_level_guard["strong_claim_allowed"] is False
    assert top_level_guard["strong_claim_requires"] == ["traceable_direct", "traceable_metric_lineage"]
    assert directness["requires_direct_match"] is True
    assert directness["strong_claim_allowed"] is False
    assert directness["projection_candidates_are_search_candidates_only"] is True
    assert directness["strong_claim_requires"] == ["traceable_direct", "traceable_metric_lineage"]


def test_metric_series_calculations_include_growth_difference() -> None:
    series = [
        {
            "series_key": "target|net_sales|product:iPhone",
            "label": "iPhone",
            "metric_role": "target_dimension_metric",
            "unit": "USD",
            "periods": ["CY2024", "CY2025"],
            "points": [
                {"period": "CY2024", "value": 100.0},
                {"period": "CY2025", "value": 110.0},
            ],
        },
        {
            "series_key": "target|net_sales|segment:Services",
            "label": "Services",
            "metric_role": "target_dimension_metric",
            "unit": "USD",
            "periods": ["CY2024", "CY2025"],
            "points": [
                {"period": "CY2024", "value": 200.0},
                {"period": "CY2025", "value": 250.0},
            ],
        },
        {
            "series_key": "denominator|revenue|Company total",
            "label": "Company total",
            "metric_role": "denominator_metric",
            "unit": "USD",
            "periods": ["CY2024", "CY2025"],
            "points": [
                {"period": "CY2024", "value": 1000.0},
                {"period": "CY2025", "value": 1250.0},
            ],
        },
    ]

    calculations = agent_index_store._metric_series_calculations(series, denominator_needed=True)

    assert {share["label"] for share in calculations["share_of_total"]} == {"iPhone", "Services"}
    assert calculations["growth_rate"][0]["growth"] == pytest.approx(0.10)
    growth_difference = calculations["growth_difference"][0]
    assert growth_difference["left_label"] == "iPhone"
    assert growth_difference["right_label"] == "Services"
    assert growth_difference["difference"] == pytest.approx(0.10 - 0.25)


def test_metric_lookup_period_filters_infer_topic_year_range() -> None:
    assert agent_index_store._metric_lookup_period_filters("AAPL revenue 2021~2025", None) == [
        "2021",
        "2022",
        "2023",
        "2024",
        "2025",
    ]
    assert agent_index_store._metric_lookup_period_filters("AAPL revenue 2021~2025년에", None) == [
        "2021",
        "2022",
        "2023",
        "2024",
        "2025",
    ]
    assert agent_index_store._metric_lookup_period_filters(
        "AAPL revenue 2021~2025",
        ["CY2024"],
    ) == ["CY2024"]
    assert agent_index_store._metric_lookup_research_period_filters(
        "AAPL revenue 2021~2025년에",
        ["2021", "2025"],
    ) == ["2021", "2022", "2023", "2024", "2025"]
    assert agent_index_store._metric_lookup_period_filters_are_annual(["2021", "2022"]) is True
    assert agent_index_store._metric_lookup_period_filters_are_annual(["CY2026Q1"]) is False


def test_metric_lookup_base_metric_terms_exclude_calculation_intents() -> None:
    assert agent_index_store._metric_lookup_base_metric_terms(
        ["revenue", "net", "sales", "share", "total", "growth"]
    ) == ["revenue", "net", "sales"]


def test_research_metric_topic_preserves_raw_year_range() -> None:
    topic = agent_index_store._research_metric_topic(
        "AAPL의 iPhone과 Services 매출 비중은 2021~2025년에?",
        "aapl iphone services 2021 2025",
    )
    assert topic is not None
    assert "2021~2025" in topic
    assert agent_index_store._metric_lookup_research_period_filters(topic, None) == [
        "2021",
        "2022",
        "2023",
        "2024",
        "2025",
    ]


def test_metric_series_from_observations_dedupes_same_period_points() -> None:
    series = agent_index_store._metric_series_from_observations(
        [
            {
                "id": "metric:one",
                "ticker": "AAPL",
                "period": "CY2025",
                "metric_role": "target_dimension_metric",
                "canonical_metric": "net_sales",
                "unit": "USD",
                "value": 100,
                "dimensions": {"product": "iPhone"},
            },
            {
                "id": "metric:two",
                "ticker": "AAPL",
                "period": "CY2025",
                "metric_role": "target_dimension_metric",
                "canonical_metric": "net_sales",
                "unit": "USD",
                "value": 100,
                "dimensions": {"product": "iPhone"},
            },
        ]
    )
    assert series[0]["periods"] == ["CY2025"]
    assert series[0]["point_count"] == 1


def test_mcp_retrieve_uses_research_context_stop_guard(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        retrieve_tool(
            question="VG의 매출 성장 지속성 가정 하에 12개월 목표치를 추정해줘.",
            ticker="VG",
            limit=5,
        )
    )

    assert payload["research_status"] == "out_of_scope_for_filing_ontology"
    assert payload["agent_autonomy"]["may_continue_research"] is False
    assert payload["agent_autonomy"]["max_additional_tool_calls"] == 0
    assert payload["direct_evidence"] == []
    assert payload["related_context"] == []
    assert "krw_ontology_query" in payload["do_not_call"]
    assert payload["stop_guard"]["cannot_answer_reason"]
    assert payload["research_pack"]["stop_guard"]["cannot_answer_reason"]
    markdown = retrieve_tool(
        question="VG의 매출 성장 지속성 가정 하에 12개월 목표치를 추정해줘.",
        ticker="VG",
        limit=5,
        response_format=ResponseFormat.MARKDOWN,
    )
    assert "Cannot answer reason:" in markdown
    assert "valuation model inputs" in markdown


def test_mcp_retrieve_attaches_research_context_for_normal_question(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        retrieve_tool(
            question="VG revenue growth evidence",
            ticker="VG",
            limit=3,
        )
    )

    assert payload["query"]["tickers"] == ["VG"]
    assert payload["research_context"]["research_status"] in {
        "sufficient_for_default_answer",
        "sufficient_but_trace_recommended",
        "partial_answer_possible",
    }
    assert payload["research_context"]["agent_autonomy"]["mode"] == "bounded"
    assert "krw_ontology_retrieve" in payload["research_context"]["do_not_call"]
    assert "strong_claim_allowed" in payload["directness_guard"]
    assert payload["research_context"]["directness_guard"] == payload["directness_guard"]


def test_mcp_retrieve_without_ticker_reports_global_spine_fanout_route(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _clone_fixture_company(tmp_path, source_ticker="VG", target_ticker="XOM")
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        retrieve_tool(
            question="revenue growth customer demand natural gas operating margin",
            limit=5,
            limit_groups=5,
        )
    )

    assert payload["routing"]["mode"] == "global_spine_fanout"
    assert payload["routing"]["fallback"] is False
    assert payload["research_context"]["routing"] == payload["routing"]
    assert payload["research_context"]["missing_shards"] == {}
    assert payload["research_context"]["unknown_tickers"] == []
    signal_pack = payload["research_context"]["research_pack"]["cross_company_signal_pack"]
    assert signal_pack["mode"] == "cross_company_signal_synthesis"
    assert {"VG", "XOM"} <= set(signal_pack["ticker_basket"])
    assert {row["ticker"] for row in signal_pack["company_evidence_rows"]} >= {"VG", "XOM"}


def test_mcp_retrieve_adds_soft_guidance_for_repeated_retrieve_context(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        retrieve_tool(
            question="VG revenue growth evidence",
            ticker="VG",
            limit=3,
            agent_context={
                "tool_usage": {
                    "total": 9,
                    "krw_ontology_retrieve": 2,
                },
            },
        )
    )

    guidance = payload["agent_guidance"]
    assert guidance["severity"] == "soft"
    assert guidance["reason"] == "repeated_retrieve"
    assert "already used krw_ontology_retrieve multiple times" in guidance["message"]
    assert "Do not repeat broad retrieve calls" in guidance["message"]
    assert guidance["tool_usage"] == {
        "total": 9,
        "krw_ontology_retrieve": 2,
    }
    if "research_context" in payload:
        assert payload["research_context"]["agent_guidance"] == guidance

    markdown = retrieve_tool(
        question="VG revenue growth evidence",
        ticker="VG",
        limit=3,
        response_format=ResponseFormat.MARKDOWN,
        agent_context={
            "tool_usage": {
                "total": 9,
                "krw_ontology_retrieve": 2,
            },
        },
    )
    assert "Agent guidance:" in markdown
    assert "Prefer query_context, targeted query, trace, or chain" in markdown


def test_mcp_retrieve_omits_soft_guidance_before_retrieve_repeats(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        retrieve_tool(
            question="VG revenue growth evidence",
            ticker="VG",
            limit=3,
            agent_context={
                "tool_usage": {
                    "total": 4,
                    "krw_ontology_retrieve": 1,
                },
            },
        )
    )

    assert "agent_guidance" not in payload


def test_mcp_retrieve_skips_secondary_retrieve_when_research_context_is_sufficient(
    tmp_path: Path,
    monkeypatch,
):
    _write_metric_dimension_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        retrieve_tool(
            question="AAPL의 iPhone과 Services 매출 비중은 2021~2025년에 어떻게 달라졌는지 비교해줘.",
            ticker="AAPL",
            periods=["CY2021", "CY2022", "CY2023", "CY2024", "CY2025"],
            limit=5,
        )
    )

    assert payload["research_status"] == "sufficient_for_default_answer"
    assert payload["kernel"]["intent"] == "metric_series"
    assert payload["secondary_retrieve_skipped"] is True
    assert "legacy_retrieve_skipped" not in payload
    assert payload["research_pack"]["metric_series_pack"]["mode"] == "metric_dimension_lookup"
    assert payload["direct_evidence"] == []
    assert payload["related_context"] == []
    assert "krw_ontology_retrieve" in payload["do_not_call"]


def test_mcp_retrieve_exposes_directness_guard_for_direct_question(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        retrieve_tool(
            question="VG는 GPU HBM 가격 변동에 직접 노출되어 있나?",
            ticker="VG",
            limit=3,
        )
    )

    assert payload["directness_guard"]["requires_direct_match"] is True
    assert payload["directness_guard"]["strong_claim_allowed"] is False
    assert payload["directness_guard"]["strong_claim_requires"] == ["traceable_direct", "traceable_metric_lineage"]


def test_mcp_markdown_outputs_include_directness_guard(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    context_markdown = query_context_tool(
        question="VG는 GPU HBM 가격 변동에 직접 노출되어 있나?",
        ticker="VG",
        limit_results=5,
        response_format=ResponseFormat.MARKDOWN,
    )
    retrieve_markdown = retrieve_tool(
        question="VG는 GPU HBM 가격 변동에 직접 노출되어 있나?",
        ticker="VG",
        limit=3,
        response_format=ResponseFormat.MARKDOWN,
    )

    assert "strong_claim_allowed: False" in context_markdown
    assert "traceable_direct, traceable_metric_lineage" in context_markdown
    assert "Strong claim allowed: False" in retrieve_markdown
    assert "traceable_direct, traceable_metric_lineage" in retrieve_markdown


def test_mcp_health_payload_reports_manifest_counts_without_sqlite_count(
    tmp_path: Path,
    monkeypatch,
):
    current = _write_v3_current_release(tmp_path, release_id="20260612_020000")

    def fail_connect(*_args, **_kwargs):
        raise AssertionError("health_payload must not open SQLite")

    monkeypatch.setattr("krw_ontology.mcp_server.server.sqlite3.connect", fail_connect)

    payload, status_code = health_payload(root=str(current))

    assert status_code == 200
    assert payload["ok"] is True
    assert payload["root"] == str(current.resolve())
    assert payload["documents"] == 2
    assert payload["objects"] >= 1
    assert payload["sqlite_checked"] is False
    assert "krw_ontology_topic_map" in payload["tools"]
    assert payload["mcp_store_hot_swap"]["rotation_pending"] is False
    assert payload["mcp_store_hot_swap"]["retired_oldest_age_sec"] == 0
    assert payload["mcp_store_hot_swap"]["retired_global_spine_stores"] == []


def test_mcp_tool_lane_runs_blocking_work_off_event_loop():
    def blocking_work() -> int:
        return threading.get_ident()

    async def run() -> tuple[int, int]:
        loop_thread_id = threading.get_ident()
        worker_thread_id = await mcp_server._run_tool_in_lane("fast", blocking_work)
        return loop_thread_id, worker_thread_id

    loop_thread_id, worker_thread_id = asyncio.run(run())

    assert worker_thread_id != loop_thread_id


def test_mcp_broad_lane_limits_blocking_work_concurrency(monkeypatch):
    monkeypatch.setenv("KRW_MCP_BROAD_LANE_CONCURRENCY", "1")
    active = 0
    max_active = 0
    lock = threading.Lock()

    def blocking_work() -> str:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.02)
            return "ok"
        finally:
            with lock:
                active -= 1

    async def run() -> list[str]:
        return await asyncio.gather(
            mcp_server._run_tool_in_lane("broad", blocking_work),
            mcp_server._run_tool_in_lane("broad", blocking_work),
        )

    assert asyncio.run(run()) == ["ok", "ok"]
    assert max_active == 1


def test_mcp_query_context_wrapper_routes_tickerless_calls_to_broad_lane(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def fake_run_tool_in_lane(lane: str, func, **kwargs):
        calls.append({"lane": lane, "func": func, "kwargs": kwargs})
        return "ok"

    monkeypatch.setattr(mcp_server, "_run_tool_in_lane", fake_run_tool_in_lane)

    result = asyncio.run(mcp_server.krw_ontology_query_context(question="AI datacenter beneficiaries"))

    assert result == "ok"
    assert calls[0]["lane"] == "broad"
    assert calls[0]["func"] is mcp_server.query_context_tool


def test_mcp_query_context_wrapper_routes_scoped_calls_to_fast_lane(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def fake_run_tool_in_lane(lane: str, func, **kwargs):
        calls.append({"lane": lane, "func": func, "kwargs": kwargs})
        return "ok"

    monkeypatch.setattr(mcp_server, "_run_tool_in_lane", fake_run_tool_in_lane)

    result = asyncio.run(mcp_server.krw_ontology_query_context(question="AI capex", ticker="AAPL"))

    assert result == "ok"
    assert calls[0]["lane"] == "fast"
    assert calls[0]["kwargs"]["ticker"] == "AAPL"


def test_spine_router_tickerless_query_context_caps_candidate_fanout(monkeypatch):
    monkeypatch.delenv("KRW_ROUTER_TICKERLESS_QUERY_CONTEXT_MAX_TICKERS", raising=False)
    router = object.__new__(OntologySpineRouter)
    captured: dict[str, Any] = {}

    def fake_candidate_tickers(question: str, *, explicit_tickers, limit: int) -> list[str]:
        captured["question"] = question
        captured["explicit_tickers"] = explicit_tickers
        captured["candidate_limit"] = limit
        return ["AAPL", "MSFT", "NVDA", "AVGO", "META"]

    def fake_store_fanout(tickers, callback):
        captured["fanout_tickers"] = list(tickers)
        return {}, {}, 0

    router._candidate_tickers = fake_candidate_tickers
    router._store_fanout = fake_store_fanout
    router._route_payload = lambda mode, tickers: {"mode": mode, "tickers": list(tickers)}
    router._fanout_diagnostics = lambda worker_count, errors: {
        "fanout_parallel": False,
        "fanout_workers": worker_count,
    }
    router._attach_missing_release_parts = lambda payload, tickers: None
    router.list_documents = lambda: []
    router._chart_series_path = Path("chart_series.sqlite")
    router._chart_series_status = {}

    payload = OntologySpineRouter.query_context(
        router,
        question="AI datacenter beneficiaries",
        limit_tickers=20,
    )

    assert captured["candidate_limit"] == 5
    assert captured["fanout_tickers"] == ["AAPL", "MSFT", "NVDA", "AVGO", "META"]
    assert payload["routing"]["requested_limit_tickers"] == 20
    assert payload["routing"]["effective_limit_tickers"] == 5
    assert payload["routing"]["tickerless_candidate_cap"] == 5


def test_spine_router_scoped_query_context_keeps_requested_candidate_limit(monkeypatch):
    monkeypatch.delenv("KRW_ROUTER_TICKERLESS_QUERY_CONTEXT_MAX_TICKERS", raising=False)
    router = object.__new__(OntologySpineRouter)
    captured: dict[str, Any] = {}

    def fake_candidate_tickers(question: str, *, explicit_tickers, limit: int) -> list[str]:
        captured["explicit_tickers"] = list(explicit_tickers or [])
        captured["candidate_limit"] = limit
        return list(explicit_tickers or [])

    def fake_store_fanout(tickers, callback):
        captured["fanout_tickers"] = list(tickers)
        return {}, {}, 0

    router._candidate_tickers = fake_candidate_tickers
    router._store_fanout = fake_store_fanout
    router._route_payload = lambda mode, tickers: {"mode": mode, "tickers": list(tickers)}
    router._fanout_diagnostics = lambda worker_count, errors: {
        "fanout_parallel": False,
        "fanout_workers": worker_count,
    }
    router._attach_missing_release_parts = lambda payload, tickers: None
    router.list_documents = lambda: []
    router._chart_series_path = Path("chart_series.sqlite")
    router._chart_series_status = {}

    payload = OntologySpineRouter.query_context(
        router,
        question="AI capex",
        tickers=["AAPL", "MSFT", "NVDA"],
        limit_tickers=20,
    )

    assert captured["candidate_limit"] == 20
    assert captured["fanout_tickers"] == ["AAPL", "MSFT", "NVDA"]
    assert payload["routing"]["requested_limit_tickers"] == 20
    assert payload["routing"]["effective_limit_tickers"] == 20
    assert "tickerless_candidate_cap" not in payload["routing"]


def test_mcp_live_payload_does_not_read_release_state(monkeypatch):
    def fail_load_manifest(*_args, **_kwargs):
        raise AssertionError("live_payload must not inspect release state")

    monkeypatch.setattr("krw_ontology.mcp_server.server.load_release_manifest", fail_load_manifest)

    payload, status_code = live_payload()

    assert status_code == 200
    assert payload == {"ok": True, "service": "krw_ontology_mcp"}


def test_mcp_ready_payload_skips_runtime_cache_for_worker_admission(
    tmp_path: Path,
    monkeypatch,
):
    current = _write_v3_current_release(tmp_path, release_id="20260612_020000")

    def fail_runtime_cache_status():
        raise AssertionError("ready_payload must not inspect runtime store/cache state")

    def fail_connect(*_args, **_kwargs):
        raise AssertionError("ready_payload must not open SQLite")

    monkeypatch.setattr("krw_ontology.mcp_server.server.mcp_runtime_cache_status", fail_runtime_cache_status)
    monkeypatch.setattr("krw_ontology.mcp_server.server.sqlite3.connect", fail_connect)

    payload, status_code = ready_payload(root=str(current))

    assert status_code == 200
    assert payload["ok"] is True
    assert payload["root"] == str(current.resolve())
    assert payload["documents"] == 2
    assert payload["objects"] >= 1
    assert payload["sqlite_checked"] is False
    assert "krw_ontology_topic_map" in payload["tools"]
    assert "cache" not in payload
    assert "mcp_store_hot_swap" not in payload


def test_mcp_metrics_payload_exposes_release_and_hot_swap_metrics(tmp_path: Path):
    current = _write_v3_current_release(tmp_path, release_id="20260612_020000")

    payload, status_code = metrics_payload(root=str(current))

    assert status_code == 200
    assert 'krw_ontology_mcp_health_ok{env="prod",release_id="20260612_020000"} 1' in payload
    assert 'krw_ontology_mcp_release_documents{env="prod",release_id="20260612_020000"} 2' in payload
    assert "krw_ontology_mcp_global_topic_spine_rows" in payload
    assert "krw_ontology_mcp_global_topics" not in payload
    assert "krw_ontology_mcp_store_rotation_pending" in payload
    assert "krw_ontology_mcp_store_rotations_total" in payload
    assert "# TYPE krw_ontology_mcp_store_retired_leased gauge" in payload


def test_mcp_diagnostics_payload_reports_live_index_counts(tmp_path: Path):
    current = _write_v3_current_release(tmp_path, release_id="20260612_020000")

    payload, status_code = diagnostics_payload(root=str(current))

    assert status_code == 200
    assert payload["ok"] is True
    assert payload["documents"] == 2
    assert payload["objects"] >= 1
    assert payload["sqlite_checked"] is True


def test_mcp_health_payload_reports_missing_index(tmp_path: Path):
    payload, status_code = health_payload(root=str(tmp_path))

    assert status_code == 503
    assert payload["ok"] is False
    assert payload["error"] == "global_spine_not_found"


def test_mcp_external_tool_schema_does_not_expose_runtime_path_overrides():
    for tool in mcp._tool_manager.list_tools():
        properties = (tool.parameters or {}).get("properties") or {}
        assert "root" not in properties, tool.name
        assert "index_path" not in properties, tool.name
        assert "global_spine_path" not in properties, tool.name
        assert "release_root" not in properties, tool.name
        assert "manifest_path" not in properties, tool.name


def _write_fixture(
    root: Path,
    *,
    period: str = "FY2025",
    text: str = "Revenue growth accelerated because customer demand increased for LNG volumes.",
    document_type: str = "10-K",
    doc_type_key: str | None = None,
) -> None:
    doc_key = doc_type_key or document_type.replace("-", "")
    ontology_dir = root / "companies" / "VG" / "ontology" / doc_key / period
    sources_dir = root / "companies" / "VG" / "sources" / doc_key / period
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    source_document_id = f"source:VG:{period}:{doc_key}"
    span_id = f"span:VG:{period}:{doc_key}:0001"
    quote_id = f"quote:VG:{period}:{doc_key}:0001"
    claim_id = f"claim:VG:{period}:{doc_key}:revenue-growth"
    risk_claim_id = f"claim:VG:{period}:{doc_key}:regulatory-risk"
    driver_id = f"business_factor:VG:{period}:{doc_key}:revenue-growth"
    risk_id = f"business_factor:VG:{period}:{doc_key}:regulatory-risk"
    unsupported_risk_id = f"business_factor:VG:{period}:{doc_key}:unsupported-risk"
    activity_id = f"business_activity:VG:{period}:{doc_key}:lng-sales"
    exposure_id = f"external_factor_exposure:VG:{period}:{doc_key}:natural-gas-price-operating-margin"
    agreement_id = f"agreement:VG:{period}:{doc_key}:spa-termination"
    span = {
        "id": span_id,
        "type": "SourceSpan",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "section_name": "item7",
        "section_key": "item7",
        "span_index": 1,
        "text": text,
        "review_status": "accepted",
    }
    quote = {
        "id": quote_id,
        "type": "EvidenceQuote",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "source_span_id": span_id,
        "quote_text": text,
        "quote_type": "business_update",
        "section_name": "item7",
        "review_status": "accepted",
    }
    claim = {
        "id": claim_id,
        "type": "ResearchClaim",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "claim_text": "Revenue growth accelerated because customer demand increased.",
        "claim_type": "business_update",
        "supported_by_quotes": [quote_id],
        "related_metrics": ["revenue"],
        "review_status": "accepted",
    }
    risk_claim = {
        "id": risk_claim_id,
        "type": "ResearchClaim",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "claim_text": "Regulatory risk could delay project approvals.",
        "claim_type": "risk_assessment",
        "supported_by_quotes": [quote_id],
        "related_metrics": ["revenue"],
        "review_status": "accepted",
    }
    driver = {
        "id": driver_id,
        "type": "BusinessFactor",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "name": "Revenue growth",
        "factor_roles": ["growth_driver"],
        "description": "Customer demand increased and supported revenue growth.",
        "category": "demand",
        "supported_by_claims": [claim_id],
        "review_status": "accepted",
    }
    risk = {
        "id": risk_id,
        "type": "BusinessFactor",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "name": "Regulatory risk",
        "factor_roles": ["risk"],
        "description": "Regulatory risk could delay approvals and pressure revenue growth.",
        "category": "regulatory",
        "supported_by_claims": [risk_claim_id],
        "review_status": "accepted",
    }
    unsupported_risk = {
        "id": unsupported_risk_id,
        "type": "BusinessFactor",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "name": "Unsupported risk",
        "factor_roles": ["risk"],
        "description": "Unsupported risk has no supporting claim.",
        "category": "operational",
        "supported_by_claims": [],
        "review_status": "accepted",
    }
    activity = {
        "id": activity_id,
        "type": "BusinessActivity",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "name": "LNG sales",
        "activity_type": "lng_sales",
        "description": "VG sells LNG under long-term SPAs and spot cargoes.",
        "related_metrics": ["revenue", "cash_flow"],
        "supported_by_claims": [claim_id],
        "review_status": "accepted",
    }
    exposure = {
        "id": exposure_id,
        "type": "ExternalFactorExposure",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "factor": "natural_gas_price",
        "factor_category": "commodity_price",
        "benchmark": "Henry Hub",
        "impact_channel": "operating_margin",
        "effect_direction": "negative",
        "mechanism": "Feed gas costs can affect operating margin.",
        "evidence_grade": "direct",
        "supported_by_claims": [claim_id],
        "review_status": "accepted",
    }
    agreement = {
        "id": agreement_id,
        "type": "AgreementTerm",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "name": "SPA termination and debt acceleration",
        "agreement_type": "sale and purchase agreement",
        "agreement_subtype": "SPA",
        "counterparty": "LNG customer",
        "termination_terms": "Termination of the SPA may create project financing or debt acceleration risk.",
        "covenant_terms": "Project financing covenants may be affected by contract termination.",
        "affected_channels": ["liquidity", "project_timing"],
        "supported_by_claims": [risk_claim_id],
        "review_status": "accepted",
    }
    edges = [
        {
            "id": "edge:quote-claim",
            "type": "Edge",
            "ticker": "VG",
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "from_id": quote_id,
            "to_id": claim_id,
            "relation_id": "supports",
            "relation_name": "supports",
            "review_status": "accepted",
        },
        {
            "id": "edge:claim-driver",
            "type": "Edge",
            "ticker": "VG",
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "from_id": claim_id,
            "to_id": driver_id,
            "relation_id": "supports",
            "relation_name": "supports",
            "review_status": "accepted",
        },
        {
            "id": "edge:claim-risk",
            "type": "Edge",
            "ticker": "VG",
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "from_id": risk_claim_id,
            "to_id": risk_id,
            "relation_id": "supports",
            "relation_name": "supports",
            "review_status": "accepted",
        },
    ]

    write_jsonl(ontology_dir / "spans.jsonl", [span])
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", [quote])
    write_jsonl(ontology_dir / "claims.jsonl", [claim, risk_claim])
    write_jsonl(ontology_dir / "business_factors.jsonl", [risk, unsupported_risk, driver])
    write_jsonl(ontology_dir / "business_activities.jsonl", [activity])
    write_jsonl(ontology_dir / "external_factor_exposures.jsonl", [exposure])
    write_jsonl(ontology_dir / "agreement_terms.jsonl", [agreement])
    write_jsonl(ontology_dir / "edges.jsonl", edges)
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    atomic_write_json(
        ontology_dir / "artifact_index.json",
        {
            "ticker": "VG",
            "document_type": document_type,
            "doc_type_key": doc_key,
            "period": period,
            "files": {
                "spans": f"companies/VG/ontology/{doc_key}/{period}/spans.jsonl",
                "evidence_quotes": f"companies/VG/ontology/{doc_key}/{period}/evidence_quotes.jsonl",
                "claims": f"companies/VG/ontology/{doc_key}/{period}/claims.jsonl",
                "business_factors": f"companies/VG/ontology/{doc_key}/{period}/business_factors.jsonl",
                "business_activities": f"companies/VG/ontology/{doc_key}/{period}/business_activities.jsonl",
                "external_factor_exposures": f"companies/VG/ontology/{doc_key}/{period}/external_factor_exposures.jsonl",
                "agreement_terms": f"companies/VG/ontology/{doc_key}/{period}/agreement_terms.jsonl",
                "edges": f"companies/VG/ontology/{doc_key}/{period}/edges.jsonl",
            },
            "counts": {
                "spans": 1,
                "evidence_quotes": 1,
                "claims": 2,
                "business_factors": 3,
                "business_activities": 1,
                "external_factor_exposures": 1,
                "agreement_terms": 1,
                "edges": 3,
            },
        },
    )
    _write_context_fixture(
        root,
        period=period,
        activity_id=activity_id,
        exposure_id=exposure_id,
        claim_id=claim_id,
        quote_id=quote_id,
    )


def _write_v3_current_release(tmp_path: Path, *, release_id: str = "20260612_010000") -> Path:
    release = tmp_path / "releases" / "prod" / release_id
    release.mkdir(parents=True)
    _write_fixture(release)
    build_spine_shard_release_outputs(
        release,
        release_id=release.name,
        workers=1,
        no_cache=True,
    )
    write_release_manifest_v3(release, release_id=release.name, env="prod")
    current = release.parent / "current"
    current.symlink_to(release.name)
    return current


def _clone_fixture_company(
    root: Path,
    *,
    source_ticker: str,
    target_ticker: str,
) -> None:
    source_dir = root / "companies" / source_ticker
    target_dir = root / "companies" / target_ticker
    shutil.copytree(source_dir, target_dir)
    for path in target_dir.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(source_ticker, target_ticker), encoding="utf-8")


def _build_v3_runtime(root: Path, *, release_id: str = "test-v3-runtime") -> dict[str, Any]:
    result = build_spine_shard_release_outputs(
        root,
        release_id=release_id,
        workers=1,
        no_cache=True,
    )
    write_release_manifest_v3(root, release_id=release_id, env="dev")
    os.environ["KRW_ONTOLOGY_ENV"] = "dev"
    os.environ["KRW_ONTOLOGY_RELEASE_ROOT"] = str(root.resolve())
    os.environ["KRW_ONTOLOGY_GLOBAL_SPINE_PATH"] = str(result.global_spine_path.resolve())
    os.environ["KRW_ONTOLOGY_SHARD_MANIFEST_PATH"] = str(result.shard_manifest_path.resolve())
    return {
        "index_path": result.global_spine_path.resolve(),
        "release_root": root.resolve(),
        "build_result": result,
    }


def _write_context_fixture(
    root: Path,
    *,
    period: str,
    activity_id: str,
    exposure_id: str,
    claim_id: str,
    quote_id: str,
) -> None:
    context_dir = root / "companies" / "VG" / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    profile = {
        "id": "company_business_profile:VG:ALL",
        "type": "CompanyBusinessProfile",
        "ticker": "VG",
        "source_document_id": "source:VG:ALL:COMPANY",
        "document_type": "COMPANY",
        "period": "ALL",
        "sector": "energy_lng",
        "business_model_summary": "Primary activities: LNG sales. Key external factors: natural_gas_price.",
        "primary_business_activities": ["LNG sales"],
        "primary_revenue_sources": ["LNG sales"],
        "primary_cost_sources": ["Feed gas procurement"],
        "key_external_factors": ["natural_gas_price"],
        "key_metrics": ["revenue", "operating_margin"],
        "key_uncertainties": ["natural_gas_price via operating_margin"],
        "source_object_ids": [activity_id, exposure_id],
        "review_status": "accepted",
    }
    trend = {
        "id": "trend:VG:ALL:lng-sales-revenue",
        "type": "TrendObservation",
        "ticker": "VG",
        "source_document_id": "source:VG:ALL:COMPANY",
        "document_type": "COMPANY",
        "period": "ALL",
        "subject": "LNG sales",
        "metric_or_factor": "revenue",
        "from_period": "FY2024",
        "to_period": period,
        "direction": "increased",
        "magnitude_text": None,
        "interpretation": "LNG sales activity and natural gas exposure are context for revenue growth.",
        "supported_by_objects": [activity_id, exposure_id],
        "confidence": "medium",
        "review_status": "accepted",
    }
    change = {
        "id": "change_event:VG:ALL:revenue-growth",
        "type": "ChangeEvent",
        "ticker": "VG",
        "source_document_id": "source:VG:ALL:COMPANY",
        "document_type": "COMPANY",
        "period": "ALL",
        "event_type": "growth_signal",
        "event_date": None,
        "description": "Revenue growth was tied to customer demand and LNG sales context.",
        "affected_objects": [activity_id],
        "supported_by_claims": [claim_id],
        "supported_by_quotes": [quote_id],
        "confidence": "medium",
        "review_status": "accepted",
    }
    edges = [
        {
            "id": "edge:VG:ALL:claim-change",
            "type": "Edge",
            "ticker": "VG",
            "source_document_id": "source:VG:ALL:COMPANY",
            "document_type": "COMPANY",
            "period": "ALL",
            "from_id": claim_id,
            "to_id": change["id"],
            "relation_id": "supports_change_event",
            "relation_name": "supports_change_event",
            "edge_class": "event",
            "evidence_level": "direct",
            "generation_method": "test_fixture",
            "rationale": "The claim supports this change event.",
            "confidence": "high",
            "review_status": "accepted",
        },
        {
            "id": "edge:VG:ALL:change-activity",
            "type": "Edge",
            "ticker": "VG",
            "source_document_id": "source:VG:ALL:COMPANY",
            "document_type": "COMPANY",
            "period": "ALL",
            "from_id": change["id"],
            "to_id": activity_id,
            "relation_id": "affects_object",
            "relation_name": "affects_object",
            "edge_class": "event",
            "evidence_level": "inferred",
            "generation_method": "test_fixture",
            "rationale": "The change event affects this business activity.",
            "confidence": "medium",
            "review_status": "accepted",
        },
    ]
    write_jsonl(context_dir / "company_business_profiles.jsonl", [profile])
    write_jsonl(context_dir / "trend_observations.jsonl", [trend])
    write_jsonl(context_dir / "change_events.jsonl", [change])
    write_jsonl(context_dir / "edges.jsonl", edges)
    atomic_write_json(
        context_dir / "artifact_index.json",
        {
            "ticker": "VG",
            "document_type": "COMPANY",
            "doc_type_key": "COMPANY",
            "period": "ALL",
            "files": {
                "company_business_profiles": "companies/VG/context/company_business_profiles.jsonl",
                "trend_observations": "companies/VG/context/trend_observations.jsonl",
                "change_events": "companies/VG/context/change_events.jsonl",
                "edges": "companies/VG/context/edges.jsonl",
            },
            "counts": {
                "company_business_profiles": 1,
                "trend_observations": 1,
                "change_events": 1,
                "edges": 2,
            },
            "schema_version": "0.1.0",
            "source_period": period,
        },
    )


def _write_metric_dimension_fixture(root: Path) -> None:
    period = "CY2025"
    ontology_dir = root / "companies" / "AAPL" / "ontology" / "10K" / period
    ontology_dir.mkdir(parents=True, exist_ok=True)

    def metric(
        suffix: str,
        *,
        metric_name: str,
        value: str,
        text: str,
        dimensions: dict[str, str] | None = None,
    ) -> dict:
        return {
            "id": f"metric_observation:AAPL:{period}:10K:{suffix}",
            "type": "MetricObservation",
            "object_type": "MetricObservation",
            "ticker": "AAPL",
            "source_document_id": f"source:AAPL:{period}:10K",
            "document_type": "10-K",
            "period": period,
            "metric_name": metric_name,
            "canonical_metric": metric_name,
            "value": value,
            "unit": "USD",
            "dimensions": dimensions or {},
            "text": text,
            "review_status": "accepted",
        }

    metrics = [
        metric(
            "total-revenue",
            metric_name="revenue",
            value="416200000000",
            text="CY2025 total net sales revenue was $416.2B.",
        ),
        metric(
            "net-income",
            metric_name="net_income",
            value="112000000000",
            text="CY2025 net income was $112.0B.",
        ),
        metric(
            "iphone-net-sales",
            metric_name="net_sales",
            value="201200000000",
            text="CY2025 iPhone net sales were $201.2B.",
            dimensions={"product": "iPhone"},
        ),
        metric(
            "services-net-sales",
            metric_name="net_sales",
            value="109200000000",
            text="CY2025 Services net sales were $109.2B.",
            dimensions={"segment": "Services"},
        ),
        metric(
            "greater-china-net-sales",
            metric_name="net_sales",
            value="64300000000",
            text="CY2025 Greater China net sales were $64.3B.",
            dimensions={"geography": "Greater China"},
        ),
    ]

    def xbrl_fact(
        suffix: str,
        *,
        value: str,
        fiscal_year: int,
        dimensions: list[str],
    ) -> dict:
        return {
            "id": f"xbrl:AAPL:{period}:10K:RevenueFromContractWithCustomerExcludingAssessedTax:{suffix}",
            "type": "XBRLFact",
            "ticker": "AAPL",
            "source_document_id": f"source:AAPL:{period}:10K",
            "document_type": "10-K",
            "period": period,
            "taxonomy_tag": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "safe_taxonomy_tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
            "value": float(value),
            "unit": "usd",
            "context": {
                "fiscal_year": fiscal_year,
                "period_type": "duration",
                "dimensions": dimensions,
                "has_dimensions": bool(dimensions),
            },
            "review_status": "accepted",
        }

    xbrl_facts = [
        xbrl_fact("company-total-revenue", value="416200000000", fiscal_year=2025, dimensions=[]),
        xbrl_fact("mac-revenue", value="31000000000", fiscal_year=2025, dimensions=["aapl:MacMember"]),
        xbrl_fact("ipad-revenue", value="28000000000", fiscal_year=2025, dimensions=["aapl:IPadMember"]),
    ]

    write_jsonl(ontology_dir / "metric_observations.jsonl", metrics)
    write_jsonl(ontology_dir / "xbrl_facts.jsonl", xbrl_facts)
    atomic_write_json(
        ontology_dir / "artifact_index.json",
        {
            "ticker": "AAPL",
            "document_type": "10-K",
            "doc_type_key": "10K",
            "period": period,
            "files": {
                "metric_observations": f"companies/AAPL/ontology/10K/{period}/metric_observations.jsonl",
                "xbrl_facts": f"companies/AAPL/ontology/10K/{period}/xbrl_facts.jsonl",
            },
            "counts": {
                "metric_observations": len(metrics),
                "xbrl_facts": len(xbrl_facts),
            },
        },
    )

    def write_company_metrics(
        ticker: str,
        specs: list[tuple[str, str, str, str, Any]],
    ) -> None:
        company_dir = root / "companies" / ticker / "ontology" / "10K" / period
        company_dir.mkdir(parents=True, exist_ok=True)
        company_metrics = [
            {
                "id": f"metric_observation:{ticker}:{period}:10K:{suffix}",
                "type": "MetricObservation",
                "object_type": "MetricObservation",
                "ticker": ticker,
                "source_document_id": f"source:{ticker}:{period}:10K",
                "document_type": "10-K",
                "period": period,
                "metric_name": metric_name,
                "canonical_metric": metric_name,
                "value": value,
                "unit": "USD",
                "dimensions": dimensions or {},
                "text": text,
                "review_status": "accepted",
            }
            for suffix, metric_name, value, text, dimensions in specs
        ]
        write_jsonl(company_dir / "metric_observations.jsonl", company_metrics)
        atomic_write_json(
            company_dir / "artifact_index.json",
            {
                "ticker": ticker,
                "document_type": "10-K",
                "doc_type_key": "10K",
                "period": period,
                "files": {
                    "metric_observations": f"companies/{ticker}/ontology/10K/{period}/metric_observations.jsonl",
                },
                "counts": {
                    "metric_observations": len(company_metrics),
                },
            },
        )

    write_company_metrics(
        "NVDA",
        [
            (
                "total-revenue",
                "revenue",
                "60000000000",
                "CY2025 total revenue was $60.0B.",
                None,
            ),
            (
                "data-center-revenue",
                "revenue",
                "47000000000",
                "CY2025 Data Center revenue was $47.0B.",
                {"segment": "Data Center"},
            ),
            (
                "gaming-revenue",
                "revenue",
                "10000000000",
                "CY2025 Gaming revenue was $10.0B.",
                {"segment": "Gaming"},
            ),
        ],
    )
    write_company_metrics(
        "AMZN",
        [
            (
                "total-revenue",
                "revenue",
                "638000000000",
                "CY2025 total net sales were $638.0B.",
                None,
            ),
            (
                "aws-operating-income",
                "operating_income",
                "43000000000",
                "CY2025 AWS operating income was $43.0B.",
                {"segment": "AWS"},
            ),
        ],
    )
    write_company_metrics(
        "MSFT",
        [
            (
                "total-revenue",
                "revenue",
                "281000000000",
                "CY2025 total revenue was $281.0B.",
                None,
            ),
            (
                "intelligent-cloud-revenue",
                "revenue",
                "105000000000",
                "CY2025 Intelligent Cloud revenue was $105.0B.",
                [{"axis": "BusinessSegmentAxis", "member": "IntelligentCloudMember"}],
            ),
        ],
    )

    write_company_metrics(
        "GOOGL",
        [
            (
                "total-revenue",
                "revenue",
                "350000000000",
                "CY2025 total revenue was $350.0B.",
                None,
            ),
            (
                "google-cloud-revenue",
                "revenue",
                "47000000000",
                "CY2025 Google Cloud revenue was $47.0B.",
                None,
            ),
        ],
    )

    service_dir = root / "companies" / "SVCX" / "ontology" / "10K" / period
    service_dir.mkdir(parents=True, exist_ok=True)
    service_facts = [
        {
            "id": f"xbrl:SVCX:{period}:10K:RevenueFromContractWithCustomerExcludingAssessedTax:service-revenue",
            "type": "XBRLFact",
            "ticker": "SVCX",
            "source_document_id": f"source:SVCX:{period}:10K",
            "document_type": "10-K",
            "period": period,
            "taxonomy_tag": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "safe_taxonomy_tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
            "value": 123000000.0,
            "unit": "usd",
            "context": {
                "fiscal_year": 2025,
                "period_type": "duration",
                "dimensions": ["us-gaap:ServiceMember"],
                "has_dimensions": True,
            },
            "review_status": "accepted",
        }
    ]
    write_jsonl(service_dir / "xbrl_facts.jsonl", service_facts)
    atomic_write_json(
        service_dir / "artifact_index.json",
        {
            "ticker": "SVCX",
            "document_type": "10-K",
            "doc_type_key": "10K",
            "period": period,
            "files": {
                "xbrl_facts": f"companies/SVCX/ontology/10K/{period}/xbrl_facts.jsonl",
            },
            "counts": {
                "xbrl_facts": len(service_facts),
            },
        },
    )


def _write_chart_metric_fixture(root: Path) -> None:
    def write_period(period: str, metrics: list[dict[str, Any]]) -> None:
        ontology_dir = root / "companies" / "AAPL" / "ontology" / "10K" / period
        ontology_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(ontology_dir / "metric_observations.jsonl", metrics)
        atomic_write_json(
            ontology_dir / "artifact_index.json",
            {
                "ticker": "AAPL",
                "document_type": "10-K",
                "doc_type_key": "10K",
                "period": period,
                "files": {
                    "metric_observations": f"companies/AAPL/ontology/10K/{period}/metric_observations.jsonl",
                },
                "counts": {"metric_observations": len(metrics)},
            },
        )

    def metric_observation(
        suffix: str,
        *,
        document_period: str,
        fiscal_year: int,
        canonical_metric: str,
        value: str,
        unit: str = "USD",
        metric_name: str | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": f"metric_observation:AAPL:{document_period}:10K:{suffix}",
            "type": "MetricObservation",
            "object_type": "MetricObservation",
            "ticker": "AAPL",
            "source_document_id": f"source:AAPL:{document_period}:10K",
            "document_type": "10-K",
            "period": document_period,
            "period_end": f"{fiscal_year}-12-31",
            "fiscal_year": fiscal_year,
            "metric_name": metric_name or canonical_metric,
            "canonical_metric": canonical_metric,
            "value": value,
            "unit": unit,
            "dimensions": {},
            "text": text or f"{document_period} {canonical_metric}: {value} {unit}.",
            "review_status": "accepted",
        }

    def revenue_metric(suffix: str, *, document_period: str, fiscal_year: int, value: str) -> dict[str, Any]:
        return metric_observation(
            suffix,
            document_period=document_period,
            fiscal_year=fiscal_year,
            canonical_metric="revenue",
            value=value,
            text=f"{document_period} revenue: ${value}.",
        )

    extended_2024 = [
        metric_observation(
            "eps-2024",
            document_period="CY2024",
            fiscal_year=2024,
            canonical_metric="eps",
            value="8.00",
            unit="USD/share",
        ),
        metric_observation(
            "gross-margin-2024",
            document_period="CY2024",
            fiscal_year=2024,
            canonical_metric="gross_margin",
            value="41",
            unit="%",
        ),
        metric_observation(
            "operating-margin-2024",
            document_period="CY2024",
            fiscal_year=2024,
            canonical_metric="operating_margin",
            value="30",
            unit="%",
        ),
        metric_observation(
            "repurchase-2024",
            document_period="CY2024",
            fiscal_year=2024,
            canonical_metric="share_repurchase",
            value="90",
        ),
        metric_observation(
            "rd-2024",
            document_period="CY2024",
            fiscal_year=2024,
            canonical_metric="research_and_development",
            value="25",
        ),
        metric_observation(
            "sbc-2024",
            document_period="CY2024",
            fiscal_year=2024,
            canonical_metric="stock_based_compensation",
            value="12",
        ),
        metric_observation(
            "ma-cash-2024",
            document_period="CY2024",
            fiscal_year=2024,
            canonical_metric="business_combinations_net_of_cash_acquired",
            metric_name="Business combinations, net of cash acquired",
            value="4",
        ),
        metric_observation(
            "ma-costs-2024",
            document_period="CY2024",
            fiscal_year=2024,
            canonical_metric="business_combination_and_other_related_costs",
            metric_name="Business combination and other related costs",
            value="1",
        ),
        metric_observation(
            "adjusted-fcf-2024",
            document_period="CY2024",
            fiscal_year=2024,
            canonical_metric="adjusted_free_cash_flow",
            value="65",
        ),
    ]
    extended_2025 = [
        metric_observation(
            "eps-2025",
            document_period="CY2025",
            fiscal_year=2025,
            canonical_metric="eps",
            value="9.00",
            unit="USD/share",
        ),
        metric_observation(
            "gross-margin-2025",
            document_period="CY2025",
            fiscal_year=2025,
            canonical_metric="gross_margin",
            value="42",
            unit="%",
        ),
        metric_observation(
            "operating-margin-2025",
            document_period="CY2025",
            fiscal_year=2025,
            canonical_metric="operating_margin",
            value="31",
            unit="%",
        ),
        metric_observation(
            "repurchase-2025",
            document_period="CY2025",
            fiscal_year=2025,
            canonical_metric="share_repurchase",
            value="95",
        ),
        metric_observation(
            "rd-2025",
            document_period="CY2025",
            fiscal_year=2025,
            canonical_metric="research_and_development",
            value="30",
        ),
        metric_observation(
            "sbc-2025",
            document_period="CY2025",
            fiscal_year=2025,
            canonical_metric="stock_based_compensation",
            value="14",
        ),
        metric_observation(
            "ma-cash-2025",
            document_period="CY2025",
            fiscal_year=2025,
            canonical_metric="business_combinations_net_of_cash_acquired",
            metric_name="Business combinations, net of cash acquired",
            value="18",
        ),
        metric_observation(
            "ma-costs-2025",
            document_period="CY2025",
            fiscal_year=2025,
            canonical_metric="business_combination_and_other_related_costs",
            metric_name="Business combination and other related costs",
            value="2",
        ),
        metric_observation(
            "adjusted-fcf-2025",
            document_period="CY2025",
            fiscal_year=2025,
            canonical_metric="adjusted_free_cash_flow",
            value="70",
        ),
    ]

    write_period(
        "CY2024",
        [
            revenue_metric(
                "current-2024",
                document_period="CY2024",
                fiscal_year=2024,
                value="100",
            ),
            *extended_2024,
        ],
    )
    write_period(
        "CY2025",
        [
            revenue_metric(
                "comparative-2024",
                document_period="CY2025",
                fiscal_year=2024,
                value="110",
            ),
            revenue_metric(
                "current-2025",
                document_period="CY2025",
                fiscal_year=2025,
                value="130",
            ),
            *extended_2025,
        ],
    )


def test_metric_lookup_is_dimension_aware_not_company_total_only(tmp_path: Path) -> None:
    _write_metric_dimension_fixture(tmp_path)
    index = build_agent_index(tmp_path)
    assert index["totals"]["metric_lookup"] > 0
    assert index["totals"]["metric_dimension_lookup"] > 0
    assert index["totals"]["company_dimension_catalog"] > 0

    with OntologyStore(index["index_path"]) as store:
        integrity = store.conn.execute("PRAGMA integrity_check").fetchone()
        assert integrity[0] == "ok"

        total_results, total_diagnostics = store.query_compact_with_diagnostics(
            topic="total net sales",
            tickers=["AAPL"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert total_results[0]["id"].endswith("total-revenue")
        assert total_diagnostics["search_strategy"]["company_total_role"] == "primary"
        assert "XBRLFact" not in total_diagnostics["search_strategy"]["object_types"]
        assert all(result["type"] != "XBRLFact" for result in total_results)
        skipped_xbrl_total = store.conn.execute(
            "SELECT COUNT(*) AS count FROM metric_lookup WHERE object_id LIKE '%company-total-revenue'"
        ).fetchone()
        assert skipped_xbrl_total["count"] == 0

        context = store.index_context()
        assert context["serving_counts"]["metric_dimension_lookup"] > 0
        assert context["serving_counts"]["company_dimension_catalog"] > 0
        assert context["capabilities"]["metric_dimension_lookup"] is True
        assert context["capabilities"]["company_dimension_catalog"] is True

        iphone_results, iphone_diagnostics = store.query_compact_with_diagnostics(
            topic="iPhone net sales",
            tickers=["AAPL"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert iphone_results[0]["id"].endswith("iphone-net-sales")
        assert iphone_results[0]["object"]["dimensions"]["product"] == "iPhone"
        assert iphone_diagnostics["search_strategy"]["dimension_anchors"] == ["iphone"]
        assert iphone_diagnostics["search_strategy"]["company_total_role"] == "denominator_or_support"

        services_results, _services_diagnostics = store.query_compact_with_diagnostics(
            topic="Services net sales",
            tickers=["AAPL"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert services_results[0]["id"].endswith("services-net-sales")
        assert services_results[0]["object"]["dimensions"]["segment"] == "Services"

        geography_results, _geography_diagnostics = store.query_compact_with_diagnostics(
            topic="Greater China net sales",
            tickers=["AAPL"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert geography_results[0]["id"].endswith("greater-china-net-sales")
        assert geography_results[0]["object"]["dimensions"]["geography"] == "Greater China"

        mac_results, mac_diagnostics = store.query_compact_with_diagnostics(
            topic="Mac net sales",
            tickers=["AAPL"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert mac_results[0]["id"].endswith("mac-revenue")
        assert mac_results[0]["type"] == "XBRLFact"
        assert mac_diagnostics["search_strategy"]["mode"] == "metric_dimension_lookup"
        assert mac_diagnostics["search_strategy"]["dimension_anchors"] == ["mac"]
        assert mac_diagnostics["search_strategy"]["metric_roles_by_object_id"][mac_results[0]["id"]] == "target_dimension_metric"

        service_xbrl_results, service_xbrl_diagnostics = store.query_compact_with_diagnostics(
            topic="Services revenue",
            tickers=["SVCX"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert service_xbrl_results[0]["id"].endswith("service-revenue")
        assert service_xbrl_results[0]["type"] == "XBRLFact"
        assert service_xbrl_diagnostics["search_strategy"]["dimension_anchors"] == ["services"]

        missing_results, missing_diagnostics = store.query_compact_with_diagnostics(
            topic="Data Center net sales",
            tickers=["AAPL"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert missing_results == []
        assert missing_diagnostics["search_strategy"]["dimension_metric_not_found"] is True
        assert missing_diagnostics["search_strategy"]["fallback_skipped"] == "dimension_metric_not_found"

        compound_results, compound_diagnostics = store.query_compact_with_diagnostics(
            topic="iPhone Services net sales",
            tickers=["AAPL"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=5,
        )
        compound_ids = {result["id"].rsplit(":", 1)[-1] for result in compound_results}
        assert {"iphone-net-sales", "services-net-sales"}.issubset(compound_ids)
        assert "total-revenue" not in compound_ids
        assert compound_diagnostics["search_strategy"]["dimension_anchors"] == ["iphone", "services"]

        data_center_results, data_center_diagnostics = store.query_compact_with_diagnostics(
            topic="Data Center revenue",
            tickers=["NVDA"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert data_center_results[0]["id"].endswith("data-center-revenue")
        assert data_center_results[0]["object"]["dimensions"]["segment"] == "Data Center"
        assert data_center_diagnostics["search_strategy"]["company_total_role"] == "denominator_or_support"

        aws_results, aws_diagnostics = store.query_compact_with_diagnostics(
            topic="AWS operating income",
            tickers=["AMZN"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert aws_results[0]["id"].endswith("aws-operating-income")
        assert aws_results[0]["object"]["dimensions"]["segment"] == "AWS"
        assert aws_diagnostics["search_strategy"]["dimension_anchors"] == ["aws"]

        intelligent_cloud_results, _intelligent_cloud_diagnostics = store.query_compact_with_diagnostics(
            topic="Intelligent Cloud revenue",
            tickers=["MSFT"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert intelligent_cloud_results[0]["id"].endswith("intelligent-cloud-revenue")
        intelligent_cloud_lookup = store.conn.execute(
            "SELECT segment_name, dimensions_json FROM metric_lookup WHERE object_id = ?",
            (intelligent_cloud_results[0]["id"],),
        ).fetchone()
        assert intelligent_cloud_lookup["segment_name"] == "Intelligent Cloud"
        assert json.loads(intelligent_cloud_lookup["dimensions_json"])["Business Segment"] == "Intelligent Cloud"

        google_cloud_results, _google_cloud_diagnostics = store.query_compact_with_diagnostics(
            topic="Google Cloud revenue",
            tickers=["GOOGL"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=3,
        )
        assert google_cloud_results[0]["id"].endswith("google-cloud-revenue")
        google_cloud_lookup = store.conn.execute(
            "SELECT segment_name, dimensions_json FROM metric_lookup WHERE object_id = ?",
            (google_cloud_results[0]["id"],),
        ).fetchone()
        assert google_cloud_lookup["segment_name"] is None
        assert json.loads(google_cloud_lookup["dimensions_json"])["inferred_unknown"] == "Google Cloud"

        catalog_rows = store.conn.execute(
            """
            SELECT ticker, dimension_key, dimension_label, dimension_kind
            FROM company_dimension_catalog
            WHERE ticker IN ('AAPL', 'NVDA', 'AMZN', 'MSFT', 'GOOGL', 'SVCX')
            ORDER BY ticker, dimension_key
            """
        ).fetchall()
        catalog_keys = {(row["ticker"], row["dimension_key"]) for row in catalog_rows}
        assert ("AAPL", "i_phone") in catalog_keys
        assert ("AAPL", "mac") in catalog_keys
        assert ("AAPL", "services") in catalog_keys
        assert ("AAPL", "greater_china") in catalog_keys
        assert ("NVDA", "data_center") in catalog_keys
        assert ("AMZN", "aws") in catalog_keys
        assert ("MSFT", "intelligent_cloud") in catalog_keys
        assert ("GOOGL", "google_cloud") in catalog_keys
        assert ("SVCX", "service") in catalog_keys

        iphone_catalog = store.conn.execute(
            """
            SELECT dimension_label, aliases_text
            FROM company_dimension_catalog
            WHERE ticker = 'AAPL' AND dimension_key = 'i_phone'
            """
        ).fetchone()
        assert iphone_catalog["dimension_label"] == "i Phone"
        assert "iphone" in iphone_catalog["aliases_text"].lower().split()

        service_catalog = store.conn.execute(
            """
            SELECT dimension_label, aliases_text
            FROM company_dimension_catalog
            WHERE ticker = 'SVCX' AND dimension_key = 'service'
            """
        ).fetchone()
        assert service_catalog["dimension_label"] == "Service"
        assert "services" in service_catalog["aliases_text"].lower().split()

        dimension_rows = store.conn.execute(
            """
            SELECT object_id, dimension_key, dimension_label, dimension_kind
            FROM metric_dimension_lookup
            WHERE dimension_key IN ('i_phone', 'mac', 'services', 'data_center', 'aws', 'intelligent_cloud', 'google_cloud')
            """
        ).fetchall()
        assert {row["dimension_key"] for row in dimension_rows} >= {
            "i_phone",
            "mac",
            "services",
            "data_center",
            "aws",
            "intelligent_cloud",
            "google_cloud",
        }

        share_results, share_diagnostics = store.query_compact_with_diagnostics(
            topic="iPhone Services net sales share",
            tickers=["AAPL"],
            document_types=["10-K"],
            periods=["CY2025"],
            object_types=["MetricObservation"],
            limit=10,
        )
        share_ids = {result["id"].rsplit(":", 1)[-1] for result in share_results}
        assert {"iphone-net-sales", "services-net-sales", "total-revenue"}.issubset(share_ids)
        share_strategy = share_diagnostics["search_strategy"]
        assert share_strategy["mode"] == "metric_dimension_lookup"
        assert share_strategy["denominator_needed"] is True
        assert share_strategy["metric_roles_by_object_id"]["metric_observation:AAPL:CY2025:10K:total-revenue"] == "denominator_metric"
        assert share_strategy["metric_roles_by_object_id"]["metric_observation:AAPL:CY2025:10K:iphone-net-sales"] == "target_dimension_metric"


def test_metric_dimension_normalization_uses_generic_rules_not_value_special_cases() -> None:
    assert agent_index_builder._metric_lookup_clean_dimension_label("aapl:IPhoneMember") == "I Phone"
    assert agent_index_builder._metric_lookup_clean_dimension_label("us-gaap:ServiceMember") == "Service"
    assert agent_index_builder._metric_dimension_key("aapl:IPhoneMember") == "i_phone"
    assert agent_index_builder._metric_dimension_key("us-gaap:ServiceMember") == "service"
    assert agent_index_builder._metric_dimension_kind("aapl:IPhoneMember", "I Phone") == "unknown"
    assert agent_index_builder._metric_dimension_kind("ProductOrServiceAxis", "IPhone") == "product"
    assert agent_index_builder._metric_dimension_kind("StatementGeographicalAxis", "Greater China") == "geography"
    aliases = agent_index_builder._metric_dimension_aliases("Service", "service")
    assert "services" in {alias.lower() for alias in aliases}


def test_query_ticker_summary_discovery_contract(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)

    payload = json.loads(
        query_tool(
            topic="commodity volatility natural gas price revenue exposure",
            object_types=["ExternalFactorExposure", "ResearchClaim", "EvidenceQuote", "SupportLink"],
            response_detail="ticker_summary",
            group_by="ticker",
            limit=20,
            limit_groups=5,
            limit_per_group=2,
            answer_candidate_only=True,
        )
    )

    assert payload["response_detail"] == "ticker_summary"
    assert payload["query"]["group_by"] == "ticker"
    assert payload["query"]["answer_candidate_only"] is True
    assert payload["query_frame"]["core_domain_terms"]
    assert payload["ticker_candidates"]
    assert payload["ticker_candidates"][0]["ticker"] == "VG"
    assert payload["ticker_candidates"][0]["tier"] == "traceable_direct"
    assert payload["ticker_candidates"][0]["trace_status"] == "traceable"
    assert payload["ticker_candidates"][0]["trace_counts"]["evidence_chains"] > 0
    assert payload["directness_guard"]["strong_claim_allowed"] is True
    assert payload["directness_guard"]["strong_claim_requires"] == ["traceable_direct", "traceable_metric_lineage"]
    assert payload["ticker_candidates"][0]["top_object_ids"]
    assert payload["ticker_candidates"][0]["matched_topics"]
    reason = payload["ticker_candidates"][0]["top_reasons"][0]
    assert isinstance(reason, dict)
    assert reason["semantic_relevance"] == "direct"
    assert reason["trace_status"] == "traceable"
    assert reason["matched_core_terms"]
    assert "missing_required_facets" in reason
    assert "SupportLink" not in payload["ticker_candidates"][0]["matched_object_counts"]
    assert "VG" in payload["results_by_ticker"]
    assert len(payload["results_by_ticker"]["VG"]) <= 2


def test_query_compact_exposes_directness_guard_for_direct_question(tmp_path: Path, monkeypatch) -> None:
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    payload = json.loads(
        query_tool(
            topic="VG GPU HBM direct exposure",
            tickers=["VG"],
            object_types=["ExternalFactorExposure"],
            response_detail="compact",
            limit=5,
        )
    )
    markdown = query_tool(
        topic="VG GPU HBM direct exposure",
        tickers=["VG"],
        object_types=["ExternalFactorExposure"],
        response_detail="compact",
        response_format=ResponseFormat.MARKDOWN,
        limit=5,
    )

    assert payload["directness_guard"]["requires_direct_match"] is True
    assert payload["directness_guard"]["strong_claim_allowed"] is False
    assert payload["directness_guard"]["strong_claim_requires"] == ["traceable_direct", "traceable_metric_lineage"]
    assert "Strong claim allowed: False" in markdown


def test_query_ids_only_response_detail_contract(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)

    payload = json.loads(
        query_tool(
            topic="revenue growth",
            response_detail="ids_only",
            limit=3,
        )
    )

    assert payload["response_detail"] == "ids_only"
    assert payload["results"]
    assert set(payload["results"][0]) == {"id", "type", "ticker", "document_id"}


def test_retrieve_ticker_summary_discovery_contract(tmp_path: Path) -> None:
    from krw_ontology.mcp_server.tools import retrieve_tool

    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)

    payload = json.loads(
        retrieve_tool(
            question="commodity volatility and natural gas price exposure",
            response_detail="ticker_summary",
            group_by="ticker",
            limit=20,
            limit_groups=5,
            limit_per_group=2,
            answer_candidate_only=True,
        )
    )

    assert payload["response_detail"] == "ticker_summary"
    assert payload["query"]["group_by"] == "ticker"
    assert payload["query"]["answer_candidate_only"] is True
    assert payload["query_frame"]["core_domain_terms"]
    assert payload["ticker_candidates"]
    assert payload["ticker_candidates"][0]["ticker"] == "VG"
    assert payload["ticker_candidates"][0]["tier"] in {
        "traceable_direct",
        "untraced_direct_candidate",
        "traceable_related",
        "untraced_related",
    }
    assert payload["ticker_candidates"][0]["top_reasons"][0]["matched_core_terms"]
    assert len(payload["results_by_ticker"]["VG"]) <= 2


def test_retrieve_ticker_summary_exposes_directness_guard_for_direct_question(tmp_path: Path) -> None:
    from krw_ontology.mcp_server.tools import retrieve_tool

    _write_fixture(tmp_path)
    _build_v3_runtime(tmp_path)

    payload = json.loads(
        retrieve_tool(
            question="VG는 GPU HBM 가격 변동에 직접 노출되어 있나?",
            response_detail="ticker_summary",
            group_by="ticker",
            limit=20,
            limit_groups=5,
            limit_per_group=2,
        )
    )

    assert payload["directness_guard"]["requires_direct_match"] is True
    assert payload["directness_guard"]["strong_claim_allowed"] is False
    assert payload["directness_guard"]["strong_claim_requires"] == ["traceable_direct", "traceable_metric_lineage"]


def test_ticker_summary_demotes_untraced_direct_candidate(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    exposure_path = (
        tmp_path
        / "companies"
        / "VG"
        / "ontology"
        / "10K"
        / "FY2025"
        / "external_factor_exposures.jsonl"
    )
    exposures = json.loads(f"[{exposure_path.read_text().strip().replace(chr(10), ',')}]")
    exposures[0]["supported_by_claims"] = []
    exposures[0]["supported_by_quotes"] = []
    write_jsonl(exposure_path, exposures)
    _build_v3_runtime(tmp_path)

    payload = json.loads(
        query_tool(
            topic="feed gas costs Henry Hub operating margin",
            response_detail="ticker_summary",
            group_by="ticker",
            tickers=["VG"],
            limit=20,
            limit_groups=5,
            limit_per_group=2,
        )
    )

    candidate = payload["ticker_candidates"][0]
    assert candidate["tier"] == "untraced_direct_candidate"
    assert candidate["top_reasons"][0]["semantic_relevance"] == "direct"
    assert candidate["top_reasons"][0]["trace_status"] == "orphan"
    assert candidate["untraced_object_ids"]


def test_typed_projection_lookup_tables_and_query_routing(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    index = build_agent_index(tmp_path)

    with OntologyStore(index["index_path"]) as store:
        context = store.index_context()
        serving_counts = context["serving_counts"]
        assert serving_counts["exposure_lookup"] == 1
        assert serving_counts["agreement_lookup"] == 1
        assert serving_counts["event_lookup"] >= 1
        assert serving_counts["factor_lookup"] >= 2
        assert context["capabilities"]["typed_projection_lookup"] is True

        agreement_results, agreement_diagnostics = store.query_compact_with_diagnostics(
            topic="SPA termination debt acceleration covenant",
            tickers=["VG"],
            object_types=["AgreementTerm"],
            limit=5,
        )
        assert agreement_results
        assert agreement_results[0]["type"] == "AgreementTerm"
        assert agreement_diagnostics["typed_projection_fast_path"] is True
        assert agreement_diagnostics["projection"]["projection_used"] == "agreement_lookup"
        assert agreement_diagnostics["projection"]["fallback_used"] is False

        exposure_results, exposure_diagnostics = store.query_compact_with_diagnostics(
            topic="Henry Hub natural gas price exposure operating margin",
            tickers=["VG"],
            object_types=["ExternalFactorExposure"],
            limit=5,
        )
        assert exposure_results
        assert exposure_results[0]["type"] == "ExternalFactorExposure"
        assert exposure_diagnostics["projection"]["projection_used"] == "exposure_lookup"

        broad_factor_profile = store._typed_projection_profile(
            topic="risk",
            object_types=["BusinessFactor"],
            explicit_object_types=False,
        )
        assert broad_factor_profile["enabled"] is False
        routed_factor_profile = store._typed_projection_profile(
            topic="margin pressure risk",
            object_types=["BusinessFactor"],
            explicit_object_types=False,
        )
        assert routed_factor_profile["enabled"] is True
        assert routed_factor_profile["table"] == "factor_lookup"
        explicit_factor_profile = store._typed_projection_profile(
            topic="risk",
            object_types=["BusinessFactor"],
            explicit_object_types=True,
        )
        assert explicit_factor_profile["enabled"] is True
        assert explicit_factor_profile["table"] == "factor_lookup"

        event_context = store.query_context(
            question="VG regulatory approval delay timeline",
            ticker="VG",
            limit_results=5,
            limit_tickers=3,
        )
        typed_projection = event_context["search_diagnostics"].get("typed_projection")
        assert typed_projection is not None
        assert typed_projection["projection_used"] == "event_lookup"
        assert event_context["research_status"] == "sufficient_but_trace_recommended"

        agreement_context = store.query_context(
            question="VG SPA termination covenant",
            ticker="VG",
            limit_results=5,
            limit_tickers=3,
        )
        agreement_projection = agreement_context["search_diagnostics"].get("typed_projection")
        assert agreement_projection is not None
        assert agreement_projection["projection_used"] == "agreement_lookup"
        assert agreement_context["search_diagnostics"]["discovery_skipped"] is True
        assert agreement_context["research_status"] == "sufficient_but_trace_recommended"
