# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/core/jobs.py."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import respx
from httpx import Response

from ddgl.cache.cache_config import CacheNS
from ddgl.client import GitLabClient
from ddgl.constants import JobStatus
from ddgl.core.jobs import filter_jobs, get_job, get_jobs, list_jobs
from ddgl.model.job import Job

from ._stubs import TEST_CONFIG, FakeCache

_PROJECT_ID = "grp/proj"


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=TEST_CONFIG.api_url) as router:
        yield router


@pytest.fixture()
async def client(mock_api: respx.MockRouter) -> GitLabClient:
    async with GitLabClient(TEST_CONFIG) as c:
        yield c


# ---------------------------------------------------------------------------
# API response helpers
# ---------------------------------------------------------------------------


def _job_payload(
    job_id: int,
    status: str = "success",
    name: str = "test-job",
    stage: str = "test",
    allow_failure: bool = False,
) -> dict[str, Any]:
    return {
        "id": job_id,
        "name": name,
        "stage": stage,
        "status": status,
        "ref": "main",
        "web_url": f"https://gitlab.example.com/grp/proj/-/jobs/{job_id}",
        "allow_failure": allow_failure,
        "failure_reason": None,
        "duration": 30.0,
        "created_at": "2024-01-01T00:00:00.000Z",
        "started_at": "2024-01-01T00:00:01.000Z",
        "finished_at": "2024-01-01T00:00:31.000Z",
    }


# ---------------------------------------------------------------------------
# get_job
# ---------------------------------------------------------------------------


