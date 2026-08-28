# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

import msgspec

from ddgl.constants import DEFAULT_JOB_RETRY_ATTEMPTS, DEFAULT_JOB_RETRY_TOTAL


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
