from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ddgl.model.job import Job
    from ddgl.model.pipeline import Pipeline

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
