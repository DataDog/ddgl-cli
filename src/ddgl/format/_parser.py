"""Tree-based IR and parser for GitLab CI job traces.

Single source of truth for all trace-related regexes. Produces a ``Trace``
tree that downstream renderers consume without re-parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── regexes ──────────────────────────────────────────────────────────────────

_SECTION_START_RE = re.compile(
    r"^section_start:(\d+):([^\r\n]+?)\r?\x1b\[0K", re.MULTILINE
)
_SECTION_END_RE = re.compile(
    r"^section_end:(\d+):([^\r\n]+?)\r?\x1b\[0K", re.MULTILINE
)
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\.\d+Z) (?:([0-9a-fA-F]{2})([OE])([ +]))?")
_NOISE_RE = re.compile(r"\r|\x1b\[0K")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHFABCDsuhl]")

# ── IR nodes ─────────────────────────────────────────────────────────────────

TraceNode = "Section | LogLine"


@dataclass
class LogLine:
    """A single line of log output."""

    text: str  # line body (noise stripped, ANSI preserved)
    raw: str  # original line verbatim
    iso_timestamp: str | None = None  # HH:MM:SS from leading ISO-8601 timestamp
    stream: str | None = None  # "stdout" or "stderr" (from 00O/01E marker)
    stream_id: int | None = None  # executor stream id (00=executor, 01=script, …)
    continuation: bool = False  # True when append flag is "+" (continuation of previous line)


@dataclass
class Section:
    """A GitLab CI section (may nest)."""

    name: str
    start_ts: int  # unix seconds from section_start marker
    end_ts: int | None = None  # unix seconds from section_end marker
    duration: int | None = None  # end_ts - start_ts
    collapsed: bool = False  # [collapsed=true] metadata
    children: list[Section | LogLine] = field(default_factory=list)


@dataclass
class Trace:
    """Root of the IR tree."""

    children: list[Section | LogLine] = field(default_factory=list)


# ── public helpers ───────────────────────────────────────────────────────────


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


# ── parser ───────────────────────────────────────────────────────────────────


def parse_trace(text: str) -> Trace:
    """Parse raw GitLab CI trace text into a :class:`Trace` tree.

    Section markers are consumed; regular lines become :class:`LogLine` nodes
    placed inside the innermost open :class:`Section` (or at the top level).
    """
    trace = Trace()
    if not text:
        return trace

    # Build an index of section markers with their spans.
    events: list[tuple[int, int, str, dict]] = []  # (start, end, kind, data)

    for m in _SECTION_START_RE.finditer(text):
        ts = int(m.group(1))
        raw_name = m.group(2)
        # Parse optional metadata like [collapsed=true]
        collapsed = "[collapsed=true]" in raw_name
        # The section name is everything before any bracket metadata.
        name = re.sub(r"\[.*?\]", "", raw_name).strip()
        events.append((m.start(), m.end(), "start", {"ts": ts, "name": name, "collapsed": collapsed}))

    for m in _SECTION_END_RE.finditer(text):
        ts = int(m.group(1))
        name = m.group(2).strip()
        events.append((m.start(), m.end(), "end", {"ts": ts, "name": name}))

    # Sort events by position in the text.
    events.sort(key=lambda e: e[0])

    # Walk through the text, splitting at event boundaries.
    stack: list[Section] = []
    pos = 0

    def _current_children() -> list[Section | LogLine]:
        return stack[-1].children if stack else trace.children

    for ev_start, ev_end, kind, data in events:
        # Process any text between pos and this event.
        if ev_start > pos:
            _ingest_lines(text[pos:ev_start], _current_children())

        if kind == "start":
            section = Section(
                name=data["name"],
                start_ts=data["ts"],
                collapsed=data["collapsed"],
            )
            _current_children().append(section)
            stack.append(section)
        else:  # "end"
            if stack:
                top = stack[-1]
                top.end_ts = data["ts"]
                top.duration = top.end_ts - top.start_ts
                stack.pop()

        pos = ev_end

    # Remaining text after the last event.
    if pos < len(text):
        _ingest_lines(text[pos:], _current_children())

    return trace


def _ingest_lines(chunk: str, target: list[Section | LogLine]) -> None:
    """Split *chunk* into lines and append :class:`LogLine` nodes to *target*."""
    for raw_line in chunk.splitlines():
        cleaned = _NOISE_RE.sub("", raw_line)
        if not cleaned:
            continue
        ts_match = _TIMESTAMP_RE.match(cleaned)
        short_ts: str | None = None
        stream: str | None = None
        stream_id: int | None = None
        continuation = False
        body = cleaned
        if ts_match:
            short_ts = ts_match.group(2)  # HH:MM:SS only
            if ts_match.group(4):  # stream flag present
                stream = "stderr" if ts_match.group(4) == "E" else "stdout"
                stream_id = int(ts_match.group(3), 16)
                continuation = ts_match.group(5) == "+"
            body = cleaned[ts_match.end() :]
        target.append(LogLine(
            text=body, raw=raw_line, iso_timestamp=short_ts,
            stream=stream, stream_id=stream_id, continuation=continuation,
        ))
