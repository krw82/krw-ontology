"""Helpers for resumable, limited-concurrency AI extraction batches."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from krw_ontology.utils.io import atomic_write_json, read_jsonl

T = TypeVar("T")

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
) -> list[tuple[int, T]]:
    """Run indexed batches with a semaphore, preserving index in results."""
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def guarded(batch_index: int) -> tuple[int, T]:
        async with semaphore:
            result = await run_one(batch_index)
            if on_complete:
                on_complete(batch_index, result)
            return batch_index, result

    tasks = [asyncio.create_task(guarded(batch_index)) for batch_index in batch_indices]
    return await asyncio.gather(*tasks)


def append_jsonl_rows(path: Path, rows: list[dict]) -> None:
    existing = read_jsonl(path)
    existing.extend(rows)
    from krw_ontology.utils.io import write_jsonl

    write_jsonl(path, existing)
