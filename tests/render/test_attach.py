"""Tests for src/ddgl/render/attach.py."""
from __future__ import annotations

from collections.abc import AsyncIterator

import msgspec
import pytest

from ddgl.model.attach import AttachEvent
from ddgl.render.attach import event_to_text, render_lines, render_live

# ---------------------------------------------------------------------------
# event_to_text
# ---------------------------------------------------------------------------


class TestEventToText:
    def test_snapshot(self) -> None:
        e = AttachEvent(
            kind="snapshot", ts="2026-07-21T12:00:03+00:00",
            pipeline_id=918342, ref="feature/checkout", status="running", jobs_total=40, jobs_done=0,
        )
        text = event_to_text(e)
        assert text.startswith("[12:00:03][INFO]")
        assert "attach #918342 feature/checkout" in text
        assert "running, 40 jobs" in text

    def test_job_without_duration(self) -> None:
        e = AttachEvent(
            kind="job", ts="2026-07-21T12:03:14+00:00",
            job_name="build:unit", old_status="created", status="running",
        )
        text = event_to_text(e)
        assert text == "[12:03:14][JOB]   build:unit created→running"

    def test_job_with_duration(self) -> None:
        e = AttachEvent(
            kind="job", ts="2026-07-21T12:05:29+00:00",
            job_name="build:unit", old_status="running", status="failed", duration=135.0,
        )
        text = event_to_text(e)
        assert "build:unit running→failed" in text
        assert "2m 15s" in text

    def test_job_new_has_no_old_status_shown_as_new(self) -> None:
        e = AttachEvent(kind="job", ts="2026-07-21T12:00:00+00:00", job_name="x", old_status=None, status="created")
        assert "x new→created" in event_to_text(e)

    def test_job_message_hidden_at_normal_detail(self) -> None:
        e = AttachEvent(
            kind="job", ts="2026-07-21T12:00:00+00:00",
            job_name="unit", old_status="running", status="failed", message="script_failure",
        )
        assert "script_failure" not in event_to_text(e, detail="normal")

    def test_job_message_shown_at_full_detail(self) -> None:
        e = AttachEvent(
            kind="job", ts="2026-07-21T12:00:00+00:00",
            job_name="unit", old_status="running", status="failed", message="script_failure",
        )
        assert "script_failure" in event_to_text(e, detail="full")

    def test_pipeline(self) -> None:
        e = AttachEvent(kind="pipeline", ts="2026-07-21T12:31:57+00:00", old_status="running", status="failed")
        assert event_to_text(e) == "[12:31:57][PIPE]  running→failed"

    def test_heartbeat(self) -> None:
        e = AttachEvent(
            kind="heartbeat", ts="2026-07-21T12:10:00+00:00",
            jobs_total=40, jobs_done=22, failed_jobs=("a", "b", "c", "d"),
        )
        assert event_to_text(e) == "[12:10:00][BEAT]  22/40 jobs, 4 failed"

    def test_switched(self) -> None:
        e = AttachEvent(kind="switched", ts="2026-07-21T12:00:00+00:00", message="newer pipeline #2 found")
        assert event_to_text(e) == "[12:00:00][WARN]  newer pipeline #2 found"

    def test_result_success_no_failed_jobs_clause(self) -> None:
        e = AttachEvent(
            kind="result", ts="2026-07-21T12:31:57+00:00", pipeline_id=1,
            status="success", reason="terminal",
        )
        text = event_to_text(e)
        assert text == "[12:31:57][FINAL] Pipeline #1 SUCCESS."

    def test_result_failed_lists_failed_jobs(self) -> None:
        e = AttachEvent(
            kind="result", ts="2026-07-21T12:31:57+00:00", pipeline_id=918342,
            status="failed", failed_jobs=("build:unit", "lint:ruff"), reason="terminal",
        )
        text = event_to_text(e)
        assert text == "[12:31:57][FINAL] Pipeline #918342 FAILED. Failed jobs: build:unit, lint:ruff"

    def test_result_timeout_with_pipeline(self) -> None:
        e = AttachEvent(kind="result", ts="2026-07-21T12:00:00+00:00", pipeline_id=1, status="running", reason="timeout")
        text = event_to_text(e)
        assert "Timed out waiting for pipeline #1" in text
        assert "running" in text

    def test_result_timeout_without_pipeline(self) -> None:
        e = AttachEvent(kind="result", ts="2026-07-21T12:00:00+00:00", pipeline_id=None, reason="timeout")
        assert event_to_text(e) == "[12:00:00][FINAL] Timed out waiting for a pipeline to appear."


