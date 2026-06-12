from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import krw_ontology.agent_index.builder as agent_index_builder
import krw_ontology.agent_index.spine_builder as spine_builder
from krw_ontology.agent_index.source_artifact_sqlite import (
    SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
    SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
)
from krw_ontology.agent_index.spine_builder import (
    build_spine_shard_release_outputs,
    build_company_shard_direct,
    emit_spine_fragment_from_company_shard,
    merge_spine_fragments,
    _company_shard_quality_summary,
)
from krw_ontology.agent_index.spine_schema import (
    create_global_spine_schema,
    verify_global_spine_schema,
    write_global_spine_metadata,
)
from krw_ontology.agent_index.spine_verify import verify_spine_shard_release


def _write_synthetic_company_shard(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        agent_index_builder._create_schema(conn)
        conn.execute(
            "INSERT INTO metadata(key, value) VALUES('build', ?)",
            (
                json.dumps(
                    {
                        "schema_version": agent_index_builder.AGENT_INDEX_SCHEMA_VERSION,
                        "agent_index_schema_version": agent_index_builder.AGENT_INDEX_SCHEMA_VERSION,
                    },
                    sort_keys=True,
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO documents(
                ticker, document_type, doc_type_key, period, artifact_index_path,
                ontology_dir, sources_json, reports_json, counts_json,
                section_quality_status, section_quality_json
            )
            VALUES('AAPL', '10-K', '10K', 'FY2025', 'companies/AAPL/context/artifact_index.json',
                   'companies/AAPL/context', '{}', '{}',
                   '{"objects": 2, "edges": 1, "quality_events": 0}', 'ok', '{}')
            """
        )
        for object_id, object_type, text in (
            ("obj:AAPL:factor", "BusinessFactor", "AI capex demand supports services growth."),
            ("obj:AAPL:metric", "MetricObservation", "Revenue was 100."),
        ):
            conn.execute(
                """
                INSERT INTO objects(
                    id, type, ticker, document_type, doc_type_key, period,
                    source_document_id, section_name, metric_name, review_status,
                    confidence, text, json, artifact_key, artifact_path
                )
                VALUES(?, ?, 'AAPL', '10-K', '10K', 'FY2025', NULL, 'Risk',
                       NULL, 'accepted', 'high', ?, ?, 'objects', 'objects.jsonl')
                """,
                (object_id, object_type, text, json.dumps({"label": text}, sort_keys=True)),
            )
            conn.execute(
                """
                INSERT INTO object_search_text(
                    object_id, type, ticker, document_type, period,
                    text_self, text_support, text_related, text_entities,
                    text_aliases, compact_text
                )
                VALUES(?, ?, 'AAPL', '10-K', 'FY2025', ?, '', 'AI capex cloud',
                       'Microsoft; data center', '', ?)
                """,
                (object_id, object_type, text, text),
            )
        conn.execute(
            """
            INSERT INTO edges(
                id, ticker, document_type, doc_type_key, period,
                from_id, to_id, relation_id, relation_name, confidence,
                review_status, json, artifact_path
            )
            VALUES('edge:AAPL:1', 'AAPL', '10-K', '10K', 'FY2025',
                   'obj:AAPL:factor', 'obj:AAPL:metric', 'supports',
                   'supports', 'high', 'accepted', '{}', 'edges.jsonl')
            """
        )
        conn.execute(
            """
            INSERT INTO factor_lookup(
                object_id, object_type, ticker, document_type, doc_type_key, period,
                factor_type, topic_family, impact_channel, risk_or_driver,
                topic_label, trace_status, evidence_strength, specificity_score,
                generic_score, boilerplate_score, lookup_text
            )
            VALUES('obj:AAPL:factor', 'BusinessFactor', 'AAPL', '10-K', '10K', 'FY2025',
                   'demand', 'AI infrastructure', 'revenue', 'AI capex',
                   'AI capex demand', 'traceable', 'high', 0.9, 0.1, 0.0,
                   'AI capex demand')
            """
        )
        conn.execute(
            """
            INSERT INTO metric_lookup(
                object_id, object_type, ticker, document_type, doc_type_key, period,
                metric_name, canonical_metric, value_text, unit, dimensions_json,
                is_company_total, trace_status, metric_lineage_status, text
            )
            VALUES('obj:AAPL:metric', 'MetricObservation', 'AAPL', '10-K', '10K', 'FY2025',
                   'Revenue', 'revenue', '100', 'USD', '{}', 1,
                   'traceable', 'ok', 'Revenue was 100')
            """
        )
        conn.execute(
            """
            INSERT INTO agreement_lookup(
                object_id, object_type, ticker, document_type, doc_type_key, period,
                agreement_type, agreement_subtype, counterparty, affected_channels,
                trace_status, evidence_strength, specificity_score, generic_score,
                boilerplate_score, lookup_text
            )
            VALUES('obj:AAPL:factor', 'AgreementTerm', 'AAPL', '10-K', '10K', 'FY2025',
                   'supply', 'cloud', 'Microsoft', '["revenue"]',
                   'traceable', 'medium', 0.8, 0.1, 0.0, 'Microsoft supply')
            """
        )
        conn.execute(
            """
            INSERT INTO company_topic_index(
                topic_id, ticker, period, document_type, doc_type_key,
                topic_label, topic_summary, topic_family, primary_object_id,
                primary_object_type, source_object_ids, top_traceable_object_ids,
                untraced_object_ids, dominant_object_types, impact_channels,
                factor_terms, metric_terms, entity_terms, mechanism_terms,
                scenario_terms, evidence_strength, materiality_score
            )
            VALUES('topic:AAPL:ai-capex', 'AAPL', 'FY2025', '10-K', '10K',
                   'AI capex demand', 'AI capex demand links cloud and devices.',
                   'growth', 'obj:AAPL:factor', 'BusinessFactor',
                   '["obj:AAPL:factor", "obj:AAPL:metric"]',
                   '["obj:AAPL:factor"]', '[]', '["BusinessFactor"]',
                   '["revenue"]', '["AI capex"]', '["revenue"]',
                   '["Microsoft"]', '["demand"]', '[]', 'high', 0.9)
            """
        )


def _write_minimal_context_artifact(root: Path, ticker: str) -> None:
    artifact_index_path = root / "companies" / ticker / "context" / "artifact_index.json"
    artifact_index_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_index_path.write_text(
        json.dumps(
            {
                "ticker": ticker,
                "document_type": "COMPANY",
                "doc_type_key": "COMPANY",
                "period": "ALL",
                "files": {},
                "counts": {},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_emit_spine_fragment_from_company_shard_projects_global_rows(tmp_path: Path) -> None:
    shard_path = tmp_path / "indexes" / "companies" / "AAPL.sqlite"
    fragment_path = tmp_path / "indexes" / "fragments" / "spine" / "AAPL.sqlite"
    _write_synthetic_company_shard(shard_path)

    result = emit_spine_fragment_from_company_shard(
        shard_path,
        fragment_path,
        ticker="AAPL",
        release_id="test-release",
        shard_path_in_release="indexes/companies/AAPL.sqlite",
    )
    verification = verify_global_spine_schema(fragment_path)

    assert result.ticker == "AAPL"
    assert verification["ok"] is True, verification["errors"]
    with sqlite3.connect(fragment_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM global_object_locator").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM global_document_catalog").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_edge_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_factor_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_topic_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_metric_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_counterparty_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_entity_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_key_stats").fetchone()[0] >= 5
        route = conn.execute(
            "SELECT shard_path FROM global_object_locator WHERE object_id = 'obj:AAPL:factor'"
        ).fetchone()[0]
    assert route == "indexes/companies/AAPL.sqlite"


def test_emit_spine_fragment_skips_edges_with_missing_endpoints(tmp_path: Path) -> None:
    shard_path = tmp_path / "indexes" / "companies" / "AAPL.sqlite"
    fragment_path = tmp_path / "indexes" / "fragments" / "spine" / "AAPL.sqlite"
    _write_synthetic_company_shard(shard_path)
    with sqlite3.connect(shard_path) as conn:
        conn.execute(
            """
            INSERT INTO edges(
                id, ticker, document_type, doc_type_key, period,
                from_id, to_id, relation_id, relation_name, confidence,
                review_status, json, artifact_path
            )
            VALUES('edge:AAPL:dangling', 'AAPL', '10-K', '10K', 'FY2025',
                   'obj:AAPL:missing', 'obj:AAPL:metric', 'supports',
                   'supports', 'high', 'accepted', '{}', 'edges.jsonl')
            """
        )

    emit_spine_fragment_from_company_shard(
        shard_path,
        fragment_path,
        ticker="AAPL",
        release_id="test-release",
        shard_path_in_release="indexes/companies/AAPL.sqlite",
    )

    with sqlite3.connect(fragment_path) as conn:
        edges = conn.execute(
            "SELECT edge_id FROM global_edge_spine ORDER BY edge_id"
        ).fetchall()
    assert [row[0] for row in edges] == ["edge:AAPL:1"]


def test_build_company_shard_direct_builds_without_monolith(tmp_path: Path) -> None:
    root = tmp_path / "source"
    artifact_index_path = root / "companies" / "AAPL" / "context" / "artifact_index.json"
    artifact_index_path.parent.mkdir(parents=True)
    artifact_index_path.write_text(
        json.dumps(
            {
                "ticker": "AAPL",
                "document_type": "COMPANY",
                "doc_type_key": "COMPANY",
                "period": "ALL",
                "files": {},
                "counts": {},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    shard_path = tmp_path / "release" / "indexes" / "companies" / "AAPL.sqlite"

    result = build_company_shard_direct(
        root,
        ticker="AAPL",
        shard_path=shard_path,
        workers=1,
        no_cache=True,
    )

    assert result.ticker == "AAPL"
    assert result.shard_path == shard_path.resolve()
    assert result.artifact_count == 1
    assert shard_path.exists()
    assert not (tmp_path / "release" / "indexes" / "agent_index.sqlite").exists()
    with sqlite3.connect(shard_path) as conn:
        metadata = json.loads(conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()[0])
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert metadata["index_role"] == "company_shard"
    assert metadata["shard_ticker"] == "AAPL"
    assert metadata["index_layout"] == "global-spine-and-company-shards"
    assert metadata["source_artifact_sqlite_schema_version"] == SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION
    assert metadata["source_artifact_sqlite_builder_version"] == SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION
    assert doc_count == 1


def test_build_spine_shard_release_outputs_builds_v3_without_monolith(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    artifact_index_path = release_root / "companies" / "AAPL" / "context" / "artifact_index.json"
    artifact_index_path.parent.mkdir(parents=True)
    artifact_index_path.write_text(
        json.dumps(
            {
                "ticker": "AAPL",
                "document_type": "COMPANY",
                "doc_type_key": "COMPANY",
                "period": "ALL",
                "files": {},
                "counts": {},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_spine_shard_release_outputs(
        release_root,
        release_id="test-release",
        workers=1,
        no_cache=True,
        generate_links=True,
    )

    assert result.global_spine_path.exists()
    assert result.shard_manifest_path.exists()
    assert result.build_plan_path.exists()
    assert result.build_summary_path.exists()
    assert result.progress_path is not None
    assert result.progress_path.exists()
    assert (release_root / "source_manifest.json").exists()
    assert not (release_root / "indexes" / "agent_index.sqlite").exists()
    assert not (release_root / "indexes" / "fragments" / "spine").exists()
    assert not (release_root / "indexes" / "fragments").exists()
    assert sorted(result.shard_manifest["shards"]) == ["AAPL"]
    shard_entry = result.shard_manifest["shards"]["AAPL"]
    assert shard_entry["quality_summary"]["format"] == "krw-ontology-shard-quality-summary/v1"
    assert shard_entry["quality_summary"]["totals"]["documents"] == 1
    assert shard_entry["quality_summary"]["ticker_quality"][0]["ticker"] == "AAPL"
    assert result.build_summary["company_count"] == 1
    assert result.build_summary["progress_path"] == "indexes/build_progress.jsonl"
    assert result.build_summary["artifact_cleanup"]["spine_fragments"]["removed"] is True
    assert result.build_summary["artifact_cleanup"]["spine_fragments"]["file_count"] >= 1
    assert result.merge_result.verification["ok"] is True
    progress_events = [
        json.loads(line)
        for line in result.progress_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_keys = {(event["stage"], event["node_id"], event["status"]) for event in progress_events}
    assert ("source_discovery", "source_manifest", "complete") in event_keys
    assert ("build_plan", "build_plan", "started") in event_keys
    assert ("cache_probe", "cache_probe", "complete") in event_keys
    assert ("build_plan", "build_plan", "complete") in event_keys
    assert ("company_shard", "company_shard:AAPL", "rebuilt") in event_keys
    assert ("spine_fragment", "spine_fragment:AAPL", "rebuilt") in event_keys
    assert ("global_spine_merge", "global_spine_merge:AAPL", "merged") in event_keys
    assert ("global_spine_merge", "global_spine_merge", "complete") in event_keys
    assert ("manifest", "shard_manifest", "complete") in event_keys
    assert ("artifact_cleanup", "spine_fragments", "complete") in event_keys
    assert ("build_summary", "build_summary", "complete") in event_keys
    assert ("build", "build", "complete") in event_keys
    company_event = next(event for event in progress_events if event["node_id"] == "company_shard:AAPL" and event["status"] == "rebuilt")
    assert company_event["cache_hit"] is False
    assert company_event["output"] == "indexes/companies/AAPL.sqlite"
    from krw_ontology.release import verify_release_root, write_release_manifest_v3

    write_release_manifest_v3(release_root, release_id="test-release", env="dev")
    verification = verify_release_root(release_root, env="dev")

    assert verification["ok"] is True, verification["errors"]
    assert verification["verification_mode"] == "release-root-v3-light"


def test_build_spine_shard_release_outputs_retries_process_pool_sequentially(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = tmp_path / "release"
    _write_minimal_context_artifact(release_root, "AAPL")
    _write_minimal_context_artifact(release_root, "MSFT")

    class BrokenExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise spine_builder.BrokenProcessPool("forced pool failure")

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(spine_builder, "ProcessPoolExecutor", BrokenExecutor)

    result = build_spine_shard_release_outputs(
        release_root,
        release_id="fallback-release",
        workers=2,
        no_cache=True,
        generate_links=False,
    )

    assert sorted(item.ticker for item in result.shard_results) == ["AAPL", "MSFT"]
    progress_events = [
        json.loads(line)
        for line in result.progress_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_keys = {(event["stage"], event["node_id"], event["status"]) for event in progress_events}
    assert ("company_shard", "company_shards", "fallback_sequential") in event_keys
    assert ("spine_fragment", "spine_fragments", "fallback_sequential") in event_keys


def test_company_shard_runtime_cache_corruption_rebuilds_and_quarantines(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    cache_root = tmp_path / "cache"
    _write_minimal_context_artifact(release_root, "AAPL")
    first = build_spine_shard_release_outputs(
        release_root,
        release_id="first-release",
        cache_root=cache_root,
        workers=1,
        generate_links=False,
    )
    first_shard = first.shard_results[0]
    assert first_shard.cache_key is not None
    digest = first_shard.cache_key.split(":", 1)[-1]
    cache_path = cache_root / "v3" / "company_shards" / digest[:2] / f"{digest}.sqlite"
    assert cache_path.exists()

    plan = spine_builder._plan_v3_artifact_inputs(
        release_root,
        sqlite_path=release_root / "indexes" / "global_spine.sqlite",
        cache_root=cache_root,
        workers=1,
        source_manifest_path=release_root / "source_manifest.json",
    )
    company_plan = spine_builder._filter_plan_for_company(
        plan,
        ticker="AAPL",
        shard_path=release_root / "indexes" / "companies" / "AAPL.sqlite",
        no_cache=False,
    )
    assert company_plan.company_items[0].cache_hit is True
    cache_path.write_text("not sqlite", encoding="utf-8")

    result = spine_builder._build_company_shard_from_plan(
        release_root,
        ticker="AAPL",
        shard_path=release_root / "indexes" / "companies" / "AAPL.sqlite",
        company_plan=company_plan,
        no_cache=False,
    )

    assert result.cache_hit is False
    assert result.shard_path.exists()
    assert not cache_path.read_bytes().startswith(b"not sqlite")
    assert list(cache_path.parent.glob(".corrupt-*.sqlite.*"))


def test_cached_company_shard_rebases_paths_to_current_release_root(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    first_root = tmp_path / "releases" / "dev" / "first-release"
    second_root = tmp_path / "releases" / "prod" / "second-release"
    _write_minimal_context_artifact(first_root, "AAPL")
    _write_minimal_context_artifact(second_root, "AAPL")

    first = build_spine_shard_release_outputs(
        first_root,
        release_id="first-release",
        cache_root=cache_root,
        workers=1,
        generate_links=False,
    )
    second = build_spine_shard_release_outputs(
        second_root,
        release_id="second-release",
        cache_root=cache_root,
        workers=1,
        generate_links=False,
    )

    assert first.shard_results[0].cache_hit is False
    assert second.shard_results[0].cache_hit is True
    with sqlite3.connect(second.shard_results[0].shard_path) as conn:
        artifact_index_path, ontology_dir = conn.execute(
            "SELECT artifact_index_path, ontology_dir FROM documents"
        ).fetchone()
        metadata = json.loads(conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()[0])
    assert artifact_index_path.startswith(str(second_root.resolve()))
    assert ontology_dir.startswith(str(second_root.resolve()))
    assert str(first_root.resolve()) not in artifact_index_path
    assert metadata["artifact_root"] == str(second_root.resolve())


def test_rebase_company_shard_updates_object_edge_and_metadata_paths(tmp_path: Path) -> None:
    old_root = tmp_path / "releases" / "dev" / "old-release"
    new_root = tmp_path / "releases" / "prod" / "new-release"
    shard_path = tmp_path / "AAPL.sqlite"
    _write_synthetic_company_shard(shard_path)
    old_doc_dir = old_root / "companies" / "AAPL" / "ontology" / "10K" / "FY2025"
    with sqlite3.connect(shard_path) as conn:
        conn.execute(
            "UPDATE documents SET artifact_index_path = ?, ontology_dir = ?",
            (str(old_doc_dir / "artifact_index.json"), str(old_doc_dir)),
        )
        conn.execute("UPDATE objects SET artifact_path = ?", (str(old_doc_dir / "business_factors.jsonl"),))
        conn.execute("UPDATE edges SET artifact_path = ?", (str(old_doc_dir / "edges.jsonl"),))
        metadata = {
            "artifact_root": str(old_root),
            "build_settings": {
                "progress_log_path": str(old_root / "indexes" / "progress" / "source_artifact_sqlite" / "AAPL.jsonl")
            },
            "index_role": "company_shard",
        }
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('build', ?)",
            (json.dumps(metadata, sort_keys=True),),
        )

    spine_builder._rebase_company_shard_release_paths(shard_path, release_root=new_root)

    with sqlite3.connect(shard_path) as conn:
        artifact_index_path, ontology_dir = conn.execute(
            "SELECT artifact_index_path, ontology_dir FROM documents"
        ).fetchone()
        object_path = conn.execute("SELECT artifact_path FROM objects LIMIT 1").fetchone()[0]
        edge_path = conn.execute("SELECT artifact_path FROM edges LIMIT 1").fetchone()[0]
        metadata = json.loads(conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()[0])
    assert artifact_index_path == str(new_root / "companies" / "AAPL" / "ontology" / "10K" / "FY2025" / "artifact_index.json")
    assert ontology_dir == str(new_root / "companies" / "AAPL" / "ontology" / "10K" / "FY2025")
    assert object_path == str(new_root / "companies" / "AAPL" / "ontology" / "10K" / "FY2025" / "business_factors.jsonl")
    assert edge_path == str(new_root / "companies" / "AAPL" / "ontology" / "10K" / "FY2025" / "edges.jsonl")
    assert metadata["artifact_root"] == str(new_root)
    assert metadata["build_settings"]["progress_log_path"] == str(
        new_root / "indexes" / "progress" / "source_artifact_sqlite" / "AAPL.jsonl"
    )


def test_rebase_spine_fragment_updates_source_path_and_metadata(tmp_path: Path) -> None:
    old_root = tmp_path / "releases" / "dev" / "old-release"
    new_root = tmp_path / "releases" / "prod" / "new-release"
    fragment_path = tmp_path / "AAPL-fragment.sqlite"
    _write_spine_fragment(
        fragment_path,
        ticker="AAPL",
        topic_key="ai_capex_device_demand",
        topic_label="AI capex device demand",
    )
    old_source_path = old_root / "companies" / "AAPL" / "context" / "artifact_index.json"
    old_shard_path = old_root / "indexes" / "companies" / "AAPL.sqlite"
    new_shard_path = new_root / "indexes" / "companies" / "AAPL.sqlite"
    with sqlite3.connect(fragment_path) as conn:
        conn.execute("UPDATE global_document_catalog SET source_path = ?", (str(old_source_path),))
        write_global_spine_metadata(
            conn,
            {
                "format": "krw-ontology-spine-fragment/v1",
                "ticker": "AAPL",
                "release_id": "old-release",
                "source_shard_path": str(old_shard_path),
                "shard_path": "indexes/companies/AAPL.sqlite",
            },
        )

    spine_builder._rebase_spine_fragment_release_paths(
        fragment_path,
        release_root=new_root,
        release_id="new-release",
        source_shard_path=new_shard_path,
        shard_path_in_release="indexes/companies/AAPL.sqlite",
    )

    with sqlite3.connect(fragment_path) as conn:
        source_path = conn.execute("SELECT source_path FROM global_document_catalog").fetchone()[0]
        metadata = json.loads(conn.execute("SELECT value_json FROM metadata WHERE key = 'release_id'").fetchone()[0])
        source_shard_path = json.loads(
            conn.execute("SELECT value_json FROM metadata WHERE key = 'source_shard_path'").fetchone()[0]
        )
    assert source_path == str(new_root / "companies" / "AAPL" / "context" / "artifact_index.json")
    assert metadata == "new-release"
    assert source_shard_path == str(new_shard_path.resolve())


def _write_spine_fragment(path: Path, *, ticker: str, topic_key: str, topic_label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    object_id = f"obj:{ticker}:factor"
    metric_id = f"obj:{ticker}:metric"
    with sqlite3.connect(path) as conn:
        create_global_spine_schema(conn)
        write_global_spine_metadata(conn, {"format": "krw-ontology-spine-fragment/v1", "ticker": ticker})
        conn.execute(
            """
            INSERT INTO global_document_catalog(
                document_id, ticker, document_type, period, shard_id, shard_path
            )
            VALUES(?, ?, '10-K', 'FY2025', ?, ?)
            """,
            (f"{ticker}:10K:FY2025", ticker, ticker, f"indexes/companies/{ticker}.sqlite"),
        )
        for item in (object_id, metric_id):
            conn.execute(
                """
                INSERT INTO global_object_locator(
                    object_id, ticker, document_id, document_type, period,
                    object_type, shard_id, shard_path, local_object_key,
                    compact_label, compact_summary
                )
                VALUES(?, ?, ?, '10-K', 'FY2025', 'BusinessFactor', ?, ?, ?,
                       ?, ?)
                """,
                (
                    item,
                    ticker,
                    f"{ticker}:10K:FY2025",
                    ticker,
                    f"indexes/companies/{ticker}.sqlite",
                    item,
                    f"{ticker} AI capex",
                    f"{ticker} AI capex cloud demand",
                ),
            )
        conn.execute(
            """
            INSERT INTO global_factor_spine(
                factor_key, factor_label, factor_family, ticker, object_id,
                document_id, impact_channel, materiality, evidence_grade, shard_id
            )
            VALUES('ai_capex', 'AI capex', 'growth', ?, ?, ?, 'revenue', 0.9, 'high', ?)
            """,
            (ticker, object_id, f"{ticker}:10K:FY2025", ticker),
        )
        conn.execute(
            """
            INSERT INTO global_metric_spine(
                canonical_metric_key, metric_name, ticker, object_id,
                document_id, period, document_type, confidence, shard_id
            )
            VALUES('revenue', 'Revenue', ?, ?, ?, 'FY2025', '10-K', 0.9, ?)
            """,
            (ticker, metric_id, f"{ticker}:10K:FY2025", ticker),
        )
        conn.execute(
            """
            INSERT INTO global_topic_spine(
                topic_id, topic_key, topic_label, topic_family, topic_summary,
                ticker, source_object_ids, factor_terms, metric_terms,
                entity_terms, mechanism_terms, impact_channels,
                evidence_grade, materiality, shard_id
            )
            VALUES(?, ?, ?, 'growth', ?, ?, ?, '["AI capex", "cloud"]',
                   '["revenue"]', '["data center"]', '["demand"]',
                   '["revenue"]', 'high', 0.9, ?)
            """,
            (
                f"topic:{ticker}:ai",
                topic_key,
                topic_label,
                f"{topic_label} links AI capex cloud demand.",
                ticker,
                json.dumps([object_id, metric_id]),
                ticker,
            ),
        )


def test_merge_spine_fragments_builds_global_spine_and_chain_links(tmp_path: Path) -> None:
    aapl_fragment = tmp_path / "fragments" / "AAPL.sqlite"
    msft_fragment = tmp_path / "fragments" / "MSFT.sqlite"
    _write_spine_fragment(
        aapl_fragment,
        ticker="AAPL",
        topic_key="ai_capex_device_demand",
        topic_label="AI capex device demand",
    )
    _write_spine_fragment(
        msft_fragment,
        ticker="MSFT",
        topic_key="cloud_ai_capex_infrastructure",
        topic_label="Cloud AI capex infrastructure demand",
    )

    result = merge_spine_fragments(
        [msft_fragment, aapl_fragment],
        tmp_path / "release" / "indexes" / "global_spine.sqlite",
        release_id="test-release",
        created_at="2026-06-12T00:00:00+00:00",
    )

    assert result.fragment_count == 2
    assert result.verification["ok"] is True, result.verification["errors"]
    assert result.counts["global_object_locator"] == 4
    assert result.chain_links.inserted > 0
    with sqlite3.connect(result.global_spine_path) as conn:
        link_types = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT link_type FROM global_chain_index"
            ).fetchall()
        }
        pair = conn.execute(
            """
            SELECT from_ticker, to_ticker
            FROM global_chain_index
            WHERE shared_key = 'ai_capex'
            LIMIT 1
            """
        ).fetchone()
        metadata = json.loads(conn.execute("SELECT value_json FROM metadata WHERE key = 'chain_links'").fetchone()[0])
    assert "shared_factor" in link_types
    assert "similar_topic" in link_types
    assert pair == ("AAPL", "MSFT")
    assert metadata["inserted"] == result.chain_links.inserted


def test_verify_spine_shard_release_checks_global_and_shard_consistency(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    (release_root / "companies" / "AAPL").mkdir(parents=True)
    shard_path = release_root / "indexes" / "companies" / "AAPL.sqlite"
    _write_synthetic_company_shard(shard_path)
    fragment_path = release_root / "indexes" / "fragments" / "spine" / "AAPL.sqlite"
    emit_spine_fragment_from_company_shard(
        shard_path,
        fragment_path,
        ticker="AAPL",
        shard_path_in_release="indexes/companies/AAPL.sqlite",
    )
    merge_spine_fragments(
        [fragment_path],
        release_root / "indexes" / "global_spine.sqlite",
        release_id="test-release",
        created_at="2026-06-12T00:00:00+00:00",
    )
    (release_root / "indexes" / "shard_manifest.json").write_text(
        json.dumps(
            {
                "format": "krw-ontology-shard-manifest/v3",
                "shards": {
                    "AAPL": {
                        "path": "companies/AAPL.sqlite",
                        "quality_summary": _company_shard_quality_summary(shard_path),
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    from krw_ontology.release import verify_release_root, write_release_manifest_v3

    write_release_manifest_v3(release_root, release_id="test-release", env="dev")

    result = verify_spine_shard_release(release_root)
    release_result = verify_release_root(release_root, env="dev")

    assert result["ok"] is True, result["errors"]
    assert release_result["ok"] is True, release_result["errors"]
    assert release_result["verification_mode"] == "release-root-v3-light"
    assert result["counts"]["global_object_locator"] == 2
    assert set(result["shards"]) == {"AAPL"}


def test_verify_release_root_light_skips_endpoint_deep_scan(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    (release_root / "companies" / "AAPL").mkdir(parents=True)
    shard_path = release_root / "indexes" / "companies" / "AAPL.sqlite"
    _write_synthetic_company_shard(shard_path)
    fragment_path = release_root / "indexes" / "fragments" / "spine" / "AAPL.sqlite"
    emit_spine_fragment_from_company_shard(
        shard_path,
        fragment_path,
        ticker="AAPL",
        shard_path_in_release="indexes/companies/AAPL.sqlite",
    )
    global_spine_path = release_root / "indexes" / "global_spine.sqlite"
    merge_spine_fragments(
        [fragment_path],
        global_spine_path,
        release_id="test-release",
        created_at="2026-06-12T00:00:00+00:00",
    )
    with sqlite3.connect(global_spine_path) as conn:
        conn.execute(
            """
            INSERT INTO global_edge_spine(
                edge_id, from_object_id, to_object_id, from_ticker, to_ticker,
                relation_type, edge_scope, source_object_type, target_object_type,
                confidence, evidence_grade, materiality, recency_score,
                shard_hint, compact_reason
            )
            VALUES(
                'edge:broken:endpoint', 'obj:AAPL:missing', 'obj:AAPL:metric',
                'AAPL', 'AAPL', 'supports', 'intra_company', NULL, NULL,
                NULL, NULL, NULL, NULL, 'indexes/companies/AAPL.sqlite', 'test'
            )
            """
        )
    (release_root / "indexes" / "shard_manifest.json").write_text(
        json.dumps(
            {
                "format": "krw-ontology-shard-manifest/v3",
                "shards": {
                    "AAPL": {
                        "path": "companies/AAPL.sqlite",
                        "quality_summary": _company_shard_quality_summary(shard_path),
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    from krw_ontology.release import verify_release_root, write_release_manifest_v3

    write_release_manifest_v3(release_root, release_id="test-release", env="dev")

    light_result = verify_release_root(release_root, env="dev")
    deep_result = verify_release_root(release_root, env="dev", deep=True)

    assert light_result["ok"] is True, light_result["errors"]
    assert light_result["verification_mode"] == "release-root-v3-light"
    assert light_result["spine_shard_verification"]["deep"] is False
    assert deep_result["ok"] is False
    assert any(error.startswith("spine_shard:edge_from_endpoint_missing:") for error in deep_result["errors"])


def test_verify_spine_shard_release_rejects_stale_quality_summary(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    (release_root / "companies" / "AAPL").mkdir(parents=True)
    shard_path = release_root / "indexes" / "companies" / "AAPL.sqlite"
    _write_synthetic_company_shard(shard_path)
    fragment_path = release_root / "indexes" / "fragments" / "spine" / "AAPL.sqlite"
    emit_spine_fragment_from_company_shard(
        shard_path,
        fragment_path,
        ticker="AAPL",
        shard_path_in_release="indexes/companies/AAPL.sqlite",
    )
    merge_spine_fragments(
        [fragment_path],
        release_root / "indexes" / "global_spine.sqlite",
        release_id="test-release",
        created_at="2026-06-12T00:00:00+00:00",
    )
    quality_summary = _company_shard_quality_summary(shard_path)
    quality_summary["totals"]["documents"] = 999
    (release_root / "indexes" / "shard_manifest.json").write_text(
        json.dumps(
            {
                "format": "krw-ontology-shard-manifest/v3",
                "shards": {
                    "AAPL": {
                        "path": "companies/AAPL.sqlite",
                        "quality_summary": quality_summary,
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    from krw_ontology.release import write_release_manifest_v3

    write_release_manifest_v3(release_root, release_id="test-release", env="dev")

    result = verify_spine_shard_release(release_root)

    assert result["ok"] is False
    assert "shard:AAPL:quality_summary_mismatch" in result["errors"]
