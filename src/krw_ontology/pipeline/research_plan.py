"""Build ticker-level research filing plans from SEC submissions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from krw_ontology.config.settings import PipelineConfig
from krw_ontology.errors import PipelineStageError
from krw_ontology.pipeline.stages.discover_source import list_source_filings
from krw_ontology.pipeline.stages.resolve_ticker import resolve_ticker


@dataclass(frozen=True)
class ResearchFilingTarget:
    """A filing period that should be processed by the ontology pipeline."""

    ticker: str
    document_type: str
    period: str
    accession_number: str
    filing_date: str
    report_date: str


def discover_research_filing_targets(
    ticker: str,
    *,
    years: int,
    config: PipelineConfig,
) -> list[ResearchFilingTarget]:
    """Discover latest annual filings and current calendar year quarterly filings."""
    if years < 1:
        raise ValueError("years must be at least 1")

    ticker = ticker.upper()
    resolved = resolve_ticker(ticker, config)
    filings = list_source_filings(resolved["cik"], ("10-K", "10-Q"), config)
    return select_research_filing_targets(ticker, filings, years=years)


def select_research_filing_targets(
    ticker: str,
    filings: Iterable[dict],
    *,
    years: int,
) -> list[ResearchFilingTarget]:
    """Select current-year 10-Q/10-K filings plus past-year annual filings."""
    if years < 1:
        raise ValueError("years must be at least 1")

    ticker = ticker.upper()
    normalized_filings = list(filings)
    annuals = _latest_unique_periods(
        (filing for filing in normalized_filings if filing.get("document_type") == "10-K"),
        limit=None,
    )
    if not annuals:
        raise PipelineStageError(f"research_plan: no 10-K filings found for {ticker}")

    quarterlies = _latest_unique_periods(
        (
            filing
            for filing in normalized_filings
            if filing.get("document_type") == "10-Q"
        ),
        limit=None,
    )
    current_calendar_year = date.today().year
    current_year_filings = [
        filing
        for filing in [*quarterlies, *annuals]
        if _period_year(filing.get("period")) == current_calendar_year
    ]
    if current_year_filings:
        selected_filings = [
            *current_year_filings,
            *[
                filing
                for filing in annuals
                if (_period_year(filing.get("period")) or 0) < current_calendar_year
            ][: max(0, years - 1)],
        ]
    else:
        selected_filings = annuals[:years]

    targets = [
        _to_target(ticker, filing)
        for filing in selected_filings
        if filing.get("period")
    ]
    return sorted(targets, key=_target_sort_key)


def _latest_unique_periods(filings: Iterable[dict], *, limit: int | None) -> list[dict]:
    selected: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for filing in filings:
        document_type = filing.get("document_type")
        period = filing.get("period")
        if not document_type or not period:
            continue
        key = (document_type, period)
        if key in seen:
            continue
        seen.add(key)
        selected.append(filing)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def _to_target(ticker: str, filing: dict) -> ResearchFilingTarget:
    return ResearchFilingTarget(
        ticker=ticker,
        document_type=str(filing["document_type"]),
        period=str(filing["period"]),
        accession_number=str(filing.get("accession_number") or ""),
        filing_date=str(filing.get("filing_date") or ""),
        report_date=str(filing.get("report_date") or ""),
    )


def _target_sort_key(target: ResearchFilingTarget) -> tuple[int, int, str]:
    year = _period_year(target.period) or 0
    quarter = _period_quarter(target.period)
    period_rank = quarter if quarter else 4
    return year, period_rank, target.document_type


def _period_year(period: str | None) -> int | None:
    if not period:
        return None
    normalized = period.upper()
    if normalized.startswith(("FY", "CY")):
        normalized = normalized[2:]
    if len(normalized) < 4:
        return None
    year_text = normalized[:4]
    return int(year_text) if year_text.isdigit() else None


def _period_quarter(period: str | None) -> int | None:
    if not period or "Q" not in period.upper():
        return None
    quarter_text = period.upper().rsplit("Q", 1)[-1]
    return int(quarter_text) if quarter_text.isdigit() else None
