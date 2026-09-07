# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/render/_styles.py."""
from __future__ import annotations

from ddgl.constants import JobStatus
from ddgl.model.job import Job
from ddgl.render._styles import format_job_status


def _make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = {
        "id": 1, "name": "build", "stage": "build",
        "status": JobStatus.FAILED, "pipeline_id": 1,
    }
    return Job(**(defaults | overrides))  # type: ignore[arg-type]


class TestFormatJobStatus:
    def test_allowed_failure_renders_as_warning(self) -> None:
        job = _make_job(status=JobStatus.FAILED, allow_failure=True)
        text = format_job_status(job)
        assert "orange1" in text
        assert "warning" in text
        assert "red" not in text

    def test_blocking_failure_renders_red(self) -> None:
        job = _make_job(status=JobStatus.FAILED, allow_failure=False)
        text = format_job_status(job)
        assert "red" in text
        assert text.endswith("failed")
        assert "warning" not in text

    def test_success_unaffected_by_allow_failure(self) -> None:
        job = _make_job(status=JobStatus.SUCCESS, allow_failure=True)
        text = format_job_status(job)
        assert "green" in text
        assert "success" in text
