"""Shared fixtures for krw-ontology tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture
def mini_10k_html(project_root: Path) -> Path:
    return project_root / "tests" / "fixtures" / "mini_10k.html"


@pytest.fixture
def mini_10k_clean_md(project_root: Path) -> Path:
    return project_root / "tests" / "fixtures" / "mini_10k_clean.md"


@pytest.fixture
def mini_10k_spans(mini_10k_clean_md: Path) -> list[dict]:
    """Parse mini_10k_spans.jsonl and return list of dicts."""
    spans_path = mini_10k_clean_md.parent / "mini_10k_spans.jsonl"
    from krw_ontology.utils.io import read_jsonl
    return read_jsonl(spans_path)


@pytest.fixture
def sample_source_span() -> dict:
    return {
        "id": "span:AAPL:FY2025:10K:item1a:0042",
        "type": "SourceSpan",
        "ticker": "AAPL",
        "source_document_id": "source:AAPL:FY2025:10K",
        "document_type": "10-K",
        "period": "FY2025",
        "section_name": "item1a",
        "section_number": "1A",
        "span_index": 42,
        "start_char": 12000,
        "end_char": 12500,
        "text": "The Company relies on a limited number of suppliers for critical components. Any disruption in the supply chain could materially adversely affect the Company's business.",
        "text_hash": "sha256:abcdef1234567890",
        "char_count": 500,
        "section_detection_confidence": "high",
        "section_detection_method": "regex",
        "schema_version": "0.1.0",
    }


@pytest.fixture
def sample_evidence_quote(sample_source_span: dict) -> dict:
    return {
        "id": "quote:AAPL:FY2025:10K:item1a:0042:001",
        "type": "EvidenceQuote",
        "ticker": "AAPL",
        "source_document_id": "source:AAPL:FY2025:10K",
        "document_type": "10-K",
        "period": "FY2025",
        "source_span_id": sample_source_span["id"],
        "quote_text": "Any disruption in the supply chain could materially adversely affect the Company's business.",
        "quote_type": "risk_language",
        "section_name": "item1a",
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }


@pytest.fixture
def sample_research_claim(sample_evidence_quote: dict) -> dict:
    return {
        "id": "claim:AAPL:FY2025:10K:supply-chain-risk",
        "type": "ResearchClaim",
        "ticker": "AAPL",
        "source_document_id": "source:AAPL:FY2025:10K",
        "document_type": "10-K",
        "period": "FY2025",
        "claim_text": "Supply chain disruption poses a material risk to the Company's operations.",
        "claim_type": "risk_assessment",
        "supported_by_quotes": [sample_evidence_quote["id"]],
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }


@pytest.fixture
def metric_dictionary(project_root: Path) -> dict:
    path = project_root / "ontology" / "schema" / "metric_dictionary.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


@pytest.fixture
def relations_whitelist(project_root: Path) -> list[dict]:
    path = project_root / "ontology" / "schema" / "relations.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    return data["relations"]


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Temporary workspace directory for pipeline tests."""
    return tmp_path
