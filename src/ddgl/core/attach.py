# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING

import httpx
import msgspec

from ddgl.constants import MAX_CONSECUTIVE_POLL_FAILURES
from ddgl.core.jobs import cache_terminal_jobs
from ddgl.core.pipeline import cache_terminal_pipeline, list_pipelines, resolve_pipeline
from ddgl.core.retry import retry_jobs, tally_attempts
from ddgl.exceptions import GitLabAPIError, NoPipelineFoundError
from ddgl.model.attach import (
    AttachEvent,
    EventContext,
    HeartbeatEvent,
    JobEvent,
    PipelineEvent,
    PipelineState,
    PollEvent,
    ResultEvent,
    RetryEvent,
    RetryPolicy,
    SnapshotEvent,
    SwitchedEvent,
    now_iso,
)

if TYPE_CHECKING:
    from ddgl.cache.cache import Cache
    from ddgl.client import GitLabClient
    from ddgl.model.job import Job
    from ddgl.model.pipeline import Pipeline
    from ddgl.model.retry import AttemptTally, RetryOutcome

logger = logging.getLogger("ddgl.core.attach")

# ---------------------------------------------------------------------------
# attach() engine
# ---------------------------------------------------------------------------
#
# Stateless: no retry/resume concept. Every call resolves (or waits for) a
# pipeline, emits a full snapshot, then polls until terminal or `timeout`.
# GitLab is the only source of truth; re-invoking `attach` after it stops
# (e.g. a harness killed the call at its own timeout) just runs this same
# sequence again — that's the whole resumability story.


def _build_pipeline_event(
    prev: Pipeline, curr: Pipeline, context: EventContext
) -> PipelineEvent | None:
    """A PipelineEvent if the pipeline changed status, else None."""
    if curr.status == prev.status:
        return None
    return PipelineEvent(
        ts=now_iso(),
        old_status=str(prev.status),
        status=str(curr.status),
        **context.as_fields(),
    )


def _build_job_events(
    prev: list[Job], curr: list[Job], context: EventContext
) -> list[JobEvent]:
    """A JobEvent per job whose status changed, in `curr` order.

    A job absent from `prev` gets `old_status=None`. That covers a job
    appearing mid-run, and is also how a retried job surfaces: GitLab
    excludes retried records from the job list, so the old attempt
    disappears and a new ID takes its place under the same name.
    """
    old_status_by_id = {job.id: job.status for job in prev}
    events = []
    for job in curr:
        old = old_status_by_id.get(job.id)
        if old == job.status:
            continue
        events.append(
            JobEvent(
                ts=now_iso(),
                job_id=job.id,
                job_name=job.name,
                job_stage=job.stage,
                old_status=str(old) if old is not None else None,
                status=str(job.status),
                duration=job.duration,
                message=job.failure_reason if job.has_failed else None,
                **context.as_fields(),
            )
        )
    return events


def _build_tick_events(
    prev: PipelineState | None,
    curr: PipelineState,
    *,
    context: EventContext,
    heartbeat: bool,
) -> list[AttachEvent]:
    """The events describing what changed between two ticks.

    With no previous tick — the first one, or the first after switching
    pipelines — everything is new, so this is a lone SnapshotEvent rather
    than a diff.

    Otherwise: the pipeline's status change (if any), then one event per
    job that changed status, then a single trailing PollEvent. A tick where
    nothing changed emits a HeartbeatEvent if `heartbeat` is set, and
    nothing at all if it isn't.
    """
    if prev is None:
        return [SnapshotEvent(ts=now_iso(), status=str(curr.pipeline.status), **context.as_fields())]

    events: list[AttachEvent] = []
    pipeline_event = _build_pipeline_event(prev.pipeline, curr.pipeline, context)
    if pipeline_event is not None:
        events.append(pipeline_event)
    events.extend(_build_job_events(prev.jobs, curr.jobs, context))

    if events:
        events.append(PollEvent(ts=now_iso(), **context.as_fields()))
    elif heartbeat:
        events.append(HeartbeatEvent(ts=now_iso(), **context.as_fields()))
    return events


