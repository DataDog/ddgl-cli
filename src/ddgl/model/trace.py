# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tree-based IR for GitLab CI job traces.

Pure data types — no parsing logic. Used by ``ddgl.format._parser``
to build the tree, and by renderers to consume it.
"""

from __future__ import annotations

from enum import Enum

import msgspec


class Stream(Enum):
    """GitLab semantic log stream type."""

    STDOUT = "O"
    STDERR = "E"


class LogLine(msgspec.Struct):
    """A single line of log output."""

    text: str  # line body (noise stripped, ANSI preserved)
    raw: str  # original line verbatim
    iso_timestamp: str | None = None  # HH:MM:SS from leading ISO-8601 timestamp
    stream: Stream | None = None  # from 00O/01E marker
    stream_id: int | None = None  # executor stream id (00=executor, 01=script, …)
    continuation: bool = False  # True when append flag is "+" (continuation of previous line)


class Section(msgspec.Struct):
    """A GitLab CI section (may nest)."""

    name: str
    start_ts: int  # unix seconds from section_start marker
    end_ts: int | None = None  # unix seconds from section_end marker
    duration: int | None = None  # end_ts - start_ts
    collapsed: bool = False  # [collapsed=true] metadata
    children: list[Section | LogLine] = []


TraceNode = Section | LogLine


class Trace(msgspec.Struct):
    """Root of the IR tree."""

    children: list[TraceNode] = []
