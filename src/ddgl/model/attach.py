# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

import msgspec

from ddgl.constants import DEFAULT_JOB_RETRY_ATTEMPTS, DEFAULT_JOB_RETRY_TOTAL
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline


def now_iso() -> str:
    """The current UTC time, as the ISO string every event's `ts` uses."""
    return datetime.now(UTC).isoformat()


class DetailLevel(StrEnum):
    """How much of attach()'s event stream a renderer shows, ordered from
    least to most verbose (NONE < MINIMAL < NORMAL < FULL — comparable
    with </<=/>/>=).

    none=final state only, minimal=summaries only, normal=summaries + job
    transitions to terminal states, full=everything. See render/attach.py's
    _visible_at and _live_markup for how each level is applied.
    """

    NONE = "none"
    MINIMAL = "minimal"
    NORMAL = "normal"
    FULL = "full"

    def _rank(self) -> int:
        return _DETAIL_RANK[self]

    # StrEnum already inherits str's (lexicographic) rich comparisons, so
    # all four must be defined explicitly here — functools.total_ordering
    # would see them already present on the class and skip synthesizing
    # from just __lt__, silently leaving the wrong (lexicographic) ones.
    def __lt__(self, other: object) -> bool:
        if self.__class__ is not other.__class__:
            return NotImplemented
        return self._rank() < other._rank()

    def __le__(self, other: object) -> bool:
        if self.__class__ is not other.__class__:
            return NotImplemented
        return self._rank() <= other._rank()

    def __gt__(self, other: object) -> bool:
        if self.__class__ is not other.__class__:
            return NotImplemented
        return self._rank() > other._rank()

    def __ge__(self, other: object) -> bool:
        if self.__class__ is not other.__class__:
            return NotImplemented
        return self._rank() >= other._rank()


_DETAIL_RANK = {level: i for i, level in enumerate(DetailLevel)}


class PipelineState(msgspec.Struct):
    """A pipeline and the jobs belonging to it, as of one point in time.

    What `ddgl attach` reads on each poll tick, and what it compares
    against the previous tick to work out which events to emit.
    """

    pipeline: Pipeline
    jobs: list[Job]

    @property
    def jobs_done(self) -> int:
        return sum(1 for job in self.jobs if job.is_terminal)

    @property
    def failed_job_names(self) -> tuple[str, ...]:
        """Names of jobs that failed in a way that fails the pipeline.

        Excludes allowed failures, matching the verdict GitLab itself
        reports for the pipeline (see `Job.is_blocking`).
        """
        return tuple(job.name for job in self.jobs if job.is_blocking)

    @property
    def current_stage(self) -> str | None:
        """Best-effort 'what stage are we in' for the live view's headline.

        The OLDEST stage that still has at least one not-yet-done job —
        the stage actually holding up progress, not the most-recently-
        started one. "Oldest" is approximated by each stage's minimum job
        ID: GitLab returns jobs newest-ID-first (not in stage order —
        there is no API field for stage sequence), but job IDs are
        assigned in roughly creation order, and jobs are normally created
        stage-by-stage at pipeline start. Falls back to the oldest stage
        overall once everything is done, or None with no jobs.
        """
        if not self.jobs:
            return None

        min_id_by_stage: dict[str, int] = {}
        incomplete_stages: set[str] = set()
        for job in self.jobs:
            min_id_by_stage[job.stage] = min(
                min_id_by_stage.get(job.stage, job.id), job.id
            )
            if not job.is_terminal:
                incomplete_stages.add(job.stage)

        candidates = incomplete_stages or min_id_by_stage.keys()
        return min(candidates, key=lambda stage: min_id_by_stage[stage])


class EventContext(msgspec.Struct, frozen=True):
    """The rollup fields carried by every `AttachEvent`.

    These mirror `AttachEvent`'s own non-`ts` base fields exactly — they
    are computed once per tick and spread into each event emitted for it,
    so a renderer can read them off any event without tracking state
    across the stream. `tests/test_models.py` asserts the two field sets
    stay identical.
    """

    pipeline_id: int | None = None
    ref: str | None = None
    current_stage: str | None = None
    pipeline_elapsed: float | None = None
    jobs_total: int | None = None
    jobs_done: int | None = None
    failed_jobs: tuple[str, ...] = ()
    eta_seconds: float | None = None

    @classmethod
    def from_state(cls, state: PipelineState) -> EventContext:
        """The rollup for every event emitted about `state`."""
        elapsed = state.pipeline.elapsed
        return cls(
            pipeline_id=state.pipeline.id,
            ref=state.pipeline.ref,
            current_stage=state.current_stage,
            pipeline_elapsed=elapsed.total_seconds() if elapsed is not None else None,
            jobs_total=len(state.jobs),
            jobs_done=state.jobs_done,
            failed_jobs=state.failed_job_names,
            eta_seconds=None,  # no ETA estimation in v1
        )

    def as_fields(self) -> dict[str, Any]:
        """Keyword arguments for constructing an `AttachEvent`."""
        return msgspec.structs.asdict(self)


