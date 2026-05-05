"""Helpers for resumable, limited-concurrency AI extraction batches."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from krw_ontology.utils.io import atomic_write_json, read_jsonl

T = TypeVar("T")


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
