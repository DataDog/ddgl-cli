"""Click decorator for trace formatting options."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import rich_click as click

F = TypeVar("F", bound=Callable)


def trace_format_options(f: F) -> F:
    """Add --raw and --[no-]{sections,strip,timestamps,highlight,color} flags."""
    f = click.option(
        "--color/--no-color",
        default=True,
        help="Preserve / strip ANSI colour from log content.",
    )(f)
    f = click.option(
        "--highlight/--no-highlight",
        default=True,
        help="Enable / disable error/warning line highlighting.",
    )(f)
    f = click.option(
        "--timestamps/--no-timestamps",
        default=True,
        help="Show / hide ISO timestamps on log lines.",
    )(f)
    f = click.option(
        "--strip/--no-strip",
        default=True,
        help="Remove / keep noise sequences (\\r, \\x1b[0K).",
    )(f)
    f = click.option(
        "--sections/--no-sections",
        default=True,
        help="Show / hide section header rules.",
    )(f)
    f = click.option(
        "--raw",
        is_flag=True,
        default=False,
        help="Output raw trace text without any formatting.",
    )(f)
    return f  # type: ignore[return-value]
