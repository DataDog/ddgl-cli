# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/core/retry.py."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import respx
from httpx import Response

from ddgl.client import GitLabClient
from ddgl.core.retry import count_attempts, retry_job, retry_jobs, retry_pipeline
from ddgl.model.job import Job

from ._stubs import TEST_CONFIG

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
    status: str = "pending",
    name: str = "test-job",
    stage: str = "test",
) -> dict[str, Any]:
    return {
        "id": job_id,
        "name": name,
        "stage": stage,
        "status": status,
        "ref": "main",
    }


def _pipeline_payload(pipeline_id: int, status: str = "running") -> dict[str, Any]:
    return {
        "id": pipeline_id,
        "ref": "main",
        "status": status,
        "sha": "abc123",
    }


def _make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = {
        "id": 1, "name": "build", "stage": "build", "status": "failed",
    }
    return Job(**(defaults | overrides))


# ---------------------------------------------------------------------------
# retry_job
# ---------------------------------------------------------------------------


class TestRetryJob:
    async def test_returns_new_job(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.post("/projects/grp%2Fproj/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(501))
        )
        job = await retry_job(client, 1)
        assert job.id == 501


# ---------------------------------------------------------------------------
# retry_pipeline
# ---------------------------------------------------------------------------


class TestRetryPipeline:
    async def test_returns_pipeline(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.post("/projects/grp%2Fproj/pipelines/100/retry").mock(
            return_value=Response(200, json=_pipeline_payload(100))
        )
        pipeline = await retry_pipeline(client, 100)
        assert pipeline.id == 100
        assert pipeline.status == "running"


# ---------------------------------------------------------------------------
# retry_jobs
# ---------------------------------------------------------------------------


class TestRetryJobs:
    async def test_all_succeed(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.post("/projects/grp%2Fproj/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(101, name="unit-tests"))
        )
        mock_api.post("/projects/grp%2Fproj/jobs/2/retry").mock(
            return_value=Response(200, json=_job_payload(102, name="lint"))
        )
        jobs = [_make_job(id=1, name="unit-tests"), _make_job(id=2, name="lint")]

        outcomes = await retry_jobs(client, jobs)

        assert len(outcomes) == 2
        by_name = {o.job_name: o for o in outcomes}
        assert by_name["unit-tests"].new_job is not None
        assert by_name["unit-tests"].new_job.id == 101
        assert by_name["unit-tests"].error is None
        assert by_name["lint"].new_job.id == 102

    async def test_partial_failure_reported_per_job(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.post("/projects/grp%2Fproj/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(101, name="unit-tests"))
        )
        mock_api.post("/projects/grp%2Fproj/jobs/2/retry").mock(
            return_value=Response(403, json={"message": "403 Forbidden"})
        )
        jobs = [_make_job(id=1, name="unit-tests"), _make_job(id=2, name="lint")]

        outcomes = await retry_jobs(client, jobs)

        by_name = {o.job_name: o for o in outcomes}
        assert by_name["unit-tests"].new_job is not None
        assert by_name["unit-tests"].error is None
        assert by_name["lint"].new_job is None
        assert by_name["lint"].error is not None
        assert by_name["lint"].old_job_id == 2

    async def test_empty_input_returns_empty(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        assert await retry_jobs(client, []) == []


# ---------------------------------------------------------------------------
# count_attempts
# ---------------------------------------------------------------------------


class TestCountAttempts:
    async def test_counts_records_per_name(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(
                200,
                json=[
                    _job_payload(1, name="unit-tests", status="failed"),
                    _job_payload(2, name="unit-tests", status="failed"),
                    _job_payload(3, name="unit-tests", status="success"),
                    _job_payload(4, name="lint", status="success"),
                ],
                headers={"x-total-pages": "1"},
            )
        )

        counts = await count_attempts(client, 100, {"unit-tests", "lint"})

        assert counts == {"unit-tests": 3, "lint": 1}

    async def test_restricted_to_requested_names(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(
                200,
                json=[
                    _job_payload(1, name="unit-tests", status="failed"),
                    _job_payload(2, name="lint", status="success"),
                ],
                headers={"x-total-pages": "1"},
            )
        )

        counts = await count_attempts(client, 100, {"unit-tests"})

        assert counts == {"unit-tests": 1}

    async def test_uses_include_retried(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(
                200, json=[], headers={"x-total-pages": "1"},
            )
        )

        await count_attempts(client, 100, {"unit-tests"})

        assert route.calls[0].request.url.params["include_retried"] == "true"
