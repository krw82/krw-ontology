"""Stage 7.1: Resolve ticker to CIK via SEC company_tickers.json API."""

from __future__ import annotations

import logging
from functools import lru_cache

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
    data = _fetch_company_tickers(config.sec_user_agent)

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


@lru_cache(maxsize=8)
def _fetch_company_tickers(user_agent: str) -> dict:
    headers = {"User-Agent": user_agent}
    try:
        resp = httpx.get(SEC_TICKERS_URL, headers=headers, timeout=120.0, follow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, Exception) as e:
        raise PipelineStageError(f"resolve_ticker: failed to fetch company_tickers.json: {e}") from e
    if not isinstance(payload, dict):
        raise PipelineStageError("resolve_ticker: company_tickers.json response is not an object")
    return payload
