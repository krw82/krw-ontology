"""Tests for the agent-facing ontology SQLite index and SDK."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

import krw_ontology.agent_index.builder as agent_index_builder
from krw_ontology.agent_index import (
    AgentRetriever,
    ClaudeAgentQueryPlanner,
    ClaudeAgentReranker,
    OntologyStore,
    QueryPlan,
    build_agent_index,
)
from krw_ontology.cli.main import app
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.utils.io import atomic_write_json, write_jsonl

runner = CliRunner()


def test_build_agent_index_and_query_trace_quality(tmp_path: Path):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q3",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Margin pressure increased because customers demanded lower prices.",
        metric_name="capex",
        metric_value=125.0,
    )
    _write_document_fixture(
        tmp_path,
        ticker="XOM",
        document_type="10-K",
        doc_type_key="10K",
        period="FY2025",
        section_quality={
            "status": "warn",
            "missing_core_sections": ["item7a"],
            "fail_reasons": [],
        },
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
        include_rejected=True,
    )

    result = build_agent_index(tmp_path)

    assert result["totals"]["documents"] == 2
    assert result["totals"]["edges"] == 4
    assert result["totals"]["quality_events"] == 2

    with OntologyStore(result["index_path"]) as store:
        assert store.list_companies() == ["VG", "XOM"]

        docs = store.list_documents(ticker="VG")
        assert docs[0]["document_type"] == "10-Q"
        assert docs[0]["section_quality_status"] == "pass"

        bundles = store.query(topic="margin pressure", tickers=["VG"])
        assert len(bundles) >= 2
        claim_bundle = next(bundle for bundle in bundles if bundle["type"] == "ResearchClaim")
        assert claim_bundle["ticker"] == "VG"
        assert claim_bundle["quality"]["object_status"] == "accepted"
        assert claim_bundle["evidence"]["quotes"][0]["type"] == "EvidenceQuote"
        assert claim_bundle["evidence"]["spans"][0]["type"] == "SourceSpan"
        assert any(
            related["type"] == "BusinessActivity"
            for related in claim_bundle["evidence"]["related_objects"]
        )

        trace = store.trace("claim:VG:FY2025Q3:10Q:margin-pressure")
        assert trace is not None
        assert trace["object"]["type"] == "ResearchClaim"
        assert trace["evidence"]["quotes"][0]["id"] == "quote:VG:FY2025Q3:10Q:0001"

        rejected = store.query(topic="unsupported claim", tickers=["XOM"])
        assert rejected == []
        rejected_included = store.query(
            topic="unsupported claim",
            tickers=["XOM"],
            include_rejected=True,
        )
        assert rejected_included[0]["quality"]["object_status"] == "rejected"

        quality = store.quality(ticker="XOM")
        assert quality["summary"]["section_warnings"] == 1
        assert quality["summary"]["rejected_objects"] == 1
        xom_bundles = store.query(topic="capital expenditures", tickers=["XOM"], limit=5)
        assert any(
            event["category"] == "section_quality"
            for bundle in xom_bundles
            for event in bundle["quality"]["events"]
        )

        comparison = store.compare(tickers=["VG", "XOM"], metric="capex")
        assert set(comparison["results"]) == {"VG", "XOM"}
        assert comparison["results"]["VG"][0]["object"]["value"] == 125.0
        assert comparison["results"]["XOM"][0]["object"]["value"] == 900.0


def test_build_agent_index_cli(tmp_path: Path):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q3",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Margin pressure increased because customers demanded lower prices.",
        metric_name="capex",
        metric_value=125.0,
    )

    result = runner.invoke(app, ["build-agent-index", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert "Agent index built:" in result.output
    assert "1 documents" in result.output
    assert (tmp_path / "indexes" / "agent_index.sqlite").exists()


def test_build_agent_index_resource_env_and_progress_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q3",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Margin pressure increased because customers demanded lower prices.",
        metric_name="capex",
        metric_value=125.0,
    )
    progress_log = tmp_path / "indexes" / "build_progress.jsonl"
    monkeypatch.setenv("KRW_BUILD_RESOURCE_PROFILE", "max-local")
    monkeypatch.setenv("KRW_SQLITE_SYNCHRONOUS", "OFF")
    monkeypatch.setenv("KRW_SQLITE_CACHE_MIB", "64")
    monkeypatch.setenv("KRW_SQLITE_MMAP_GIB", "1")
    monkeypatch.setenv("KRW_BUILD_BATCH_SIZE", "1234")
    monkeypatch.setenv("KRW_COMPANY_TOPIC_BATCH_SIZE", "234")
    monkeypatch.setenv("KRW_SQLITE_WAL_AUTOCHECKPOINT", "0")
    monkeypatch.setenv("KRW_BUILD_CHECKPOINT_EVERY_ARTIFACTS", "7")
    monkeypatch.setenv("KRW_BUILD_LOG_INTERVAL_SEC", "0")
    monkeypatch.setenv("KRW_BUILD_PROGRESS_LOG", str(progress_log))

    result = build_agent_index(tmp_path)

    settings = result["build_settings"]
    assert settings["resource_profile"] == "max-local"
    assert settings["sqlite_synchronous"] == "OFF"
    assert settings["sqlite_cache_mib"] == 64
    assert settings["sqlite_mmap_gib"] == 1.0
    assert settings["bulk_insert_chunk_size"] == 1234
    assert settings["company_topic_batch_size"] == 234
    assert settings["sqlite_wal_autocheckpoint"] == 0
    assert settings["checkpoint_every_artifacts"] == 7

    with sqlite3.connect(result["index_path"]) as conn:
        raw_build_metadata = conn.execute(
            "SELECT value FROM metadata WHERE key = 'build'"
        ).fetchone()[0]
    build_metadata = json.loads(raw_build_metadata)
    assert build_metadata["build_settings"]["sqlite_synchronous"] == "OFF"
    assert build_metadata["build_settings"]["sqlite_cache_mib"] == 64
    assert build_metadata["build_settings"]["bulk_insert_chunk_size"] == 1234

    log_rows = [json.loads(line) for line in progress_log.read_text().splitlines()]
    assert log_rows
    assert log_rows[0]["phase"] == "start"
    assert any(row["phase"] == "finalize_done" for row in log_rows)
    assert all(row["event"] == "build_phase" for row in log_rows)
    assert log_rows[0]["sqlite_synchronous"] == "OFF"
    assert log_rows[0]["sqlite_cache_mib"] == 64
    assert "db_size_mb" in log_rows[0]
    assert "wal_size_mb" in log_rows[0]


def test_build_resource_settings_default_to_off_and_max_local_profile(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "KRW_BUILD_RESOURCE_PROFILE",
        "KRW_SQLITE_SYNCHRONOUS",
        "KRW_SQLITE_CACHE_MIB",
        "KRW_SQLITE_MMAP_GIB",
        "KRW_BUILD_BATCH_SIZE",
        "KRW_COMPANY_TOPIC_BATCH_SIZE",
        "KRW_SQLITE_WAL_AUTOCHECKPOINT",
        "KRW_BUILD_CHECKPOINT_EVERY_ARTIFACTS",
        "KRW_BUILD_PROGRESS_LOG",
    ):
        monkeypatch.delenv(name, raising=False)

    index_path = Path("/tmp/krw-test/indexes/agent_index.sqlite")
    default_settings = agent_index_builder._build_resource_settings(index_path)
    assert default_settings["resource_profile"] == "max-local"
    assert default_settings["sqlite_synchronous"] == "OFF"
    assert default_settings["sqlite_cache_mib"] == 4096
    assert default_settings["sqlite_mmap_gib"] == 16.0
    assert default_settings["bulk_insert_chunk_size"] == 20_000
    assert default_settings["company_topic_batch_size"] == 2_000
    assert default_settings["sqlite_wal_autocheckpoint"] == 0
    assert default_settings["progress_log_path"] == "/tmp/krw-test/indexes/build_progress.jsonl"

    monkeypatch.setenv("KRW_BUILD_RESOURCE_PROFILE", "default")
    max_local_settings = agent_index_builder._build_resource_settings(index_path)
    assert max_local_settings["resource_profile"] == "default"
    assert max_local_settings["sqlite_synchronous"] == "OFF"
    assert max_local_settings["sqlite_cache_mib"] == 4096
    assert max_local_settings["sqlite_mmap_gib"] == 16.0
    assert max_local_settings["bulk_insert_chunk_size"] == 20_000
    assert max_local_settings["company_topic_batch_size"] == 2_000

    monkeypatch.setenv("KRW_BUILD_RESOURCE_PROFILE", "max-local")
    max_local_settings = agent_index_builder._build_resource_settings(index_path)
    assert max_local_settings["resource_profile"] == "max-local"
    assert max_local_settings["sqlite_synchronous"] == "OFF"
    assert max_local_settings["sqlite_cache_mib"] == 4096
    assert max_local_settings["sqlite_mmap_gib"] == 16.0
    assert max_local_settings["bulk_insert_chunk_size"] == 20_000
    assert max_local_settings["company_topic_batch_size"] == 2_000


def test_force_build_skips_existing_fts_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q3",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Margin pressure increased because customers demanded lower prices.",
        metric_name="capex",
        metric_value=125.0,
    )

    original_connect = sqlite3.connect
    connections: list[TracingConnection] = []

    class TracingConnection(sqlite3.Connection):
        statements: list[str]

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.statements = []
            connections.append(self)

        def execute(self, sql: str, parameters=(), /):
            self.statements.append(sql)
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        kwargs["factory"] = TracingConnection
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(agent_index_builder.sqlite3, "connect", connect)

    build_agent_index(tmp_path, force=True)

    statements = [statement for conn in connections for statement in conn.statements]
    assert not any("DELETE FROM object_fts" in statement for statement in statements)


def test_no_force_build_replaces_existing_fts_entries(tmp_path: Path):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q3",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Margin pressure increased because customers demanded lower prices.",
        metric_name="capex",
        metric_value=125.0,
    )

    index = build_agent_index(tmp_path, force=True)
    build_agent_index(tmp_path, force=False)

    with sqlite3.connect(index["index_path"]) as conn:
        objects_with_text = conn.execute(
            "SELECT COUNT(*) FROM objects WHERE text != ''"
        ).fetchone()[0]
        fts_rows = conn.execute("SELECT COUNT(*) FROM object_fts").fetchone()[0]

    assert fts_rows == objects_with_text


def test_business_factor_retrieval_text_inherits_supported_claim_keywords(tmp_path: Path):
    ontology_dir = tmp_path / "companies" / "NVDA" / "ontology" / "10K" / "FY2026"
    ontology_dir.mkdir(parents=True)
    claim_id = "claim:NVDA:FY2026:10K:export-controls"
    factor_id = "business_factor:NVDA:FY2026:10K:generic-risk"
    quote_id = "quote:NVDA:FY2026:10K:0001"
    write_jsonl(
        ontology_dir / "claims.jsonl",
        [
            {
                "id": claim_id,
                "type": "ResearchClaim",
                "ticker": "NVDA",
                "document_type": "10-K",
                "period": "FY2026",
                "claim_text": "Export restrictions targeted A100, H100, and DGX exports to China.",
                "supported_by_quotes": [quote_id],
                "review_status": "accepted",
            }
        ],
    )
    write_jsonl(
        ontology_dir / "evidence_quotes.jsonl",
        [
            {
                "id": quote_id,
                "type": "EvidenceQuote",
                "ticker": "NVDA",
                "document_type": "10-K",
                "period": "FY2026",
                "quote_text": "The U.S. government restricted exports of A100 and H100 products to China.",
                "review_status": "accepted",
            }
        ],
    )
    write_jsonl(
        ontology_dir / "business_factors.jsonl",
        [
            {
                "id": factor_id,
                "type": "BusinessFactor",
                "ticker": "NVDA",
                "document_type": "10-K",
                "period": "FY2026",
                "name": "Geopolitical event",
                "factor_roles": ["risk"],
                "description": "Generic geopolitical risk.",
                "supported_by_claims": [claim_id],
                "review_status": "accepted",
            }
        ],
    )
    atomic_write_json(
        ontology_dir / "artifact_index.json",
        {
            "ticker": "NVDA",
            "document_type": "10-K",
            "doc_type_key": "10K",
            "period": "FY2026",
            "files": {
                "claims": "companies/NVDA/ontology/10K/FY2026/claims.jsonl",
                "evidence_quotes": "companies/NVDA/ontology/10K/FY2026/evidence_quotes.jsonl",
                "business_factors": "companies/NVDA/ontology/10K/FY2026/business_factors.jsonl",
            },
            "counts": {"claims": 1, "evidence_quotes": 1, "business_factors": 1},
        },
    )

    index = build_agent_index(tmp_path)

    with OntologyStore(index["index_path"]) as store:
        results = store.query(
            topic="export controls China H100",
            tickers=["NVDA"],
            object_types=["BusinessFactor"],
        )

    assert [result["id"] for result in results] == [factor_id]


def test_agent_retriever_plans_and_retrieves_latest_10q(tmp_path: Path):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q2",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Older quarter margin pressure was limited.",
        metric_name="capex",
        metric_value=100.0,
    )
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q3",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Margin pressure increased because customers demanded lower prices.",
        metric_name="capex",
        metric_value=125.0,
    )
    index = build_agent_index(tmp_path)

    with OntologyStore(index["index_path"]) as store:
        retriever = AgentRetriever(store)
        result = retriever.retrieve("VG 최근 10-Q에서 마진 압박 근거 찾아줘")

    assert "answerable" not in result
    assert "results" not in result
    assert result["answerability"]["recommended_answer_mode"] in {"direct_evidence", "related_context"}
    assert result["plan"]["tickers"] == ["VG"]
    assert result["plan"]["document_types"] == ["10-Q"]
    assert result["resolved_periods"] == ["FY2025Q3"]
    bundles = result["direct_evidence"] + result["related_context"]
    assert {bundle["period"] for bundle in bundles} == {"FY2025Q3"}
    assert any(bundle["evidence"]["quotes"] for bundle in bundles)
    assert result["audit"]["executed_queries"][0]["topic"] == "margin pressure"


def test_agent_retriever_key_risk_question_uses_latest_10k_and_broad_risk_topic(tmp_path: Path):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-K",
        doc_type_key="10K",
        period="FY2024",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Older regulatory risk was limited.",
        metric_name="revenue",
        metric_value=100.0,
    )
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-K",
        doc_type_key="10K",
        period="FY2025",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Regulatory risk could delay approvals and pressure revenue.",
        metric_name="revenue",
        metric_value=125.0,
    )
    index = build_agent_index(tmp_path)

    with OntologyStore(index["index_path"]) as store:
        retriever = AgentRetriever(store)
        result = retriever.retrieve("What are the key risks for VG?", limit=5)

    assert "answerable" not in result
    assert "results" not in result
    assert result["answerability"]["recommended_answer_mode"] in {"direct_evidence", "related_context"}
    assert result["plan"]["tickers"] == ["VG"]
    assert result["plan"]["document_types"] == ["10-K"]
    assert result["plan"]["period_policy"] == "latest"
    assert result["plan"]["topics"] == ["risk"]
    assert result["plan"]["object_types"] == ["BusinessFactor", "ExternalFactorExposure", "ResearchClaim"]
    assert result["resolved_periods"] == ["FY2025"]
    bundles = result["direct_evidence"] + result["related_context"]
    assert {bundle["period"] for bundle in bundles} == {"FY2025"}


def test_agent_retriever_korean_intent_narrows_default_object_types():
    catalog = {"companies": ["NVDA"], "document_types": ["10-K"], "documents": []}
    planner = agent_index_builder  # keep module import used in this file
    del planner
    from krw_ontology.agent_index.retriever import DefaultQueryPlanner

    event_plan = DefaultQueryPlanner().plan("NVDA 최근 중요한 이벤트는?", catalog)
    assert event_plan.object_types == ["BusinessEvent", "ChangeEvent", "ResearchClaim", "EvidenceQuote"]

    metric_plan = DefaultQueryPlanner().plan("NVDA의 주요 지표는?", catalog)
    assert "MetricObservation" in metric_plan.object_types
    assert "SupportLink" not in metric_plan.object_types
    assert "CanonicalEntity" not in metric_plan.object_types
    assert "XBRLFact" not in metric_plan.object_types


def test_metric_observation_trace_includes_xbrl_lineage(tmp_path: Path):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-K",
        doc_type_key="10K",
        period="FY2025",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
    )
    index = build_agent_index(tmp_path)

    with OntologyStore(index["index_path"]) as store:
        trace = store.trace("metric_observation:VG:FY2025:10K:capex")

    assert trace is not None
    lineage = trace["evidence"]["metric_lineage"]
    assert lineage["trace_type"] == "metric_lineage"
    assert lineage["formatted_value"].startswith("FY2025 capex: $900")
    assert lineage["xbrl_facts"][0]["id"] == "xbrl:VG:FY2025:10K:capex"


def test_agent_retriever_compare_and_quality_paths(tmp_path: Path):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q3",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for network investments.",
        metric_name="capex",
        metric_value=125.0,
    )
    _write_document_fixture(
        tmp_path,
        ticker="XOM",
        document_type="10-K",
        doc_type_key="10K",
        period="FY2025",
        section_quality={"status": "warn", "missing_core_sections": ["item7a"], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
        include_rejected=True,
    )
    index = build_agent_index(tmp_path)

    with OntologyStore(index["index_path"]) as store:
        retriever = AgentRetriever(store)
        compare = retriever.retrieve("VG와 XOM capex 비교해줘", tickers=["VG", "XOM"])
        quality = retriever.retrieve("XOM 품질과 rejected 확인해줘")

    assert compare["plan"]["intent"] == "compare"
    assert compare["compare"]["mode"] == "metric"
    assert compare["compare"]["results"]["VG"][0]["object"]["value"] == 125.0
    assert compare["compare"]["results"]["XOM"][0]["object"]["value"] == 900.0

    assert quality["plan"]["intent"] == "quality_check"
    assert quality["quality"]["summary"]["section_warnings"] == 1
    assert quality["quality"]["summary"]["rejected_objects"] == 1


def test_agent_retriever_accepts_injected_planner_and_reranker(tmp_path: Path):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q3",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Margin pressure increased because customers demanded lower prices.",
        metric_name="capex",
        metric_value=125.0,
    )
    index = build_agent_index(tmp_path)

    def planner(_question: str, _catalog: dict) -> QueryPlan:
        return QueryPlan(
            question="custom",
            tickers=["VG"],
            document_types=["10-Q"],
            topics=["margin pressure"],
            object_types=["ResearchClaim", "EvidenceQuote"],
            limit=5,
        )

    def reranker(candidates: list[dict], _plan: QueryPlan) -> list[dict]:
        return sorted(candidates, key=lambda item: item["type"] != "ResearchClaim")

    with OntologyStore(index["index_path"]) as store:
        retriever = AgentRetriever(store, planner=planner, reranker=reranker)
        result = retriever.retrieve("ignored")

    assert result["plan"]["question"] == "custom"
    bundles = result["direct_evidence"] + result["related_context"]
    assert bundles[0]["type"] == "ResearchClaim"


def test_claude_sdk_planner_coerces_output_to_catalog():
    def structured_runner(_prompt: str, _schema: dict) -> dict:
        return {
            "intent": "evidence_search",
            "tickers": ["VG", "FAKE"],
            "document_types": ["10-Q", "8-K"],
            "periods": ["fy2025q3"],
            "period_policy": "latest",
            "topics": ["margin pressure"],
            "metric": None,
            "object_types": ["ResearchClaim", "UnknownType"],
            "include_rejected": False,
            "limit": 99,
        }

    catalog = {
        "companies": ["VG"],
        "document_types": ["10-Q"],
        "documents": [{"ticker": "VG", "document_type": "10-Q", "period": "FY2025Q3"}],
    }
    planner = ClaudeAgentQueryPlanner(structured_runner=structured_runner)

    plan = planner.plan("VG 최근 10-Q에서 마진 압박 근거 찾아줘", catalog)

    assert plan.tickers == ["VG"]
    assert plan.document_types == ["10-Q"]
    assert plan.periods == ["FY2025Q3"]
    assert plan.object_types == ["ResearchClaim"]
    assert plan.limit == 50


def test_claude_sdk_reranker_uses_selected_existing_ids():
    def structured_runner(_prompt: str, _schema: dict) -> dict:
        return {
            "selected": [
                {"id": "claim:1", "reason": "directly supported"},
                {"id": "missing", "reason": "ignored"},
                {"id": "quote:1", "reason": "source quote"},
            ]
        }

    reranker = ClaudeAgentReranker(structured_runner=structured_runner)
    candidates = [
        {"id": "quote:1", "type": "EvidenceQuote"},
        {"id": "claim:1", "type": "ResearchClaim"},
    ]
    plan = QueryPlan(question="test", topics=["margin pressure"])

    result = reranker(candidates, plan)

    assert [item["id"] for item in result] == ["claim:1", "quote:1"]


@pytest.mark.skipif(
    os.environ.get("KRW_RUN_CLAUDE_SDK_TESTS") != "1",
    reason="Set KRW_RUN_CLAUDE_SDK_TESTS=1 to run live Claude Agent SDK calls.",
)
def test_live_claude_sdk_planner_retrieves_fixture(tmp_path: Path):
    _write_document_fixture(
        tmp_path,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q3",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Margin pressure increased because customers demanded lower prices.",
        metric_name="capex",
        metric_value=125.0,
    )
    index = build_agent_index(tmp_path)

    with OntologyStore(index["index_path"]) as store:
        retriever = AgentRetriever(
            store,
            planner=ClaudeAgentQueryPlanner(cwd=Path.cwd(), max_turns=3, timeout_seconds=90),
        )
        result = retriever.retrieve("VG 최근 10-Q에서 마진 압박 근거 찾아줘", limit=5)

    assert "answerable" not in result
    assert "results" not in result
    assert result["answerability"]["recommended_answer_mode"] in {"direct_evidence", "related_context"}
    assert result["plan"]["tickers"] == ["VG"]
    assert result["plan"]["document_types"] == ["10-Q"]
    bundles = result["direct_evidence"] + result["related_context"]
    assert bundles
    assert all(bundle["ticker"] == "VG" for bundle in bundles)


def _write_document_fixture(
    root: Path,
    *,
    ticker: str,
    document_type: str,
    doc_type_key: str,
    period: str,
    section_quality: dict,
    topic_text: str,
    metric_name: str,
    metric_value: float,
    include_rejected: bool = False,
) -> None:
    ontology_dir = root / "companies" / ticker / "ontology" / doc_type_key / period
    sources_dir = root / "companies" / ticker / "sources" / doc_type_key / period
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    source_document_id = f"source:{ticker}:{period}:{doc_type_key}"
    span_id = f"span:{ticker}:{period}:{doc_type_key}:0001"
    quote_id = f"quote:{ticker}:{period}:{doc_type_key}:0001"
    claim_id = f"claim:{ticker}:{period}:{doc_type_key}:margin-pressure"

    span = {
        "id": span_id,
        "type": "SourceSpan",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "section_name": "part1_item2" if document_type == "10-Q" else "item7",
        "section_number": "2" if document_type == "10-Q" else "7",
        "section_key": "part1_item2" if document_type == "10-Q" else "item7",
        "section_instance": 0,
        "span_index": 1,
        "start_char": 0,
        "end_char": len(topic_text),
        "text": topic_text,
        "text_hash": "sha256:test",
        "char_count": len(topic_text),
        "section_detection_confidence": "high",
        "section_detection_method": "test",
        "schema_version": "0.1.0",
    }
    quote = {
        "id": quote_id,
        "type": "EvidenceQuote",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "source_span_id": span_id,
        "quote_text": topic_text,
        "quote_type": "business_update",
        "section_name": span["section_name"],
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    claim = {
        "id": claim_id,
        "type": "ResearchClaim",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "claim_text": f"{topic_text} This is a claim grounded in the filing.",
        "claim_type": "period_update",
        "supported_by_quotes": [quote_id],
        "related_metrics": [metric_name],
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    metric = {
        "id": f"metric_observation:{ticker}:{period}:{doc_type_key}:{metric_name}",
        "type": "MetricObservation",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "metric_name": metric_name,
        "value": metric_value,
        "unit": "USD",
        "fiscal_year": 2025,
        "fiscal_period": period,
        "period_type": "quarter" if document_type == "10-Q" else "annual",
        "source_fact_ids": [f"xbrl:{ticker}:{period}:{doc_type_key}:capex"],
        "source_type": "reported",
        "schema_version": "0.1.0",
    }
    xbrl_fact = {
        "id": f"xbrl:{ticker}:{period}:{doc_type_key}:capex",
        "type": "XBRLFact",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "taxonomy_tag": "us-gaap:CapitalExpenditures",
        "safe_taxonomy_tag": "us_gaap_CapitalExpenditures",
        "concept": "us-gaap:CapitalExpenditures",
        "value": metric_value,
        "unit": "USD",
        "period_end": "2025-12-31",
        "context_ref": "duration_2025",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    activity = {
        "id": f"business_activity:{ticker}:{period}:{doc_type_key}:customer-pricing",
        "type": "BusinessActivity",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "name": "Customer pricing",
        "activity_type": "revenue_source",
        "description": "Customer pricing activity.",
        "related_metrics": [metric_name],
        "supported_by_claims": [claim_id],
        "supported_by_quotes": [quote_id],
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    edges = [
        {
            "id": f"edge:{ticker}:{period}:{doc_type_key}:contains_quote",
            "type": "Edge",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "from_id": span_id,
            "to_id": quote_id,
            "relation_name": "contains_quote",
            "relation_id": "contains_quote",
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": "0.1.0",
        },
        {
            "id": f"edge:{ticker}:{period}:{doc_type_key}:supports",
            "type": "Edge",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "from_id": quote_id,
            "to_id": claim_id,
            "relation_name": "supports",
            "relation_id": "supports",
            "confidence": "high",
            "review_status": "accepted",
            "schema_version": "0.1.0",
        },
    ]

    write_jsonl(ontology_dir / "spans.jsonl", [span])
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", [quote])
    write_jsonl(ontology_dir / "claims.jsonl", [claim])
    write_jsonl(ontology_dir / "business_activities.jsonl", [activity])
    write_jsonl(ontology_dir / "metric_observations.jsonl", [metric])
    write_jsonl(ontology_dir / "xbrl_facts.jsonl", [xbrl_fact])
    write_jsonl(ontology_dir / "edges.jsonl", edges)
    if include_rejected:
        write_jsonl(
            ontology_dir / "rejected_objects.jsonl",
            [
                {
                    **claim,
                    "id": f"claim:{ticker}:{period}:{doc_type_key}:unsupported",
                    "claim_text": "unsupported claim should not be retrieved by default",
                    "rejection_stage": "numeric_guard",
                    "rejection_reason": "test rejection",
                }
            ],
        )
    atomic_write_json(ontology_dir / "section_quality.json", section_quality)

    build_indexes(
        ticker=ticker,
        period=period,
        doc_type_key=doc_type_key,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=root,
        document_type=document_type,
    )
