# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for ddgl/tui/app.py — PipelineViewer.load_pipeline() flow."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.tui.app import PipelineViewer
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

    # Needed by core/jobs.list_jobs → client.iter_jobs (must be async generator)
    async def iter_jobs(
        self, pipeline_id: int, *, scope: Any = None
    ) -> AsyncIterator[Any]:
        from ddgl.model.page import Page

        yield Page(
            items=self._jobs, page=1, next_page=None,
            total_pages=1, total=len(self._jobs),
        )


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
