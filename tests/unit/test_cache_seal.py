from __future__ import annotations

from pathlib import Path

from krw_ontology.agent_index.cache_seal import (
    immutable_file_identity,
    immutable_file_identity_matches,
)


def test_immutable_cache_identity_ignores_volume_device_rebind(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite"
    path.write_bytes(b"immutable-cache")
    identity = immutable_file_identity(path)

    assert immutable_file_identity_matches(
        {**identity, "device": identity["device"] + 1},
        identity,
    )
    assert not immutable_file_identity_matches(
        {**identity, "inode": identity["inode"] + 1},
        identity,
    )
    assert not immutable_file_identity_matches(
        {**identity, "size_bytes": identity["size_bytes"] + 1},
        identity,
    )
    assert not immutable_file_identity_matches(
        {**identity, "mtime_ns": identity["mtime_ns"] + 1},
        identity,
    )
