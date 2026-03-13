"""Render a :class:`Trace` tree to Rich renderables."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

import msgspec
from rich.rule import Rule
from rich.text import Text

from ddgl.format._parser import _ANSI_RE, _NOISE_RE
from ddgl.model.trace import LogLine, Section, Trace

if TYPE_CHECKING:
    from rich.console import RenderableType

_ERROR_RE = re.compile(r"\b(?:error|fatal|exception|traceback)\b", re.IGNORECASE)
_WARN_RE = re.compile(r"\b(?:warn(?:ing)?)\b", re.IGNORECASE)

_SECTION_COLOR = "dark_orange"
_INDENT = "  "


class TraceOptions(msgspec.Struct):
    """Controls how a :class:`Trace` is rendered to Rich renderables."""

    sections: bool = True  # section markers → Rule headers
    strip: bool = True  # remove noise sequences from log lines
    timestamps: bool = True  # dim leading ISO timestamp
    highlight: bool = True  # colour error/warning lines
    color: bool = True  # preserve ANSI colour from log content

    @classmethod
    def raw(cls) -> TraceOptions:
        return cls(sections=False, strip=False, timestamps=False, highlight=False)


def render_trace(
    trace: Trace,
    options: TraceOptions | None = None,
) -> list[RenderableType]:
    """Walk *trace* and produce a flat list of Rich renderables."""
    opts = options or TraceOptions()
    return list(_walk(trace.children, opts, depth=0, is_first=True))


def _walk(
    nodes: list[Section | LogLine],
    opts: TraceOptions,
    depth: int,
    *,
    is_first: bool = False,
) -> Iterator[RenderableType]:
    first = is_first
    for node in nodes:
        if isinstance(node, Section):
            if opts.sections:
                # Blank line before section for visual breathing room.
                if not first:
                    yield Text("")
                first = False
                yield _section_rule(node, depth)
                if node.collapsed:
                    continue
            yield from _walk(node.children, opts, depth + (1 if opts.sections else 0))
        else:
            first = False
            yield _render_line(node, opts, depth)


def _section_rule(section: Section, depth: int) -> Rule:
    indent = _INDENT * depth
    icon = "\u25b8" if section.collapsed else "\u25be"
    parts = [f"{indent}[{_SECTION_COLOR}]{icon}[/{_SECTION_COLOR}] [{_SECTION_COLOR} bold]{section.name}[/{_SECTION_COLOR} bold]"]
    if section.duration is not None:
        parts.append(f"[dim]{section.duration}s[/dim]")
    title = "  ".join(parts)
    return Rule(title, align="left", style=_SECTION_COLOR)


def _render_line(line: LogLine, opts: TraceOptions, depth: int) -> Text:
    body = line.text

    if opts.strip:
        body = _NOISE_RE.sub("", body)

    if opts.color:
        txt = Text.from_ansi(body)
    else:
        body = _ANSI_RE.sub("", body)
        txt = Text(body)

    if opts.timestamps and line.iso_timestamp:
        ts = Text(f"{line.iso_timestamp} ", style="dim")
        txt = Text.assemble(ts, txt)

    # Indent lines within sections.
    if depth > 0 and opts.sections:
        indent = Text(_INDENT * depth)
        txt = Text.assemble(indent, txt)

    if opts.highlight:
        plain = txt.plain
        if _ERROR_RE.search(plain):
            txt.stylize("red")
        elif _WARN_RE.search(plain):
            txt.stylize("yellow")

    return txt
