# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""``ddgl attach`` — block on a CI pipeline, streaming progress until done."""

from __future__ import annotations

import asyncio
import re
import sys

import httpx
import rich_click as click
from click.core import ParameterSource

from ddgl.cache import Cache
from ddgl.cli._options import CACHE_DIR, pipeline_resolution_options
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.constants import DEFAULT_JOB_RETRY_ATTEMPTS, DEFAULT_JOB_RETRY_TOTAL
from ddgl.core.attach import attach
from ddgl.exceptions import (
    ConfigError,
    GitLabAPIError,
    NoPipelineFoundError,
    NotFoundError,
)
from ddgl.model.attach import AttachEvent, DetailLevel, RetryPolicy
from ddgl.render._console import console
from ddgl.render.attach import render_lines, render_live

_DETAIL_CHOICES = tuple(level.value for level in DetailLevel)

_RETRY_TUNING_PARAMS = ("retry_attempts", "retry_total", "retry_exclude")


def _explicit_tuning_flags(ctx: click.Context) -> list[str]:
    """Names of the retry tuning flags the invocation actually set.

    Distinguishing these from their defaults is what lets `--retry-total 5`
    without `--retry` be reported rather than silently ignored.
    """
    defaults = (ParameterSource.DEFAULT, ParameterSource.DEFAULT_MAP)
    return [
        f"--{name.replace('_', '-')}"
        for name in _RETRY_TUNING_PARAMS
        if ctx.get_parameter_source(name) not in defaults
    ]


def _retry_policy(
    ctx: click.Context,
    *,
    retry: bool,
    attempts: int,
    total: int,
    exclude: tuple[str, ...],
) -> RetryPolicy:
    """Build the policy from the flags, or exit 2 if they don't make sense.

    Rejects tuning without `--retry`, and an unparseable `--retry-exclude`
    pattern — which would otherwise surface as a traceback mid-run, on
    whichever poll tick first had a candidate to match it against.
    """
    if not retry and (tuning := _explicit_tuning_flags(ctx)):
        click.echo(
            f"Error: {', '.join(tuning)}{' has' if len(tuning) == 1 else ' have'}"
            " no effect without --retry.",
            err=True,
        )
        sys.exit(2)

    for pattern in exclude:
        try:
            re.compile(pattern)
        except re.error as exc:
            click.echo(f"Error: --retry-exclude {pattern!r} is not a valid regex: {exc}", err=True)
            sys.exit(2)

    return RetryPolicy(
        enabled=retry, attempts_per_job=attempts, total=total, exclude=exclude,
    )


