# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/core/_concurrency.py."""
from __future__ import annotations

import asyncio

from ddgl.core._concurrency import gather_bounded


class _Tracker:
    """Records how many awaitables were ever in flight simultaneously."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    async def task(self, value: int) -> int:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        # Yield control so every already-started task can pile up before any
        # of them finishes — without this the peak would depend on scheduling.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.in_flight -= 1
        return value


class TestGatherBounded:
    async def test_never_exceeds_the_limit(self) -> None:
        tracker = _Tracker()
        await gather_bounded((tracker.task(i) for i in range(50)), limit=5)
        assert tracker.peak == 5

    async def test_returns_results_in_input_order(self) -> None:
        async def identity(value: int) -> int:
            await asyncio.sleep(0)
            return value

        results = await gather_bounded((identity(i) for i in range(20)), limit=3)
        assert results == list(range(20))

    async def test_empty_input(self) -> None:
        assert await gather_bounded([], limit=5) == []

    async def test_propagates_an_exception(self) -> None:
        async def boom() -> int:
            raise ValueError("nope")

        async def fine() -> int:
            return 1

        try:
            await gather_bounded([fine(), boom()], limit=2)
        except ValueError as exc:
            assert str(exc) == "nope"
        else:  # pragma: no cover
            raise AssertionError("expected ValueError to propagate")
