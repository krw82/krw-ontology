"""Stage 7.1: Resolve ticker to CIK via SEC company_tickers.json API."""

from __future__ import annotations

import logging

import httpx

from krw_ontology.config.settings import PipelineConfig
from krw_ontology.errors import PipelineStageError

logger = logging.getLogger("krw_ontology")

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def resolve_ticker(ticker: str, config: PipelineConfig) -> dict:
    """Resolve a stock ticker to its SEC CIK.

    Returns dict with: cik, company_name, ticker, exchange.
    """
    ticker_upper = ticker.upper()
    headers = {"User-Agent": config.sec_user_agent}

    try:
        resp = httpx.get(SEC_TICKERS_URL, headers=headers, timeout=120.0, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, Exception) as e:
        raise PipelineStageError(f"resolve_ticker: failed to fetch company_tickers.json: {e}") from e

    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            cik = str(entry["cik_str"]).zfill(10)
            return {
                "cik": cik,
                "company_name": entry.get("title", ""),
                "ticker": ticker_upper,
                "exchange": None,
            }

    raise PipelineStageError(f"resolve_ticker: ticker '{ticker}' not found in SEC company_tickers.json")
