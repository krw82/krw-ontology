from __future__ import annotations

import json
import shutil
import sqlite3
from copy import deepcopy
from pathlib import Path

from krw_ontology.agent_index.router_coherence import (
    RouterCoherence,
    build_router_coherence,
    load_router_coherence_profile,
    rebind_router_coherence_release,
    verify_router_coherence,
)
from krw_ontology.agent_index.router_sidecar import RouterSidecar, build_router_sidecar
from krw_ontology.agent_index.spine_schema import (
    create_global_spine_schema,
    write_global_spine_metadata,
)


def _write_coherence_spine(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        create_global_spine_schema(conn)
        write_global_spine_metadata(
            conn,
            {
                "release_id": "coherence-test-release",
                "source_manifest_hash": "source-hash",
                "created_at": "2026-07-11T00:00:00+00:00",
            },
        )
        rows = (
            ("claim:false:alpha", "FALSE", "alpha exposure"),
            ("claim:false:beta", "FALSE", "beta exposure"),
            ("claim:true:coherent", "TRUE", "alpha beta exposure"),
        )
        for object_id, ticker, summary in rows:
            conn.execute(
                """
                INSERT INTO global_object_locator(
                    object_id, ticker, company_name, document_id, document_type,
                    period, object_type, shard_id, shard_path,
                    compact_label, compact_summary
                ) VALUES (?, ?, ?, ?, '10-K', 'FY2025', 'ResearchClaim',
                          ?, ?, ?, ?)
                """,
                (
                    object_id,
                    ticker,
                    f"{ticker} Corp.",
                    f"{ticker}:10K:FY2025",
                    ticker,
                    f"indexes/companies/{ticker}.sqlite",
                    object_id,
                    summary,
                ),
            )
    return path


def _low_information_profile(tmp_path: Path) -> Path:
    profile = deepcopy(load_router_coherence_profile())
    profile["profile_id"] = "router-source-coherence-test"
    profile["query"]["minimum_information"] = 0.0
    path = tmp_path / "coherence-profile.json"
    path.write_text(json.dumps(profile, sort_keys=True), encoding="utf-8")
    return path


def test_build_router_coherence_is_small_verified_and_source_bound(tmp_path: Path) -> None:
    spine = _write_coherence_spine(tmp_path / "indexes" / "global_spine.sqlite")
    result = build_router_coherence(
        spine,
        release_id="coherence-test-release",
        profile_path=_low_information_profile(tmp_path),
    )

    verification = verify_router_coherence(
        result.path,
        expected_global_spine_sha256=result.metadata["source_global_spine_sha256"],
        expected_release_id="coherence-test-release",
        expected_profile_sha256=result.metadata["profile_sha256"],
        deep=True,
    )

    assert verification["ok"] is True, verification["errors"]
    assert verification["integrity_source"] == "immutable_cache_seal"
    assert result.counts["coherence_ticker"] == 2
    assert result.counts["coherence_unit"] == 3
    assert result.counts["coherence_fts"] == 3
    assert result.path.stat().st_size < 1_000_000


def test_router_coherence_requires_terms_in_one_source_object(tmp_path: Path) -> None:
    spine = _write_coherence_spine(tmp_path / "indexes" / "global_spine.sqlite")
    result = build_router_coherence(
        spine,
        profile_path=_low_information_profile(tmp_path),
    )

    with RouterCoherence(result.path) as coherence:
        search = coherence.search_terms(["alpha", "beta"])

    assert search["applied"] is True
    assert [row["ticker"] for row in search["candidates"]] == ["TRUE"]
    assert search["candidates"][0]["matched_terms"] == ["alpha", "beta"]
    assert search["candidates"][0]["term_coverage"] == 1.0


def test_router_coherence_honors_explicit_ticker_scope(tmp_path: Path) -> None:
    spine = _write_coherence_spine(tmp_path / "indexes" / "global_spine.sqlite")
    result = build_router_coherence(
        spine,
        profile_path=_low_information_profile(tmp_path),
    )

    with RouterCoherence(result.path) as coherence:
        search = coherence.search_terms(["alpha", "beta"], tickers=["FALSE"])

    assert search["applied"] is False
    assert search["reason"] == "no_source_coherent_match"


def test_router_sidecar_fuses_coherence_candidates_without_text_duplication(tmp_path: Path) -> None:
    spine = _write_coherence_spine(tmp_path / "indexes" / "global_spine.sqlite")
    sidecar = build_router_sidecar(spine, release_id="coherence-test-release")
    build_router_coherence(
        spine,
        release_id="coherence-test-release",
        profile_path=_low_information_profile(tmp_path),
    )

    with RouterSidecar(sidecar.path) as router:
        assert router.coherence_available is True
        result = router.search("alpha beta", limit=10)

    assert result["coherence_rerank"]["applied"] is True
    assert result["ticker_candidates"][0]["ticker"] == "TRUE"
    assert result["ticker_candidates"][0]["source_coherent_match"] is True
    assert result["ticker_candidates"][0]["coherence_matched_terms"] == ["alpha", "beta"]


def test_router_coherence_release_rebind_inherits_trusted_copy_seal(tmp_path: Path) -> None:
    spine = _write_coherence_spine(tmp_path / "source" / "indexes" / "global_spine.sqlite")
    source = build_router_coherence(
        spine,
        release_id="coherence-test-release",
        profile_path=_low_information_profile(tmp_path),
    )
    target = tmp_path / "target" / "indexes" / "router_coherence.sqlite"
    target.parent.mkdir(parents=True)
    shutil.copy2(source.path, target)

    rebound = rebind_router_coherence_release(
        target,
        release_id="coherence-prod-release",
        expected_global_spine_sha256=source.metadata["source_global_spine_sha256"],
        expected_previous_release_id="coherence-test-release",
        trusted_source_path=source.path,
    )

    assert rebound["ok"] is True, rebound["errors"]
    assert rebound["seal_status"] == "valid"
    assert rebound["metadata"]["release_id"] == "coherence-prod-release"
    source_after = verify_router_coherence(source.path, deep=True)
    assert source_after["metadata"]["release_id"] == "coherence-test-release"