class _RetryLedger(msgspec.Struct):
    """What one attach run has to remember about the retries it issued."""

    posted_job_ids: set[int] = msgspec.field(default_factory=set)
    """Records already retried. GitLab may not have processed a POST by the
    next tick, leaving the old failed record still the newest one for its
    name."""

    total_issued: int = 0
    """Accepted retries so far, counted against `RetryPolicy.total`."""


def _candidates_before_tally(
    state: PipelineState, policy: RetryPolicy, ledger: _RetryLedger
) -> list[Job]:
    """Failed jobs that could still be retried, judged on what this tick
    already knows: blocking, not already retried by this run, not
    name-excluded, and within the global budget.

    The remaining checks — newest record for the name, and per-name budget
    — need an attempt tally, which costs a request. An empty result means
    this tick needs no tally.
    """
    if policy.total and ledger.total_issued >= policy.total:
        return []
    return [
        job for job in state.jobs
        if job.is_blocking
        and job.id not in ledger.posted_job_ids
        and not any(re.search(pattern, job.name) for pattern in policy.exclude)
    ]


def _within_budget(job: Job, tally: AttemptTally, policy: RetryPolicy) -> bool:
    """Whether `job` has attempts left under `RetryPolicy.attempts_per_job`.

    A job that has never been retried already has one attempt, so the
    comparison is against attempts *beyond* the first.
    """
    if not policy.attempts_per_job:
        return True
    return tally.count(job.name) - 1 < policy.attempts_per_job


async def _apply_retry_policy(
    client: GitLabClient,
    state: PipelineState,
    policy: RetryPolicy,
    ledger: _RetryLedger,
) -> tuple[list[RetryOutcome], AttemptTally | None]:
    """Retry the failed jobs this tick allows, returning what happened and
    the tally the decision used.

    The tally is None when no job got as far as needing one.
    """
    if not policy.enabled:
        return [], None

    candidates = _candidates_before_tally(state, policy, ledger)
    if not candidates:
        return [], None

    tally = await tally_attempts(client, state.pipeline.id, {j.name for j in candidates})

    selected: list[Job] = []
    for job in candidates:
        if policy.total and ledger.total_issued + len(selected) >= policy.total:
            logger.info("attach: retry budget of %d reached", policy.total)
            break
        if not tally.is_newest(job):
            logger.info(
                "attach: %s already has a newer attempt, not retrying job %d",
                job.name, job.id,
            )
            continue
        if not _within_budget(job, tally, policy):
            logger.info(
                "attach: %s has used its %d retries, not retrying job %d",
                job.name, policy.attempts_per_job, job.id,
            )
            continue
        selected.append(job)

    if not selected:
        return [], tally

    outcomes = await retry_jobs(client, selected)
    for outcome in outcomes:
        ledger.posted_job_ids.add(outcome.old_job_id)
        if outcome.new_job is not None:
            ledger.total_issued += 1
    return outcomes, tally


def _build_retry_events(
    outcomes: Sequence[RetryOutcome], tally: AttemptTally | None, context: EventContext
) -> list[RetryEvent]:
    """One event per retry GitLab accepted.

    Rejected retries produce no event; `retry_jobs` has already logged
    them, and there is no new job to point at.
    """
    events = []
    for outcome in outcomes:
        new_job = outcome.new_job
        if new_job is None:
            continue
        events.append(
            RetryEvent(
                ts=now_iso(),
                job_id=outcome.old_job_id,
                job_name=outcome.job_name,
                job_stage=new_job.stage,
                new_job_id=new_job.id,
                attempt=(tally.count(outcome.job_name) if tally else 0) + 1,
                **context.as_fields(),
            )
        )
    return events


async def _get_pipeline_state(
    client: GitLabClient, pipeline_id: int, *, cache: Cache | None, project_id: str
) -> PipelineState:
    """Fetch a pipeline's current status and its full job list.

    Both reads bypass the API response cache: its TTLs (30s for a
    pipeline, 15s for a job list) are tuned for browsing and would
    otherwise put a floor under the poll interval, so a 10s poll would
    keep re-reading the same stale tick.

    Terminal jobs and a SUCCESS pipeline are written to the durable object
    cache on the way through.

    Raises GitLabAPIError or httpx.TransportError; whether one bad fetch
    should end the run is the caller's decision, not this function's.
    """
    pipeline = await client.get_pipeline(pipeline_id, fresh=True)
    jobs = await client.get_all_jobs(pipeline_id, fresh=True)
    cache_terminal_pipeline(cache, project_id, pipeline)
    cache_terminal_jobs(cache, project_id, jobs)
    return PipelineState(pipeline=pipeline, jobs=jobs)


