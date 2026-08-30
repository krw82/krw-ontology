from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import krw_ontology.agent_index.builder as agent_index_builder
import krw_ontology.agent_index.spine_builder as spine_builder
import krw_ontology.agent_index.spine_verify as spine_verify
from krw_ontology.agent_index.cache_seal import read_immutable_sqlite_cache_sha256
from krw_ontology.agent_index.chart_series import build_chart_series_index
from krw_ontology.agent_index.router_coherence import build_router_coherence
from krw_ontology.agent_index.router_sidecar import build_router_sidecar, verify_router_sidecar
from krw_ontology.agent_index.spine_router import OntologySpineRouter
from krw_ontology.agent_index.metric_dictionary import metric_dictionary_binding
from krw_ontology.agent_index.source_artifact_sqlite import (
    SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
    SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
)
from krw_ontology.agent_index.spine_builder import (
    build_spine_shard_release_outputs,
    build_company_shard_direct,
    emit_spine_fragment_from_company_shard,
    merge_spine_fragments,
    plan_spine_shard_release_outputs,
    _company_shard_quality_summary,
)
from krw_ontology.agent_index.spine_preflight import (
    preflight_company_shard_identities,
    preflight_spine_fragments,
)
from krw_ontology.agent_index.spine_schema import (
    SPINE_FRAGMENT_SCHEMA_VERSION,
    create_global_spine_schema,
    create_spine_fragment_schema,
    verify_global_spine_schema,
    verify_spine_fragment_schema,
    write_global_spine_metadata,
    write_spine_verification_seal,
)
from krw_ontology.agent_index.spine_verify import verify_spine_shard_release


def test_spine_router_object_prefix_lookup_is_index_bounded(tmp_path: Path) -> None:
    indexes_dir = tmp_path / "indexes"
    companies_dir = indexes_dir / "companies"
    companies_dir.mkdir(parents=True)
    shard_path = companies_dir / "AAPL.sqlite"
    shard_path.touch()
    global_spine_path = indexes_dir / "global_spine.sqlite"
    local_object_key = "validation_report:UNKNOWN:UNKNOWN:UNKNOWN:with_edges"
    projected_object_id = f"scoped:AAPL:{local_object_key}"

    with sqlite3.connect(global_spine_path) as conn:
        create_global_spine_schema(conn)
        conn.executemany(
            """
            INSERT INTO global_object_locator(
                object_id, ticker, shard_id, shard_path, local_object_key,
                object_type, occurrence_count
            )
            VALUES(?, 'ZZZ', 'ZZZ', 'indexes/companies/ZZZ.sqlite', ?,
                   'BusinessFactor', 1)
            """,
            ((f"dummy:{index:06d}", f"dummy:{index:06d}") for index in range(50_000)),
        )
        conn.execute(
            """
            INSERT INTO global_object_locator(
                object_id, ticker, document_id, document_type, period,
                object_type, shard_id, shard_path, local_object_key,
                occurrence_count
            )
            VALUES(?, 'AAPL', 'doc:AAPL', '10-K', 'FY2025',
                   'ValidationReport', 'AAPL', ?, ?, 1)
            """,
            (projected_object_id, str(shard_path), local_object_key),
        )
        conn.execute(
            """
            INSERT INTO global_object_replica(
                object_id, ticker, document_id, document_type, period,
                shard_id, shard_path, object_type, local_object_key
            )
            VALUES(?, 'AAPL', 'doc:AAPL', '10-K', 'FY2025',
                   'AAPL', ?, 'ValidationReport', ?)
            """,
            (projected_object_id, str(shard_path), local_object_key),
        )

    (indexes_dir / "shard_manifest.json").write_text(
        json.dumps({"shards": {"AAPL": {"path": str(shard_path)}}}),
        encoding="utf-8",
    )

    progress_callbacks = 0

    def stop_unbounded_scan() -> int:
        nonlocal progress_callbacks
        progress_callbacks += 1
        return int(progress_callbacks > 2_000)

    with OntologySpineRouter(global_spine_path) as router:
        router.conn.set_progress_handler(stop_unbounded_scan, 100)
        candidates = router.find_object_ids(
            "validation_report:UNKNOWN",
            ticker="AAPL",
        )
        missing = router.find_object_ids(
            "definitely-not-an-object",
            ticker="AAPL",
        )

    assert [candidate["id"] for candidate in candidates] == [projected_object_id]
    assert missing == []
    assert progress_callbacks <= 2_000


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
                        "source_artifact_sqlite_schema_version": agent_index_builder.AGENT_INDEX_SCHEMA_VERSION,
                        "source_artifact_sqlite_builder_version": agent_index_builder.SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
                        "metric_dictionary": metric_dictionary_binding(),
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
                filing_period, observation_period, observation_period_type,
                observation_start_date, observation_end_date, observation_context_key,
                metric_name, canonical_metric, value_text, value_numeric, unit, dimensions_json,
                is_company_total, trace_status, metric_lineage_status, text
            )
            VALUES('obj:AAPL:metric', 'MetricObservation', 'AAPL', '10-K', '10K', 'FY2025',
                   'FY2025', 'FY2025', 'annual', '2025-01-01', '2025-12-31',
                   'ctx-aapl-fy2025', 'Revenue', 'revenue', '100', 100.0, 'USD', '{}', 1,
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


def test_plan_spine_shard_release_outputs_is_read_only(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    _write_minimal_context_artifact(release_root, "AAPL")
    source_manifest_path = release_root / "source_manifest.json"
    source_manifest_path.write_text('{"sentinel": true}\n', encoding="utf-8")
    original_bytes = source_manifest_path.read_bytes()

    plan = plan_spine_shard_release_outputs(
        release_root,
        release_id="preview-only",
        workers=1,
        source_manifest_path=source_manifest_path,
        no_cache=True,
    )

    assert source_manifest_path.read_bytes() == original_bytes
    assert plan["source_manifest"]["artifact_count"] == 1
    assert plan["source_manifest"]["manifest_hash"] == plan["source_manifest_hash"]
    assert plan["plan"]["discovery_mode"] == "in-memory-source-manifest"
    assert not (release_root / "indexes").exists()


def test_clone_or_copy_file_falls_back_to_isolated_full_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.sqlite"
    target_path = tmp_path / "target.sqlite"
    source_path.write_bytes(b"immutable-sqlite-bytes")
    monkeypatch.setenv("KRW_INDEX_COPY_MODE", "copy")

    copy_mode = spine_builder._clone_or_copy_file(source_path, target_path)

    assert copy_mode == "copy"
    assert target_path.read_bytes() == source_path.read_bytes()
    target_path.write_bytes(b"changed-target")
    assert source_path.read_bytes() == b"immutable-sqlite-bytes"


def test_clone_or_copy_file_can_require_reflink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.sqlite"
    target_path = tmp_path / "target.sqlite"
    source_path.write_bytes(b"immutable-sqlite-bytes")
    monkeypatch.setenv("KRW_INDEX_COPY_MODE", "reflink-required")
    monkeypatch.setattr(spine_builder, "_try_reflink_copy", lambda *_args: False)

    with pytest.raises(RuntimeError, match="reflink_required_but_unavailable"):
        spine_builder._clone_or_copy_file(source_path, target_path)

    assert not target_path.exists()


def test_clone_or_copy_immutable_tree_fallback_preserves_ignore_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source"
    target_path = tmp_path / "target"
    (source_path / "nested" / ".krw_pipeline").mkdir(parents=True)
    (source_path / "nested" / "keep.json").write_text("keep", encoding="utf-8")
    (source_path / "nested" / ".krw_pipeline" / "ignore.json").write_text(
        "ignore",
        encoding="utf-8",
    )
    monkeypatch.setenv("KRW_INDEX_COPY_MODE", "copy")

    copy_mode = spine_builder.clone_or_copy_immutable_tree(
        source_path,
        target_path,
        ignored_names=(".krw_pipeline",),
    )

    assert copy_mode == "copy"
    assert (target_path / "nested" / "keep.json").read_text(encoding="utf-8") == "keep"
    assert not (target_path / "nested" / ".krw_pipeline").exists()


def test_global_merge_sqlite_threads_are_bounded_by_cpu_and_compile_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(spine_builder, "_sqlite_compile_max_worker_threads", lambda _conn: 8)
    with sqlite3.connect(":memory:") as conn:
        assert (
            spine_builder._global_merge_sqlite_thread_limit(
                conn,
                requested=99,
                cpu_count=16,
            )
            == 8
        )
        assert (
            spine_builder._global_merge_sqlite_thread_limit(
                conn,
                requested=99,
                cpu_count=4,
            )
            == 3
        )
        assert (
            spine_builder._global_merge_sqlite_thread_limit(
                conn,
                requested=99,
                cpu_count=1,
            )
            == 0
        )


def test_global_merge_connection_uses_file_temp_store_and_bounded_threads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        spine_builder,
        "_global_merge_sqlite_thread_limit",
        lambda _conn: 2,
    )
    with sqlite3.connect(tmp_path / "merge.sqlite") as conn:
        settings = spine_builder._configure_global_merge_connection(conn)

    assert settings["journal_mode"] == "off"
    assert settings["synchronous"] == 0
    assert settings["temp_store"] == 1
    assert settings["cache_size_kib"] == spine_builder.DEFAULT_GLOBAL_MERGE_SQLITE_CACHE_KIB
    assert settings["sqlite_version"] == sqlite3.sqlite_version
    assert int(settings["compiled_max_worker_threads"]) >= int(settings["threads"])
    assert 0 <= int(settings["threads"]) <= 2


def test_static_build_runtime_preflight_requires_json1_and_fts5() -> None:
    capabilities = spine_builder._validate_static_build_runtime()

    assert capabilities["json1"] is True
    assert capabilities["fts5"] is True
    assert capabilities["sqlite_version"] == sqlite3.sqlite_version


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
    verification = verify_spine_fragment_schema(fragment_path)

    assert result.ticker == "AAPL"
    assert verification["ok"] is True, verification["errors"]
    assert verification["verification_mode"] == "deep-sealed"
    assert verification["integrity_source"] == "immutable_seal"
    with sqlite3.connect(fragment_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM global_object_locator").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM global_document_catalog").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_edge_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_factor_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_topic_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_metric_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_counterparty_spine").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM global_entity_spine").fetchone()[0] == 1
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        route = conn.execute(
            "SELECT shard_path FROM global_object_locator WHERE object_id = 'obj:AAPL:factor'"
        ).fetchone()[0]
        metric_period, metric_document_id = conn.execute(
            """
            SELECT period, document_id
            FROM global_metric_spine
            WHERE object_id = 'obj:AAPL:metric'
            """
        ).fetchone()
    assert route == "indexes/companies/AAPL.sqlite"
    assert "global_key_stats" not in tables
    assert "global_chain_index" not in tables
    assert "global_search_fts" not in tables
    assert metric_period == "FY2025"
    assert metric_document_id == "AAPL:10K:FY2025"


