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


class DetailLevel(StrEnum):
    """How much of attach()'s event stream a renderer shows.

    none=final state only, minimal=summaries only, normal=summaries + job
    transitions to terminal states, full=everything. See render/attach.py's
    _visible_at and _live_markup for how each level is applied.
    """

    NONE = "none"
    MINIMAL = "minimal"
    NORMAL = "normal"
    FULL = "full"


class AttachEvent(msgspec.Struct):
    """A single event emitted by the `ddgl attach` engine.

    One `kind`-tagged struct rather than a class hierarchy: renderers switch
    on `.kind` and the whole thing serializes cleanly for `--json` (JSONL).

    `pipeline_id`, `ref`, `current_stage`, `pipeline_elapsed`, `jobs_total`,
    `jobs_done`, `failed_jobs`, and `eta_seconds` are a *rollup* of the
    latest known pipeline/job state, so a renderer (e.g. the live
    single-line view) never needs to track cross-event state just to answer
    "how many jobs are done" or "how long has this been running". Most
    events carry the full rollup — two exceptions: attach()'s very first
    `snapshot` (emitted before the job list is fetched, which can be slow
    on a pipeline with hundreds of jobs) has `jobs_total`/`jobs_done`/
    `failed_jobs`/`current_stage` still at their None/() defaults — treat
    `jobs_total is None` as "still loading", not zero jobs. The
    wait-for-start-timeout `result` event (no pipeline was ever resolved)
    has no rollup at all — `pipeline_id` is also None there.

    `current_stage` is a heuristic: the OLDEST stage that still has an
    incomplete job (the bottleneck), approximated by each stage's minimum
    job ID since GitLab returns jobs newest-ID-first, not in stage order —
    not an authoritative GitLab concept. `eta_seconds` is always None — v1
    ships no ETA estimation.

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
