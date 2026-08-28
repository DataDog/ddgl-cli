# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for ddgl/tui/widgets/status.py."""
from __future__ import annotations

import pytest
from rich.text import Text

from ddgl.constants import JobStatus
from ddgl.model.job import Job
from ddgl.tui.widgets.status import (
    job_status_color,
    job_status_icon,
    job_status_label,
    status_color,
    status_icon,
    status_text,
)


def _make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = {
        "id": 1, "name": "build", "stage": "build",
        "status": JobStatus.FAILED, "pipeline_id": 1,
    }
    return Job(**(defaults | overrides))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "status,expected",
    [
        ("success", "✓"),
        ("failed", "✗"),
        ("running", "●"),
        ("pending", "○"),
        ("canceled", "⊘"),
        ("skipped", "→"),
        ("manual", "▶"),
        ("created", "○"),
        ("preparing", "◌"),
        ("waiting_for_resource", "◌"),
        ("waiting_for_callback", "◌"),
        ("scheduled", "⏱"),
        ("canceling", "⊘"),
    ],
)
def test_status_icon(status: str, expected: str) -> None:
    assert status_icon(status) == expected


@pytest.mark.parametrize(
    "status,expected",
    [
        ("success", "#2DA160"),
        ("failed", "#DD2B0E"),
        ("running", "#1F75CB"),
        ("pending", "#C17D10"),
        ("canceled", "#737278"),
        ("skipped", "#737278"),
        ("manual", "#6B4FBB"),
        ("created", "#AAAAAA"),
        ("preparing", "#1F75CB"),
        ("waiting_for_resource", "#C17D10"),
        ("waiting_for_callback", "#C17D10"),
        ("scheduled", "#6B4FBB"),
        ("canceling", "#C17D10"),
    ],
)
def test_status_color(status: str, expected: str) -> None:
    assert status_color(status) == expected


def test_status_icon_unknown() -> None:
    assert status_icon("totally_unknown") == "?"


def test_status_color_unknown() -> None:
    assert status_color("totally_unknown") == "white"


def test_status_text_returns_rich_text() -> None:
    result = status_text("success")
    assert isinstance(result, Text)


def test_status_text_contains_status_and_icon() -> None:
    result = status_text("failed")
    assert "✗" in result.plain
    assert "failed" in result.plain


def test_status_text_unknown_has_question_mark() -> None:
    result = status_text("unknown")
    assert "?" in result.plain


class TestJobStatusIconColor:
    def test_allowed_failure_gets_warning_glyph(self) -> None:
        job = _make_job(status=JobStatus.FAILED, allow_failure=True)
        assert job_status_icon(job) == "⚠"

    def test_allowed_failure_gets_warning_color(self) -> None:
        job = _make_job(status=JobStatus.FAILED, allow_failure=True)
        assert job_status_color(job) == "#C17D10"

    def test_blocking_failure_unchanged(self) -> None:
        job = _make_job(status=JobStatus.FAILED, allow_failure=False)
        assert job_status_icon(job) == "✗"
        assert job_status_color(job) == "#DD2B0E"

    def test_non_failed_job_with_allow_failure_unaffected(self) -> None:
        job = _make_job(status=JobStatus.SUCCESS, allow_failure=True)
        assert job_status_icon(job) == "✓"
        assert job_status_color(job) == "#2DA160"


class TestJobStatusLabel:
    def test_allowed_failure_shows_warning(self) -> None:
        job = _make_job(status=JobStatus.FAILED, allow_failure=True)
        assert job_status_label(job) == "warning"

    def test_blocking_failure_shows_failed(self) -> None:
        job = _make_job(status=JobStatus.FAILED, allow_failure=False)
        assert job_status_label(job) == "failed"

    def test_non_failed_job_with_allow_failure_shows_own_status(self) -> None:
        job = _make_job(status=JobStatus.SUCCESS, allow_failure=True)
        assert job_status_label(job) == "success"
