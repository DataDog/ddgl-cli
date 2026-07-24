from __future__ import annotations

from enum import StrEnum

import msgspec


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


class AttachEvent(msgspec.Struct, kw_only=True, tag_field="kind"):
    """Base class for events emitted by the `ddgl attach` engine.

    Construct the specific subclass (`JobEvent(...)`, `SnapshotEvent(...)`,
    etc.) instead of this abstract base.

    `--json` (JSONL) output still gets a `"kind"` string key per line,
    injected by msgspec's tagged-union support (`tag_field`/`tag` below).

    `pipeline_id`, `ref`, `current_stage`, `pipeline_elapsed`, `jobs_total`,
    `jobs_done`, `failed_jobs`, and `eta_seconds` live here rather than
    being redeclared per subclass because the live single-line view reads
    them off every non-final event uniformly (see render/attach.py's
    `_live_markup`). They're a *rollup* of the latest known pipeline/job
    state; `jobs_total is None` means "not loaded yet", not zero jobs.
    `eta_seconds` is always None for now, implementation TBD.
    """

    ts: str
    pipeline_id: int | None = None
    ref: str | None = None
    current_stage: str | None = None
    pipeline_elapsed: float | None = None
    jobs_total: int | None = None
    jobs_done: int | None = None
    failed_jobs: tuple[str, ...] = ()
    eta_seconds: float | None = None


class SnapshotEvent(AttachEvent, kw_only=True, tag="snapshot"):
    """attach()'s initial and post-job-load snapshots."""

    status: str
    """The pipeline's status at attach time."""


class JobEvent(AttachEvent, kw_only=True, tag="job"):
    """A single job's status transition."""

    job_id: int
    """The job's GitLab ID."""

    job_name: str
    """The job's name."""

    job_stage: str
    """That job's own stage — distinct from the contextual `current_stage`
    rollup field on the base class."""

    status: str
    """That job's own status."""

    old_status: str | None = None
    """Status before this transition. None when the job is newly
    discovered this tick (no previously known status)."""

    duration: float | None = None
    """That job's own duration, when known."""

    message: str | None = None
    """The job's failure_reason, when failed."""


class PipelineEvent(AttachEvent, kw_only=True, tag="pipeline"):
    """The pipeline itself transitioned to a new status."""

    status: str
    """The new status."""

    old_status: str
    """Status before this transition."""


class PollEvent(AttachEvent, kw_only=True, tag="poll"):
    """The post-poll rollup summary emitted once after a changed tick."""


class HeartbeatEvent(AttachEvent, kw_only=True, tag="heartbeat"):
    """A tally line emitted on a quiet poll tick (--heartbeat only)."""


class SwitchedEvent(AttachEvent, kw_only=True, tag="switched"):
    """A --follow rebind to a newer pipeline."""

    message: str
    """Human-readable description of the switch."""


class ResultEvent(AttachEvent, kw_only=True, tag="result"):
    """The final event of an attach() run."""

    status: str | None = None
    """The final status. None if attach() timed out before ever resolving
    a pipeline."""

    duration: float | None = None
    """Final pipeline elapsed seconds — same value as `pipeline_elapsed`
    at that point, kept as its own field since `duration` means something
    different on JobEvent."""

    reason: str
    """Why this result happened: "terminal" or "timeout"."""
