# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import quote

import httpx

from ddgl.config import Config
from ddgl.constants import (
    CACHE_TTL_API_JOB,
    CACHE_TTL_API_JOB_LIST,
    CACHE_TTL_API_PIPELINE,
    CACHE_TTL_API_PIPELINE_LIST,
    MAX_PAGES,
    JobStatus,
    PipelineScope,
)
from ddgl.exceptions import (
    ConfigError,
    GitLabAPIError,
    NotFoundError,
    PaginationLimitError,
)
from ddgl.model.job import Job
from ddgl.model.page import Page
from ddgl.model.pipeline import Pipeline

if TYPE_CHECKING:
    from ddgl.cache import Cache

T = TypeVar("T")

logger = logging.getLogger("ddgl.http")


class GitLabClient:
    """Async GitLab REST API client."""

    def __init__(self, config: Config, cache: Cache | None = None) -> None:
        self._config = config
        self._cache = cache
        self._http = httpx.AsyncClient(
            base_url=config.api_url,
            headers={"Authorization": f"Bearer {config.private_token}"},
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
            raise ConfigError(
                "No project ID configured. "
                "Set GITLAB_PROJECT_ID or let ddgl detect it from the git remote."
            )
        return f"/projects/{quote(pid, safe='')}"

    # -- Low-level helpers --

    @staticmethod
    def _cache_key(path: str, params: dict) -> str:
        raw = f"{path}?{'&'.join(f'{k}={v}' for k, v in sorted(params.items()))}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _raise_for_status(self, resp: httpx.Response) -> None:
        """Translate HTTP errors into typed exceptions.

        Raises:
            ConfigError: HTTP 404 when the project itself is not found.
            NotFoundError: HTTP 404 on a sub-resource.
            GitLabAPIError: any other HTTP error status.
        """
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            path = str(resp.request.url.path)
            if resp.status_code == 404:
                body = resp.text[:200] if resp.text else ""
                if "Project Not Found" in body:
                    pid = self._config.project_id or "unknown"
                    raise ConfigError(
                        f"GitLab project '{pid}' not found. "
                        "Check GITLAB_PROJECT_ID or your git remote configuration."
                    ) from exc
                raise NotFoundError(path, "") from exc
            raise GitLabAPIError(
                resp.status_code, "GET", path,
                resp.text[:200] if resp.text else "",
            ) from exc

    async def _get(self, path: str, ttl: float = 0, **params: Any) -> Any:
        """Fetch a single JSON object (non-paginated).

        If *ttl* > 0 and a cache is configured, results are read from / written
        to the API_RESPONSES namespace.

        Raises:
            NotFoundError: HTTP 404 — resource does not exist.
            GitLabAPIError: any other HTTP error status.
        """
        if self._cache is not None and ttl > 0:
            from ddgl.cache import CacheNS

            key = self._cache_key(path, params)
            hit = self._cache[CacheNS.API_RESPONSES][key]
            if hit is not None:
                logger.debug("API cache HIT %s", path)
                return json.loads(hit)
        logger.debug("GET %s", path)
        resp = await self._http.get(path, params=params)
        logger.debug("GET %s -> %d", path, resp.status_code)
        self._raise_for_status(resp)
        data = resp.json()
        if self._cache is not None and ttl > 0:
            from ddgl.cache import CacheNS

            key = self._cache_key(path, params)
            self._cache[CacheNS.API_RESPONSES].set(key, json.dumps(data), ttl=ttl)
        return data

    async def _get_text(self, path: str) -> str:
        """Fetch a plain-text response body.

        Raises:
            NotFoundError: HTTP 404 — resource does not exist.
            GitLabAPIError: any other HTTP error status.
        """
        logger.debug("GET %s", path)
        resp = await self._http.get(path)
        logger.debug("GET %s -> %d", path, resp.status_code)
        self._raise_for_status(resp)
        return resp.text

    async def _get_page(
        self,
        path: str,
        item_factory: Callable[[dict], T],
        ttl: float = 0,
        **params: Any,
    ) -> Page[T]:
        """Fetch a single page of paginated results.

        If *ttl* > 0 and a cache is configured, the **raw JSON list** is cached
        (pagination metadata is not — only the item payload is stored).

        Raises:
            NotFoundError: HTTP 404 — resource does not exist.
            GitLabAPIError: any other HTTP error status.
        """
        if self._cache is not None and ttl > 0:
            from ddgl.cache import CacheNS

            key = self._cache_key(path, params)
            hit = self._cache[CacheNS.API_RESPONSES][key]
            if hit is not None:
                logger.debug("API cache HIT %s", path)
                items = [item_factory(d) for d in json.loads(hit)]
                return Page(items=items, page=1, next_page=None, total_pages=1, total=len(items))
        logger.debug("GET %s", path)
        resp = await self._http.get(path, params=params)
        logger.debug("GET %s -> %d", path, resp.status_code)
        self._raise_for_status(resp)
        page = Page.from_response(resp, item_factory)
        if self._cache is not None and ttl > 0:
            from ddgl.cache import CacheNS

            key = self._cache_key(path, params)
            self._cache[CacheNS.API_RESPONSES].set(key, json.dumps(resp.json()), ttl=ttl)
        return page

    async def _paginate(
        self,
        path: str,
        item_factory: Callable[[dict], T],
        ttl: float = 0,
        **params: Any,
    ) -> AsyncIterator[Page[T]]:
        """Async generator yielding pages until exhausted."""
        page_num = 1
        pages_fetched = 0
        while True:
            page = await self._get_page(
                path, item_factory, ttl=ttl, page=page_num, **params
            )
            yield page
            pages_fetched += 1
            logger.debug(
                "Page %d/%s fetched (%d items)",
                pages_fetched, page.total_pages or "?", len(page.items),
            )
            if not page.has_next:
                break
            if pages_fetched >= MAX_PAGES:
                raise PaginationLimitError(MAX_PAGES, page.total_pages)
            page_num = page.next_page

    async def _get_all(
        self,
        path: str,
        item_factory: Callable[[dict], T],
        ttl: float = 0,
        **params: Any,
    ) -> list[T]:
        """Exhaust pagination and return all items as a flat list."""
        items: list[T] = []
        async for page in self._paginate(path, item_factory, ttl=ttl, **params):
            items.extend(page.items)
        return items

    # -- Pipelines --

    async def fetch_pipelines(
        self,
        ref: str | None = None,
        project_id: str | None = None,
        per_page: int = 20,
        scope: PipelineScope | None = None,
    ) -> Page[Pipeline]:
        """Fetch a single page of pipelines."""
        logger.info("Fetching pipelines (ref=%s)", ref or "all")
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if ref:
            params["ref"] = ref
        if scope is not None:
            params["scope"] = scope
        return await self._get_page(
            f"{base}/pipelines", Pipeline.from_api, ttl=CACHE_TTL_API_PIPELINE_LIST, **params
        )

    async def iter_pipelines(
        self,
        ref: str | None = None,
        project_id: str | None = None,
        per_page: int = 20,
        scope: PipelineScope | None = None,
    ) -> AsyncIterator[Page[Pipeline]]:
        """Stream pages of pipelines."""
        logger.info("Streaming pipelines (ref=%s)", ref or "all")
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if ref:
            params["ref"] = ref
        if scope is not None:
            params["scope"] = scope
        async for page in self._paginate(
            f"{base}/pipelines",
            Pipeline.from_api,
            ttl=CACHE_TTL_API_PIPELINE_LIST,
            **params,
        ):
            yield page

    async def get_all_pipelines(
        self,
        ref: str | None = None,
        project_id: str | None = None,
        per_page: int = 20,
        scope: PipelineScope | None = None,
    ) -> list[Pipeline]:
        """Get all pipelines (exhausts pagination)."""
        logger.info("Getting all pipelines (ref=%s)", ref or "all")
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if ref:
            params["ref"] = ref
        if scope is not None:
            params["scope"] = scope
        return await self._get_all(
            f"{base}/pipelines",
            Pipeline.from_api,
            ttl=CACHE_TTL_API_PIPELINE_LIST,
            **params,
        )

    async def get_pipeline(
        self,
        pipeline_id: int,
        project_id: str | None = None,
    ) -> Pipeline:
        """Get details of a single pipeline.

        Raises:
            NotFoundError: pipeline does not exist.
            GitLabAPIError: other HTTP error.
        """
        logger.info("Getting pipeline %d", pipeline_id)
        base = self._project_path(project_id)
        try:
            data = await self._get(f"{base}/pipelines/{pipeline_id}", ttl=CACHE_TTL_API_PIPELINE)
        except NotFoundError:
            raise NotFoundError("pipeline", pipeline_id)
        return Pipeline.from_api(data)

    # -- Jobs --

    async def fetch_jobs(
        self,
        pipeline_id: int,
        project_id: str | None = None,
        per_page: int = 100,
        scope: JobStatus | None = None,
    ) -> Page[Job]:
        """Fetch a single page of jobs for a pipeline."""
        logger.info("Fetching jobs for pipeline %d", pipeline_id)
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if scope is not None:
            params["scope"] = scope
        return await self._get_page(
            f"{base}/pipelines/{pipeline_id}/jobs",
            Job.from_api,
            ttl=CACHE_TTL_API_JOB_LIST,
            **params,
        )

    async def iter_jobs(
        self,
        pipeline_id: int,
        project_id: str | None = None,
        per_page: int = 100,
        scope: JobStatus | None = None,
    ) -> AsyncIterator[Page[Job]]:
        """Stream pages of jobs for a pipeline."""
        logger.info("Streaming jobs for pipeline %d", pipeline_id)
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if scope is not None:
            params["scope"] = scope
        async for page in self._paginate(
            f"{base}/pipelines/{pipeline_id}/jobs",
            Job.from_api,
            ttl=CACHE_TTL_API_JOB_LIST,
            **params,
        ):
            yield page

    async def get_all_jobs(
        self,
        pipeline_id: int,
        project_id: str | None = None,
        per_page: int = 100,
        scope: JobStatus | None = None,
    ) -> list[Job]:
        """Get all jobs for a pipeline (exhausts pagination)."""
        logger.info("Getting all jobs for pipeline %d", pipeline_id)
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if scope is not None:
            params["scope"] = scope
        return await self._get_all(
            f"{base}/pipelines/{pipeline_id}/jobs",
            Job.from_api,
            ttl=CACHE_TTL_API_JOB_LIST,
            **params,
        )

    async def get_job(
        self,
        job_id: int,
        project_id: str | None = None,
    ) -> Job:
        """Get details of a single job.

        Raises:
            NotFoundError: job does not exist.
            GitLabAPIError: other HTTP error.
        """
        logger.info("Getting job %d", job_id)
        base = self._project_path(project_id)
        try:
            data = await self._get(f"{base}/jobs/{job_id}", ttl=CACHE_TTL_API_JOB)
        except NotFoundError:
            raise NotFoundError("job", job_id)
        return Job.from_api(data)

    async def get_job_log(
        self,
        job_id: int,
        project_id: str | None = None,
    ) -> str:
        """Get the raw log output of a job.

        Raises:
            NotFoundError: job does not exist.
            GitLabAPIError: other HTTP error.
        """
        logger.info("Getting log for job %d", job_id)
        base = self._project_path(project_id)
        try:
            return await self._get_text(f"{base}/jobs/{job_id}/trace")
        except NotFoundError:
            raise NotFoundError("job", job_id)

    async def stream_job_log(
        self,
        job_id: int,
        project_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream the raw log of a job line by line.

        Raises:
            NotFoundError: job does not exist.
            GitLabAPIError: other HTTP error.
        """
        logger.info("Streaming log for job %d", job_id)
        base = self._project_path(project_id)
        path = f"{base}/jobs/{job_id}/trace"
        async with self._http.stream("GET", path) as resp:
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if resp.status_code == 404:
                    raise NotFoundError("job", job_id) from exc
                raise GitLabAPIError(
                    resp.status_code, "GET", path,
                    (await resp.aread()).decode()[:200],
                ) from exc
            async for line in resp.aiter_lines():
                yield line
