"""Render a :class:`Trace` tree to Rich renderables."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.rule import Rule
from rich.text import Text

from ddgl.format._parser import _ANSI_RE, _NOISE_RE, LogLine, Section, Trace

if TYPE_CHECKING:
    from rich.console import RenderableType

_ERROR_RE = re.compile(r"\b(?:error|fatal|exception|traceback)\b", re.IGNORECASE)
_WARN_RE = re.compile(r"\b(?:warn(?:ing)?)\b", re.IGNORECASE)


@dataclass
class TraceOptions:
    """Controls how a :class:`Trace` is rendered to Rich renderables."""

    sections: bool = True  # section markers → Rule headers
    strip: bool = True  # remove noise sequences from log lines
    timestamps: bool = True  # dim leading ISO timestamp
    highlight: bool = True  # colour error/warning lines
    no_color: bool = False  # strip all ANSI from log content

    @classmethod
    def raw(cls) -> TraceOptions:
        return cls(sections=False, strip=False, timestamps=False, highlight=False)


def render_trace(
    trace: Trace,
    options: TraceOptions | None = None,
) -> list[RenderableType]:
    """Walk *trace* and produce a flat list of Rich renderables."""
    opts = options or TraceOptions()
    out: list[RenderableType] = []
    _walk(trace.children, opts, depth=0, out=out)
    return out


def _walk(
    nodes: list[Section | LogLine],
    opts: TraceOptions,
    depth: int,
    out: list[RenderableType],
) -> None:
    for node in nodes:
        if isinstance(node, Section):
            if opts.sections:
                out.append(_section_rule(node, depth))
            _walk(node.children, opts, depth + 1, out)
        else:
            out.append(_render_line(node, opts))


def _section_rule(section: Section, depth: int) -> Rule:
    indent = "  " * depth
    parts = [f"{indent}[bold]{section.name}[/bold]"]
    if section.duration is not None:
        parts.append(f"[dim]{section.duration}s[/dim]")
    title = "  ".join(parts)
    return Rule(title, align="left")


def _render_line(line: LogLine, opts: TraceOptions) -> Text:
    body = line.text

    if opts.strip:
        body = _NOISE_RE.sub("", body)

    if opts.no_color:
        body = _ANSI_RE.sub("", body)
        txt = Text(body)
    else:
        txt = Text.from_ansi(body)

    if opts.timestamps and line.iso_timestamp:
        ts = Text(f"{line.iso_timestamp} ", style="dim")
        txt = Text.assemble(ts, txt)

    if opts.highlight:
        plain = txt.plain
        if _ERROR_RE.search(plain):
            txt.stylize("red")
        elif _WARN_RE.search(plain):
            txt.stylize("yellow")

    return txt
