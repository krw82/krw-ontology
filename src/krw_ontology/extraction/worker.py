"""ExtractionWorker: Claude Code structured-output calls with retry (Section 10)."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from krw_ontology.errors import ExtractionError, RateLimitError
from krw_ontology.extraction.schemas import STAGE_OUTPUT_MODELS

logger = logging.getLogger("krw_ontology")

_MAX_DELAY = 120
_SYSTEM_PROMPT = (
    "Return the JSON object that matches the configured structured output schema. "
    'The root object must contain an "items" array. Do not wrap the response in '
    '{"output": ...}.'
)


class ExtractionWorker:
    """Runs Claude Code structured-output calls with retry and hard timeouts."""

    def __init__(
        self,
        model: str,
        cwd: Path,
        max_retries: int = 3,
        call_timeout_s: int = 180,
        max_turns: int = 6,
    ):
        self.model = model
        self.cwd = cwd
        self.max_retries = max_retries
        self.call_timeout_s = call_timeout_s
        self.max_turns = max_turns

    async def extract(
        self,
        prompt_template: str,
        input_data: dict,
        output_schema: dict,
        stage_name: str,
    ) -> list[dict]:
        """Run extraction with retry and validation.

        Returns list of extracted objects.
        Raises ExtractionError after max retries exhausted.
        """
        user_prompt = prompt_template.format(**input_data)
        data = await self._call_with_retry(user_prompt, output_schema, stage_name)
        return parse_structured_data(data, stage_name)

    async def _call_with_retry(
        self, prompt_text: str, output_schema: dict, stage_name: str
    ) -> Any:
        """Agent SDK call with exponential backoff retry."""
        sdk_schema = _wrap_items_schema(output_schema)
        delay = 5
        for attempt in range(self.max_retries):
            try:
                return await self._call_once(prompt_text, sdk_schema, stage_name)
            except RateLimitError as e:
                if attempt < self.max_retries - 1:
                    sleep_for = min(delay, _MAX_DELAY) + random.uniform(0, min(delay, _MAX_DELAY))
                    logger.warning(
                        f"{stage_name} attempt {attempt + 1}/{self.max_retries} rate limited: {e}. "
                        f"Retrying same request in {sleep_for:.1f}s",
                        extra={"stage": stage_name, "rate_limited": True},
                    )
                    await asyncio.sleep(sleep_for)
                    delay = min(delay * 2, _MAX_DELAY)
                else:
                    raise
            except ExtractionError:
                raise
            except Exception as e:
                delay = min(delay * 2, _MAX_DELAY)
                jitter = random.uniform(0, min(3, delay * 0.2))
                logger.warning(
                    f"{stage_name} attempt {attempt + 1}/{self.max_retries} failed: {e}. "
                    f"Retrying in {delay + jitter:.1f}s",
                    extra={"stage": stage_name},
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(delay + jitter)
                else:
                    raise ExtractionError(
                        f"{stage_name} failed after {self.max_retries} attempts: {e}"
                    ) from e
        raise ExtractionError(f"{stage_name} failed after {self.max_retries} attempts")

    async def _call_once(self, prompt_text: str, sdk_schema: dict, stage_name: str) -> Any:
        """Run one Claude Code structured-output request with a hard subprocess timeout."""
        cli_path = await asyncio.to_thread(
            lambda: SubprocessCLITransport(
                prompt=prompt_text,
                options=ClaudeAgentOptions(model=self.model, cwd=str(self.cwd)),
            )._find_cli()
        )
        cmd = [
            cli_path,
            "-p",
            "--output-format",
            "json",
            "--system-prompt",
            _SYSTEM_PROMPT,
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps({"mcpServers": {}}),
            "--max-turns",
            str(self.max_turns),
            "--model",
            self.model,
            "--json-schema",
            json.dumps(sdk_schema),
            "--input-format",
            "text",
            "--no-session-persistence",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt_text.encode()),
                timeout=self.call_timeout_s,
            )
        except TimeoutError as e:
            proc.kill()
            await proc.wait()
            raise ExtractionError(
                f"{stage_name}: Claude call timed out after {self.call_timeout_s}s"
            ) from e

        stdout_text = stdout.decode(errors="replace").strip()
        stderr_text = stderr.decode(errors="replace").strip()
        if proc.returncode != 0:
            message = stderr_text or stdout_text[:500]
            if _is_rate_limit_message(message):
                raise RateLimitError(
                    f"{stage_name}: Claude CLI rate limited: {message}"
                )
            raise ExtractionError(
                f"{stage_name}: Claude CLI exited {proc.returncode}: "
                f"{message}"
            )
        if not stdout_text:
            raise ExtractionError(f"{stage_name}: empty response from Claude CLI")

        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError as e:
            raise ExtractionError(
                f"{stage_name}: failed to parse Claude CLI JSON: {e}\n"
                f"Raw output preview: {stdout_text[:500]}"
            ) from e

        if data.get("is_error"):
            message = str(data.get("subtype") or data)
            if _is_rate_limit_message(message):
                raise RateLimitError(f"{stage_name}: Claude CLI rate limited: {message}")
            raise ExtractionError(f"{stage_name}: Claude CLI result error: {data.get('subtype')}")
        if "structured_output" in data:
            return data["structured_output"]
        if "result" in data:
            return data["result"]
        return data


def _wrap_items_schema(item_schema: dict) -> dict:
    """Build the root schema expected by Claude structured output."""
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": item_schema,
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def _is_rate_limit_message(message: str) -> bool:
    text = str(message or "").lower()
    return any(
        token in text
        for token in (
            "429",
            "rate limit",
            "rate_limit",
            "too many requests",
            "overloaded",
        )
    )


def parse_structured_output(
    raw: str, output_schema: dict, stage_name: str
) -> list[dict]:
    """Parse AI JSON output into validated objects.

    Handles JSON extraction from markdown code blocks.
    Raises ExtractionError on unparseable output.
    """
    json_str = _extract_json(raw)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as first_error:
        repaired = _repair_common_json_issues(json_str)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as e:
            raise ExtractionError(
                f"{stage_name}: failed to parse JSON output: {e}\nRaw output preview: {raw[:500]}"
            ) from first_error

    return parse_structured_data(data, stage_name)


def parse_structured_data(data: Any, stage_name: str) -> list[dict]:
    """Validate structured SDK output or parsed fallback JSON into objects."""
    if isinstance(data, dict):
        # Check if it wraps an array
        for key in ("items", "results", "data", "quotes", "claims", "objects", "edges"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            data = [data]

    if not isinstance(data, list):
        raise ExtractionError(f"{stage_name}: expected array, got {type(data).__name__}")

    model_cls = STAGE_OUTPUT_MODELS.get(stage_name)
    validated: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if model_cls:
            try:
                item = model_cls.model_validate(item).model_dump(by_alias=False)
            except Exception as e:
                logger.warning(
                    f"{stage_name}: skipping invalid item: {e}",
                    extra={"stage": stage_name},
                )
                continue
        validated.append(item)

    return validated


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _extract_json(raw: str) -> str:
    """Extract JSON from potential markdown code block wrapping."""
    match = _JSON_BLOCK_RE.search(raw)
    if match:
        return match.group(1).strip()
    return raw.strip()


def _repair_common_json_issues(json_str: str) -> str:
    """Repair small JSON mistakes common in model output."""
    # Trailing commas before object/array close are invalid JSON but common in
    # long model-generated arrays.
    return re.sub(r",\s*([}\]])", r"\1", json_str)
