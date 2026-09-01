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
from ddgl.core.retry import (
    retry_job,
    retry_jobs,
    retry_pipeline,
    select_by_id,
    select_in_pipeline,
    tally_attempts,
)
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline

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
    *,
    allow_failure: bool = False,
) -> dict[str, Any]:
    return {
        "id": job_id,
        "name": name,
        "stage": stage,
        "status": status,
        "ref": "main",
        "allow_failure": allow_failure,
        "pipeline": {"id": 100},
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
        "pipeline_id": 100,
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


class TestTallyAttempts:
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

        tally = await tally_attempts(client, 100, {"unit-tests", "lint"})

        assert tally.count("unit-tests") == 3
        assert tally.count("lint") == 1
        assert tally.count("never-ran") == 0

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

        tally = await tally_attempts(client, 100, {"unit-tests"})

        assert tally.count("unit-tests") == 1
        assert tally.count("lint") == 0

    async def test_uses_include_retried(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(
                200, json=[], headers={"x-total-pages": "1"},
            )
        )

        await tally_attempts(client, 100, {"unit-tests"})

        assert route.calls[0].request.url.params["include_retried"] == "true"


# ---------------------------------------------------------------------------
# select_by_id / select_in_pipeline — which jobs a retry will act on
# ---------------------------------------------------------------------------


def _pipeline(pipeline_id: int = 100, ref: str = "main") -> Pipeline:
    return Pipeline(id=pipeline_id, ref=ref, status="failed", sha="abc")


class TestSelectByID:
    async def test_keeps_only_retryable_jobs(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/jobs/1").mock(
            return_value=Response(200, json=_job_payload(1, name="a", status="failed"))
        )
        mock_api.get("/projects/grp%2Fproj/jobs/2").mock(
            return_value=Response(200, json=_job_payload(2, name="b", status="success"))
        )

        selection = await select_by_id(client, [1, 2])

        assert [j.id for j in selection.jobs] == [1]
        assert selection.matched == 2

    async def test_force_keeps_everything(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/jobs/1").mock(
            return_value=Response(200, json=_job_payload(1, name="a", status="success"))
        )

        selection = await select_by_id(client, [1], force=True)

        assert [j.id for j in selection.jobs] == [1]

    async def test_derives_the_pipeline_from_the_jobs(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/jobs/1").mock(
            return_value=Response(200, json=_job_payload(1, name="a", status="failed"))
        )

        selection = await select_by_id(client, [1])

        assert (selection.pipeline_id, selection.ref) == (100, "main")

    async def test_no_pipeline_when_jobs_span_pipelines(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        """Naming one pipeline would imply the other job belongs to it."""
        mock_api.get("/projects/grp%2Fproj/jobs/1").mock(
            return_value=Response(200, json=_job_payload(1, name="a", status="failed"))
        )
        two = _job_payload(2, name="b", status="failed")
        two["pipeline"] = {"id": 999}
        mock_api.get("/projects/grp%2Fproj/jobs/2").mock(return_value=Response(200, json=two))

        selection = await select_by_id(client, [1, 2])

        assert (selection.pipeline_id, selection.ref) == (None, None)


class TestSelectInPipeline:
    def _mock_jobs(self, mock_api: respx.MockRouter, jobs: list[dict]) -> respx.Route:
        return mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(200, json=jobs)
        )

    async def test_keeps_only_retryable_jobs(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        self._mock_jobs(mock_api, [
            _job_payload(1, name="a", status="failed"),
            _job_payload(2, name="b", status="canceled"),
            _job_payload(3, name="c", status="success"),
        ])

        selection = await select_in_pipeline(client, _pipeline())

        assert [j.id for j in selection.jobs] == [1, 2]
        assert selection.matched == 3

    async def test_reports_the_resolved_pipeline(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        """The resolved pipeline is reported even though the jobs' own
        back-reference says otherwise — it's the authoritative source."""
        mock_api.get("/projects/grp%2Fproj/pipelines/7/jobs").mock(
            return_value=Response(200, json=[_job_payload(1, name="a", status="failed")])
        )

        selection = await select_in_pipeline(client, _pipeline(7, ref="feature"))

        assert (selection.pipeline_id, selection.ref) == (7, "feature")

    async def test_failed_only_is_pushed_to_the_api_as_scope(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        """stage/name have no server-side equivalent, but scope does."""
        route = self._mock_jobs(mock_api, [_job_payload(1, name="a", status="failed")])

        await select_in_pipeline(client, _pipeline(), failed_only=True)

        assert route.calls.last.request.url.params["scope"] == "failed"

    async def test_stage_filter_is_applied(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        self._mock_jobs(mock_api, [
            _job_payload(1, name="a", status="failed", stage="test"),
            _job_payload(2, name="b", status="failed", stage="build"),
        ])

        selection = await select_in_pipeline(client, _pipeline(), stage="test")

        assert [j.id for j in selection.jobs] == [1]

    async def test_allowed_failures_excluded_by_default(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        payloads = [
            _job_payload(1, name="a", status="failed"),
            _job_payload(2, name="b", status="failed"),
        ]
        payloads[1]["allow_failure"] = True
        self._mock_jobs(mock_api, payloads)

        selection = await select_in_pipeline(client, _pipeline(), failed_only=True)

        assert [j.id for j in selection.jobs] == [1]

    async def test_include_allowed_failures_widens(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        payloads = [
            _job_payload(1, name="a", status="failed"),
            _job_payload(2, name="b", status="failed"),
        ]
        payloads[1]["allow_failure"] = True
        self._mock_jobs(mock_api, payloads)

        selection = await select_in_pipeline(
            client, _pipeline(), failed_only=True, include_allowed_failures=True,
        )

        assert [j.id for j in selection.jobs] == [1, 2]

    async def test_newest_id_is_the_highest_record_for_a_name(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        """Records arrive newest-first; the newest is by ID, not position."""
        mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(
                200,
                json=[
                    _job_payload(7, name="unit-tests", status="pending"),
                    _job_payload(3, name="unit-tests", status="failed"),
                ],
                headers={"x-total-pages": "1"},
            )
        )

        tally = await tally_attempts(client, 100, {"unit-tests"})

        assert tally.is_newest(_make_job(id=7, name="unit-tests")) is True
        assert tally.is_newest(_make_job(id=3, name="unit-tests")) is False

    async def test_unknown_name_is_never_newest(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines/100/jobs").mock(
            return_value=Response(200, json=[], headers={"x-total-pages": "1"})
        )

        tally = await tally_attempts(client, 100, {"unit-tests"})

        assert tally.is_newest(_make_job(id=1, name="unit-tests")) is False
