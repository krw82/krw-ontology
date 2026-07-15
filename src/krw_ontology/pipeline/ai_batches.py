"""Helpers for resumable, limited-concurrency AI extraction batches."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable, Deque, TypeVar

from krw_ontology.errors import ProviderOverloadError
from krw_ontology.utils.io import atomic_write_json, read_jsonl

T = TypeVar("T")

_PROVIDER_OVERLOAD_PAUSE_SECONDS = 10 * 60
logger = logging.getLogger("krw_ontology")

TRANSIENT_PROVIDER_STATUSES = {429, 500, 502, 503, 504, 529}
TRANSIENT_PROVIDER_ERROR_TYPES = {"RateLimitError", "TransientServiceError"}
_TRANSIENT_PROVIDER_TOKENS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "overloaded",
    "temporarily overloaded",
    "server-side issue",
    "try again later",
    "network error",
)


def batch_cache_dir(ontology_dir: Path, stage_name: str) -> Path:
    return ontology_dir / ".ai_batches" / stage_name


def batch_cache_path(ontology_dir: Path, stage_name: str, batch_index: int) -> Path:
    return batch_cache_dir(ontology_dir, stage_name) / f"batch_{batch_index:04d}.json"


def read_batch_cache(ontology_dir: Path, stage_name: str, batch_index: int) -> list[dict] | None:
    path = batch_cache_path(ontology_dir, stage_name, batch_index)
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        if not is_reusable_batch_cache_metadata(data.get("metadata") or {}):
            return None
        return data["items"]
    if isinstance(data, list):
        return data
    return None


def write_batch_cache(
    ontology_dir: Path,
    stage_name: str,
    batch_index: int,
    items: list[dict],
    *,
    metadata: dict | None = None,
) -> None:
    payload = {
        "stage": stage_name,
        "batch_index": batch_index,
        "items": items,
        "metadata": metadata or {},
    }
    atomic_write_json(batch_cache_path(ontology_dir, stage_name, batch_index), payload)


def clear_stage_batch_cache(ontology_dir: Path, stage_name: str) -> None:
    cache_dir = batch_cache_dir(ontology_dir, stage_name)
    if not cache_dir.exists():
        return
    for path in cache_dir.glob("batch_*.json"):
        path.unlink()


def transient_provider_failure_metadata(
    error_message: str,
    *,
    error_type: str = "ExtractionError",
) -> dict:
    """Classify provider-side transient failures for quality and repair planning."""
    status = extract_provider_error_status(error_message)
    lower = str(error_message or "").lower()
    transient = (
        error_type in TRANSIENT_PROVIDER_ERROR_TYPES
        or status in TRANSIENT_PROVIDER_STATUSES
        or any(token in lower for token in _TRANSIENT_PROVIDER_TOKENS)
    )
    if not transient and status is None:
        return {}

    metadata: dict[str, object] = {}
    if status is not None:
        metadata["provider_error_status"] = status
    if transient:
        metadata.update({
            "provider_transient": True,
            "provider_error_kind": _provider_error_kind(status, lower),
            "quality_repair_hint": "retry_transient_batch",
        })
    return metadata


def extract_provider_error_status(error_message: str) -> int | None:
    text = str(error_message or "")
    patterns = (
        r"api_error_status\\?\"?\s*[:=]\s*(\d{3})",
        r"api error:\s*(\d{3})",
        r"\bhttp\s+(\d{3})\b",
        r"\b(429|500|502|503|504|529)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def is_reusable_batch_cache_metadata(metadata: dict) -> bool:
    status = metadata.get("status")
    if status in {"failed", "partial_failed", "transient_failed"}:
        return False
    if metadata.get("provider_transient"):
        return False
    for key in ("failed_spans", "failed_quotes", "failed_batches"):
        try:
            if int(metadata.get(key) or 0) > 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _provider_error_kind(status: int | None, lower_message: str) -> str:
    if status == 529 or "overloaded" in lower_message:
        return "overload"
    if status == 429 or "rate limit" in lower_message or "too many requests" in lower_message:
        return "rate_limit"
    if status in {500, 502, 503, 504} or "server-side issue" in lower_message:
        return "server_error"
    if "network error" in lower_message:
        return "network"
    return "transient"


async def run_limited_batches(
    *,
    batch_indices: list[int],
    concurrency: int,
    run_one: Callable[[int], Awaitable[T]],
    on_complete: Callable[[int, T], None] | None = None,
    overload_pause_seconds: float = _PROVIDER_OVERLOAD_PAUSE_SECONDS,
    stage_name: str | None = None,
) -> list[tuple[int, T]]:
    """Run bounded concurrent batches, pausing and retrying overloads in place.

    A 529/1305 is not recorded as a failed extraction batch. The scheduler stops
    launching new work, waits for already-started work to settle, pauses for ten
    minutes, and retries the first overloaded batch by itself. Once that probe
    succeeds, normal concurrency resumes for the remaining work.
    """
    if overload_pause_seconds < 0:
        raise ValueError("overload_pause_seconds must be non-negative")

    pending: Deque[int] = deque(batch_indices)
    running: dict[asyncio.Task[T], int] = {}
    results: list[tuple[int, T]] = []
    retry_probe = False

    def record_success(batch_index: int, result: T) -> None:
        if on_complete:
            on_complete(batch_index, result)
        results.append((batch_index, result))

    async def settle_running_after_overload() -> list[int]:
        """Finish already-started work without launching any new batch."""
        overloaded: list[int] = []
        active = list(running.items())
        running.clear()
        settled = await asyncio.gather(
            *(task for task, _batch_index in active),
            return_exceptions=True,
        )
        for (_task, batch_index), outcome in zip(active, settled):
            if isinstance(outcome, ProviderOverloadError):
                overloaded.append(batch_index)
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                record_success(batch_index, outcome)
        return overloaded

    try:
        while pending or running:
            launch_limit = 1 if retry_probe else max(1, concurrency)
            while pending and len(running) < launch_limit:
                batch_index = pending.popleft()
                running[asyncio.create_task(run_one(batch_index))] = batch_index

            done, _pending_tasks = await asyncio.wait(
                running,
                return_when=asyncio.FIRST_COMPLETED,
            )
            overloaded = []
            for task in done:
                batch_index = running.pop(task)
                try:
                    outcome = task.result()
                except ProviderOverloadError:
                    overloaded.append(batch_index)
                except BaseException:
                    raise
                else:
                    record_success(batch_index, outcome)

            if overloaded:
                overloaded.extend(await settle_running_after_overload())
                retry_indices = sorted(set(overloaded))
                pending.extendleft(reversed(retry_indices))
                retry_probe = True
                first_batch = retry_indices[0] + 1
                logger.warning(
                    "provider overload; pausing new batches for %ss before retrying batch %s first",
                    int(overload_pause_seconds),
                    first_batch,
                    extra={"stage": stage_name, "rate_limited": True} if stage_name else None,
                )
                await asyncio.sleep(overload_pause_seconds)
                continue

            if retry_probe and not running:
                retry_probe = False
    except BaseException:
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        raise

    return results


def append_jsonl_rows(path: Path, rows: list[dict]) -> None:
    existing = read_jsonl(path)
    existing.extend(rows)
    from krw_ontology.utils.io import write_jsonl

    write_jsonl(path, existing)
