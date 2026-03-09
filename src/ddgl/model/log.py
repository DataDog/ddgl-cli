from __future__ import annotations

import re
from dataclasses import dataclass, field

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# GitLab CI section markers:
#   section_start:<timestamp>:<name>\r\x1b[0K
#   section_end:<timestamp>:<name>\r\x1b[0K
_SECTION_START_RE = re.compile(
    r"section_start:(\d+):(.+?)(?:\r)?$"
)
_SECTION_END_RE = re.compile(
    r"section_end:(\d+):(.+?)(?:\r)?$"
)


@dataclass
class LogSection:
    name: str
    lines: list[str] = field(default_factory=list)


class JobLog:
    """Parsed GitLab job log with ANSI stripping and section support."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self._sections: list[LogSection] | None = None

    @property
    def clean(self) -> str:
        """Log text with ANSI escape sequences removed."""
        return _ANSI_RE.sub("", self.raw)

    @property
    def sections(self) -> list[LogSection]:
        """Parse the log into GitLab CI sections."""
        if self._sections is not None:
            return self._sections
        self._sections = _parse_sections(self.clean)
        return self._sections

    @property
    def lines(self) -> list[str]:
        return self.clean.splitlines()


def _parse_sections(text: str) -> list[LogSection]:
    sections: list[LogSection] = []
    current: LogSection | None = None

    for line in text.splitlines():
        start = _SECTION_START_RE.match(line)
        if start:
            current = LogSection(name=start.group(2))
            sections.append(current)
            continue

        end = _SECTION_END_RE.match(line)
        if end:
            current = None
            continue

        if current is not None:
            current.lines.append(line)

    return sections
