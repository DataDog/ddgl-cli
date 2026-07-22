"""Tests for src/ddgl/core/attach.py."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
import respx
from httpx import Response

from ddgl.cache.cache_config import CacheNS
from ddgl.client import GitLabClient
from ddgl.constants import JobStatus, PipelineStatus
from ddgl.core.attach import DurationEstimator, NullEstimator, attach
from ddgl.exceptions import NoPipelineFoundError
from ddgl.model.attach import AttachEvent
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline

from ._stubs import TEST_CONFIG, FakeCache

_PROJECT_ID = TEST_CONFIG.project_id
_ENCODED_PROJECT = _PROJECT_ID.replace("/", "%2F")

# ---------------------------------------------------------------------------
# NullEstimator
# ---------------------------------------------------------------------------


class TestNullEstimator:
    def test_returns_none(self) -> None:
        estimator = NullEstimator()
        pipeline = Pipeline(id=1, ref="main", status=PipelineStatus.RUNNING)
        jobs = [Job(id=1, name="build", stage="build", status=JobStatus.RUNNING)]
        assert estimator.estimate_remaining(pipeline, jobs) is None

    def test_satisfies_protocol(self) -> None:
        assert isinstance(NullEstimator(), DurationEstimator)


class _FakeEstimator:
    """Test-only estimator: always predicts a fixed remaining duration.

    Local to this test module — not a production stub — matching the
    codebase's convention of not sharing fakes across production imports.
    """

    def estimate_remaining(self, pipeline: Pipeline, jobs: list[Job]) -> timedelta | None:
        return timedelta(seconds=42)


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
        # full one once jobs are loaded. Within a tick, a pipeline
        # transition is checked before job transitions — matches tick2
        # here: pipeline flips before jobs do.
        assert kinds == ["snapshot", "snapshot", "job", "pipeline", "job", "job", "result"]

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

    async def test_eta_seconds_none_by_default(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        """No estimator passed -> NullEstimator -> eta_seconds stays None."""
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines").mock(
            return_value=Response(200, json=[_pipeline_payload(1, "success")])
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "success", "a")])
        )
        events = await _collect(client, ref="main")
        assert all(e.eta_seconds is None for e in events)

    async def test_eta_seconds_populated_from_custom_estimator(
        self, client: GitLabClient, mock_api: respx.MockRouter
    ) -> None:
        """A real estimator's value flows through every event via _context()."""
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines").mock(
            return_value=Response(200, json=[_pipeline_payload(1, "success")])
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[_job_payload(10, "success", "a")])
        )
        events = await _collect(client, ref="main", estimator=_FakeEstimator())
        assert events[0].eta_seconds is None  # early snapshot: jobs not loaded, no context yet
        assert events[1].eta_seconds == 42.0  # full snapshot
        assert events[-1].eta_seconds == 42.0  # result


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


class TestAttachHeartbeat:
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
        beat = next(e for e in events if e.kind == "heartbeat")
        assert (beat.jobs_total, beat.jobs_done) == (1, 0)
        assert beat.ref == "main"

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
