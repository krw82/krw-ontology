"""Stage 7.5: Extract sections from a canonical document-node model."""

from __future__ import annotations

import logging
from pathlib import Path

from krw_ontology.errors import PipelineStageError
from krw_ontology.pipeline.document_model import (
    build_document_nodes,
    build_section_candidates,
    select_section_boundaries,
)
from krw_ontology.utils.io import atomic_write_json, write_jsonl

logger = logging.getLogger("krw_ontology")

def extract_sections(
    clean_md_path: Path,
    *,
    raw_html_path: Path | None = None,
    output_dir: Path | None = None,
    document_type: str = "10-K",
) -> dict:
    """Detect and extract sections from clean markdown and raw HTML hints.

    Returns dict with: sections, document_nodes, section_candidates,
    section_boundary_audit, and section_quality.
    """
    try:
        text = clean_md_path.read_text()
    except OSError as e:
        raise PipelineStageError(f"extract_sections: cannot read {clean_md_path}: {e}") from e

    raw_html_text = None
    if raw_html_path and raw_html_path.exists():
        try:
            raw_html_text = raw_html_path.read_text(errors="ignore")
        except OSError:
            raw_html_text = None

    document_nodes = build_document_nodes(text, raw_html_text, document_type=document_type)
    section_candidates = build_section_candidates(document_nodes, document_type=document_type)
    sections, section_boundary_audit, section_quality = select_section_boundaries(
        text,
        section_candidates,
        document_type=document_type,
    )

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(output_dir / "document_nodes.jsonl", document_nodes)
        write_jsonl(output_dir / "section_candidates.jsonl", section_candidates)
        write_jsonl(output_dir / "sections.jsonl", sections)
        write_jsonl(output_dir / "section_boundary_audit.jsonl", section_boundary_audit)
        atomic_write_json(output_dir / "section_quality.json", section_quality)

    if not sections:
        logger.warning("extract_sections: no sections detected", extra={"stage": "extract_sections"})
        return {
            "sections": [],
            "document_nodes": document_nodes,
            "section_candidates": section_candidates,
            "section_boundary_audit": section_boundary_audit,
            "section_quality": section_quality,
        }

    found_names = [s["section_key"] for s in sections]
    logger.info(
        "extract_sections: found %d boundaries: %s",
        len(sections), found_names,
        extra={"stage": "extract_sections"},
    )
    return {
        "sections": sections,
        "document_nodes": document_nodes,
        "section_candidates": section_candidates,
        "section_boundary_audit": section_boundary_audit,
        "section_quality": section_quality,
    }
