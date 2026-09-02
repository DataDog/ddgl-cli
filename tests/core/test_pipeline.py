# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/core/pipeline.py."""
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response

from ddgl.client import GitLabClient
from ddgl.constants import PipelineStatus
from ddgl.core.pipeline import (
    find_latest_pipeline,
    get_pipeline,
    get_pipelines,
    list_pipelines,
    resolve_pipeline,
)
from ddgl.exceptions import NoPipelineFoundError, ShellError
from ddgl.model.pipeline import Pipeline

from ._stubs import TEST_CONFIG, FakeCache

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PROJECT_ID = TEST_CONFIG.project_id


@pytest.fixture()
def mock_api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=TEST_CONFIG.api_url) as router:
        yield router


@pytest.fixture()
async def client(mock_api: respx.MockRouter) -> GitLabClient:
    async with GitLabClient(TEST_CONFIG) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pipeline_payload(
    pipeline_id: int,
    status: str = "success",
    ref: str = "main",
) -> dict[str, Any]:
    return {
        "id": pipeline_id,
        "ref": ref,
        "status": status,
        "sha": "abc123",
        "web_url": f"https://gitlab.example.com/grp/proj/-/pipelines/{pipeline_id}",
        "source": "push",
        "created_at": "2024-01-01T00:00:00.000Z",
        "finished_at": "2024-01-01T00:01:00.000Z",
        "duration": 60,
    }


# ---------------------------------------------------------------------------
# get_pipeline
# ---------------------------------------------------------------------------


