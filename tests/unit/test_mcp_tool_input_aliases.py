"""Focused MCP input-normalization tests for agent/tool argument drift."""

from __future__ import annotations

import json
from pathlib import Path

from krw_ontology.agent_index import build_agent_index
from krw_ontology.mcp_server.server import mcp
from krw_ontology.mcp_server.tools import compare_tool, query_tool, retrieve_tool
from tests.unit.test_mcp_server import _write_fixture


def test_mcp_public_schema_exposes_aliases_without_required_extra_args() -> None:
    schemas = {
        tool.name: (
            getattr(tool, "parameters", None)
            or getattr(tool, "inputSchema", None)
            or getattr(tool, "input_schema", None)
        )
        for tool in mcp._tool_manager.list_tools()
    }

    query_schema = schemas["krw_ontology_query"]
    query_props = query_schema["properties"]
    assert {"ticker", "document_type", "period", "object_type"} <= set(query_props)
    assert "extra_args" not in query_props
    assert "extra_args" not in (query_schema.get("required") or [])

    retrieve_schema = schemas["krw_ontology_retrieve"]
    retrieve_props = retrieve_schema["properties"]
    assert "ticker" in retrieve_props
    assert "extra_args" not in retrieve_props
    assert retrieve_schema.get("required") == ["question"]

    compare_schema = schemas["krw_ontology_compare"]
    compare_props = compare_schema["properties"]
    assert {"ticker", "ticker_a", "ticker_b", "tickers"} <= set(compare_props)
    assert "extra_args" not in compare_props
    assert "extra_args" not in (compare_schema.get("required") or [])


def test_query_tool_accepts_scalar_aliases_and_records_unknown_args(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)

    payload = json.loads(
        query_tool(
            root=str(tmp_path),
            topic="revenue growth",
            ticker="VG",
            document_type="10-K",
            period="FY2025",
            object_type="claim",
            limit=5,
            agent_sent_wrong_field="kept-for-debug",
        )
    )

    assert payload["query"]["tickers"] == ["VG"]
    assert payload["query"]["ticker_alias"] == "VG"
    assert payload["query"]["document_types"] == ["10-K"]
    assert payload["query"]["document_type_alias"] == "10-K"
    assert payload["query"]["periods"] == ["FY2025"]
    assert payload["query"]["period_alias"] == "FY2025"
    assert payload["query"]["object_types"] == ["ResearchClaim"]
    assert payload["query"]["object_type_alias"] == "claim"
    assert payload["query"]["object_types_requested"] == ["claim"]
    assert payload["kernel"]["version"] == "research_kernel_v0.1"
    assert payload["kernel"]["status"] == "sufficient_for_default_answer"
    assert payload["results"]
    assert {result["ticker"] for result in payload["results"]} == {"VG"}
    assert payload["input_warnings"] == [
        {
            "code": "ignored_extra_args",
            "message": "Unsupported extra MCP arguments were ignored.",
            "args": ["agent_sent_wrong_field"],
        }
    ]
    assert "ignored_extra_args" in payload["search_diagnostics"]["warnings"]


def test_query_tool_scalar_aliases_do_not_force_tickers_when_absent(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)

    payload = json.loads(
        query_tool(
            root=str(tmp_path),
            topic="revenue growth",
            document_type="10-K",
            period="FY2025",
            object_type="claim",
            limit=5,
        )
    )

    assert payload["query"]["tickers"] == []
    assert payload["query"]["document_types"] == ["10-K"]
    assert payload["query"]["periods"] == ["FY2025"]
    assert payload["query"]["object_types"] == ["ResearchClaim"]
    assert payload["kernel"]["answer_mode"] == "targeted_search_results"
    assert payload["results"]
    assert {result["ticker"] for result in payload["results"]} == {"VG"}
    assert "input_warnings" not in payload


def test_retrieve_tool_accepts_ticker_alias_and_records_unknown_args(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)

    payload = json.loads(
        retrieve_tool(
            root=str(tmp_path),
            question="VG revenue growth evidence",
            ticker="VG",
            limit=3,
            unused_model_arg=True,
        )
    )

    assert payload["query"]["tickers"] == ["VG"]
    assert payload["query"]["ticker_alias"] == "VG"
    assert payload["input_warnings"][0]["code"] == "ignored_extra_args"
    assert payload["input_warnings"][0]["args"] == ["unused_model_arg"]


def test_compare_tool_accepts_pair_aliases_and_records_unknown_args(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)

    payload = json.loads(
        compare_tool(
            root=str(tmp_path),
            ticker_a="VG",
            ticker_b="XOM",
            topic="revenue growth",
            limit_per_ticker=2,
            random_agent_arg="ignored",
        )
    )

    assert payload["query"]["tickers"] == ["VG", "XOM"]
    assert payload["query"]["ticker_a_alias"] == "VG"
    assert payload["query"]["ticker_b_alias"] == "XOM"
    assert payload["kernel"]["intent"] == "comparison"
    assert payload["kernel"]["primary_context"] == "compare_context"
    assert payload["input_warnings"][0]["code"] == "ignored_extra_args"
    assert payload["input_warnings"][0]["args"] == ["random_agent_arg"]
    assert {row["comparison_key"] for row in payload["comparison_rows"]} == {"VG", "XOM"}


def test_compare_tool_accepts_single_ticker_alias_for_period_compare(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)

    payload = json.loads(
        compare_tool(
            root=str(tmp_path),
            ticker="VG",
            periods=["FY2024", "FY2025"],
            topic="revenue growth",
            limit_per_ticker=1,
        )
    )

    assert payload["query"]["tickers"] == ["VG"]
    assert payload["query"]["ticker_alias"] == "VG"
    assert payload["mode"] == "period_topic"
    assert payload["kernel"]["intent"] == "comparison"
    assert {row["comparison_key"] for row in payload["comparison_rows"]} == {"FY2024", "FY2025"}


def test_full_response_detail_is_globally_downgraded(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    build_agent_index(tmp_path)

    query_payload = json.loads(
        query_tool(
            root=str(tmp_path),
            topic="revenue growth",
            ticker="VG",
            response_detail="full",
            limit=3,
        )
    )
    retrieve_payload = json.loads(
        retrieve_tool(
            root=str(tmp_path),
            question="VG revenue growth evidence",
            ticker="VG",
            response_detail="full",
            limit=3,
        )
    )
    compare_payload = json.loads(
        compare_tool(
            root=str(tmp_path),
            ticker="VG",
            periods=["FY2024", "FY2025"],
            topic="revenue growth",
            response_detail="full",
            limit_per_ticker=1,
        )
    )

    for payload in (query_payload, retrieve_payload, compare_payload):
        assert payload["response_detail"] == "compact"
        assert payload["response_detail_policy"] == {
            "requested": "full",
            "effective": "compact",
            "action": "downgraded_full_disabled",
        }
