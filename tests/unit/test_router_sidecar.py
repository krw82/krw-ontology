from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from krw_ontology.agent_index.router_sidecar import (
    ROUTER_SIDECAR_SCHEMA_VERSION,
    RouterSidecar,
    build_router_micro_derivative,
    build_router_sidecar,
    load_router_ranking_profile,
    rebind_router_sidecar_release,
    verify_router_sidecar,
)
from krw_ontology.agent_index.spine_schema import (
    create_global_spine_schema,
    write_global_spine_metadata,
)


def _write_global_spine(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        create_global_spine_schema(conn)
        write_global_spine_metadata(
            conn,
            {
                "release_id": "router-test-release",
                "source_manifest_hash": "source-hash",
                "created_at": "2026-07-10T00:00:00+00:00",
            },
        )
        conn.execute(
            """
            INSERT INTO global_document_catalog(
                document_id, ticker, company_name, document_type, period,
                shard_id, shard_path
            ) VALUES(
                'AAPL:10K:FY2025', 'AAPL', 'Apple Inc.', '10-K', 'FY2025',
                'AAPL', 'indexes/companies/AAPL.sqlite'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO global_chain_index(
                link_id, link_type, from_ticker, to_ticker, shared_key,
                shared_key_type, weight, confidence, evidence_grade,
                materiality, explanation_template
            ) VALUES(
                'link:AAPL:MSFT:stronger', 'shared_entity', 'AAPL', 'MSFT',
                'Microsoft', 'entity', 0.95, 0.8, 'medium', 0.9,
                'Stronger shared counterparty'
            )
            """
        )
        for object_id, object_type, label in (
            ("obj:AAPL:factor", "BusinessFactor", "AI capex demand"),
            ("obj:AAPL:metric", "MetricObservation", "Revenue"),
        ):
            conn.execute(
                """
                INSERT INTO global_object_locator(
                    object_id, ticker, company_name, document_id, document_type,
                    period, object_type, shard_id, shard_path, compact_label
                ) VALUES (?, 'AAPL', 'Apple Inc.', 'AAPL:10K:FY2025', '10-K',
                          'FY2025', ?, 'AAPL', 'indexes/companies/AAPL.sqlite', ?)
                """,
                (object_id, object_type, label),
            )
        conn.execute(
            """
            INSERT INTO global_topic_spine(
                topic_id, topic_key, topic_label, topic_family, topic_summary,
                ticker, source_object_ids, factor_terms, metric_terms,
                entity_terms, mechanism_terms, impact_channels,
                evidence_grade, materiality, shard_id
            ) VALUES(
                'topic:AAPL:ai', 'ai capex', 'AI capex demand',
                'AI infrastructure', 'AI investment supports cloud demand.',
                'AAPL', '["obj:AAPL:factor", "obj:AAPL:metric"]',
                '["AI capex"]', '["Revenue"]', '["Microsoft"]',
                '["demand"]', '["revenue"]', 'high', 0.9, 'AAPL'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO global_factor_spine(
                factor_key, factor_label, factor_family, ticker, object_id,
                document_id, impact_channel, materiality, evidence_grade, shard_id
            ) VALUES(
                'ai capex', 'AI capex demand', 'AI infrastructure', 'AAPL',
                'obj:AAPL:factor', 'AAPL:10K:FY2025', 'revenue', 0.9, 'high', 'AAPL'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO global_metric_spine(
                canonical_metric_key, metric_name, unit, dimensions_hash,
                ticker, object_id, document_id, period, document_type,
                value_normalized, shard_id
            ) VALUES(
                'revenue', 'Revenue', 'USD', 'dimensions', 'AAPL',
                'obj:AAPL:metric', 'AAPL:10K:FY2025', 'FY2025', '10-K', 100, 'AAPL'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO global_entity_spine(
                entity_key, entity_type, canonical_name, aliases, ticker,
                object_id, document_id, confidence, shard_id
            ) VALUES(
                'microsoft', 'Organization', 'Microsoft', '["MSFT"]', 'AAPL',
                'obj:AAPL:factor', 'AAPL:10K:FY2025', 0.9, 'AAPL'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO global_counterparty_spine(
                counterparty_key, counterparty_name, relationship_type, ticker,
                object_id, document_id, agreement_type, affected_channels,
                materiality, evidence_grade, shard_id
            ) VALUES(
                'microsoft', 'Microsoft', 'cloud', 'AAPL', 'obj:AAPL:factor',
                'AAPL:10K:FY2025', 'supply', '["revenue"]', 0.8, 'medium', 'AAPL'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO global_chain_index(
                link_id, link_type, from_ticker, to_ticker, shared_key,
                shared_key_type, weight, confidence, evidence_grade,
                materiality, explanation_template
            ) VALUES(
                'link:AAPL:MSFT', 'shared_entity', 'AAPL', 'MSFT', 'Microsoft',
                'entity', 0.8, 0.9, 'high', 0.8, 'Shared counterparty'
            )
            """
        )
    return path


def _write_ranking_safety_spine(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        create_global_spine_schema(conn)
        write_global_spine_metadata(
            conn,
            {
                "release_id": "router-ranking-safety",
                "source_manifest_hash": "source-hash",
                "created_at": "2026-07-10T00:00:00+00:00",
            },
        )
        for index in range(100):
            ticker = f"T{index:03d}"
            company_name = "Infrastructure Holdings" if index == 69 else f"Company {index:03d}"
            conn.execute(
                """
                INSERT INTO global_document_catalog(
                    document_id, ticker, company_name, document_type, period,
                    shard_id, shard_path
                ) VALUES (?, ?, ?, '10-K', 'FY2025', ?, ?)
                """,
                (
                    f"{ticker}:10K:FY2025",
                    ticker,
                    company_name,
                    ticker,
                    f"indexes/companies/{ticker}.sqlite",
                ),
            )
            shared = index < 70
            rare = index == 69
            topic_key = "shared" if shared else "other"
            topic_label = "Shared signal" if shared else "Other signal"
            summary_terms = ["common", topic_key]
            if rare:
                summary_terms.append("rare")
            conn.execute(
                """
                INSERT INTO global_topic_spine(
                    topic_id, topic_key, topic_label, topic_family, topic_summary,
                    ticker, source_object_ids, factor_terms, metric_terms,
                    entity_terms, mechanism_terms, impact_channels,
                    evidence_grade, materiality, shard_id
                ) VALUES (?, ?, ?, 'business', ?, ?, '[]', '[]', '[]',
                          '[]', '[]', '[]', 'high', ?, ?)
                """,
                (
                    f"topic:{ticker}",
                    topic_key,
                    topic_label,
                    " ".join(summary_terms),
                    ticker,
                    0.99 if rare else 0.5,
                    ticker,
                ),
            )
    return path


def _write_micro_coherence_spine(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        create_global_spine_schema(conn)
        write_global_spine_metadata(
            conn,
            {
                "release_id": "micro-coherence",
                "source_manifest_hash": "source-hash",
                "created_at": "2026-07-10T00:00:00+00:00",
            },
        )
        for ticker, company_name in (
            ("FALSE", "Separated Topics Corp."),
            ("TRUE", "Coherent Topic Corp."),
        ):
            conn.execute(
                """
                INSERT INTO global_document_catalog(
                    document_id, ticker, company_name, document_type, period,
                    shard_id, shard_path
                ) VALUES (?, ?, ?, '10-K', 'FY2025', ?, ?)
                """,
                (
                    f"{ticker}:10K:FY2025",
                    ticker,
                    company_name,
                    ticker,
                    f"indexes/companies/{ticker}.sqlite",
                ),
            )
        topic_rows = (
            ("topic:false:alpha", "alpha", "alpha", "FALSE"),
            ("topic:false:beta", "beta", "beta", "FALSE"),
            ("topic:true:coherent", "alpha beta", "alpha beta", "TRUE"),
        )
        for topic_id, topic_key, summary, ticker in topic_rows:
            conn.execute(
                """
                INSERT INTO global_topic_spine(
                    topic_id, topic_key, topic_label, topic_family, topic_summary,
                    ticker, source_object_ids, factor_terms, metric_terms,
                    entity_terms, mechanism_terms, impact_channels,
                    evidence_grade, materiality, shard_id
                ) VALUES (?, ?, ?, 'shared family', ?, ?, '[]', '[]', '[]',
                          '[]', '[]', '[]', 'high', 0.9, ?)
                """,
                (topic_id, topic_key, topic_key, summary, ticker, ticker),
            )
    return path


def _write_no_micro_spine(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        create_global_spine_schema(conn)
        write_global_spine_metadata(
            conn,
            {
                "release_id": "no-micro",
                "source_manifest_hash": "source-hash",
                "created_at": "2026-07-10T00:00:00+00:00",
            },
        )
        for ticker, company_name in (
            ("AAA", "Alpha Holdings"),
            ("BBB", "Beta Holdings"),
        ):
            conn.execute(
                """
                INSERT INTO global_document_catalog(
                    document_id, ticker, company_name, document_type, period,
                    shard_id, shard_path
                ) VALUES (?, ?, ?, '10-K', 'FY2025', ?, ?)
                """,
                (
                    f"{ticker}:10K:FY2025",
                    ticker,
                    company_name,
                    ticker,
                    f"indexes/companies/{ticker}.sqlite",
                ),
            )
    return path


def test_build_router_sidecar_is_verified_and_bound_to_source(tmp_path: Path) -> None:
    spine_path = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")

    result = build_router_sidecar(spine_path, release_id="router-test-release")
    verification = verify_router_sidecar(
        result.path,
        global_spine_path=spine_path,
        deep=True,
    )

    assert result.path == tmp_path / "indexes" / "router_sidecar.sqlite"
    assert verification["ok"] is True, verification["errors"]
    assert verification["metadata"]["schema_version"] == ROUTER_SIDECAR_SCHEMA_VERSION
    assert verification["metadata"]["ranking_profile_sha256"]
    assert verification["metadata"]["source_global_spine_sha256"]
    assert verification["metadata"]["content_sha256"]
    assert verification["metadata"]["build_fingerprint_sha256"]
    assert verification["counts"]["ticker_profile"] == 1
    assert verification["counts"]["routing_unit"] == 2
    assert verification["counts"]["routing_fts"] == 2
    assert verification["counts"]["micro_routing_unit"] == 0
    assert verification["counts"]["micro_routing_fts"] == 0
    assert verification["counts"]["facet_posting"] >= 5
    assert verification["counts"]["short_token_posting"] > 0
    assert verification["counts"]["graph_prior"] == 1
    with sqlite3.connect(result.path) as conn:
        micro_fts_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'micro_routing_fts'"
        ).fetchone()[0]
        source_kinds = {
            row[0] for row in conn.execute("SELECT DISTINCT source_kind FROM micro_routing_unit")
        }
    assert "routing_unit_id UNINDEXED" not in micro_fts_sql
    assert "routing_unit_id" in micro_fts_sql
    assert source_kinds == set()


def test_router_sidecar_exact_alias_fielded_search_facets_and_graph(tmp_path: Path) -> None:
    spine_path = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    result = build_router_sidecar(spine_path)

    with RouterSidecar(result.path) as router:
        aliases = router.lookup_alias("Apple")
        search = router.search(
            "Apple AI capex revenue",
            explicit_entity_scope=True,
        )
        korean_rescue = router.search("인공지능 투자", ticker="AAPL")
        filtered = router.search(
            "revenue",
            facet_filters={"metric": ["Revenue"]},
        )
        neighbors = router.graph_neighbors("AAPL", terms=["Microsoft"])

    assert aliases[0]["ticker"] == "AAPL"
    assert aliases[0]["alias_kind"] == "company_token"
    assert search["resolved_tickers"] == ["AAPL"]
    assert search["ticker_candidates"][0]["ticker"] == "AAPL"
    assert any(row["topic_family_key"] == "ai infrastructure" for row in search["routing_units"])
    assert korean_rescue["scope_tickers"] == ["AAPL"]
    assert filtered["ticker_candidates"][0]["ticker"] == "AAPL"
    assert neighbors[0]["to_ticker"] == "MSFT"
    assert neighbors[0]["weight"] == 0.95
    assert neighbors[0]["explanation"] == "Stronger shared counterparty"


def test_router_sidecar_coverage_first_ranking_and_safe_auxiliary_gates(
    tmp_path: Path,
) -> None:
    spine_path = _write_ranking_safety_spine(tmp_path / "indexes" / "global_spine.sqlite")
    result = build_router_sidecar(spine_path)

    with RouterSidecar(result.path) as router:
        generic_company_token = router.search("infrastructure spending")
        explicit_company_token = router.search(
            "infrastructure spending",
            explicit_entity_scope=True,
        )
        common = router.search("common")
        capped = router.search("shared")
        coverage = router.search("rare shared")
        # The production profile keeps the expensive experimental micro reranker
        # disabled. Enable it only inside this gate-specific unit test.
        router._profile["micro_rerank"]["enabled"] = True
        nonselective_micro = router.search("common shared")

    assert generic_company_token["resolved_tickers"] == []
    assert not any(
        "exact_alias" in row["channel_scores"] for row in generic_company_token["routing_units"]
    )
    assert explicit_company_token["resolved_tickers"] == ["T069"]
    assert any(
        "exact_alias" in row["channel_scores"] for row in explicit_company_token["routing_units"]
    )

    common_stat = next(row for row in common["query_term_stats"] if row["term"] == "common")
    assert common_stat["ticker_df_ratio"] == 1.0
    assert common_stat["facet_eligible"] is False
    assert not any("facet" in row["channel_scores"] for row in common["routing_units"])

    shared_aliases = [row for row in capped["exact_aliases"] if row["alias_norm"] == "shared"]
    assert len(shared_aliases) == 64

    assert coverage["minimum_should_match"] == 2
    assert coverage["ticker_candidates"][0]["ticker"] == "T069"
    assert coverage["routing_units"][0]["strict_match"] is True
    assert coverage["routing_units"][0]["matched_terms"] == ["rare", "shared"]
    assert coverage["graph_expansion_used"] is False
    assert nonselective_micro["micro_rerank"]["applied"] is False
    assert nonselective_micro["micro_rerank"]["reason"] == "insufficient_selective_anchor"


def test_micro_rerank_requires_query_terms_in_one_source_coherent_unit(
    tmp_path: Path,
) -> None:
    spine_path = _write_micro_coherence_spine(tmp_path / "indexes" / "global_spine.sqlite")
    experimental_profile = deepcopy(load_router_ranking_profile())
    experimental_profile["micro_rerank"]["enabled"] = True
    experimental_profile_path = tmp_path / "micro-enabled-profile.json"
    experimental_profile_path.write_text(
        json.dumps(experimental_profile),
        encoding="utf-8",
    )
    result = build_router_sidecar(
        spine_path,
        ranking_profile_path=experimental_profile_path,
    )

    with RouterSidecar(result.path) as router:
        micro = router.search("alpha beta", limit=2)
        explicit_scope = router.search(
            "Separated alpha beta",
            limit=2,
            explicit_entity_scope=True,
        )

    assert {row["ticker"] for row in micro["ticker_candidates"]} == {"FALSE", "TRUE"}
    assert micro["ticker_candidates"][0]["ticker"] == "TRUE"
    assert micro["ticker_candidates"][0]["source_coherent_match"] is True
    false_row = next(row for row in micro["ticker_candidates"] if row["ticker"] == "FALSE")
    assert false_row["source_coherent_match"] is False
    assert micro["micro_rerank"]["applied"] is True
    assert all(row["ticker"] != "FALSE" for row in micro["micro_routing_units"])
    assert explicit_scope["resolved_tickers"] == ["FALSE"]
    assert explicit_scope["ticker_candidates"][0]["ticker"] == "FALSE"


def test_micro_rerank_preserves_coarse_recall_and_order_when_micro_data_absent(
    tmp_path: Path,
) -> None:
    spine_path = _write_no_micro_spine(tmp_path / "indexes" / "global_spine.sqlite")
    result = build_router_sidecar(spine_path)

    with RouterSidecar(result.path) as router:
        default_disabled = router.search(
            "alpha beta holdings",
            limit=2,
            explicit_entity_scope=True,
        )
        router._profile["micro_rerank"]["enabled"] = True
        enabled_without_data = router.search(
            "alpha beta holdings",
            limit=2,
            explicit_entity_scope=True,
        )

    disabled_tickers = [row["ticker"] for row in default_disabled["ticker_candidates"]]
    enabled_tickers = [row["ticker"] for row in enabled_without_data["ticker_candidates"]]
    assert disabled_tickers == enabled_tickers
    assert set(disabled_tickers) == {"AAA", "BBB"}
    assert default_disabled["micro_rerank"]["applied"] is False
    assert default_disabled["micro_rerank"]["reason"] == "disabled_by_profile"
    assert default_disabled["micro_rerank"]["fallback"] == "preserve_coarse_order"
    assert enabled_without_data["micro_rerank"]["applied"] is False
    assert enabled_without_data["micro_rerank"]["reason"] == "micro_index_empty"


def test_micro_index_is_deep_verified_and_derivative_is_deterministic(
    tmp_path: Path,
) -> None:
    spine_path = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    base = build_router_sidecar(
        spine_path,
        output_path=tmp_path / "indexes" / "router-base.sqlite",
    )
    derivative = build_router_micro_derivative(
        spine_path,
        base.path,
        tmp_path / "indexes" / "router-derivative.sqlite",
    )

    assert base.counts["micro_routing_unit"] == 0
    assert base.counts["micro_routing_fts"] == 0
    assert derivative.verification["ok"] is True
    assert derivative.counts["micro_routing_unit"] > 0
    assert derivative.counts["micro_routing_unit"] == derivative.counts["micro_routing_fts"]
    assert derivative.metadata["content_sha256"] != base.metadata["content_sha256"]
    assert (
        derivative.metadata["build_fingerprint_sha256"] != base.metadata["build_fingerprint_sha256"]
    )
    assert derivative.metadata["coarse_profile_sha256"] == base.metadata["coarse_profile_sha256"]

    incompatible_profile = deepcopy(load_router_ranking_profile())
    incompatible_profile["lexical"]["coverage_weight"] += 0.01
    incompatible_profile_path = tmp_path / "incompatible-profile.json"
    incompatible_profile_path.write_text(
        json.dumps(incompatible_profile),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="coarse ranking/build profile is incompatible"):
        build_router_micro_derivative(
            spine_path,
            base.path,
            tmp_path / "indexes" / "incompatible-derivative.sqlite",
            ranking_profile_path=incompatible_profile_path,
        )

    with sqlite3.connect(derivative.path) as conn:
        micro_unit_id = conn.execute(
            "SELECT micro_unit_id FROM micro_routing_fts ORDER BY micro_unit_id LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM micro_routing_fts WHERE micro_unit_id = ?",
            (micro_unit_id,),
        )
    tampered = verify_router_sidecar(derivative.path, deep=True)
    assert tampered["ok"] is False
    assert "router_sidecar_micro_fts_unit_count_mismatch" in tampered["errors"]


def test_router_sidecar_profile_and_source_hash_mismatches_fail_verification(
    tmp_path: Path,
) -> None:
    spine_path = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    result = build_router_sidecar(spine_path)

    with sqlite3.connect(result.path) as conn:
        config_json = conn.execute(
            "SELECT config_json FROM ranking_profile WHERE active = 1"
        ).fetchone()[0]
        profile = json.loads(config_json)
        profile["rrf_k"] = int(profile["rrf_k"]) + 1
        conn.execute(
            "UPDATE ranking_profile SET config_json = ? WHERE active = 1",
            (json.dumps(profile, sort_keys=True),),
        )

    profile_mismatch = verify_router_sidecar(result.path, deep=False)
    assert profile_mismatch["ok"] is False
    assert any("ranking_profile_hash_mismatch" in error for error in profile_mismatch["errors"])

    result = build_router_sidecar(spine_path)
    with spine_path.open("ab") as handle:
        handle.write(b"source-mutated")
    source_mismatch = verify_router_sidecar(
        result.path,
        global_spine_path=spine_path,
        deep=False,
    )
    assert source_mismatch["ok"] is False
    assert "router_sidecar_source_global_spine_hash_mismatch" in source_mismatch["errors"]

    release_mismatch = verify_router_sidecar(
        result.path,
        expected_global_spine_sha256=result.metadata["source_global_spine_sha256"],
        expected_release_id="different-release",
        deep=False,
    )
    assert release_mismatch["ok"] is False
    assert "router_sidecar_release_id_mismatch" in release_mismatch["errors"]


def test_router_sidecar_rebuild_has_stable_semantic_hashes(tmp_path: Path) -> None:
    spine_path = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    first = build_router_sidecar(spine_path)
    first_metadata = dict(first.metadata)
    second = build_router_sidecar(spine_path)

    assert second.metadata["content_sha256"] == first_metadata["content_sha256"]
    assert second.metadata["build_fingerprint_sha256"] == first_metadata["build_fingerprint_sha256"]


def test_rebind_router_sidecar_release_updates_only_release_fingerprint(
    tmp_path: Path,
) -> None:
    spine_path = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    result = build_router_sidecar(spine_path, release_id="candidate-old")

    rebound = rebind_router_sidecar_release(
        result.path,
        release_id="candidate-new",
        expected_global_spine_sha256=result.metadata["source_global_spine_sha256"],
        expected_previous_release_id="candidate-old",
    )

    assert rebound["ok"] is True, rebound["errors"]
    assert rebound["metadata"]["release_id"] == "candidate-new"
    assert rebound["metadata"]["content_sha256"] == result.metadata["content_sha256"]
    assert (
        rebound["metadata"]["ranking_profile_sha256"] == result.metadata["ranking_profile_sha256"]
    )
    assert (
        rebound["metadata"]["build_fingerprint_sha256"]
        != result.metadata["build_fingerprint_sha256"]
    )


def test_router_sidecar_derives_company_alias_from_sec_registrant_heading(
    tmp_path: Path,
) -> None:
    spine_path = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    with sqlite3.connect(spine_path) as conn:
        conn.execute("UPDATE global_document_catalog SET company_name = NULL")
        conn.execute("UPDATE global_object_locator SET company_name = NULL")
    clean_path = tmp_path / "companies" / "AAPL" / "sources" / "10K" / "CY2025" / "clean.md"
    clean_path.parent.mkdir(parents=True)
    clean_path.write_text(
        "FORM 10-K\n\nApple Inc.\n\n(Exact name of registrant as specified in its charter)\n",
        encoding="utf-8",
    )

    result = build_router_sidecar(spine_path)
    with RouterSidecar(result.path) as router:
        aliases = router.lookup_alias("Apple")

    assert aliases[0]["ticker"] == "AAPL"
    assert aliases[0]["alias_kind"] == "company_token"


def test_router_sidecar_derives_company_alias_from_inline_xbrl_fact(
    tmp_path: Path,
) -> None:
    spine_path = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    with sqlite3.connect(spine_path) as conn:
        conn.execute("UPDATE global_document_catalog SET company_name = NULL")
        conn.execute("UPDATE global_object_locator SET company_name = NULL")
    source_root = tmp_path / "companies" / "AAPL" / "sources" / "10K" / "CY2025"
    source_root.mkdir(parents=True)
    (source_root / "clean.md").write_text("FORM 10-K\n", encoding="utf-8")
    (source_root / "raw.html").write_text(
        '<ix:nonNumeric contextRef="cover" name="dei:EntityRegistrantName">'
        "<span>Apple &amp; Partners Inc.</span></ix:nonNumeric>",
        encoding="utf-8",
    )

    result = build_router_sidecar(spine_path)
    with RouterSidecar(result.path) as router:
        aliases = router.lookup_alias("Apple")

    assert aliases[0]["ticker"] == "AAPL"
    assert aliases[0]["alias_kind"] == "company_token"


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("rrf_k",), 0),
        (("candidate_limit",), -1),
        (("channel_weights", "facet"), float("nan")),
        (("fts_field_weights", "company_text"), -0.1),
        (("bigram", "field_weights", "topic_text"), float("inf")),
        (("micro_rerank", "micro_unit_candidate_limit"), 0),
        (("micro_rerank", "micro_weight"), float("nan")),
        (("build", "max_values_per_field"), 0),
    ),
)
def test_router_ranking_profile_rejects_unsafe_numeric_values(
    tmp_path: Path,
    path: tuple[str, ...],
    value: object,
) -> None:
    profile = deepcopy(load_router_ranking_profile())
    target = profile
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    profile_path = tmp_path / "invalid-profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    with pytest.raises(ValueError, match="router ranking profile"):
        load_router_ranking_profile(profile_path)


def test_router_ranking_profile_rejects_missing_nested_field_weight(tmp_path: Path) -> None:
    profile = deepcopy(load_router_ranking_profile())
    del profile["fts_field_weights"]["company_text"]
    profile_path = tmp_path / "invalid-profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    with pytest.raises(ValueError, match="missing keys: company_text"):
        load_router_ranking_profile(profile_path)


def test_router_ranking_profile_rejects_unsafe_micro_source_policy(
    tmp_path: Path,
) -> None:
    profile = deepcopy(load_router_ranking_profile())
    profile["build"]["micro_source_kinds"] = ["topic", "company_override"]
    profile_path = tmp_path / "invalid-profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    with pytest.raises(ValueError, match="micro_source_kinds"):
        load_router_ranking_profile(profile_path)
