from __future__ import annotations

from typing import Literal

import msgspec

AttachEventKind = Literal["snapshot", "job", "pipeline", "heartbeat", "switched", "result"]


class AttachEvent(msgspec.Struct):
    """A single event emitted by the `ddgl attach` engine.

    One `kind`-tagged struct rather than a class hierarchy: renderers switch
    on `.kind` and the whole thing serializes cleanly for `--json` (JSONL).

    `ref`, `current_stage`, `jobs_total`, `jobs_done`, `failed_jobs`, and
    `eta_seconds` are the current *rollup* — populated on every event, not
    just snapshot/heartbeat. This is deliberate: a renderer (e.g. the live
    single-line view) should never need to track cross-event state, or hold
    a Pipeline/Job domain object, just to answer "how many jobs are done
    right now" or "how long until this is done" — the latest event always
    has the answer. `current_stage` is a heuristic (the stage of the first
    not-yet-done job, falling back to the last job's stage once everything
    is done) — not an authoritative GitLab concept. `eta_seconds` is None
    unless an estimator was passed to attach() (v1 ships none — see
    core/attach.py's DurationEstimator seam).

    Kind-specific fields, otherwise unset:
    - job:       job_id, job_name, job_stage (that job's own stage — distinct
                 from the contextual `current_stage` above), old_status,
                 status, duration, message (the job's failure_reason, when
                 failed)
    - pipeline:  old_status, status
    - switched:  message (human-readable description of the switch)
    - result:    status, duration (pipeline elapsed seconds), reason
                 ("terminal" or "timeout")
    """

    kind: AttachEventKind
    ts: str
    pipeline_id: int | None = None
    ref: str | None = None
    current_stage: str | None = None
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
