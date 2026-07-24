# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/core/logs.py."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx
from httpx import Response

from ddgl.cache.cache_config import CacheNS
from ddgl.client import GitLabClient
from ddgl.core.logs import _log_is_complete, get_log, get_logs, stream_log

from ._stubs import TEST_CONFIG, FakeCache

_BASE = "/projects/grp%2Fproj"

_COMPLETE_LOG = "Running step...\nJob succeeded\n"
_RUNNING_LOG = "Running step...\nstill going...\n"


@pytest.fixture()
def mock_api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=TEST_CONFIG.api_url) as router:
        yield router


@pytest.fixture()
async def client(mock_api: respx.MockRouter) -> GitLabClient:
    async with GitLabClient(TEST_CONFIG) as c:
        yield c


def _log_route(mock_api: respx.MockRouter, job_id: int, text: str) -> None:
    mock_api.get(f"{_BASE}/jobs/{job_id}/trace").mock(
        return_value=Response(200, text=text)
    )


# ---------------------------------------------------------------------------
# _log_is_complete
# ---------------------------------------------------------------------------


def test_log_is_complete_succeeded() -> None:
    assert _log_is_complete("...\nJob succeeded\n") is True


def test_log_is_complete_failed() -> None:
    assert _log_is_complete("...\nERROR: Job failed: exit code 1\n") is True


def test_log_is_complete_running() -> None:
    assert _log_is_complete("step 1\nstep 2\nstill running\n") is False


def test_log_is_complete_marker_not_in_tail() -> None:
    # Marker buried far above the tail — should not trigger
    prefix = "Job succeeded\n" + "noise\n" * 20
    assert _log_is_complete(prefix) is False


# ---------------------------------------------------------------------------
# get_logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_input(client: GitLabClient) -> None:
    result = await get_logs(client, [])
    assert result == {}


@pytest.mark.asyncio
async def test_fetches_all_without_cache(
    mock_api: respx.MockRouter, client: GitLabClient
) -> None:
    _log_route(mock_api, 1, _COMPLETE_LOG)
    _log_route(mock_api, 2, _RUNNING_LOG)

    result = await get_logs(client, [1, 2])

    assert result == {1: _COMPLETE_LOG, 2: _RUNNING_LOG}


@pytest.mark.asyncio
async def test_cache_hit_skips_api(
    mock_api: respx.MockRouter, client: GitLabClient
) -> None:
    cache = FakeCache()
    cache[CacheNS.LOGS].set("1", "cached-log", ttl=3600.0)

    result = await get_logs(client, [1], cache=cache)

    assert result == {1: "cached-log"}
    assert not any(r.called for r in mock_api.routes)


@pytest.mark.asyncio
async def test_partial_cache_hit_fetches_misses(
    mock_api: respx.MockRouter, client: GitLabClient
) -> None:
    cache = FakeCache()
    cache[CacheNS.LOGS].set("1", "cached", ttl=3600.0)
    _log_route(mock_api, 2, _COMPLETE_LOG)

    result = await get_logs(client, [1, 2], cache=cache)

    assert result == {1: "cached", 2: _COMPLETE_LOG}


@pytest.mark.asyncio
async def test_complete_log_stored_in_cache(
    mock_api: respx.MockRouter, client: GitLabClient
) -> None:
    _log_route(mock_api, 10, _COMPLETE_LOG)
    cache = FakeCache()

    await get_logs(client, [10], cache=cache)

    assert cache[CacheNS.LOGS]["10"] == _COMPLETE_LOG


@pytest.mark.asyncio
async def test_incomplete_log_not_stored_in_cache(
    mock_api: respx.MockRouter, client: GitLabClient
) -> None:
    _log_route(mock_api, 20, _RUNNING_LOG)
    cache = FakeCache()

    await get_logs(client, [20], cache=cache)

    assert cache[CacheNS.LOGS]["20"] is None


# ---------------------------------------------------------------------------
# get_log (single-job convenience wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_log_returns_single(
    mock_api: respx.MockRouter, client: GitLabClient
) -> None:
    _log_route(mock_api, 5, _COMPLETE_LOG)
    result = await get_log(client, 5)
    assert result == _COMPLETE_LOG


# ---------------------------------------------------------------------------
# stream_log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_log_from_cache(client: GitLabClient) -> None:
    cache = FakeCache()
    cache[CacheNS.LOGS].set("7", "line-a\nline-b\nline-c", ttl=3600.0)

    lines = [line async for line in stream_log(client, 7, cache=cache)]

    assert lines == ["line-a", "line-b", "line-c"]


@pytest.mark.asyncio
async def test_stream_log_from_api(
    mock_api: respx.MockRouter, client: GitLabClient
) -> None:
    _log_route(mock_api, 8, "x\ny\nz")

    lines = [line async for line in stream_log(client, 8)]

    assert lines == ["x", "y", "z"]


@pytest.mark.asyncio
async def test_stream_log_caches_complete_log(
    mock_api: respx.MockRouter, client: GitLabClient
) -> None:
    _log_route(mock_api, 9, _COMPLETE_LOG)
    cache = FakeCache()

    _ = [line async for line in stream_log(client, 9, cache=cache)]

    assert cache[CacheNS.LOGS]["9"] == _COMPLETE_LOG.rstrip("\n")


@pytest.mark.asyncio
async def test_stream_log_does_not_cache_incomplete(
    mock_api: respx.MockRouter, client: GitLabClient
) -> None:
    _log_route(mock_api, 11, _RUNNING_LOG)
    cache = FakeCache()

    _ = [line async for line in stream_log(client, 11, cache=cache)]

    assert cache[CacheNS.LOGS]["11"] is None
