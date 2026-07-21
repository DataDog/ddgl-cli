from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ddgl.cache.cache_config import CacheNS
from ddgl.constants import CACHE_TTL_FINISHED_JOB, JobStatus, PipelineStatus
from ddgl.core.pipeline import list_pipelines, resolve_pipeline
from ddgl.exceptions import NoPipelineFoundError
from ddgl.model.attach import AttachEvent

if TYPE_CHECKING:
    from ddgl.cache.cache import Cache
    from ddgl.client import GitLabClient
    from ddgl.model.job import Job
    from ddgl.model.pipeline import Pipeline

logger = logging.getLogger("ddgl.core.attach")

# ---------------------------------------------------------------------------
# ETA seam
# ---------------------------------------------------------------------------
#
# `attach`'s live view has a slot for "time remaining", but there is no ETA
# field in the GitLab API. v1 ships no estimation logic at all — just this
# protocol and a no-op implementation — so the slot renders empty. A future
# estimator (preferred: backed by Datadog CI Visibility historical durations,
# rather than querying GitLab pipeline history) can be wired in without any
# change to the attach engine or renderers.


@runtime_checkable
class DurationEstimator(Protocol):
    """Estimates time remaining for a running pipeline.

    Implementations may use any signal (historical durations, per-job
    critical path, etc.). Return None when no estimate is available.
    """

    def estimate_remaining(
        self, pipeline: Pipeline, jobs: list[Job]
    ) -> timedelta | None: ...


class NullEstimator:
    """No-op estimator. Always returns None (v1: no ETA implementation)."""

    def estimate_remaining(
        self, pipeline: Pipeline, jobs: list[Job]
    ) -> timedelta | None:
        return None


# ---------------------------------------------------------------------------
# attach() engine
# ---------------------------------------------------------------------------
#
# Stateless: no retry/resume concept. Every call resolves (or waits for) a
# pipeline, emits a full snapshot, then polls until terminal or `timeout`.
# GitLab is the only source of truth; re-invoking `attach` after it stops
# (e.g. a harness killed the call at its own timeout) just runs this same
# sequence again — that's the whole resumability story.

# Jobs "done" for progress-counting purposes: any terminal status. Mirrors
# core/jobs.py's private terminal set; kept local since it's a small,
# self-contained detail of attach's progress rollups.
_JOB_DONE = frozenset(
    {JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.SKIPPED}
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _cache_terminal_jobs(cache: Cache | None, project_id: str, jobs: list[Job]) -> None:
    """Write terminal-state jobs to the durable object cache.

    Mirrors core/jobs.py's caching rule. Needed here because attach polls via
    `client.get_all_jobs(..., fresh=True)` directly — a raw client call that,
    unlike core/jobs.py's wrappers, never writes to the structured cache.
    """
    if cache is None:
        return
    for job in jobs:
        if job.status in _JOB_DONE:
            cache[CacheNS.OBJECTS].set(
                ("jobs", project_id, job.id), job, ttl=CACHE_TTL_FINISHED_JOB
            )


def _cache_terminal_pipeline(cache: Cache | None, project_id: str, pipeline: Pipeline) -> None:
    """Write a SUCCESS pipeline to the durable object cache.

    Mirrors core/pipeline.py's rule: only SUCCESS is cached (other terminal
    statuses can still change, e.g. a manual retry). See _cache_terminal_jobs
    for why attach needs its own copy of this write.
    """
    if cache is None:
        return
    if pipeline.status == PipelineStatus.SUCCESS:
        cache[CacheNS.OBJECTS].set(
            ("pipelines", project_id, pipeline.id), pipeline, ttl=CACHE_TTL_FINISHED_JOB
        )


def _rollup(jobs: list[Job]) -> tuple[int, int, tuple[str, ...]]:
    """Return (jobs_total, jobs_done, failed_job_names) for a job list."""
    total = len(jobs)
    done = sum(1 for j in jobs if j.status in _JOB_DONE)
    failed = tuple(j.name for j in jobs if j.has_failed)
    return total, done, failed


def _result_event(pipeline: Pipeline, jobs: list[Job], *, reason: str) -> AttachEvent:
    elapsed = pipeline.elapsed
    _, _, failed = _rollup(jobs)
    return AttachEvent(
        kind="result",
        ts=_now(),
        pipeline_id=pipeline.id,
        status=str(pipeline.status),
        failed_jobs=failed,
        duration=elapsed.total_seconds() if elapsed is not None else None,
        reason=reason,
    )


async def _resolve_or_wait(
    client: GitLabClient,
    *,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    wait_for_start: bool,
    deadline: float | None,
    interval: float,
    cache: Cache | None,
) -> Pipeline | None:
    """Resolve the target pipeline, waiting for one to appear if needed.

    Returns None if `wait_for_start` is True and no pipeline appeared before
    `deadline`. Propagates NoPipelineFoundError when `wait_for_start` is False,
    and propagates any other exception (NotFoundError, ConfigError) always —
    those represent a real failure to start, not something to wait out.
    """
    try:
        return await resolve_pipeline(client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache)
    except NoPipelineFoundError:
        if not wait_for_start:
            raise

    while True:
        if deadline is not None and time.monotonic() >= deadline:
            return None
        sleep_for = interval if deadline is None else min(interval, deadline - time.monotonic())
        await asyncio.sleep(max(sleep_for, 0))
        try:
            return await resolve_pipeline(client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache)
        except NoPipelineFoundError:
            continue


async def _find_newer_pipeline(
    client: GitLabClient, ref: str, current_id: int, *, cache: Cache | None
) -> Pipeline | None:
    """Return a pipeline for `ref` newer than `current_id`, or None."""
    candidates = await list_pipelines(client, ref, count=5, cache=cache)
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.id)
    return newest if newest.id > current_id else None


