"""Tests for ddgl/tui/widgets/job_list.py."""
from __future__ import annotations

import pytest

from ddgl.constants import JobStatus
from ddgl.tui.widgets.job_list import (
    SortMode,
    _apply_filter,
    _apply_sort,
    _fmt_duration,
    _sort_alphabetical,
    _sort_by_stage,
    _sort_by_start_time,
)
from ddgl.tui.widgets.search_bar import FilterSpec

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
    assert [j.stage for j in result] == ["build", "build", "test"]


def test_sort_by_stage_then_alpha_within_stage() -> None:
    jobs = [
        make_job(id=1, name="zebra", stage="test"),
        make_job(id=2, name="alpha", stage="test"),
        make_job(id=3, name="mango", stage="test"),
    ]
    result = _sort_by_stage(jobs)
    assert [j.name for j in result] == ["alpha", "mango", "zebra"]


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


# ---------------------------------------------------------------------------
# _sort_alphabetical
# ---------------------------------------------------------------------------


def test_sort_alphabetical_orders_by_name() -> None:
    jobs = [make_job(name="z"), make_job(name="a"), make_job(name="m")]
    result = _sort_alphabetical(jobs)
    assert [j.name for j in result] == ["a", "m", "z"]


def test_sort_alphabetical_ignores_stage() -> None:
    jobs = [
        make_job(name="z-job", stage="a-stage"),
        make_job(name="a-job", stage="z-stage"),
    ]
    result = _sort_alphabetical(jobs)
    assert result[0].name == "a-job"


# ---------------------------------------------------------------------------
# _sort_by_start_time
# ---------------------------------------------------------------------------


def test_sort_by_start_time_orders_chronologically() -> None:
    jobs = [
        make_job(id=1, name="c", started_at="2024-01-01T12:00:00"),
        make_job(id=2, name="a", started_at="2024-01-01T10:00:00"),
        make_job(id=3, name="b", started_at="2024-01-01T11:00:00"),
    ]
    result = _sort_by_start_time(jobs)
    assert [j.name for j in result] == ["a", "b", "c"]


def test_sort_by_start_time_none_sorts_first() -> None:
    jobs = [
        make_job(id=1, name="z", started_at="2024-01-01T10:00:00"),
        make_job(id=2, name="a", started_at=None),
    ]
    result = _sort_by_start_time(jobs)
    assert result[0].name == "a"


# ---------------------------------------------------------------------------
# SortMode
# ---------------------------------------------------------------------------


def test_sort_mode_next_cycles() -> None:
    assert SortMode.STAGE.next() == SortMode.ALPHABETICAL
    assert SortMode.ALPHABETICAL.next() == SortMode.START_TIME
    assert SortMode.START_TIME.next() == SortMode.STAGE


def test_sort_mode_label_nonempty() -> None:
    for mode in SortMode:
        assert mode.label()


# ---------------------------------------------------------------------------
# _apply_sort dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,expected_first_name",
    [
        (SortMode.STAGE, "alpha"),       # stage "a" comes first, then alpha
        (SortMode.ALPHABETICAL, "alpha"),
        (SortMode.START_TIME, "beta"),   # beta has earlier start_at
    ],
)
def test_apply_sort_dispatch(mode: SortMode, expected_first_name: str) -> None:
    jobs = [
        make_job(name="alpha", stage="a", started_at="2024-01-01T12:00:00"),
        make_job(name="beta", stage="b", started_at="2024-01-01T10:00:00"),
    ]
    result = _apply_sort(jobs, mode)
    assert result[0].name == expected_first_name


# ---------------------------------------------------------------------------
# _apply_filter
# ---------------------------------------------------------------------------


def test_apply_filter_empty_spec_returns_all() -> None:
    jobs = [
        make_job(id=1, name="lint", stage="test", status=JobStatus.SUCCESS),
        make_job(id=2, name="build", stage="build", status=JobStatus.FAILED),
    ]
    assert _apply_filter(jobs, FilterSpec()) == jobs


def test_apply_filter_by_status() -> None:
    jobs = [
        make_job(id=1, status=JobStatus.SUCCESS),
        make_job(id=2, status=JobStatus.FAILED),
    ]
    result = _apply_filter(jobs, FilterSpec(statuses={"failed"}))
    assert [j.id for j in result] == [2]


def test_apply_filter_by_stage() -> None:
    jobs = [
        make_job(id=1, stage="build"),
        make_job(id=2, stage="test"),
    ]
    result = _apply_filter(jobs, FilterSpec(stages={"test"}))
    assert [j.id for j in result] == [2]


def test_apply_filter_by_text() -> None:
    jobs = [
        make_job(id=1, name="build-app"),
        make_job(id=2, name="unit-test"),
    ]
    result = _apply_filter(jobs, FilterSpec(text="bld"))
    assert [j.id for j in result] == [1]


def test_apply_filter_combined_predicates_are_anded() -> None:
    jobs = [
        make_job(id=1, name="lint", stage="test", status=JobStatus.FAILED),
        make_job(id=2, name="build", stage="build", status=JobStatus.FAILED),
        make_job(id=3, name="deploy", stage="test", status=JobStatus.SUCCESS),
    ]
    # status:failed AND stage:test → only job 1
    spec = FilterSpec(statuses={"failed"}, stages={"test"})
    result = _apply_filter(jobs, spec)
    assert [j.id for j in result] == [1]


def test_apply_filter_multi_status() -> None:
    jobs = [
        make_job(id=1, status=JobStatus.SUCCESS),
        make_job(id=2, status=JobStatus.FAILED),
        make_job(id=3, status=JobStatus.RUNNING),
    ]
    spec = FilterSpec(statuses={"failed", "running"})
    result = _apply_filter(jobs, spec)
    assert {j.id for j in result} == {2, 3}


def test_apply_filter_stage_case_insensitive() -> None:
    jobs = [make_job(id=1, stage="Build")]
    result = _apply_filter(jobs, FilterSpec(stages={"build"}))
    assert len(result) == 1


def test_apply_filter_empty_jobs() -> None:
    assert _apply_filter([], FilterSpec(statuses={"failed"})) == []