# ---------------------------------------------------------------------------
# render_lines / render_live
# ---------------------------------------------------------------------------


def _events(*events: AttachEvent) -> AsyncIterator[AttachEvent]:
    async def _gen() -> AsyncIterator[AttachEvent]:
        for e in events:
            yield e
    return _gen()


_SNAPSHOT = AttachEvent(
    kind="snapshot", ts="2026-07-21T12:00:00+00:00", pipeline_id=1, ref="main", status="running",
    jobs_total=1, jobs_done=0,
)
_JOB = AttachEvent(kind="job", ts="2026-07-21T12:00:05+00:00", job_name="a", old_status="running", status="success", duration=5.0)
_PIPELINE = AttachEvent(kind="pipeline", ts="2026-07-21T12:00:05+00:00", old_status="running", status="success")
_HEARTBEAT = AttachEvent(kind="heartbeat", ts="2026-07-21T12:00:02+00:00", jobs_total=1, jobs_done=0)
_RESULT = AttachEvent(
    kind="result", ts="2026-07-21T12:00:05+00:00", pipeline_id=1, ref="main", status="success",
    jobs_total=1, jobs_done=1, duration=5.0, reason="terminal",
)


class TestRenderLines:
    async def test_prints_lines_and_returns_result(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = await render_lines(_events(_SNAPSHOT, _JOB, _PIPELINE, _RESULT))
        out = capsys.readouterr().out
        assert "[INFO]" in out
        assert "[JOB]" in out
        assert "[PIPE]" in out
        assert "[FINAL]" in out
        assert result.kind == "result"
        assert result is _RESULT

    async def test_as_json_emits_valid_jsonl(self, capsys: pytest.CaptureFixture[str]) -> None:
        await render_lines(_events(_SNAPSHOT, _RESULT), as_json=True)
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line]
        assert len(lines) == 2
        decoded = [msgspec.json.decode(line, type=AttachEvent) for line in lines]
        assert decoded == [_SNAPSHOT, _RESULT]

    async def test_detail_none_suppresses_snapshot_and_job(self, capsys: pytest.CaptureFixture[str]) -> None:
        await render_lines(_events(_SNAPSHOT, _JOB, _PIPELINE, _HEARTBEAT, _RESULT), detail="none")
        out = capsys.readouterr().out
        assert "[INFO]" not in out
        assert "[JOB]" not in out
        assert "[BEAT]" not in out
        assert "[PIPE]" in out
        assert "[FINAL]" in out

    async def test_detail_minimal_shows_snapshot_but_not_job(self, capsys: pytest.CaptureFixture[str]) -> None:
        await render_lines(_events(_SNAPSHOT, _JOB, _RESULT), detail="minimal")
        out = capsys.readouterr().out
        assert "[INFO]" in out
        assert "[JOB]" not in out

    async def test_result_line_always_present_regardless_of_detail(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        for detail in ("none", "minimal", "normal", "full"):
            await render_lines(_events(_SNAPSHOT, _RESULT), detail=detail)
            out = capsys.readouterr().out
            assert "[FINAL]" in out, f"missing [FINAL] at detail={detail}"

    async def test_long_line_is_not_word_wrapped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A long job name (or a long JSONL line) must stay one physical
        line — Rich word-wraps to terminal width by default, which would
        silently split one event across multiple output lines."""
        long_job = AttachEvent(
            kind="job", ts="2026-07-21T12:00:00+00:00",
            job_name="a" * 200, old_status="running", status="success",
        )
        await render_lines(_events(long_job, _RESULT))
        out = capsys.readouterr().out
        job_lines = [line for line in out.splitlines() if line and "[JOB]" in line]
        assert len(job_lines) == 1
        assert "a" * 200 in job_lines[0]


class TestRenderLive:
    async def test_returns_result(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = await render_live(_events(_SNAPSHOT, _JOB, _RESULT))
        assert result is _RESULT

    async def test_switched_prints_warning_above_live_region(self, capsys: pytest.CaptureFixture[str]) -> None:
        switched = AttachEvent(kind="switched", ts="2026-07-21T12:00:00+00:00", pipeline_id=2, message="switching to #2")
        await render_live(_events(_SNAPSHOT, switched, _RESULT))
        out = capsys.readouterr().out
        assert "switching to #2" in out
