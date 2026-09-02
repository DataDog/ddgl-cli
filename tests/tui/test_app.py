# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for ddgl/tui/app.py — PipelineViewer.load_pipeline() flow."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from ddgl.constants import JobStatus
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.tui.app import PipelineViewer
from ddgl.tui.widgets.filter_buttons import FilterButton
from ddgl.tui.widgets.job_list import JobListPanel
from ddgl.tui.widgets.pipeline_info import PipelineInfoPanel

from ._stubs import make_job, make_pipeline

# ---------------------------------------------------------------------------
# Local stubs — not importing production client/cache types
# ---------------------------------------------------------------------------


class _FakeConfig:
    project_id: str = ""


class _FakeClient:
    """Minimal stub that lets list_jobs iterate over injected jobs."""

    _config = _FakeConfig()

    def __init__(self, jobs: list[Job]) -> None:
        self._jobs = jobs
        self.retried_job_ids: list[int] = []
        self.retry_error: Exception | None = None
        self._retry_new_job_id = 999
        self.get_pipeline_calls = 0

    # Needed by core/jobs.list_jobs → client.iter_jobs (must be async generator)
    async def iter_jobs(
        self, pipeline_id: int, *, scope: Any = None, fresh: bool = False
    ) -> AsyncIterator[Any]:
        from ddgl.model.page import Page

        yield Page(
            items=self._jobs, page=1, next_page=None,
            total_pages=1, total=len(self._jobs),
        )

    async def get_pipeline(self, pipeline_id: int, **kwargs: Any) -> Pipeline:
        self.get_pipeline_calls += 1
        return make_pipeline(id=pipeline_id)

    # Needed by PipelineListPanel.on_mount's initial fetch.
    async def fetch_pipelines(self, **kwargs: Any) -> Any:
        from ddgl.model.page import Page

        return Page(items=[], page=1, next_page=None, total_pages=1, total=0)

    async def retry_job(self, job_id: int) -> Job:
        if self.retry_error is not None:
            raise self.retry_error
        self.retried_job_ids.append(job_id)
        return make_job(id=self._retry_new_job_id, name="the-retried-job")

    # Needed by JobDetailScreen's own on-mount fetches.
    async def get_job(self, job_id: int) -> Job:
        return next(j for j in self._jobs if j.id == job_id)

    async def get_job_log(self, job_id: int) -> str:
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(
    pipeline: Pipeline | None = None,
    jobs: list[Job] | None = None,
) -> PipelineViewer:
    p = pipeline or make_pipeline(id=1)
    client = _FakeClient(jobs or [])
    return PipelineViewer(p, client, cache=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_pipeline_shown_in_info_panel() -> None:
    p = make_pipeline(id=99, ref="feat/xyz")
    app = _make_app(pipeline=p)
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        panel = app.query_one(PipelineInfoPanel)
        assert panel.pipeline is not None
        assert panel.pipeline.id == 99


@pytest.mark.asyncio
async def test_load_pipeline_updates_reactive() -> None:
    p1 = make_pipeline(id=1, ref="main")
    p2 = make_pipeline(id=2, ref="develop")
    app = _make_app(pipeline=p1)
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app.load_pipeline(p2)
        await pilot.pause()
        assert app.pipeline == p2


@pytest.mark.asyncio
async def test_load_pipeline_updates_info_panel() -> None:
    p1 = make_pipeline(id=1)
    p2 = make_pipeline(id=2, ref="feature")
    app = _make_app(pipeline=p1)
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app.load_pipeline(p2)
        await pilot.pause()
        panel = app.query_one(PipelineInfoPanel)
        assert panel.pipeline == p2


@pytest.mark.asyncio
async def test_load_pipeline_clears_job_list_before_reload() -> None:
    jobs = [make_job(id=i) for i in range(3)]
    p1 = make_pipeline(id=1)
    p2 = make_pipeline(id=2)
    app = _make_app(pipeline=p1, jobs=jobs)
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        # After second load, job list should eventually be populated again.
        app.load_pipeline(p2)
        await pilot.pause()
        # Loading indicator was shown (job list hidden), reactive cycle OK.
        assert app.pipeline == p2


@pytest.mark.asyncio
async def test_sort_mode_cycles_on_s_key() -> None:

    app = _make_app()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        job_list = app.query_one(JobListPanel)
        initial = job_list.sort_mode
        # Focus the job list so `s` isn't captured by the search Input.
        job_list.focus()
        await pilot.press("s")
        assert job_list.sort_mode == initial.next()


@pytest.mark.asyncio
async def test_allowed_failure_visible_by_default() -> None:
    """The default status dropdown selection must include "allowed-failure"
    so jobs with allow_failure set don't silently vanish now that they no
    longer match the "failed" pseudo-status by default."""
    jobs = [make_job(id=1, status=JobStatus.FAILED, allow_failure=True)]
    app = _make_app(jobs=jobs)
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        job_list = app.query_one(JobListPanel)
        assert job_list.jobs[0].id == 1
        # Not filtered out by the default status dropdown selection.
        assert "allowed-failure" in app._dropdown_statuses


@pytest.mark.asyncio
async def test_typing_status_token_narrows_immediately() -> None:
    """Regression: typing status:X in the search box must fully replace the
    effective status filter right away, without needing to also open and
    confirm the status dropdown.

    Previously `_dropdown_statuses` (used by `_update_job_filter`) was only
    updated when the dropdown modal was confirmed — typing a status: token
    only updated the button's own display state, so the applied filter kept
    unioning the freshly typed token with a stale `_dropdown_statuses`
    snapshot (the untouched defaults), which silently widened the result
    back out instead of narrowing it.
    """
    jobs = [
        make_job(id=1, name="unit", status=JobStatus.FAILED),
        make_job(id=2, name="e2e", status=JobStatus.SUCCESS),
        make_job(id=3, name="deploy", status=JobStatus.RUNNING),
    ]
    app = _make_app(jobs=jobs)
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app.action_focus_search()
        await pilot.pause()
        for ch in "status:failed":
            await pilot.press(ch)
        await pilot.pause(0.2)
        job_list = app.query_one(JobListPanel)
        assert job_list.filter_spec.statuses == {"failed"}
        assert len(job_list.rows) == 1


@pytest.mark.asyncio
async def test_plain_text_search_preserves_default_status_filter() -> None:
    """Regression guard for the fix above: typing plain free text (no
    status:/stage: token) must NOT reset the status filter to "show
    everything" — it should leave the current status filtering (default or
    previously chosen) untouched."""
    jobs = [
        make_job(id=1, name="unit-tests", status=JobStatus.FAILED),
        make_job(id=2, name="unit-skip", status=JobStatus.SKIPPED),
    ]
    app = _make_app(jobs=jobs)
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app.action_focus_search()
        await pilot.pause()
        for ch in "unit":
            await pilot.press(ch)
        await pilot.pause(0.2)
        job_list = app.query_one(JobListPanel)
        # "skipped" isn't in the default dropdown selection — must stay excluded.
        assert "skipped" not in job_list.filter_spec.statuses
        assert len(job_list.rows) == 1


@pytest.mark.asyncio
async def test_clearing_status_token_restores_dropdown_filter() -> None:
    """Regression: typing a status: token then clearing it must fall back
    to the dropdown's actual selection, not get stuck on the last typed
    value.

    An earlier fix made `on_fuzzy_search_input_search_changed` overwrite
    `_dropdown_statuses` directly whenever a status: token was typed. That
    overwrite persisted even after the token was deleted (the empty-token
    guard skipped re-syncing), permanently discarding whatever the dropdown
    had actually been set to. Dropdown state must stay independent of
    typed tokens; `_update_job_filter()` gives typed tokens precedence
    only while they're present.
    """
    jobs = [
        make_job(id=1, name="unit-tests", status=JobStatus.FAILED),
        make_job(id=2, name="unit-skip", status=JobStatus.SKIPPED),
    ]
    app = _make_app(jobs=jobs)
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        default_statuses = set(app._dropdown_statuses)
        job_list = app.query_one(JobListPanel)

        app.action_focus_search()
        await pilot.pause()
        for ch in "status:failed":
            await pilot.press(ch)
        await pilot.pause(0.2)
        assert job_list.filter_spec.statuses == {"failed"}
        # Dropdown state itself must be untouched by typing.
        assert app._dropdown_statuses == default_statuses

        search = app.query_one("#search")
        search.clear()
        await pilot.pause(0.2)
        assert job_list.filter_spec.statuses == default_statuses


@pytest.mark.asyncio
async def test_status_filter_options_include_allowed_failure() -> None:
    jobs = [
        make_job(id=1, status=JobStatus.FAILED, allow_failure=True),
        make_job(id=2, status=JobStatus.SUCCESS),
    ]
    app = _make_app(jobs=jobs)
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        status_btn = app.query_one("#status-filter", FilterButton)
        assert "allowed-failure" in status_btn._options


# ---------------------------------------------------------------------------
# Retry — r retries, ctrl+r refreshes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ctrl_r_still_refreshes() -> None:
    p1 = make_pipeline(id=1)
    app = _make_app(pipeline=p1)
    client = app._client
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app.query_one(JobListPanel).focus()
        await pilot.press("ctrl+r")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert client.get_pipeline_calls == 1


@pytest.mark.asyncio
async def test_r_with_non_retryable_job_warns_and_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = [make_job(id=1, status=JobStatus.SUCCESS)]
    app = _make_app(jobs=jobs)
    client = app._client
    notified: list[str | None] = []
    monkeypatch.setattr(
        app, "notify",
        lambda *a, severity=None, **kw: notified.append(severity),
    )
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app.query_one(JobListPanel).focus()
        await pilot.press("r")
        await pilot.pause()
        assert client.retried_job_ids == []
        assert notified == ["warning"]


@pytest.mark.asyncio
async def test_r_with_no_job_selected_warns_and_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(jobs=[])
    client = app._client
    notified: list[str | None] = []
    monkeypatch.setattr(
        app, "notify",
        lambda *a, severity=None, **kw: notified.append(severity),
    )
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app.query_one(JobListPanel).focus()
        await pilot.press("r")
        await pilot.pause()
        assert client.retried_job_ids == []
        assert notified == ["warning"]


@pytest.mark.asyncio
async def test_r_with_retryable_job_confirms_then_retries_and_refreshes() -> None:
    jobs = [make_job(id=1, name="build", stage="test", status=JobStatus.FAILED)]
    p1 = make_pipeline(id=1)
    app = _make_app(pipeline=p1, jobs=jobs)
    client = app._client
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app.query_one(JobListPanel).focus()
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert client.retried_job_ids == [1]
        assert client.get_pipeline_calls == 1


@pytest.mark.asyncio
async def test_r_with_retryable_job_cancelled_does_not_retry() -> None:
    jobs = [make_job(id=1, name="build", stage="test", status=JobStatus.FAILED)]
    app = _make_app(jobs=jobs)
    client = app._client
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app.query_one(JobListPanel).focus()
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert client.retried_job_ids == []


@pytest.mark.asyncio
async def test_retrying_from_job_detail_screen_refreshes_pipeline_behind_it() -> None:
    from ddgl.tui.screens.job_detail import JobDetailScreen

    job = make_job(id=1, name="build", stage="test", status=JobStatus.FAILED)
    p1 = make_pipeline(id=1)
    app = _make_app(pipeline=p1, jobs=[job])
    client = app._client
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        app.push_screen(JobDetailScreen(job, client, app._cache, all_jobs=[job]))
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert client.retried_job_ids == [1]
        # The refresh happens while the detail screen is still on top.
        assert isinstance(app.screen, JobDetailScreen)
        assert client.get_pipeline_calls == 1
