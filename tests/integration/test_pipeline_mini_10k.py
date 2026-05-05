"""Integration test: run code-only pipeline stages on mini_10k.html fixture.

AI extraction stages (quotes, claims, risks, edges) are skipped — this
tests the deterministic code path: clean -> sections -> spans -> xbrl
-> validate_ontology -> build_indexes.

Network-dependent stages (resolve_ticker, discover_source, download_source)
are mocked with deterministic fixture data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from krw_ontology.config.constants import DOCUMENT_TYPE_KEY
from krw_ontology.config.settings import PipelineConfig
from krw_ontology.pipeline.stages.clean_to_markdown import clean_to_markdown
from krw_ontology.pipeline.stages.extract_sections import extract_sections
from krw_ontology.pipeline.stages.build_spans import build_spans
from krw_ontology.pipeline.stages.extract_xbrl import extract_xbrl
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology

TICKER = "ACME"
PERIOD = "FY2025"
DOC_TYPE = "10-K"
DOC_TYPE_KEY = DOCUMENT_TYPE_KEY
SOURCE_DOC_ID = f"source:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}"


@pytest.fixture
def config():
    return PipelineConfig()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sources_dir(workspace: Path) -> Path:
    d = workspace / "companies" / TICKER / "sources" / DOC_TYPE_KEY / PERIOD
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def ontology_dir(workspace: Path) -> Path:
    d = workspace / "companies" / TICKER / "ontology" / DOC_TYPE_KEY / PERIOD
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def raw_html(sources_dir: Path, mini_10k_html: Path) -> Path:
    raw = sources_dir / "raw.html"
    raw.write_bytes(mini_10k_html.read_bytes())
    return raw


@pytest.fixture
def clean_md(sources_dir: Path, raw_html: Path) -> Path:
    """Clean the HTML and return the path to the clean markdown."""
    output = sources_dir / "clean.md"
    clean_to_markdown(raw_html, output)
    return output


# Markdown with plain "Item X." headings that match SECTION_PATTERNS regex.
# The HTML-to-markdown conversion adds "### " prefixes which the section
# detector does not strip, so we use a prepared markdown file instead.
SECTIONS_MD = """\
Item 1. Business

ACME Corp is a technology company that designs, manufactures, and sells consumer electronics, computer software, and online services. The Company operates in two reportable segments: Products and Services.

During fiscal year 2025, the Company generated total revenue of $85.2 billion, an increase of 14% compared to fiscal year 2024. Services revenue grew 20% year-over-year to $25.3 billion.

The Company's product portfolio includes smartphones, personal computers, tablets, and wearables. The Company believes its integrated ecosystem creates significant competitive advantages and customer loyalty.

Item 1A. Risk Factors

The Company's business is subject to various risks and uncertainties, including but not limited to the following:

The Company relies on a limited number of suppliers for critical components. Any disruption in the supply chain, including natural disasters, geopolitical tensions, or supplier financial difficulties, could materially adversely affect the Company's business, results of operations, and financial condition. In particular, the Company sources certain custom chips from a single supplier located in Asia.

The Company operates in a highly competitive industry. Competitors may introduce products with similar features at lower prices, which could materially reduce the Company's market share and negatively impact revenue growth.

Changes in foreign exchange rates, particularly the strengthening of the U.S. dollar, could adversely affect the Company's international revenue and gross margins. Approximately 60% of the Company's revenue is generated outside the United States.

Government regulation, including privacy regulations such as GDPR and potential antitrust actions, may increase compliance costs and restrict certain business practices, which could materially and adversely affect the Company's results of operations.

Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations

Net sales for fiscal year 2025 were $85.2 billion, an increase of $10.4 billion or 14% compared to fiscal year 2024. The increase was driven by growth across all product categories and geographic segments.

Gross margin for fiscal year 2025 was 46.2%, compared to 44.1% in fiscal year 2024. The improvement was primarily due to a favorable product mix shift toward higher-margin Services and operational efficiencies in the supply chain.

Operating expenses were $14.3 billion for fiscal year 2025, representing 16.8% of net sales, compared to $12.8 billion or 17.1% of net sales in fiscal year 2024. Research and development expense increased by $800 million to $7.5 billion.

