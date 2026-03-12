from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable
from typing import overload

from ddgl.cache.cache import Cache
from ddgl.cache.cache_config import CacheNS
from ddgl.client import GitLabClient
from ddgl.constants import CACHE_TTL_FINISHED_JOB, JobStatus
from ddgl.model.job import Job

logger = logging.getLogger("ddgl.core.jobs")

_JOB_TERMINAL = frozenset(
    {JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.SKIPPED}
)


async def get_jobs(
    client: GitLabClient,
    job_ids: Iterable[int],
    *,
    cache: Cache | None = None,
) -> list[Job]:
    """Bulk-fetch jobs by ID with cache optimisation.

    1. Reads all cached jobs for the project in one query.
    2. Fetches any cache misses concurrently via the client.
    3. Returns results in input order.
    """
    ids = list(job_ids)
    if not ids:
        return []

    project_id = client._config.project_id or ""
    if cache is not None:
        cached_objects = cache[CacheNS.OBJECTS]["jobs"][project_id].get_many(ids, cls=Job)
        cached_map: dict[int, Job] = {
            j.id: j for j in cached_objects if isinstance(j, Job)
        }
    else:
        cached_map = {}

    misses = [jid for jid in ids if jid not in cached_map]
    if misses:
        fresh: list[Job] = list(
            await asyncio.gather(*[client.get_job(jid) for jid in misses])
        )
        for j in fresh:
            if cache is not None and j.status in _JOB_TERMINAL:
                cache[CacheNS.OBJECTS].set(
                    ("jobs", project_id, j.id),
                    j,
                    ttl=CACHE_TTL_FINISHED_JOB,
                )
            cached_map[j.id] = j

    return [cached_map[jid] for jid in ids if jid in cached_map]


async def get_job(
    client: GitLabClient,
    job_id: int,
    *,
    cache: Cache | None = None,
) -> Job:
    """Fetch a single job by ID."""
    return (await get_jobs(client, [job_id], cache=cache))[0]


async def list_jobs(
    client: GitLabClient,
    pipeline_id: int,
    *,
    scope: JobStatus | None = None,
    cache: Cache | None = None,
) -> AsyncIterator[Job]:
    """Stream jobs for a pipeline as an async generator.

    scope=None fetches ALL jobs.
    Yields jobs as pages arrive; each terminal-state job is cached individually.
    """
    project_id = client._config.project_id or ""
    async for page in client.iter_jobs(pipeline_id, scope=scope):
        for job in page.items:
            if cache is not None and job.status in _JOB_TERMINAL:
                cache[CacheNS.OBJECTS].set(
                    ("jobs", project_id, job.id),
                    job,
                    ttl=CACHE_TTL_FINISHED_JOB,
                )
            yield job


def _make_job_predicate(
    *,
    failed_only: bool,
    name_pattern: str | None,
    stage: str | None,
) -> Callable[[Job], bool]:
    compiled = re.compile(name_pattern) if name_pattern else None

    def _pred(job: Job) -> bool:
        if failed_only and not job.has_failed:
            return False
        if compiled and not compiled.search(job.name):
            return False
        if stage and job.stage != stage:
            return False
        return True

    return _pred


@overload
def filter_jobs(
    jobs: AsyncIterable[Job],
    *,
    failed_only: bool = ...,
    name_pattern: str | None = ...,
    stage: str | None = ...,
) -> AsyncIterator[Job]: ...


@overload
def filter_jobs(
    jobs: Iterable[Job],
    *,
    failed_only: bool = ...,
    name_pattern: str | None = ...,
    stage: str | None = ...,
) -> list[Job]: ...


def filter_jobs(
    jobs: AsyncIterable[Job] | Iterable[Job],
    *,
    failed_only: bool = False,
    name_pattern: str | None = None,
    stage: str | None = None,
) -> AsyncIterator[Job] | list[Job]:
    """Client-side filter. All active predicates compose with AND.

    Accepts both sync iterables (returns list) and async iterables (returns
    AsyncIterator, yielding matching jobs as they arrive).
    """
    pred = _make_job_predicate(failed_only=failed_only, name_pattern=name_pattern, stage=stage)
    if isinstance(jobs, AsyncIterable):
        async def _afilter() -> AsyncIterator[Job]:
            async for job in jobs:
                if pred(job):
                    yield job
        return _afilter()
    return [j for j in jobs if pred(j)]
