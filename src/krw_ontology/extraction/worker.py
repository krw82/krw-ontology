"""ExtractionWorker: Claude Code structured-output calls with retry (Section 10)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import random
import re
import time
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
        self.call_log_context: dict[str, Any] = {}

    async def extract(
        self,
        prompt_template: str,
        input_data: dict,
        output_schema: dict,
        stage_name: str,
        call_metadata: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Run extraction with retry and validation.

        Returns list of extracted objects.
        Raises ExtractionError after max retries exhausted.
        """
        user_prompt = prompt_template.format(**input_data)
        metadata = {**self.call_log_context, **(call_metadata or {})}
        data = await self._call_with_retry(user_prompt, output_schema, stage_name, metadata)
        if isinstance(data, str):
            return parse_structured_output(data, output_schema, stage_name)
        return parse_structured_data(data, stage_name)

    async def _call_with_retry(
        self,
        prompt_text: str,
        output_schema: dict,
        stage_name: str,
        call_metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Agent SDK call with exponential backoff retry."""
        sdk_schema = _wrap_items_schema(output_schema)
        delay = 5
        for attempt in range(self.max_retries):
            try:
                return await self._call_once(
                    prompt_text,
                    sdk_schema,
                    stage_name,
                    call_metadata=call_metadata,
                    attempt=attempt + 1,
                )
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

    async def _call_once(
        self,
        prompt_text: str,
        sdk_schema: dict,
        stage_name: str,
        *,
        call_metadata: dict[str, Any] | None = None,
        attempt: int = 1,
    ) -> Any:
        """Run one Claude Code structured-output request with a hard subprocess timeout."""
        started_monotonic = time.monotonic()
        metadata = dict(call_metadata or {})
        log_path = self._agent_call_log_path(stage_name, metadata)
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
        proc_pid = getattr(proc, "pid", None)
        self._write_agent_call_log(
            log_path,
            {
                "event": "start",
                "stage": stage_name,
                "model": self.model,
                "attempt": attempt,
                "max_retries": self.max_retries,
                "pid": proc_pid,
                "timeout_seconds": self.call_timeout_s,
                "max_turns": self.max_turns,
                **metadata,
            },
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt_text.encode()),
                timeout=self.call_timeout_s,
            )
        except TimeoutError as e:
            proc.kill()
            await proc.wait()
            self._write_agent_call_log(
                log_path,
                self._agent_call_end_payload(
                    stage_name,
                    metadata,
                    started_monotonic=started_monotonic,
                    attempt=attempt,
                    pid=proc_pid,
                    status="timeout",
                    error_preview=f"Claude call timed out after {self.call_timeout_s}s",
                    returncode=proc.returncode,
                ),
            )
            raise ExtractionError(
                f"{stage_name}: Claude call timed out after {self.call_timeout_s}s"
            ) from e

        stdout_text = stdout.decode(errors="replace").strip()
        stderr_text = stderr.decode(errors="replace").strip()
        if proc.returncode != 0:
            message = stderr_text or stdout_text[:500]
            status = "rate_limited" if _is_rate_limit_message(message) else "error"
            self._write_agent_call_log(
                log_path,
                self._agent_call_end_payload(
                    stage_name,
                    metadata,
                    started_monotonic=started_monotonic,
                    attempt=attempt,
                    pid=proc_pid,
                    status=status,
                    error_preview=message,
                    returncode=proc.returncode,
                ),
            )
            if _is_rate_limit_message(message):
                raise RateLimitError(
                    f"{stage_name}: Claude CLI rate limited: {message}"
                )
            raise ExtractionError(
                f"{stage_name}: Claude CLI exited {proc.returncode}: "
                f"{message}"
            )
        if not stdout_text:
            self._write_agent_call_log(
                log_path,
                self._agent_call_end_payload(
                    stage_name,
                    metadata,
                    started_monotonic=started_monotonic,
                    attempt=attempt,
                    pid=proc_pid,
                    status="error",
                    error_preview="empty response from Claude CLI",
                    returncode=proc.returncode,
                ),
            )
            raise ExtractionError(f"{stage_name}: empty response from Claude CLI")

        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError as e:
            self._write_agent_call_log(
                log_path,
                self._agent_call_end_payload(
                    stage_name,
                    metadata,
                    started_monotonic=started_monotonic,
                    attempt=attempt,
                    pid=proc_pid,
                    status="error",
                    error_preview=f"failed to parse Claude CLI JSON: {e}; {stdout_text[:500]}",
                    returncode=proc.returncode,
                ),
            )
            raise ExtractionError(
                f"{stage_name}: failed to parse Claude CLI JSON: {e}\n"
                f"Raw output preview: {stdout_text[:500]}"
            ) from e

        if data.get("is_error"):
            message = str(data.get("subtype") or data)
            status = "rate_limited" if _is_rate_limit_message(message) else "error"
            self._write_agent_call_log(
                log_path,
                self._agent_call_end_payload(
                    stage_name,
                    metadata,
                    started_monotonic=started_monotonic,
                    attempt=attempt,
                    pid=proc_pid,
                    status=status,
                    error_preview=message,
                    returncode=proc.returncode,
                ),
            )
            if _is_rate_limit_message(message):
                raise RateLimitError(f"{stage_name}: Claude CLI rate limited: {message}")
            raise ExtractionError(f"{stage_name}: Claude CLI result error: {data.get('subtype')}")
        self._write_agent_call_log(
            log_path,
            self._agent_call_end_payload(
                stage_name,
                metadata,
                started_monotonic=started_monotonic,
                attempt=attempt,
                pid=proc_pid,
                status="success",
                returncode=proc.returncode,
                output_preview=_safe_preview(stdout_text),
            ),
        )
        if "structured_output" in data:
            return data["structured_output"]
        if "result" in data:
            return data["result"]
        return data

    def _agent_call_log_path(self, stage_name: str, metadata: dict[str, Any]) -> Path | None:
        job_id = str(metadata.get("job_id") or "").strip()
        if not job_id:
            return None
        batch_index = metadata.get("batch_index")
        suffix = "call" if batch_index is None else f"batch-{batch_index}"
        return (
            self.cwd
            / ".krw_pipeline"
            / "quality"
            / "logs"
            / "agent-calls"
            / _safe_filename(job_id)
            / f"{_safe_filename(stage_name)}-{_safe_filename(suffix)}.log"
        )

    def _write_agent_call_log(self, path: Path | None, payload: dict[str, Any]) -> None:
        if path is None:
            return
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            logger.debug("failed to write agent call log: %s", path, exc_info=True)

    def _agent_call_end_payload(
        self,
        stage_name: str,
        metadata: dict[str, Any],
        *,
        started_monotonic: float,
        attempt: int,
        pid: int | None,
        status: str,
        returncode: int | None = None,
        error_preview: str | None = None,
        output_preview: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": "end",
            "stage": stage_name,
            "model": self.model,
            "attempt": attempt,
            "pid": pid,
            "status": status,
            "duration_ms": int((time.monotonic() - started_monotonic) * 1000),
            "timeout_seconds": self.call_timeout_s,
            "returncode": returncode,
            **metadata,
        }
        if error_preview:
            payload["error_preview"] = _safe_preview(error_preview)
        if output_preview:
            payload["output_preview"] = output_preview
        return payload


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


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or ""))[:160] or "unknown"


def _safe_preview(value: str, limit: int = 1000) -> str:
    text = str(value or "").replace("\x00", "")
    return text[:limit]


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
    text = raw.strip()
    start_positions = [
        position for position in (text.find("{"), text.find("[")) if position >= 0
    ]
    if not start_positions:
        return text
    start = min(start_positions)
    end = max(text.rfind("}"), text.rfind("]"))
    if end > start:
        return text[start : end + 1].strip()
    return text


def _repair_common_json_issues(json_str: str) -> str:
    """Repair small JSON mistakes common in model output."""
    # Trailing commas before object/array close are invalid JSON but common in
    # long model-generated arrays.
    repaired = re.sub(r",\s*([}\]])", r"\1", json_str)
    # Long object outputs sometimes drop the comma between a completed value and
    # the next object key. Only apply this after normal parsing failed.
    return re.sub(
        r'(?<=[}\]"])\s*\n\s*("[-A-Za-z0-9_]+"(?:\s*):)',
        r",\n\1",
        repaired,
    )
