"""Tests for SEC filing discovery period selection."""

from __future__ import annotations

import httpx
import pytest

from krw_ontology.config.settings import PipelineConfig
from krw_ontology.errors import PipelineStageError
from krw_ontology.pipeline.stages.discover_source import discover_source


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _submissions_payload() -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["10-K", "10-K/A", "10-K"],
                "accessionNumber": ["000-latest", "000-amend", "000-older"],
                "filingDate": ["2025-11-01", "2025-11-15", "2024-11-01"],
                "reportDate": ["2025-09-30", "2025-09-30", "2024-09-30"],
                "primaryDocument": ["latest.htm", "amend.htm", "older.htm"],
            }
        }
    }


def test_discover_source_uses_matching_period(monkeypatch: pytest.MonkeyPatch):
    def fake_get(*args, **kwargs):
        return _Response(_submissions_payload())

    monkeypatch.setattr(httpx, "get", fake_get)

    result = discover_source(
        cik="0000320193",
        document_type="10-K",
        latest=False,
        config=PipelineConfig(),
        period="FY2024",
    )

    assert result["accession_number"] == "000-older"
    assert result["report_date"] == "2024-09-30"
    assert result["source_url"].endswith("/older.htm")


def test_discover_source_rejects_missing_period(monkeypatch: pytest.MonkeyPatch):
    def fake_get(*args, **kwargs):
        return _Response(_submissions_payload())

    monkeypatch.setattr(httpx, "get", fake_get)

    with pytest.raises(PipelineStageError, match="FY2023"):
        discover_source(
            cik="0000320193",
            document_type="10-K",
            latest=False,
            config=PipelineConfig(),
            period="FY2023",
        )

