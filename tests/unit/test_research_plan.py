"""Tests for default research filing target selection."""

from __future__ import annotations

import pytest

from krw_ontology.errors import PipelineStageError
from krw_ontology.pipeline.research_plan import select_research_filing_targets


def _filing(document_type: str, period: str) -> dict:
    return {
        "document_type": document_type,
        "period": period,
        "accession_number": f"accession-{document_type}-{period}",
        "filing_date": "2026-01-01",
        "report_date": "2025-12-31",
    }


def test_select_research_targets_uses_latest_annual_years_and_matching_quarters():
    filings = [
        _filing("10-K", "FY2026"),
        _filing("10-Q", "FY2026Q1"),
        _filing("10-K", "FY2025"),
        _filing("10-Q", "FY2025Q3"),
        _filing("10-Q", "FY2025Q2"),
        _filing("10-Q", "FY2025Q1"),
        _filing("10-K", "FY2024"),
        _filing("10-Q", "FY2024Q3"),
        _filing("10-Q", "FY2024Q2"),
        _filing("10-Q", "FY2024Q1"),
        _filing("10-K", "FY2023"),
        _filing("10-Q", "FY2023Q3"),
    ]

    targets = select_research_filing_targets("acme", filings, years=2)

    assert [(target.document_type, target.period) for target in targets] == [
        ("10-K", "FY2025"),
        ("10-Q", "FY2026Q1"),
        ("10-K", "FY2026"),
    ]


def test_select_research_targets_deduplicates_periods():
    filings = [
        _filing("10-K", "FY2025"),
        _filing("10-K", "FY2025"),
        _filing("10-Q", "FY2025Q3"),
        _filing("10-Q", "FY2025Q3"),
    ]

    targets = select_research_filing_targets("acme", filings, years=1)

    assert [(target.document_type, target.period) for target in targets] == [
        ("10-K", "FY2025"),
    ]


def test_select_research_targets_requires_annual_filings():
    with pytest.raises(PipelineStageError, match="no 10-K filings"):
        select_research_filing_targets("acme", [_filing("10-Q", "FY2025Q3")], years=1)