async def attach(
    client: GitLabClient,
    *,
    ref: str | None = None,
    pipeline_id: int | None = None,
    depth: int = 10,
    interval: float = 10.0,
    heartbeat: bool = False,
    wait_for_start: bool = True,
    follow: bool = False,
    timeout: float | None = None,
    cache: Cache | None = None,
) -> AsyncIterator[AttachEvent]:
    """Block on a CI pipeline, yielding AttachEvents until terminal/timeout.

    Yields events only — never prints, never chooses an exit code. See
    render/attach.py for presentation and cli/attach.py for the exit-code
    mapping.

    Flow: resolve (or wait for) the pipeline -> emit a full snapshot -> poll
    every `interval` seconds (cache-bypassed) -> emit a `pipeline`/`job` event
    per transition, a `heartbeat` on quiet ticks (if enabled), a `switched`
    event on follow-rebind -> emit a final `result` event and return once the
    pipeline is terminal or `timeout` elapses.
    """
    project_id = client._config.project_id or ""
    deadline = time.monotonic() + timeout if timeout is not None else None

    pipeline = await _resolve_or_wait(
        client, ref=ref, pipeline_id=pipeline_id, depth=depth,
        wait_for_start=wait_for_start, deadline=deadline, interval=interval, cache=cache,
    )
    if pipeline is None:
        yield AttachEvent(kind="result", ts=_now(), reason="timeout")
        return

    jobs = await client.get_all_jobs(pipeline.id, fresh=True)
    _cache_terminal_jobs(cache, project_id, jobs)

    jobs_total, jobs_done, failed_jobs = _rollup(jobs)
    logger.info(
        "attach: resolved pipeline %d (%s), %d jobs", pipeline.id, pipeline.status, jobs_total
    )
    yield AttachEvent(
        kind="snapshot",
        ts=_now(),
        pipeline_id=pipeline.id,
        status=str(pipeline.status),
        jobs_total=jobs_total,
        jobs_done=jobs_done,
        failed_jobs=failed_jobs,
    )

    if pipeline.is_finished:
        yield _result_event(pipeline, jobs, reason="terminal")
        return

    while True:
        if deadline is not None and time.monotonic() >= deadline:
            yield _result_event(pipeline, jobs, reason="timeout")
            return

        sleep_for = interval if deadline is None else min(interval, deadline - time.monotonic())
        await asyncio.sleep(max(sleep_for, 0))

        if follow:
            newer = await _find_newer_pipeline(client, pipeline.ref, pipeline.id, cache=cache)
            if newer is not None:
                logger.info("attach: following newer pipeline %d (was %d)", newer.id, pipeline.id)
                yield AttachEvent(
                    kind="switched",
                    ts=_now(),
                    pipeline_id=newer.id,
                    message=f"newer pipeline #{newer.id} found for ref {newer.ref!r}; "
                            f"switching from #{pipeline.id}",
                )
                pipeline = newer
                jobs = await client.get_all_jobs(pipeline.id, fresh=True)
                _cache_terminal_jobs(cache, project_id, jobs)
                _cache_terminal_pipeline(cache, project_id, pipeline)
                if pipeline.is_finished:
                    # The pipeline we just switched to may already be done
                    # (e.g. a fast re-push). Don't wait for another tick.
                    yield _result_event(pipeline, jobs, reason="terminal")
                    return
                continue

        fresh_pipeline = await client.get_pipeline(pipeline.id, fresh=True)
        fresh_jobs = await client.get_all_jobs(pipeline.id, fresh=True)
        _cache_terminal_pipeline(cache, project_id, fresh_pipeline)
        _cache_terminal_jobs(cache, project_id, fresh_jobs)

        changed = False

        if fresh_pipeline.status != pipeline.status:
            yield AttachEvent(
                kind="pipeline",
                ts=_now(),
                pipeline_id=fresh_pipeline.id,
                old_status=str(pipeline.status),
                status=str(fresh_pipeline.status),
            )
            changed = True

        old_status_by_id = {j.id: j.status for j in jobs}
        for job in fresh_jobs:
            prev = old_status_by_id.get(job.id)
            if prev != job.status:
                yield AttachEvent(
                    kind="job",
                    ts=_now(),
                    job_id=job.id,
                    job_name=job.name,
                    old_status=str(prev) if prev is not None else None,
                    status=str(job.status),
                    duration=job.duration,
                    message=job.failure_reason if job.has_failed else None,
                )
                changed = True

        if not changed and heartbeat:
            hb_total, hb_done, hb_failed = _rollup(fresh_jobs)
            yield AttachEvent(
                kind="heartbeat", ts=_now(),
                jobs_total=hb_total, jobs_done=hb_done, failed_jobs=hb_failed,
            )

        pipeline, jobs = fresh_pipeline, fresh_jobs

        if pipeline.is_finished:
            logger.info("attach: pipeline %d reached terminal status %s", pipeline.id, pipeline.status)
            yield _result_event(pipeline, jobs, reason="terminal")
            return
