from __future__ import annotations

from typing import Literal

import msgspec

AttachEventKind = Literal["snapshot", "job", "pipeline", "heartbeat", "switched", "result"]


class AttachEvent(msgspec.Struct):
    """A single event emitted by the `ddgl attach` engine.

    One `kind`-tagged struct rather than a class hierarchy: renderers switch
    on `.kind` and the whole thing serializes cleanly for `--json` (JSONL).
    Fields are optional and populated according to `kind`:

    - snapshot:  pipeline_id, status, jobs_total, jobs_done, failed_jobs
    - job:       job_id, job_name, old_status, status, duration
    - pipeline:  pipeline_id, old_status, status
    - heartbeat: jobs_total, jobs_done, failed_jobs
    - switched:  pipeline_id, message (old -> new pipeline id, as text)
    - result:    pipeline_id, status, failed_jobs, duration, reason
                 (reason is "terminal" or "timeout")
    """

    kind: AttachEventKind
    ts: str
    pipeline_id: int | None = None
    status: str | None = None
    old_status: str | None = None
    job_id: int | None = None
    job_name: str | None = None
    duration: float | None = None
    message: str | None = None
    jobs_total: int | None = None
    jobs_done: int | None = None
    failed_jobs: tuple[str, ...] = ()
    reason: str | None = None
