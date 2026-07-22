from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ddgl.cache.cache_config import CacheNS
from ddgl.constants import (
    CACHE_TTL_FINISHED_JOB,
    MAX_CONSECUTIVE_POLL_FAILURES,
    JobStatus,
    PipelineStatus,
)
from ddgl.core.pipeline import list_pipelines, resolve_pipeline
from ddgl.exceptions import GitLabAPIError, NoPipelineFoundError
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


def _current_stage(jobs: list[Job]) -> str | None:
    """Best-effort 'what stage are we in' for the live view's headline.

    The OLDEST stage that still has at least one not-yet-done job — the
    stage actually holding up progress, not the most-recently-started one.
    "Oldest" is approximated by each stage's minimum job ID: GitLab returns
    jobs newest-ID-first (not in stage order — there is no API field for
    stage sequence), but job IDs are assigned in roughly creation order, and
    jobs are normally created stage-by-stage at pipeline start. Falls back
    to the oldest stage overall once everything is done, or None for an
    empty job list.
    """
    if not jobs:
        return None

    min_id_by_stage: dict[str, int] = {}
    incomplete_stages: set[str] = set()
    for job in jobs:
        min_id_by_stage[job.stage] = min(min_id_by_stage.get(job.stage, job.id), job.id)
        if job.status not in _JOB_DONE:
            incomplete_stages.add(job.stage)

    candidates = incomplete_stages or min_id_by_stage.keys()
    return min(candidates, key=lambda s: min_id_by_stage[s])


def _context(pipeline: Pipeline, jobs: list[Job], estimator: DurationEstimator) -> dict[str, object]:
    """Rollup fields attached to every event (see AttachEvent's docstring).

    Calls `estimator` here — not in the renderer — because this is where
    real Pipeline/Job domain objects exist. Renderers only ever see the
    resulting `eta_seconds` field on the event, never the estimator itself.
    """
    total, done, failed = _rollup(jobs)
    remaining = estimator.estimate_remaining(pipeline, jobs)
    elapsed = pipeline.elapsed
    return {
        "pipeline_id": pipeline.id,
        "ref": pipeline.ref,
        "current_stage": _current_stage(jobs),
        "pipeline_elapsed": elapsed.total_seconds() if elapsed is not None else None,
        "jobs_total": total,
        "jobs_done": done,
        "failed_jobs": failed,
        "eta_seconds": remaining.total_seconds() if remaining is not None else None,
    }


