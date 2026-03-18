from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from ddgl.cache import Cache
from ddgl.client import GitLabClient
from ddgl.config import Config
from ddgl.exceptions import ConfigError, PaginationLimitError
from ddgl.model.job import Job
from ddgl.model.page import Page
from ddgl.model.pipeline import Pipeline

MOCK_PIPELINES = [
    {"id": 100, "status": "success", "ref": "main", "sha": "abc123"},
    {"id": 99, "status": "failed", "ref": "main", "sha": "def456"},
]

MOCK_PIPELINE_DETAIL = {
    "id": 100, "status": "success", "ref": "main", "sha": "abc123",
    "web_url": "https://gitlab.example.com/p/-/pipelines/100",
    "duration": 299,
    "created_at": "2025-01-01T00:00:00Z",
    "finished_at": "2025-01-01T00:05:00Z",
}

MOCK_JOBS_PAGE1 = [
    {"id": 1, "name": "build", "stage": "build", "status": "success",
     "ref": "main"},
    {"id": 2, "name": "test", "stage": "test", "status": "failed",
     "ref": "main", "failure_reason": "script_failure"},
]

MOCK_JOBS_PAGE2 = [
    {"id": 3, "name": "deploy", "stage": "deploy", "status": "success",
     "ref": "main"},
]


def _paginated_response(
    data: list,
    page: int = 1,
    next_page: int | None = None,
    total_pages: int = 1,
    total: int | None = None,
) -> httpx.Response:
    headers = {
        "x-page": str(page),
        "x-total-pages": str(total_pages),
    }
    if next_page is not None:
        headers["x-next-page"] = str(next_page)
    if total is not None:
        headers["x-total"] = str(total)
    return httpx.Response(200, json=data, headers=headers)


class TestFetchPipelines:
    async def test_returns_page(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines",
        ).mock(return_value=_paginated_response(
            MOCK_PIPELINES, page=1, total_pages=3, next_page=2, total=50,
        ))

        page = await client.fetch_pipelines(ref="main")

        assert isinstance(page, Page)
        assert len(page.items) == 2
        assert isinstance(page.items[0], Pipeline)
        assert page.items[0].id == 100
        assert page.has_next is True
        assert page.next_page == 2
        assert page.total == 50

    async def test_single_page_no_next(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines",
        ).mock(return_value=_paginated_response(
            MOCK_PIPELINES, page=1, total_pages=1,
        ))

        page = await client.fetch_pipelines()
        assert page.has_next is False
        assert page.next_page is None


class TestIterPipelines:
    async def test_iterates_pages(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines",
        )
        route.side_effect = [
            _paginated_response(
                [MOCK_PIPELINES[0]], page=1, next_page=2, total_pages=2,
            ),
            _paginated_response(
                [MOCK_PIPELINES[1]], page=2, total_pages=2,
            ),
        ]

        pages = []
        async for page in client.iter_pipelines():
            pages.append(page)

        assert len(pages) == 2
        assert pages[0].items[0].id == 100
        assert pages[1].items[0].id == 99

    async def test_max_pages_raises(
        self, client: GitLabClient, mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("ddgl.client.MAX_PAGES", 1)
        mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines",
        ).mock(return_value=_paginated_response(
            MOCK_PIPELINES, page=1, next_page=2, total_pages=100,
        ))

        with pytest.raises(PaginationLimitError, match="1/100"):
            async for _ in client.iter_pipelines():
                pass


class TestGetAllPipelines:
    async def test_flattens_pages(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines",
        )
        route.side_effect = [
            _paginated_response(
                [MOCK_PIPELINES[0]], page=1, next_page=2, total_pages=2,
            ),
            _paginated_response(
                [MOCK_PIPELINES[1]], page=2, total_pages=2,
            ),
        ]

        pipelines = await client.get_all_pipelines()

        assert len(pipelines) == 2
        assert isinstance(pipelines[0], Pipeline)
        assert pipelines[0].id == 100
        assert pipelines[1].id == 99


