"""End-to-end runner for the standalone guru ontology pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.guru.extractor import DEFAULT_AGENT_SDK_CONCURRENCY, extract_guru_ontology
from krw_ontology.guru.fetcher import fetch_guru_sources
from krw_ontology.guru.parser import DEFAULT_MAX_SPAN_CHARS, parse_guru_sources
from krw_ontology.guru.workspace import (
    guru_root,
    guru_running_root,
    initialize_guru_workspace,
)


def run_guru_pipeline(
    root: Path | str | None = None,
    *,
    running_root: Path | str | None = None,
    author_keys: list[str] | None = None,
    force: bool = False,
    limit_per_author: int | None = None,
    discover: bool = True,
    execute_agent_sdk: bool = False,
    model: str | None = None,
    max_batches: int | None = None,
    concurrency: int = DEFAULT_AGENT_SDK_CONCURRENCY,
    max_span_chars: int = DEFAULT_MAX_SPAN_CHARS,
    client: httpx.Client | None = None,
    worker: ExtractionWorker | None = None,
) -> dict[str, Any]:
    """Run init, fetch, parse, and extraction planning for guru sources.

    Claude Agent SDK execution is opt-in through ``execute_agent_sdk``.
    """
    root_path = guru_root(root)
    running_path = guru_running_root(running_root)
    initialized = _ensure_workspace(
        root_path,
        running_path=running_path,
        author_keys=author_keys,
        force=force,
    )
    raw_manifest = fetch_guru_sources(
        root_path,
        running_root=running_path,
        limit_per_author=limit_per_author,
        overwrite=force,
        discover=discover,
        client=client,
    )
    parsed_manifest = parse_guru_sources(
        root_path,
        running_root=running_path,
        overwrite=force,
        max_span_chars=max_span_chars,
    )
    extraction_manifest = extract_guru_ontology(
        root_path,
        running_root=running_path,
        execute_agent_sdk=execute_agent_sdk,
        model=model,
        max_batches=max_batches,
        concurrency=concurrency,
        worker=worker,
    )
    raw_errors = [document for document in raw_manifest.raw_documents if document.status == "error"]
    parsed_errors = [
        document for document in parsed_manifest.parsed_documents if document.status == "error"
    ]
    parsed_documents = [
        document for document in parsed_manifest.parsed_documents if document.status == "parsed"
    ]
    return {
        "root": str(root_path),
        "running_root": str(running_path),
        "initialized": initialized,
        "collection_started": True,
        "extraction_started": extraction_manifest.extraction_started,
        "execution_mode": extraction_manifest.execution_mode,
        "agent_sdk_called": extraction_manifest.agent_sdk_called,
        "concurrency": extraction_manifest.concurrency,
        "raw_documents": len(raw_manifest.raw_documents),
        "raw_errors": len(raw_errors),
        "parsed_documents": len(parsed_documents),
        "parsed_errors": len(parsed_errors),
        "extraction_batches": len(extraction_manifest.batches),
        "files": {
            "source_manifest": str(root_path / "source_manifest.yaml"),
            "collection_plan": str(root_path / "collection_plan.json"),
            "ontology_manifest": str(root_path / "ontology_manifest.json"),
            "raw_manifest": str(running_path / "raw_manifest.json"),
            "parsed_manifest": str(running_path / "parsed_manifest.json"),
            "extraction_manifest": str(running_path / "generated" / "extraction_manifest.json"),
        },
    }


def _ensure_workspace(
    root: Path,
    *,
    running_path: Path,
    author_keys: list[str] | None,
    force: bool,
) -> bool:
    required = (
        root / "source_manifest.yaml",
        root / "collection_plan.json",
        root / "ontology_manifest.json",
    )
    if force or any(not path.exists() for path in required):
        initialize_guru_workspace(
            root,
            running_root=running_path,
            author_keys=author_keys,
            force=force,
        )
        return True
    return False
