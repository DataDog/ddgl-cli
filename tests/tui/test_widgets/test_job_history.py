"""Tests for ddgl/tui/widgets/job_history.py — pure-function tests only."""
from __future__ import annotations

from ddgl.tui.widgets.job_history import _fmt_date


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