def _result_event(
    pipeline: Pipeline, jobs: list[Job], estimator: DurationEstimator, *, reason: str
) -> AttachEvent:
    elapsed = pipeline.elapsed
    return AttachEvent(
        kind="result",
        ts=_now(),
        status=str(pipeline.status),
        duration=elapsed.total_seconds() if elapsed is not None else None,
        reason=reason,
        **_context(pipeline, jobs, estimator),  # includes pipeline_id
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
    estimator: DurationEstimator | None = None,
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

    `estimator` defaults to NullEstimator (v1 ships no ETA implementation);
    see DurationEstimator's docstring for the seam this leaves for later.
    """
    project_id = client._config.project_id or ""
    estimator = estimator or NullEstimator()
    deadline = time.monotonic() + timeout if timeout is not None else None

    pipeline = await _resolve_or_wait(
        client, ref=ref, pipeline_id=pipeline_id, depth=depth,
        wait_for_start=wait_for_start, deadline=deadline, interval=interval, cache=cache,
    )
    if pipeline is None:
        yield AttachEvent(kind="result", ts=_now(), reason="timeout")
        return

    # Emit an early snapshot immediately — before the job list fetch below,
    # which can take a long time on a pipeline with hundreds of jobs (even
    # with parallel pagination) — so a human sees "attached" right away
    # instead of a frozen terminal. Job counts are unknown at this point:
    # GitLab has no job-count endpoint (see
    # docs/superpowers/specs/2026-07-21-attach-delta-polling-future-work.md),
    # so jobs_total/jobs_done/current_stage stay at their None/() defaults
    # until the second snapshot below.
    logger.info("attach: attached to pipeline %d (%s)", pipeline.id, pipeline.status)
    elapsed = pipeline.elapsed
    yield AttachEvent(
        kind="snapshot",
        ts=_now(),
        pipeline_id=pipeline.id,
        ref=pipeline.ref,
        status=str(pipeline.status),
        pipeline_elapsed=elapsed.total_seconds() if elapsed is not None else None,
    )

    jobs = await client.get_all_jobs(pipeline.id, fresh=True)
    _cache_terminal_jobs(cache, project_id, jobs)

    ctx = _context(pipeline, jobs, estimator)
    logger.info(
        "attach: loaded %d jobs for pipeline %d (%s)", ctx["jobs_total"], pipeline.id, pipeline.status
    )
    yield AttachEvent(kind="snapshot", ts=_now(), status=str(pipeline.status), **ctx)  # ctx includes pipeline_id

    if pipeline.is_finished:
        yield _result_event(pipeline, jobs, estimator, reason="terminal")
        return

    consecutive_failures = 0

    while True:
        if deadline is not None and time.monotonic() >= deadline:
            yield _result_event(pipeline, jobs, estimator, reason="timeout")
            return

        sleep_for = interval if deadline is None else min(interval, deadline - time.monotonic())
        await asyncio.sleep(max(sleep_for, 0))

        if follow:
            # Opportunistic: a failure here just means "no follow this tick"
            # (the client already retried transient errors internally). It
            # never counts toward giving up — the main poll below still gets
            # its own independent attempt at the current pipeline regardless.
            try:
                newer = await _find_newer_pipeline(client, pipeline.ref, pipeline.id, cache=cache)
            except GitLabAPIError as exc:
                logger.warning("attach: follow-check failed (%s), will retry next tick", exc)
                newer = None
            if newer is not None:
                try:
                    newer_jobs = await client.get_all_jobs(newer.id, fresh=True)
                except GitLabAPIError as exc:
                    logger.warning(
                        "attach: found newer pipeline %d but failed to fetch its jobs (%s); "
                        "staying on #%d, will retry next tick", newer.id, exc, pipeline.id,
                    )
                    newer = None
            if newer is not None:
                logger.info("attach: following newer pipeline %d (was %d)", newer.id, pipeline.id)
                _cache_terminal_jobs(cache, project_id, newer_jobs)
                _cache_terminal_pipeline(cache, project_id, newer)
                yield AttachEvent(
                    kind="switched",
                    ts=_now(),
                    message=f"newer pipeline #{newer.id} found for ref {newer.ref!r}; "
                            f"switching from #{pipeline.id}",
                    **_context(newer, newer_jobs, estimator),  # includes pipeline_id (= newer.id)
                )
                pipeline, jobs = newer, newer_jobs
                if pipeline.is_finished:
                    # The pipeline we just switched to may already be done
                    # (e.g. a fast re-push). Don't wait for another tick.
                    yield _result_event(pipeline, jobs, estimator, reason="terminal")
                    return
                continue

        # The main poll fetch: pipeline status + full job list for THIS
        # tick. The client already retries transient (408/429/5xx) failures
        # internally (see client.py's _get_response) — reaching here means
        # those retries were exhausted, or the error wasn't transient at all
        # (e.g. a genuine 4xx). Either way, one bad tick must not kill a
        # long-running attach: skip it and try again next interval, up to
        # MAX_CONSECUTIVE_POLL_FAILURES before finally giving up. This is
        # exactly the "500 mid-poll on a pipeline with hundreds of jobs"
        # scenario attach hits in practice on large, long-running pipelines.
        try:
            fresh_pipeline = await client.get_pipeline(pipeline.id, fresh=True)
            fresh_jobs = await client.get_all_jobs(pipeline.id, fresh=True)
        except GitLabAPIError as exc:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_POLL_FAILURES:
                logger.warning(
                    "attach: poll failed %d times in a row (%s), giving up",
                    consecutive_failures, exc,
                )
                raise
            logger.warning(
                "attach: poll tick failed (%s), skipping — retrying in %.0fs (failure %d/%d)",
                exc, interval, consecutive_failures, MAX_CONSECUTIVE_POLL_FAILURES,
            )
            continue
        consecutive_failures = 0

        _cache_terminal_pipeline(cache, project_id, fresh_pipeline)
        _cache_terminal_jobs(cache, project_id, fresh_jobs)

        ctx = _context(fresh_pipeline, fresh_jobs, estimator)
        changed = False

        if fresh_pipeline.status != pipeline.status:
            yield AttachEvent(
                kind="pipeline",
                ts=_now(),
                old_status=str(pipeline.status),
                status=str(fresh_pipeline.status),
                **ctx,  # includes pipeline_id (= fresh_pipeline.id)
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
                    job_stage=job.stage,
                    old_status=str(prev) if prev is not None else None,
                    status=str(job.status),
                    duration=job.duration,
                    message=job.failure_reason if job.has_failed else None,
                    **ctx,
                )
                changed = True

        if not changed and heartbeat:
            yield AttachEvent(kind="heartbeat", ts=_now(), **ctx)

        pipeline, jobs = fresh_pipeline, fresh_jobs

        if pipeline.is_finished:
            logger.info("attach: pipeline %d reached terminal status %s", pipeline.id, pipeline.status)
            yield _result_event(pipeline, jobs, estimator, reason="terminal")
            return
