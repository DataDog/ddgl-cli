"""Tests for ddgl/tui/screens/job_detail.py — pure-function tests only."""
from __future__ import annotations

from rich.text import Text

from ddgl.constants import JobStatus
from ddgl.tui.screens.job_detail import _fmt_duration, _render_meta

from .._stubs import make_job

# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------


def test_fmt_duration_none() -> None:
    assert _fmt_duration(None) == "—"


def test_fmt_duration_zero() -> None:
    assert _fmt_duration(0.0) == "0m 0s"


def test_fmt_duration_seconds_only() -> None:
    assert _fmt_duration(45.0) == "0m 45s"


def test_fmt_duration_minutes_and_seconds() -> None:
    assert _fmt_duration(252.0) == "4m 12s"


# ---------------------------------------------------------------------------
# _render_meta — returns Text
# ---------------------------------------------------------------------------


def test_render_meta_returns_text() -> None:
    job = make_job()
    result = _render_meta(job)
    assert isinstance(result, Text)


def test_render_meta_contains_name() -> None:
    job = make_job(name="build-image")
    plain = _render_meta(job).plain
    assert "build-image" in plain


def test_render_meta_contains_stage() -> None:
    job = make_job(stage="build")
    plain = _render_meta(job).plain
    assert "build" in plain


def test_render_meta_contains_status() -> None:
    job = make_job(status=JobStatus.FAILED)
    plain = _render_meta(job).plain
    assert "failed" in plain


def test_render_meta_contains_status_icon() -> None:
    job = make_job(status=JobStatus.FAILED)
    plain = _render_meta(job).plain
    assert "✗" in plain


def test_render_meta_contains_duration() -> None:
    job = make_job(duration=252.0)
    plain = _render_meta(job).plain
    assert "4m 12s" in plain


def test_render_meta_shows_failure_reason() -> None:
    job = make_job(status=JobStatus.FAILED, failure_reason="script_failure")
    plain = _render_meta(job).plain
    assert "script_failure" in plain


def test_render_meta_omits_failure_reason_when_none() -> None:
    job = make_job()
    plain = _render_meta(job).plain
    assert "Reason" not in plain


def test_render_meta_shows_url() -> None:
    job = make_job(web_url="https://gitlab.com/j/123")
    plain = _render_meta(job).plain
    assert "gitlab.com" in plain


def test_render_meta_omits_url_when_empty() -> None:
    job = make_job(web_url="")
    plain = _render_meta(job).plain
    assert "URL" not in plain


def test_render_meta_shows_runner_info() -> None:
    job = make_job(
        runner_description="shared-runner-01",
        runner_tags=("docker", "linux"),
    )
    plain = _render_meta(job).plain
    assert "shared-runner-01" in plain
    assert "docker" in plain


def test_render_meta_omits_runner_when_none() -> None:
    job = make_job()
    plain = _render_meta(job).plain
    assert "Runner" not in plain


def test_render_meta_shows_queued_duration() -> None:
    job = make_job(queued_duration=65.0)
    plain = _render_meta(job).plain
    assert "Queued" in plain
    assert "1m 5s" in plain


def test_render_meta_omits_queued_when_none() -> None:
    job = make_job()
    plain = _render_meta(job).plain
    assert "Queued" not in plain
