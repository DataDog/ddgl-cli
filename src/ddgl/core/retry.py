# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.constants import JobStatus
from ddgl.core._concurrency import gather_bounded
from ddgl.core.jobs import filter_jobs, get_jobs, list_jobs
from ddgl.exceptions import GitLabAPIError, NotFoundError
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.model.retry import AttemptTally, RetryOutcome, RetrySelection

logger = logging.getLogger("ddgl.core.retry")


async def retry_job(client: GitLabClient, job_id: int) -> Job:
    """Retry a single job, returning the new `pending` job GitLab creates.

    The new job has a different ID; the name is what carries across a
    retry.

    Raises:
        NotFoundError: job does not exist.
        GitLabAPIError: other HTTP error (e.g. 403 insufficient token scope).
    """
    return await client.retry_job(job_id)


async def retry_pipeline(client: GitLabClient, pipeline_id: int) -> Pipeline:
    """Retry every failed and canceled job in a pipeline.

    Returns the pipeline, now `running`. GitLab does not report which jobs
    it restarted — use `retry_jobs` when that detail matters.

    Raises:
        NotFoundError: pipeline does not exist.
        GitLabAPIError: other HTTP error (e.g. 403 insufficient token scope).
    """
    # Says "every failed and canceled job" rather than restating the
    # client's own "Retrying pipeline N": this is the line that makes a
    # bulk run distinguishable from a targeted one in a log, since bulk
    # otherwise never touches the selection path that announces itself.
    logger.info("Retrying every failed and canceled job in pipeline %d", pipeline_id)
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
            # Logged here rather than left to the caller: a partial failure
            # is reported as data, so without this a rejected retry would
            # leave no trace in -v output.
            logger.warning("Retry of job %d (%s) failed: %s", job.id, job.name, exc)
            return RetryOutcome(old_job_id=job.id, job_name=job.name, error=str(exc))
        logger.debug("Retried job %d (%s) as %d", job.id, job.name, new_job.id)
        return RetryOutcome(old_job_id=job.id, job_name=job.name, new_job=new_job)

    logger.info("Retrying %d job(s)", len(jobs))
    outcomes = await gather_bounded(_retry_one(job) for job in jobs)
    failures = sum(1 for o in outcomes if o.new_job is None)
    if failures:
        logger.warning("%d of %d retries were rejected", failures, len(outcomes))
    return outcomes


def _select(jobs: list[Job], *, force: bool, pipeline: Pipeline | None) -> RetrySelection:
    """Apply the retryable gate and work out which pipeline to report."""
    matched = len(jobs)
    candidates = jobs if force else [job for job in jobs if job.is_retryable]

    if pipeline is not None:
        pipeline_id, ref = pipeline.id, pipeline.ref
    else:
        # Only --job can produce a set spanning pipelines; naming one of
        # them would imply the others belong to it too. Jobs of a single
        # pipeline always share its ref, so the first job's is the ref.
        pipeline_ids = {job.pipeline_id for job in candidates}
        one = len(pipeline_ids) == 1
        pipeline_id = pipeline_ids.pop() if one else None
        ref = (candidates[0].ref or None) if one else None

    logger.info(
        "Retry selection: %d matched, %d retryable%s",
        matched, len(candidates), " (--force)" if force else "",
    )
    return RetrySelection(
        jobs=candidates, matched=matched, pipeline_id=pipeline_id, ref=ref,
    )


async def select_by_id(
    client: GitLabClient,
    job_ids: Sequence[int],
    *,
    force: bool = False,
    cache: Cache | None = None,
) -> RetrySelection:
    """Candidates for an explicit list of job IDs.

    No pipeline resolution and no filtering — the IDs *are* the filter.

    Raises:
        NotFoundError: one of the IDs doesn't exist.
    """
    logger.info("Selecting %d job(s) by ID for retry", len(job_ids))
    jobs = await get_jobs(client, job_ids, cache=cache)
    return _select(jobs, force=force, pipeline=None)


async def select_in_pipeline(
    client: GitLabClient,
    pipeline: Pipeline,
    *,
    failed_only: bool = False,
    include_allowed_failures: bool = False,
    stage: str | None = None,
    name_pattern: str | None = None,
    force: bool = False,
    cache: Cache | None = None,
) -> RetrySelection:
    """Candidates among a pipeline's jobs, narrowed by the usual filters.

    `failed_only` is pushed to GitLab as a `scope` query param; stage and
    name have no server-side equivalent on this endpoint, so they are
    applied client-side by `filter_jobs` — the same split `jobs list`
    uses, which is what keeps the two commands selecting the same set.
    """
    logger.info("Selecting jobs to retry in pipeline %d", pipeline.id)
    scope = JobStatus.FAILED if failed_only else None
    jobs = [
        job async for job in filter_jobs(
            list_jobs(client, pipeline.id, scope=scope, cache=cache),
            failed_only=failed_only,
            include_allowed_failures=include_allowed_failures,
            name_pattern=name_pattern,
            stage=stage,
        )
    ]
    return _select(jobs, force=force, pipeline=pipeline)


async def tally_attempts(
    client: GitLabClient, pipeline_id: int, names: set[str]
) -> AttemptTally:
    """Count the recorded attempts of each of `names`, and note which
    record is the newest for each.

    Reads the one endpoint that reports superseded records
    (`include_retried=true`); GitLab's normal job list omits them, so from
    that list alone a job that was retried is indistinguishable from one
    that never was.

    Reflects the moment it is called — nothing is cached between calls.
    """
    records = await client.get_job_attempts(pipeline_id)
    counts: dict[str, int] = defaultdict(int)
    newest_ids: dict[str, int] = {}
    for job in records:
        if job.name not in names:
            continue
        counts[job.name] += 1
        newest_ids[job.name] = max(newest_ids.get(job.name, job.id), job.id)
    return AttemptTally(counts=dict(counts), newest_ids=newest_ids)
