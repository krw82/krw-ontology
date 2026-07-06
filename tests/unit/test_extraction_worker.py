"""Tests for Claude structured output handling."""

from __future__ import annotations

import asyncio
import json

import pytest

import krw_ontology.extraction.worker as worker_module
from krw_ontology.errors import RateLimitError
from krw_ontology.extraction.worker import ExtractionWorker
from krw_ontology.extraction.worker import (
    parse_structured_output,
)


def test_quote_output_accepts_single_language_signal_dict():
    raw = """
    [{
      "id": "span:1",
      "quote_text": "Risk could affect operations.",
      "quote_type": "risk_language",
      "section_name": "item1a",
      "confidence": "high",
      "language_signals": {
        "type": "potential_negative",
        "strength": "strong",
        "direction": "negative",
        "certainty": "conditional",
        "temporal_scope": "future"
      }
    }]
    """

    items = parse_structured_output(raw, {}, "extract_evidence_quotes")

    assert len(items) == 1
    assert items[0]["language_signals"][0]["signal_type"] == "potential_negative"


def test_quote_output_accepts_language_signal_string_list():
    raw = """
    [{
      "id": "span:1",
      "quote_text": "Risk could affect operations.",
      "quote_type": "risk_language",
      "section_name": "item1a",
      "confidence": "high",
      "language_signals": ["potential_negative", "uncertainty"]
    }]
    """

    items = parse_structured_output(raw, {}, "extract_evidence_quotes")

    assert len(items) == 1
    assert items[0]["language_signals"][0]["signal_type"] == "potential_negative"
    assert items[0]["language_signals"][1]["signal_type"] == "uncertainty"


def test_numeric_confidence_is_normalized():
    raw = """
    [{
      "id": "claim:1",
      "claim_text": "Revenue increased.",
      "claim_type": "financial_performance",
      "supported_by_quotes": ["quote:1"],
      "confidence": 0.95
    }]
    """

    items = parse_structured_output(raw, {}, "extract_research_claims")

    assert items[0]["confidence"] == "high"


def test_trailing_commas_are_repaired():
    raw = """
    [
      {
        "id": "claim:1",
        "claim_text": "Revenue increased.",
        "claim_type": "financial_performance",
        "supported_by_quotes": ["quote:1"],
        "confidence": "high",
      },
    ]
    """

    items = parse_structured_output(raw, {}, "extract_research_claims")

    assert len(items) == 1
    assert items[0]["id"] == "claim:1"


def test_prose_wrapped_json_is_extracted():
    raw = """
    Here is the JSON:
    {
      "items": [
        {
          "id": "claim:1",
          "claim_text": "Revenue increased.",
          "claim_type": "financial_performance",
          "supported_by_quotes": ["quote:1"],
          "confidence": "high"
        }
      ]
    }
    Done.
    """

    items = parse_structured_output(raw, {}, "extract_research_claims")

    assert len(items) == 1
    assert items[0]["id"] == "claim:1"


def test_missing_comma_between_json_fields_is_repaired():
    raw = """
    {
      "items": [
        {
          "id": "claim:1"
          "claim_text": "Revenue increased.",
          "claim_type": "financial_performance",
          "supported_by_quotes": ["quote:1"],
          "confidence": "high"
        }
      ]
    }
    """

    items = parse_structured_output(raw, {}, "extract_research_claims")

    assert len(items) == 1
    assert items[0]["id"] == "claim:1"


def test_extract_uses_claude_cli_structured_output(monkeypatch, tmp_path):
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, stdin):
            captured["stdin"] = stdin.decode()
            return (
                json.dumps({
                    "structured_output": {
                        "items": [
                            {
                                "id": "claim:1",
                                "claim_text": "Revenue increased.",
                                "claim_type": "financial_performance",
                                "supported_by_quotes": ["quote:1"],
                                "confidence": 0.95,
                            }
                        ]
                    }
                }).encode(),
                b"",
            )

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        return FakeProcess()

    monkeypatch.setattr(
        worker_module.SubprocessCLITransport,
        "_find_cli",
        lambda self: "/bin/claude",
    )
    monkeypatch.setattr(
        worker_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    item_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "claim_text": {"type": "string"},
            "claim_type": {"type": "string"},
            "supported_by_quotes": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string"},
        },
        "required": [
            "id",
            "claim_text",
            "claim_type",
            "supported_by_quotes",
            "confidence",
        ],
    }
    extraction_worker = ExtractionWorker(model="claude-test", cwd=tmp_path)

    items = asyncio.run(
        extraction_worker.extract(
            "Analyze {company}.",
            {"company": "Apple"},
            item_schema,
            "extract_research_claims",
        )
    )

    assert captured["stdin"] == "Analyze Apple."
    assert captured["cwd"] == str(tmp_path)
    assert captured["cmd"][:4] == ("/bin/claude", "-p", "--output-format", "json")
    assert "--json-schema" in captured["cmd"]
    assert "--strict-mcp-config" in captured["cmd"]
    assert items == [
        {
            "id": "claim:1",
            "claim_text": "Revenue increased.",
            "claim_type": "financial_performance",
            "supported_by_quotes": ["quote:1"],
            "related_metrics": None,
            "object_type_hints": None,
            "theme_hint": None,
            "factor_hint": None,
            "activity_hint": None,
            "benchmark_hint": None,
            "impact_channels": None,
            "effect_direction": None,
            "materiality_hint": None,
            "time_horizon": None,
            "sector_hint": None,
            "confidence": "high",
        }
    ]


