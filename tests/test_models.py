# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import msgspec
import pytest

from ddgl.constants import JobStatus, PipelineStatus
from ddgl.model.attach import (
    AttachEvent,
    EventContext,
    HeartbeatEvent,
    JobEvent,
    PipelineState,
    ResultEvent,
    SnapshotEvent,
)
from ddgl.model.job import Job
from ddgl.model.log import JobLog
from ddgl.model.pipeline import Pipeline


class TestPipeline:
    def _make(self, **overrides: object) -> Pipeline:
        defaults: dict[str, object] = {
            "id": 1, "ref": "main",
            "status": PipelineStatus.SUCCESS, "sha": "abc",
        }
        return Pipeline(**(defaults | overrides))

    def test_from_api(self) -> None:
        data = {
            "id": 1, "ref": "main", "status": "success", "sha": "abc",
            "web_url": "https://example.com", "duration": 120,
            "extra_field": "ignored",
        }
        p = Pipeline.from_api(data)
        assert p.id == 1
        assert p.status is PipelineStatus.SUCCESS
        assert p.duration == 120

    def test_is_running(self) -> None:
        p = self._make(status=PipelineStatus.RUNNING)
        assert p.is_running is True
        assert p.is_finished is False

    def test_is_finished(self) -> None:
        p = self._make(status=PipelineStatus.SUCCESS)
        assert p.is_finished is True
        assert p.is_running is False

    def test_elapsed_finished(self) -> None:
        p = self._make(
            created_at="2025-01-01T00:00:00+00:00",
            finished_at="2025-01-01T00:05:00+00:00",
        )
        assert p.elapsed is not None
        assert p.elapsed.total_seconds() == 300

    def test_elapsed_none_without_created_at(self) -> None:
        p = self._make(created_at="")
        assert p.elapsed is None


class TestJob:
    def _make(self, **overrides: object) -> Job:
        defaults: dict[str, object] = {
            "id": 1, "name": "build", "stage": "build",
            "status": JobStatus.SUCCESS, "ref": "main", "pipeline_id": 1,
        }
        return Job(**(defaults | overrides))

    def test_from_api(self) -> None:
        data = {
            "id": 10, "name": "test", "stage": "test",
            "status": "failed", "ref": "main",
            "failure_reason": "script_failure",
            "pipeline": {"id": 1, "project_id": 7},  # only `id` is read
        }
        j = Job.from_api(data)
        assert j.name == "test"
        assert j.status is JobStatus.FAILED
        assert j.failure_reason == "script_failure"
        assert j.pipeline_id == 1

    def test_from_api_requires_the_nested_pipeline(self) -> None:
        """A payload with no `pipeline` isn't a job GitLab ever returns, so
        this is a hard error rather than a silently-unlabelled job."""
        data = {"id": 10, "name": "test", "stage": "test", "status": "failed"}
        with pytest.raises(KeyError):
            Job.from_api(data)

    def test_is_running(self) -> None:
        j = self._make(status=JobStatus.RUNNING)
        assert j.is_running is True

    def test_has_failed(self) -> None:
        j = self._make(status=JobStatus.FAILED)
        assert j.has_failed is True

    def test_not_failed_when_success(self) -> None:
        j = self._make(status=JobStatus.SUCCESS)
        assert j.has_failed is False

    def test_has_failed_true_regardless_of_allow_failure(self) -> None:
        j = self._make(status=JobStatus.FAILED, allow_failure=True)
        assert j.has_failed is True

    def test_is_blocking_when_failed_and_not_allowed(self) -> None:
        j = self._make(status=JobStatus.FAILED, allow_failure=False)
        assert j.is_blocking is True

    def test_is_blocking_false_when_failed_but_allowed(self) -> None:
        j = self._make(status=JobStatus.FAILED, allow_failure=True)
        assert j.is_blocking is False

    def test_is_blocking_false_when_not_failed(self) -> None:
        j = self._make(status=JobStatus.SUCCESS, allow_failure=False)
        assert j.is_blocking is False

    def test_is_blocking_false_when_success_and_allow_failure(self) -> None:
        j = self._make(status=JobStatus.SUCCESS, allow_failure=True)
        assert j.is_blocking is False

    def test_is_allowed_failure_when_failed_and_allowed(self) -> None:
        j = self._make(status=JobStatus.FAILED, allow_failure=True)
        assert j.is_allowed_failure is True

    def test_is_allowed_failure_false_when_failed_and_not_allowed(self) -> None:
        j = self._make(status=JobStatus.FAILED, allow_failure=False)
        assert j.is_allowed_failure is False

    def test_is_allowed_failure_false_when_not_failed(self) -> None:
        j = self._make(status=JobStatus.SUCCESS, allow_failure=False)
        assert j.is_allowed_failure is False

    def test_is_allowed_failure_false_when_success_and_allow_failure(self) -> None:
        j = self._make(status=JobStatus.SUCCESS, allow_failure=True)
        assert j.is_allowed_failure is False

    def test_is_retryable_when_failed(self) -> None:
        j = self._make(status=JobStatus.FAILED)
        assert j.is_retryable is True

    def test_is_retryable_when_canceled(self) -> None:
        j = self._make(status=JobStatus.CANCELED)
        assert j.is_retryable is True

    def test_is_retryable_false_when_success(self) -> None:
        j = self._make(status=JobStatus.SUCCESS)
        assert j.is_retryable is False

    def test_is_retryable_false_when_running(self) -> None:
        j = self._make(status=JobStatus.RUNNING)
        assert j.is_retryable is False