class TestGetPipeline:
    async def test_fetches_from_api_on_cache_miss(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines/42").mock(
            return_value=Response(200, json=_pipeline_payload(42))
        )
        pipeline = await get_pipeline(client, 42)
        assert pipeline.id == 42
        assert pipeline.status == PipelineStatus.SUCCESS

    async def test_cache_hit_skips_api(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        from ddgl.cache.cache_config import CacheNS

        cache = FakeCache()
        p = Pipeline.from_api(_pipeline_payload(99))
        cache[CacheNS.OBJECTS].set(("pipelines", _PROJECT_ID, 99), p)

        pipeline = await get_pipeline(client, 99, cache=cache)
        assert pipeline.id == 99
        assert mock_api.calls.call_count == 0

    async def test_success_pipeline_written_to_cache(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        from ddgl.cache.cache_config import CacheNS

        mock_api.get("/projects/grp%2Fproj/pipelines/10").mock(
            return_value=Response(200, json=_pipeline_payload(10, status="success"))
        )
        cache = FakeCache()
        await get_pipeline(client, 10, cache=cache)
        assert cache[CacheNS.OBJECTS][("pipelines", _PROJECT_ID, 10)] is not None

    async def test_non_success_pipeline_not_cached(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        from ddgl.cache.cache_config import CacheNS

        mock_api.get("/projects/grp%2Fproj/pipelines/11").mock(
            return_value=Response(200, json=_pipeline_payload(11, status="running"))
        )
        cache = FakeCache()
        await get_pipeline(client, 11, cache=cache)
        assert cache[CacheNS.OBJECTS][("pipelines", _PROJECT_ID, 11)] is None

    async def test_forwards_fresh_to_the_client(
        self, client: GitLabClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        async def fake_get_pipeline(pipeline_id: int, **kwargs: Any) -> Pipeline:
            seen.update(kwargs)
            return Pipeline.from_api(_pipeline_payload(pipeline_id))

        monkeypatch.setattr(client, "get_pipeline", fake_get_pipeline)
        await get_pipeline(client, 42, fresh=True)
        assert seen["fresh"] is True


# ---------------------------------------------------------------------------
# get_pipelines
# ---------------------------------------------------------------------------


class TestGetPipelines:
    async def test_empty_input(self, client: GitLabClient) -> None:
        result = await get_pipelines(client, [])
        assert result == []

    async def test_fetches_misses_concurrently(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        for pid in (1, 2):
            mock_api.get(f"/projects/grp%2Fproj/pipelines/{pid}").mock(
                return_value=Response(200, json=_pipeline_payload(pid))
            )
        result = await get_pipelines(client, [1, 2])
        assert {p.id for p in result} == {1, 2}

    async def test_cache_hits_skip_api(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        from ddgl.cache.cache_config import CacheNS

        cache = FakeCache()
        for pid in (1, 2):
            cache[CacheNS.OBJECTS].set(
                ("pipelines", _PROJECT_ID, pid),
                Pipeline.from_api(_pipeline_payload(pid)),
            )
        result = await get_pipelines(client, [1, 2], cache=cache)
        assert mock_api.calls.call_count == 0
        assert {p.id for p in result} == {1, 2}

    async def test_preserves_input_order(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        for pid in (5, 3, 7):
            mock_api.get(f"/projects/grp%2Fproj/pipelines/{pid}").mock(
                return_value=Response(200, json=_pipeline_payload(pid))
            )
        result = await get_pipelines(client, [5, 3, 7])
        assert [p.id for p in result] == [5, 3, 7]


# ---------------------------------------------------------------------------
# list_pipelines
# ---------------------------------------------------------------------------


class TestListPipelines:
    async def test_returns_pipelines(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(
                200, json=[_pipeline_payload(1), _pipeline_payload(2)]
            )
        )
        result = await list_pipelines(client, "main")
        assert [p.id for p in result] == [1, 2]

    async def test_passes_scope_to_api(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        from ddgl.constants import PipelineScope

        route = mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[_pipeline_payload(3, status="failed")])
        )
        await list_pipelines(client, "main", scope=PipelineScope.FINISHED)
        assert route.calls.last.request.url.params.get("scope") == "finished"

    async def test_passes_ref_to_api(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[])
        )
        await list_pipelines(client, "feature/foo")
        assert route.calls.last.request.url.params.get("ref") == "feature/foo"

    async def test_caches_success_pipelines(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        from ddgl.cache.cache_config import CacheNS

        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[
                _pipeline_payload(10, status="success"),
                _pipeline_payload(11, status="failed"),
            ])
        )
        cache = FakeCache()
        await list_pipelines(client, "main", cache=cache)
        assert cache[CacheNS.OBJECTS][("pipelines", _PROJECT_ID, 10)] is not None
        assert cache[CacheNS.OBJECTS][("pipelines", _PROJECT_ID, 11)] is None

    async def test_empty_returns_empty_list(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[])
        )
        result = await list_pipelines(client, "main")
        assert result == []


# ---------------------------------------------------------------------------
# find_latest_pipeline
# ---------------------------------------------------------------------------


class TestFindLatestPipeline:
    async def test_finds_pipeline_by_ref(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[_pipeline_payload(50)])
        )
        result = await find_latest_pipeline(client, "main")
        assert result.id == 50

    async def test_picks_latest_by_id(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[
                _pipeline_payload(10), _pipeline_payload(20), _pipeline_payload(5),
            ])
        )
        result = await find_latest_pipeline(client, "main")
        assert result.id == 20

    async def test_raises_when_nothing_found(
        self,
        client: GitLabClient,
        mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[])
        )

        async def _fake_shas(depth: int = 10, start: str = "HEAD") -> list[str]:
            return ["sha1", "sha2"]

        monkeypatch.setattr("ddgl.core.pipeline.get_recent_shas", _fake_shas)
        with pytest.raises(NoPipelineFoundError) as exc_info:
            await find_latest_pipeline(client, "main", depth=2)
        assert exc_info.value.ref == "main"
        assert exc_info.value.depth == 2

    async def test_falls_back_to_sha_walking(
        self,
        client: GitLabClient,
        mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _pipelines_by_ref(request: Any, *_: Any) -> Response:
            ref = request.url.params.get("ref", "")
            if ref == "deadbeef":
                return Response(200, json=[_pipeline_payload(77)])
            return Response(200, json=[])

        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            side_effect=_pipelines_by_ref
        )

        async def _fake_shas(depth: int = 10, start: str = "HEAD") -> list[str]:
            return ["sha1", "deadbeef", "sha3"]

        monkeypatch.setattr("ddgl.core.pipeline.get_recent_shas", _fake_shas)
        result = await find_latest_pipeline(client, "main")
        assert result.id == 77

    async def test_walks_from_origin_prefixed_ref_not_head(
        self,
        client: GitLabClient,
        mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression: the fallback used to walk local HEAD regardless of
        `ref` — e.g. `--ref main^` (not a real GitLab ref, and not HEAD
        either) would silently scan HEAD's history instead of main^'s."""
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[])
        )
        seen_starts = []

        async def _fake_shas(depth: int = 10, start: str = "HEAD") -> list[str]:
            seen_starts.append(start)
            return []

        monkeypatch.setattr("ddgl.core.pipeline.get_recent_shas", _fake_shas)
        with pytest.raises(NoPipelineFoundError):
            await find_latest_pipeline(client, "main^")
        assert seen_starts == ["origin/main^"]

    async def test_walks_from_sha_as_is_no_origin_prefix(
        self,
        client: GitLabClient,
        mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[])
        )
        seen_starts = []

        async def _fake_shas(depth: int = 10, start: str = "HEAD") -> list[str]:
            seen_starts.append(start)
            return []

        monkeypatch.setattr("ddgl.core.pipeline.get_recent_shas", _fake_shas)
        sha = "abc1234"
        with pytest.raises(NoPipelineFoundError):
            await find_latest_pipeline(client, sha)
        assert seen_starts == [sha]

    async def test_does_not_double_prefix_already_origin_ref(
        self,
        client: GitLabClient,
        mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[])
        )
        seen_starts = []

        async def _fake_shas(depth: int = 10, start: str = "HEAD") -> list[str]:
            seen_starts.append(start)
            return []

        monkeypatch.setattr("ddgl.core.pipeline.get_recent_shas", _fake_shas)
        with pytest.raises(NoPipelineFoundError):
            await find_latest_pipeline(client, "origin/main")
        assert seen_starts == ["origin/main"]

    async def test_logs_warning_when_prefixing_with_origin(
        self,
        client: GitLabClient,
        mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(logging.getLogger("ddgl"), "propagate", True)
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[])
        )

        async def _fake_shas(depth: int = 10, start: str = "HEAD") -> list[str]:
            return []

        monkeypatch.setattr("ddgl.core.pipeline.get_recent_shas", _fake_shas)
        with caplog.at_level("WARNING", logger="ddgl.core.pipeline"):
            with pytest.raises(NoPipelineFoundError):
                await find_latest_pipeline(client, "main^")
        assert any("origin/main^" in msg for msg in caplog.messages)

    async def test_unresolvable_revision_treated_as_no_candidates(
        self,
        client: GitLabClient,
        mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A local clone that hasn't fetched `origin/<ref>` shouldn't crash
        the whole command — just falls through to NoPipelineFoundError."""
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[])
        )

        async def _raising_shas(depth: int = 10, start: str = "HEAD") -> list[str]:
            raise ShellError(["git", "log"], 128, "fatal: bad revision")

        monkeypatch.setattr("ddgl.core.pipeline.get_recent_shas", _raising_shas)
        with pytest.raises(NoPipelineFoundError):
            await find_latest_pipeline(client, "some-branch")


