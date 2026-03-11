from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import msgspec

from ddgl.cache.cache import Cache
from ddgl.cache.cache_config import CacheNS
from ddgl.client import GitLabClient
from ddgl.constants import CACHE_TTL_FINISHED_JOB, PipelineScope, PipelineStatus
from ddgl.exceptions import NoPipelineFoundError
from ddgl.git import get_current_branch, get_recent_shas
from ddgl.model.pipeline import Pipeline

logger = logging.getLogger("ddgl.core.pipeline")


async def get_pipeline(
    client: GitLabClient,
    pipeline_id: int,
    *,
    cache: Cache | None = None,
) -> Pipeline:
    """Fetch a single pipeline by ID. One API call on cache miss.

    Only SUCCESS pipelines are written to cache (other statuses can still change).
    """
    project_id = client._config.project_id or ""
    if cache is not None:
        cached = cache[CacheNS.OBJECTS][("pipelines", project_id, pipeline_id)]
        if cached is not None:
            logger.debug("cache hit: pipeline %d", pipeline_id)
            return msgspec.convert(cached, Pipeline, strict=False)

    logger.debug("fetching pipeline %d from API", pipeline_id)
    pipeline = await client.get_pipeline(pipeline_id)

    if cache is not None and pipeline.status == PipelineStatus.SUCCESS:
        cache[CacheNS.OBJECTS].set(
            ("pipelines", project_id, pipeline_id),
            pipeline,
            ttl=CACHE_TTL_FINISHED_JOB,
        )

    return pipeline


async def get_pipelines(
    client: GitLabClient,
    pipeline_ids: Iterable[int],
    *,
    cache: Cache | None = None,
) -> list[Pipeline]:
    """Bulk-fetch pipelines by ID with cache optimisation.

    1. Reads all cached pipelines for the project in one SQLite query.
    2. Fetches any cache misses concurrently.
    3. Returns results in input order.
    """
    ids = list(pipeline_ids)
    if not ids:
        return []

    project_id = client._config.project_id or ""
    if cache is not None:
        cached_objects = cache[CacheNS.OBJECTS]["pipelines"][project_id].get_many(
            ids, cls=Pipeline
        )
        cached_map: dict[int, Pipeline] = {
            p.id: p for p in cached_objects if isinstance(p, Pipeline)
        }
    else:
        cached_map = {}

    misses = [pid for pid in ids if pid not in cached_map]
    if misses:
        fresh = await asyncio.gather(
            *[get_pipeline(client, pid, cache=cache) for pid in misses]
        )
        for p in fresh:
            cached_map[p.id] = p

    return [cached_map[pid] for pid in ids if pid in cached_map]


async def list_pipelines(
    client: GitLabClient,
    ref: str,
    *,
    scope: PipelineScope | None = None,
    count: int = 20,
    cache: Cache | None = None,
) -> list[Pipeline]:
    """Fetch up to `count` pipelines for a ref. One API call.

    No list-level caching. SUCCESS pipelines among the results are cached individually.
    """
    project_id = client._config.project_id or ""
    page = await client.fetch_pipelines(ref=ref, per_page=count, scope=scope)
    pipelines = page.items

    if cache is not None:
        for p in pipelines:
            if p.status == PipelineStatus.SUCCESS:
                cache[CacheNS.OBJECTS].set(
                    ("pipelines", project_id, p.id),
                    p,
                    ttl=CACHE_TTL_FINISHED_JOB,
                )

    return pipelines


async def find_latest_pipeline(
    client: GitLabClient,
    ref: str,
    *,
    depth: int = 10,
    cache: Cache | None = None,
) -> Pipeline:
    """Find the latest pipeline for a ref by walking commit history.

    1. Try ref as a branch name.
    2. If no result: walk get_recent_shas(depth) one by one.
    3. Raise NoPipelineFoundError if nothing found.
    """
    pipelines = await list_pipelines(client, ref, count=5, cache=cache)
    if pipelines:
        return max(pipelines, key=lambda p: p.id)

    shas = await get_recent_shas(depth)
    for sha in shas:
        pipelines = await list_pipelines(client, sha, count=5, cache=cache)
        if pipelines:
            return max(pipelines, key=lambda p: p.id)

    raise NoPipelineFoundError(ref, depth)


async def resolve_pipeline(
    client: GitLabClient,
    *,
    ref: str | None = None,
    pipeline_id: int | None = None,
    depth: int = 10,
    cache: Cache | None = None,
) -> Pipeline:
    """CLI convenience: auto-detect ref → find_latest_pipeline() → Pipeline.

    If pipeline_id is given, calls get_pipeline() directly (no ref resolution).
    """
    if pipeline_id is not None:
        return await get_pipeline(client, pipeline_id, cache=cache)

    if ref is None:
        ref = await get_current_branch()

    return await find_latest_pipeline(client, ref, depth=depth, cache=cache)
