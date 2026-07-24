# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Parser for GitLab CI job traces.

Single source of truth for all trace-related regexes. Builds a ``Trace``
tree from raw text that downstream renderers consume without re-parsing.
"""

from __future__ import annotations

import re

from ddgl.model.trace import LogLine, Section, Stream, Trace

# ── regexes ──────────────────────────────────────────────────────────────────

# Section markers (matched against the line body after stripping prefix/noise).
_SECTION_START_RE = re.compile(
    r"section_start:(\d+):(\S+)"
)
_SECTION_END_RE = re.compile(
    r"section_end:(\d+):(\S+)"
)
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\.\d+Z) ")
_STREAM_RE = re.compile(r"^([0-9a-fA-F]{2})([OE])([ +])")
_NOISE_RE = re.compile(r"\r|\x1b\[0K")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHFABCDsuhl]")


# ── public helpers ───────────────────────────────────────────────────────────


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


# ── parser ───────────────────────────────────────────────────────────────────


def parse_trace(text: str) -> Trace:
    """Parse raw GitLab CI trace text into a :class:`Trace` tree.

    Processes the trace line-by-line:
    1. Strip noise (``\\r``, ``\\x1b[0K``)
    2. Strip timestamp prefix
    3. Strip stream marker (00O, 01E, etc.)
    4. Check for section markers
    5. Otherwise create a :class:`LogLine`
    """
    trace = Trace()
    if not text:
        return trace

    stack: list[Section] = []

    def _target() -> list[Section | LogLine]:
        return stack[-1].children if stack else trace.children

    for raw_line in text.splitlines():
        cleaned = _NOISE_RE.sub("", raw_line)
        if not cleaned:
            continue

        # Step 1: strip ISO timestamp prefix.
        ts_match = _TIMESTAMP_RE.match(cleaned)
        short_ts: str | None = None
        body = cleaned
        if ts_match:
            short_ts = ts_match.group(2)
            body = cleaned[ts_match.end():]

        # Step 2: strip stream marker (00O, 01E, etc.).
        stream: Stream | None = None
        stream_id: int | None = None
        continuation = False
        stream_match = _STREAM_RE.match(body)
        if stream_match:
            stream = Stream(stream_match.group(2))
            stream_id = int(stream_match.group(1), 16)
            continuation = stream_match.group(3) == "+"
            body = body[stream_match.end():]

        # Strip any remaining ANSI noise from the body prefix before
        # checking section markers (e.g. "[0K" remnants).
        body_for_match = _ANSI_RE.sub("", body)

        # Check for section markers.
        start = _SECTION_START_RE.match(body_for_match)
        if start:
            raw_name = start.group(2)
            collapsed = "collapsed=true" in raw_name
            name = re.sub(r"\[.*?\]", "", raw_name).strip()
            section = Section(
                name=name,
                start_ts=int(start.group(1)),
                collapsed=collapsed,
            )
            _target().append(section)
            stack.append(section)
            continue

        end = _SECTION_END_RE.match(body_for_match)
        if end:
            if stack:
                top = stack[-1]
                top.end_ts = int(end.group(1))
                top.duration = top.end_ts - top.start_ts
                stack.pop()
            continue

        # Regular log line.
        _target().append(LogLine(
            text=body, raw=raw_line, iso_timestamp=short_ts,
            stream=stream, stream_id=stream_id, continuation=continuation,
        ))

    return trace