class TestFetchJobs:
    async def test_returns_page(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100/jobs",
        ).mock(return_value=_paginated_response(
            MOCK_JOBS_PAGE1, page=1, next_page=2, total_pages=2, total=3,
        ))

        page = await client.fetch_jobs(100)

        assert isinstance(page, Page)
        assert len(page.items) == 2
        assert isinstance(page.items[0], Job)
        assert page.has_next is True


class TestIterJobs:
    async def test_iterates_pages(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100/jobs",
        )
        route.side_effect = [
            _paginated_response(
                MOCK_JOBS_PAGE1, page=1, next_page=2, total_pages=2,
            ),
            _paginated_response(
                MOCK_JOBS_PAGE2, page=2, total_pages=2,
            ),
        ]

        pages = []
        async for page in client.iter_jobs(100):
            pages.append(page)

        assert len(pages) == 2
        assert pages[0].items[0].name == "build"
        assert pages[1].items[0].name == "deploy"


class TestGetAllJobs:
    async def test_get_all_jobs(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100/jobs",
        )
        route.side_effect = [
            _paginated_response(
                MOCK_JOBS_PAGE1, page=1, next_page=2, total_pages=2,
            ),
            _paginated_response(
                MOCK_JOBS_PAGE2, page=2, total_pages=2,
            ),
        ]

        jobs = await client.get_all_jobs(100)

        assert len(jobs) == 3
        assert isinstance(jobs[0], Job)
        assert jobs[2].name == "deploy"

    async def test_single_page_jobs(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100/jobs",
        ).mock(return_value=_paginated_response(MOCK_JOBS_PAGE1))

        jobs = await client.get_all_jobs(100)
        assert len(jobs) == 2
        assert jobs[1].failure_reason == "script_failure"


