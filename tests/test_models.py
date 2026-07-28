# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import msgspec

from ddgl.constants import JobStatus, PipelineStatus
from ddgl.model.attach import HeartbeatEvent, JobEvent, ResultEvent, SnapshotEvent
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
            "status": JobStatus.SUCCESS, "ref": "main",
        }
        return Job(**(defaults | overrides))

    def test_from_api(self) -> None:
        data = {
            "id": 10, "name": "test", "stage": "test",
            "status": "failed", "ref": "main",
            "failure_reason": "script_failure",
            "pipeline": {"id": 1},  # extra nested data ignored
        }
        j = Job.from_api(data)
        assert j.name == "test"
        assert j.status is JobStatus.FAILED
        assert j.failure_reason == "script_failure"

    def test_is_running(self) -> None:
        j = self._make(status=JobStatus.RUNNING)
        assert j.is_running is True

    def test_has_failed(self) -> None:
        j = self._make(status=JobStatus.FAILED)
        assert j.has_failed is True

    def test_not_failed_when_success(self) -> None:
        j = self._make(status=JobStatus.SUCCESS)
        assert j.has_failed is False


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
