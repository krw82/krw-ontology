"""Tests for bounded AI batch scheduling."""

from __future__ import annotations

import asyncio

import krw_ontology.pipeline.ai_batches as ai_batches
from krw_ontology.errors import ProviderOverloadError
from krw_ontology.pipeline.ai_batches import run_limited_batches


def test_overload_retries_same_batch_before_launching_pending_batches(monkeypatch):
    started: list[int] = []
    completed: list[int] = []
    attempts: dict[int, int] = {}
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(ai_batches.asyncio, "sleep", fake_sleep)

    async def run_one(batch_index: int) -> str:
        started.append(batch_index)
        attempts[batch_index] = attempts.get(batch_index, 0) + 1
        if batch_index == 1 and attempts[batch_index] == 1:
            raise ProviderOverloadError("API Error: 529 [1305] temporarily overloaded")
        return f"result-{batch_index}"

    results = asyncio.run(
        run_limited_batches(
            batch_indices=[0, 1, 2, 3],
            concurrency=2,
            run_one=run_one,
            on_complete=lambda batch_index, _result: completed.append(batch_index),
            overload_pause_seconds=600,
            stage_name="extract_research_claims",
        )
    )

    assert started == [0, 1, 1, 2, 3]
    assert sorted(completed) == [0, 1, 2, 3]
    assert pauses == [600]
    assert sorted(results) == [
        (0, "result-0"),
        (1, "result-1"),
        (2, "result-2"),
        (3, "result-3"),
    ]
