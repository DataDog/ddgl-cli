# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import asyncio
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
    HTTP_RETRY_ATTEMPTS,
    HTTP_RETRY_BACKOFF_INITIAL_SECONDS,
    HTTP_RETRY_BACKOFF_MULTIPLIER,
    MAX_CONCURRENT_PAGE_FETCHES,
    MAX_PAGES,
    HttpMethod,
    JobStatus,
    PipelineScope,
)
from ddgl.exceptions import (
    RATE_LIMITED_STATUS_CODES,
    RETRYABLE_STATUS_CODES,
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


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff delay before the given 0-indexed retry attempt."""
    return HTTP_RETRY_BACKOFF_INITIAL_SECONDS * (HTTP_RETRY_BACKOFF_MULTIPLIER**attempt)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse a 429 response's `Retry-After` header, if present and numeric.

    GitLab may also send an HTTP-date form (e.g. "Wed, 21 Oct 2026 07:28:00
    GMT"); that's rare in practice for this API and not handled here — falls
    back to the caller's own backoff delay instead of failing.
    """
    value = resp.headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


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

    async def _request(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        retry_statuses: frozenset[int] = frozenset(),
        retry_transport: bool = False,
    ) -> httpx.Response:
        """Issue one HTTP request, retrying per `retry_statuses`/`retry_transport`.

        Those two knobs aren't independently chosen per call in practice —
        each of this method's two callers passes a fixed pair: see their
        values below. They're still parameters of `_request` (rather than
        switched on `method` inside this function) because *why* GET and
        POST differ is idempotency, a property of the specific
        endpoint/verb that only the caller knows: a GET can always be
        safely re-sent. A POST cannot: re-sending `POST /jobs/:id/retry`
        after a timeout or a 502 could mint two jobs, because there is no
        way to distinguish "GitLab never saw it" from "GitLab processed it
        and the response was lost on the way back".

        Args:
            method: HTTP verb, forwarded to `self._http.request`.
            path: Path relative to the client's `base_url`.
            params: Query-string parameters. GET only, in practice.
            json: JSON request body. POST only, in practice.
            retry_statuses: HTTP status codes to treat as retryable. Empty
                (the default) means "never retry on status". `_get_response`
                passes `RETRYABLE_STATUS_CODES` (408/429/5xx); `_post_response`
                passes `RATE_LIMITED_STATUS_CODES` ({429}) only — a 429 means
                the request was *rejected* rather than processed, so
                re-sending it is safe even for a non-idempotent verb.
            retry_transport: Whether to retry an `httpx.TransportError`
                (timeout, DNS failure, connection reset — GitLab never
                responded at all). `_get_response` passes True.
                `_post_response` passes **False**: "no response" is
                precisely the ambiguous case where a re-send might
                duplicate the side effect.

        Retries up to `HTTP_RETRY_ATTEMPTS` times with exponential backoff
        (`HTTP_RETRY_BACKOFF_INITIAL_SECONDS` *
        `HTTP_RETRY_BACKOFF_MULTIPLIER` ** attempt), honoring a 429's
        `Retry-After` header in place of the computed delay when one is
        present and numeric.

        Returns:
            The final `httpx.Response`, whatever its status. Deliberately
            does NOT raise on a non-2xx — callers still run
            `_raise_for_status`, so the exception mapping
            (ConfigError/NotFoundError/GitLabAPIError) stays defined in
            exactly one place.

        Raises:
            httpx.TransportError: The request never got a response, and
                either `retry_transport` is False or the attempts were
                exhausted.
        """
        last_exc: httpx.TransportError | None = None
        for attempt in range(HTTP_RETRY_ATTEMPTS):
            is_last_attempt = attempt == HTTP_RETRY_ATTEMPTS - 1
            try:
                resp = await self._http.request(method, path, params=params, json=json)
            except httpx.TransportError as exc:
                last_exc = exc
                if not retry_transport or is_last_attempt:
                    raise
                delay = _backoff_delay(attempt)
                logger.warning(
                    "%s %s -> connection error (%s), retrying in %.1fs (attempt %d/%d)",
                    method, path, exc, delay, attempt + 2, HTTP_RETRY_ATTEMPTS,
                )
                await asyncio.sleep(delay)
                continue

            if resp.status_code not in retry_statuses or is_last_attempt:
                return resp

            delay = _retry_after_seconds(resp) or _backoff_delay(attempt)
            logger.warning(
                "%s %s -> %d (retryable), retrying in %.1fs (attempt %d/%d)",
                method, path, resp.status_code, delay, attempt + 2, HTTP_RETRY_ATTEMPTS,
            )
            await asyncio.sleep(delay)

        # Unreachable: the loop above always returns or raises on its last
        # iteration. Satisfies type checkers without a real code path.
        assert last_exc is not None
        raise last_exc

    async def _get_response(self, path: str, **params: Any) -> httpx.Response:
        """GET with automatic retry on transient (retryable) failures.

        Retries a connection-level failure (timeout, DNS, reset — GitLab
        never even responded) or a retryable HTTP status (408/429/5xx, see
        RETRYABLE_STATUS_CODES) up to HTTP_RETRY_ATTEMPTS times, honoring a
        429's `Retry-After` header when present. See `_request` for the
        full retry mechanics — this is a thin GET-flavored wrapper over it.

        Does NOT raise on a non-2xx response itself — callers still call
        `_raise_for_status()` on the returned response, so the exact
        exception mapping (ConfigError/NotFoundError/GitLabAPIError) stays
        defined in exactly one place. This only decides whether to retry
        before handing back whatever response (or connection error) it
        ultimately has.
        """
        return await self._request(
            HttpMethod.GET, path, params=params,
            retry_statuses=RETRYABLE_STATUS_CODES, retry_transport=True,
        )

    async def _post_response(self, path: str, json: dict[str, Any] | None = None) -> httpx.Response:
        """POST with automatic retry on a 429 only.

        Unlike GET, a POST isn't idempotent, so it can't be retried as
        aggressively — see `_request`'s docstring for the full rationale.
        """
        return await self._request(
            HttpMethod.POST, path, json=json,
            retry_statuses=RATE_LIMITED_STATUS_CODES, retry_transport=False,
        )

    def _raise_for_status(self, resp: httpx.Response, method: HttpMethod) -> None:
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
                resp.status_code, method, path,
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
        resp = await self._get_response(path, **params)
        logger.debug("GET %s -> %d", path, resp.status_code)
        self._raise_for_status(resp, HttpMethod.GET)
        data = resp.json()
        if self._cache is not None and ttl > 0:
            from ddgl.cache import CacheNS

            key = self._cache_key(path, params)
            self._cache[CacheNS.API_RESPONSES].set(key, json.dumps(data), ttl=ttl)
        return data

    async def _post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        """POST and parse the JSON response body.

        Never cached — a POST is a mutation, not a fetch.

        Raises:
            NotFoundError: HTTP 404 — resource does not exist.
            GitLabAPIError: any other HTTP error status.
        """
        logger.debug("POST %s", path)
        resp = await self._post_response(path, json=json)
        logger.debug("POST %s -> %d", path, resp.status_code)
        self._raise_for_status(resp, HttpMethod.POST)
        return resp.json()

    async def _get_text(self, path: str) -> str:
        """Fetch a plain-text response body.

        Raises:
            NotFoundError: HTTP 404 — resource does not exist.
            GitLabAPIError: any other HTTP error status.
        """
        logger.debug("GET %s", path)
        resp = await self._get_response(path)
        logger.debug("GET %s -> %d", path, resp.status_code)
        self._raise_for_status(resp, HttpMethod.GET)
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
        resp = await self._get_response(path, **params)
        logger.debug("GET %s -> %d", path, resp.status_code)
        self._raise_for_status(resp, HttpMethod.GET)
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
        """Exhaust pagination and return all items as a flat list.

        Fetches page 1 first (to learn the total page count from GitLab's
        `x-total-pages` header), then fetches the remaining pages
        concurrently — bounded by `MAX_CONCURRENT_PAGE_FETCHES` — instead of
        one at a time. Collapses N sequential round-trips into ~2 for large
        result sets (e.g. a pipeline with hundreds of jobs).

        Raises:
            PaginationLimitError: total pages exceeds MAX_PAGES.
            NotFoundError: HTTP 404 — resource does not exist.
            GitLabAPIError: any other HTTP error status, from any page. If a
                later page fails after earlier ones succeeded, still-pending
                page fetches are canceled rather than left to complete
                unobserved.
        """
        first = await self._get_page(path, item_factory, ttl=ttl, page=1, **params)
        items: list[T] = list(first.items)

        if not first.has_next:
            return items

        total_pages = first.total_pages
        if total_pages is None:
            # No page count available. Not reachable via the normal HTTP
            # response path (GitLab always reports x-total-pages), but the
            # per-page cache-hit branch of _get_page synthesizes has_next as
            # False for a lone cached page — so total_pages is only ever
            # None here if has_next was somehow still True. Fall back to a
            # sequential fetch (can't reuse _paginate: it always starts its
            # own internal page counter at 1, so passing page=first.next_page
            # into it would collide with its own page= kwarg).
            page_num = first.next_page
            pages_fetched = 1  # page 1 already counted
            while page_num is not None:
                if pages_fetched >= MAX_PAGES:
                    raise PaginationLimitError(MAX_PAGES, None)
                page = await self._get_page(path, item_factory, ttl=ttl, page=page_num, **params)
                items.extend(page.items)
                pages_fetched += 1
                page_num = page.next_page
            return items

        if total_pages > MAX_PAGES:
            raise PaginationLimitError(MAX_PAGES, total_pages)

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGE_FETCHES)

        async def _fetch(page_num: int) -> Page[T]:
            async with semaphore:
                return await self._get_page(path, item_factory, ttl=ttl, page=page_num, **params)

        tasks = [asyncio.create_task(_fetch(n)) for n in range(2, total_pages + 1)]
        try:
            pages = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            raise

        for page in pages:
            items.extend(page.items)
        return items

    # -- Pipelines --

    async def fetch_pipelines(
        self,
        ref: str | None = None,
        project_id: str | None = None,
        per_page: int = 20,
        scope: PipelineScope | None = None,
        *,
        fresh: bool = False,
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
            f"{base}/pipelines",
            Pipeline.from_api,
            ttl=0 if fresh else CACHE_TTL_API_PIPELINE_LIST,
            **params,
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
        *,
        fresh: bool = False,
    ) -> Pipeline:
        """Get details of a single pipeline.

        If *fresh* is True, bypasses the low-level API response cache
        (read and write) — used by `ddgl attach` when polling a running
        pipeline.

        Raises:
            NotFoundError: pipeline does not exist.
            GitLabAPIError: other HTTP error.
        """
        logger.info("Getting pipeline %d", pipeline_id)
        base = self._project_path(project_id)
        ttl = 0 if fresh else CACHE_TTL_API_PIPELINE
        try:
            data = await self._get(f"{base}/pipelines/{pipeline_id}", ttl=ttl)
        except NotFoundError:
            raise NotFoundError("pipeline", pipeline_id)
        return Pipeline.from_api(data)

    async def retry_pipeline(
        self,
        pipeline_id: int,
        project_id: str | None = None,
    ) -> Pipeline:
        """Retry every failed and canceled job in a pipeline.

        Returns only the `Pipeline` — GitLab does not report which jobs it
        restarted (see `retry_job` for that per-job detail).

        Raises:
            NotFoundError: pipeline does not exist.
            GitLabAPIError: other HTTP error (e.g. 403 insufficient token scope).
        """
        logger.info("Retrying pipeline %d", pipeline_id)
        base = self._project_path(project_id)
        try:
            data = await self._post(f"{base}/pipelines/{pipeline_id}/retry")
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
        *,
        fresh: bool = False,
        include_retried: bool = False,
    ) -> list[Job]:
        """Get all jobs for a pipeline (exhausts pagination).

        If *fresh* is True, bypasses the low-level API response cache for
        each page fetched. Used by `ddgl attach` when polling running job
        status (see `get_pipeline`).

        If *include_retried* is True, prior attempts of a retried job are
        included too (GitLab excludes them by default) — see
        `get_job_attempts`, which is this with `include_retried=True`.
        """
        logger.info("Getting all jobs for pipeline %d", pipeline_id)
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if scope is not None:
            params["scope"] = scope
        if include_retried:
            params["include_retried"] = True
        ttl = 0 if fresh else CACHE_TTL_API_JOB_LIST
        return await self._get_all(
            f"{base}/pipelines/{pipeline_id}/jobs",
            Job.from_api,
            ttl=ttl,
            **params,
        )

    async def get_job_attempts(
        self,
        pipeline_id: int,
        project_id: str | None = None,
    ) -> list[Job]:
        """Get every job record for a pipeline, including retried ones.

        "Attempts" rather than "retries" to head off an off-by-one: a job
        that has never been retried has 1 attempt, 0 retries.

        Always `fresh=True` — this backs the retry ledger's per-job-name
        attempt count, so a cached (stale, up to 15s old) count could let a
        run exceed `--retry-attempts`. Bypassing the cache also sidesteps
        a sharper correctness issue: `_get_page`'s cache-hit path always
        reports a lone cached page as `has_next=False`, so a cached read
        of a pipeline with more than one page of jobs would silently drop
        every page past the first.
        """
        return await self.get_all_jobs(
            pipeline_id, project_id, fresh=True, include_retried=True,
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

    async def retry_job(
        self,
        job_id: int,
        project_id: str | None = None,
    ) -> Job:
        """Retry a single job.

        GitLab mints a *new* job record under a different ID — the old
        job's ID never appears again in the pipeline's job list. Anything
        tracking retries across attempts (see `get_job_attempts`) must key
        on the job's NAME, not its ID.

        Raises:
            NotFoundError: job does not exist.
            GitLabAPIError: other HTTP error (e.g. 403 insufficient token scope).
        """
        logger.info("Retrying job %d", job_id)
        base = self._project_path(project_id)
        try:
            data = await self._post(f"{base}/jobs/{job_id}/retry")
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
                    resp.status_code, HttpMethod.GET, path,
                    (await resp.aread()).decode()[:200],
                ) from exc
            async for line in resp.aiter_lines():
                yield line
