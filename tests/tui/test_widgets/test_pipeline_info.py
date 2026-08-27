# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for ddgl/tui/widgets/pipeline_info.py."""
from __future__ import annotations

from rich.text import Text

from ddgl.constants import JobStatus, PipelineStatus
from ddgl.tui.widgets.pipeline_info import _render, _render_job_stats

from .._stubs import make_job, make_pipeline


def test_render_does_not_repeat_pipeline_id() -> None:
    # Pipeline ID is shown in the border title, not duplicated in the body.
    p = make_pipeline(id=42)
    result = _render(p, [])
    assert "Pipeline #42" not in result.plain


def test_render_contains_ref() -> None:
    p = make_pipeline(ref="my-branch")
    result = _render(p, [])
    assert "my-branch" in result.plain


def test_render_contains_status() -> None:
    p = make_pipeline(status=PipelineStatus.FAILED)
    result = _render(p, [])
    assert "failed" in result.plain


def test_render_status_uses_icon() -> None:
    p = make_pipeline(status=PipelineStatus.SUCCESS)
    result = _render(p, [])
    assert "✓" in result.plain


def test_render_shows_sha_truncated() -> None:
    p = make_pipeline(sha="abcdef1234567890")
    result = _render(p, [])
    assert "abcdef123456" in result.plain
    assert "7890" not in result.plain


def test_render_sha_missing() -> None:
    p = make_pipeline(sha="")
    result = _render(p, [])
    assert "—" in result.plain


def test_render_shows_source() -> None:
    p = make_pipeline(source="push")
    result = _render(p, [])
    assert "push" in result.plain


def test_render_source_missing() -> None:
    p = make_pipeline(source="")
    result = _render(p, [])
    assert "—" in result.plain


def test_render_shows_url_when_present() -> None:
    p = make_pipeline(web_url="https://gitlab.example.com/proj/-/pipelines/1")
    result = _render(p, [])
    assert "https://gitlab.example.com" in result.plain


def test_render_omits_url_when_absent() -> None:
    p = make_pipeline(web_url="")
    result = _render(p, [])
    assert "URL" not in result.plain


def test_render_returns_rich_text() -> None:
    p = make_pipeline()
    assert isinstance(_render(p, []), Text)


# ---------------------------------------------------------------------------
# _render_job_stats
# ---------------------------------------------------------------------------


def test_render_job_stats_shows_counts() -> None:
    jobs = [
        make_job(status=JobStatus.SUCCESS),
        make_job(status=JobStatus.SUCCESS),
        make_job(status=JobStatus.FAILED),
    ]
    text = _render_job_stats(jobs).plain
    assert "2" in text  # two successes
    assert "1" in text  # one failure


def test_render_job_stats_uses_icons() -> None:
    jobs = [make_job(status=JobStatus.SUCCESS), make_job(status=JobStatus.FAILED)]
    text = _render_job_stats(jobs).plain
    assert "✓" in text
    assert "✗" in text


def test_render_job_stats_omits_zero_counts() -> None:
    jobs = [make_job(status=JobStatus.SUCCESS)]
    text = _render_job_stats(jobs).plain
    assert "✗" not in text  # no failures


def test_render_job_stats_allowed_failure_gets_own_bucket() -> None:
    jobs = [
        make_job(status=JobStatus.FAILED, allow_failure=True),
        make_job(status=JobStatus.FAILED, allow_failure=True),
        make_job(status=JobStatus.FAILED, allow_failure=False),
    ]
    text = _render_job_stats(jobs).plain
    assert "⚠ 2" in text  # two allowed failures, counted separately
    assert "✗ 1" in text  # one blocking failure, unaffected


def test_render_with_jobs_includes_stats() -> None:
    p = make_pipeline()
    jobs = [make_job(status=JobStatus.SUCCESS)]
    text = _render(p, jobs).plain
    assert "✓" in text
