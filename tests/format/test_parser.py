from __future__ import annotations

from ddgl.format._parser import LogLine, Section, Trace, parse_trace, strip_ansi


class TestStripAnsi:
    def test_removes_color_codes(self) -> None:
        assert strip_ansi("\x1b[32mGreen\x1b[0m") == "Green"

    def test_removes_cursor_codes(self) -> None:
        assert strip_ansi("\x1b[2K\x1b[1Atext") == "text"

    def test_noop_on_plain_text(self) -> None:
        assert strip_ansi("hello world") == "hello world"


class TestParseTraceEmpty:
    def test_empty_string(self) -> None:
        trace = parse_trace("")
        assert trace.children == []

    def test_whitespace_only(self) -> None:
        trace = parse_trace("   \n  \n")
        # Whitespace-only lines are still LogLines.
        assert all(isinstance(c, LogLine) for c in trace.children)


class TestParseTraceSections:
    def test_single_section(self) -> None:
        raw = (
            "section_start:100:build\r\x1b[0K\n"
            "compiling...\n"
            "section_end:105:build\r\x1b[0K\n"
        )
        trace = parse_trace(raw)
        assert len(trace.children) == 1
        sec = trace.children[0]
        assert isinstance(sec, Section)
        assert sec.name == "build"
        assert sec.start_ts == 100
        assert sec.end_ts == 105
        assert sec.duration == 5
        assert sec.collapsed is False
        assert len(sec.children) == 1
        assert isinstance(sec.children[0], LogLine)
        assert sec.children[0].text == "compiling..."

    def test_multiple_sections(self) -> None:
        raw = (
            "section_start:1:build\r\x1b[0K\n"
            "compiling\n"
            "section_end:2:build\r\x1b[0K\n"
            "section_start:3:test\r\x1b[0K\n"
            "testing\n"
            "section_end:4:test\r\x1b[0K\n"
        )
        trace = parse_trace(raw)
        assert len(trace.children) == 2
        assert trace.children[0].name == "build"  # type: ignore[union-attr]
        assert trace.children[1].name == "test"  # type: ignore[union-attr]

    def test_collapsed_section(self) -> None:
        raw = (
            "section_start:10:prepare[collapsed=true]\r\x1b[0K\n"
            "setup\n"
            "section_end:12:prepare\r\x1b[0K\n"
        )
        trace = parse_trace(raw)
        sec = trace.children[0]
        assert isinstance(sec, Section)
        assert sec.name == "prepare"
        assert sec.collapsed is True

    def test_nested_sections(self) -> None:
        raw = (
            "section_start:1:outer\r\x1b[0K\n"
            "before inner\n"
            "section_start:2:inner\r\x1b[0K\n"
            "deep\n"
            "section_end:3:inner\r\x1b[0K\n"
            "after inner\n"
            "section_end:4:outer\r\x1b[0K\n"
        )
        trace = parse_trace(raw)
        assert len(trace.children) == 1
        outer = trace.children[0]
        assert isinstance(outer, Section)
        assert outer.name == "outer"
        # outer has: LogLine("before inner"), Section("inner"), LogLine("after inner")
        assert len(outer.children) == 3
        assert isinstance(outer.children[0], LogLine)
        assert outer.children[0].text == "before inner"
        inner = outer.children[1]
        assert isinstance(inner, Section)
        assert inner.name == "inner"
        assert len(inner.children) == 1
        assert inner.children[0].text == "deep"
        assert isinstance(outer.children[2], LogLine)
        assert outer.children[2].text == "after inner"

    def test_unclosed_section(self) -> None:
        raw = (
            "section_start:10:hanging\r\x1b[0K\n"
            "still going\n"
        )
        trace = parse_trace(raw)
        sec = trace.children[0]
        assert isinstance(sec, Section)
        assert sec.end_ts is None
        assert sec.duration is None
        assert len(sec.children) == 1


class TestParseTraceLines:
    def test_lines_outside_sections(self) -> None:
        raw = "hello\nworld\n"
        trace = parse_trace(raw)
        assert len(trace.children) == 2
        assert all(isinstance(c, LogLine) for c in trace.children)
        assert trace.children[0].text == "hello"
        assert trace.children[1].text == "world"

    def test_noise_stripped_from_lines(self) -> None:
        raw = "some output\r\x1b[0Kmore\n"
        trace = parse_trace(raw)
        # \r and \x1b[0K should be stripped from line text.
        line = trace.children[0]
        assert isinstance(line, LogLine)
        assert "\r" not in line.text
        assert "\x1b[0K" not in line.text

    def test_ansi_preserved_in_line_text(self) -> None:
        raw = "\x1b[32mGreen\x1b[0m\n"
        trace = parse_trace(raw)
        line = trace.children[0]
        assert isinstance(line, LogLine)
        assert "\x1b[32m" in line.text

    def test_iso_timestamp_extracted(self) -> None:
        raw = "2025-01-15T10:30:45.123Z compiling foo.c\n"
        trace = parse_trace(raw)
        line = trace.children[0]
        assert isinstance(line, LogLine)
        assert line.iso_timestamp == "2025-01-15T10:30:45.123Z"
        assert line.text == "compiling foo.c"

    def test_no_timestamp(self) -> None:
        raw = "just a regular line\n"
        trace = parse_trace(raw)
        line = trace.children[0]
        assert isinstance(line, LogLine)
        assert line.iso_timestamp is None
        assert line.text == "just a regular line"

    def test_raw_preserved(self) -> None:
        raw = "\x1b[32mcolored output\x1b[0m\n"
        trace = parse_trace(raw)
        line = trace.children[0]
        assert isinstance(line, LogLine)
        # raw keeps the original line (without the trailing newline from splitlines)
        assert line.raw == "\x1b[32mcolored output\x1b[0m"