class TestFindLatestPipelineNeverQueriesOriginPrefixedRef:
    """End-to-end regression, using a real git repo (not a mocked
    get_recent_shas): "origin/<ref>" is a purely local git concept for
    remote-tracking branches — GitLab itself has no such ref, it only
    knows the plain branch/tag name (or, for the fallback loop, a real
    commit SHA). The fallback must never send an "origin/"-prefixed
    string to the GitLab API, only use it to seed the local git walk.
    """

    @pytest.fixture()
    def repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """A real git repo with a real, pushed-to origin remote."""
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.co",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.co"}
        origin = tmp_path / "origin.git"
        origin.mkdir()
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main"], cwd=origin,
                       check=True, capture_output=True)

        work = tmp_path / "work"
        work.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True,
                       env=env, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=work,
                       check=True, capture_output=True)
        for i in range(2):
            (work / f"f{i}.txt").write_text(str(i))
            subprocess.run(["git", "add", "."], cwd=work, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"c{i}"], cwd=work, check=True,
                           env=env, capture_output=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=work,
                       check=True, capture_output=True)
        monkeypatch.chdir(work)
        return work

    async def test_only_plain_ref_and_real_shas_reach_gitlab(
        self, repo: Path, client: GitLabClient, mock_api: respx.MockRouter,
    ) -> None:
        seen_refs: list[str] = []

        def _capture(request: Any, *_: Any) -> Response:
            seen_refs.append(request.url.params.get("ref", ""))
            return Response(200, json=[])

        mock_api.get("/projects/grp%2Fproj/pipelines").mock(side_effect=_capture)

        with pytest.raises(NoPipelineFoundError):
            await find_latest_pipeline(client, "main^", depth=2)

        assert seen_refs, "the walk should have queried at least one ref"
        assert not any("origin/" in ref for ref in seen_refs)
        # First query is the literal text (step 1); the rest are real
        # commit SHAs from walking origin/main^ locally (step 2) — never
        # the "origin/main^" revision string itself.
        assert seen_refs[0] == "main^"
        assert all(len(r) == 40 for r in seen_refs[1:])


