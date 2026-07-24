# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Unified trace parsing and rendering for GitLab CI job logs.

Public API
----------
parse_trace   — raw text → Trace IR tree
render_trace  — Trace → list[RenderableType]
format_trace  — raw text → list[RenderableType]  (convenience)
to_json        — raw text → str  (JSON tree)
strip_ansi    — remove ANSI escapes from text
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec

from ddgl.format._parser import parse_trace, strip_ansi
from ddgl.format._renderer import TraceOptions, render_trace
from ddgl.model.trace import LogLine, Section, Trace, TraceNode

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
    "to_json",
]

_encoder = msgspec.json.Encoder()


def format_trace(
    text: str,
    options: TraceOptions | None = None,
) -> list[RenderableType]:
    """Parse raw trace text and render to Rich renderables."""
    trace = parse_trace(text)
    return render_trace(trace, options or TraceOptions())


def to_json(text: str) -> str:
    """Parse trace and return the IR tree as JSON."""
    trace = parse_trace(text)
    return _encoder.encode(trace).decode()
