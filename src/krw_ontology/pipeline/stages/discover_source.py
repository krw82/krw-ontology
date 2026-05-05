"""Stage 7.2: Discover source document via SEC submissions API."""

from __future__ import annotations

import logging

import httpx

from krw_ontology.config.settings import PipelineConfig
from krw_ontology.errors import PipelineStageError

logger = logging.getLogger("krw_ontology")

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


def discover_source(
    cik: str,
    document_type: str,
    latest: bool,
    config: PipelineConfig,
    period: str | None = None,
) -> dict:
    """Discover the source filing for a given CIK and document type.

    Returns dict with: accession_number, filing_date, report_date, primary_document, source_url.
    """
    headers = {"User-Agent": config.sec_user_agent}
    url = SEC_SUBMISSIONS_URL.format(cik=cik)

    try:
        resp = httpx.get(url, headers=headers, timeout=120.0, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, Exception) as e:
        raise PipelineStageError(f"discover_source: failed to fetch submissions for CIK {cik}: {e}") from e

    filings = data.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    accession_numbers = filings.get("accessionNumber", [])
    filing_dates = filings.get("filingDate", [])
    primary_documents = filings.get("primaryDocument", [])
    report_dates = filings.get("reportDate", [])

    target_form = document_type
    target_year = _period_year(period)
    for i, form in enumerate(forms):
        if form == f"{target_form}/A":
            logger.info(
                "discover_source: skipping amendment %s filed %s",
                accession_numbers[i], filing_dates[i],
                extra={"stage": "discover_source_document", "amendment_skipped": True},
            )
            continue
        if form == target_form:
            accession_number = accession_numbers[i]
            filing_date = filing_dates[i]
            report_date = report_dates[i] if i < len(report_dates) else ""
            primary_document = primary_documents[i] if i < len(primary_documents) else ""

            if target_year and _filing_year(report_date, filing_date) != target_year:
                continue

            accession_no_dashes = accession_number.replace("-", "")
            source_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{accession_no_dashes}/{primary_document}"
            )

            return {
                "accession_number": accession_number,
                "filing_date": filing_date,
                "report_date": report_date,
                "primary_document": primary_document,
                "source_url": source_url,
            }

    raise PipelineStageError(
        f"discover_source: no {document_type} filing found for CIK {cik}"
        + (f" and period {period}" if period else "")
    )


def _period_year(period: str | None) -> str | None:
    """Extract four-digit fiscal year from period strings like FY2025."""
    if not period:
        return None
    if len(period) == 6 and period.upper().startswith("FY") and period[2:].isdigit():
        return period[2:]
    return None


def _filing_year(report_date: str | None, filing_date: str | None) -> str | None:
    """Prefer reportDate year, falling back to filingDate year."""
    for value in (report_date, filing_date):
        if value and len(value) >= 4 and value[:4].isdigit():
            return value[:4]
    return None