@click.command()
@pipeline_resolution_options
@click.option(
    "--interval", default=10.0, show_default=True, type=float,
    help="Poll cadence in seconds (also controls --heartbeat cadence).",
)
@click.option(
    "--heartbeat/--no-heartbeat", "heartbeat", default=False,
    help="Emit a tally line on poll ticks where nothing changed.",
)
@click.option(
    "--detail", type=click.Choice(_DETAIL_CHOICES), default="normal", show_default=True,
    help=(
        "How much to show: none=final state only, minimal=summaries only, "
        "normal=summaries + job transitions to terminal states, full=everything. "
        "Affects --plain and --live; --json always shows everything."
    ),
)
@click.option(
    "--no-wait", is_flag=True, default=False,
    help="Require an existing pipeline; error immediately instead of waiting for one to appear.",
)
@click.option(
    "--follow", is_flag=True, default=False,
    help="Switch to a newer pipeline for the ref if one appears (warns on switch).",
)
@click.option(
    "--timeout", default=None, type=float,
    help="Max seconds to block. On elapse while still running, exit 124.",
)
@click.option(
    "--retry", is_flag=True, default=False,
    help=(
        "Auto-retry failed jobs — both those already failed when attaching and "
        "any that fail while polling."
    ),
)
@click.option(
    "--retry-attempts", type=click.IntRange(min=0),
    default=DEFAULT_JOB_RETRY_ATTEMPTS, show_default=True,
    help=(
        "Retries allowed per job name, counting GitLab's own `retry:` attempts. "
        "`0` for unlimited. Requires `--retry`."
    ),
)
@click.option(
    "--retry-total", type=click.IntRange(min=0),
    default=DEFAULT_JOB_RETRY_TOTAL, show_default=True,
    help="Retries allowed across the whole run. `0` for unlimited. Requires `--retry`.",
)
@click.option(
    "--retry-exclude", multiple=True, metavar="REGEX",
    help="Never auto-retry jobs whose name matches (repeatable). Requires `--retry`.",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Force JSONL output, even in a TTY.")
@click.option("--plain", is_flag=True, default=False, help="Force append-only line output (override TTY auto-detect).")
@click.option("--live", "force_live", is_flag=True, default=False, help="Force the redrawing TTY view (override non-TTY auto-detect).")
@click.pass_context
def attach_cmd(
    ctx: click.Context,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    interval: float,
    heartbeat: bool,
    detail: str,
    no_wait: bool,
    follow: bool,
    timeout: float | None,
    retry: bool,
    retry_attempts: int,
    retry_total: int,
    retry_exclude: tuple[str, ...],
    output_json: bool,
    plain: bool,
    force_live: bool,
) -> None:
    """Block on a CI pipeline, streaming progress until it finishes.

    Exit codes: 0 succeeded, 1 failed/canceled, 2 unexpected/config error,
    124 --timeout elapsed while still running (GNU `timeout` convention).
    """
    if plain and force_live:
        click.echo("Error: --plain and --live are mutually exclusive.", err=True)
        sys.exit(2)

    policy = _retry_policy(
        ctx, retry=retry, attempts=retry_attempts, total=retry_total, exclude=retry_exclude,
    )

    no_cache = (ctx.obj or {}).get("no_cache", False)
    exit_code = asyncio.run(_attach(
        ref=ref, pipeline_id=pipeline_id, depth=depth, interval=interval,
        heartbeat=heartbeat, detail=detail, wait_for_start=not no_wait,
        follow=follow, timeout=timeout, retry_policy=policy, output_json=output_json,
        plain=plain, force_live=force_live, no_cache=no_cache,
    ))
    sys.exit(exit_code)


def _use_live(*, output_json: bool, plain: bool, force_live: bool) -> bool:
    """TTY auto-detect with explicit overrides — mirrors the existing
    console.is_terminal + --json pattern used by other ddgl commands.
    --json always forces JSONL (via render_lines), even in a TTY.
    """
    if force_live:
        return True
    if plain or output_json:
        return False
    return console.is_terminal


def _exit_code(result: AttachEvent) -> int:
    if result.reason == "timeout":
        return 124
    return 0 if result.status == "success" else 1


async def _attach(
    *,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    interval: float,
    heartbeat: bool,
    detail: str,
    wait_for_start: bool,
    follow: bool,
    timeout: float | None,
    retry_policy: RetryPolicy,
    output_json: bool,
    plain: bool,
    force_live: bool,
    no_cache: bool,
) -> int:
    use_live = _use_live(output_json=output_json, plain=plain, force_live=force_live)
    detail_level = DetailLevel(detail)

    try:
        with Cache.open(CACHE_DIR, bypass=no_cache) as cache:
            config = await load_config(cache=cache)
            async with GitLabClient(config, cache=cache) as client:
                events = attach(
                    client,
                    ref=ref, pipeline_id=pipeline_id, depth=depth, interval=interval,
                    heartbeat=heartbeat, wait_for_start=wait_for_start, follow=follow,
                    timeout=timeout, retry_policy=retry_policy, cache=cache,
                )
                if use_live:
                    result = await render_live(events, detail=detail_level)
                else:
                    result = await render_lines(events, as_json=output_json, detail=detail_level)
    except (ConfigError, NoPipelineFoundError, NotFoundError, GitLabAPIError, httpx.TransportError) as e:
        click.echo(f"Error: {e}", err=True)
        return 2

    return _exit_code(result)
