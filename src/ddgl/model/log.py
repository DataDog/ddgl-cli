from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ddgl.model.trace import Trace


@dataclass
class LogSection:
    name: str
    lines: list[str] = field(default_factory=list)


class JobLog:
    """Parsed GitLab job log with ANSI stripping and section support.

    Imports from ``ddgl.format`` are deferred to break a circular import:
    format._parser → model.trace → model.__init__ → model.log → format._parser
    """

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self._trace: Trace | None = None

    @property
    def trace(self) -> Trace:
        if self._trace is None:
            from ddgl.format._parser import parse_trace

            self._trace = parse_trace(self.raw)
        return self._trace

    @property
    def clean(self) -> str:
        """Log text with ANSI escape sequences removed."""
        from ddgl.format._parser import strip_ansi

        return strip_ansi(self.raw)

    @property
    def sections(self) -> list[LogSection]:
        """Backward-compatible flat section list."""
        from ddgl.model.trace import Section

        return [
            LogSection(
                name=node.name,
                lines=[child.text for child in node.children if not isinstance(child, Section)],
            )
            for node in self.trace.children
            if isinstance(node, Section)
        ]

    @property
    def lines(self) -> list[str]:
        return self.clean.splitlines()