# ---------------------------------------------------------------------------
# resolve_pipeline
# ---------------------------------------------------------------------------


class TestResolvePipeline:
    async def test_with_explicit_pipeline_id(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines/99").mock(
            return_value=Response(200, json=_pipeline_payload(99))
        )
        result = await resolve_pipeline(client, pipeline_id=99)
        assert result.id == 99

    async def test_auto_detects_branch(
        self,
        client: GitLabClient,
        mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_branch() -> str:
            return "my-branch"

        monkeypatch.setattr("ddgl.core.pipeline.get_current_branch", _fake_branch)
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[_pipeline_payload(5, ref="my-branch")])
        )
        result = await resolve_pipeline(client)
        assert result.id == 5
        assert result.ref == "my-branch"

    async def test_explicit_ref_overrides_auto_detect(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[_pipeline_payload(6, ref="other")])
        )
        await resolve_pipeline(client, ref="other")
        assert route.calls.last.request.url.params.get("ref") == "other"

    async def test_auto_detected_ref_keeps_full_depth(
        self,
        client: GitLabClient,
        mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_branch() -> str:
            return "my-branch"

        monkeypatch.setattr("ddgl.core.pipeline.get_current_branch", _fake_branch)
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[])
        )
        seen_depths = []

        async def _fake_shas(depth: int = 10, start: str = "HEAD") -> list[str]:
            seen_depths.append(depth)
            return []

        monkeypatch.setattr("ddgl.core.pipeline.get_recent_shas", _fake_shas)
        with pytest.raises(NoPipelineFoundError):
            await resolve_pipeline(client, depth=10)
        assert seen_depths == [10]

    async def test_explicit_ref_caps_fallback_depth(
        self,
        client: GitLabClient,
        mock_api: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_api.get("/projects/grp%2Fproj/pipelines").mock(
            return_value=Response(200, json=[])
        )
        seen_depths = []

        async def _fake_shas(depth: int = 10, start: str = "HEAD") -> list[str]:
            seen_depths.append(depth)
            return []

        monkeypatch.setattr("ddgl.core.pipeline.get_recent_shas", _fake_shas)
        with pytest.raises(NoPipelineFoundError):
            await resolve_pipeline(client, ref="main^", depth=10)
        assert seen_depths == [1]
