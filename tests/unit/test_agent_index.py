"""Tests for the agent-facing ontology SQLite index and SDK."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

import krw_ontology.agent_index.builder as agent_index_builder
from krw_ontology.agent_index import (
    AgentRetriever,
    ClaudeAgentQueryPlanner,
    ClaudeAgentReranker,
    OntologyStore,
    OntologyStoreRouter,
    QueryPlan,
    build_agent_index,
    open_ontology_store,
    plan_agent_index,
    verify_source_artifact_manifest,
    write_source_artifact_manifest,
)
from krw_ontology.cli.main import app
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.release import verify_release_root, write_release_manifest
from krw_ontology.utils.io import atomic_write_json, write_jsonl

runner = CliRunner()


def test_ontology_store_read_cache_pragmas_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    index_path = tmp_path / "agent_index.sqlite"
    sqlite3.connect(index_path).close()
    monkeypatch.setenv("KRW_SQLITE_READ_CACHE_MIB", "256")
    monkeypatch.setenv("KRW_SQLITE_READ_MMAP_MIB", "1024")

    with OntologyStore(index_path) as store:
        assert store.conn.execute("PRAGMA cache_size").fetchone()[0] == -262144
        assert store.conn.execute("PRAGMA mmap_size").fetchone()[0] == 1024 * 1024 * 1024


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

    result = runner.invoke(app, ["index", "build", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert "Agent index built:" in result.output
    assert "Build plan:" in result.output
    assert "Global catalog:" in result.output
    assert "Global topics:" in result.output
    assert "Company shards:" in result.output
    assert "1 documents" in result.output
    assert (tmp_path / "indexes" / "agent_index.sqlite").exists()
    assert (tmp_path / "indexes" / "global_catalog.sqlite").exists()
    assert (tmp_path / "indexes" / "global_topics.sqlite").exists()
    assert (tmp_path / "indexes" / "companies" / "VG.sqlite").exists()
    assert (tmp_path / "indexes" / "shard_manifest.json").exists()
    assert (tmp_path / "indexes" / "build_summary.json").exists()
    assert (tmp_path / "indexes" / "build_plan.json").exists()
    assert (tmp_path / "indexes" / "artifact_manifest.json").exists()


def test_build_agent_index_monolith_and_shards_outputs_verified_shards(tmp_path: Path):
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
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
    )

    result = build_agent_index(tmp_path, layout="monolith-and-shards")

    shards = result["shards"]
    catalog_path = Path(shards["global_catalog_path"])
    global_topics_path = Path(shards["global_topics_path"])
    companies_dir = Path(shards["companies_dir"])
    manifest_path = Path(shards["manifest_path"])
    assert catalog_path.exists()
    assert global_topics_path.exists()
    assert companies_dir.exists()
    assert manifest_path.exists()
    assert shards["ticker_count"] == 2
    assert shards["verification"]["ok"] is True
    verification = agent_index_builder.verify_index_shards(
        tmp_path / "indexes",
        monolith_index_path=Path(result["index_path"]),
    )
    assert verification["ok"] is True, verification["errors"]
    assert verification["counts"]["ticker_count"] == 2
    assert verification["counts"]["documents"] == 2
    assert verification["counts"]["objects"] == result["totals"]["objects"]
    assert verification["counts"]["global_topics"] > 0
    assert verification["global_topics_verification"]["ok"] is True

    with sqlite3.connect(catalog_path) as conn:
        catalog_rows = conn.execute(
            """
            SELECT ticker, shard_path, document_count
            FROM shards
            ORDER BY ticker
            """
        ).fetchall()
    assert catalog_rows == [
        ("VG", "companies/VG.sqlite", 1),
        ("XOM", "companies/XOM.sqlite", 1),
    ]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["index_layout_version"] == agent_index_builder.INDEX_LAYOUT_VERSION
    assert manifest["global_topics"] == "global_topics.sqlite"
    assert manifest["global_topics_counts"]["company_topic_index"] == verification["counts"]["global_topics"]
    assert sorted(manifest["shards"]) == ["VG", "XOM"]

    with sqlite3.connect(global_topics_path) as conn:
        topic_count = conn.execute("SELECT COUNT(*) FROM company_topic_index").fetchone()[0]
        topic_fts_count = conn.execute("SELECT COUNT(*) FROM company_topic_fts").fetchone()[0]
    assert topic_count == verification["counts"]["global_topics"]
    assert topic_fts_count == topic_count

    with OntologyStore(companies_dir / "VG.sqlite") as store:
        assert store.list_companies() == ["VG"]
        assert store.query(topic="margin pressure", tickers=["VG"])
        assert store.query(topic="capital expenditures", tickers=["XOM"]) == []

    build_summary = json.loads(Path(result["build_summary_path"]).read_text(encoding="utf-8"))
    assert build_summary["shards"]["verification"]["ok"] is True
    assert build_summary["build_plan"]["layout"] == "monolith-and-shards"

    release_manifest = write_release_manifest(tmp_path, release_id="rel-shards", env="dev")
    assert release_manifest["format"] == "krw-ontology-release/v2"
    assert release_manifest["index_shards_present"] is True
    assert release_manifest["company_shard_count"] == 2
    assert release_manifest["global_catalog_path"] == "indexes/global_catalog.sqlite"
    assert release_manifest["global_topics_path"] == "indexes/global_topics.sqlite"
    assert release_manifest["global_topics_present"] is True
    assert release_manifest["global_topic_count"] == verification["counts"]["global_topics"]
    assert release_manifest["indexes"]["monolith"]["path"] == "indexes/agent_index.sqlite"
    assert release_manifest["indexes"]["global_catalog"]["path"] == "indexes/global_catalog.sqlite"
    assert release_manifest["indexes"]["global_topics"]["path"] == "indexes/global_topics.sqlite"
    assert release_manifest["indexes"]["shard_manifest"]["path"] == "indexes/shard_manifest.json"
    assert release_manifest["indexes"]["company_shards"]["count"] == 2
    assert sorted(release_manifest["indexes"]["company_shards"]["tickers"]) == ["VG", "XOM"]
    assert all(
        entry["sha256"]
        for entry in release_manifest["indexes"]["company_shards"]["tickers"].values()
    )
    release_verification = verify_release_root(tmp_path, env="dev")
    assert release_verification["ok"] is True, release_verification["errors"]
    assert release_verification["index_shard_verification"]["ok"] is True


def test_ontology_store_router_routes_ticker_scoped_reads_to_company_shards(tmp_path: Path):
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
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
    )
    result = build_agent_index(tmp_path)

    with open_ontology_store(result["index_path"]) as store:
        assert isinstance(store, OntologyStoreRouter)
        assert store.list_companies() == ["VG", "XOM"]
        bundles, diagnostics = store.query_with_diagnostics(
            topic="margin pressure",
            tickers=["VG"],
            limit=5,
        )
        assert diagnostics["routing"]["mode"] == "company_shard"
        assert {bundle["ticker"] for bundle in bundles} == {"VG"}
        assert store.routing_status()["open_shards"] == ["VG"]

        trace = store.trace("claim:VG:FY2025Q3:10Q:margin-pressure")
        assert trace is not None
        assert trace["object"]["ticker"] == "VG"

        discovery = store.discover_company_topics(question="capital expenditures margin pressure")
        assert discovery["search_diagnostics"]["routing"]["mode"] == "global_topics"
        assert store.routing_status()["global_topics_open"] is True
        assert store.routing_status()["monolith_open"] is False

        comparison = store.compare(tickers=["VG", "XOM"], metric="capex")
        assert comparison["routing"]["mode"] == "company_shards"
        assert comparison["results"]["VG"][0]["object"]["value"] == 125.0
        assert comparison["results"]["XOM"][0]["object"]["value"] == 900.0
        assert store.routing_status()["monolith_open"] is True


def test_build_agent_index_cli_monolith_and_shards_outputs_paths(tmp_path: Path):
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

    result = runner.invoke(
        app,
        ["index", "build", "--root", str(tmp_path), "--layout", "monolith-and-shards"],
    )

    assert result.exit_code == 0
    assert "Global catalog:" in result.output
    assert "Global topics:" in result.output
    assert "Company shards:" in result.output
    assert (tmp_path / "indexes" / "global_catalog.sqlite").exists()
    assert (tmp_path / "indexes" / "global_topics.sqlite").exists()
    assert (tmp_path / "indexes" / "companies" / "VG.sqlite").exists()
    assert (tmp_path / "indexes" / "shard_manifest.json").exists()


def test_index_build_and_verify_cli_wrap_production_builder(tmp_path: Path):
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

    build_result = runner.invoke(
        app,
        ["index", "build", "--root", str(tmp_path), "--layout", "monolith-and-shards"],
    )
    verify_result = runner.invoke(app, ["index", "verify", "--root", str(tmp_path)])

    assert build_result.exit_code == 0, build_result.output
    assert "Agent index built:" in build_result.output
    assert "Global catalog:" in build_result.output
    assert "Global topics:" in build_result.output
    assert "Company shards:" in build_result.output
    assert verify_result.exit_code == 0, verify_result.output
    assert "Agent index verify: ok" in verify_result.output
    assert "Shard verify: ok" in verify_result.output
    assert "Shards: tickers=1" in verify_result.output


def test_index_verify_cli_fails_for_missing_index(tmp_path: Path):
    result = runner.invoke(app, ["index", "verify", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "Agent index verify: failed" in result.output
    assert "FAIL index_missing" in result.output


def test_index_plan_cli_does_not_build_sqlite(tmp_path: Path):
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

    result = runner.invoke(
        app,
        [
            "index",
            "plan",
            "--root",
            str(tmp_path),
            "--workers",
            "2",
            "--layout",
            "monolith-and-shards",
        ],
    )

    assert result.exit_code == 0
    assert "Artifacts: 1" in result.output
    assert "Dirty artifacts: 1" in result.output
    assert "Workers: 2" in result.output
    assert "Layout: monolith-and-shards" in result.output
    assert not (tmp_path / "indexes" / "agent_index.sqlite").exists()
    assert not (tmp_path / "indexes" / "build_plan.json").exists()


def test_index_inspect_and_explain_last_build_cli(tmp_path: Path):
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
    build_agent_index(tmp_path)

    inspect_result = runner.invoke(app, ["index", "inspect", "--root", str(tmp_path), "--json"])
    explain_result = runner.invoke(app, ["index", "explain-last-build", "--root", str(tmp_path), "--json"])

    assert inspect_result.exit_code == 0, inspect_result.output
    inspect_payload = json.loads(inspect_result.output)
    assert inspect_payload["verification"]["ok"] is True
    assert inspect_payload["shard_verification"]["ok"] is True
    assert inspect_payload["build_summary"]["build_plan"]["artifact_count"] == 1
    assert explain_result.exit_code == 0, explain_result.output
    explain_payload = json.loads(explain_result.output)
    assert explain_payload["build_plan"]["artifact_count"] == 1
    assert explain_payload["fragment_cache"]["misses"] == 1
    assert explain_payload["slow_phases"]


def test_plan_agent_index_uses_content_addressed_fragment_keys(tmp_path: Path):
    cache_root = tmp_path / "cache"
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

    first = plan_agent_index(
        tmp_path,
        cache_root=cache_root,
        workers=3,
        layout="monolith-and-shards",
    )
    first_item = first.items[0]
    assert first.workers == 3
    assert first.layout == "monolith-and-shards"
    assert first.dirty_tickers == ("VG",)
    assert first_item.cache_key.startswith("sha256:")
    assert first_item.fragment_path.parent == cache_root / "fragments" / first_item.cache_key.split(":", 1)[1][:2]
    assert first_item.cache_hit is False
    assert first_item.cache_errors == ("fragment_missing",)
    assert first_item.estimated_bytes > 0
    assert first_item.estimated_rows is not None

    first_item.fragment_path.parent.mkdir(parents=True)
    first_item.fragment_path.write_text("fragment-placeholder", encoding="utf-8")
    corrupt = plan_agent_index(tmp_path, cache_root=cache_root)
    assert corrupt.items[0].cache_hit is False
    assert any(error.startswith("sqlite_error:") for error in corrupt.items[0].cache_errors)

    agent_index_builder.write_index_fragment_metadata(first_item.fragment_path, first_item)
    verification = agent_index_builder.verify_index_fragment(first_item.fragment_path, expected=first_item)
    assert verification["ok"] is True
    mismatch = agent_index_builder.verify_index_fragment(
        first_item.fragment_path,
        expected={"cache_key": "sha256:wrong"},
    )
    assert mismatch["ok"] is False
    assert "metadata_mismatch:cache_key" in mismatch["errors"]

    cached = plan_agent_index(tmp_path, cache_root=cache_root)
    assert cached.items[0].cache_key == first_item.cache_key
    assert cached.items[0].cache_hit is True
    assert cached.items[0].cache_errors == ()
    assert cached.cached_items == cached.items
    assert cached.dirty_items == ()

    spans_path = tmp_path / "companies" / "VG" / "ontology" / "10Q" / "FY2025Q3" / "spans.jsonl"
    spans_path.write_text(spans_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    changed = plan_agent_index(tmp_path, cache_root=cache_root)
    assert changed.items[0].content_hash != first_item.content_hash
    assert changed.items[0].cache_key != first_item.cache_key
    assert changed.items[0].cache_hit is False
    assert changed.dirty_tickers == ("VG",)


def test_source_manifest_only_discovery_and_build_graph(tmp_path: Path):
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
    manifest = write_source_artifact_manifest(tmp_path)
    manifest_path = Path(manifest["path"])

    verification = verify_source_artifact_manifest(tmp_path, manifest_path=manifest_path)
    plan = plan_agent_index(tmp_path, source_manifest_path=manifest_path)
    result = build_agent_index(tmp_path, source_manifest_path=manifest_path)

    assert verification["ok"] is True, verification["errors"]
    assert manifest["format"] == agent_index_builder.SOURCE_ARTIFACT_MANIFEST_FORMAT_VERSION
    assert plan.discovery_mode == "source-manifest"
    assert plan.source_manifest_hash == manifest["manifest_hash"]
    assert len(plan.items) == 1
    build_graph = json.loads(Path(result["build_graph_path"]).read_text(encoding="utf-8"))
    assert build_graph["format"] == agent_index_builder.INDEX_BUILD_GRAPH_FORMAT_VERSION
    assert build_graph["discovery_mode"] == "source-manifest"
    assert any(node["type"] == "source_manifest" for node in build_graph["nodes"])
    assert any(node["type"] == "company_projection" for node in build_graph["nodes"])

    spans_path = tmp_path / "companies" / "VG" / "ontology" / "10Q" / "FY2025Q3" / "spans.jsonl"
    spans_path.write_text(spans_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    changed_verification = verify_source_artifact_manifest(tmp_path, manifest_path=manifest_path)
    assert changed_verification["ok"] is False
    assert any(error.startswith("source_manifest_content_hash_mismatch:") for error in changed_verification["errors"])


def test_build_agent_index_reuses_company_shard_cache(tmp_path: Path):
    cache_root = tmp_path / "cache"
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
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
    )

    first = build_agent_index(tmp_path, cache_root=cache_root)
    second = build_agent_index(tmp_path, cache_root=cache_root)

    assert first["shards"]["company_cache"]["hits"] == 0
    assert first["shards"]["company_cache"]["misses"] == 2
    assert second["shards"]["company_cache"]["hits"] == 2
    assert second["shards"]["company_cache"]["misses"] == 0
    assert sorted(second["shards"]["shards"]) == ["VG", "XOM"]
    assert all(entry["cache_hit"] is True for entry in second["shards"]["shards"].values())
    build_graph = json.loads(Path(second["build_graph_path"]).read_text(encoding="utf-8"))
    shard_nodes = [node for node in build_graph["nodes"] if node["type"] == "company_shard"]
    assert {node["status"] for node in shard_nodes} == {"cached"}


def test_fragment_cache_key_changes_with_builder_code_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cache_root = tmp_path / "cache"
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
    first = plan_agent_index(tmp_path, cache_root=cache_root)
    first_item = first.items[0]
    agent_index_builder.write_index_fragment_metadata(first_item.fragment_path, first_item)

    monkeypatch.setattr(agent_index_builder, "AGENT_INDEX_BUILDER_VERSION", "agent-index-builder/test-next")
    changed = plan_agent_index(tmp_path, cache_root=cache_root)
    old_fragment = agent_index_builder.verify_index_fragment(first_item.fragment_path)

    assert changed.builder_code_version == "agent-index-builder/test-next"
    assert changed.items[0].content_hash == first_item.content_hash
    assert changed.items[0].cache_key != first_item.cache_key
    assert changed.items[0].cache_hit is False
    assert "metadata_mismatch:builder_code_version" in old_fragment["errors"]


def test_build_agent_index_reuses_verified_fragment_cache(tmp_path: Path):
    cache_root = tmp_path / "cache"
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

    first = build_agent_index(tmp_path, cache_root=cache_root)
    second = build_agent_index(tmp_path, cache_root=cache_root)

    assert first["fragment_cache"] == {
        "hits": 0,
        "misses": 1,
        "cache_root": str(cache_root.resolve()),
        "workers": first["build_plan_summary"]["workers"],
    }
    assert second["fragment_cache"] == {
        "hits": 1,
        "misses": 0,
        "cache_root": str(cache_root.resolve()),
        "workers": second["build_plan_summary"]["workers"],
    }
    with sqlite3.connect(second["index_path"]) as conn:
        raw_build_metadata = conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()[0]
    build_metadata = json.loads(raw_build_metadata)
    assert build_metadata["fragment_cache_hits"] == 1
    assert build_metadata["fragment_cache_misses"] == 0
    assert build_metadata["fragment_compile_workers"] == second["build_plan_summary"]["workers"]

    with OntologyStore(second["index_path"]) as store:
        assert store.list_companies() == ["VG"]
        assert store.query(topic="margin pressure", tickers=["VG"])


def test_build_agent_index_rebuilds_corrupt_fragment_cache(tmp_path: Path):
    cache_root = tmp_path / "cache"
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
    plan = plan_agent_index(tmp_path, cache_root=cache_root)
    item = plan.items[0]
    item.fragment_path.parent.mkdir(parents=True)
    item.fragment_path.write_text("not sqlite", encoding="utf-8")

    result = build_agent_index(tmp_path, cache_root=cache_root)

    assert result["fragment_cache"]["hits"] == 0
    assert result["fragment_cache"]["misses"] == 1
    verification = agent_index_builder.verify_index_fragment(item.fragment_path, expected=item)
    assert verification["ok"] is True
    assert verification["counts"]["documents"] == 1
    assert verification["counts"]["objects"] > 0


def test_index_cache_status_reports_referenced_fragments(tmp_path: Path):
    cache_root = tmp_path / "cache"
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
    build_agent_index(tmp_path, cache_root=cache_root)

    result = runner.invoke(
        app,
        [
            "index",
            "cache",
            "status",
            "--root",
            str(tmp_path),
            "--cache-root",
            str(cache_root),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Fragment cache: ok" in result.output
    assert "Fragments: total=1 valid=1 invalid=0 referenced=1 unreferenced=0" in result.output
    assert "Rows: documents=1" in result.output
    assert "Tickers: VG" in result.output


def test_index_cache_gc_removes_invalid_and_unreferenced_fragments_only_with_yes(tmp_path: Path):
    cache_root = tmp_path / "cache"
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
    build_agent_index(tmp_path, cache_root=cache_root)
    referenced_fragment = next((cache_root / "fragments").rglob("*.sqlite"))
    stale_fragment = cache_root / "fragments" / "ff" / "stale-copy.sqlite"
    stale_fragment.parent.mkdir(parents=True)
    shutil.copy2(referenced_fragment, stale_fragment)
    invalid_fragment = cache_root / "fragments" / "ee" / "invalid.sqlite"
    invalid_fragment.parent.mkdir(parents=True)
    invalid_fragment.write_text("not sqlite", encoding="utf-8")

    dry_run = runner.invoke(
        app,
        [
            "index",
            "cache",
            "gc",
            "--root",
            str(tmp_path),
            "--cache-root",
            str(cache_root),
        ],
    )

    assert dry_run.exit_code == 0, dry_run.output
    assert "Fragment cache GC: dry-run" in dry_run.output
    assert "Candidates: 2" in dry_run.output
    assert stale_fragment.exists()
    assert invalid_fragment.exists()
    assert referenced_fragment.exists()

    deleted = runner.invoke(
        app,
        [
            "index",
            "cache",
            "gc",
            "--root",
            str(tmp_path),
            "--cache-root",
            str(cache_root),
            "--yes",
        ],
    )

    assert deleted.exit_code == 0, deleted.output
    assert "Fragment cache GC: deleted" in deleted.output
    assert "Deleted: 2" in deleted.output
    assert not stale_fragment.exists()
    assert not invalid_fragment.exists()
    assert referenced_fragment.exists()

    status = agent_index_builder.inspect_index_fragment_cache(
        cache_root,
        referenced_fragments=[referenced_fragment],
    )
    assert status["fragment_count"] == 1
    assert status["valid_fragment_count"] == 1
    assert status["invalid_fragment_count"] == 0
    assert status["referenced_fragment_count"] == 1


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
    assert result["verification"]["ok"] is True
    assert Path(result["build_summary_path"]).exists()
    assert Path(result["build_plan_path"]).exists()
    assert Path(result["artifact_manifest_path"]).exists()
    build_summary = json.loads(Path(result["build_summary_path"]).read_text(encoding="utf-8"))
    assert build_summary["verification"]["ok"] is True
    assert build_summary["totals"]["documents"] == 1
    assert build_summary["build_plan"]["artifact_count"] == 1
    assert build_summary["artifact_manifest"]["artifact_count"] == 1
    assert build_summary["build_plan"]["dirty_artifact_count"] == 1
    assert build_summary["fragment_cache"]["misses"] == 1
    assert build_summary["slow_phases"]

    log_rows = [json.loads(line) for line in progress_log.read_text().splitlines()]
    assert log_rows
    assert log_rows[0]["phase"] == "start"
    assert any(row["phase"] == "finalize_done" for row in log_rows)
    assert all(row["event"] == "build_phase" for row in log_rows)
    assert log_rows[0]["sqlite_synchronous"] == "OFF"
    assert log_rows[0]["sqlite_cache_mib"] == 64
    assert "db_size_mb" in log_rows[0]
    assert "wal_size_mb" in log_rows[0]


def test_build_agent_index_failure_preserves_existing_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    first = build_agent_index(tmp_path)
    index_path = first["index_path"]

    _write_document_fixture(
        tmp_path,
        ticker="XOM",
        document_type="10-K",
        doc_type_key="10K",
        period="FY2025",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
    )

    def fail_index_artifact(*args, **kwargs):
        raise RuntimeError("forced artifact index failure")

    monkeypatch.setattr(agent_index_builder, "_index_artifact", fail_index_artifact)

    with pytest.raises(RuntimeError, match="forced artifact index failure"):
        build_agent_index(tmp_path, force=True, workers=1)

    with OntologyStore(index_path) as store:
        assert store.list_companies() == ["VG"]

    assert not list(index_path.parent.glob(f".{index_path.name}.*.tmp"))


def test_build_agent_index_compiles_fragments_with_process_workers(tmp_path: Path):
    cache_root = tmp_path / "cache"
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
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
    )

    result = build_agent_index(tmp_path, cache_root=cache_root, workers=2)

    assert result["fragment_cache"]["hits"] == 0
    assert result["fragment_cache"]["misses"] == 2
    with OntologyStore(result["index_path"]) as store:
        assert store.list_companies() == ["VG", "XOM"]


def test_build_agent_index_api_rejects_active_release_and_active_release_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    release_root = tmp_path / "releases" / "dev" / "active"
    _write_document_fixture(
        release_root,
        ticker="VG",
        document_type="10-Q",
        doc_type_key="10Q",
        period="FY2025Q3",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Margin pressure increased because customers demanded lower prices.",
        metric_name="capex",
        metric_value=125.0,
    )
    current = release_root.parent / "current"
    current.symlink_to(release_root.name)

    with pytest.raises(ValueError, match="current is an immutable release pointer"):
        build_agent_index(current)

    running = tmp_path / "running"
    _write_document_fixture(
        running,
        ticker="XOM",
        document_type="10-K",
        doc_type_key="10K",
        period="FY2025",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
    )
    with pytest.raises(ValueError, match="current is an immutable release pointer"):
        build_agent_index(running, cache_root=current / "cache")

    monkeypatch.setenv("KRW_BUILD_PROGRESS_LOG", str(current / "build_progress.jsonl"))
    with pytest.raises(ValueError, match="current is an immutable release pointer"):
        build_agent_index(running, cache_root=tmp_path / "cache")


def test_parallel_fragment_compile_dispatches_large_artifacts_first(tmp_path: Path):
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
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
    )
    items = plan_agent_index(tmp_path).items
    small = replace(items[0], estimated_bytes=10, relative_path="z-small")
    large_b = replace(items[1], estimated_bytes=100, relative_path="b-large")
    large_a = replace(items[0], estimated_bytes=100, relative_path="a-large")

    ordered = agent_index_builder._fragment_compile_submission_order([small, large_b, large_a])

    assert [item.relative_path for item in ordered] == ["a-large", "b-large", "z-small"]


def test_parallel_fragment_pool_failure_retries_sequential_compile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cache_root = tmp_path / "cache"
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
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
    )

    class BrokenExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise agent_index_builder.BrokenProcessPool("forced pool failure")

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(agent_index_builder, "ProcessPoolExecutor", BrokenExecutor)

    result = build_agent_index(tmp_path, cache_root=cache_root, workers=2)

    assert result["fragment_cache"]["hits"] == 0
    assert result["fragment_cache"]["misses"] == 2
    with OntologyStore(result["index_path"]) as store:
        assert store.list_companies() == ["VG", "XOM"]


def test_parallel_fragment_failure_preserves_existing_index(tmp_path: Path):
    cache_root = tmp_path / "cache"
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
    first = build_agent_index(tmp_path, cache_root=cache_root, workers=1)
    index_path = first["index_path"]

    _write_document_fixture(
        tmp_path,
        ticker="XOM",
        document_type="10-K",
        doc_type_key="10K",
        period="FY2025",
        section_quality={"status": "pass", "missing_core_sections": [], "fail_reasons": []},
        topic_text="Capital expenditures increased for upstream projects.",
        metric_name="capex",
        metric_value=900.0,
    )
    spans_path = tmp_path / "companies" / "XOM" / "ontology" / "10K" / "FY2025" / "spans.jsonl"
    spans_path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="fragment compile failed"):
        build_agent_index(tmp_path, cache_root=cache_root, workers=2)

    with OntologyStore(index_path) as store:
        assert store.list_companies() == ["VG"]


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