async def _check_for_new_pipeline(
    client: GitLabClient, current: PipelineState, *, cache: Cache | None, project_id: str
) -> PipelineState | None:
    """The state of a pipeline newer than `current`'s for the same ref, or
    None if there isn't one.

    Never raises: a failure here means "no switch this tick", leaving the
    caller's own poll of the current pipeline unaffected.

    The newer pipeline's status comes from the listing that found it, so
    unlike `_get_pipeline_state` this issues no second request for it.
    """
    try:
        candidates = await list_pipelines(
            client, current.pipeline.ref, count=5, cache=cache, fresh=True
        )
    except (GitLabAPIError, httpx.TransportError) as exc:
        logger.warning("attach: follow-check failed (%s), will retry next tick", exc)
        return None

    newest = max(candidates, key=lambda p: p.id, default=None)
    if newest is None or newest.id <= current.pipeline.id:
        return None

    try:
        jobs = await client.get_all_jobs(newest.id, fresh=True)
    except (GitLabAPIError, httpx.TransportError) as exc:
        logger.warning(
            "attach: found newer pipeline %d but failed to fetch its jobs (%s); "
            "staying on #%d, will retry next tick", newest.id, exc, current.pipeline.id,
        )
        return None

    logger.info(
        "attach: following newer pipeline %d (was %d)", newest.id, current.pipeline.id
    )
    cache_terminal_jobs(cache, project_id, jobs)
    cache_terminal_pipeline(cache, project_id, newest)
    return PipelineState(pipeline=newest, jobs=jobs)


async def _resolve_or_wait(
    client: GitLabClient,
    *,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    wait_for_start: bool,
    interval: float,
    cache: Cache | None,
) -> Pipeline:
    """Resolve the target pipeline, waiting for one to appear if needed.

    Raises NoPipelineFoundError immediately when `wait_for_start` is False.
    Any other exception (NotFoundError, ConfigError) always propagates —
    those are a real failure to start, not something waiting will fix.

    With `wait_for_start`, waits indefinitely; `attach()`'s
    `asyncio.timeout` is what bounds the wait.
    """
    while True:
        try:
            return await resolve_pipeline(
                client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache, fresh=True
            )
        except NoPipelineFoundError:
            if not wait_for_start:
                raise
        await asyncio.sleep(interval)


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
    retry_policy: RetryPolicy | None = None,
    cache: Cache | None = None,
) -> AsyncIterator[AttachEvent]:
    """Block on a CI pipeline, yielding AttachEvents until terminal/timeout.

    Wraps `_poll_pipeline` to enforce `timeout` and, on expiry, to close the
    stream with a timeout ResultEvent built from the last event seen.
    """
    last_event: AttachEvent | None = None
    try:
        async with asyncio.timeout(timeout):
            async for event in _poll_pipeline(
                client,
                ref=ref,
                pipeline_id=pipeline_id,
                depth=depth,
                interval=interval,
                heartbeat=heartbeat,
                wait_for_start=wait_for_start,
                follow=follow,
                retry_policy=retry_policy,
                cache=cache,
            ):
                last_event = event
                yield event
    except TimeoutError:
        yield ResultEvent.timed_out(last_event)


