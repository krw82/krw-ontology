"""Author-sharded read index for reviewed guru ontology artifacts."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence

from krw_ontology.guru.workspace import guru_root


GURU_INDEX_SCHEMA_VERSION = "krw-guru-shard-index/v1"
GURU_INDEX_DIRNAME = "indexes"
GURU_SHARD_DIRNAME = "shards"
GURU_SHARD_MANIFEST_FILENAME = "guru_shard_manifest.json"

REVIEWED_FILES = {
    "guru_objects": "guru_objects.jsonl",
    "consultation_objects": "consultation_objects.jsonl",
    "data_needs": "data_needs.jsonl",
    "relationships": "relationships.jsonl",
    "corpus_metadata": "corpus_metadata.jsonl",
    "rejected_candidates": "rejected_candidates.jsonl",
}
OBJECT_FAMILY_BY_FILE = {
    "guru_objects": "guru_object",
    "consultation_objects": "consultation_object",
    "data_needs": "data_need",
    "corpus_metadata": "corpus_metadata",
}
FAMILY_BY_OBJECT_FAMILY = {value: key for key, value in OBJECT_FAMILY_BY_FILE.items()}


def build_guru_shard_index(
    root: str | Path | None = None,
    *,
    index_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build per-author SQLite shards from reviewed guru JSONL artifacts."""
    root_path = guru_root(root)
    index_path = _index_dir(root_path, index_dir)
    shard_dir = index_path / GURU_SHARD_DIRNAME
    reviewed_dir = root_path / "reviewed"
    index_path.mkdir(parents=True, exist_ok=True)
    shard_dir.mkdir(parents=True, exist_ok=True)

    files = {
        family: _read_jsonl(reviewed_dir / filename)
        for family, filename in REVIEWED_FILES.items()
    }
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(_empty_group)
    object_id_to_authors: dict[str, set[str]] = defaultdict(set)
    skipped_rows: list[dict[str, Any]] = []

    for family, object_family in OBJECT_FAMILY_BY_FILE.items():
        for row in files[family]:
            if not isinstance(row, dict):
                continue
            reviewed_id = _clean_scalar(row.get("reviewed_id"))
            author_key = _clean_scalar(row.get("author_key"))
            if not reviewed_id or not author_key:
                skipped_rows.append(
                    {
                        "family": family,
                        "reviewed_id": reviewed_id,
                        "reason": "missing_reviewed_id_or_author_key",
                    }
                )
                continue
            payload = dict(row)
            payload["object_family"] = object_family
            grouped[author_key][family].append(payload)
            object_id_to_authors[reviewed_id].add(author_key)

    relationship_rows = [
        row for row in files["relationships"] if isinstance(row, dict)
    ]
    for relationship in relationship_rows:
        authors = set()
        for endpoint_key in ("from_id", "to_id"):
            endpoint = _clean_scalar(relationship.get(endpoint_key))
            if endpoint:
                authors.update(object_id_to_authors.get(endpoint, set()))
        if not authors:
            for endpoint_key in ("from_id", "to_id"):
                endpoint_author = _author_from_reviewed_id(relationship.get(endpoint_key))
                if endpoint_author:
                    authors.add(endpoint_author)
        for author_key in authors:
            grouped[author_key]["relationships"].append(dict(relationship))

    authors: dict[str, Any] = {}
    for author_key in sorted(grouped):
        shard_path = shard_dir / f"{author_key}.sqlite"
        shard_payload = grouped[author_key]
        fts_enabled = _write_author_shard(
            shard_path,
            author_key=author_key,
            rows_by_family=shard_payload,
        )
        authors[author_key] = {
            "author_key": author_key,
            "shard_path": str(shard_path),
            "sha256": _sha256_file(shard_path),
            "fts_enabled": fts_enabled,
            "counts": {
                family: len(shard_payload[family])
                for family in (
                    "guru_objects",
                    "consultation_objects",
                    "data_needs",
                    "corpus_metadata",
                    "relationships",
                )
            },
        }

    manifest = {
        "schema_version": GURU_INDEX_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root_path),
        "reviewed_dir": str(reviewed_dir),
        "index_dir": str(index_path),
        "shard_dir": str(shard_dir),
        "manifest_path": str(index_path / GURU_SHARD_MANIFEST_FILENAME),
        "authors": authors,
        "source_files": {
            family: {
                "path": str(reviewed_dir / filename),
                "exists": (reviewed_dir / filename).is_file(),
                "rows": len(files[family]),
                "sha256": _sha256_file(reviewed_dir / filename),
            }
            for family, filename in REVIEWED_FILES.items()
        },
        "skipped_rows": skipped_rows,
        "source_of_truth": "reviewed_jsonl",
        "runtime_policy": {
            "preferred": "author_shard",
            "fallback": "reviewed_jsonl",
            "notes": [
                "Shard files are read indexes only.",
                "Reviewed JSONL remains the canonical ontology store.",
            ],
        },
    }
    _write_json_atomic(index_path / GURU_SHARD_MANIFEST_FILENAME, manifest)
    return manifest


