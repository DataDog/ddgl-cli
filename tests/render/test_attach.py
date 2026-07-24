"""Tests for src/ddgl/render/attach.py."""
from __future__ import annotations

from collections.abc import AsyncIterator

import msgspec
import pytest

from ddgl.model.attach import (
    AttachEvent,
    HeartbeatEvent,
    JobEvent,
    PipelineEvent,
    PollEvent,
    ResultEvent,
    SnapshotEvent,
    SwitchedEvent,
)
from ddgl.render.attach import _live_markup, event_to_text, render_lines, render_live

# ---------------------------------------------------------------------------
# event_to_text
# ---------------------------------------------------------------------------


class TestEventToText:
    def test_snapshot(self) -> None:
        e = SnapshotEvent(ts="2026-07-21T12:00:03+00:00",
            pipeline_id=918342, ref="feature/checkout", status="running", jobs_total=40, jobs_done=0,
        )
        text = event_to_text(e)
        assert "40 jobs" in text

    def test_snapshot_with_no_jobs_loaded_yet_shows_loading(self) -> None:
        """attach()'s early snapshot (before the job fetch) has jobs_total
        None — must render as 'loading jobs…', never the string 'None jobs'."""
        e = SnapshotEvent(ts="2026-07-21T12:00:03+00:00",
            pipeline_id=918342, ref="feature/checkout", status="running",
        )
        text = event_to_text(e)
        assert "None" not in text
        assert "loading jobs" in text
        assert text.startswith("[12:00:03][INFO]")
        assert "attach #918342 feature/checkout" in text

    def test_job_without_duration(self) -> None:
        e = JobEvent(ts="2026-07-21T12:03:14+00:00",
            job_id=1, job_stage="test", job_name="build:unit", old_status="created", status="running",
        )
        text = event_to_text(e)
        assert text == "[12:03:14][JOB]   build:unit created→running"

    def test_job_with_duration(self) -> None:
        e = JobEvent(ts="2026-07-21T12:05:29+00:00",
            job_id=1, job_stage="test", job_name="build:unit", old_status="running", status="failed", duration=135.0,
        )
        text = event_to_text(e)
        assert "build:unit running→failed" in text
        assert "2m 15s" in text

    def test_job_new_has_no_old_status_shown_as_new(self) -> None:
        e = JobEvent(ts="2026-07-21T12:00:00+00:00", job_id=1, job_stage="test", job_name="x", old_status=None, status="created")
        assert "x new→created" in event_to_text(e)

    def test_job_message_hidden_at_normal_detail(self) -> None:
        e = JobEvent(ts="2026-07-21T12:00:00+00:00",
            job_id=1, job_stage="test", job_name="unit", old_status="running", status="failed", message="script_failure",
        )
        assert "script_failure" not in event_to_text(e, detail="normal")

    def test_job_message_shown_at_full_detail(self) -> None:
        e = JobEvent(ts="2026-07-21T12:00:00+00:00",
            job_id=1, job_stage="test", job_name="unit", old_status="running", status="failed", message="script_failure",
        )
        assert "script_failure" in event_to_text(e, detail="full")

    def test_pipeline(self) -> None:
        e = PipelineEvent(ts="2026-07-21T12:31:57+00:00", old_status="running", status="failed")
        assert event_to_text(e) == "[12:31:57][PIPE]  running→failed"

    def test_heartbeat(self) -> None:
        e = HeartbeatEvent(ts="2026-07-21T12:10:00+00:00",
            jobs_total=40, jobs_done=22, failed_jobs=("a", "b", "c", "d"),
        )
        assert event_to_text(e) == "[12:10:00][BEAT]  22/40 jobs, 4 failed"

    def test_poll(self) -> None:
        e = PollEvent(ts="2026-07-21T12:10:00+00:00",
            jobs_total=40, jobs_done=22, failed_jobs=("a", "b"), current_stage="test",
        )
        assert event_to_text(e) == "[12:10:00][POLL]  22/40 jobs · 2 failed · test"

    def test_switched(self) -> None:
        e = SwitchedEvent(ts="2026-07-21T12:00:00+00:00", message="newer pipeline #2 found")
        assert event_to_text(e) == "[12:00:00][WARN]  newer pipeline #2 found"

    def test_result_success_no_failed_jobs_clause(self) -> None:
        e = ResultEvent(ts="2026-07-21T12:31:57+00:00", pipeline_id=1,
            status="success", reason="terminal",
        )
        text = event_to_text(e)
        assert text == "[12:31:57][FINAL] Pipeline #1 SUCCESS."

    def test_result_failed_lists_failed_jobs(self) -> None:
        e = ResultEvent(ts="2026-07-21T12:31:57+00:00", pipeline_id=918342,
            status="failed", failed_jobs=("build:unit", "lint:ruff"), reason="terminal",
        )
        text = event_to_text(e)
        assert text == "[12:31:57][FINAL] Pipeline #918342 FAILED. Failed jobs: build:unit, lint:ruff"

    def test_result_timeout_with_pipeline(self) -> None:
        e = ResultEvent(ts="2026-07-21T12:00:00+00:00", pipeline_id=1, status="running", reason="timeout")
        text = event_to_text(e)
        assert "Timed out waiting for pipeline #1" in text
        assert "running" in text

    def test_result_timeout_without_pipeline(self) -> None:
        e = ResultEvent(ts="2026-07-21T12:00:00+00:00", pipeline_id=None, reason="timeout")
        assert event_to_text(e) == "[12:00:00][FINAL] Timed out waiting for a pipeline to appear."