async def _poll_pipeline(
    client: GitLabClient,
    *,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    interval: float,
    heartbeat: bool,
    wait_for_start: bool,
    follow: bool,
    retry_policy: RetryPolicy | None,
    cache: Cache | None,
) -> AsyncIterator[AttachEvent]:
    """Yield the event stream for one attach run, ending at the pipeline's
    terminal state.

    Yields events only — never prints, never chooses an exit code. See
    render/attach.py for presentation and cli/attach.py for the exit-code
    mapping.

    Resolves (or waits for) the pipeline, emits an early snapshot, then
    loops: emit this tick's events, stop if the pipeline is finished,
    otherwise sleep and read the next tick. `--follow` replaces the tick
    with a newer pipeline's when one appears.

    Nothing here tracks a deadline: every await is bounded by `attach()`'s
    `asyncio.timeout`, which is the engine's sole timeout mechanism.
    """
    project_id = client._config.project_id or ""
    pipeline = await _resolve_or_wait(
        client, ref=ref, pipeline_id=pipeline_id, depth=depth,
        wait_for_start=wait_for_start, interval=interval, cache=cache,
    )

    # Emitted before the job fetch below, which on a pipeline with hundreds
    # of jobs takes long enough (even with parallel pagination) to look like
    # a frozen terminal. Job counts are unknown at this point — GitLab has no
    # job-count endpoint (see
    # docs/superpowers/specs/2026-07-21-attach-delta-polling-future-work.md)
    # — so those fields keep their defaults until the first full tick.
    logger.info("attach: attached to pipeline %d (%s)", pipeline.id, pipeline.status)
    elapsed = pipeline.elapsed
    yield SnapshotEvent(
        ts=now_iso(),
        pipeline_id=pipeline.id,
        ref=pipeline.ref,
        status=str(pipeline.status),
        pipeline_elapsed=elapsed.total_seconds() if elapsed is not None else None,
    )

    jobs = await client.get_all_jobs(pipeline.id, fresh=True)
    cache_terminal_jobs(cache, project_id, jobs)
    logger.info(
        "attach: loaded %d jobs for pipeline %d (%s)", len(jobs), pipeline.id, pipeline.status
    )

    # The resolved pipeline and its jobs are the first tick, so the loop
    # starts already holding one: re-reading them through
    # _get_pipeline_state() would repeat a fetch just made.
    policy = retry_policy or RetryPolicy()
    ledger = _RetryLedger()
    prev: PipelineState | None = None
    curr = PipelineState(pipeline=pipeline, jobs=jobs)
    consecutive_failures = 0

    while True:
        context = EventContext.from_state(curr)
        for event in _build_tick_events(prev, curr, context=context, heartbeat=heartbeat):
            yield event

        outcomes, tally = await _apply_retry_policy(client, curr, policy, ledger)
        for event in _build_retry_events(outcomes, tally, context):
            yield event

        # A pipeline we just retried reads as terminal only because GitLab
        # hasn't processed the retry yet; it is about to run again. Gating
        # on accepted retries means a tick where every one was rejected
        # still finishes normally instead of waiting for --timeout.
        retried_now = sum(1 for o in outcomes if o.new_job is not None)
        if curr.pipeline.is_finished and not retried_now:
            logger.info(
                "attach: pipeline %d reached terminal status %s",
                curr.pipeline.id, curr.pipeline.status,
            )
            yield ResultEvent.terminal(curr, context, retries=ledger.total_issued)
            return

        # Acquire the next tick. A failed read is skipped rather than fatal:
        # the client already retried transient (408/429/5xx) errors, so
        # reaching here means those were exhausted or the error was never
        # transient — but one bad tick must not kill a long attach, which is
        # the "500 mid-poll on a huge pipeline" case seen in practice. Give
        # up only after MAX_CONSECUTIVE_POLL_FAILURES in a row.
        prev = curr
        while True:
            await asyncio.sleep(interval)

            if follow:
                switched = await _check_for_new_pipeline(
                    client, curr, cache=cache, project_id=project_id
                )
                if switched is not None:
                    yield SwitchedEvent(
                        ts=now_iso(),
                        message=f"newer pipeline #{switched.pipeline.id} found for ref "
                                f"{switched.pipeline.ref!r}; switching from #{curr.pipeline.id}",
                        **EventContext.from_state(switched).as_fields(),
                    )
                    # No previous tick for a pipeline we've never seen, so
                    # the next pass snapshots it instead of diffing it
                    # against the old pipeline's unrelated jobs.
                    prev, curr = None, switched
                    break

            try:
                curr = await _get_pipeline_state(
                    client, curr.pipeline.id, cache=cache, project_id=project_id
                )
            except (GitLabAPIError, httpx.TransportError) as exc:
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
            break
