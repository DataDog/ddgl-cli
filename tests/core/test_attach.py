"""Tests for src/ddgl/core/attach.py."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
import respx
from httpx import Response

from ddgl.cache.cache_config import CacheNS
from ddgl.client import GitLabClient
from ddgl.constants import JobStatus, PipelineStatus
from ddgl.core.attach import _current_stage, attach
from ddgl.exceptions import GitLabAPIError, NoPipelineFoundError
from ddgl.model.attach import AttachEvent
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline

from ._stubs import TEST_CONFIG, FakeCache

_PROJECT_ID = TEST_CONFIG.project_id
_ENCODED_PROJECT = _PROJECT_ID.replace("/", "%2F")

# ---------------------------------------------------------------------------
# _current_stage — "oldest stage still holding incomplete jobs"
# ---------------------------------------------------------------------------


def _job(job_id: int, stage: str, status: JobStatus) -> Job:
    return Job(id=job_id, name=f"job-{job_id}", stage=stage, status=status)


class TestCurrentStage:
    def test_empty_returns_none(self) -> None:
        assert _current_stage([]) is None

    def test_single_stage(self) -> None:
        jobs = [_job(1, "build", JobStatus.RUNNING), _job(2, "build", JobStatus.CREATED)]
        assert _current_stage(jobs) == "build"

    def test_returns_oldest_incomplete_stage_not_most_advanced(self) -> None:
        """Regression: must pick the EARLIEST (lowest min job ID) stage that
        still has incomplete work — the bottleneck — not whichever stage
        happens to have the highest-ID (most recently created/advanced) job.

        Deliberately constructed so a naive 'first not-done job in list
        order' (the old, buggy behavior) would pick the wrong stage: GitLab
        returns jobs newest-ID-first, so a highest-ID-first list places the
        most-advanced stage's job before the oldest stage's job.
        """
        jobs = [
            _job(30, "deploy", JobStatus.RUNNING),   # newest, most-advanced stage — still incomplete
            _job(20, "test", JobStatus.SUCCESS),      # test stage: done
            _job(10, "build", JobStatus.RUNNING),     # oldest stage — still incomplete: this is the answer
        ]
        assert _current_stage(jobs) == "build"

    def test_ignores_stage_with_no_incomplete_jobs(self) -> None:
        jobs = [
            _job(10, "build", JobStatus.SUCCESS),   # done — not a candidate
            _job(20, "test", JobStatus.RUNNING),    # oldest remaining incomplete stage
            _job(30, "deploy", JobStatus.CREATED),
        ]
        assert _current_stage(jobs) == "test"

    def test_all_done_falls_back_to_oldest_stage_overall(self) -> None:
        jobs = [
            _job(30, "deploy", JobStatus.SUCCESS),
            _job(10, "build", JobStatus.SUCCESS),
            _job(20, "test", JobStatus.SUCCESS),
        ]
        assert _current_stage(jobs) == "build"


# ---------------------------------------------------------------------------
# attach() engine
# ---------------------------------------------------------------------------

_INTERVAL = 0.01  # keep tests fast; real polling cadence is a CLI concern


@pytest.fixture()
def mock_api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=TEST_CONFIG.api_url) as router:
        yield router


@pytest.fixture()
async def client(mock_api: respx.MockRouter) -> GitLabClient:
    async with GitLabClient(TEST_CONFIG) as c:
        yield c


def _pipeline_payload(pipeline_id: int, status: str = "running", ref: str = "main") -> dict[str, Any]:
    return {
        "id": pipeline_id, "ref": ref, "status": status, "sha": "abc123",
        "created_at": "2024-01-01T00:00:00.000Z",
        "finished_at": "2024-01-01T00:05:00.000Z" if status != "running" else None,
    }


def _job_payload(job_id: int, status: str = "created", name: str = "job", stage: str = "test") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": job_id, "name": name, "stage": stage, "status": status, "ref": "main",
        "duration": 30.0 if status in ("success", "failed") else None,
    }
    if status == "failed":
        payload["failure_reason"] = "script_failure"
    return payload


def _mock_resolve(mock_api: respx.MockRouter, *, pipeline_id: int, ref: str = "main") -> None:
    """Mock the initial list-pipelines call used by resolve_pipeline."""
    mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines").mock(
        return_value=Response(200, json=[_pipeline_payload(pipeline_id, ref=ref)])
    )


async def _collect(
    client: GitLabClient, cache: FakeCache | None = None, **kwargs: Any
) -> list[AttachEvent]:
    return [e async for e in attach(client, interval=_INTERVAL, cache=cache, **kwargs)]


class TestAttachHappyPath:
    async def test_snapshot_then_transitions_then_success(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        _mock_resolve(mock_api, pipeline_id=1)

        jobs_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs")
        jobs_route.side_effect = [
            Response(200, json=[_job_payload(10, "created", "a"), _job_payload(11, "created", "b")]),
            Response(200, json=[_job_payload(10, "running", "a"), _job_payload(11, "created", "b")]),
            Response(200, json=[_job_payload(10, "success", "a"), _job_payload(11, "success", "b")]),
        ]
        pipeline_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1")
        pipeline_route.side_effect = [
            Response(200, json=_pipeline_payload(1, "running")),
            Response(200, json=_pipeline_payload(1, "success")),
        ]

        events = await _collect(client, ref="main")
        kinds = [e.kind for e in events]
        # attach() emits TWO snapshots: an early one right after resolving
        # the pipeline (before the — potentially slow — job fetch), then a
        # full one once jobs are loaded. Every changed tick ends with one
        # poll rollup; within a tick, a pipeline transition is checked before
        # job transitions — matches tick2 here: pipeline flips before jobs do.
        assert kinds == [
            "snapshot", "snapshot", "job", "poll", "pipeline", "job", "job", "poll", "result"
        ]
        # Regression: pipeline_id was only ever set explicitly on some event
        # kinds; "job" and "heartbeat" events fell through to the struct's
        # None default because _context() didn't include it. Every event
        # here has a real, resolved pipeline — none should show pipeline_id
        # as None.
        assert all(e.pipeline_id == 1 for e in events)

        early_snapshot = events[0]
        assert early_snapshot.pipeline_id == 1
        assert early_snapshot.ref == "main"
        assert early_snapshot.jobs_total is None  # jobs not loaded yet
        assert early_snapshot.jobs_done is None
        assert early_snapshot.pipeline_elapsed is not None and early_snapshot.pipeline_elapsed > 0

        snapshot = events[1]
        assert snapshot.pipeline_id == 1
        assert snapshot.jobs_total == 2
        assert snapshot.jobs_done == 0
        # Context fields: populated even on kinds that aren't obviously
        # "about" them, so renderers never need cross-event state.
        assert snapshot.ref == "main"
        assert snapshot.current_stage == "test"  # first not-done job's stage
        assert snapshot.pipeline_elapsed is not None and snapshot.pipeline_elapsed > 0

        job_events = [e for e in events if e.kind == "job"]
        assert (job_events[0].job_name, job_events[0].old_status, job_events[0].status) == ("a", "created", "running")
        assert job_events[0].job_stage == "test"  # that job's own stage

        result = events[-1]
        assert result.status == "success"
        assert result.reason == "terminal"
        assert result.failed_jobs == ()
        assert result.ref == "main"

    async def test_eta_seconds_always_none(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        """v1 ships no ETA estimation — eta_seconds always stays None."""
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines").mock(
            return_value=Response(200, json=[_pipeline_payload(1, "success")])
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "success", "a")])
        )
        events = await _collect(client, ref="main")
        assert all(e.eta_seconds is None for e in events)


class TestAttachFailure:
    async def test_result_reports_failed_jobs(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        _mock_resolve(mock_api, pipeline_id=1)

        jobs_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs")
        jobs_route.side_effect = [
            Response(200, json=[_job_payload(10, "running", "unit")]),
            Response(200, json=[_job_payload(10, "failed", "unit")]),
        ]
        pipeline_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1")
        pipeline_route.side_effect = [Response(200, json=_pipeline_payload(1, "failed"))]

        events = await _collect(client, ref="main")
        result = events[-1]
        assert result.kind == "result"
        assert result.status == "failed"
        assert result.reason == "terminal"
        assert result.failed_jobs == ("unit",)

        job_event = next(e for e in events if e.kind == "job")
        assert job_event.message == "script_failure"


class TestAttachAlreadyTerminal:
    async def test_no_polling_when_already_finished(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines").mock(
            return_value=Response(200, json=[_pipeline_payload(1, "success")])
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "success", "a")])
        )
        events = await _collect(client, ref="main")
        assert [e.kind for e in events] == ["snapshot", "snapshot", "result"]
        assert events[-1].reason == "terminal"


class TestAttachWaitForStart:
    async def test_waits_then_finds_pipeline(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines")
        route.side_effect = [
            Response(200, json=[]),
            Response(200, json=[]),
            Response(200, json=[_pipeline_payload(1, "success", ref="main")]),
        ]
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[])
        )
        events = await _collect(client, ref="main", wait_for_start=True)
        assert events[0].kind == "snapshot"
        assert events[0].pipeline_id == 1

    async def test_no_wait_raises(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines").mock(
            return_value=Response(200, json=[])
        )
        with pytest.raises(NoPipelineFoundError):
            await _collect(client, ref="main", wait_for_start=False)

    async def test_wait_times_out(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines").mock(
            return_value=Response(200, json=[])
        )
        events = await _collect(client, ref="main", wait_for_start=True, timeout=0.03)
        assert len(events) == 1
        assert events[0].kind == "result"
        assert events[0].reason == "timeout"
        assert events[0].pipeline_id is None


class TestAttachFollow:
    async def test_switches_to_newer_pipeline(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        # follow re-lists pipelines each tick to look for a newer id
        list_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines")
        list_route.side_effect = [
            Response(200, json=[_pipeline_payload(1, "running")]),  # initial resolve
            Response(200, json=[_pipeline_payload(2, "success", ref="main")]),  # follow check finds #2
        ]
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "running", "a")])
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/2/jobs").mock(
            return_value=Response(200, json=[_job_payload(20, "success", "a")])
        )
        # No GET .../pipelines/2 mock: the engine already knows #2 is
        # terminal from the list payload above, so it must not re-fetch it.

        events = await _collect(client, ref="main", follow=True)
        switched = next(e for e in events if e.kind == "switched")
        assert switched.pipeline_id == 2
        assert events[-1].pipeline_id == 2
        assert events[-1].status == "success"

    async def test_follow_bypasses_pipeline_list_cache(
        self, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        list_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines")
        list_route.side_effect = [
            Response(200, json=[_pipeline_payload(1, "running")]),
            Response(200, json=[_pipeline_payload(2, "success", ref="main")]),
        ]
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "running", "a")])
        )
        real_get_all_jobs = GitLabClient.get_all_jobs

        async def get_all_jobs(pipeline_id: int, **kwargs: Any) -> Any:
            if pipeline_id == 2:
                return [Job(id=20, name="a", stage="test", status=JobStatus.SUCCESS)]
            return await real_get_all_jobs(cached_client, pipeline_id, **kwargs)

        async def get_pipeline(pipeline_id: int, **kwargs: Any) -> Pipeline:
            return Pipeline(id=pipeline_id, ref="main", status=PipelineStatus.SUCCESS)

        async with GitLabClient(TEST_CONFIG, cache=FakeCache()) as cached_client:
            monkeypatch.setattr(cached_client, "get_all_jobs", get_all_jobs)
            monkeypatch.setattr(cached_client, "get_pipeline", get_pipeline)
            events = await _collect(cached_client, ref="main", follow=True)

        assert next(e for e in events if e.kind == "switched").pipeline_id == 2
        assert list_route.call_count == 2


class TestAttachHeartbeat:
    async def test_emits_one_poll_summary_after_each_changed_tick(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        _mock_resolve(mock_api, pipeline_id=1)
        jobs_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs")
        jobs_route.side_effect = [
            Response(200, json=[_job_payload(10, "created", "a"), _job_payload(11, "created", "b")]),
            Response(200, json=[_job_payload(10, "running", "a"), _job_payload(11, "created", "b")]),
            Response(200, json=[_job_payload(10, "success", "a"), _job_payload(11, "success", "b")]),
        ]
        pipeline_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1")
        pipeline_route.side_effect = [
            Response(200, json=_pipeline_payload(1, "running")),
            Response(200, json=_pipeline_payload(1, "success")),
        ]

        events = await _collect(client, ref="main")
        polls = [e for e in events if e.kind == "poll"]
        assert len(polls) == 2
        assert (polls[0].jobs_total, polls[0].jobs_done) == (2, 0)
        assert (polls[1].jobs_total, polls[1].jobs_done) == (2, 2)

    async def test_emits_tally_on_quiet_tick(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        _mock_resolve(mock_api, pipeline_id=1)
        jobs_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs")
        jobs_route.side_effect = [
            Response(200, json=[_job_payload(10, "running", "a")]),
            Response(200, json=[_job_payload(10, "running", "a")]),  # quiet tick
            Response(200, json=[_job_payload(10, "success", "a")]),
        ]
        pipeline_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1")
        pipeline_route.side_effect = [
            Response(200, json=_pipeline_payload(1, "running")),
            Response(200, json=_pipeline_payload(1, "success")),
        ]
        events = await _collect(client, ref="main", heartbeat=True)
        assert "heartbeat" in [e.kind for e in events]
        assert [e.kind for e in events].count("poll") == 1
        beat = next(e for e in events if e.kind == "heartbeat")
        assert (beat.jobs_total, beat.jobs_done) == (1, 0)
        assert beat.ref == "main"
        assert beat.pipeline_id == 1  # regression: heartbeat used to fall through to None

    async def test_no_heartbeat_by_default(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        _mock_resolve(mock_api, pipeline_id=1)
        jobs_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs")
        jobs_route.side_effect = [
            Response(200, json=[_job_payload(10, "running", "a")]),
            Response(200, json=[_job_payload(10, "running", "a")]),
            Response(200, json=[_job_payload(10, "success", "a")]),
        ]
        pipeline_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1")
        pipeline_route.side_effect = [
            Response(200, json=_pipeline_payload(1, "running")),
            Response(200, json=_pipeline_payload(1, "success")),
        ]
        events = await _collect(client, ref="main", heartbeat=False)
        assert "heartbeat" not in [e.kind for e in events]
        assert [e.kind for e in events].count("poll") == 1


class TestAttachTimeoutWhileRunning:
    async def test_exits_with_timeout_reason(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        _mock_resolve(mock_api, pipeline_id=1)
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "running", "a")])
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1").mock(
            return_value=Response(200, json=_pipeline_payload(1, "running"))
        )
        events = await _collect(client, ref="main", timeout=0.03)
        result = events[-1]
        assert result.kind == "result"
        assert result.reason == "timeout"
        assert result.status == "running"

    async def test_cancels_initial_job_fetch_at_deadline(
        self, client: GitLabClient, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_resolve(mock_api, pipeline_id=1)

        async def slow_get_all_jobs(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(0.1)
            return []

        monkeypatch.setattr(client, "get_all_jobs", slow_get_all_jobs)
        started = asyncio.get_running_loop().time()
        events = await _collect(client, ref="main", timeout=0.01)

        assert asyncio.get_running_loop().time() - started < 0.05
        assert [event.kind for event in events] == ["snapshot", "result"]
        assert events[-1].reason == "timeout"

class TestAttachCaching:
    async def test_polls_bypass_cache_and_write_terminal_jobs(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        _mock_resolve(mock_api, pipeline_id=1)
        jobs_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs")
        jobs_route.side_effect = [
            Response(200, json=[_job_payload(10, "running", "a")]),  # initial snapshot
            Response(200, json=[_job_payload(10, "running", "a")]),  # tick1 (quiet)
            Response(200, json=[_job_payload(10, "success", "a")]),  # tick2 (terminal)
        ]
        pipeline_route = mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1")
        pipeline_route.side_effect = [
            Response(200, json=_pipeline_payload(1, "running")),
            Response(200, json=_pipeline_payload(1, "success")),
        ]
        cache = FakeCache()
        await _collect(client, cache=cache, ref="main")

        # Every poll hit the API (no cache read shortcut) despite repeated
        # requests to the same path — proves `fresh=True` was honored.
        assert jobs_route.call_count == 3
        assert pipeline_route.call_count == 2
        # Terminal job was written to the durable object cache.
        assert cache[CacheNS.OBJECTS][("jobs", _PROJECT_ID, 10)] is not None
        # SUCCESS pipeline was written to the durable object cache.
        assert cache[CacheNS.OBJECTS][("pipelines", _PROJECT_ID, 1)] is not None


# ---------------------------------------------------------------------------
# Poll resilience: one bad tick (e.g. a 500 mid-poll on a large pipeline —
# the exact scenario hit in practice) must not kill a long-running attach.
# These monkeypatch client methods directly rather than going through respx,
# so the engine's tick-skip logic is tested in isolation from the client's
# own per-call retry logic (already covered by tests/test_client.py).
# ---------------------------------------------------------------------------


class TestAttachPollResilience:
    async def test_transient_tick_failure_is_skipped_not_fatal(
        self, client: GitLabClient, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_resolve(mock_api, pipeline_id=1)
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "success", "a")])
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1").mock(
            return_value=Response(200, json=_pipeline_payload(1, "success"))
        )

        real_get_pipeline = client.get_pipeline
        calls = {"n": 0}

        async def flaky_get_pipeline(pipeline_id: int, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise GitLabAPIError(500, "GET", "/fake", "boom")
            return await real_get_pipeline(pipeline_id, **kwargs)

        monkeypatch.setattr(client, "get_pipeline", flaky_get_pipeline)

        events = await _collect(client, ref="main")
        assert calls["n"] == 2  # tick1 failed, tick2 is what actually succeeded
        assert events[-1].kind == "result"
        assert events[-1].status == "success"
        # The failed tick produced no events at all — it's silently skipped,
        # not surfaced as a warning/error event (design decision: log-only).
        assert all(e.kind != "result" or e is events[-1] for e in events)

    async def test_transport_tick_failure_is_skipped_not_fatal(
        self, client: GitLabClient, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        _mock_resolve(mock_api, pipeline_id=1)
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "success", "a")])
        )
        calls = {"n": 0}

        async def flaky_get_pipeline(pipeline_id: int, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("connection reset")
            return Pipeline(id=pipeline_id, ref="main", status=PipelineStatus.SUCCESS)

        monkeypatch.setattr(client, "get_pipeline", flaky_get_pipeline)

        events = await _collect(client, ref="main")

        assert calls["n"] == 2
        assert events[-1].status == "success"

    async def test_gives_up_after_max_consecutive_failures(
        self, client: GitLabClient, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ddgl.constants import MAX_CONSECUTIVE_POLL_FAILURES

        _mock_resolve(mock_api, pipeline_id=1)
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[])
        )
        calls = {"n": 0}

        async def always_failing_get_pipeline(pipeline_id: int, **kwargs: Any) -> Any:
            calls["n"] += 1
            raise GitLabAPIError(500, "GET", "/fake", "boom")

        monkeypatch.setattr(client, "get_pipeline", always_failing_get_pipeline)

        with pytest.raises(GitLabAPIError):
            await _collect(client, ref="main")
        # Discriminates "tolerates a run of failures before giving up" from
        # merely "eventually raises" (which the old, unfixed engine also
        # did — just on the very first failure).
        assert calls["n"] == MAX_CONSECUTIVE_POLL_FAILURES

    async def test_follow_check_failure_does_not_block_main_poll(
        self, client: GitLabClient, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_resolve(mock_api, pipeline_id=1)
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "success", "a")])
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1").mock(
            return_value=Response(200, json=_pipeline_payload(1, "success"))
        )

        async def always_failing_list_pipelines(*args: Any, **kwargs: Any) -> Any:
            raise GitLabAPIError(500, "GET", "/fake", "boom")

        import ddgl.core.attach as attach_module
        monkeypatch.setattr(attach_module, "list_pipelines", always_failing_list_pipelines)

        events = await _collect(client, ref="main", follow=True)
        # Despite the follow-check failing every single tick, the main poll
        # still runs in the same tick and reaches a normal result — a
        # follow failure is silent and never fatal to the attach itself.
        assert events[-1].kind == "result"
        assert events[-1].status == "success"

    async def test_follow_job_fetch_failure_stays_on_old_pipeline(
        self, client: GitLabClient, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_state = {"list_calls": 0}

        def _list_side_effect(request: Any, **kwargs: Any) -> Response:
            call_state["list_calls"] += 1
            if call_state["list_calls"] == 1:
                return Response(200, json=[_pipeline_payload(1, "running")])  # initial resolve
            return Response(200, json=[_pipeline_payload(2, "running", ref="main")])  # every follow-check

        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines").mock(side_effect=_list_side_effect)
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "success", "a")])
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1").mock(
            return_value=Response(200, json=_pipeline_payload(1, "success"))
        )

        real_get_all_jobs = client.get_all_jobs

        async def flaky_get_all_jobs(pipeline_id: int, **kwargs: Any) -> Any:
            if pipeline_id == 2:  # the "newer" pipeline's job fetch always fails
                raise GitLabAPIError(500, "GET", "/fake", "boom")
            return await real_get_all_jobs(pipeline_id, **kwargs)

        monkeypatch.setattr(client, "get_all_jobs", flaky_get_all_jobs)

        events = await _collect(client, ref="main", follow=True)
        # The follow attempt always dies at the job-fetch step for #2, so we
        # never actually switch — no 'switched' event, and attach completes
        # normally on the original pipeline (#1) instead.
        assert "switched" not in [e.kind for e in events]
        assert events[-1].pipeline_id == 1
        assert events[-1].status == "success"
