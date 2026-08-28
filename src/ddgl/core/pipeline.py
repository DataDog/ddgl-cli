# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import logging
from collections.abc import Iterable

import msgspec

from ddgl.cache.cache import Cache
from ddgl.cache.cache_config import CacheNS
from ddgl.client import GitLabClient
from ddgl.constants import CACHE_TTL_FINISHED_JOB, PipelineScope, PipelineStatus
from ddgl.core._concurrency import gather_bounded
from ddgl.exceptions import NoPipelineFoundError, ShellError
from ddgl.git import get_current_branch, get_recent_shas, looks_like_sha
from ddgl.model.pipeline import Pipeline

logger = logging.getLogger("ddgl.core.pipeline")

# Cap the commit-history fallback depth when --ref is explicit (not
# auto-detected from the current branch) — see resolve_pipeline. Walking
# many commits back from a ref the user specifically asked about risks
# silently returning an unrelated pipeline.
_EXPLICIT_REF_FALLBACK_DEPTH = 1


def _fallback_revision(ref: str) -> str:
    """The git revision to start the commit-history fallback walk from.

    GitLab only ever builds pushed commits, so a plain branch/tag-like ref
    (including a revision expression like "main^") is resolved against the
    *remote* state via "origin/<ref>", not whatever's checked out locally.
    A ref that already looks like a (possibly abbreviated) commit SHA, or
    is already "origin/"-prefixed, is used as-is.
    """
    if looks_like_sha(ref) or ref.startswith("origin/"):
        return ref
    revision = f"origin/{ref}"
    logger.warning(
        "No pipeline found for ref %r directly; searching commit history from "
        "%r instead (assumes your local clone's remote-tracking ref is up to "
        "date — run `git fetch` if this seems stale).",
        ref, revision,
    )
    return revision


def cache_terminal_pipeline(cache: Cache | None, project_id: str, pipeline: Pipeline) -> None:
    """Write a SUCCESS pipeline to the durable object cache.

    Only SUCCESS is cached — other terminal statuses can still change
    (e.g. a manual retry).
    """
    if cache is None:
        return
    if pipeline.status == PipelineStatus.SUCCESS:
        cache[CacheNS.OBJECTS].set(
            ("pipelines", project_id, pipeline.id),
            pipeline,
            ttl=CACHE_TTL_FINISHED_JOB,
        )


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
    cache_terminal_pipeline(cache, project_id, pipeline)
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
        fresh = await gather_bounded(
            get_pipeline(client, pid, cache=cache) for pid in misses
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
    fresh: bool = False,
) -> list[Pipeline]:
    """Fetch up to `count` pipelines for a ref. One API call.

    No list-level caching. SUCCESS pipelines among the results are cached individually.
    """
    project_id = client._config.project_id or ""
    page = await client.fetch_pipelines(ref=ref, per_page=count, scope=scope, fresh=fresh)
    pipelines = page.items

    for p in pipelines:
        cache_terminal_pipeline(cache, project_id, p)

    return pipelines


async def find_latest_pipeline(
    client: GitLabClient,
    ref: str,
    *,
    depth: int = 10,
    cache: Cache | None = None,
    fresh: bool = False,
) -> Pipeline:
    """Find the latest pipeline for a ref by walking commit history.

    1. Try ref as a literal GitLab ref (branch/tag) name.
    2. If no result: walk up to `depth` commits from _fallback_revision(ref)
       (ref itself if it looks like a SHA, otherwise "origin/<ref>").
    3. Raise NoPipelineFoundError if nothing found.
    """
    pipelines = await list_pipelines(client, ref, count=5, cache=cache, fresh=fresh)
    if pipelines:
        return max(pipelines, key=lambda p: p.id)

    revision = _fallback_revision(ref)
    try:
        shas = await get_recent_shas(depth, start=revision)
    except ShellError as exc:
        logger.warning("Could not resolve %r to walk commit history (%s)", revision, exc)
        shas = []

    for sha in shas:
        pipelines = await list_pipelines(client, sha, count=5, cache=cache, fresh=fresh)
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
    fresh: bool = False,
) -> Pipeline:
    """CLI convenience: auto-detect ref → find_latest_pipeline() → Pipeline.

    If pipeline_id is given, calls get_pipeline() directly (no ref resolution).

    An explicit `ref` caps the commit-history fallback depth to
    _EXPLICIT_REF_FALLBACK_DEPTH — unlike the auto-detected current branch
    (which may legitimately be a few unpushed commits ahead of the last
    built one), a ref the user specifically asked about shouldn't silently
    walk far back and return an unrelated pipeline.
    """
    if pipeline_id is not None:
        return await get_pipeline(client, pipeline_id, cache=cache)

    if ref is None:
        ref = await get_current_branch()
    else:
        depth = min(depth, _EXPLICIT_REF_FALLBACK_DEPTH)

    return await find_latest_pipeline(client, ref, depth=depth, cache=cache, fresh=fresh)
