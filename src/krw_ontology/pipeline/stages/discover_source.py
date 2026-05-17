"""Stage 7.2: Discover source document via SEC submissions API."""

from __future__ import annotations

import logging
import re

import httpx

from krw_ontology.config.settings import PipelineConfig
from krw_ontology.errors import PipelineStageError

logger = logging.getLogger("krw_ontology")

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_PERIOD_RE = re.compile(r"^(?:FY|CY)?(?P<year>\d{4})(?:Q(?P<quarter>[1-4]))?$", re.IGNORECASE)


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
    data = _fetch_submissions(cik, config)
    filings = _extract_source_filings(cik, data, {document_type})

    for filing in filings:
        if _filing_matches_period(
            document_type=document_type,
            period=period,
            report_date=filing.get("report_date"),
            filing_date=filing.get("filing_date"),
            fiscal_year=filing.get("fiscal_year"),
            fiscal_period=filing.get("fiscal_period"),
            fiscal_year_end=filing.get("fiscal_year_end"),
        ):
            return {
                "accession_number": filing["accession_number"],
                "filing_date": filing["filing_date"],
                "report_date": filing["report_date"],
                "fiscal_year": filing.get("fiscal_year"),
                "fiscal_period": filing.get("fiscal_period"),
                "fiscal_year_end": filing.get("fiscal_year_end"),
                "primary_document": filing["primary_document"],
                "source_url": filing["source_url"],
            }

    raise PipelineStageError(
        f"discover_source: no {document_type} filing found for CIK {cik}"
        + (f" and period {period}" if period else "")
    )


def list_source_filings(
    cik: str,
    document_types: set[str] | tuple[str, ...] | list[str],
    config: PipelineConfig,
) -> list[dict]:
    """List normalized SEC filings for the given document types in recent-first order."""
    data = _fetch_submissions(cik, config)
    return _extract_source_filings(cik, data, set(document_types))


def derive_period_key(
    document_type: str,
    report_date: str | None,
    filing_date: str | None,
    *,
    fiscal_year: object = None,
    fiscal_period: object = None,
    fiscal_year_end: object = None,
) -> str:
    """Derive the pipeline period key from SEC reportDate/filingDate calendar dates."""
    date_value = report_date or filing_date or ""
    year = date_value[:4] if len(date_value) >= 4 and date_value[:4].isdigit() else "unknown"
    if document_type == "10-Q":
        quarter = _filing_quarter(report_date, filing_date)
        return f"CY{year}Q{quarter}" if quarter else f"CY{year}Q?"
    return f"CY{year}"


def _value_at(values: list[object], index: int) -> object:
    return values[index] if index < len(values) else None


def _normalize_fiscal_year(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"\d{4}", text)
    return match.group(0) if match else None


def _normalize_fiscal_period(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _fiscal_period_quarter(value: object) -> int | None:
    text = _normalize_fiscal_period(value)
    if not text:
        return None
    if text in {"1", "2", "3", "4"}:
        return int(text)
    match = re.fullmatch(r"Q([1-4])", text)
    return int(match.group(1)) if match else None


def _fetch_submissions(cik: str, config: PipelineConfig) -> dict:
    headers = {"User-Agent": config.sec_user_agent}
    url = SEC_SUBMISSIONS_URL.format(cik=cik)

    try:
        resp = httpx.get(url, headers=headers, timeout=120.0, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, Exception) as e:
        raise PipelineStageError(f"discover_source: failed to fetch submissions for CIK {cik}: {e}") from e


def _extract_source_filings(cik: str, data: dict, document_types: set[str]) -> list[dict]:
    filings_data = data.get("filings", {}).get("recent", {})
    forms = filings_data.get("form", [])
    accession_numbers = filings_data.get("accessionNumber", [])
    filing_dates = filings_data.get("filingDate", [])
    primary_documents = filings_data.get("primaryDocument", [])
    report_dates = filings_data.get("reportDate", [])
    fiscal_years = filings_data.get("fy", []) or []
    fiscal_periods = filings_data.get("fp", []) or []

    normalized: list[dict] = []

    for i, form in enumerate(forms):
        base_form = form[:-2] if form.endswith("/A") else form
        if form.endswith("/A") and base_form in document_types:
            logger.info(
                "discover_source: skipping amendment %s filed %s",
                accession_numbers[i],
                filing_dates[i],
                extra={"stage": "discover_source_document", "amendment_skipped": True},
            )
            continue
        if form not in document_types:
            continue

        accession_number = accession_numbers[i]
        filing_date = filing_dates[i]
        report_date = report_dates[i] if i < len(report_dates) else ""
        primary_document = primary_documents[i] if i < len(primary_documents) else ""
        fiscal_year = _value_at(fiscal_years, i)
        fiscal_period = _value_at(fiscal_periods, i)
        accession_no_dashes = accession_number.replace("-", "")
        source_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{accession_no_dashes}/{primary_document}"
        )
        normalized.append(
            {
                "document_type": form,
                "period": derive_period_key(
                    form,
                    report_date,
                    filing_date,
                    fiscal_year=fiscal_year,
                    fiscal_period=fiscal_period,
                ),
                "accession_number": accession_number,
                "filing_date": filing_date,
                "report_date": report_date,
                "fiscal_year": _normalize_fiscal_year(fiscal_year),
                "fiscal_period": _normalize_fiscal_period(fiscal_period),
                "primary_document": primary_document,
                "source_url": source_url,
            }
        )
    return normalized


def _filing_matches_period(
    document_type: str,
    period: str | None,
    report_date: str | None,
    filing_date: str | None,
    fiscal_year: object = None,
    fiscal_period: object = None,
    fiscal_year_end: object = None,
) -> bool:
    derived_period = derive_period_key(
        document_type,
        report_date,
        filing_date,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
    )
    target_year = _period_year(period)
    if target_year and _period_year(derived_period) != target_year:
        return False
    target_quarter = _period_quarter(period)
    if document_type == "10-Q" and target_quarter:
        return _period_quarter(derived_period) == target_quarter
    return True


def _period_year(period: str | None) -> str | None:
    """Extract four-digit fiscal year from period strings like FY2025 or FY2025Q3."""
    if not period:
        return None
    match = _PERIOD_RE.match(period.strip())
    return match.group("year") if match else None


def _period_quarter(period: str | None) -> int | None:
    """Extract quarter number from period strings like FY2025Q3."""
    if not period:
        return None
    match = _PERIOD_RE.match(period.strip())
    if not match or not match.group("quarter"):
        return None
    return int(match.group("quarter"))


def _filing_year(report_date: str | None, filing_date: str | None) -> str | None:
    """Prefer reportDate year, falling back to filingDate year."""
    for value in (report_date, filing_date):
        if value and len(value) >= 4 and value[:4].isdigit():
            return value[:4]
    return None


def _filing_quarter(report_date: str | None, filing_date: str | None) -> int | None:
    """Prefer reportDate quarter, falling back to filingDate quarter."""
    for value in (report_date, filing_date):
        if not value or len(value) < 7:
            continue
        try:
            month = int(value[5:7])
        except ValueError:
            continue
        if 1 <= month <= 12:
            return (month - 1) // 3 + 1
    return None
