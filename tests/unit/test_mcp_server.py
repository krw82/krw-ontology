"""Tests for the krw-ontology MCP tool layer."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from krw_ontology.agent_index import builder as agent_index_builder
from krw_ontology.agent_index import store as agent_index_store
from krw_ontology.agent_index import OntologyStore, build_agent_index
from krw_ontology.mcp_server.http_server import prepare_mcp_runtime
from krw_ontology.mcp_server import tools as mcp_tools
from krw_ontology.mcp_server.server import diagnostics_payload, health_payload, mcp
from krw_ontology.mcp_server.tools import (
    _normalize_object_types,
    catalog_tool,
    chain_tool,
    compare_tool,
    index_context_tool,
    plan_query_tool,
    quality_tool,
    query_context_tool,
    query_tool,
    retrieve_tool,
    trace_tool,
    topic_map_tool,
    ResponseFormat,
)
from krw_ontology.release import write_release_manifest
from krw_ontology.utils.io import atomic_write_json, write_jsonl


def test_mcp_tools_query_trace_quality_and_compare(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    index = build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    catalog = json.loads(catalog_tool())
    assert catalog["index_path"] == str(index["index_path"])
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

    chain = json.loads(chain_tool(object_id="business_factor:VG:FY2025:10K:revenue-growth"))
    assert chain["object"]["type"] == "BusinessFactor"
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


def test_mcp_health_reports_release_manifest(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    write_release_manifest(tmp_path, release_id="20260528_010000", env="prod")
    monkeypatch.setenv("KRW_ONTOLOGY_ENV", "prod")
    monkeypatch.setenv("KRW_ONTOLOGY_RELEASE_ROOT", str(tmp_path))
    monkeypatch.delenv("KRW_ONTOLOGY_INDEX_PATH", raising=False)

    payload, status_code = health_payload()

    assert status_code == 200
    assert payload["ok"] is True
    assert payload["env"] == "prod"
    assert payload["release_id"] == "20260528_010000"
    assert payload["manifest_valid"] is True
    assert payload["manifest_path"] == str(tmp_path / "manifest.json")
    assert payload["documents"] == 2


def test_mcp_prepare_runtime_requires_prod_current_symlink(tmp_path: Path):
    release = tmp_path / "releases" / "prod" / "20260529_010000"
    release.mkdir(parents=True)
    _write_fixture(release)
    build_agent_index(release)
    write_release_manifest(release, release_id="20260529_010000", env="prod")
    current = release.parent / "current"
    current.symlink_to(release.name)
    env_names = (
        "KRW_ONTOLOGY_ENV",
        "KRW_ONTOLOGY_RELEASE_ROOT",
        "KRW_ONTOLOGY_ROOT",
        "KRW_ONTOLOGY_MANIFEST_PATH",
        "KRW_ONTOLOGY_INDEX_PATH",
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
    assert verification["release_id"] == "20260529_010000"
    assert verification["current_symlink"] is True
    assert status["store"]["mode"] == "persistent"
    assert status["store"]["stores"] == 1
    assert status["store"]["idle"] == 1


def test_mcp_prepare_runtime_rejects_non_symlink_prod_root(tmp_path: Path):
    release = tmp_path / "releases" / "prod" / "20260529_010000"
    release.mkdir(parents=True)
    _write_fixture(release)
    build_agent_index(release)
    write_release_manifest(release, release_id="20260529_010000", env="prod")

    with pytest.raises(RuntimeError, match="current_symlink_required"):
        prepare_mcp_runtime(root=release, env="prod")


def test_mcp_persistent_store_reuses_sqlite_connection(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    index = build_agent_index(tmp_path)
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


def test_mcp_query_normalizes_object_type_aliases(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    topic_map = json.loads(topic_map_tool(ticker="VG", limit=10))

    assert topic_map["ticker"] == "VG"
    assert topic_map["source"]["company_business_profile_ids"] == [
        "company_business_profile:VG:ALL"
    ]
    assert topic_map["source"]["fallback_used"] is False
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


def test_mcp_chain_returns_object_specific_chains(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
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


def test_mcp_chain_respects_depth_and_quote_text_option(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))

    trace = json.loads(trace_tool(object_id="claim:VG:FY2025:10K:revenue"))

    assert trace["resolved_from_prefix"] == "claim:VG:FY2025:10K:revenue"
    assert trace["object"]["id"] == "claim:VG:FY2025:10K:revenue-growth"


def test_mcp_trace_returns_ambiguous_prefix_candidates(tmp_path: Path, monkeypatch):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
            "index_context is an operational/debug capability card. "
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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


def test_mcp_query_context_stops_out_of_scope_valuation(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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


def test_mcp_query_context_risk_thesis_router_skips_metric_deep_path(
    tmp_path: Path,
    monkeypatch,
):
    _write_metric_dimension_fixture(tmp_path)
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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


def test_mcp_retrieve_adds_soft_guidance_for_repeated_retrieve_context(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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


def test_mcp_retrieve_skips_legacy_when_research_context_is_sufficient(
    tmp_path: Path,
    monkeypatch,
):
    _write_metric_dimension_fixture(tmp_path)
    build_agent_index(tmp_path)
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
    assert payload["legacy_retrieve_skipped"] is True
    assert payload["research_pack"]["metric_series_pack"]["mode"] == "metric_dimension_lookup"
    assert payload["direct_evidence"] == []
    assert payload["related_context"] == []
    assert "krw_ontology_retrieve" in payload["do_not_call"]


def test_mcp_retrieve_exposes_directness_guard_for_direct_question(
    tmp_path: Path,
    monkeypatch,
):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)
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
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    write_release_manifest(tmp_path, release_id="20260528_020000", env="prod")

    def fail_connect(*_args, **_kwargs):
        raise AssertionError("health_payload must not open SQLite")

    monkeypatch.setattr("krw_ontology.mcp_server.server.sqlite3.connect", fail_connect)

    payload, status_code = health_payload(root=str(tmp_path))

    assert status_code == 200
    assert payload["ok"] is True
    assert payload["root"] == str(tmp_path.resolve())
    assert payload["documents"] == 2
    assert payload["objects"] >= 1
    assert payload["sqlite_checked"] is False
    assert "krw_ontology_topic_map" in payload["tools"]


def test_mcp_diagnostics_payload_reports_live_index_counts(tmp_path: Path):
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)
    write_release_manifest(tmp_path, release_id="20260528_020000", env="prod")

    payload, status_code = diagnostics_payload(root=str(tmp_path))

    assert status_code == 200
    assert payload["ok"] is True
    assert payload["documents"] == 2
    assert payload["objects"] >= 1
    assert payload["sqlite_checked"] is True


def test_mcp_health_payload_reports_missing_index(tmp_path: Path):
    payload, status_code = health_payload(root=str(tmp_path))

    assert status_code == 503
    assert payload["ok"] is False
    assert payload["error"] == "agent_index_not_found"


def _write_fixture(
    root: Path,
    *,
    period: str = "FY2025",
    text: str = "Revenue growth accelerated because customer demand increased for LNG volumes.",
) -> None:
    ontology_dir = root / "companies" / "VG" / "ontology" / "10K" / period
    sources_dir = root / "companies" / "VG" / "sources" / "10K" / period
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    source_document_id = f"source:VG:{period}:10K"
    span_id = f"span:VG:{period}:10K:0001"
    quote_id = f"quote:VG:{period}:10K:0001"
    claim_id = f"claim:VG:{period}:10K:revenue-growth"
    risk_claim_id = f"claim:VG:{period}:10K:regulatory-risk"
    driver_id = f"business_factor:VG:{period}:10K:revenue-growth"
    risk_id = f"business_factor:VG:{period}:10K:regulatory-risk"
    unsupported_risk_id = f"business_factor:VG:{period}:10K:unsupported-risk"
    activity_id = f"business_activity:VG:{period}:10K:lng-sales"
    exposure_id = f"external_factor_exposure:VG:{period}:10K:natural-gas-price-operating-margin"
    agreement_id = f"agreement:VG:{period}:10K:spa-termination"
    span = {
        "id": span_id,
        "type": "SourceSpan",
        "ticker": "VG",
        "source_document_id": source_document_id,
        "document_type": "10-K",
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
        "document_type": "10-K",
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
        "document_type": "10-K",
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
        "document_type": "10-K",
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
        "document_type": "10-K",
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
        "document_type": "10-K",
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
        "document_type": "10-K",
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
        "document_type": "10-K",
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
        "document_type": "10-K",
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
        "document_type": "10-K",
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
            "document_type": "10-K",
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
            "document_type": "10-K",
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
            "document_type": "10-K",
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
            "document_type": "10-K",
            "doc_type_key": "10K",
            "period": period,
            "files": {
                "spans": f"companies/VG/ontology/10K/{period}/spans.jsonl",
                "evidence_quotes": f"companies/VG/ontology/10K/{period}/evidence_quotes.jsonl",
                "claims": f"companies/VG/ontology/10K/{period}/claims.jsonl",
                "business_factors": f"companies/VG/ontology/10K/{period}/business_factors.jsonl",
                "business_activities": f"companies/VG/ontology/10K/{period}/business_activities.jsonl",
                "external_factor_exposures": f"companies/VG/ontology/10K/{period}/external_factor_exposures.jsonl",
                "agreement_terms": f"companies/VG/ontology/10K/{period}/agreement_terms.jsonl",
                "edges": f"companies/VG/ontology/10K/{period}/edges.jsonl",
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
    build_agent_index(tmp_path)

    payload = json.loads(
        query_tool(
            root=str(tmp_path),
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
    build_agent_index(tmp_path)
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
    build_agent_index(tmp_path)

    payload = json.loads(
        query_tool(
            root=str(tmp_path),
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
    build_agent_index(tmp_path)

    payload = json.loads(
        retrieve_tool(
            root=str(tmp_path),
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
    build_agent_index(tmp_path)

    payload = json.loads(
        retrieve_tool(
            root=str(tmp_path),
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
    build_agent_index(tmp_path)

    payload = json.loads(
        query_tool(
            root=str(tmp_path),
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
