# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from typing import TypeVar

from ddgl.constants import MAX_CONCURRENT_REQUESTS

T = TypeVar("T")


async def gather_bounded(
    awaitables: Iterable[Awaitable[T]],
    *,
    limit: int = MAX_CONCURRENT_REQUESTS,
) -> list[T]:
    """`asyncio.gather`, but with at most *limit* awaitables in flight.

    Every bulk helper in core/ fans out over an unbounded ID list — the
    jobs of a pipeline, the logs of those jobs, the jobs being retried —
    so a plain gather issues one request per item simultaneously. On a
    large pipeline that is hundreds of concurrent requests, which GitLab
    answers with 429s (the client retries those, so it self-inflicts
    latency rather than failing outright).

    Mirrors the bound `client._get_all` already applies to page fetches.
    Results keep input order, as with `asyncio.gather`.
    """
    semaphore = asyncio.Semaphore(limit)

    async def _run(awaitable: Awaitable[T]) -> T:
        async with semaphore:
            return await awaitable

    return list(await asyncio.gather(*[_run(a) for a in awaitables]))
