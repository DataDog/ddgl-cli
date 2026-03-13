"""Unified trace parsing and rendering for GitLab CI job logs.

Public API
----------
parse_trace   — raw text → Trace IR tree
render_trace  — Trace → list[RenderableType]
format_trace  — raw text → list[RenderableType]  (convenience)
to_json_lines — raw text → Iterator[str]  (JSON Lines)
strip_ansi    — remove ANSI escapes from text
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ddgl.format._parser import LogLine, Section, Trace, TraceNode, parse_trace, strip_ansi
from ddgl.format._renderer import TraceOptions, render_trace

if TYPE_CHECKING:
    from rich.console import RenderableType

__all__ = [
    "LogLine",
    "Section",
    "Trace",
    "TraceNode",
    "TraceOptions",
    "format_trace",
    "parse_trace",
    "render_trace",
    "strip_ansi",
    "to_json_lines",
]


def format_trace(
    text: str,
    options: TraceOptions | None = None,
) -> list[RenderableType]:
    """Parse raw trace text and render to Rich renderables."""
    trace = parse_trace(text)
    return render_trace(trace, options or TraceOptions())


def to_json_lines(text: str) -> Iterator[str]:
    """Parse trace and yield one JSON string per event."""
    trace = parse_trace(text)
    yield from _walk_json(trace.children, section_path=[])


def _walk_json(
    nodes: list[Section | LogLine],
    section_path: list[str],
) -> Iterator[str]:
    for node in nodes:
        if isinstance(node, Section):
            yield json.dumps({
                "type": "section_start",
                "name": node.name,
                "section": "/".join(section_path + [node.name]),
                "start_ts": node.start_ts,
                "collapsed": node.collapsed,
            })
            yield from _walk_json(node.children, section_path + [node.name])
            yield json.dumps({
                "type": "section_end",
                "name": node.name,
                "section": "/".join(section_path + [node.name]),
                "end_ts": node.end_ts,
                "duration": node.duration,
            })
        else:
            record: dict[str, object] = {
                "type": "line",
                "text": node.text,
            }
            if section_path:
                record["section"] = "/".join(section_path)
            if node.iso_timestamp:
                record["timestamp"] = node.iso_timestamp
            yield json.dumps(record)
