"""Compact web catalog export for GCP web/API control-plane use."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krw_ontology.release import verify_release_root

WEB_CATALOG_FORMAT = "krw-ontology-web-catalog/v1"


def export_web_catalog(
    root: Path | str,
    *,
    env: str | None = None,
    index_path: Path | str | None = None,
) -> dict[str, Any]:
    """Export a compact, read-only web catalog from an existing release.

    This function intentionally never builds or mutates the ontology index.
    The release must already contain a valid manifest and agent_index.sqlite.
    """
    verification = verify_release_root(root, env=env, index_path=index_path)
    if not verification["ok"]:
        raise ValueError(f"Release verification failed: {', '.join(verification['errors'])}")

    root_path = Path(verification["root"])
    resolved_index_path = Path(str(verification["index_path"]))
    manifest = verification["manifest"]
    with closing(_connect_readonly(resolved_index_path)) as conn:
        conn.row_factory = sqlite3.Row
        companies = _export_companies(conn)
        summary = _export_summary(conn, companies=companies)

    return {
        "format": WEB_CATALOG_FORMAT,
        "env": manifest.get("env"),
        "release_id": manifest.get("release_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "root": str(root_path),
            "manifest_path": verification.get("manifest_path"),
            "index_path": str(resolved_index_path),
        },
        "release": {
            "release_id": manifest.get("release_id"),
            "env": manifest.get("env"),
            "created_at": manifest.get("created_at"),
            "agent_index_schema_version": manifest.get("agent_index_schema_version"),
            "index_generated_at": manifest.get("index_generated_at"),
        },
        "summary": summary,
        "companies": companies,
    }


def write_web_catalog(
    root: Path | str,
    out: Path | str,
    *,
    env: str | None = None,
    index_path: Path | str | None = None,
) -> dict[str, Any]:
    """Write a compact web catalog JSON file from an existing release."""
    catalog = export_web_catalog(root, env=env, index_path=index_path)
    out_path = Path(out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(catalog, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog


def _connect_readonly(index_path: Path) -> sqlite3.Connection:
    uri = f"file:{index_path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _export_summary(conn: sqlite3.Connection, *, companies: list[dict[str, Any]]) -> dict[str, Any]:
    index_document_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    object_count = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    return {
        "company_count": len(companies),
        "document_count": sum(company["coverage"]["document_count"] for company in companies),
        "index_document_count": index_document_count,
        "object_count": object_count,
    }


def _export_companies(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    tickers = [
        row["ticker"]
        for row in conn.execute(
            """
            SELECT ticker FROM documents
            UNION
            SELECT ticker FROM objects
            ORDER BY ticker
            """
        ).fetchall()
        if row["ticker"]
    ]
    return [_export_company(conn, ticker) for ticker in tickers]


def _export_company(conn: sqlite3.Connection, ticker: str) -> dict[str, Any]:
    documents = _export_company_documents(conn, ticker)
    profile = _load_company_profile(conn, ticker)
    object_counts = _object_counts(conn, ticker)
    quality_counts = _quality_counts(conn, ticker)
    document_types = sorted({document["document_type"] for document in documents})
    periods = sorted({document["period"] for document in documents}, key=_period_sort_key, reverse=True)
    latest_period = periods[0] if periods else None
    dimension_count = _safe_count(conn, "SELECT COUNT(*) FROM company_dimension_catalog WHERE ticker = ?", (ticker,))
    topic_count = _safe_count(conn, "SELECT COUNT(*) FROM company_topic_index WHERE ticker = ?", (ticker,))

    return {
        "ticker": ticker,
        "name": _first_string(profile, "company_name", "name") or ticker,
        "sector": _first_string(profile, "sector", "industry"),
        "description": _first_string(profile, "description", "business_model_summary"),
        "latest_period": latest_period,
        "coverage": {
            "document_count": len(documents),
            "document_types": document_types,
            "periods": periods,
            "object_counts": dict(sorted(object_counts.items())),
            "quality_event_counts": dict(sorted(quality_counts.items())),
            "dimension_count": dimension_count,
            "topic_count": topic_count,
        },
        "documents": documents,
        "generated_at": _latest_generated_at(documents),
    }


def _export_company_documents(conn: sqlite3.Connection, ticker: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    rows = conn.execute(
        """
        SELECT document_type, doc_type_key, period, counts_json, section_quality_status, generated_at
        FROM documents
        WHERE ticker = ? AND document_type IN ('10-K', '10-Q')
        ORDER BY period DESC, document_type
        """,
        (ticker,),
    ).fetchall()
    for row in rows:
        documents.append(
            {
                "document_type": row["document_type"],
                "doc_type_key": row["doc_type_key"],
                "period": row["period"],
                "counts": _parse_json_object(row["counts_json"]),
                "section_quality_status": row["section_quality_status"],
                "generated_at": row["generated_at"],
            }
        )
    return sorted(documents, key=lambda document: _period_sort_key(document["period"]), reverse=True)


def _load_company_profile(conn: sqlite3.Connection, ticker: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT json FROM objects
        WHERE ticker = ? AND type = 'CompanyBusinessProfile'
        ORDER BY period DESC
        LIMIT 1
        """,
        (ticker,),
    ).fetchone()
    if not row:
        return {}
    return _parse_json_object(row["json"])


def _object_counts(conn: sqlite3.Connection, ticker: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in conn.execute(
        "SELECT type, COUNT(*) AS count FROM objects WHERE ticker = ? GROUP BY type",
        (ticker,),
    ).fetchall():
        counts[str(row["type"])] = int(row["count"])
    return counts


def _quality_counts(conn: sqlite3.Connection, ticker: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in conn.execute(
        "SELECT severity, COUNT(*) AS count FROM quality_events WHERE ticker = ? GROUP BY severity",
        (ticker,),
    ).fetchall():
        counts[str(row["severity"])] = int(row["count"])
    return counts


def _safe_count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    except sqlite3.Error:
        return 0


def _parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _latest_generated_at(documents: list[dict[str, Any]]) -> str | None:
    values = [document.get("generated_at") for document in documents if document.get("generated_at")]
    return max(values) if values else None


def _period_sort_key(period: str | None) -> tuple[int, int, str]:
    text = str(period or "")
    year_match = re.search(r"(19|20)\d{2}", text)
    quarter_match = re.search(r"Q([1-4])", text, re.IGNORECASE)
    year = int(year_match.group(0)) if year_match else -1
    quarter = int(quarter_match.group(1)) if quarter_match else 5
    return year, quarter, text
