from __future__ import annotations

from enum import StrEnum

import msgspec


class AttachEventKind(StrEnum):
    SNAPSHOT = "snapshot"
    JOB = "job"
    PIPELINE = "pipeline"
    POLL = "poll"
    HEARTBEAT = "heartbeat"
    SWITCHED = "switched"
    RESULT = "result"


class AttachEvent(msgspec.Struct):
    """A single event emitted by the `ddgl attach` engine.

    One `kind`-tagged struct rather than a class hierarchy: renderers switch
    on `.kind` and the whole thing serializes cleanly for `--json` (JSONL).

    `pipeline_id`, `ref`, `current_stage`, `pipeline_elapsed`, `jobs_total`,
    `jobs_done`, `failed_jobs`, and `eta_seconds` are the current *rollup* —
    populated on every event that has a resolved pipeline, not just
    snapshot/poll/heartbeat. This is deliberate: a renderer (e.g. the live
    single-line view) should never need to track cross-event state, or hold
    a Pipeline/Job domain object, just to answer "how many jobs are done
    right now" or "how long has this been running" — the latest event
    always has the answer. `current_stage` is a heuristic: the OLDEST stage
    that still has an incomplete job (the bottleneck), approximated by each
    stage's minimum job ID since GitLab returns jobs newest-ID-first, not in
    stage order — not an authoritative GitLab concept. `eta_seconds` is
    always None — v1 ships no ETA estimation.

    Exceptions to "every event has the rollup": attach() emits TWO
    `snapshot` events. The first fires immediately after resolving the
    pipeline, before the job list is fetched (which can be slow on a
    pipeline with hundreds of jobs) — its `jobs_total`/`jobs_done`/
    `failed_jobs`/`current_stage` are all still at their None/() defaults,
    since GitLab has no job-count endpoint (job counts are only knowable by
    listing jobs). The second snapshot, once jobs are loaded, has the full
    rollup like every subsequent event. Renderers must treat
    `jobs_total is None` as "still loading", not as zero jobs. Separately,
    the wait-for-start-timeout `result` event (no pipeline was ever
    resolved) has no rollup at all — pipeline_id is also None there.

    Kind-specific fields, otherwise unset:
    - job:       job_id, job_name, job_stage (that job's own stage — distinct
                 from the contextual `current_stage` above), old_status,
                 status, duration (that job's own duration), message (the
                 job's failure_reason, when failed)
    - pipeline:  old_status, status
    - poll:      the post-poll rollup summary
    - switched:  message (human-readable description of the switch)
    - result:    status, duration (final pipeline elapsed seconds — same
                 value as `pipeline_elapsed` at that point, kept as its own
                 field since `duration` already has this meaning here),
                 reason ("terminal" or "timeout")
    """

    kind: AttachEventKind
    ts: str
    pipeline_id: int | None = None
    ref: str | None = None
    current_stage: str | None = None
    pipeline_elapsed: float | None = None
    status: str | None = None
    old_status: str | None = None
    job_id: int | None = None
    job_name: str | None = None
    job_stage: str | None = None
    duration: float | None = None
    message: str | None = None
    jobs_total: int | None = None
    jobs_done: int | None = None
    failed_jobs: tuple[str, ...] = ()
    eta_seconds: float | None = None
    reason: str | None = None