def test_extract_parses_claude_cli_result_markdown_json(monkeypatch, tmp_path):
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, stdin):
            captured["stdin"] = stdin.decode()
            return (
                json.dumps(
                    {
                        "type": "result",
                        "result": """```json
{
  "items": [
    {
      "id": "claim:1",
      "claim_text": "Revenue increased.",
      "claim_type": "financial_performance",
      "supported_by_quotes": ["quote:1"],
      "confidence": "high"
    }
  ]
}
```""",
                    }
                ).encode(),
                b"",
            )

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(
        worker_module.SubprocessCLITransport,
        "_find_cli",
        lambda self: "/bin/claude",
    )
    monkeypatch.setattr(
        worker_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    extraction_worker = ExtractionWorker(model="claude-test", cwd=tmp_path)

    items = asyncio.run(
        extraction_worker.extract(
            "Analyze.",
            {},
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "claim_text": {"type": "string"},
                    "claim_type": {"type": "string"},
                    "supported_by_quotes": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string"},
                },
                "required": ["id", "claim_text", "claim_type", "supported_by_quotes", "confidence"],
            },
            "extract_research_claims",
        )
    )

    assert items[0]["id"] == "claim:1"
    assert items[0]["confidence"] == "high"


def test_extract_retries_rate_limit_same_request(monkeypatch, tmp_path):
    calls = {"count": 0}
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    async def fake_call_once(prompt_text, sdk_schema, stage_name, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RateLimitError("HTTP 429 too many requests")
        return {
            "items": [
                {
                    "id": "claim:1",
                    "claim_text": "Revenue increased.",
                    "claim_type": "factual",
                    "supported_by_quotes": ["quote:1"],
                    "confidence": "high",
                }
            ]
        }

    monkeypatch.setattr(worker_module.asyncio, "sleep", fake_sleep)
    extraction_worker = ExtractionWorker(model="claude-test", cwd=tmp_path)
    monkeypatch.setattr(extraction_worker, "_call_once", fake_call_once)

    items = asyncio.run(
        extraction_worker.extract(
            "Analyze.",
            {},
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "claim_text": {"type": "string"},
                    "claim_type": {"type": "string"},
                    "supported_by_quotes": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string"},
                },
                "required": ["id", "claim_text", "claim_type", "supported_by_quotes", "confidence"],
            },
            "extract_research_claims",
        )
    )

    assert calls["count"] == 2
    assert len(sleeps) == 1
    assert items[0]["id"] == "claim:1"


def test_call_once_classifies_cli_429_as_rate_limit(monkeypatch, tmp_path):
    class RateLimitedProcess:
        returncode = 1

        async def communicate(self, stdin):
            return b"", b"HTTP 429 too many requests"

        def kill(self):
            pass

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        return RateLimitedProcess()

    monkeypatch.setattr(
        worker_module.SubprocessCLITransport,
        "_find_cli",
        lambda self: "/bin/claude",
    )
    monkeypatch.setattr(
        worker_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    extraction_worker = ExtractionWorker(model="claude-test", cwd=tmp_path)
    with pytest.raises(RateLimitError):
        asyncio.run(
            extraction_worker._call_once(
                "Analyze.",
                {"type": "object", "properties": {}, "additionalProperties": False},
                "extract_research_claims",
            )
        )


def test_extract_kills_claude_cli_on_timeout(monkeypatch, tmp_path):
    captured = {}

    class SlowProcess:
        returncode = None

        async def communicate(self, stdin):
            await asyncio.sleep(10)
            return b"", b""

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            self.returncode = -9
            return self.returncode

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        return SlowProcess()

    monkeypatch.setattr(
        worker_module.SubprocessCLITransport,
        "_find_cli",
        lambda self: "/bin/claude",
    )
    monkeypatch.setattr(
        worker_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    extraction_worker = ExtractionWorker(model="claude-test", cwd=tmp_path, call_timeout_s=0.01)
    try:
        asyncio.run(
            extraction_worker.extract(
                "Analyze {company}.",
                {"company": "Apple"},
                {"type": "object", "properties": {}, "additionalProperties": False},
                "extract_research_claims",
            )
        )
    except Exception as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("Expected timeout")

    assert captured["killed"] is True
