from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest

import krw_ontology.agent_index.router_cache as router_cache_module
from krw_ontology.agent_index.router_cache import (
    publish_router_sidecar_cache,
    restore_router_sidecar_cache,
    router_sidecar_cache_path,
    router_sidecar_semantic_cache_key,
    verify_router_sidecar_cache,
)
from krw_ontology.agent_index.router_sidecar import (
    build_router_sidecar,
    verify_router_sidecar,
)
from krw_ontology.agent_index.spine_builder import (
    SpineFragmentResult,
    _materialize_router_sidecar,
)
from krw_ontology.agent_index.spine_schema import write_global_spine_metadata
from tests.unit.test_router_sidecar import _write_global_spine


def _copy(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return "copy"


def _cache_key(fragment_rows: list[tuple[str, str]]) -> str:
    return router_sidecar_semantic_cache_key(
        fragment_cache_keys=fragment_rows,
        source_manifest_hash="manifest-hash",
        generate_links=True,
        global_spine_schema_version="global/v1",
        global_spine_builder_version="builder/v1",
        spine_projection_version="projection/v1",
        cross_company_link_builder_version="links/v1",
        metric_dictionary={"sha256": "metric-dictionary"},
    )


def test_router_sidecar_cache_key_is_order_independent_and_semantic() -> None:
    first = _cache_key([("MSFT", "fragment-msft"), ("AAPL", "fragment-aapl")])
    second = _cache_key([("AAPL", "fragment-aapl"), ("MSFT", "fragment-msft")])
    changed = _cache_key([("AAPL", "fragment-changed"), ("MSFT", "fragment-msft")])

    assert first == second
    assert changed != first


def test_router_sidecar_cache_publish_restore_and_source_rebind(tmp_path: Path) -> None:
    first_spine = _write_global_spine(tmp_path / "first" / "indexes" / "global_spine.sqlite")
    built = build_router_sidecar(first_spine, release_id="release-one")
    cache_key = _cache_key([("AAPL", "fragment-aapl")])
    cache_path = router_sidecar_cache_path(tmp_path / "cache", cache_key)

    assert (
        publish_router_sidecar_cache(
            built.path,
            cache_path,
            cache_key=cache_key,
            copy_file=_copy,
        )
        == "copy"
    )
    assert verify_router_sidecar_cache(cache_path, cache_key=cache_key) == ()
    cached_before = verify_router_sidecar(cache_path, deep=False)

    second_spine = _write_global_spine(tmp_path / "second" / "indexes" / "global_spine.sqlite")
    with sqlite3.connect(second_spine) as conn:
        write_global_spine_metadata(
            conn,
            {
                "release_id": "release-two",
                "source_manifest_hash": "manifest-hash",
                "created_at": "2026-07-12T00:00:00+00:00",
            },
        )
    restored_path = tmp_path / "second" / "indexes" / "router_sidecar.sqlite"

    restored = restore_router_sidecar_cache(
        cache_path,
        restored_path,
        cache_key=cache_key,
        global_spine_path=second_spine,
        release_id="release-two",
        copy_file=_copy,
    )

    verification = verify_router_sidecar(
        restored.path,
        global_spine_path=second_spine,
        expected_release_id="release-two",
        deep=False,
    )
    cached_after = verify_router_sidecar(cache_path, deep=False)
    assert verification["ok"] is True, verification["errors"]
    assert restored.cache_key == cache_key
    assert restored.copy_mode == "copy"
    assert verification["metadata"]["content_sha256"] == cached_before["metadata"]["content_sha256"]
    assert cached_after["metadata"] == cached_before["metadata"]


def test_router_sidecar_cache_rejects_a_tampered_file(tmp_path: Path) -> None:
    spine = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    built = build_router_sidecar(spine)
    cache_key = _cache_key([("AAPL", "fragment-aapl")])
    cache_path = router_sidecar_cache_path(tmp_path / "cache", cache_key)
    publish_router_sidecar_cache(
        built.path,
        cache_path,
        cache_key=cache_key,
        copy_file=_copy,
    )

    with cache_path.open("ab") as handle:
        handle.write(b"tampered")

    assert "router_sidecar_cache_size_mismatch" in verify_router_sidecar_cache(
        cache_path,
        cache_key=cache_key,
    )


def test_router_sidecar_cache_seal_avoids_rehash_until_stat_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spine = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    built = build_router_sidecar(spine)
    cache_key = _cache_key([("AAPL", "fragment-aapl")])
    cache_path = router_sidecar_cache_path(tmp_path / "cache", cache_key)
    publish_router_sidecar_cache(
        built.path,
        cache_path,
        cache_key=cache_key,
        copy_file=_copy,
    )

    def unexpected_hash(_path: Path) -> str:
        raise AssertionError("sealed immutable cache should not be rehashed")

    monkeypatch.setattr(router_cache_module, "immutable_file_sha256", unexpected_hash)
    assert verify_router_sidecar_cache(cache_path, cache_key=cache_key) == ()


def test_router_sidecar_cache_ignores_ctime_only_metadata_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spine = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    built = build_router_sidecar(spine)
    cache_key = _cache_key([("AAPL", "fragment-aapl")])
    cache_path = router_sidecar_cache_path(tmp_path / "cache", cache_key)
    publish_router_sidecar_cache(
        built.path,
        cache_path,
        cache_key=cache_key,
        copy_file=_copy,
    )
    before = cache_path.stat()
    os.chmod(cache_path, stat.S_IMODE(before.st_mode) ^ stat.S_IXUSR)
    after = cache_path.stat()

    def unexpected_hash(_path: Path) -> str:
        raise AssertionError("ctime-only drift must not rehash an immutable cache")

    monkeypatch.setattr(router_cache_module, "immutable_file_sha256", unexpected_hash)
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns
    assert verify_router_sidecar_cache(cache_path, cache_key=cache_key) == ()


def test_router_sidecar_cache_same_size_mutation_forces_sha_verification(
    tmp_path: Path,
) -> None:
    spine = _write_global_spine(tmp_path / "indexes" / "global_spine.sqlite")
    built = build_router_sidecar(spine)
    cache_key = _cache_key([("AAPL", "fragment-aapl")])
    cache_path = router_sidecar_cache_path(tmp_path / "cache", cache_key)
    publish_router_sidecar_cache(
        built.path,
        cache_path,
        cache_key=cache_key,
        copy_file=_copy,
    )
    payload = bytearray(cache_path.read_bytes())
    payload[-1] ^= 1
    cache_path.write_bytes(payload)

    assert "router_sidecar_cache_sha256_mismatch" in verify_router_sidecar_cache(
        cache_path,
        cache_key=cache_key,
    )


def test_router_sidecar_materialization_reuses_semantic_cache_across_releases(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    fragment = SpineFragmentResult(
        ticker="AAPL",
        fragment_path=tmp_path / "fragments" / "AAPL.sqlite",
        shard_path=tmp_path / "companies" / "AAPL.sqlite",
        counts={},
        cache_key="fragment-cache-key",
    )
    first_spine = _write_global_spine(tmp_path / "first" / "indexes" / "global_spine.sqlite")
    first, first_cache = _materialize_router_sidecar(
        global_spine_path=first_spine,
        router_sidecar_path=tmp_path / "first" / "indexes" / "router_sidecar.sqlite",
        release_id="release-one",
        fragment_results=[fragment],
        cache_root=cache_root,
        source_manifest_hash="manifest-hash",
        generate_links=True,
        no_cache=False,
    )

    second_spine = _write_global_spine(tmp_path / "second" / "indexes" / "global_spine.sqlite")
    with sqlite3.connect(second_spine) as conn:
        write_global_spine_metadata(
            conn,
            {
                "release_id": "release-two",
                "source_manifest_hash": "manifest-hash",
                "created_at": "2026-07-12T00:00:00+00:00",
            },
        )
    second, second_cache = _materialize_router_sidecar(
        global_spine_path=second_spine,
        router_sidecar_path=tmp_path / "second" / "indexes" / "router_sidecar.sqlite",
        release_id="release-two",
        fragment_results=[fragment],
        cache_root=cache_root,
        source_manifest_hash="manifest-hash",
        generate_links=True,
        no_cache=False,
    )

    assert first_cache["hit"] is False
    assert first_cache["publish_mode"] in {"copy", "reflink"}
    assert second_cache["hit"] is True
    assert second_cache["copy_mode"] in {"copy", "reflink"}
    assert first.metadata["content_sha256"] == second.metadata["content_sha256"]
    verification = verify_router_sidecar(
        second.path,
        global_spine_path=second_spine,
        expected_release_id="release-two",
        deep=False,
    )
    assert verification["ok"] is True, verification["errors"]
