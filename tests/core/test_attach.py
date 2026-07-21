"""Tests for src/ddgl/core/attach.py."""
from __future__ import annotations

from ddgl.constants import JobStatus, PipelineStatus
from ddgl.core.attach import DurationEstimator, NullEstimator
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline

# ---------------------------------------------------------------------------
# NullEstimator
# ---------------------------------------------------------------------------


class TestNullEstimator:
    def test_returns_none(self) -> None:
        estimator = NullEstimator()
        pipeline = Pipeline(id=1, ref="main", status=PipelineStatus.RUNNING)
        jobs = [Job(id=1, name="build", stage="build", status=JobStatus.RUNNING)]
        assert estimator.estimate_remaining(pipeline, jobs) is None

    def test_satisfies_protocol(self) -> None:
        assert isinstance(NullEstimator(), DurationEstimator)