# ---------------------------------------------------------------------------
# render_lines / render_live
# ---------------------------------------------------------------------------


def _events(*events: AttachEvent) -> AsyncIterator[AttachEvent]:
    async def _gen() -> AsyncIterator[AttachEvent]:
        for e in events:
            yield e
    return _gen()


_SNAPSHOT = SnapshotEvent(ts="2026-07-21T12:00:00+00:00", pipeline_id=1, ref="main", status="running",
    jobs_total=1, jobs_done=0,
)
_JOB = JobEvent(ts="2026-07-21T12:00:05+00:00", job_id=1, job_stage="test", job_name="a", old_status="running", status="success", duration=5.0)
_PIPELINE = PipelineEvent(ts="2026-07-21T12:00:05+00:00", old_status="running", status="success")
_HEARTBEAT = HeartbeatEvent(ts="2026-07-21T12:00:02+00:00", jobs_total=1, jobs_done=0)
_RESULT = ResultEvent(ts="2026-07-21T12:00:05+00:00", pipeline_id=1, ref="main", status="success",
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
        assert result is _RESULT

    async def test_as_json_emits_valid_jsonl(self, capsys: pytest.CaptureFixture[str]) -> None:
        await render_lines(_events(_SNAPSHOT, _RESULT), as_json=True)
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line]
        assert len(lines) == 2
        decoded = [msgspec.json.decode(line, type=dict) for line in lines]
        expected = [msgspec.to_builtins(_SNAPSHOT), msgspec.to_builtins(_RESULT)]
        for e in expected:
            e["failed_jobs"] = list(e["failed_jobs"])  # JSON array decodes as list, not tuple
        assert decoded == expected

    async def test_detail_none_shows_only_result(self, capsys: pytest.CaptureFixture[str]) -> None:
        await render_lines(_events(_SNAPSHOT, _JOB, _PIPELINE, _HEARTBEAT, _RESULT), detail="none")
        out = capsys.readouterr().out
        assert "[INFO]" not in out
        assert "[JOB]" not in out
        assert "[BEAT]" not in out
        assert "[PIPE]" not in out
        assert "[FINAL]" in out

    async def test_detail_minimal_shows_summaries_but_not_job_or_pipeline(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await render_lines(_events(_SNAPSHOT, _JOB, _PIPELINE, _HEARTBEAT, _RESULT), detail="minimal")
        out = capsys.readouterr().out
        assert "[INFO]" in out
        assert "[BEAT]" in out
        assert "[JOB]" not in out
        assert "[PIPE]" not in out

    async def test_detail_normal_shows_job_only_when_terminal(self, capsys: pytest.CaptureFixture[str]) -> None:
        """At --detail normal, job transitions are the one kind gated by
        status: only transitions reaching a terminal state (success/
        failed/canceled/skipped) are shown — the created→running/
        running→pending blips that dominate on a large pipeline are not."""
        in_progress = JobEvent(ts="2026-07-21T12:00:00+00:00",
            job_id=1, job_stage="test", job_name="build", old_status="created", status="running",
        )
        terminal = JobEvent(ts="2026-07-21T12:00:01+00:00",
            job_id=1, job_stage="test", job_name="build", old_status="running", status="success",
        )
        await render_lines(_events(in_progress, terminal, _RESULT), detail="normal")
        out = capsys.readouterr().out
        job_lines = [line for line in out.splitlines() if "[JOB]" in line]
        assert len(job_lines) == 1
        assert "running→success" in job_lines[0]

    async def test_detail_normal_shows_pipeline_and_switched_unconditionally(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Pipeline transitions and --follow rebinds are rare/low-noise
        compared to job transitions, so unlike "job" they aren't gated by
        terminal status at the normal level."""
        switched = SwitchedEvent(ts="2026-07-21T12:00:00+00:00", message="newer pipeline #2 found")
        await render_lines(_events(_PIPELINE, switched, _RESULT), detail="normal")
        out = capsys.readouterr().out
        assert "[PIPE]" in out
        assert "[WARN]" in out

    async def test_poll_summary_follows_all_transitions_in_a_tick(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The engine emits a distinct post-poll summary once the tick's
        transition burst is complete; transitions themselves stay concise."""
        job = JobEvent(ts="2026-07-21T12:00:00+00:00",
            job_id=1, job_stage="test", job_name="build", old_status="running", status="success",
            jobs_total=10, jobs_done=4, failed_jobs=("lint",), current_stage="test",
        )
        poll = PollEvent(ts="2026-07-21T12:00:00+00:00",
            jobs_total=10, jobs_done=4, failed_jobs=("lint",), current_stage="test",
        )
        await render_lines(_events(job, poll, _RESULT))
        out = capsys.readouterr().out
        job_line = next(line for line in out.splitlines() if "[JOB]" in line)
        poll_line = next(line for line in out.splitlines() if "[POLL]" in line)
        assert "4/10 jobs" not in job_line
        assert "1 failed" not in job_line
        assert "test" not in job_line
        assert "4/10 jobs" in poll_line
        assert "1 failed" in poll_line
        assert "test" in poll_line

    async def test_minimal_shows_poll_summary_but_not_transition(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        job = JobEvent(ts="2026-07-21T12:00:00+00:00",
            job_id=1, job_stage="test", job_name="build", old_status="running", status="success",
        )
        poll = PollEvent(ts="2026-07-21T12:00:00+00:00", jobs_total=10, jobs_done=4)
        await render_lines(_events(job, poll, _RESULT), detail="minimal")
        out = capsys.readouterr().out
        assert "[JOB]" not in out
        assert "[POLL]" in out

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
        long_job = JobEvent(ts="2026-07-21T12:00:00+00:00",
            job_id=1, job_stage="test", job_name="a" * 200, old_status="running", status="success",
        )
        await render_lines(_events(long_job, _RESULT))
        out = capsys.readouterr().out
        job_lines = [line for line in out.splitlines() if line and "[JOB]" in line]
        assert len(job_lines) == 1
        assert "a" * 200 in job_lines[0]


class TestLiveMarkup:
    def test_loading_state_before_jobs_are_known(self) -> None:
        """render_live's Rich Live only preserves its FINAL frame when
        captured non-interactively, so the 'loading jobs…' mid-run state
        (attach()'s early snapshot) can only be verified at this level."""
        e = SnapshotEvent(ts="x", pipeline_id=1, ref="main", status="running")
        markup = _live_markup(e, "normal")
        assert "None" not in markup
        assert "loading jobs" in markup

    def test_normal_state_shows_counts_and_stage_and_eta(self) -> None:
        e = SnapshotEvent(ts="x", pipeline_id=1, ref="main", status="running",
            jobs_total=40, jobs_done=18, failed_jobs=("a", "b"),
            current_stage="test", eta_seconds=90,
        )
        markup = _live_markup(e, "normal")
        assert "18/40 jobs" in markup
        assert "2 failed" in markup
        assert "test" in markup
        assert "left" in markup

    def test_detail_none_is_empty(self) -> None:
        """--detail none in live mode is the bare spinner with no text at
        all — the only content is the final state, shown separately via
        _final_renderable once the result event arrives."""
        e = SnapshotEvent(ts="x", pipeline_id=1, ref="main", status="running",
            jobs_total=40, jobs_done=18,
        )
        assert _live_markup(e, "none") == ""

    def test_detail_minimal_omits_stage_and_eta(self) -> None:
        e = SnapshotEvent(ts="x", pipeline_id=1, ref="main", status="running",
            jobs_total=40, jobs_done=18, current_stage="test", eta_seconds=90,
        )
        markup = _live_markup(e, "minimal")
        assert "18/40 jobs" in markup
        assert "test" not in markup
        assert "left" not in markup

    def test_detail_full_names_failed_jobs_instead_of_counting(self) -> None:
        e = SnapshotEvent(ts="x", pipeline_id=1, ref="main", status="running",
            jobs_total=40, jobs_done=18, failed_jobs=("build:unit", "lint:ruff"),
        )
        markup = _live_markup(e, "full")
        assert "2 failed" not in markup
        assert "build:unit" in markup
        assert "lint:ruff" in markup


class TestRenderLive:
    async def test_returns_result(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = await render_live(_events(_SNAPSHOT, _JOB, _RESULT))
        assert result is _RESULT

    async def test_switched_prints_warning_above_live_region(self, capsys: pytest.CaptureFixture[str]) -> None:
        switched = SwitchedEvent(ts="2026-07-21T12:00:00+00:00", pipeline_id=2, message="switching to #2")
        await render_live(_events(_SNAPSHOT, switched, _RESULT))
        out = capsys.readouterr().out
        assert "switching to #2" in out
