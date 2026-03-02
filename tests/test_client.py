from __future__ import annotations

import httpx
import pytest
import respx

from ddgl.client import GitLabClient
from ddgl.config import Config

MOCK_PIPELINES = [
    {"id": 100, "status": "success", "ref": "main"},
    {"id": 99, "status": "failed", "ref": "main"},
]

MOCK_PIPELINE = {"id": 100, "status": "success", "ref": "main", "sha": "abc123"}

MOCK_JOBS = [
    {"id": 1, "name": "build", "status": "success", "stage": "build"},
    {"id": 2, "name": "test", "status": "failed", "stage": "test"},
]


class TestGitLabClient:
    async def test_get_pipelines(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines",
        ).mock(return_value=httpx.Response(200, json=MOCK_PIPELINES))

        result = await client.get_pipelines(ref="main")

        assert route.called
        assert len(result) == 2
        assert result[0]["id"] == 100

    async def test_get_pipeline(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100",
        ).mock(return_value=httpx.Response(200, json=MOCK_PIPELINE))

        result = await client.get_pipeline(100)
        assert result["sha"] == "abc123"

    async def test_get_jobs(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100/jobs",
        ).mock(return_value=httpx.Response(200, json=MOCK_JOBS))

        result = await client.get_jobs(100)
        assert len(result) == 2
        assert result[1]["name"] == "test"

    async def test_get_job_log(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(
            "/projects/my-group%2Fmy-project/jobs/1/trace",
        ).mock(return_value=httpx.Response(200, text="Build succeeded\nDone."))

        log = await client.get_job_log(1)
        assert "Build succeeded" in log

    async def test_missing_project_id_raises(self) -> None:
        config = Config(
            gitlab_url="https://gitlab.example.com",
            private_token="tok",
            project_id=None,
        )
        async with GitLabClient(config) as client:
            with pytest.raises(ValueError, match="No project ID"):
                await client.get_pipelines()

    async def test_auth_header_sent(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines",
        ).mock(return_value=httpx.Response(200, json=[]))

        await client.get_pipelines()

        request = route.calls[0].request
        assert request.headers["PRIVATE-TOKEN"] == "test-token"
