# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for ddgl/tui/widgets/job_history.py — pure-function tests only."""
from __future__ import annotations

from ddgl.constants import JobStatus
from ddgl.tui.widgets.job_history import _fmt_date, _status_cell

from .._stubs import make_job


def test_fmt_date_iso() -> None:
    assert _fmt_date("2026-03-12T14:30:00Z") == "Mar 12"


def test_fmt_date_january() -> None:
    assert _fmt_date("2026-01-05T00:00:00Z") == "Jan 5"


def test_fmt_date_december() -> None:
    assert _fmt_date("2025-12-25T08:00:00Z") == "Dec 25"


def test_fmt_date_empty() -> None:
    assert _fmt_date("") == "—"


def test_fmt_date_short_string() -> None:
    assert _fmt_date("2026") == "—"


# ---------------------------------------------------------------------------
# _status_cell
# ---------------------------------------------------------------------------


def test_status_cell_allowed_failure_shows_warning() -> None:
    job = make_job(id=1, status=JobStatus.FAILED, allow_failure=True)
    cell = _status_cell(job)
    assert cell.plain == "⚠ warning"
    assert cell.style == "#C17D10"


def test_status_cell_blocking_failure_unchanged() -> None:
    job = make_job(id=1, status=JobStatus.FAILED, allow_failure=False)
    cell = _status_cell(job)
    assert cell.plain == "✗ failed"
    assert cell.style == "#DD2B0E"