def test_fragment_temp_verification_failure_preserves_existing_valid_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shard_path = tmp_path / "indexes" / "companies" / "AAPL.sqlite"
    fragment_path = tmp_path / "indexes" / "fragments" / "spine" / "AAPL.sqlite"
    _write_synthetic_company_shard(shard_path)
    emit_spine_fragment_from_company_shard(
        shard_path,
        fragment_path,
        ticker="AAPL",
        release_id="first-release",
    )
    seal_path = spine_builder.spine_verification_seal_path(fragment_path)
    original_bytes = fragment_path.read_bytes()
    original_seal = seal_path.read_bytes()
    verified_paths: list[Path] = []

    def reject_temp(path: Path, **_kwargs: object) -> dict[str, object]:
        verified_paths.append(Path(path))
        return {
            "ok": False,
            "errors": ["forced_fragment_temp_verification_failure"],
        }

    monkeypatch.setattr(spine_builder, "verify_spine_fragment_schema", reject_temp)

    with pytest.raises(RuntimeError, match="forced_fragment_temp_verification_failure"):
        emit_spine_fragment_from_company_shard(
            shard_path,
            fragment_path,
            ticker="AAPL",
            release_id="second-release",
        )

    assert len(verified_paths) == 1
    assert verified_paths[0] != fragment_path
    assert fragment_path.read_bytes() == original_bytes
    assert seal_path.read_bytes() == original_seal


def test_cleanup_stale_spine_temp_removes_database_and_sidecars(tmp_path: Path) -> None:
    target = tmp_path / "global_spine.sqlite"
    stale = tmp_path / ".global_spine.sqlite.123.456.tmp"
    stale_wal = Path(str(stale) + "-wal")
    orphan = tmp_path / ".global_spine.sqlite.789.012.tmp"
    orphan_shm = Path(str(orphan) + "-shm")
    stale.write_bytes(b"stale")
    stale_wal.write_bytes(b"stale-wal")
    orphan_shm.write_bytes(b"orphan-shm")

    removed = spine_builder._cleanup_stale_spine_temps(
        target,
        older_than_seconds=0,
    )

    assert stale.name in removed
    assert orphan.name in removed
    assert not stale.exists()
    assert not stale_wal.exists()
    assert not orphan_shm.exists()


def test_emit_spine_fragment_skips_and_reports_edges_with_missing_endpoints(
    tmp_path: Path,
) -> None:
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

    company_preflight = preflight_company_shard_identities({"AAPL": shard_path})
    assert company_preflight["ok"] is True
    assert company_preflight["warning_kinds"] == {"dangling_edges_skipped": 1}

    emit_spine_fragment_from_company_shard(
        shard_path,
        fragment_path,
        ticker="AAPL",
        release_id="test-release",
        shard_path_in_release="indexes/companies/AAPL.sqlite",
    )
    with sqlite3.connect(fragment_path) as conn:
        edges = conn.execute("SELECT edge_id FROM global_edge_spine ORDER BY edge_id").fetchall()
        quality = json.loads(
            conn.execute(
                "SELECT value_json FROM metadata WHERE key = 'projection_quality'"
            ).fetchone()[0]
        )
    assert [row[0] for row in edges] == ["edge:AAPL:1"]
    assert quality == {
        "dangling_edges_skipped": 1,
        "status": "degraded",
        "topic_source_refs_skipped": 0,
    }


