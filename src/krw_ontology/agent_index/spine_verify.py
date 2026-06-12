"""Deep verification for v3 global spine + company shard releases."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from krw_ontology.agent_index.source_artifact_sqlite import verify_source_artifact_sqlite
from krw_ontology.agent_index.spine_builder import (
    SHARD_QUALITY_SUMMARY_FORMAT_VERSION,
    _company_shard_quality_summary,
)
from krw_ontology.agent_index.spine_schema import (
    GLOBAL_SPINE_LAYOUT,
    verify_global_spine_schema,
)


def verify_spine_shard_release(
    release_root: Path,
    *,
    manifest_path: Path | None = None,
    require_manifest: bool = True,
    sample_limit: int = 20,
) -> dict[str, Any]:
    """Deep-verify a v3 release without any monolith dependency."""
    root = release_root.expanduser().resolve()
    manifest_file = manifest_path.expanduser().resolve() if manifest_path is not None else root / "manifest.json"
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        manifest = {}
        if require_manifest:
            errors.append("manifest_missing")
    except json.JSONDecodeError as exc:
        manifest = {}
        errors.append(f"manifest_invalid_json:{exc}")

    if manifest:
        if manifest.get("format") != "krw-ontology-release/v3":
            errors.append("manifest_format_unsupported")
        if manifest.get("index_layout") != GLOBAL_SPINE_LAYOUT:
            errors.append("manifest_index_layout_unsupported")
        if manifest.get("monolith_required") is not False:
            errors.append("manifest_monolith_required_not_false")

    global_spine_path = _manifest_file_path(root, manifest, "global_spine", default="indexes/global_spine.sqlite")
    shard_manifest_path = _manifest_file_path(root, manifest, "shard_manifest", default="indexes/shard_manifest.json")
    company_shards_dir = _manifest_dir_path(root, manifest, "company_shards", default="indexes/companies")

    if manifest:
        errors.extend(_manifest_file_digest_errors(manifest, "global_spine", global_spine_path))
        errors.extend(_manifest_file_digest_errors(manifest, "shard_manifest", shard_manifest_path))
    spine_verification = verify_global_spine_schema(global_spine_path)
    if not spine_verification["ok"]:
        errors.extend(f"global_spine:{error}" for error in spine_verification["errors"])

    shard_manifest = _read_json(shard_manifest_path)
    if shard_manifest is None:
        errors.append("shard_manifest_missing_or_invalid")
        shard_entries: dict[str, Any] = {}
    else:
        raw_entries = shard_manifest.get("shards")
        shard_entries = raw_entries if isinstance(raw_entries, dict) else {}
        if not isinstance(raw_entries, dict):
            errors.append("shard_manifest_shards_missing")

    manifest_shards = ((manifest.get("indexes") or {}).get("company_shards") or {}).get("tickers") or {}
    if manifest and isinstance(manifest_shards, dict) and shard_entries and set(manifest_shards) != set(shard_entries):
        errors.append("manifest_shard_manifest_ticker_mismatch")

    shard_results: dict[str, Any] = {}
    if global_spine_path.is_file() and spine_verification["ok"]:
        with sqlite3.connect(global_spine_path) as spine_conn:
            spine_conn.row_factory = sqlite3.Row
            counts.update(_global_counts(spine_conn))
            errors.extend(_global_endpoint_errors(spine_conn, sample_limit=sample_limit))
            for index, (ticker, entry) in enumerate(sorted(shard_entries.items()), start=1):
                shard_path = _resolve_shard_path(root, company_shards_dir, entry)
                result = _verify_one_shard(
                    spine_conn,
                    shard_path,
                    ticker=str(ticker),
                    schema_name=f"shard_{index}",
                    sample_limit=sample_limit,
                    expected_sha256=_expected_shard_sha256(manifest, str(ticker), entry),
                    shard_entry=entry if isinstance(entry, Mapping) else {},
                )
                shard_results[str(ticker)] = result
                if not result["ok"]:
                    errors.extend(f"shard:{ticker}:{error}" for error in result["errors"])
    elif shard_entries:
        errors.append("global_spine_missing")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "release_root": str(root),
        "manifest_path": str(manifest_file),
        "global_spine_path": str(global_spine_path),
        "shard_manifest_path": str(shard_manifest_path),
        "company_shards_dir": str(company_shards_dir),
        "counts": counts,
        "global_spine_verification": spine_verification,
        "shards": shard_results,
        "verification_mode": "spine-shard-release",
        "manifest_required": require_manifest,
    }


def _verify_one_shard(
    spine_conn: sqlite3.Connection,
    shard_path: Path,
    *,
    ticker: str,
    schema_name: str,
    sample_limit: int,
    expected_sha256: str | None,
    shard_entry: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    counts: dict[str, int] = {}
    if not shard_path.is_file():
        return {
            "ok": False,
            "errors": ["shard_missing"],
            "path": str(shard_path),
            "counts": counts,
            "source_artifact_sqlite_verification": None,
        }
    if expected_sha256 is None:
        errors.append("sha256_missing")
    elif _file_sha256(shard_path) != expected_sha256:
        errors.append("sha256_mismatch")
    errors.extend(_quality_summary_errors(shard_path, shard_entry))
    source_artifact_verification = verify_source_artifact_sqlite(shard_path)
    if not source_artifact_verification["ok"]:
        errors.extend(f"source_artifact_sqlite:{error}" for error in source_artifact_verification["errors"])
    spine_conn.execute(f"ATTACH DATABASE ? AS {schema_name}", (str(shard_path),))
    try:
        counts["shard_objects"] = _count(spine_conn, f"{schema_name}.objects")
        counts["shard_documents"] = _count(spine_conn, f"{schema_name}.documents")
        counts["shard_edges"] = _count(spine_conn, f"{schema_name}.edges")
        counts["shard_quality_events"] = _count(spine_conn, f"{schema_name}.quality_events")
        counts["locator_objects"] = int(
            spine_conn.execute(
                "SELECT COUNT(*) FROM global_object_locator WHERE ticker = ?",
                (ticker,),
            ).fetchone()[0]
        )
        counts["document_catalog"] = int(
            spine_conn.execute(
                "SELECT COUNT(*) FROM global_document_catalog WHERE ticker = ?",
                (ticker,),
            ).fetchone()[0]
        )
        missing_locator = int(
            spine_conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {schema_name}.objects AS objects
                LEFT JOIN global_object_locator AS locator
                  ON locator.object_id = objects.id
                WHERE objects.ticker = ?
                  AND locator.object_id IS NULL
                """,
                (ticker,),
            ).fetchone()[0]
        )
        if missing_locator:
            errors.append(f"objects_missing_locator:{missing_locator}")
            errors.extend(
                _sample_values(
                    spine_conn,
                    f"""
                    SELECT objects.id
                    FROM {schema_name}.objects AS objects
                    LEFT JOIN global_object_locator AS locator
                      ON locator.object_id = objects.id
                    WHERE objects.ticker = ?
                      AND locator.object_id IS NULL
                    ORDER BY objects.id
                    LIMIT ?
                    """,
                    (ticker, sample_limit),
                    prefix="object_missing_locator",
                )
            )
        missing_object = int(
            spine_conn.execute(
                f"""
                SELECT COUNT(*)
                FROM global_object_locator AS locator
                LEFT JOIN {schema_name}.objects AS objects
                  ON objects.id = locator.object_id
                WHERE locator.ticker = ?
                  AND objects.id IS NULL
                """,
                (ticker,),
            ).fetchone()[0]
        )
        if missing_object:
            errors.append(f"locator_missing_object:{missing_object}")
        missing_doc = int(
            spine_conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {schema_name}.documents AS docs
                LEFT JOIN global_document_catalog AS catalog
                  ON catalog.ticker = docs.ticker
                 AND catalog.document_type = docs.document_type
                 AND catalog.period = docs.period
                WHERE docs.ticker = ?
                  AND catalog.document_id IS NULL
                """,
                (ticker,),
            ).fetchone()[0]
        )
        if missing_doc:
            errors.append(f"documents_missing_catalog:{missing_doc}")
    finally:
        spine_conn.commit()
        spine_conn.execute(f"DETACH DATABASE {schema_name}")
    return {
        "ok": not errors,
        "errors": errors,
        "path": str(shard_path),
        "counts": counts,
        "source_artifact_sqlite_verification": source_artifact_verification,
    }


def _quality_summary_errors(shard_path: Path, shard_entry: Mapping[str, Any]) -> list[str]:
    expected = shard_entry.get("quality_summary")
    if not isinstance(expected, Mapping):
        return ["quality_summary_missing"]
    if expected.get("format") != SHARD_QUALITY_SUMMARY_FORMAT_VERSION:
        return ["quality_summary_format_mismatch"]
    actual = _company_shard_quality_summary(shard_path)
    expected_json = json.dumps(expected, ensure_ascii=False, sort_keys=True, default=str)
    actual_json = json.dumps(actual, ensure_ascii=False, sort_keys=True, default=str)
    if expected_json != actual_json:
        return ["quality_summary_mismatch"]
    return []


def _manifest_file_digest_errors(manifest: dict[str, Any], role: str, path: Path) -> list[str]:
    output = ((manifest.get("indexes") or {}).get(role) or {}) if manifest else {}
    expected = output.get("sha256") if isinstance(output, dict) else None
    if not isinstance(expected, str) or not expected:
        return [f"{role}_sha256_missing"]
    if not path.is_file():
        return []
    return [f"{role}_sha256_mismatch"] if _file_sha256(path) != expected else []


def _expected_shard_sha256(manifest: dict[str, Any], ticker: str, shard_entry: Any) -> str | None:
    manifest_entry = (
        ((((manifest.get("indexes") or {}).get("company_shards") or {}).get("tickers") or {}).get(ticker))
        if manifest
        else None
    )
    if isinstance(manifest_entry, dict):
        expected = manifest_entry.get("sha256")
        if isinstance(expected, str) and expected:
            return expected
    if isinstance(shard_entry, dict):
        expected = shard_entry.get("sha256")
        if isinstance(expected, str) and expected:
            return expected
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _global_endpoint_errors(conn: sqlite3.Connection, *, sample_limit: int) -> list[str]:
    errors: list[str] = []
    edge_from_missing = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM global_edge_spine AS edge
            LEFT JOIN global_object_locator AS locator
              ON locator.object_id = edge.from_object_id
            WHERE locator.object_id IS NULL
            """
        ).fetchone()[0]
    )
    edge_to_missing = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM global_edge_spine AS edge
            LEFT JOIN global_object_locator AS locator
              ON locator.object_id = edge.to_object_id
            WHERE locator.object_id IS NULL
            """
        ).fetchone()[0]
    )
    if edge_from_missing:
        errors.append(f"edge_from_endpoint_missing:{edge_from_missing}")
    if edge_to_missing:
        errors.append(f"edge_to_endpoint_missing:{edge_to_missing}")
    chain_from_missing = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM global_chain_index AS link
            LEFT JOIN global_object_locator AS locator
              ON locator.object_id = link.from_object_id
            WHERE link.from_object_id IS NOT NULL
              AND locator.object_id IS NULL
            """
        ).fetchone()[0]
    )
    chain_to_missing = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM global_chain_index AS link
            LEFT JOIN global_object_locator AS locator
              ON locator.object_id = link.to_object_id
            WHERE link.to_object_id IS NOT NULL
              AND locator.object_id IS NULL
            """
        ).fetchone()[0]
    )
    if chain_from_missing:
        errors.append(f"chain_from_endpoint_missing:{chain_from_missing}")
    if chain_to_missing:
        errors.append(f"chain_to_endpoint_missing:{chain_to_missing}")
    if errors:
        errors.extend(
            _sample_values(
                conn,
                """
                SELECT edge.edge_id
                FROM global_edge_spine AS edge
                LEFT JOIN global_object_locator AS from_locator
                  ON from_locator.object_id = edge.from_object_id
                LEFT JOIN global_object_locator AS to_locator
                  ON to_locator.object_id = edge.to_object_id
                WHERE from_locator.object_id IS NULL
                   OR to_locator.object_id IS NULL
                ORDER BY edge.edge_id
                LIMIT ?
                """,
                (sample_limit,),
                prefix="edge_endpoint_sample",
            )
        )
    return errors


def _global_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "global_object_locator",
        "global_document_catalog",
        "global_edge_spine",
        "global_factor_spine",
        "global_topic_spine",
        "global_metric_spine",
        "global_entity_spine",
        "global_counterparty_spine",
        "global_chain_index",
    )
    return {table: _count(conn, table) for table in tables}


def _manifest_file_path(root: Path, manifest: dict[str, Any], role: str, *, default: str) -> Path:
    raw = (((manifest.get("indexes") or {}).get(role) or {}).get("path")) if manifest else None
    if not isinstance(raw, str) or not raw:
        raw = default
    candidate = Path(raw)
    return candidate.expanduser().resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _manifest_dir_path(root: Path, manifest: dict[str, Any], role: str, *, default: str) -> Path:
    raw = (((manifest.get("indexes") or {}).get(role) or {}).get("dir")) if manifest else None
    if not isinstance(raw, str) or not raw:
        raw = default
    candidate = Path(raw)
    return candidate.expanduser().resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _resolve_shard_path(root: Path, company_shards_dir: Path, entry: Mapping[str, Any]) -> Path:
    raw = entry.get("path") or entry.get("shard_path")
    if not isinstance(raw, str) or not raw:
        return company_shards_dir / "<missing>"
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.expanduser().resolve()
    if candidate.parts and candidate.parts[0] == "indexes":
        return (root / candidate).resolve()
    return (root / "indexes" / candidate).resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _count(conn: sqlite3.Connection, table_name: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _sample_values(
    conn: sqlite3.Connection,
    query: str,
    params: tuple[Any, ...],
    *,
    prefix: str,
) -> list[str]:
    return [f"{prefix}:{row[0]}" for row in conn.execute(query, params).fetchall()]
