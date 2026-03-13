from __future__ import annotations

from rich.rule import Rule
from rich.text import Text

from ddgl.format._parser import LogLine, Section, Trace
from ddgl.format._renderer import TraceOptions, render_trace


def _lines(trace: Trace, **kw: object) -> list[Text]:
    """Render and return only the Text items."""
    return [r for r in render_trace(trace, TraceOptions(**kw)) if isinstance(r, Text)]  # type: ignore[arg-type]


def _rules(trace: Trace, **kw: object) -> list[Rule]:
    return [r for r in render_trace(trace, TraceOptions(**kw)) if isinstance(r, Rule)]


class TestSectionRendering:
    def test_section_produces_rule(self) -> None:
        trace = Trace(children=[
            Section(name="build", start_ts=0, end_ts=5, duration=5, children=[
                LogLine(text="compiling", raw="compiling"),
            ]),
        ])
        result = render_trace(trace)
        rules = [r for r in result if isinstance(r, Rule)]
        assert len(rules) == 1
        assert "build" in rules[0].title  # type: ignore[operator]
        assert "5s" in rules[0].title  # type: ignore[operator]

    def test_sections_false_skips_rule(self) -> None:
        trace = Trace(children=[
            Section(name="build", start_ts=0, end_ts=5, duration=5, children=[
                LogLine(text="compiling", raw="compiling"),
            ]),
        ])
        rules = _rules(trace, sections=False)
        assert rules == []
        # But the child line is still rendered.
        lines = _lines(trace, sections=False)
        assert len(lines) == 1

    def test_nested_sections_produce_multiple_rules(self) -> None:
        inner = Section(name="inner", start_ts=1, end_ts=2, duration=1, children=[
            LogLine(text="deep", raw="deep"),
        ])
        outer = Section(name="outer", start_ts=0, end_ts=3, duration=3, children=[inner])
        trace = Trace(children=[outer])
        rules = _rules(trace)
        assert len(rules) == 2

    def test_collapsed_section_hides_children(self) -> None:
        trace = Trace(children=[
            Section(name="prepare", start_ts=0, end_ts=2, duration=2, collapsed=True, children=[
                LogLine(text="hidden setup", raw="hidden setup"),
            ]),
        ])
        result = render_trace(trace)
        rules = [r for r in result if isinstance(r, Rule)]
        lines = [r for r in result if isinstance(r, Text)]
        assert len(rules) == 1
        assert "prepare" in rules[0].title  # type: ignore[operator]
        assert lines == []  # children are hidden

    def test_collapsed_section_children_shown_when_sections_disabled(self) -> None:
        trace = Trace(children=[
            Section(name="prepare", start_ts=0, end_ts=2, duration=2, collapsed=True, children=[
                LogLine(text="visible", raw="visible"),
            ]),
        ])
        lines = _lines(trace, sections=False)
        assert len(lines) == 1
        assert lines[0].plain == "visible"

    def test_unclosed_section_no_duration_in_title(self) -> None:
        trace = Trace(children=[
            Section(name="hanging", start_ts=0, children=[
                LogLine(text="still going", raw="still going"),
            ]),
        ])
        rules = _rules(trace)
        assert len(rules) == 1
        # No "s" duration suffix.
        assert "None" not in rules[0].title  # type: ignore[operator]


class TestLineRendering:
    def test_strip_removes_noise(self) -> None:
        trace = Trace(children=[
            LogLine(text="output\r\x1b[0Kmore", raw="output\r\x1b[0Kmore"),
        ])
        lines = _lines(trace, strip=True)
        assert "\r" not in lines[0].plain
        assert "\x1b[0K" not in lines[0].plain

    def test_no_color_strips_ansi(self) -> None:
        trace = Trace(children=[
            LogLine(text="\x1b[32mGreen\x1b[0m", raw="\x1b[32mGreen\x1b[0m"),
        ])
        lines = _lines(trace, no_color=True)
        assert lines[0].plain == "Green"

    def test_color_preserved_from_ansi(self) -> None:
        trace = Trace(children=[
            LogLine(text="\x1b[32mGreen\x1b[0m", raw="\x1b[32mGreen\x1b[0m"),
        ])
        lines = _lines(trace, no_color=False)
        assert lines[0].plain == "Green"
        # Should have style spans from ANSI parsing.
        assert len(lines[0]._spans) > 0

    def test_timestamp_prepended_when_enabled(self) -> None:
        trace = Trace(children=[
            LogLine(text="hello", raw="14:30:00 hello", iso_timestamp="14:30:00"),
        ])
        lines = _lines(trace, timestamps=True)
        assert lines[0].plain.startswith("14:30:00")

    def test_timestamp_omitted_when_disabled(self) -> None:
        trace = Trace(children=[
            LogLine(text="hello", raw="14:30:00 hello", iso_timestamp="14:30:00"),
        ])
        lines = _lines(trace, timestamps=False)
        assert not lines[0].plain.startswith("14:30:00")


class TestHighlighting:
    def test_error_line_red(self) -> None:
        trace = Trace(children=[
            LogLine(text="fatal error occurred", raw="fatal error occurred"),
        ])
        lines = _lines(trace, highlight=True)
        assert any(s.style and "red" in str(s.style) for s in lines[0]._spans)

    def test_warning_line_yellow(self) -> None:
        trace = Trace(children=[
            LogLine(text="warning: deprecated", raw="warning: deprecated"),
        ])
        lines = _lines(trace, highlight=True)
        assert any(s.style and "yellow" in str(s.style) for s in lines[0]._spans)

    def test_highlight_disabled(self) -> None:
        trace = Trace(children=[
            LogLine(text="error happened", raw="error happened"),
        ])
        lines = _lines(trace, highlight=False)
        # No red spans added.
        assert not any(s.style and "red" in str(s.style) for s in lines[0]._spans)


class TestTraceOptionsRaw:
    def test_raw_preset(self) -> None:
        opts = TraceOptions.raw()
        assert opts.sections is False
        assert opts.strip is False
        assert opts.timestamps is False
        assert opts.highlight is False


class TestOutputTypes:
    def test_all_items_are_text_or_rule(self) -> None:
        trace = Trace(children=[
            Section(name="s", start_ts=0, end_ts=1, duration=1, children=[
                LogLine(text="line", raw="line"),
            ]),
            LogLine(text="top", raw="top"),
        ])
        result = render_trace(trace)
        for item in result:
            assert isinstance(item, (Text, Rule))
