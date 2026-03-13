from __future__ import annotations

from dataclasses import dataclass, field

from ddgl.format import Section as _FmtSection
from ddgl.format import Trace, parse_trace, strip_ansi


@dataclass
class LogSection:
    name: str
    lines: list[str] = field(default_factory=list)


class JobLog:
    """Parsed GitLab job log with ANSI stripping and section support."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self._trace: Trace | None = None

    @property
    def trace(self) -> Trace:
        if self._trace is None:
            self._trace = parse_trace(self.raw)
        return self._trace

    @property
    def clean(self) -> str:
        """Log text with ANSI escape sequences removed."""
        return strip_ansi(self.raw)

    @property
    def sections(self) -> list[LogSection]:
        """Backward-compatible flat section list."""
        return [
            LogSection(
                name=node.name,
                lines=[child.text for child in node.children if not isinstance(child, _FmtSection)],
            )
            for node in self.trace.children
            if isinstance(node, _FmtSection)
        ]

    @property
    def lines(self) -> list[str]:
        return self.clean.splitlines()
