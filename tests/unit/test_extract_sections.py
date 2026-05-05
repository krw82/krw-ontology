"""Tests for extract_sections stage — ATX heading and plain text detection."""

from __future__ import annotations

from pathlib import Path

from krw_ontology.pipeline.stages.extract_sections import extract_sections


class TestATXHeadingDetection:
    """Verify ATX heading markers are stripped before regex matching."""

    def test_h3_item1(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        md.write_text(
            "### Item 1. Business\n\n"
            "Apple Inc. designs and manufactures consumer electronics.\n\n"
            "### Item 1A. Risk Factors\n\n"
            "The Company faces various risks.\n"
        )
        result = extract_sections(md)
        names = [s["name"] for s in result["sections"]]
        assert "item1" in names
        assert "item1a" in names

    def test_h2_item1a(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        md.write_text(
            "## Item 1A. Risk Factors\n\n"
            "Risks related to competition.\n"
        )
        result = extract_sections(md)
        assert len(result["sections"]) == 1
        assert result["sections"][0]["name"] == "item1a"

    def test_h1_item7(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        md.write_text(
            "# Item 7. Management's Discussion and Analysis\n\n"
            "Revenue increased 14%.\n"
        )
        result = extract_sections(md)
        assert len(result["sections"]) == 1
        assert result["sections"][0]["name"] == "item7"


class TestPlainTextDetection:
    """Plain text headings (no ATX marker) still work."""

    def test_plain_item1(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        md.write_text(
            "Item 1. Business\n\n"
            "The Company operates globally.\n"
        )
        result = extract_sections(md)
        assert len(result["sections"]) == 1
        assert result["sections"][0]["name"] == "item1"


class TestSectionInstanceKeys:
    """Repeated semantic sections keep distinct ID keys."""

    def test_repeated_item1_gets_unique_section_keys(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "This business section has enough body text to survive TOC filtering. " * 8
        md.write_text(
            "Item 1. Business\n\n"
            f"First business block. {body}\n\n"
            "Item 1A. Risk Factors\n\n"
            f"Risk factor block. {body}\n\n"
            "Item 1. Business\n\n"
            f"Second business-like block from a repeated heading. {body}\n"
        )

        sections = extract_sections(md)["sections"]
        item1_sections = [s for s in sections if s["name"] == "item1"]

        assert [s["section_key"] for s in item1_sections] == ["item1_00", "item1_01"]
        assert [s["section_instance"] for s in item1_sections] == [0, 1]
        assert any(s["name"] == "item1a" and s["section_key"] == "item1a_00" for s in sections)


class TestSectionBoundaryScoring:
    """TOC candidates are audited but body headings are selected."""

    def test_toc_entries_are_not_selected_when_body_headings_exist(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "This section has enough body content to be treated as the real filing body. " * 10
        md.write_text(
            "# Table of Contents\n\n"
            "Item 1. Business    4\n\n"
            "Item 1A. Risk Factors    12\n\n"
            "Item 7. Management's Discussion and Analysis    33\n\n"
            "Item 8. Financial Statements and Supplementary Data    55\n\n"
            "### Item 1. Business\n\n"
            f"{body}\n\n"
            "### Item 1A. Risk Factors\n\n"
            f"{body}\n\n"
            "### Item 7. Management's Discussion and Analysis\n\n"
            f"{body}\n\n"
            "### Item 8. Financial Statements and Supplementary Data\n\n"
            f"{body}\n"
        )

        result = extract_sections(md)
        sections = result["sections"]
        audit = result["section_boundary_audit"]

        selected_text = "\n".join(section["text"][:80] for section in sections)
        assert "Table of Contents" not in selected_text
        assert [s["section_key"] for s in sections] == [
            "item1_00",
            "item1a_00",
            "item7_00",
            "item8_00",
        ]
        assert any(row["is_toc_like"] and not row["selected"] for row in audit)

    def test_writes_boundary_artifacts(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        out = tmp_path / "ontology"
        md.write_text(
            "### Item 7. Management's Discussion and Analysis\n\n"
            "Revenue increased 14% because demand improved.\n"
        )

        result = extract_sections(md, output_dir=out)

        assert result["document_nodes"]
        assert (out / "document_nodes.jsonl").exists()
        assert (out / "section_candidates.jsonl").exists()
        assert (out / "sections.jsonl").exists()
        assert (out / "section_boundary_audit.jsonl").exists()
        assert (out / "section_quality.json").exists()

    def test_markdown_table_row_headings_are_candidates(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        md.write_text(
            "| ITEM 1. BUSINESS | | |\n\n"
            "Business body text.\n\n"
            "| ITEM 1A. RISK FACTORS | | |\n\n"
            "Risk body text.\n\n"
            "| ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS | | |\n\n"
            "MD&A body text.\n\n"
            "| ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA | | |\n\n"
            "Financial statement body text.\n"
        )

        sections = extract_sections(md)["sections"]

        assert [s["section_key"] for s in sections] == [
            "item1_00",
            "item1a_00",
            "item7_00",
            "item8_00",
        ]


class TestNoSections:
    """Edge case: no recognizable sections."""

    def test_empty_file(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        md.write_text("")
        result = extract_sections(md)
        assert result["sections"] == []

    def test_no_match(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        md.write_text("This is just some random text with no 10-K items.\n")
        result = extract_sections(md)
        assert result["sections"] == []
