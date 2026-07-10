"""Integration tests for SearchPlan -> sidecar -> shard -> ResearchState v2."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from krw_ontology.mcp_server import tools as mcp_tools
from krw_ontology.mcp_server.contracts import QueryClause, SearchPlan
from krw_ontology.mcp_server.tools import query_context_tool
from tests.unit.test_mcp_server import _build_v3_runtime, _write_fixture


@pytest.fixture(autouse=True)
def _isolate_mcp_runtime_env() -> Iterator[None]:
    """Keep the shared v3 test builder from leaking process-wide runtime paths."""
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
        yield
    finally:
        mcp_tools.reset_mcp_runtime_caches()
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_tickerless_search_plan_routes_through_sidecar_without_spine_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logging.disable(logging.CRITICAL)
    try:
        _write_fixture(tmp_path)
        _build_v3_runtime(tmp_path)
    finally:
        logging.disable(logging.NOTSET)
    monkeypatch.setenv("KRW_ONTOLOGY_ROOT", str(tmp_path))
    mcp_tools.reset_mcp_runtime_caches()

    state = query_context_tool(
        search_plan=SearchPlan(
            question="Which covered company reports demand-backed revenue growth?",
            intent="company_discovery",
            clauses=[
                QueryClause(
                    clause_id="revenue_growth",
                    retrieval_query="revenue growth driven by customer demand",
                    required_concepts=["revenue growth", "customer demand"],
                    required_predicates=["driven by"],
                )
            ],
            limit_results=8,
            limit_tickers=10,
        )
    )

    assert "VG" in state.resolved_scope.resolved_tickers
    assert state.evidence_units
    assert {unit.ticker for unit in state.evidence_units} == {"VG"}
    assert all(unit.supports_clause_ids == ["revenue_growth"] for unit in state.evidence_units)
    assert "router_sidecar_unavailable" not in state.warnings
    assert "planned_ticker_candidates_not_found" not in state.warnings

    mcp_tools.reset_mcp_runtime_caches()
