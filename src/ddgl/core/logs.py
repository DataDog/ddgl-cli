from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable

from ddgl.cache.cache import Cache
from ddgl.cache.cache_config import CacheNS
from ddgl.client import GitLabClient
from ddgl.constants import CACHE_TTL_FINISHED_JOB

logger = logging.getLogger("ddgl.core.logs")

# GitLab runner appends one of these lines as the very last output when the
# job reaches a terminal state.  We scan the tail of the log so we never
# cache a partial (still-running) log.
_TERMINAL_MARKERS = (
    "Job succeeded",
    "Job failed",
    "ERROR: Job failed",
)
_TAIL_LINES = 10


def _log_is_complete(text: str) -> bool:
    """Return True if the log tail contains a GitLab job-completion marker."""
    tail = text.splitlines()[-_TAIL_LINES:]
    return any(marker in line for line in tail for marker in _TERMINAL_MARKERS)


async def get_logs(
    client: GitLabClient,
    job_ids: Iterable[int],
    *,
    cache: Cache | None = None,
) -> dict[int, str]:
    """Bulk-fetch raw log text for a collection of job IDs.

    Cache read: one file-read per job ID (TextFileBackend).
    Cache write: only when the log tail contains a GitLab completion marker,
    meaning the job has finished and the log is complete.
    Fetches all misses concurrently.

    Returns {job_id: log_text}.
    """
    ids = list(job_ids)
    if not ids:
        return {}

    result: dict[int, str] = {}
    misses: list[int]

    if cache is not None:
        misses = []
        for jid in ids:
            cached = cache[CacheNS.LOGS][str(jid)]
            if cached is not None:
                logger.debug("Log cache hit: job %d", jid)
                result[jid] = str(cached)
            else:
                misses.append(jid)
    else:
        misses = ids

    if misses:
        log_texts: list[str] = list(
            await asyncio.gather(*[client.get_job_log(jid) for jid in misses])
        )
        for jid, text in zip(misses, log_texts):
            result[jid] = text
            if cache is not None and _log_is_complete(text):
                cache[CacheNS.LOGS].set(str(jid), text, ttl=CACHE_TTL_FINISHED_JOB)

    return result


async def get_log(
    client: GitLabClient,
    job_id: int,
    *,
    cache: Cache | None = None,
) -> str:
    """Fetch a single job log by ID."""
    return (await get_logs(client, [job_id], cache=cache))[job_id]


async def stream_log(
    client: GitLabClient,
    job_id: int,
    *,
    cache: Cache | None = None,
) -> AsyncIterator[str]:
    """Stream a job log line by line.

    Checks the log cache first; if hit, yields from the cached text.
    Otherwise streams from the API.  If the log tail contains a completion
    marker, the full log is cached before returning.
    """
    if cache is not None:
        cached = cache[CacheNS.LOGS][str(job_id)]
        if cached is not None:
            logger.debug("Log cache hit (stream): job %d", job_id)
            for line in str(cached).splitlines():
                yield line
            return

    lines: list[str] = []
    async for line in client.stream_job_log(job_id):
        yield line
        if cache is not None:
            lines.append(line)

    if cache is not None:
        text = "\n".join(lines)
        if _log_is_complete(text):
            cache[CacheNS.LOGS].set(str(job_id), text, ttl=CACHE_TTL_FINISHED_JOB)