class TestGetJob:
    async def test_returns_job(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(
            "/projects/my-group%2Fmy-project/jobs/1",
        ).mock(return_value=httpx.Response(200, json=MOCK_JOBS_PAGE1[0]))

        result = await client.get_job(1)
        assert isinstance(result, Job)
        assert result.name == "build"
        assert result.status == "success"


class TestGetPipeline:
    async def test_returns_pipeline(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100",
        ).mock(return_value=httpx.Response(200, json=MOCK_PIPELINE_DETAIL))

        result = await client.get_pipeline(100)
        assert isinstance(result, Pipeline)
        assert result.sha == "abc123"
        assert result.duration == 299


class TestGetJobLog:
    async def test_returns_text(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(
            "/projects/my-group%2Fmy-project/jobs/1/trace",
        ).mock(
            return_value=httpx.Response(200, text="Build succeeded\nDone.")
        )

        log = await client.get_job_log(1)
        assert "Build succeeded" in log


class TestClientErrors:
    async def test_missing_project_id_raises(self) -> None:
        config = Config(
            gitlab_url="https://gitlab.example.com",
            private_token="tok",
            project_id=None,
        )
        async with GitLabClient(config) as c:
            with pytest.raises(ConfigError, match="No project ID"):
                await c.fetch_pipelines()

    async def test_auth_header_sent(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines",
        ).mock(return_value=_paginated_response([]))

        await client.fetch_pipelines()

        request = route.calls[0].request
        assert request.headers["PRIVATE-TOKEN"] == "test-token"


class TestPageModel:
    def test_has_next_true(self) -> None:
        page: Page[int] = Page(
            items=[1, 2], page=1, next_page=2,
            total_pages=3, total=6,
        )
        assert page.has_next is True

    def test_has_next_false(self) -> None:
        page: Page[int] = Page(
            items=[1, 2], page=3, next_page=None,
            total_pages=3, total=6,
        )
        assert page.has_next is False

    def test_from_response(self) -> None:
        resp = httpx.Response(
            200,
            json=[{"v": 1}, {"v": 2}],
            headers={
                "x-page": "2",
                "x-next-page": "3",
                "x-total-pages": "5",
                "x-total": "100",
            },
        )
        page = Page.from_response(resp, lambda d: d["v"])
        assert page.items == [1, 2]
        assert page.page == 2
        assert page.next_page == 3
        assert page.total_pages == 5
        assert page.total == 100

    def test_from_response_last_page(self) -> None:
        resp = httpx.Response(
            200,
            json=[{"v": 3}],
            headers={
                "x-page": "5",
                "x-total-pages": "5",
                "x-total": "100",
            },
        )
        page = Page.from_response(resp, lambda d: d["v"])
        assert page.has_next is False
        assert page.next_page is None


# ---------------------------------------------------------------------------
# API response caching
# ---------------------------------------------------------------------------

TEST_CONFIG = Config(
    gitlab_url="https://gitlab.example.com",
    private_token="test-token",
    project_id="my-group/my-project",
)


class TestClientCaching:
    """Tests for API response cache integration in GitLabClient."""

    @pytest.fixture(autouse=True)
    def _reset_cache_singleton(self) -> None:
        Cache._instance = None

    async def test_get_returns_cached_on_second_call(
        self, tmp_path: Path, mock_api: respx.MockRouter
    ) -> None:
        """Second _get() with same path+params returns cached data; only 1 HTTP call."""
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100",
        ).mock(return_value=httpx.Response(200, json=MOCK_PIPELINE_DETAIL))

        with Cache.open(tmp_path / "cache") as cache:
            async with GitLabClient(TEST_CONFIG, cache=cache) as client:
                r1 = await client.get_pipeline(100)
                r2 = await client.get_pipeline(100)

        assert r1.id == r2.id == 100
        assert route.call_count == 1

    async def test_cache_miss_fetches_from_api(
        self, tmp_path: Path, mock_api: respx.MockRouter
    ) -> None:
        """First call is always a cache miss and hits the API."""
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100",
        ).mock(return_value=httpx.Response(200, json=MOCK_PIPELINE_DETAIL))

        with Cache.open(tmp_path / "cache") as cache:
            async with GitLabClient(TEST_CONFIG, cache=cache) as client:
                result = await client.get_pipeline(100)

        assert result.id == 100
        assert route.call_count == 1

    async def test_get_text_is_not_cached(
        self, tmp_path: Path, mock_api: respx.MockRouter
    ) -> None:
        """_get_text() (job logs) is NOT cached by the API response cache."""
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/jobs/1/trace",
        ).mock(return_value=httpx.Response(200, text="Build succeeded\nDone."))

        with Cache.open(tmp_path / "cache") as cache:
            async with GitLabClient(TEST_CONFIG, cache=cache) as client:
                await client.get_job_log(1)
                await client.get_job_log(1)

        assert route.call_count == 2

    async def test_different_params_different_cache_keys(
        self, tmp_path: Path, mock_api: respx.MockRouter
    ) -> None:
        """Different params produce different cache keys."""
        mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines",
        ).mock(return_value=_paginated_response(MOCK_PIPELINES))

        with Cache.open(tmp_path / "cache") as cache:
            async with GitLabClient(TEST_CONFIG, cache=cache) as client:
                await client.fetch_pipelines(ref="main")
                await client.fetch_pipelines(ref="develop")

        # Two different refs = two different requests
        calls = mock_api.calls
        assert len(calls) == 2

    async def test_get_page_cached(
        self, tmp_path: Path, mock_api: respx.MockRouter
    ) -> None:
        """_get_page() also uses the cache."""
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100/jobs",
        ).mock(return_value=_paginated_response(MOCK_JOBS_PAGE1))

        with Cache.open(tmp_path / "cache") as cache:
            async with GitLabClient(TEST_CONFIG, cache=cache) as client:
                p1 = await client.fetch_jobs(100)
                p2 = await client.fetch_jobs(100)

        assert len(p1.items) == len(p2.items) == 2
        assert route.call_count == 1

    async def test_no_cache_means_no_caching(
        self, tmp_path: Path, mock_api: respx.MockRouter
    ) -> None:
        """When no cache is provided, every call hits the API."""
        route = mock_api.get(
            "/projects/my-group%2Fmy-project/pipelines/100",
        ).mock(return_value=httpx.Response(200, json=MOCK_PIPELINE_DETAIL))

        async with GitLabClient(TEST_CONFIG, cache=None) as client:
            await client.get_pipeline(100)
            await client.get_pipeline(100)

        assert route.call_count == 2
