# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import TypeVar

import rich_click as click

from ddgl.render._console import console

CACHE_DIR = Path("~/.cache/ddgl").expanduser()

F = TypeVar("F", bound=Callable)


def stdin_is_tty() -> bool:
    """Whether stdin can carry an interactive answer.

    A named function rather than an inline `sys.stdin.isatty()` because it
    gates whether a command may act without confirmation, and Click's
    CliRunner swaps `sys.stdin` for a non-TTY pipe — so tests can only
    reach the interactive path by substituting this.
    """
    return sys.stdin.isatty()


@contextmanager
def status_spinner(label: str, *, quiet: bool) -> Iterator[None]:
    """Show a progress spinner, unless *quiet*.

    Commands suppress it while emitting `--json`, where a spinner would
    write control characters into machine-read output.
    """
    with nullcontext() if quiet else console.status(label):
        yield


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
    """Add -f/--failed, --stage, --name, --include-allowed-failures to a command."""
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
        "--include-allowed-failures",
        is_flag=True,
        help="With -f/--failed, also include jobs with allow_failure set.",
    )(f)
    f = click.option(
        "-f",
        "--failed",
        "failed_only",
        is_flag=True,
        help="Show/fetch only failed jobs (excludes allowed failures).",
    )(f)
    return f  # type: ignore[return-value]