class TestGetJob:
    async def test_fetches_from_api_on_cache_miss(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/jobs/10").mock(
            return_value=Response(200, json=_job_payload(10))
        )
        job = await get_job(client, 10)
        assert job.id == 10
        assert job.status == JobStatus.SUCCESS

    async def test_cache_hit_skips_api(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        cache = FakeCache()
        j = Job.from_api(_job_payload(99))
        cache[CacheNS.OBJECTS].set(("jobs", _PROJECT_ID, 99), j)

        job = await get_job(client, 99, cache=cache)
        assert job.id == 99
        assert mock_api.calls.call_count == 0

    async def test_terminal_job_written_to_cache(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/jobs/20").mock(
            return_value=Response(200, json=_job_payload(20, status="success"))
        )
        cache = FakeCache()
        await get_job(client, 20, cache=cache)
        assert cache[CacheNS.OBJECTS][("jobs", _PROJECT_ID, 20)] is not None

    async def test_running_job_not_cached(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/jobs/21").mock(
            return_value=Response(200, json=_job_payload(21, status="running"))
        )
        cache = FakeCache()
        await get_job(client, 21, cache=cache)
        assert cache[CacheNS.OBJECTS][("jobs", _PROJECT_ID, 21)] is None

    async def test_failed_job_written_to_cache(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/jobs/22").mock(
            return_value=Response(200, json=_job_payload(22, status="failed"))
        )
        cache = FakeCache()
        await get_job(client, 22, cache=cache)
        assert cache[CacheNS.OBJECTS][("jobs", _PROJECT_ID, 22)] is not None

    async def test_skipped_job_written_to_cache(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/jobs/23").mock(
            return_value=Response(200, json=_job_payload(23, status="skipped"))
        )
        cache = FakeCache()
        await get_job(client, 23, cache=cache)
        assert cache[CacheNS.OBJECTS][("jobs", _PROJECT_ID, 23)] is not None


# ---------------------------------------------------------------------------
# get_jobs
# ---------------------------------------------------------------------------


class TestGetJobs:
    async def test_empty_input(self, client: GitLabClient) -> None:
        result = await get_jobs(client, [])
        assert result == []

    async def test_fetches_misses_concurrently(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        for jid in (1, 2):
            mock_api.get(f"/projects/grp%2Fproj/jobs/{jid}").mock(
                return_value=Response(200, json=_job_payload(jid))
            )
        result = await get_jobs(client, [1, 2])
        assert {j.id for j in result} == {1, 2}

    async def test_cache_hits_skip_api(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        cache = FakeCache()
        for jid in (1, 2):
            cache[CacheNS.OBJECTS].set(
                ("jobs", _PROJECT_ID, jid),
                Job.from_api(_job_payload(jid)),
            )
        result = await get_jobs(client, [1, 2], cache=cache)
        assert mock_api.calls.call_count == 0
        assert {j.id for j in result} == {1, 2}

    async def test_preserves_input_order(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        for jid in (5, 3, 7):
            mock_api.get(f"/projects/grp%2Fproj/jobs/{jid}").mock(
                return_value=Response(200, json=_job_payload(jid))
            )
        result = await get_jobs(client, [5, 3, 7])
        assert [j.id for j in result] == [5, 3, 7]


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------


class TestListJobs:
    async def test_yields_jobs(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(200, json=[_job_payload(1), _job_payload(2)])
        )
        jobs = [j async for j in list_jobs(client, 100)]
        assert [j.id for j in jobs] == [1, 2]

    async def test_passes_scope_to_api(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(200, json=[_job_payload(3, status="failed")])
        )
        jobs = [j async for j in list_jobs(client, 100, scope=JobStatus.FAILED)]
        assert len(jobs) == 1
        assert route.calls.last.request.url.params.get("scope") == "failed"

    async def test_caches_terminal_jobs(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(200, json=[
                _job_payload(10, status="success"),
                _job_payload(11, status="running"),
            ])
        )
        cache = FakeCache()
        _ = [j async for j in list_jobs(client, 100, cache=cache)]
        assert cache[CacheNS.OBJECTS][("jobs", _PROJECT_ID, 10)] is not None
        assert cache[CacheNS.OBJECTS][("jobs", _PROJECT_ID, 11)] is None

    async def test_empty_pipeline_yields_nothing(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(200, json=[])
        )
        jobs = [j async for j in list_jobs(client, 100)]
        assert jobs == []


# ---------------------------------------------------------------------------
# filter_jobs
# ---------------------------------------------------------------------------


class TestFilterJobs:
    def _make_jobs(self) -> list[Job]:
        return [
            Job.from_api(_job_payload(1, status="failed", name="unit-test", stage="test")),
            Job.from_api(_job_payload(2, status="success", name="build-app", stage="build")),
            Job.from_api(_job_payload(3, status="failed", name="e2e-test", stage="test")),
            Job.from_api(_job_payload(4, status="running", name="deploy", stage="deploy")),
        ]

    def test_no_filter_returns_all(self) -> None:
        jobs = self._make_jobs()
        assert filter_jobs(jobs) == jobs

    def test_failed_only(self) -> None:
        jobs = self._make_jobs()
        result = filter_jobs(jobs, failed_only=True)
        assert [j.id for j in result] == [1, 3]

    def test_name_pattern_regex(self) -> None:
        jobs = self._make_jobs()
        result = filter_jobs(jobs, name_pattern=r"test$")
        assert [j.id for j in result] == [1, 3]

    def test_stage_filter(self) -> None:
        jobs = self._make_jobs()
        result = filter_jobs(jobs, stage="build")
        assert [j.id for j in result] == [2]

    def test_combined_filters(self) -> None:
        jobs = self._make_jobs()
        result = filter_jobs(jobs, failed_only=True, stage="test")
        assert [j.id for j in result] == [1, 3]

    def test_no_match_returns_empty(self) -> None:
        jobs = self._make_jobs()
        result = filter_jobs(jobs, stage="nonexistent")
        assert result == []

    def test_accepts_iterable(self) -> None:
        jobs = self._make_jobs()
        result = filter_jobs(iter(jobs), failed_only=True)
        assert len(result) == 2

    def test_failed_only_excludes_allowed_failures(self) -> None:
        jobs = self._make_jobs() + [
            Job.from_api(
                _job_payload(5, status="failed", name="flaky-test", stage="test", allow_failure=True)
            )
        ]
        result = filter_jobs(jobs, failed_only=True)
        assert [j.id for j in result] == [1, 3]

    def test_failed_only_with_include_allowed_failures_restores_old_behaviour(self) -> None:
        jobs = self._make_jobs() + [
            Job.from_api(
                _job_payload(5, status="failed", name="flaky-test", stage="test", allow_failure=True)
            )
        ]
        result = filter_jobs(jobs, failed_only=True, include_allowed_failures=True)
        assert [j.id for j in result] == [1, 3, 5]
