"""Stage 7.14: Build artifact_index.json from all JSONL files."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from krw_ontology.errors import PipelineStageError
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import atomic_write_json, read_jsonl, write_jsonl

logger = logging.getLogger("krw_ontology")

INDEX_FILE_KEYS = [
    ("run_manifests", "run_manifests.jsonl"),
    ("ontology_registry_snapshots", "ontology_registry_snapshots.jsonl"),
    ("validation_reports", "validation_reports.jsonl"),
    ("taxonomy_terms", "taxonomy_terms.jsonl"),
    ("source_documents", "source_documents.jsonl"),
    ("source_locations", "source_locations.jsonl"),
    ("source_tables", "source_tables.jsonl"),
    ("source_table_cells", "source_table_cells.jsonl"),
    ("document_nodes", "document_nodes.jsonl"),
    ("section_candidates", "section_candidates.jsonl"),
    ("sections", "sections.jsonl"),
    ("section_boundary_audit", "section_boundary_audit.jsonl"),
    ("spans", "spans.jsonl"),
    ("span_eligibility_audit", "span_eligibility_audit.jsonl"),
    ("evidence_quotes", "evidence_quotes.jsonl"),
    ("language_signals", "language_signals.jsonl"),
    ("support_links", "support_links.jsonl"),
    ("canonical_entities", "canonical_entities.jsonl"),
    ("entity_mentions", "entity_mentions.jsonl"),
    ("claims", "claims.jsonl"),
    ("metric_observations", "metric_observations.jsonl"),
    ("calculations", "calculations.jsonl"),
    ("business_factors", "business_factors.jsonl"),
    ("agreement_terms", "agreement_terms.jsonl"),
    ("business_events", "business_events.jsonl"),
    ("business_activities", "business_activities.jsonl"),
    ("external_factor_exposures", "external_factor_exposures.jsonl"),
    ("assumption_candidates", "assumption_candidates.jsonl"),
    ("edges", "edges.jsonl"),
    ("xbrl_facts", "xbrl_facts.jsonl"),
    ("rejected_objects", "rejected_objects.jsonl"),
    ("batch_failures", "batch_failures.jsonl"),
]


def build_indexes(
    ticker: str,
    period: str,
    doc_type_key: str,
    ontology_dir: Path,
    sources_dir: Path,
    output_dir: Path,
    document_type: str = "10-K",
) -> dict:
    """Build artifact_index.json from all existing JSONL files.

    Returns dict with: artifact_index_path.
    """
    try:
        ontology_rel = f"companies/{ticker}/ontology/{doc_type_key}/{period}"
        sources_rel = f"companies/{ticker}/sources/{doc_type_key}/{period}"

        files = {}
        counts = {}
        for key, filename in INDEX_FILE_KEYS:
            file_path = ontology_dir / filename
            rel_path = f"{ontology_rel}/{filename}"
            files[key] = rel_path
            if not file_path.exists():
                write_jsonl(file_path, [])
            objects = read_jsonl(file_path)
            counts[key] = len(objects)

        sources = {
            "raw_html": f"{sources_rel}/raw.html",
            "clean_md": f"{sources_rel}/clean.md",
            "metadata": f"{sources_rel}/metadata.json",
        }

        reports = {
            "graph_report": f"{ontology_rel}/graph_report.md",
            "audit_report": f"{ontology_rel}/audit_report.md",
            "pipeline_config": f"{ontology_rel}/pipeline_config.json",
        }

        artifact_index = {
            "ticker": ticker,
            "document_type": document_type,
            "doc_type_key": doc_type_key,
            "period": period,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": SCHEMA_VERSION,
            "files": files,
            "counts": counts,
            "sources": sources,
            "reports": reports,
        }

        index_path = ontology_dir / "artifact_index.json"
        atomic_write_json(index_path, artifact_index)

        # Build company_artifact_index.json skeleton
        company_index_path = output_dir / "companies" / ticker / "indexes" / "company_artifact_index.json"
        company_index = {
            "ticker": ticker,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": SCHEMA_VERSION,
            "documents": {
                period: {
                    "document_type": document_type,
                    "doc_type_key": doc_type_key,
                    "artifact_index": f"{ontology_rel}/artifact_index.json",
                },
            },
        }
        if company_index_path.exists():
            import json
            try:
                existing = json.loads(company_index_path.read_text())
                existing.setdefault("documents", {}).update(company_index["documents"])
                company_index = existing
            except (json.JSONDecodeError, OSError):
                pass
        atomic_write_json(company_index_path, company_index)

        logger.info(
            "build_indexes: wrote artifact_index.json with %d file types", len(files),
            extra={"stage": "build_indexes"},
        )
        return {"artifact_index_path": index_path}

    except PipelineStageError:
        raise
    except Exception as e:
        raise PipelineStageError(f"build_indexes: {e}") from e
