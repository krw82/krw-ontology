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
    """Repeated semantic item labels do not break canonical section order."""

    def test_repeated_item1_after_item1a_is_audited_not_selected(self, tmp_path: Path):
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

        result = extract_sections(md)
        sections = result["sections"]
        item1_sections = [s for s in sections if s["name"] == "item1"]
        repeated_item1_candidates = [
            row for row in result["section_boundary_audit"]
            if row["section_name"] == "item1" and not row["selected"]
        ]

        assert [s["section_key"] for s in item1_sections] == ["item1_00"]
        assert [s["section_instance"] for s in item1_sections] == [0]
        assert any(s["name"] == "item1a" and s["section_key"] == "item1a_00" for s in sections)
        assert repeated_item1_candidates


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

    def test_toc_only_entries_are_not_promoted_to_sections(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        md.write_text(
            "# Table of Contents\n\n"
            "Item 1. Business    4\n\n"
            "Item 1A. Risk Factors    12\n\n"
            "Item 7. Management's Discussion and Analysis    33\n\n"
            "Item 8. Financial Statements and Supplementary Data    55\n"
        )

        result = extract_sections(md)

        assert result["sections"] == []
        assert result["section_quality"]["status"] == "fail"
        assert "no_sections_detected" in result["section_quality"]["fail_reasons"]

    def test_title_only_core_headings_are_high_confidence_boundaries(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Body text long enough to avoid short-section confidence penalties. " * 10
        md.write_text(
            "# BUSINESS\n\n"
            f"{body}\n\n"
            "# RISK FACTORS\n\n"
            f"{body}\n\n"
            "# MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS\n\n"
            f"{body}\n\n"
            "# QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK\n\n"
            f"{body}\n\n"
            "# Financial Statements and Supplementary Data\n\n"
            f"{body}\n"
        )

        result = extract_sections(md)

        assert [s["name"] for s in result["sections"]] == [
            "item1",
            "item1a",
            "item7",
            "item7a",
            "item8",
        ]
        assert result["section_quality"]["status"] == "pass"
        assert all(s["section_detection_confidence"] == "high" for s in result["sections"])

    def test_dash_separated_item_headings_are_boundaries(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Body text long enough for section confidence. " * 12
        md.write_text(
            "Item 1—Business\n\n"
            f"{body}\n\n"
            "Item 1A—Risk Factors\n\n"
            f"{body}\n\n"
            "Item 7—Management's Discussion and Analysis\n\n"
            f"{body}\n\n"
            "Item 7A—Quantitative and Qualitative Disclosures About Market Risk\n\n"
            f"{body}\n\n"
            "Item 8—Financial Statements and Supplementary Data\n\n"
            f"{body}\n"
        )

        result = extract_sections(md)

        assert [s["name"] for s in result["sections"]] == [
            "item1",
            "item1a",
            "item7",
            "item7a",
            "item8",
        ]
        assert result["section_quality"]["status"] == "pass"

    def test_title_only_table_row_body_headings_are_selectable(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Body text long enough for section confidence. " * 12
        md.write_text(
            "| MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS | | |\n\n"
            f"{body}\n\n"
            "| Financial Statements and Supplementary Data | | |\n\n"
            f"{body}\n"
        )

        sections = extract_sections(md)["sections"]

        assert [s["name"] for s in sections] == ["item7", "item8"]
        assert all(s["section_detection_confidence"] != "low" for s in sections)

    def test_raw_html_heading_recovers_flattened_markdown_boundary(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        raw_html = tmp_path / "raw.html"
        body = "Management discussion body with enough text for a body section. " * 12
        md.write_text(
            "Some intro text that markdownify flattened.\n"
            "Item 7. Management's Discussion and Analysis\n"
            f"{body}\n"
        )
        raw_html.write_text(
            "<html><body><span><b>Item 7. Management's Discussion and Analysis</b></span>"
            f"<p>{body}</p></body></html>"
        )

        result = extract_sections(md, raw_html_path=raw_html)

        assert [s["name"] for s in result["sections"]] == ["item7"]
        selected = [row for row in result["section_boundary_audit"] if row["selected"]]
        assert selected[0]["source"] == "raw_html_heading"

    def test_adjacent_item_label_and_title_heading_are_merged(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "MD&A body content that should belong to a single Item 7 section. " * 12
        md.write_text(
            "Item 7. Example Corporation and Subsidiaries\n\n"
            "Management's Discussion and Analysis of Financial Condition and Results of Operations\n\n"
            f"{body}\n\n"
            "Item 7A. Quantitative and Qualitative Disclosures About Market Risk\n\n"
            f"{body}\n"
        )

        sections = extract_sections(md)["sections"]

        assert [s["section_key"] for s in sections] == ["item7_00", "item7a_00"]
        assert sections[0]["section_detection_confidence"] == "high"

    def test_cross_references_are_not_selected_as_boundaries(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Body text long enough for section confidence. " * 12
        md.write_text(
            "Item 1. Business\n\n"
            f"{body}\n\n"
            "Item 1A. Risk Factors\n\n"
            f"{body}\n\n"
            "Item 1A. Risk Factors of this Annual Report on Form 10-K\n\n"
            "This sentence points back to risk factors but is not a section boundary.\n\n"
            "Item 7. Management's Discussion and Analysis\n\n"
            f"{body}\n"
        )

        result = extract_sections(md)

        assert [s["section_key"] for s in result["sections"]] == ["item1_00", "item1a_00", "item7_00"]
        rejected = [
            row for row in result["section_boundary_audit"]
            if row["rejection_reason"] == "cross_reference_candidate"
        ]
        assert rejected

    def test_ordered_path_keeps_later_core_heading_after_repeated_early_items(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Body text long enough for section confidence. " * 12
        md.write_text(
            "Item 1. Business\n\n"
            f"{body}\n\n"
            "Item 1A. Risk Factors\n\n"
            f"{body}\n\n"
            "Item 1. Business Segment Discussion\n\n"
            f"{body}\n\n"
            "Item 7. Management's Discussion and Analysis\n\n"
            f"{body}\n\n"
            "Item 7A. Quantitative and Qualitative Disclosures About Market Risk\n\n"
            f"{body}\n\n"
            "Item 8. Financial Statements and Supplementary Data\n\n"
            f"{body}\n"
        )

        sections = extract_sections(md)["sections"]

        assert [s["section_key"] for s in sections] == [
            "item1_00",
            "item1a_00",
            "item7_00",
            "item7a_00",
            "item8_00",
        ]

    def test_company_specific_title_only_headings_map_to_core_items(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Body text long enough for section confidence. " * 12
        md.write_text(
            "# BUSINESS SUMMARY\n\n"
            f"{body}\n\n"
            "# RISK FACTORS\n\n"
            f"{body}\n\n"
            "# MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS\n\n"
            f"{body}\n\n"
            "# FINANCING AND MARKET RISK\n\n"
            f"{body}\n\n"
            "# CONSOLIDATED FINANCIAL STATEMENTS\n\n"
            f"{body}\n"
        )

        result = extract_sections(md)

        assert [s["name"] for s in result["sections"]] == [
            "item1",
            "item1a",
            "item7",
            "item7a",
            "item8",
        ]
        assert result["section_quality"]["status"] == "pass"

    def test_large_core_gap_marks_quality_fail(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Risk body content. " * 30
        md.write_text(
            "Item 1A. Risk Factors\n\n"
            f"{body}\n\n"
            "Item 15. Exhibits and Financial Statement Schedules\n\n"
            f"{body}\n"
        )

        result = extract_sections(md)

        assert [s["name"] for s in result["sections"]] == ["item1a", "item15"]
        assert result["section_quality"]["status"] == "fail"
        assert "large_core_section_gap" in result["section_quality"]["fail_reasons"]

    def test_single_missing_core_section_remains_warning(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Body content long enough for confidence. " * 16
        md.write_text(
            "Item 1. Business\n\n"
            f"{body}\n\n"
            "Item 1A. Risk Factors\n\n"
            f"{body}\n\n"
            "Item 7. Management's Discussion and Analysis\n\n"
            f"{body}\n\n"
            "Item 8. Financial Statements and Supplementary Data\n\n"
            f"{body}\n"
        )

        result = extract_sections(md)

        assert result["section_quality"]["status"] == "warn"
        assert result["section_quality"]["missing_core_sections"] == ["item7a"]
        assert result["section_quality"]["fail_reasons"] == []

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

    def test_toc_table_links_do_not_suppress_nearby_body_table_heading(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Cisco-style body text long enough for section confidence. " * 12
        md.write_text(
            "[Table of Contents](#toc)\n\n"
            "| Item 1. | [Business](#business) | [1](#business) |\n\n"
            "| Item 1A. | [Risk Factors](#risk) | [11](#risk) |\n\n"
            "PART I\n\n"
            "| Item 1. | Business |\n\n"
            f"{body}\n\n"
            "| Item 1A. | Risk Factors |\n\n"
            f"{body}\n"
        )

        sections = extract_sections(md)["sections"]

        assert [s["section_key"] for s in sections] == ["item1_00", "item1a_00"]
        assert "Table of Contents" not in sections[0]["text"][:100]

    def test_raw_html_anchor_text_is_not_recovered_as_section_heading(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        raw_html = tmp_path / "raw.html"
        body = "Business body text long enough for section confidence. " * 12
        md.write_text(
            "Forward looking statements refer to [Item 1A. Risk Factors](#risk) in this report.\n\n"
            "Item 1. Business\n\n"
            f"{body}\n"
        )
        raw_html.write_text(
            "<html><body>"
            "<p>Forward looking statements refer to <a href='#risk'>Item 1A. Risk Factors</a> in this report.</p>"
            "<p><b>Item 1. Business</b></p>"
            f"<p>{body}</p>"
            "</body></html>"
        )

        sections = extract_sections(md, raw_html_path=raw_html)["sections"]

        assert [s["section_key"] for s in sections] == ["item1_00"]

    def test_item7a_after_item7_is_preferred_over_early_market_risk_subheading(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Body text long enough for section confidence. " * 12
        md.write_text(
            "Item 1A. Risk Factors\n\n"
            "Market Risk\n\n"
            f"{body}\n\n"
            "Item 7. Management's Discussion and Analysis\n\n"
            f"{body}\n\n"
            "Item 7A. Quantitative and Qualitative Disclosures About Market Risk\n\n"
            f"{body}\n"
        )

        sections = extract_sections(md)["sections"]

        assert [s["section_key"] for s in sections] == ["item1a_00", "item7_00", "item7a_00"]


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


class TestTenQSectionDetection:
    """10-Q section detection keeps Part I and Part II item namespaces separate."""

    def test_ten_q_part_context_disambiguates_reused_item_numbers(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Quarterly filing body text with enough substance for confidence. " * 12
        md.write_text(
            "PART I\n\n"
            "Item 1. Financial Statements\n\n"
            f"{body}\n\n"
            "Item 2. Management's Discussion and Analysis of Financial Condition and Results of Operations\n\n"
            f"{body}\n\n"
            "Item 3. Quantitative and Qualitative Disclosures About Market Risk\n\n"
            f"{body}\n\n"
            "Item 4. Controls and Procedures\n\n"
            f"{body}\n\n"
            "PART II\n\n"
            "Item 1. Legal Proceedings\n\n"
            f"{body}\n\n"
            "Item 1A. Risk Factors\n\n"
            "There have been no material changes to the risk factors previously disclosed.\n\n"
            "Item 6. Exhibits\n\n"
            f"{body}\n"
        )

        result = extract_sections(md, document_type="10-Q")

        assert [s["section_key"] for s in result["sections"]] == [
            "part1_item1_00",
            "part1_item2_00",
            "part1_item3_00",
            "part1_item4_00",
            "part2_item1_00",
            "part2_item1a_00",
            "part2_item6_00",
        ]
        assert result["section_quality"]["status"] == "pass"

    def test_ten_q_toc_entries_are_not_selected(self, tmp_path: Path):
        md = tmp_path / "clean.md"
        body = "Quarterly filing body text with enough substance for confidence. " * 12
        md.write_text(
            "# Table of Contents\n\n"
            "Part I\n\n"
            "Item 1. Financial Statements    4\n\n"
            "Item 2. Management's Discussion and Analysis    22\n\n"
            "Item 4. Controls and Procedures    45\n\n"
            "Part II\n\n"
            "Item 1A. Risk Factors    51\n\n"
            "PART I\n\n"
            "Item 1. Financial Statements\n\n"
            f"{body}\n\n"
            "Item 2. Management's Discussion and Analysis\n\n"
            f"{body}\n\n"
            "Item 4. Controls and Procedures\n\n"
            f"{body}\n\n"
            "PART II\n\n"
            "Item 1A. Risk Factors\n\n"
            f"{body}\n"
        )

        result = extract_sections(md, document_type="10-Q")

        assert [s["section_key"] for s in result["sections"]] == [
            "part1_item1_00",
            "part1_item2_00",
            "part1_item4_00",
            "part2_item1a_00",
        ]
        assert result["section_quality"]["status"] == "pass"
        assert any(row["is_toc_like"] and not row["selected"] for row in result["section_boundary_audit"])
