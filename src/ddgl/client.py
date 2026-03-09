from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar
from urllib.parse import quote

import httpx

from ddgl.config import Config
from ddgl.constants import MAX_PAGES
from ddgl.model.job import Job
from ddgl.model.page import Page
from ddgl.model.pipeline import Pipeline

T = TypeVar("T")


class PaginationLimitError(Exception):
    """Raised when pagination exceeds the maximum allowed pages."""

    def __init__(self, max_pages: int, total_pages: int | None) -> None:
        self.max_pages = max_pages
        self.total_pages = total_pages
        total = f"/{total_pages}" if total_pages else ""
        super().__init__(
            f"Pagination limit reached: fetched {max_pages}{total} pages"
        )


class GitLabClient:
    """Async GitLab REST API client."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=config.api_url,
            headers={"PRIVATE-TOKEN": config.private_token},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> GitLabClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _project_path(self, project_id: str | None = None) -> str:
        pid = project_id or self._config.project_id
        if pid is None:
            raise ValueError(
                "No project ID configured. "
                "Set GITLAB_PROJECT_ID or pass project_id."
            )
        return f"/projects/{quote(pid, safe='')}"

    # -- Low-level helpers --

    async def _get(self, path: str, **params: Any) -> Any:
        """Fetch a single JSON object (non-paginated)."""
        resp = await self._http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _get_text(self, path: str) -> str:
        resp = await self._http.get(path)
        resp.raise_for_status()
        return resp.text

    async def _get_page(
        self,
        path: str,
        item_factory: Callable[[dict], T],
        **params: Any,
    ) -> Page[T]:
        """Fetch a single page of paginated results."""
        resp = await self._http.get(path, params=params)
        resp.raise_for_status()
        return Page.from_response(resp, item_factory)

    async def _paginate(
        self,
        path: str,
        item_factory: Callable[[dict], T],
        **params: Any,
    ) -> AsyncIterator[Page[T]]:
        """Async generator yielding pages until exhausted."""
        page_num = 1
        pages_fetched = 0
        while True:
            page = await self._get_page(
                path, item_factory, page=page_num, **params
            )
            yield page
            pages_fetched += 1
            if not page.has_next:
                break
            if pages_fetched >= MAX_PAGES:
                raise PaginationLimitError(MAX_PAGES, page.total_pages)
            page_num = page.next_page

    async def _get_all(
        self,
        path: str,
        item_factory: Callable[[dict], T],
        **params: Any,
    ) -> list[T]:
        """Exhaust pagination and return all items as a flat list."""
        items: list[T] = []
        async for page in self._paginate(path, item_factory, **params):
            items.extend(page.items)
        return items

    # -- Pipelines --

    async def fetch_pipelines(
        self,
        ref: str | None = None,
        project_id: str | None = None,
        per_page: int = 20,
    ) -> Page[Pipeline]:
        """Fetch a single page of pipelines."""
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if ref:
            params["ref"] = ref
        return await self._get_page(
            f"{base}/pipelines", Pipeline.from_api, **params
        )

    async def iter_pipelines(
        self,
        ref: str | None = None,
        project_id: str | None = None,
        per_page: int = 20,
    ) -> AsyncIterator[Page[Pipeline]]:
        """Stream pages of pipelines."""
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if ref:
            params["ref"] = ref
        async for page in self._paginate(
            f"{base}/pipelines",
            Pipeline.from_api,
            **params,
        ):
            yield page

    async def get_all_pipelines(
        self,
        ref: str | None = None,
        project_id: str | None = None,
        per_page: int = 20,
    ) -> list[Pipeline]:
        """Get all pipelines (exhausts pagination)."""
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if ref:
            params["ref"] = ref
        return await self._get_all(
            f"{base}/pipelines",
            Pipeline.from_api,
            **params,
        )

    async def get_pipeline(
        self,
        pipeline_id: int,
        project_id: str | None = None,
    ) -> Pipeline:
        """Get details of a single pipeline."""
        base = self._project_path(project_id)
        data = await self._get(f"{base}/pipelines/{pipeline_id}")
        return Pipeline.from_api(data)

    # -- Jobs --

    async def fetch_jobs(
        self,
        pipeline_id: int,
        project_id: str | None = None,
        per_page: int = 100,
    ) -> Page[Job]:
        """Fetch a single page of jobs for a pipeline."""
        base = self._project_path(project_id)
        return await self._get_page(
            f"{base}/pipelines/{pipeline_id}/jobs",
            Job.from_api,
            per_page=per_page,
        )

    async def iter_jobs(
        self,
        pipeline_id: int,
        project_id: str | None = None,
        per_page: int = 100,
    ) -> AsyncIterator[Page[Job]]:
        """Stream pages of jobs for a pipeline."""
        base = self._project_path(project_id)
        async for page in self._paginate(
            f"{base}/pipelines/{pipeline_id}/jobs",
            Job.from_api,
            per_page=per_page,
        ):
            yield page

    async def get_all_jobs(
        self,
        pipeline_id: int,
        project_id: str | None = None,
        per_page: int = 100,
    ) -> list[Job]:
        """Get all jobs for a pipeline (exhausts pagination)."""
        base = self._project_path(project_id)
        return await self._get_all(
            f"{base}/pipelines/{pipeline_id}/jobs",
            Job.from_api,
            per_page=per_page,
        )

    async def get_job(
        self,
        job_id: int,
        project_id: str | None = None,
    ) -> Job:
        """Get details of a single job."""
        base = self._project_path(project_id)
        data = await self._get(f"{base}/jobs/{job_id}")
        return Job.from_api(data)

    async def get_job_log(
        self,
        job_id: int,
        project_id: str | None = None,
    ) -> str:
        """Get the raw log output of a job."""
        base = self._project_path(project_id)
        return await self._get_text(f"{base}/jobs/{job_id}/trace")