class RetryPolicy(msgspec.Struct, frozen=True):
    """Auto-retry configuration for one `ddgl attach` run.

    Populated from the `--retry`/`--retry-attempts`/`--retry-total`/
    `--retry-exclude` CLI flags (cli/attach.py) and consumed by the poll
    loop's `_apply_retry_policy` (core/attach.py).
    """

    enabled: bool = False
    attempts_per_job: int = DEFAULT_JOB_RETRY_ATTEMPTS  # 0 = unlimited
    total: int = DEFAULT_JOB_RETRY_TOTAL                # 0 = unlimited
    exclude: tuple[str, ...] = ()  # job-name regexes never auto-retried


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

    _min_detail: ClassVar[DetailLevel] = DetailLevel.FULL

    @property
    def min_detail_level(self) -> DetailLevel:
        """The lowest --detail level at which a renderer should show this
        event (see render/attach.py's _visible_at)."""
        return self._min_detail


_TERMINAL_JOB_STATUSES = frozenset({"success", "failed", "canceled", "skipped"})


class SnapshotEvent(AttachEvent, kw_only=True, tag="snapshot"):
    """attach()'s initial and post-job-load snapshots."""

    _min_detail: ClassVar[DetailLevel] = DetailLevel.MINIMAL

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

    @property
    def min_detail_level(self) -> DetailLevel:
        # Job transitions are where nearly all the noise lives on a large
        # pipeline (hundreds of created→running/running→pending blips), so
        # only ones reaching a terminal status show below --detail full.
        return DetailLevel.NORMAL if self.status in _TERMINAL_JOB_STATUSES else DetailLevel.FULL


class PipelineEvent(AttachEvent, kw_only=True, tag="pipeline"):
    """The pipeline itself transitioned to a new status."""

    _min_detail: ClassVar[DetailLevel] = DetailLevel.NORMAL

    status: str
    """The new status."""

    old_status: str
    """Status before this transition."""


class PollEvent(AttachEvent, kw_only=True, tag="poll"):
    """The post-poll rollup summary emitted once after a changed tick."""

    _min_detail: ClassVar[DetailLevel] = DetailLevel.MINIMAL


class HeartbeatEvent(AttachEvent, kw_only=True, tag="heartbeat"):
    """A tally line emitted on a quiet poll tick (--heartbeat only)."""

    _min_detail: ClassVar[DetailLevel] = DetailLevel.MINIMAL


class SwitchedEvent(AttachEvent, kw_only=True, tag="switched"):
    """A --follow rebind to a newer pipeline."""

    _min_detail: ClassVar[DetailLevel] = DetailLevel.NORMAL

    message: str
    """Human-readable description of the switch."""


class RetryEvent(AttachEvent, kw_only=True, tag="retry"):
    """A failed or canceled job was auto-retried."""

    _min_detail: ClassVar[DetailLevel] = DetailLevel.MINIMAL  # rare + high-signal

    job_id: int
    """The OLD job's GitLab ID — the one that failed and was retried, not
    the new one GitLab minted."""

    job_name: str
    """The job's name, stable across the retry (unlike its ID)."""

    job_stage: str
    """That job's own stage."""

    new_job_id: int
    """The ID of the job GitLab created for this attempt."""

    attempt: int
    """This job name's attempt number after the retry (2 for a job
    retried once, whether that's its first auto-retry or it already had
    a manual one)."""


class ResultEvent(AttachEvent, kw_only=True, tag="result"):
    """The final event of an attach() run."""

    _min_detail: ClassVar[DetailLevel] = DetailLevel.NONE

    status: str | None = None
    """The final status. None if attach() timed out before ever resolving
    a pipeline."""

    duration: float | None = None
    """Final pipeline elapsed seconds — same value as `pipeline_elapsed`
    at that point, kept as its own field since `duration` means something
    different on JobEvent."""

    reason: str
    """Why this result happened: "terminal" or "timeout"."""

    retries: int = 0
    """Jobs successfully auto-retried over the course of the run. 0 when
    --retry wasn't used."""

    @classmethod
    def terminal(cls, state: PipelineState, context: EventContext) -> ResultEvent:
        """The result for a pipeline that reached a terminal status."""
        elapsed = state.pipeline.elapsed
        return cls(
            ts=now_iso(),
            status=str(state.pipeline.status),
            duration=elapsed.total_seconds() if elapsed is not None else None,
            reason="terminal",
            **context.as_fields(),
        )

    @classmethod
    def timed_out(cls, last_event: AttachEvent | None) -> ResultEvent:
        """The result for a run that hit its timeout, carrying whatever
        state the last emitted event knew.

        `last_event` is None when the timeout elapsed before any event was
        emitted — i.e. while still waiting for a pipeline to exist — so
        there is no pipeline to report.
        """
        if last_event is None:
            return cls(ts=now_iso(), reason="timeout")
        return cls(
            ts=now_iso(),
            pipeline_id=last_event.pipeline_id,
            ref=last_event.ref,
            current_stage=last_event.current_stage,
            pipeline_elapsed=last_event.pipeline_elapsed,
            # Not every event kind carries a status; those that don't leave
            # the result's status None rather than inventing one.
            status=getattr(last_event, "status", None),
            duration=last_event.pipeline_elapsed,
            jobs_total=last_event.jobs_total,
            jobs_done=last_event.jobs_done,
            failed_jobs=last_event.failed_jobs,
            eta_seconds=last_event.eta_seconds,
            reason="timeout",
        )
