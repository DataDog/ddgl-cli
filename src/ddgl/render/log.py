from __future__ import annotations

from typing import TYPE_CHECKING

from rich.rule import Rule

from ddgl.render._console import console
from ddgl.render._styles import format_duration, format_status

if TYPE_CHECKING:
    from rich.console import RenderableType


def render_log_section(
    name: str,
    body: list[RenderableType],
    *,
    status: str = "",
    duration: float | None = None,
) -> None:
    """Print a styled Rule header followed by pre-rendered body items."""
    title_parts = [f"[bold]{name}[/bold]"]
    if status:
        title_parts.append(format_status(status))
    if duration is not None:
        title_parts.append(format_duration(duration))
    title = "  ".join(title_parts)

    console.print(Rule(title, align="left"))

    for item in body:
        console.print(item, highlight=False)