def test_occurrence_objects_with_unknown_source_ticker_are_namespaced_and_relinked(
    tmp_path: Path,
) -> None:
    fragments: list[Path] = []
    shards: dict[str, Path] = {}
    raw_validation_id = "validation_report:UNKNOWN:UNKNOWN:UNKNOWN:with_edges"
    for ticker, total_input in (("ECL", 1082), ("KR", 623)):
        shard_path = tmp_path / "companies" / f"{ticker}.sqlite"
        fragment_path = tmp_path / "fragments" / f"{ticker}.sqlite"
        _write_synthetic_company_shard(shard_path)
        shards[ticker] = shard_path
        with sqlite3.connect(shard_path) as conn:
            for table_name in (
                "documents",
                "objects",
                "object_search_text",
                "edges",
                "factor_lookup",
                "metric_lookup",
                "agreement_lookup",
                "company_topic_index",
            ):
                conn.execute(f"UPDATE {table_name} SET ticker = ?", (ticker,))
            conn.execute(
                """
                INSERT INTO objects(
                    id, type, ticker, document_type, doc_type_key, period,
                    source_document_id, section_name, review_status, confidence,
                    text, json, artifact_key, artifact_path
                )
                VALUES(?, 'ValidationReport', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                       'UNKNOWN', 'source:UNKNOWN:UNKNOWN:UNKNOWN', 'Validation',
                       'accepted', 'high', 'validation report', ?,
                       'validation_reports', 'validation_report.jsonl')
                """,
                (
                    raw_validation_id,
                    json.dumps(
                        {
                            "id": raw_validation_id,
                            "type": "ValidationReport",
                            "ticker": "UNKNOWN",
                            "total_input": total_input,
                        },
                        sort_keys=True,
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO edges(
                    id, ticker, document_type, doc_type_key, period,
                    from_id, to_id, relation_id, relation_name, confidence,
                    review_status, json, artifact_path
                )
                VALUES('edge:UNKNOWN:validation', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                       'UNKNOWN', ?, 'obj:AAPL:metric', 'validates', 'validates',
                       'high', 'accepted', '{}', 'edges.jsonl')
                """,
                (raw_validation_id,),
            )
            if ticker == "KR":
                conn.execute(
                    """
                    INSERT INTO edges(
                        id, ticker, document_type, doc_type_key, period,
                        from_id, to_id, relation_id, relation_name, confidence,
                        review_status, json, artifact_path
                    )
                    VALUES('edge:UNKNOWN:dangling', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                           'UNKNOWN', 'obj:UNKNOWN:missing', ?, 'validates', 'validates',
                           'high', 'accepted', '{}', 'edges.jsonl')
                    """,
                    (raw_validation_id,),
                )
            conn.execute(
                """
                INSERT INTO object_search_text(
                    object_id, type, ticker, document_type, period,
                    text_self, text_support, text_related, text_entities,
                    text_aliases, compact_text
                )
                VALUES(?, 'ValidationReport', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                       'validation report', '', '', '', '', 'validation report')
                """,
                (raw_validation_id,),
            )
            # object_fts is external content over object_search_text: rebuild
            # the index instead of hand-inserting rows (direct FTS inserts get
            # rowids that no longer match the content table).
            conn.execute("INSERT INTO object_fts(object_fts) VALUES('rebuild')")

        emit_spine_fragment_from_company_shard(
            shard_path,
            fragment_path,
            ticker=ticker,
            shard_path_in_release=f"indexes/companies/{ticker}.sqlite",
        )
        fragments.append(fragment_path)

        projected_id = f"scoped:{ticker}:{raw_validation_id}"
        with sqlite3.connect(fragment_path) as conn:
            locator = conn.execute(
                """
                SELECT object_id, ticker, local_object_key, document_id
                FROM global_object_locator
                WHERE local_object_key = ?
                """,
                (raw_validation_id,),
            ).fetchone()
            edge = conn.execute(
                """
                SELECT edge_id, ticker, from_object_id, to_object_id
                FROM global_edge_spine
                WHERE compact_reason = 'validates'
                """
            ).fetchone()
        assert locator == (
            projected_id,
            ticker,
            raw_validation_id,
            f"scoped:{ticker}:source:UNKNOWN:UNKNOWN:UNKNOWN",
        )
        assert edge == (
            f"scoped:{ticker}:edge:UNKNOWN:validation",
            ticker,
            projected_id,
            f"scoped:{ticker}:obj:AAPL:metric",
        )

    preflight = preflight_spine_fragments(fragments)
    assert preflight["ok"] is True, preflight["conflicts"]
    company_preflight = preflight_company_shard_identities(shards)
    assert company_preflight["ok"] is True, company_preflight["conflicts"]

    global_spine_path = tmp_path / "indexes" / "global_spine.sqlite"
    merge_spine_fragments(
        fragments,
        global_spine_path,
        release_id="scope-test",
        generate_links=False,
        semantic_preflight=False,
    )
    (global_spine_path.parent / "shard_manifest.json").write_text(
        json.dumps(
            {"shards": {ticker: {"path": str(path)} for ticker, path in shards.items()}},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    scoped_kr_id = f"scoped:KR:{raw_validation_id}"
    with OntologySpineRouter(global_spine_path) as router:
        resolved = router.get_object(scoped_kr_id)
        traced = router.trace(scoped_kr_id)
        rows, diagnostics = router.query_compact_with_diagnostics(
            tickers=["KR"],
            object_types=["ValidationReport"],
            limit=5,
        )
        planned_rows, planned_diagnostics = router.query_planned_compact_with_diagnostics(
            retrieval_query="validation report",
            tickers=["KR"],
            object_types=["ValidationReport"],
            limit=5,
        )
        prefix_candidates = router.find_object_ids(
            "validation_report:UNKNOWN",
            ticker="KR",
        )
        chained = router.chain(rows[0]["id"], ticker="KR")
    assert resolved is not None
    assert resolved["id"] == scoped_kr_id
    assert resolved["local_object_key"] == raw_validation_id
    assert traced is not None
    assert traced["object"]["id"] == scoped_kr_id
    assert traced["object"]["local_object_key"] == raw_validation_id
    assert traced["object_locator"]["object_id"] == scoped_kr_id
    assert diagnostics["result_count"] == 1
    assert rows[0]["id"] == scoped_kr_id
    assert rows[0]["trace_id"] == scoped_kr_id
    assert rows[0]["object"]["id"] == scoped_kr_id
    assert planned_diagnostics["result_count"] == 1
    assert planned_rows[0]["id"] == scoped_kr_id
    assert planned_rows[0]["ticker"] == "KR"
    assert [candidate["id"] for candidate in prefix_candidates] == [scoped_kr_id]
    assert chained is not None
    assert chained["object"]["id"] == scoped_kr_id

    kr_shard = shards["KR"]
    with sqlite3.connect(global_spine_path) as conn:
        conn.row_factory = sqlite3.Row
        shard_verification = spine_verify._verify_one_shard(
            conn,
            kr_shard,
            ticker="KR",
            schema_name="verify_kr",
            sample_limit=20,
            expected_sha256=hashlib.sha256(kr_shard.read_bytes()).hexdigest(),
            shard_entry={
                "quality_summary": _company_shard_quality_summary(kr_shard),
                "metric_dictionary": metric_dictionary_binding(),
            },
            expected_metric_dictionary=metric_dictionary_binding(),
        )
    assert shard_verification["ok"] is True, shard_verification["errors"]

    chart_result = build_chart_series_index(
        tmp_path,
        shard_manifest_path=global_spine_path.parent / "shard_manifest.json",
        output_path=tmp_path / "indexes" / "chart_series.sqlite",
        release_id="scope-test",
    )
    assert chart_result.verification["ok"] is True
    with sqlite3.connect(chart_result.path) as conn:
        chart_object_id = conn.execute(
            """
            SELECT object_id
            FROM chart_series_points
            WHERE ticker = 'KR'
            LIMIT 1
            """
        ).fetchone()[0]
    assert chart_object_id == "scoped:KR:obj:AAPL:metric"


def test_company_identity_preflight_rejects_concrete_cross_shard_ticker(
    tmp_path: Path,
) -> None:
    shard_path = tmp_path / "AAPL.sqlite"
    _write_synthetic_company_shard(shard_path)
    with sqlite3.connect(shard_path) as conn:
        conn.execute("UPDATE objects SET ticker = 'MSFT' WHERE id = 'obj:AAPL:factor'")

    result = preflight_company_shard_identities({"AAPL": shard_path})

    assert result["ok"] is False
    assert result["conflict_count"] == 1
    assert result["conflicts"][0]["fields"] == ["ticker_scope_mismatch"]


def test_company_identity_preflight_rejects_topic_projection_collision(
    tmp_path: Path,
) -> None:
    shard_path = tmp_path / "AAPL.sqlite"
    _write_synthetic_company_shard(shard_path)
    with sqlite3.connect(shard_path) as conn:
        for topic_id in ("raw-topic", "scoped:AAPL:raw-topic"):
            conn.execute(
                """
                INSERT INTO company_topic_index(
                    topic_id, ticker, period, document_type, doc_type_key,
                    topic_label, topic_summary, topic_family, primary_object_id,
                    primary_object_type, source_object_ids,
                    top_traceable_object_ids, untraced_object_ids,
                    dominant_object_types, impact_channels, factor_terms,
                    metric_terms, entity_terms, mechanism_terms, scenario_terms,
                    evidence_strength, materiality_score
                )
                SELECT ?, ticker, period, document_type, doc_type_key,
                       topic_label, topic_summary, topic_family, primary_object_id,
                       primary_object_type, source_object_ids,
                       top_traceable_object_ids, untraced_object_ids,
                       dominant_object_types, impact_channels, factor_terms,
                       metric_terms, entity_terms, mechanism_terms, scenario_terms,
                       evidence_strength, materiality_score
                FROM company_topic_index
                LIMIT 1
                """,
                (topic_id,),
            )

    result = preflight_company_shard_identities({"AAPL": shard_path})

    assert result["ok"] is False
    assert result["conflict_count"] == 1
    assert result["conflicts"][0]["fields"] == ["topic_projection_collision"]


def test_company_identity_preflight_normalizes_global_terms_but_keeps_core_fields_strict(
    tmp_path: Path,
) -> None:
    shards: dict[str, Path] = {}
    object_id = "term:factor:federal_funds_rate"
    for ticker, display_name in (("AAPL", "Federal Funds Rate"), ("MSFT", "federal_funds_rate")):
        shard_path = tmp_path / f"{ticker}.sqlite"
        shards[ticker] = shard_path
        with sqlite3.connect(shard_path) as conn:
            agent_index_builder._create_schema(conn)
            payload = {
                "id": object_id,
                "type": "TaxonomyTerm",
                "object_type": "TaxonomyTerm",
                "term_type": "factor",
                "display_name": display_name,
                "aliases": [display_name],
                "schema_version": "v1",
            }
            conn.execute(
                """
                INSERT INTO objects(
                    id, type, ticker, document_type, doc_type_key, period,
                    text, json, artifact_key, artifact_path
                )
                VALUES(?, 'TaxonomyTerm', ?, '10-K', '10K', 'FY2025',
                       ?, ?, 'terms', 'terms.jsonl')
                """,
                (object_id, ticker, display_name, json.dumps(payload, sort_keys=True)),
            )

    compatible = preflight_company_shard_identities(shards)
    assert compatible["ok"] is True, compatible["conflicts"]

    with sqlite3.connect(shards["MSFT"]) as conn:
        payload = json.loads(conn.execute("SELECT json FROM objects").fetchone()[0])
        payload["term_type"] = "channel"
        conn.execute("UPDATE objects SET json = ?", (json.dumps(payload, sort_keys=True),))

    conflicting = preflight_company_shard_identities(shards)
    assert conflicting["ok"] is False
    assert conflicting["conflict_count"] == 1
    assert conflicting["conflicts"][0]["fields"] == ["semantic_hash"]


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
        metadata = json.loads(
            conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()[0]
        )
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert metadata["index_role"] == "company_shard"
    assert metadata["shard_ticker"] == "AAPL"
    assert metadata["index_layout"] == "global-spine-and-company-shards"
    assert (
        metadata["source_artifact_sqlite_schema_version"] == SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION
    )
    assert (
        metadata["source_artifact_sqlite_builder_version"] == SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION
    )
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
    assert result.router_sidecar_path.exists()
    assert result.shard_manifest_path.exists()
    assert result.build_plan_path.exists()
    assert result.build_summary_path.exists()
    assert result.chart_series_path is not None
    assert result.chart_series_path.exists()
    assert result.progress_path is not None
    assert result.progress_path.exists()
    assert (release_root / "source_manifest.json").exists()
    assert not (release_root / "indexes" / "agent_index.sqlite").exists()
    assert not (release_root / "indexes" / "fragments" / "spine").exists()
    assert not (release_root / "indexes" / "fragments").exists()
    assert sorted(result.shard_manifest["shards"]) == ["AAPL"]
    assert result.shard_manifest["metric_dictionary"] == metric_dictionary_binding()
    shard_entry = result.shard_manifest["shards"]["AAPL"]
    assert shard_entry["metric_dictionary"] == metric_dictionary_binding()
    assert shard_entry["quality_summary"]["format"] == "krw-ontology-shard-quality-summary/v1"
    assert shard_entry["quality_summary"]["totals"]["documents"] == 1
    assert shard_entry["quality_summary"]["ticker_quality"][0]["ticker"] == "AAPL"
    assert result.build_summary["company_count"] == 1
    assert result.build_summary["progress_path"] == "indexes/build_progress.jsonl"
    assert result.build_summary["chart_series"]["status"] == "complete"
    assert result.build_summary["router_sidecar"]["verification"]["ok"] is True
    assert result.build_summary["router_sidecar"]["ranking_profile_sha256"]
    assert result.build_summary["chart_series"]["path"] == "indexes/chart_series.sqlite"
    assert result.build_summary["chart_series"]["verification"]["ok"] is True
    assert result.build_summary["artifact_cleanup"]["spine_fragments"]["removed"] is True
    assert result.build_summary["artifact_cleanup"]["spine_fragments"]["file_count"] >= 1
    assert result.merge_result.verification["ok"] is True
    shard_verification = spine_builder.verify_source_artifact_sqlite(
        result.shard_results[0].shard_path
    )
    assert shard_verification["integrity_source"] == "immutable_cache_seal"
    assert (
        read_immutable_sqlite_cache_sha256(result.shard_results[0].shard_path)
        == result.shard_manifest["shards"]["AAPL"]["sha256"]
    )
    sealed_router_verification = verify_router_sidecar(
        result.router_sidecar_path,
        expected_release_id="test-release",
        deep=True,
    )
    assert sealed_router_verification["verification_mode"] == "router-sidecar-deep-sealed"
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
    assert ("chart_series", "chart_series", "complete") in event_keys
    assert ("artifact_cleanup", "spine_fragments", "complete") in event_keys
    assert ("build_summary", "build_summary", "complete") in event_keys
    assert ("build", "build", "complete") in event_keys
    company_event = next(
        event
        for event in progress_events
        if event["node_id"] == "company_shard:AAPL" and event["status"] == "rebuilt"
    )
    assert company_event["cache_hit"] is False
    assert company_event["output"] == "indexes/companies/AAPL.sqlite"
    from krw_ontology.release import verify_release_root, write_release_manifest_v3

    build_router_sidecar(
        release_root / "indexes" / "global_spine.sqlite",
        release_id="test-release",
    )
    release_manifest = write_release_manifest_v3(
        release_root,
        release_id="test-release",
        env="dev",
    )
    verification = verify_release_root(release_root, env="dev")

    assert verification["ok"] is True, verification["errors"]
    assert release_manifest["metric_dictionary"] == metric_dictionary_binding()
    assert verification["verification_mode"] == "release-root-v3-light"
    assert verification["chart_series_verification"]["ok"] is True


def test_build_reuses_exact_global_spine_semantic_cache(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    first_root = tmp_path / "releases" / "dev" / "first"
    second_root = tmp_path / "releases" / "dev" / "second"
    _write_minimal_context_artifact(first_root, "AAPL")
    _write_minimal_context_artifact(second_root, "AAPL")

    first = build_spine_shard_release_outputs(
        first_root,
        release_id="first",
        cache_root=cache_root,
        workers=1,
        generate_links=True,
    )
    second = build_spine_shard_release_outputs(
        second_root,
        release_id="second",
        cache_root=cache_root,
        workers=1,
        generate_links=True,
    )

    first_cache = first.build_summary["global_spine"]["cache"]
    second_cache = second.build_summary["global_spine"]["cache"]
    assert first_cache["hit"] is False
    assert first_cache["publish_mode"] in {"reflink", "copy", "existing"}
    assert second_cache["hit"] is True
    assert second_cache["key"] == first_cache["key"]
    assert second.build_summary["semantic_preflight"]["cached"] is True
    assert second.merge_result.verification["runtime"]["cache_hit"] is True
    assert second.merge_result.counts == first.merge_result.counts
    assert second.build_summary["router_sidecar"]["cache"]["hit"] is True
    restored_router_verification = verify_router_sidecar(
        second.router_sidecar_path,
        expected_release_id="second",
        deep=True,
    )
    assert restored_router_verification["verification_mode"] == "router-sidecar-deep-sealed"
    with sqlite3.connect(second.global_spine_path) as conn:
        metadata = spine_builder.read_global_spine_metadata(conn)
    assert metadata["release_id"] == "second"
    assert metadata["global_spine_cache_key"] == second_cache["key"]


def test_build_bootstraps_global_spine_cache_from_compatible_current(tmp_path: Path) -> None:
    env_root = tmp_path / "releases" / "dev"
    cache_root = tmp_path / "cache"
    first_root = env_root / "first"
    second_root = env_root / "second"
    _write_minimal_context_artifact(first_root, "AAPL")
    _write_minimal_context_artifact(second_root, "AAPL")
    build_spine_shard_release_outputs(
        first_root,
        release_id="first",
        cache_root=cache_root,
        workers=1,
        no_cache=True,
        generate_links=True,
    )
    (env_root / "current").symlink_to("first")

    second = build_spine_shard_release_outputs(
        second_root,
        release_id="second",
        cache_root=cache_root,
        workers=1,
        generate_links=True,
    )

    cache = second.build_summary["global_spine"]["cache"]
    assert cache["hit"] is True
    assert cache["bootstrap"]["adopted"] is True
    assert cache["bootstrap"]["source_release"] == "first"
    assert second.merge_result.verification["runtime"]["cache_hit"] is True


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
        metadata = json.loads(
            conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()[0]
        )
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
        conn.execute(
            "UPDATE objects SET artifact_path = ?", (str(old_doc_dir / "business_factors.jsonl"),)
        )
        conn.execute("UPDATE edges SET artifact_path = ?", (str(old_doc_dir / "edges.jsonl"),))
        metadata = {
            "artifact_root": str(old_root),
            "build_settings": {
                "progress_log_path": str(
                    old_root / "indexes" / "progress" / "source_artifact_sqlite" / "AAPL.jsonl"
                )
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
        metadata = json.loads(
            conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()[0]
        )
    assert artifact_index_path == str(
        new_root / "companies" / "AAPL" / "ontology" / "10K" / "FY2025" / "artifact_index.json"
    )
    assert ontology_dir == str(new_root / "companies" / "AAPL" / "ontology" / "10K" / "FY2025")
    assert object_path == str(
        new_root / "companies" / "AAPL" / "ontology" / "10K" / "FY2025" / "business_factors.jsonl"
    )
    assert edge_path == str(
        new_root / "companies" / "AAPL" / "ontology" / "10K" / "FY2025" / "edges.jsonl"
    )
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
                "format": spine_builder.SPINE_FRAGMENT_FORMAT_VERSION,
                "fragment_schema_version": SPINE_FRAGMENT_SCHEMA_VERSION,
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
        metadata = json.loads(
            conn.execute("SELECT value_json FROM metadata WHERE key = 'release_id'").fetchone()[0]
        )
        source_shard_path = json.loads(
            conn.execute(
                "SELECT value_json FROM metadata WHERE key = 'source_shard_path'"
            ).fetchone()[0]
        )
    assert source_path == str(new_root / "companies" / "AAPL" / "context" / "artifact_index.json")
    assert metadata == "new-release"
    assert source_shard_path == str(new_shard_path.resolve())


def _write_spine_fragment(path: Path, *, ticker: str, topic_key: str, topic_label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    object_id = f"obj:{ticker}:factor"
    metric_id = f"obj:{ticker}:metric"
    with sqlite3.connect(path) as conn:
        create_spine_fragment_schema(conn)
        write_global_spine_metadata(
            conn,
            {
                "format": spine_builder.SPINE_FRAGMENT_FORMAT_VERSION,
                "fragment_schema_version": SPINE_FRAGMENT_SCHEMA_VERSION,
                "ticker": ticker,
            },
        )
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


def _add_shared_graph_occurrence(path: Path, *, ticker: str) -> None:
    document_id = f"{ticker}:10K:FY2025"
    shard_path = f"indexes/companies/{ticker}.sqlite"
    shared_objects = (
        ("term:factor:shared", "TaxonomyTerm", "semantic:shared-factor"),
        ("term:metric:shared", "TaxonomyTerm", "semantic:shared-metric"),
    )
    with sqlite3.connect(path) as conn:
        for object_id, object_type, semantic_hash in shared_objects:
            conn.execute(
                """
                INSERT INTO global_object_locator(
                    object_id, ticker, document_id, document_type, period,
                    object_type, shard_id, shard_path, local_object_key,
                    object_hash, semantic_hash, compact_label,
                    compact_summary, quality_status
                )
                VALUES(?, ?, ?, '10-K', 'FY2025', ?, ?, ?, ?, ?, ?, ?, ?, 'high')
                """,
                (
                    object_id,
                    ticker,
                    document_id,
                    object_type,
                    ticker,
                    shard_path,
                    object_id,
                    f"occurrence:{ticker}:{object_id}",
                    semantic_hash,
                    f"Shared {object_type}",
                    f"{ticker} occurrence of {object_id}",
                ),
            )
        conn.execute(
            """
            INSERT INTO global_edge_spine(
                edge_id, ticker, document_id, document_type, period,
                shard_id, shard_path, from_object_id, to_object_id,
                from_ticker, to_ticker, relation_type, edge_scope,
                source_object_type, target_object_type, confidence,
                evidence_grade, compact_reason, semantic_hash
            )
            VALUES(
                'edge:shared:factor-to-metric', ?, ?, '10-K', 'FY2025',
                ?, ?, 'term:factor:shared', 'term:metric:shared', ?, ?,
                'supports', 'intra_company', 'CanonicalEntity',
                'TaxonomyTerm', 0.9, 'high', ?, 'semantic:shared-edge'
            )
            """,
            (ticker, document_id, ticker, shard_path, ticker, ticker, f"{ticker} evidence"),
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

    progress_events: list[dict[str, object]] = []
    result = merge_spine_fragments(
        [msft_fragment, aapl_fragment],
        tmp_path / "release" / "indexes" / "global_spine.sqlite",
        release_id="test-release",
        created_at="2026-06-12T00:00:00+00:00",
        progress_callback=lambda event: progress_events.append(dict(event)),
    )

    assert result.fragment_count == 2
    assert result.verification["ok"] is True, result.verification["errors"]
    assert result.counts["global_object_locator"] == 4
    assert "global_search_fts" not in result.counts
    assert result.chain_links.inserted > 0
    assert result.verification["integrity_source"] == "immutable_seal"
    assert result.verification["deep_verification"]["integrity_source"] == "sqlite_integrity_check"
    runtime = result.verification["runtime"]
    assert runtime["sqlite"]["temp_store"] == 1
    assert (
        runtime["sqlite"]["cache_size_kib"] == spine_builder.DEFAULT_GLOBAL_MERGE_SQLITE_CACHE_KIB
    )
    assert 0 <= runtime["sqlite"]["threads"] <= runtime["sqlite"]["compiled_max_worker_threads"]
    assert {
        "fragment_verification",
        "fragment_merge",
        "chain_generation",
        "secondary_index_build",
        "deep_verification",
        "total",
    }.issubset(runtime["stage_timings_ms"])
    assert all(value >= 0 for value in runtime["stage_timings_ms"].values())
    with sqlite3.connect(result.global_spine_path) as conn:
        link_types = {
            row[0]
            for row in conn.execute("SELECT DISTINCT link_type FROM global_chain_index").fetchall()
        }
        pair = conn.execute(
            """
            SELECT from_ticker, to_ticker
            FROM global_chain_index
            WHERE shared_key = 'ai_capex'
            LIMIT 1
            """
        ).fetchone()
        metadata = json.loads(
            conn.execute("SELECT value_json FROM metadata WHERE key = 'chain_links'").fetchone()[0]
        )
        merge_runtime = json.loads(
            conn.execute("SELECT value_json FROM metadata WHERE key = 'merge_runtime'").fetchone()[
                0
            ]
        )
        legacy_search_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'global_search_fts'"
        ).fetchone()
    assert "shared_factor" in link_types
    assert "similar_topic" in link_types
    assert pair == ("AAPL", "MSFT")
    assert metadata["inserted"] == result.chain_links.inserted
    assert merge_runtime["sqlite"]["threads"] == runtime["sqlite"]["threads"]
    assert any(
        event.get("node_id") == "global_spine_merge:runtime" and event.get("status") == "configured"
        for event in progress_events
    )
    assert any(
        event.get("node_id") == "global_spine_merge:runtime" and event.get("status") == "complete"
        for event in progress_events
    )
    assert legacy_search_table is None


def test_merge_fails_fast_with_fragment_semantic_conflict_and_preserves_target(
    tmp_path: Path,
) -> None:
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
    with sqlite3.connect(msft_fragment) as conn:
        conn.execute(
            "UPDATE global_object_locator SET object_id = 'obj:AAPL:factor' "
            "WHERE object_id = 'obj:MSFT:factor'"
        )
    output = tmp_path / "release" / "indexes" / "global_spine.sqlite"
    output.parent.mkdir(parents=True)
    original_bytes = b"previous-verified-global-spine"
    output.write_bytes(original_bytes)

    with pytest.raises(spine_builder.SpineFragmentCollisionError) as exc_info:
        merge_spine_fragments(
            [aapl_fragment, msft_fragment],
            output,
            release_id="test-release",
        )

    message = str(exc_info.value)
    assert "spine_fragment_identity_scope_violation" in message
    assert "table=global_object_locator" in message
    assert 'key={"object_id":"obj:AAPL:factor"}' in message
    assert f"fragment={msft_fragment.resolve()}" in message
    assert "fields=identity_scope" in message
    assert output.read_bytes() == original_bytes


def test_merge_preserves_all_equivalent_object_and_edge_occurrences(tmp_path: Path) -> None:
    aapl_fragment = tmp_path / "fragments" / "Z_AAPL.sqlite"
    msft_fragment = tmp_path / "fragments" / "A_MSFT.sqlite"
    for path, ticker in ((aapl_fragment, "AAPL"), (msft_fragment, "MSFT")):
        _write_spine_fragment(
            path,
            ticker=ticker,
            topic_key=f"{ticker.lower()}_ai",
            topic_label=f"{ticker} AI",
        )
        _add_shared_graph_occurrence(path, ticker=ticker)

    result = merge_spine_fragments(
        [aapl_fragment, msft_fragment],
        tmp_path / "release" / "indexes" / "global_spine.sqlite",
        release_id="test-release",
        generate_links=False,
    )

    with sqlite3.connect(result.global_spine_path) as conn:
        object_canonical = conn.execute(
            """
            SELECT ticker, document_id, occurrence_count
            FROM global_object_locator
            WHERE object_id = 'term:factor:shared'
            """
        ).fetchone()
        edge_canonical = conn.execute(
            """
            SELECT ticker, document_id, occurrence_count
            FROM global_edge_spine
            WHERE edge_id = 'edge:shared:factor-to-metric'
            """
        ).fetchone()
        object_replicas = conn.execute(
            """
            SELECT ticker, document_id, shard_id, shard_path
            FROM global_object_replica
            WHERE object_id = 'term:factor:shared'
            ORDER BY ticker
            """
        ).fetchall()
        edge_replicas = conn.execute(
            """
            SELECT ticker, document_id, shard_id, shard_path
            FROM global_edge_replica
            WHERE edge_id = 'edge:shared:factor-to-metric'
            ORDER BY ticker
            """
        ).fetchall()
        duplicate_metadata = json.loads(
            conn.execute(
                "SELECT value_json FROM metadata WHERE key = 'benign_duplicate_counts'"
            ).fetchone()[0]
        )

    assert object_canonical == ("AAPL", "AAPL:10K:FY2025", 2)
    assert edge_canonical == ("AAPL", "AAPL:10K:FY2025", 2)
    assert object_replicas == [
        (
            "AAPL",
            "AAPL:10K:FY2025",
            "AAPL",
            "indexes/companies/AAPL.sqlite",
        ),
        (
            "MSFT",
            "MSFT:10K:FY2025",
            "MSFT",
            "indexes/companies/MSFT.sqlite",
        ),
    ]
    assert edge_replicas == object_replicas
    assert result.counts["global_object_replica"] == 8
    assert result.counts["global_edge_replica"] == 2
    assert result.counts["benign_duplicate_objects"] == 2
    assert result.counts["benign_duplicate_edges"] == 1
    assert result.counts["benign_duplicate_total"] == 3
    assert duplicate_metadata == {"edges": 1, "objects": 2, "total": 3}
    assert result.verification["benign_duplicate_counts"] == duplicate_metadata


def test_merge_fails_fast_on_shared_edge_semantic_conflict(tmp_path: Path) -> None:
    aapl_fragment = tmp_path / "fragments" / "AAPL.sqlite"
    msft_fragment = tmp_path / "fragments" / "MSFT.sqlite"
    for path, ticker in ((aapl_fragment, "AAPL"), (msft_fragment, "MSFT")):
        _write_spine_fragment(
            path,
            ticker=ticker,
            topic_key=f"{ticker.lower()}_ai",
            topic_label=f"{ticker} AI",
        )
        _add_shared_graph_occurrence(path, ticker=ticker)
    with sqlite3.connect(msft_fragment) as conn:
        conn.execute(
            """
            UPDATE global_edge_spine
            SET semantic_hash = 'semantic:conflicting-edge'
            WHERE edge_id = 'edge:shared:factor-to-metric'
            """
        )

    with pytest.raises(spine_builder.SpineFragmentCollisionError) as exc_info:
        merge_spine_fragments(
            [aapl_fragment, msft_fragment],
            tmp_path / "global_spine.sqlite",
            generate_links=False,
        )

    message = str(exc_info.value)
    assert "spine_fragment_semantic_conflict" in message
    assert "table=global_edge_spine" in message
    assert 'key={"edge_id":"edge:shared:factor-to-metric"}' in message
    assert f"fragment={msft_fragment.resolve()}" in message
    assert "fields=semantic_hash" in message


@pytest.mark.parametrize(
    ("table_name", "update_sql", "key_json"),
    (
        (
            "global_document_catalog",
            "UPDATE global_document_catalog SET document_id = 'AAPL:10K:FY2025'",
            '{"document_id":"AAPL:10K:FY2025"}',
        ),
        (
            "global_topic_spine",
            "UPDATE global_topic_spine SET topic_id = 'topic:AAPL:ai'",
            '{"topic_id":"topic:AAPL:ai"}',
        ),
    ),
)
def test_document_and_topic_primary_keys_remain_strict(
    tmp_path: Path,
    table_name: str,
    update_sql: str,
    key_json: str,
) -> None:
    aapl_fragment = tmp_path / "fragments" / "AAPL.sqlite"
    msft_fragment = tmp_path / "fragments" / "MSFT.sqlite"
    _write_spine_fragment(
        aapl_fragment,
        ticker="AAPL",
        topic_key="aapl_ai",
        topic_label="AAPL AI",
    )
    _write_spine_fragment(
        msft_fragment,
        ticker="MSFT",
        topic_key="msft_ai",
        topic_label="MSFT AI",
    )
    with sqlite3.connect(msft_fragment) as conn:
        conn.execute(update_sql)

    with pytest.raises(spine_builder.SpineFragmentCollisionError) as exc_info:
        merge_spine_fragments(
            [aapl_fragment, msft_fragment],
            tmp_path / "global_spine.sqlite",
            generate_links=False,
        )

    message = str(exc_info.value)
    assert "spine_fragment_identity_scope_violation" in message
    assert f"table={table_name}" in message
    assert f"key={key_json}" in message
    assert f"fragment={msft_fragment.resolve()}" in message


@pytest.mark.parametrize(
    "canonical_name",
    (
        "federal_funds_rate",
        "Federal_Funds_Rate",
        "federal funds rate",
        "Federal Funds Rate",
        "Federal Funds rate",
        "Ｆｅｄｅｒａｌ　Ｆｕｎｄｓ　Ｒａｔｅ",
    ),
)
def test_canonical_entity_semantic_hash_ignores_spelling_and_alias_variants(
    canonical_name: str,
) -> None:
    row = {
        "id": "entity:benchmark:federal_funds_rate",
        "type": "CanonicalEntity",
    }
    payload = {
        "id": row["id"],
        "type": "CanonicalEntity",
        "object_type": "CanonicalEntity",
        "entity_type": "benchmark",
        "canonical_name": canonical_name,
        "aliases": [canonical_name, "FEDERAL FUNDS RATE"],
        "status": "active",
        "schema_version": "v1",
    }
    semantic_hash = spine_builder._semantic_object_hash(row, payload)
    expected_payload = {**payload, "canonical_name": "federal_funds_rate", "aliases": []}
    expected_hash = spine_builder._semantic_object_hash(row, expected_payload)
    assert semantic_hash == expected_hash


def test_canonical_entity_semantic_hash_keeps_core_type_strict() -> None:
    row = {
        "id": "entity:benchmark:federal_funds_rate",
        "type": "CanonicalEntity",
    }
    base = {
        "id": row["id"],
        "type": "CanonicalEntity",
        "object_type": "CanonicalEntity",
        "canonical_name": "Federal Funds Rate",
        "status": "active",
        "schema_version": "v1",
    }
    benchmark_hash = spine_builder._semantic_object_hash(row, {**base, "entity_type": "benchmark"})
    factor_hash = spine_builder._semantic_object_hash(row, {**base, "entity_type": "factor"})
    assert benchmark_hash != factor_hash


@pytest.mark.parametrize(
    "display_name",
    (
        "financial_condition",
        "financial condition",
        "Financial-Condition",
        "Ｆｉｎａｎｃｉａｌ　Ｃｏｎｄｉｔｉｏｎ",
    ),
)
def test_taxonomy_term_semantic_hash_ignores_display_and_alias_variants(
    display_name: str,
) -> None:
    row = {"id": "term:channel:financial_condition", "type": "TaxonomyTerm"}
    base = {
        "id": row["id"],
        "type": "TaxonomyTerm",
        "object_type": "TaxonomyTerm",
        "term_type": "channel",
        "display_name": display_name,
        "aliases": [display_name],
        "schema_version": "v1",
    }
    expected = {**base, "display_name": "financial_condition", "aliases": []}
    assert spine_builder._semantic_object_hash(row, base) == spine_builder._semantic_object_hash(
        row, expected
    )


def test_semantic_preflight_reports_all_conflicts_before_merge(tmp_path: Path) -> None:
    aapl_fragment = tmp_path / "fragments" / "AAPL.sqlite"
    msft_fragment = tmp_path / "fragments" / "MSFT.sqlite"
    _write_spine_fragment(
        aapl_fragment,
        ticker="AAPL",
        topic_key="aapl_ai",
        topic_label="AAPL AI",
    )
    _write_spine_fragment(
        msft_fragment,
        ticker="MSFT",
        topic_key="msft_ai",
        topic_label="MSFT AI",
    )
    _add_shared_graph_occurrence(aapl_fragment, ticker="AAPL")
    _add_shared_graph_occurrence(msft_fragment, ticker="MSFT")
    with sqlite3.connect(msft_fragment) as conn:
        conn.execute(
            "UPDATE global_object_locator SET semantic_hash = 'conflict:factor' "
            "WHERE object_id = 'term:factor:shared'"
        )
        conn.execute(
            "UPDATE global_object_locator SET semantic_hash = 'conflict:metric' "
            "WHERE object_id = 'term:metric:shared'"
        )
        conn.execute(
            "UPDATE global_edge_spine SET semantic_hash = 'conflict:edge' "
            "WHERE edge_id = 'edge:shared:factor-to-metric'"
        )

    report_path = tmp_path / "verify" / "semantic_preflight.json"
    result = preflight_spine_fragments(
        [msft_fragment, aapl_fragment],
        report_path=report_path,
    )

    assert result["ok"] is False
    assert result["conflict_count"] == 3
    assert [conflict["kind"] for conflict in result["conflicts"]].count("semantic") == 3
    assert {tuple(sorted(conflict["key"].items())) for conflict in result["conflicts"]} == {
        (("edge_id", "edge:shared:factor-to-metric"),),
        (("object_id", "term:factor:shared"),),
        (("object_id", "term:metric:shared"),),
    }
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["conflict_count"] == 3


def test_shared_canonical_is_deterministic_across_fragment_orders(tmp_path: Path) -> None:
    canonical_rows: list[tuple[tuple[object, ...], tuple[object, ...]]] = []
    for run, paths in enumerate(
        (
            (("Z_AAPL.sqlite", "AAPL"), ("A_MSFT.sqlite", "MSFT")),
            (("A_AAPL.sqlite", "AAPL"), ("Z_MSFT.sqlite", "MSFT")),
        ),
        start=1,
    ):
        fragments: list[Path] = []
        for filename, ticker in paths:
            fragment = tmp_path / f"run-{run}" / filename
            _write_spine_fragment(
                fragment,
                ticker=ticker,
                topic_key=f"{ticker.lower()}_ai",
                topic_label=f"{ticker} AI",
            )
            _add_shared_graph_occurrence(fragment, ticker=ticker)
            fragments.append(fragment)
        output = tmp_path / f"global-{run}.sqlite"
        merge_spine_fragments(
            list(reversed(fragments)),
            output,
            generate_links=False,
        )
        with sqlite3.connect(output) as conn:
            canonical_rows.append(
                (
                    conn.execute(
                        """
                        SELECT ticker, document_id, shard_id, shard_path,
                               object_hash, compact_summary, occurrence_count
                        FROM global_object_locator
                        WHERE object_id = 'term:factor:shared'
                        """
                    ).fetchone(),
                    conn.execute(
                        """
                        SELECT ticker, document_id, shard_id, shard_path,
                               confidence, compact_reason, occurrence_count
                        FROM global_edge_spine
                        WHERE edge_id = 'edge:shared:factor-to-metric'
                        """
                    ).fetchone(),
                )
            )

    assert canonical_rows[0] == canonical_rows[1]
    assert canonical_rows[0][0][0] == "AAPL"
    assert canonical_rows[0][1][0] == "AAPL"
    assert canonical_rows[0][0][-1] == 2
    assert canonical_rows[0][1][-1] == 2


def test_semantic_object_hash_ignores_real_shared_id_provenance_diff() -> None:
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT 'term:factor:sofr' AS id, 'CanonicalEntity' AS type").fetchone()
    aapl = {
        "id": "term:factor:sofr",
        "type": "CanonicalEntity",
        "canonical_name": "SOFR",
        "source_document_id": "AAPL:10K:FY2025",
        "ticker": "AAPL",
        "period": "FY2025",
        "ticker_scope": ["AAPL"],
        "document_type": "10-K",
    }
    xom = {
        **aapl,
        "source_document_id": "XOM:10Q:Q1-2026",
        "ticker": "XOM",
        "period": "Q1-2026",
        "ticker_scope": ["XOM"],
        "document_type": "10-Q",
    }

    assert spine_builder._semantic_object_hash(row, aapl) == spine_builder._semantic_object_hash(
        row,
        xom,
    )


def test_merge_verifies_temp_database_before_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fragment = tmp_path / "fragments" / "AAPL.sqlite"
    output = tmp_path / "release" / "indexes" / "global_spine.sqlite"
    _write_spine_fragment(
        fragment,
        ticker="AAPL",
        topic_key="ai_capex_device_demand",
        topic_label="AI capex device demand",
    )
    output.parent.mkdir(parents=True)
    original_bytes = b"previous-verified-global-spine"
    output.write_bytes(original_bytes)
    verified_paths: list[Path] = []

    def reject_temp(path: Path) -> dict[str, object]:
        verified_paths.append(Path(path))
        return {
            "ok": False,
            "path": str(path),
            "errors": ["forced_temp_verification_failure"],
            "warnings": [],
            "tables": [],
            "metadata": {},
        }

    monkeypatch.setattr(spine_builder, "verify_global_spine_schema", reject_temp)

    with pytest.raises(RuntimeError, match="forced_temp_verification_failure"):
        merge_spine_fragments(
            [fragment],
            output,
            release_id="test-release",
            created_at="2026-06-12T00:00:00+00:00",
        )

    assert len(verified_paths) == 1
    assert verified_paths[0] != output
    assert verified_paths[0].parent == output.parent
    assert output.read_bytes() == original_bytes


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
                "company_shard_schema_version": spine_builder.COMPANY_SHARD_SCHEMA_VERSION,
                "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
                "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
                "metric_dictionary": metric_dictionary_binding(),
                "shards": {
                    "AAPL": {
                        "path": "companies/AAPL.sqlite",
                        "schema_version": spine_builder.COMPANY_SHARD_SCHEMA_VERSION,
                        "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
                        "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
                        "quality_summary": _company_shard_quality_summary(shard_path),
                        "metric_dictionary": metric_dictionary_binding(),
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    from krw_ontology.release import verify_release_root, write_release_manifest_v3

    build_router_sidecar(
        release_root / "indexes" / "global_spine.sqlite",
        release_id="test-release",
    )
    build_router_coherence(
        release_root / "indexes" / "global_spine.sqlite",
        release_id="test-release",
    )
    write_release_manifest_v3(release_root, release_id="test-release", env="dev")

    result = verify_spine_shard_release(release_root)
    release_result = verify_release_root(release_root, env="dev")

    assert result["ok"] is True, result["errors"]
    assert release_result["ok"] is True, release_result["errors"]
    assert release_result["verification_mode"] == "release-root-v3-light"
    assert result["counts"]["global_object_locator"] == 2
    assert set(result["shards"]) == {"AAPL"}

    with sqlite3.connect(shard_path) as conn:
        build_metadata = json.loads(
            conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()[0]
        )
        build_metadata["schema_version"] = "1.0.0-alpha.3"
        build_metadata["agent_index_schema_version"] = "1.0.0-alpha.3"
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'build'",
            (json.dumps(build_metadata, sort_keys=True),),
        )

    stale = verify_spine_shard_release(release_root)

    assert stale["ok"] is False
    assert any(
        "source_artifact_sqlite:agent_index_schema_version_mismatch" in error
        for error in stale["errors"]
    )

    build_metadata["schema_version"] = agent_index_builder.AGENT_INDEX_SCHEMA_VERSION
    build_metadata["agent_index_schema_version"] = agent_index_builder.AGENT_INDEX_SCHEMA_VERSION
    build_metadata["source_artifact_sqlite_schema_version"] = (
        agent_index_builder.AGENT_INDEX_SCHEMA_VERSION
    )
    build_metadata["source_artifact_sqlite_builder_version"] = "source-artifact-sqlite-builder/v1"
    with sqlite3.connect(shard_path) as conn:
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'build'",
            (json.dumps(build_metadata, sort_keys=True),),
        )

    stale_builder = verify_spine_shard_release(release_root)

    assert stale_builder["ok"] is False
    assert any(
        "source_artifact_sqlite:source_artifact_sqlite_builder_version_mismatch" in error
        for error in stale_builder["errors"]
    )


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
        conn.execute(
            """
            INSERT INTO global_edge_replica(
                edge_id, ticker, document_id, document_type, period,
                shard_id, shard_path, from_object_id, to_object_id,
                relation_type, semantic_hash
            )
            VALUES(
                'edge:broken:endpoint', '', '', '', '', '', '',
                'obj:AAPL:missing', 'obj:AAPL:metric', 'supports', NULL
            )
            """
        )
    (release_root / "indexes" / "shard_manifest.json").write_text(
        json.dumps(
            {
                "format": "krw-ontology-shard-manifest/v3",
                "company_shard_schema_version": spine_builder.COMPANY_SHARD_SCHEMA_VERSION,
                "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
                "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
                "metric_dictionary": metric_dictionary_binding(),
                "shards": {
                    "AAPL": {
                        "path": "companies/AAPL.sqlite",
                        "schema_version": spine_builder.COMPANY_SHARD_SCHEMA_VERSION,
                        "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
                        "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
                        "quality_summary": _company_shard_quality_summary(shard_path),
                        "metric_dictionary": metric_dictionary_binding(),
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    from krw_ontology.release import verify_release_root, write_release_manifest_v3

    global_verification = verify_global_spine_schema(
        global_spine_path,
        deep=True,
        trust_seal=False,
    )
    write_spine_verification_seal(global_spine_path, global_verification)
    build_router_sidecar(global_spine_path, release_id="test-release")
    build_router_coherence(global_spine_path, release_id="test-release")
    write_release_manifest_v3(release_root, release_id="test-release", env="dev")

    light_result = verify_release_root(release_root, env="dev")
    deep_result = verify_release_root(release_root, env="dev", deep=True)

    assert light_result["ok"] is True, light_result["errors"]
    assert light_result["verification_mode"] == "release-root-v3-light"
    assert light_result["spine_shard_verification"]["deep"] is False
    assert deep_result["ok"] is False
    assert any(
        error.startswith("spine_shard:edge_from_endpoint_missing:")
        for error in deep_result["errors"]
    )


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
                "company_shard_schema_version": spine_builder.COMPANY_SHARD_SCHEMA_VERSION,
                "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
                "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
                "metric_dictionary": metric_dictionary_binding(),
                "shards": {
                    "AAPL": {
                        "path": "companies/AAPL.sqlite",
                        "schema_version": spine_builder.COMPANY_SHARD_SCHEMA_VERSION,
                        "source_artifact_sqlite_schema_version": SOURCE_ARTIFACT_SQLITE_SCHEMA_VERSION,
                        "source_artifact_sqlite_builder_version": SOURCE_ARTIFACT_SQLITE_BUILDER_VERSION,
                        "quality_summary": quality_summary,
                        "metric_dictionary": metric_dictionary_binding(),
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    from krw_ontology.release import write_release_manifest_v3

    build_router_sidecar(
        release_root / "indexes" / "global_spine.sqlite",
        release_id="test-release",
    )
    build_router_coherence(
        release_root / "indexes" / "global_spine.sqlite",
        release_id="test-release",
    )
    write_release_manifest_v3(release_root, release_id="test-release", env="dev")

    result = verify_spine_shard_release(release_root)

    assert result["ok"] is False
    assert "shard:AAPL:quality_summary_mismatch" in result["errors"]


def test_spine_fragment_cache_key_ignores_source_manifest_hash():
    key_a = spine_builder._spine_fragment_cache_key(
        ticker="VG", company_cache_key="company-key-1", source_manifest_hash="manifest-a"
    )
    key_b = spine_builder._spine_fragment_cache_key(
        ticker="VG", company_cache_key="company-key-1", source_manifest_hash="manifest-b"
    )
    key_c = spine_builder._spine_fragment_cache_key(
        ticker="VG", company_cache_key="company-key-2", source_manifest_hash="manifest-a"
    )
    assert key_a == key_b
    assert key_a != key_c
