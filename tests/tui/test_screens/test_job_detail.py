"""Tests for ddgl/tui/screens/job_detail.py — pure-function tests only."""
from __future__ import annotations

import msgspec
from rich.text import Text

from ddgl.constants import JobStatus
from ddgl.tui.screens.job_detail import (
    _fmt_duration,
    _render_meta,
    _walk_trace,
)
from ddgl.tui.search import apply_search

from .._stubs import make_job

# ---------------------------------------------------------------------------
# Local stubs for trace IR (avoids coupling tests to production constructors)
# ---------------------------------------------------------------------------


def _logline(text: str, *, raw: str = "", iso_timestamp: str | None = None):
    """Minimal LogLine stub."""
    from ddgl.model.trace import LogLine

    return LogLine(text=text, raw=raw or text, iso_timestamp=iso_timestamp)


def _section(
    name: str,
    *,
    duration: int | None = None,
    collapsed: bool = False,
    children=None,
):
    """Minimal Section stub."""
    from ddgl.model.trace import Section

    return Section(
        name=name,
        start_ts=0,
        duration=duration,
        collapsed=collapsed,
        children=children or [],
    )


def _trace(*children):
    from ddgl.model.trace import Trace

    return Trace(children=list(children))

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


# ---------------------------------------------------------------------------
# apply_search (replaces _highlight_text)
# ---------------------------------------------------------------------------


def test_highlight_no_match_returns_original() -> None:
    original = Text("hello world")
    result, spans = apply_search(original, "xyz")
    assert result is original
    assert spans == []


def test_highlight_match_returns_copy() -> None:
    original = Text("hello world")
    result, spans = apply_search(original, "world")
    assert result is not original
    assert result.plain == original.plain
    assert len(spans) == 1


def test_highlight_preserves_plain_text() -> None:
    original = Text("error: build failed")
    result, _ = apply_search(original, "error")
    assert result.plain == "error: build failed"


def test_highlight_case_insensitive() -> None:
    original = Text("ERROR: build FAILED")
    result, spans = apply_search(original, "error")
    assert result is not original
    assert len(spans) == 1


def test_highlight_multiple_matches() -> None:
    original = Text("foo bar foo baz foo")
    result, spans = apply_search(original, "foo")
    assert len(spans) == 3


def test_highlight_regex_mode() -> None:
    original = Text("error: timeout after 30s")
    result, spans = apply_search(original, r"error.*\d+s", regex=True)
    assert len(spans) == 1


def test_highlight_invalid_regex_returns_no_matches() -> None:
    original = Text("hello world")
    result, spans = apply_search(original, "[invalid", regex=True)
    assert result is original
    assert spans == []


# ---------------------------------------------------------------------------
# _walk_trace
# ---------------------------------------------------------------------------


def test_walk_trace_empty() -> None:
    result = _walk_trace(_trace())
    assert result == []


def test_walk_trace_single_logline() -> None:
    result = _walk_trace(_trace(_logline("hello")))
    assert len(result) == 1
    assert result[0].plain == "hello"


def test_walk_trace_ansi_in_logline_preserved() -> None:
    result = _walk_trace(_trace(_logline("\x1b[31mred\x1b[0m")))
    assert result[0].plain == "red"


def test_walk_trace_section_header_contains_name() -> None:
    result = _walk_trace(_trace(_section("build")))
    assert len(result) == 1
    assert "build" in result[0].plain


def test_walk_trace_section_header_contains_duration() -> None:
    result = _walk_trace(_trace(_section("build", duration=42)))
    assert "42s" in result[0].plain


def test_walk_trace_section_children_included() -> None:
    sec = _section("build", children=[_logline("output")])
    result = _walk_trace(_trace(sec))
    assert len(result) == 2
    assert any("output" in t.plain for t in result)


def test_walk_trace_collapsed_section_skips_children() -> None:
    sec = _section("prepare", collapsed=True, children=[_logline("hidden")])
    result = _walk_trace(_trace(sec))
    assert len(result) == 1
    assert "prepare" in result[0].plain
    assert not any("hidden" in t.plain for t in result)


def test_walk_trace_timestamp_prefix() -> None:
    result = _walk_trace(_trace(_logline("msg", iso_timestamp="12:34:56")))
    assert "12:34:56" in result[0].plain
    assert "msg" in result[0].plain


def test_walk_trace_nested_section() -> None:
    inner_line = _logline("inner output")
    inner = _section("inner-sec", children=[inner_line])
    outer = _section("outer-sec", children=[inner])
    result = _walk_trace(_trace(outer))
    # outer header + inner header + inner log line
    assert len(result) == 3
    plains = [t.plain for t in result]
    assert any("outer-sec" in p for p in plains)
    assert any("inner-sec" in p for p in plains)
    assert any("inner output" in p for p in plains)


def test_walk_trace_multiple_top_level_nodes() -> None:
    result = _walk_trace(_trace(_logline("a"), _logline("b"), _logline("c")))
    assert len(result) == 3
    assert [t.plain for t in result] == ["a", "b", "c"]
