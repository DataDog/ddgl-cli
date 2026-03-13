"""Click decorator for trace formatting options."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import rich_click as click

F = TypeVar("F", bound=Callable)


def trace_format_options(f: F) -> F:
    """Add --raw, --no-sections, --no-strip, --no-timestamps, --no-highlight, --no-color."""
    f = click.option(
        "--no-color",
        is_flag=True,
        default=False,
        help="Strip all ANSI colour from log content.",
    )(f)
    f = click.option(
        "--no-highlight",
        is_flag=True,
        default=False,
        help="Disable error/warning line highlighting.",
    )(f)
    f = click.option(
        "--no-timestamps",
        is_flag=True,
        default=False,
        help="Hide ISO timestamps from log lines.",
    )(f)
    f = click.option(
        "--no-strip",
        is_flag=True,
        default=False,
        help="Keep noise sequences (\\r, \\x1b[0K) in output.",
    )(f)
    f = click.option(
        "--no-sections",
        is_flag=True,
        default=False,
        help="Hide section header rules.",
    )(f)
    f = click.option(
        "--raw",
        is_flag=True,
        default=False,
        help="Output raw trace text without any formatting.",
    )(f)
    return f  # type: ignore[return-value]
