"""Build governance artifacts: RunManifest and OntologyRegistrySnapshot."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krw_ontology.agent_index.builder import AGENT_INDEX_SCHEMA_VERSION
from krw_ontology.registry import load_ontology_registry
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import write_jsonl


PIPELINE_VERSION = "1.0.0-alpha"


def build_governance_artifacts(
    *,
    ontology_dir: Path,
    ticker: str,
    period: str,
    document_type: str,
    source_document_id: str,
    config: Any,
    raw_html_sha256: str | None = None,
    clean_md_sha256: str | None = None,
) -> dict[str, Any]:
    """Write deterministic governance rows for the current document run."""
    registry = load_ontology_registry()
    created_at = datetime.now(timezone.utc).isoformat()
    run_id = f"run:{ticker}:{period}:{document_type.replace('-', '')}:{created_at}"

    manifest = {
        "id": f"run_manifest:{ticker}:{period}:{document_type.replace('-', '')}",
        "type": "RunManifest",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "run_id": run_id,
        "pipeline_version": PIPELINE_VERSION,
        "ontology_schema_version": SCHEMA_VERSION,
        "ontology_registry_version": registry.version,
        "agent_index_schema_version": AGENT_INDEX_SCHEMA_VERSION,
        "model": getattr(config, "model", ""),
        "stage_models": getattr(config, "stage_models", {}),
        "source_hash": raw_html_sha256,
        "clean_text_hash": clean_md_sha256,
        "created_at": created_at,
        "schema_version": SCHEMA_VERSION,
    }
    snapshot = {
        "id": f"ontology_registry_snapshot:{ticker}:{period}:{document_type.replace('-', '')}",
        "type": "OntologyRegistrySnapshot",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "registry_version": registry.version,
        "canonical_artifacts": registry.canonical_artifacts,
        "text_fields_by_type": registry.text_fields_by_type,
        "created_at": created_at,
        "schema_version": SCHEMA_VERSION,
    }
    write_jsonl(ontology_dir / "run_manifests.jsonl", [manifest])
    write_jsonl(ontology_dir / "ontology_registry_snapshots.jsonl", [snapshot])
    return {"run_manifest": manifest, "ontology_registry_snapshot": snapshot}
