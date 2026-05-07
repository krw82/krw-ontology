"""Tests for filing-canonical inline XBRL extraction."""

from __future__ import annotations

from pathlib import Path

from krw_ontology.pipeline.stages.extract_xbrl import extract_xbrl
from krw_ontology.utils.io import read_jsonl


def test_extract_xbrl_builds_financial_and_derived_values(tmp_path: Path):
    raw_html = tmp_path / "raw.html"
    raw_html.write_text(
        """
        <html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
              xmlns:xbrli="http://www.xbrl.org/2003/instance"
              xmlns:us-gaap="http://fasb.org/us-gaap/2025">
          <body>
            <xbrli:context id="c-current">
              <xbrli:entity><xbrli:identifier>0000320193</xbrli:identifier></xbrli:entity>
              <xbrli:period><xbrli:startDate>2024-09-29</xbrli:startDate><xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period>
            </xbrli:context>
            <xbrli:context id="c-prior">
              <xbrli:entity><xbrli:identifier>0000320193</xbrli:identifier></xbrli:entity>
              <xbrli:period><xbrli:startDate>2023-10-01</xbrli:startDate><xbrli:endDate>2024-09-28</xbrli:endDate></xbrli:period>
            </xbrli:context>
            <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-current" unitRef="usd" scale="6">416,161</ix:nonFraction>
            <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-prior" unitRef="usd" scale="6">391,035</ix:nonFraction>
            <ix:nonFraction name="us-gaap:GrossProfit" contextRef="c-current" unitRef="usd" scale="6">195,201</ix:nonFraction>
          </body>
        </html>
        """
    )
    output_path = tmp_path / "xbrl_facts.jsonl"

    result = extract_xbrl(
        raw_html_path=raw_html,
        ticker="AAPL",
        period="FY2025",
        doc_type_key="10K",
        source_document_id="source:AAPL:FY2025:10K",
        output_path=output_path,
    )

    assert result["status"] == "ok"
    facts = read_jsonl(output_path)
    metric_values = read_jsonl(tmp_path / "financial_metric_values.jsonl")
    derived_values = read_jsonl(tmp_path / "derived_metric_values.jsonl")

    assert len(facts) == 3
    assert any(row["metric_name"] == "revenue" and row["value"] == 416_161_000_000 for row in metric_values)
    assert not any(row["metric_name"] == "gross_margin" for row in metric_values)
    revenue_growth = next(row for row in derived_values if row["metric_name"] == "revenue_growth")
    gross_margin = next(row for row in derived_values if row["metric_name"] == "gross_margin")
    assert round(revenue_growth["value"], 1) == 6.4
    assert round(gross_margin["value"], 1) == 46.9


def test_extract_xbrl_preserves_10q_document_and_period_context(tmp_path: Path):
    raw_html = tmp_path / "raw.html"
    raw_html.write_text(
        """
        <html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
              xmlns:xbrli="http://www.xbrl.org/2003/instance"
              xmlns:us-gaap="http://fasb.org/us-gaap/2025">
          <body>
            <xbrli:context id="q-current">
              <xbrli:entity><xbrli:identifier>0000320193</xbrli:identifier></xbrli:entity>
              <xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate><xbrli:endDate>2025-06-30</xbrli:endDate></xbrli:period>
            </xbrli:context>
            <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="q-current" unitRef="usd" scale="6">95,000</ix:nonFraction>
          </body>
        </html>
        """
    )
    output_path = tmp_path / "xbrl_facts.jsonl"

    extract_xbrl(
        raw_html_path=raw_html,
        ticker="AAPL",
        period="FY2025Q2",
        doc_type_key="10Q",
        source_document_id="source:AAPL:FY2025Q2:10Q",
        output_path=output_path,
        document_type="10-Q",
    )

    fact = read_jsonl(output_path)[0]
    metric = next(row for row in read_jsonl(tmp_path / "financial_metric_values.jsonl") if row["metric_name"] == "revenue")
    assert fact["id"].startswith("xbrl:AAPL:FY2025Q2:10Q:")
    assert fact["document_type"] == "10-Q"
    assert metric["document_type"] == "10-Q"
    assert metric["fiscal_period"] == "Q2"
    assert metric["period_type"] == "quarter"


def test_extract_xbrl_uses_sibling_inline_document_when_primary_has_no_ix_tags(tmp_path: Path):
    raw_html = tmp_path / "raw.html"
    raw_html.write_text("<html><body>Primary filing shell without inline XBRL.</body></html>")
    inline_html = tmp_path / "inline-document.htm"
    inline_html.write_text(
        """
        <html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
              xmlns:xbrli="http://www.xbrl.org/2003/instance"
              xmlns:us-gaap="http://fasb.org/us-gaap/2025">
          <body>
            <xbrli:context id="c-current">
              <xbrli:entity><xbrli:identifier>0000320193</xbrli:identifier></xbrli:entity>
              <xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period>
            </xbrli:context>
            <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-current" unitRef="usd" scale="6">10</ix:nonFraction>
          </body>
        </html>
        """
    )

    result = extract_xbrl(
        raw_html_path=raw_html,
        ticker="VG",
        period="FY2024",
        doc_type_key="10K",
        source_document_id="source:VG:FY2024:10K",
        output_path=tmp_path / "xbrl_facts.jsonl",
    )

    facts = read_jsonl(tmp_path / "xbrl_facts.jsonl")
    assert result["status"] == "ok"
    assert facts[0]["source_filing_detail"] == str(inline_html)
    assert facts[0]["value"] == 10_000_000


def test_extract_xbrl_writes_empty_metric_outputs_when_no_inline_xbrl(tmp_path: Path):
    raw_html = tmp_path / "raw.html"
    raw_html.write_text("<html><body>No inline XBRL.</body></html>")

    result = extract_xbrl(
        raw_html_path=raw_html,
        ticker="VG",
        period="FY2024",
        doc_type_key="10K",
        source_document_id="source:VG:FY2024:10K",
        output_path=tmp_path / "xbrl_facts.jsonl",
    )

    assert result["status"] == "missing_or_failed"
    assert read_jsonl(tmp_path / "xbrl_facts.jsonl") == []
    assert read_jsonl(tmp_path / "financial_metric_values.jsonl") == []
    assert read_jsonl(tmp_path / "derived_metric_values.jsonl") == []
