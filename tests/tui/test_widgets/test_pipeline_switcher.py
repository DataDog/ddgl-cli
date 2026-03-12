"""Tests for ddgl/tui/widgets/pipeline_switcher.py."""
from __future__ import annotations

from ddgl.tui.widgets.pipeline_switcher import _fmt_ts


def test_fmt_ts_none() -> None:
    assert _fmt_ts("") == "—"


def test_fmt_ts_valid_iso() -> None:
    assert _fmt_ts("2024-03-15T14:32:00.000Z") == "14:32"


def test_fmt_ts_no_t_separator() -> None:
    assert _fmt_ts("2024-03-15") == "—"


def test_fmt_ts_short_string() -> None:
    assert _fmt_ts("T12:3") == "—"