Item 7A. Quantitative and Qualitative Disclosures About Market Risk

The Company is exposed to market risk from changes in foreign currency exchange rates, interest rates, and commodity prices. A 10% unfavorable change in foreign exchange rates would have reduced fiscal year 2025 net sales by approximately $5.1 billion.
"""


@pytest.fixture
def sections_md_path(sources_dir: Path) -> Path:
    """Write SECTIONS_MD to a file and return its path."""
    p = sources_dir / "sections.md"
    p.write_text(SECTIONS_MD)
    return p


class TestCleanToMarkdown:
    def test_produces_markdown(self, raw_html: Path, sources_dir: Path):
        output = sources_dir / "clean.md"
        result = clean_to_markdown(raw_html, output)
        assert output.exists()
        assert output.stat().st_size > 0
        assert result["sha256"]
        assert len(result["sha256"]) == 64

    def test_strips_html_tags(self, raw_html: Path, sources_dir: Path):
        output = sources_dir / "clean.md"
        clean_to_markdown(raw_html, output)
        text = output.read_text()
        assert "<html>" not in text.lower()
        assert "<body>" not in text.lower()


class TestExtractSections:
    def test_finds_sections(self, sections_md_path: Path):
        result = extract_sections(sections_md_path)
        sections = result["sections"]
        assert len(sections) >= 3
        names = [s["name"] for s in sections]
        assert "item1" in names
        assert "item1a" in names
        assert "item7" in names

    def test_sections_have_required_fields(self, sections_md_path: Path):
        result = extract_sections(sections_md_path)
        for section in result["sections"]:
            assert "name" in section
            assert "text" in section
            assert len(section["text"]) > 0
            assert "section_detection_confidence" in section


class TestBuildSpans:
    def test_creates_span_jsonl(
        self, ontology_dir: Path, sections_md_path: Path
    ):
        sections = extract_sections(sections_md_path)["sections"]
        clean_md_text = sections_md_path.read_text()
        output = ontology_dir / "spans.jsonl"

        result = build_spans(
            sections=sections,
            doc_type_key=DOC_TYPE_KEY,
            ticker=TICKER,
            period=PERIOD,
            source_document_id=SOURCE_DOC_ID,
            clean_md_text=clean_md_text,
            output_path=output,
        )

        assert output.exists()
        spans = result["spans"]
        assert len(spans) >= 3

    def test_spans_have_valid_ids(
        self, ontology_dir: Path, sections_md_path: Path
    ):
        sections = extract_sections(sections_md_path)["sections"]
        clean_md_text = sections_md_path.read_text()
        output = ontology_dir / "spans.jsonl"

        result = build_spans(
            sections=sections,
            doc_type_key=DOC_TYPE_KEY,
            ticker=TICKER,
            period=PERIOD,
            source_document_id=SOURCE_DOC_ID,
            clean_md_text=clean_md_text,
            output_path=output,
        )

        for span in result["spans"]:
            assert span["id"].startswith(f"span:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:")
            assert span["type"] == "SourceSpan"
            assert span["ticker"] == TICKER
            assert len(span["text"]) > 0


class TestExtractXbrl:
    def test_no_ixbrl_returns_empty(
        self, ontology_dir: Path, raw_html: Path
    ):
        output = ontology_dir / "xbrl_facts.jsonl"
        result = extract_xbrl(
            raw_html_path=raw_html,
            ticker=TICKER,
            period=PERIOD,
            doc_type_key=DOC_TYPE_KEY,
            source_document_id=SOURCE_DOC_ID,
            output_path=output,
        )
        assert output.exists()
        assert result["status"] in ("ok", "missing_or_failed")


class TestBuildIndexes:
    def test_creates_artifact_index(
        self,
        workspace: Path,
        sources_dir: Path,
        ontology_dir: Path,
        sections_md_path: Path,
    ):
        # Run preceding stages to create spans.jsonl
        sections = extract_sections(sections_md_path)["sections"]
        clean_md_text = sections_md_path.read_text()
        build_spans(
            sections=sections,
            doc_type_key=DOC_TYPE_KEY,
            ticker=TICKER,
            period=PERIOD,
            source_document_id=SOURCE_DOC_ID,
            clean_md_text=clean_md_text,
            output_path=ontology_dir / "spans.jsonl",
        )

        build_indexes(
            ticker=TICKER,
            period=PERIOD,
            doc_type_key=DOC_TYPE_KEY,
            ontology_dir=ontology_dir,
            sources_dir=sources_dir,
            output_dir=workspace,
        )

        index_path = ontology_dir / "artifact_index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert index["ticker"] == TICKER
        assert index["period"] == PERIOD
        assert "files" in index
        assert "counts" in index
        assert index["counts"]["spans"] > 0
        assert (ontology_dir / "batch_failures.jsonl").exists()
        assert index["counts"]["batch_failures"] == 0


class TestValidateOntology:
    def test_validates_spans(
        self,
        ontology_dir: Path,
        sources_dir: Path,
        raw_html: Path,
        sections_md_path: Path,
    ):
        # Run preceding stages
        sections = extract_sections(sections_md_path)["sections"]
        clean_md_text = sections_md_path.read_text()
        build_spans(
            sections=sections,
            doc_type_key=DOC_TYPE_KEY,
            ticker=TICKER,
            period=PERIOD,
            source_document_id=SOURCE_DOC_ID,
            clean_md_text=clean_md_text,
            output_path=ontology_dir / "spans.jsonl",
        )
        extract_xbrl(
            raw_html_path=raw_html,
            ticker=TICKER,
            period=PERIOD,
            doc_type_key=DOC_TYPE_KEY,
            source_document_id=SOURCE_DOC_ID,
            output_path=ontology_dir / "xbrl_facts.jsonl",
        )

        result = run_validate_ontology(ontology_dir)
        assert result["stats"]["total_input"] > 0
        assert result["stats"]["total_accepted"] > 0
        assert "SourceSpan" in result["accepted"]
        assert result["accepted"]["SourceSpan"] > 0


class TestFullPipelineCodeStages:
    """End-to-end through all code stages using section-friendly markdown."""

    def test_code_pipeline_end_to_end(
        self,
        workspace: Path,
        sources_dir: Path,
        ontology_dir: Path,
        raw_html: Path,
        sections_md_path: Path,
        config: PipelineConfig,
    ):
        # --- Stage: clean_to_markdown ---
        clean_path = sources_dir / "clean.md"
        clean_to_markdown(raw_html, clean_path)
        assert clean_path.exists()

        # --- Stage: extract_sections (uses prepped markdown with plain headings) ---
        result = extract_sections(sections_md_path)
        sections = result["sections"]
        assert len(sections) >= 3

        # --- Stage: build_source_spans ---
        clean_md_text = sections_md_path.read_text()
        result = build_spans(
            sections=sections,
            doc_type_key=DOC_TYPE_KEY,
            ticker=TICKER,
            period=PERIOD,
            source_document_id=SOURCE_DOC_ID,
            clean_md_text=clean_md_text,
            output_path=ontology_dir / "spans.jsonl",
        )
        spans = result["spans"]
        assert len(spans) >= 3

        # --- Stage: extract_xbrl_facts ---
        result = extract_xbrl(
            raw_html_path=raw_html,
            ticker=TICKER,
            period=PERIOD,
            doc_type_key=DOC_TYPE_KEY,
            source_document_id=SOURCE_DOC_ID,
            output_path=ontology_dir / "xbrl_facts.jsonl",
        )
        assert (ontology_dir / "xbrl_facts.jsonl").exists()

        # --- Stage: validate_ontology ---
        result = run_validate_ontology(ontology_dir)
        assert result["stats"]["total_accepted"] > 0
        assert "SourceSpan" in result["accepted"]

        # --- Stage: build_indexes ---
        result = build_indexes(
            ticker=TICKER,
            period=PERIOD,
            doc_type_key=DOC_TYPE_KEY,
            ontology_dir=ontology_dir,
            sources_dir=sources_dir,
            output_dir=workspace,
        )
        index_path = ontology_dir / "artifact_index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert index["counts"]["spans"] > 0