class TestJobLog:
    def test_ansi_stripping(self) -> None:
        raw = "\x1b[32mGreen text\x1b[0m normal"
        log = JobLog(raw)
        assert log.clean == "Green text normal"

    def test_lines(self) -> None:
        log = JobLog("line1\nline2\nline3")
        assert log.lines == ["line1", "line2", "line3"]

    def test_section_parsing(self) -> None:
        raw = (
            "section_start:1234:my_section\r\x1b[0K\n"
            "doing work\n"
            "more work\n"
            "section_end:1235:my_section\r\x1b[0K\n"
        )
        log = JobLog(raw)
        sections = log.sections
        assert len(sections) == 1
        assert sections[0].name == "my_section"
        assert "doing work" in sections[0].lines

    def test_multiple_sections(self) -> None:
        raw = (
            "section_start:1:build\r\x1b[0K\n"
            "compiling\n"
            "section_end:2:build\r\x1b[0K\n"
            "section_start:3:test\r\x1b[0K\n"
            "testing\n"
            "section_end:4:test\r\x1b[0K\n"
        )
        log = JobLog(raw)
        assert len(log.sections) == 2
        assert log.sections[0].name == "build"
        assert log.sections[1].name == "test"

    def test_empty_log(self) -> None:
        log = JobLog("")
        assert log.clean == ""
        assert log.sections == []
        assert log.lines == []


class TestAttachEvent:
    def test_defaults(self) -> None:
        e = HeartbeatEvent(ts="2025-01-01T00:00:00Z")
        assert e.pipeline_id is None
        assert e.ref is None
        assert e.current_stage is None
        assert e.pipeline_elapsed is None
        assert e.failed_jobs == ()
        # job_stage is JobEvent-only now — a heartbeat doesn't even have
        # the attribute, rather than having it default to None.
        assert not hasattr(e, "job_stage")

    def test_job_transition_fields(self) -> None:
        e = JobEvent(ts="2025-01-01T00:00:00Z",
            job_id=1, job_name="build:unit", job_stage="test",
            old_status="running", status="failed", duration=135.0,
        )
        assert e.old_status == "running"
        assert e.status == "failed"

    def test_result_fields(self) -> None:
        e = ResultEvent(ts="2025-01-01T00:31:57Z",
            pipeline_id=918342, status="failed",
            failed_jobs=("build:unit", "lint:ruff"),
            duration=1914.0, reason="terminal",
        )
        assert e.reason == "terminal"
        assert e.failed_jobs == ("build:unit", "lint:ruff")

    def test_json_roundtrip(self) -> None:
        e = SnapshotEvent(ts="2025-01-01T00:00:00Z",
            pipeline_id=1, status="running", jobs_total=5, jobs_done=1,
        )
        decoded = msgspec.json.decode(msgspec.json.encode(e), type=dict)
        assert decoded == {
            "kind": "snapshot", "ts": "2025-01-01T00:00:00Z", "pipeline_id": 1,
            "ref": None, "current_stage": None, "pipeline_elapsed": None,
            "status": "running", "jobs_total": 5, "jobs_done": 1,
            "failed_jobs": [], "eta_seconds": None,
        }


class TestEventContext:
    def test_fields_match_attach_events_base_fields(self) -> None:
        """EventContext exists to be splatted into any AttachEvent, so its
        fields must stay exactly AttachEvent's own (minus `ts`, which is
        per-event rather than per-tick). Adding a rollup field to one and
        not the other would otherwise fail only at runtime, on whichever
        event kind happened to be constructed first."""
        context_fields = set(EventContext.__struct_fields__)
        event_fields = set(AttachEvent.__struct_fields__) - {"ts"}
        assert context_fields == event_fields

    def test_as_fields_constructs_an_event(self) -> None:
        context = EventContext(pipeline_id=7, ref="main", jobs_total=3, jobs_done=1)
        event = HeartbeatEvent(ts="now", **context.as_fields())
        assert (event.pipeline_id, event.ref) == (7, "main")
        assert (event.jobs_total, event.jobs_done) == (3, 1)


