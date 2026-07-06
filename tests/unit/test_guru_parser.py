from __future__ import annotations

import json
from pathlib import Path

from krw_ontology.guru.models import GuruRawDocument, GuruRawManifest, utc_now_iso
import krw_ontology.guru.parser as guru_parser
from krw_ontology.guru.parser import parse_guru_sources, parse_raw_document_text


def test_parse_guru_sources_converts_html_to_private_spans(tmp_path: Path):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    raw_path = running_root / "raw" / "buffett" / "letter.html"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(
        """
        <html><body>
          <nav>Navigation</nav>
          <h1>2024 Letter</h1>
          <p>Capital allocation matters over long periods.</p>
        </body></html>
        """,
        encoding="utf-8",
    )
    _write_raw_manifest(root, running_root, raw_path, content_type="text/html")

    manifest = parse_guru_sources(root, running_root=running_root)

    parsed = manifest.parsed_documents[0]
    assert parsed.status == "parsed"
    assert parsed.parser == "beautifulsoup-markdownify"
    assert parsed.span_count >= 1
    spans_path = Path(parsed.spans_path or "")
    span_payload = json.loads(spans_path.read_text(encoding="utf-8").splitlines()[0])
    assert span_payload["storage_policy"] == "private_cache_only"
    assert "Capital allocation matters" in span_payload["text"]
    assert "Navigation" not in Path(parsed.parsed_path or "").read_text(encoding="utf-8")


def test_parse_raw_document_text_uses_pdf_parser(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "memo.pdf"
    pdf_path.write_bytes(b"%PDF fake")

    monkeypatch.setattr(
        guru_parser,
        "_extract_pdf_text",
        lambda path, **_: ("Margin of safety matters.", "fake_pdf_parser"),
    )

    text, parser_name = parse_raw_document_text(pdf_path, "application/pdf")

    assert text == "Margin of safety matters."
    assert parser_name == "fake_pdf_parser"


def test_pdf_fragment_page_start_is_zero_based():
    assert guru_parser._pdf_page_start_index("https://example.com/report.pdf#page=9") == 8
    assert guru_parser._pdf_page_start_index("https://example.com/report.pdf?page=3") == 2
    assert guru_parser._pdf_page_start_index("https://example.com/report.pdf#section") is None


def test_trim_pdf_pages_stops_before_financial_statement_sections():
    pages = [
        "Cover page",
        "LETTER TO SHAREHOLDERS To the Shareholders of Example Corp.",
        "Portfolio update and investment discussion.",
        "Principal Risks and Uncertainties The Board has responsibility.",
        "Financial Statements",
    ]

    trimmed = guru_parser._trim_pdf_page_texts(pages, start_index=1)

    assert trimmed == [
        "LETTER TO SHAREHOLDERS To the Shareholders of Example Corp.",
        "Portfolio update and investment discussion.",
    ]


def test_trim_pdf_pages_can_keep_full_standalone_pdf():
    pages = [
        "Standalone letter opening.",
        "Financial Statements mentioned in an appendix.",
    ]

    trimmed = guru_parser._trim_pdf_page_texts(pages, start_index=0, stop_at_section=False)

    assert trimmed == pages


def _write_raw_manifest(
    root: Path,
    running_root: Path,
    raw_path: Path,
    *,
    content_type: str,
) -> None:
    running_root.mkdir(parents=True, exist_ok=True)
    manifest = GuruRawManifest(
        generated_at=utc_now_iso(),
        root=str(root),
        running_root=str(running_root),
        source_count=1,
        raw_documents=[
            GuruRawDocument(
                source_id="buffett:2024-letter",
                author_key="buffett",
                title="2024 Letter",
                source_type="shareholder_letter",
                official_url="https://example.com/letter.html",
                content_type=content_type,
                raw_path=str(raw_path),
                sha256="test",
                byte_count=raw_path.stat().st_size,
                fetched_at=utc_now_iso(),
                status="fetched",
            )
        ],
    )
    (running_root / "raw_manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json")),
        encoding="utf-8",
    )
