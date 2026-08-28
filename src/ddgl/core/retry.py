# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Sequence

from ddgl.client import GitLabClient
from ddgl.exceptions import GitLabAPIError, NotFoundError
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.model.retry import RetryOutcome

logger = logging.getLogger("ddgl.core.retry")


async def retry_job(client: GitLabClient, job_id: int) -> Job:
    """Retry a single job by ID.

    A pure passthrough to `client.retry_job` — no `cache` parameter, unlike
    the rest of core/: the returned job is always `pending`, which can
    never satisfy `cache_terminal_jobs`' terminal-state gate, so there is
    nothing to cache here, by construction. This wrapper exists so callers
    (cli/retry.py, the TUI's `r` binding) reach retry through core/ like
    every other action, instead of importing client.py directly.

    Raises:
        NotFoundError: job does not exist.
        GitLabAPIError: other HTTP error (e.g. 403 insufficient token scope).
    """
    return await client.retry_job(job_id)


async def retry_pipeline(client: GitLabClient, pipeline_id: int) -> Pipeline:
    """Retry every failed and canceled job in a pipeline.

    Returns only the `Pipeline` — GitLab does not report which jobs it
    restarted (see `retry_job`/`retry_jobs` for that per-job detail).

    A pure passthrough to `client.retry_pipeline`, for the same layering
    reason as `retry_job` — see its docstring.

    Raises:
        NotFoundError: pipeline does not exist.
        GitLabAPIError: other HTTP error (e.g. 403 insufficient token scope).
    """
    return await client.retry_pipeline(pipeline_id)


async def retry_jobs(client: GitLabClient, jobs: Sequence[Job]) -> list[RetryOutcome]:
    """Retry each job concurrently.

    Never raises — a per-job failure lands in that job's
    `RetryOutcome.error` instead, so one bad job in a batch doesn't abort
    the rest.
    """

    async def _retry_one(job: Job) -> RetryOutcome:
        try:
            new_job = await client.retry_job(job.id)
        except (NotFoundError, GitLabAPIError) as exc:
            return RetryOutcome(old_job_id=job.id, job_name=job.name, error=str(exc))
        return RetryOutcome(old_job_id=job.id, job_name=job.name, new_job=new_job)

    return list(await asyncio.gather(*[_retry_one(job) for job in jobs]))


async def count_attempts(
    client: GitLabClient, pipeline_id: int, names: set[str]
) -> dict[str, int]:
    """Attempt counts (job records seen, including retried ones) per job
    name, restricted to `names`.

    Wraps `client.get_job_attempts`, the only endpoint that reports prior
    attempts — GitLab's normal job list excludes them.
    """
    attempts = await client.get_job_attempts(pipeline_id)
    counts: dict[str, int] = defaultdict(int)
    for job in attempts:
        if job.name in names:
            counts[job.name] += 1
    return dict(counts)