def guru_index_manifest_path(
    root: str | Path | None = None,
    *,
    index_dir: str | Path | None = None,
) -> Path:
    root_path = guru_root(root)
    return _index_dir(root_path, index_dir) / GURU_SHARD_MANIFEST_FILENAME


def guru_index_status(
    root: str | Path | None = None,
    *,
    index_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return lightweight shard-index availability metadata."""
    root_path = guru_root(root)
    manifest_path = guru_index_manifest_path(root_path, index_dir=index_dir)
    manifest = _read_json(manifest_path)
    if not manifest:
        return {
            "present": False,
            "usable": False,
            "runtime_mode": "reviewed_jsonl",
            "manifest_path": str(manifest_path),
            "reason": "manifest_not_found",
        }
    authors = manifest.get("authors") if isinstance(manifest, Mapping) else {}
    if not isinstance(authors, Mapping):
        authors = {}
    missing = [
        author_key
        for author_key, author_payload in authors.items()
        if not Path(str(author_payload.get("shard_path", ""))).is_file()
    ]
    usable = not missing and bool(authors)
    return {
        "present": True,
        "usable": usable,
        "runtime_mode": "author_shard" if usable else "reviewed_jsonl",
        "schema_version": manifest.get("schema_version"),
        "generated_at": manifest.get("generated_at"),
        "manifest_path": str(manifest_path),
        "index_dir": manifest.get("index_dir"),
        "shard_dir": manifest.get("shard_dir"),
        "authors": {
            key: {
                "shard_path": payload.get("shard_path"),
                "counts": payload.get("counts", {}),
                "fts_enabled": bool(payload.get("fts_enabled")),
            }
            for key, payload in authors.items()
            if isinstance(payload, Mapping)
        },
        "missing_shards": missing,
        "source_of_truth": manifest.get("source_of_truth", "reviewed_jsonl"),
    }


def load_guru_index_bundle(
    root: str | Path | None = None,
    *,
    author_keys: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    """Load reviewed artifacts from author shards, returning ``None`` on fallback."""
    root_path = guru_root(root)
    manifest = _read_json(guru_index_manifest_path(root_path))
    if not manifest or manifest.get("schema_version") != GURU_INDEX_SCHEMA_VERSION:
        return None
    authors_manifest = manifest.get("authors")
    if not isinstance(authors_manifest, Mapping) or not authors_manifest:
        return None

    selected_authors = _clean_list(author_keys) or sorted(authors_manifest)
    shard_paths: list[Path] = []
    for author_key in selected_authors:
        author_payload = authors_manifest.get(author_key)
        if not isinstance(author_payload, Mapping):
            return None
        shard_path = Path(str(author_payload.get("shard_path", "")))
        if not shard_path.is_file():
            return None
        shard_paths.append(shard_path)

    files: dict[str, list[dict[str, Any]]] = {
        family: [] for family in REVIEWED_FILES
    }
    relationships_by_key: dict[str, dict[str, Any]] = {}
    for shard_path in shard_paths:
        shard_rows, shard_relationships = _read_author_shard(shard_path)
        for object_family, rows in shard_rows.items():
            family = FAMILY_BY_OBJECT_FAMILY.get(object_family)
            if family:
                files[family].extend(rows)
        for relationship in shard_relationships:
            relationships_by_key[_relationship_key(relationship)] = relationship

    files["relationships"] = list(relationships_by_key.values())
    searchable: list[dict[str, Any]] = []
    for family in ("guru_objects", "consultation_objects", "data_needs", "corpus_metadata"):
        for row in files[family]:
            payload = dict(row)
            payload["object_family"] = OBJECT_FAMILY_BY_FILE[family]
            searchable.append(payload)
    objects_by_id = {
        str(row["reviewed_id"]): row
        for row in searchable
        if row.get("reviewed_id")
    }
    relationships_by_id: dict[str, list[dict[str, Any]]] = {}
    for relationship in files["relationships"]:
        for key in ("from_id", "to_id"):
            object_id = relationship.get(key)
            if object_id:
                relationships_by_id.setdefault(str(object_id), []).append(relationship)

    return {
        **files,
        "objects_by_id": objects_by_id,
        "relationships_by_id": relationships_by_id,
        "_index": {
            "enabled": True,
            "runtime_mode": "author_shard",
            "schema_version": manifest.get("schema_version"),
            "manifest_path": str(guru_index_manifest_path(root_path)),
            "author_keys": selected_authors,
        },
    }


def _write_author_shard(
    shard_path: Path,
    *,
    author_key: str,
    rows_by_family: Mapping[str, Sequence[dict[str, Any]]],
) -> bool:
    tmp_path = shard_path.with_name(f".{shard_path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    conn = sqlite3.connect(tmp_path)
    fts_enabled = False
    try:
        conn.executescript(
            """
            PRAGMA journal_mode=OFF;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE objects (
                reviewed_id TEXT PRIMARY KEY,
                author_key TEXT NOT NULL,
                object_family TEXT NOT NULL,
                object_type TEXT,
                object_origin TEXT,
                intent_family TEXT,
                decision_stage TEXT,
                requires_company_data INTEGER NOT NULL DEFAULT 0,
                requires_portfolio_data INTEGER NOT NULL DEFAULT 0,
                label_ko TEXT,
                label_en TEXT,
                summary_ko TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE relationships (
                relationship_key TEXT PRIMARY KEY,
                from_id TEXT,
                to_id TEXT,
                relation_type TEXT,
                explanation_ko TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE source_anchors (
                reviewed_id TEXT NOT NULL,
                span_id TEXT NOT NULL,
                author_key TEXT NOT NULL,
                object_family TEXT NOT NULL,
                PRIMARY KEY (reviewed_id, span_id)
            );
            CREATE INDEX idx_objects_author_family ON objects(author_key, object_family);
            CREATE INDEX idx_objects_intent ON objects(intent_family);
            CREATE INDEX idx_objects_stage ON objects(decision_stage);
            CREATE INDEX idx_objects_company ON objects(requires_company_data);
            CREATE INDEX idx_relationships_from ON relationships(from_id);
            CREATE INDEX idx_relationships_to ON relationships(to_id);
            CREATE INDEX idx_source_anchors_span ON source_anchors(span_id);
            """
        )
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE objects_fts USING fts5(reviewed_id UNINDEXED, text)"
            )
            fts_enabled = True
        except sqlite3.OperationalError:
            conn.execute("CREATE TABLE objects_fts (reviewed_id TEXT PRIMARY KEY, text TEXT)")

        for family, object_family in OBJECT_FAMILY_BY_FILE.items():
            for row in rows_by_family.get(family, []):
                payload = dict(row)
                reviewed_id = _clean_scalar(payload.get("reviewed_id"))
                if not reviewed_id:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO objects (
                        reviewed_id,
                        author_key,
                        object_family,
                        object_type,
                        object_origin,
                        intent_family,
                        decision_stage,
                        requires_company_data,
                        requires_portfolio_data,
                        label_ko,
                        label_en,
                        summary_ko,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reviewed_id,
                        author_key,
                        object_family,
                        _object_type(payload, object_family),
                        _clean_scalar(payload.get("object_origin")),
                        _clean_scalar(payload.get("intent_family")),
                        _clean_scalar(payload.get("decision_stage")),
                        int(_boolish(payload.get("requires_company_data"))),
                        int(_boolish(payload.get("requires_portfolio_data"))),
                        _clean_scalar(payload.get("label_ko")),
                        _clean_scalar(payload.get("label_en")),
                        _clean_scalar(payload.get("summary_ko")),
                        _json_dumps(payload),
                    ),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO objects_fts (reviewed_id, text) VALUES (?, ?)",
                    (reviewed_id, _search_text(payload)),
                )
                for span_id in _clean_list(payload.get("supporting_span_ids")):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO source_anchors (
                            reviewed_id, span_id, author_key, object_family
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (reviewed_id, span_id, author_key, object_family),
                    )

        for relationship in rows_by_family.get("relationships", []):
            relationship_key = _relationship_key(relationship)
            conn.execute(
                """
                INSERT OR REPLACE INTO relationships (
                    relationship_key,
                    from_id,
                    to_id,
                    relation_type,
                    explanation_ko,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    relationship_key,
                    _clean_scalar(relationship.get("from_id")),
                    _clean_scalar(relationship.get("to_id")),
                    _clean_scalar(relationship.get("relation_type")),
                    _clean_scalar(relationship.get("explanation_ko")),
                    _json_dumps(relationship),
                ),
            )

        metadata = {
            "schema_version": GURU_INDEX_SCHEMA_VERSION,
            "author_key": author_key,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "fts_enabled": fts_enabled,
        }
        for key, value in metadata.items():
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (key, _json_dumps(value)),
            )
        conn.commit()
    finally:
        conn.close()
    tmp_path.replace(shard_path)
    return fts_enabled


def _read_author_shard(
    shard_path: Path,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    rows_by_family: dict[str, list[dict[str, Any]]] = {
        family: [] for family in OBJECT_FAMILY_BY_FILE.values()
    }
    relationships: list[dict[str, Any]] = []
    conn = sqlite3.connect(f"file:{shard_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            "SELECT object_family, payload_json FROM objects ORDER BY reviewed_id"
        ):
            payload = json.loads(row["payload_json"])
            payload["object_family"] = row["object_family"]
            rows_by_family[row["object_family"]].append(payload)
        for row in conn.execute(
            "SELECT payload_json FROM relationships ORDER BY relationship_key"
        ):
            relationships.append(json.loads(row["payload_json"]))
    finally:
        conn.close()
    return rows_by_family, relationships


def _index_dir(root_path: Path, index_dir: str | Path | None) -> Path:
    if index_dir is None:
        return root_path / GURU_INDEX_DIRNAME
    return Path(index_dir).expanduser().resolve()


def _empty_group() -> dict[str, list[dict[str, Any]]]:
    return {family: [] for family in REVIEWED_FILES}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(_json_dumps(payload) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _clean_scalar(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence):
        values = list(value)
    else:
        values = [value]
    cleaned = []
    for item in values:
        text = _clean_scalar(item)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _object_type(row: Mapping[str, Any], object_family: str) -> str | None:
    return (
        _clean_scalar(row.get("object_type"))
        or _clean_scalar(row.get("data_need_family"))
        or object_family
    )


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _search_text(row: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "reviewed_id",
        "author_key",
        "object_family",
        "object_type",
        "label_ko",
        "label_en",
        "summary_ko",
        "summary_en",
        "question_pattern_ko",
        "answer_guidance_ko",
        "data_need_key",
        "data_need_family",
        "intent_family",
        "decision_stage",
    ):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    for key in ("intent_tags", "company_data_hooks", "portfolio_data_hooks"):
        value = row.get(key)
        if isinstance(value, Sequence) and not isinstance(value, str):
            parts.extend(str(item) for item in value if item)
    for key in ("applicability", "specificity", "answer_role"):
        value = row.get(key)
        if isinstance(value, Mapping):
            parts.append(_json_dumps(value))
    return "\n".join(parts)


def _relationship_key(relationship: Mapping[str, Any]) -> str:
    explicit = _clean_scalar(relationship.get("relationship_id"))
    if explicit:
        return explicit
    raw = _json_dumps(
        {
            "from_id": relationship.get("from_id"),
            "to_id": relationship.get("to_id"),
            "relation_type": relationship.get("relation_type"),
            "explanation_ko": relationship.get("explanation_ko"),
        }
    )
    return "relationship:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _author_from_reviewed_id(value: Any) -> str | None:
    text = _clean_scalar(value)
    if not text:
        return None
    parts = text.split(":")
    if len(parts) >= 3 and parts[0] == "guru":
        return parts[1]
    return None


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