class TestResultEventConstructors:
    def _state(self, status: PipelineStatus) -> PipelineState:
        return PipelineState(
            pipeline=Pipeline(id=1, ref="main", status=status), jobs=[]
        )

    def test_terminal_carries_status_and_context(self) -> None:
        context = EventContext(pipeline_id=1, ref="main", jobs_total=2, jobs_done=2)
        result = ResultEvent.terminal(self._state(PipelineStatus.SUCCESS), context)
        assert result.reason == "terminal"
        assert result.status == "success"
        assert (result.pipeline_id, result.jobs_done) == (1, 2)

    def test_timed_out_without_any_event(self) -> None:
        """No event yet means no pipeline was ever resolved."""
        result = ResultEvent.timed_out(None)
        assert result.reason == "timeout"
        assert result.pipeline_id is None
        assert result.status is None

    def test_timed_out_carries_the_last_events_state(self) -> None:
        last = HeartbeatEvent(ts="now", pipeline_id=9, ref="main", jobs_total=5, jobs_done=4)
        result = ResultEvent.timed_out(last)
        assert result.reason == "timeout"
        assert (result.pipeline_id, result.jobs_total, result.jobs_done) == (9, 5, 4)

    def test_timed_out_leaves_status_none_for_a_statusless_event(self) -> None:
        """HeartbeatEvent has no status field — the result must not invent
        one rather than reporting the pipeline as some default."""
        result = ResultEvent.timed_out(HeartbeatEvent(ts="now", pipeline_id=9))
        assert result.status is None


class TestPipelineState:
    def _job(self, job_id: int, stage: str, status: JobStatus) -> Job:
        return Job(
            id=job_id, name=f"job-{job_id}", stage=stage, status=status, pipeline_id=1
        )

    def _state(self, jobs: list[Job]) -> PipelineState:
        return PipelineState(
            pipeline=Pipeline(id=1, ref="main", status=PipelineStatus.RUNNING), jobs=jobs
        )

    def test_jobs_done_counts_terminal_jobs(self) -> None:
        state = self._state([
            self._job(1, "build", JobStatus.SUCCESS),
            self._job(2, "test", JobStatus.RUNNING),
            self._job(3, "test", JobStatus.SKIPPED),
        ])
        assert state.jobs_done == 2

    def test_failed_job_names_excludes_allowed_failures(self) -> None:
        allowed = self._job(2, "test", JobStatus.FAILED)
        allowed.allow_failure = True
        state = self._state([self._job(1, "test", JobStatus.FAILED), allowed])
        assert state.failed_job_names == ("job-1",)

    # -- current_stage: the oldest stage still holding incomplete work --

    def test_current_stage_none_without_jobs(self) -> None:
        assert self._state([]).current_stage is None

    def test_current_stage_single_stage(self) -> None:
        state = self._state([
            self._job(1, "build", JobStatus.RUNNING),
            self._job(2, "build", JobStatus.CREATED),
        ])
        assert state.current_stage == "build"

    def test_current_stage_is_oldest_incomplete_not_most_advanced(self) -> None:
        """Regression: must pick the EARLIEST (lowest min job ID) stage that
        still has incomplete work — the bottleneck — not whichever stage
        happens to have the highest-ID (most recently created/advanced) job.

        Deliberately constructed so a naive 'first not-done job in list
        order' (the old, buggy behavior) would pick the wrong stage: GitLab
        returns jobs newest-ID-first, so a highest-ID-first list places the
        most-advanced stage's job before the oldest stage's job.
        """
        state = self._state([
            self._job(30, "deploy", JobStatus.RUNNING),
            self._job(20, "test", JobStatus.SUCCESS),
            self._job(10, "build", JobStatus.RUNNING),
        ])
        assert state.current_stage == "build"

    def test_current_stage_ignores_fully_done_stages(self) -> None:
        state = self._state([
            self._job(10, "build", JobStatus.SUCCESS),
            self._job(20, "test", JobStatus.RUNNING),
            self._job(30, "deploy", JobStatus.CREATED),
        ])
        assert state.current_stage == "test"

    def test_current_stage_all_done_falls_back_to_oldest_overall(self) -> None:
        state = self._state([
            self._job(30, "deploy", JobStatus.SUCCESS),
            self._job(10, "build", JobStatus.SUCCESS),
            self._job(20, "test", JobStatus.SUCCESS),
        ])
        assert state.current_stage == "build"


class TestEventContextFromState:
    def test_reads_the_rollup_off_the_state(self) -> None:
        state = PipelineState(
            pipeline=Pipeline(id=42, ref="feature", status=PipelineStatus.RUNNING),
            jobs=[
                Job(id=1, name="a", stage="build", status=JobStatus.SUCCESS, pipeline_id=42),
                Job(id=2, name="b", stage="test", status=JobStatus.FAILED, pipeline_id=42),
            ],
        )
        context = EventContext.from_state(state)
        assert (context.pipeline_id, context.ref) == (42, "feature")
        assert (context.jobs_total, context.jobs_done) == (2, 2)
        assert context.failed_jobs == ("b",)
        assert context.current_stage == "build"  # all done -> oldest overall
        assert context.eta_seconds is None
