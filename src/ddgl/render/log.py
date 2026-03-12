from __future__ import annotations

import re

from rich.rule import Rule

from ddgl.render._console import console
from ddgl.render._styles import format_duration, format_status

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_ESCAPE.sub("", text)


def render_log_section(
    name: str,
    text: str,
    *,
    status: str = "",
    duration: float | None = None,
) -> None:
    """Print a styled Rule header + log body.

    On a TTY, ANSI escape codes embedded in the log text are preserved.
    Off a TTY (piped output), they are stripped.
    """
    title_parts = [f"[bold]{name}[/bold]"]
    if status:
        title_parts.append(format_status(status))
    if duration is not None:
        title_parts.append(format_duration(duration))
    title = "  ".join(title_parts)

    console.print(Rule(title, align="left"))

    body = text if console.is_terminal else strip_ansi(text)
    # Print without rich markup processing so embedded ANSI codes pass through.
    console.print(body, markup=False, highlight=False, end="\n")
