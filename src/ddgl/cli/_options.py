from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import rich_click as click

CACHE_DIR = Path("~/.cache/ddgl").expanduser()

F = TypeVar("F", bound=Callable)


def pipeline_resolution_options(f: F) -> F:
    """Add --ref, --pipeline, --depth to a command."""
    f = click.option(
        "--depth",
        default=10,
        show_default=True,
        help="Commits to walk when searching for a pipeline.",
    )(f)
    f = click.option(
        "--pipeline",
        "pipeline_id",
        default=None,
        type=int,
        help="Pin a specific pipeline by ID (skips ref resolution).",
    )(f)
    f = click.option(
        "--ref",
        default=None,
        help="Git ref (branch, tag, SHA). Default: current branch.",
    )(f)
    return f  # type: ignore[return-value]


def output_options(f: F) -> F:
    """Add --json and --no-pager to a command."""
    f = click.option(
        "--no-pager",
        is_flag=True,
        default=False,
        help="Disable the pager for long output.",
    )(f)
    f = click.option(
        "--json",
        "output_json",
        is_flag=True,
        default=False,
        help="Output as JSON.",
    )(f)
    return f  # type: ignore[return-value]


def job_filter_options(f: F) -> F:
    """Add -f/--failed, --stage, --name to a command."""
    f = click.option(
        "--name",
        "name_pattern",
        default=None,
        help="Filter by job name (regex).",
    )(f)
    f = click.option(
        "--stage",
        default=None,
        help="Filter by stage name (exact match).",
    )(f)
    f = click.option(
        "-f",
        "--failed",
        "failed_only",
        is_flag=True,
        help="Show/fetch only failed jobs.",
    )(f)
    return f  # type: ignore[return-value]
