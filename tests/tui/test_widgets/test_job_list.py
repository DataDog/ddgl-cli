"""Tests for ddgl/tui/widgets/job_list.py."""
from __future__ import annotations

from ddgl.constants import JobStatus
from ddgl.tui.widgets.job_list import _fmt_duration, _sort_by_stage

from .._stubs import make_job

# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------


def test_fmt_duration_none() -> None:
    assert _fmt_duration(None) == "—"


def test_fmt_duration_zero() -> None:
    assert _fmt_duration(0) == "0m 0s"


def test_fmt_duration_seconds_only() -> None:
    assert _fmt_duration(45) == "0m 45s"


def test_fmt_duration_minutes_and_seconds() -> None:
    assert _fmt_duration(125) == "2m 5s"


def test_fmt_duration_exact_minute() -> None:
    assert _fmt_duration(60) == "1m 0s"


# ---------------------------------------------------------------------------
# _sort_by_stage
# ---------------------------------------------------------------------------


def test_sort_by_stage_groups_by_stage() -> None:
    jobs = [
        make_job(id=1, name="z-job", stage="build"),
        make_job(id=2, name="a-job", stage="test"),
        make_job(id=3, name="m-job", stage="build"),
    ]
    result = _sort_by_stage(jobs)
    stages = [j.stage for j in result]
    assert stages == ["build", "build", "test"]


def test_sort_by_stage_then_alpha_within_stage() -> None:
    jobs = [
        make_job(id=1, name="zebra", stage="test"),
        make_job(id=2, name="alpha", stage="test"),
        make_job(id=3, name="mango", stage="test"),
    ]
    result = _sort_by_stage(jobs)
    names = [j.name for j in result]
    assert names == ["alpha", "mango", "zebra"]


def test_sort_by_stage_does_not_mutate_input() -> None:
    jobs = [
        make_job(id=1, name="b", stage="z-stage"),
        make_job(id=2, name="a", stage="a-stage"),
    ]
    original_order = [j.id for j in jobs]
    _sort_by_stage(jobs)
    assert [j.id for j in jobs] == original_order


def test_sort_by_stage_empty() -> None:
    assert _sort_by_stage([]) == []


def test_sort_by_stage_preserves_all_statuses() -> None:
    statuses = [JobStatus.FAILED, JobStatus.SUCCESS, JobStatus.RUNNING]
    jobs = [make_job(id=i, status=s, stage="s") for i, s in enumerate(statuses)]
    result = _sort_by_stage(jobs)
    assert len(result) == 3
