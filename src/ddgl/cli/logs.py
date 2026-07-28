# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import asyncio
import json
import re
import sys
from contextlib import nullcontext
from pathlib import Path

import rich_click as click
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)
from rich.text import Text

from ddgl.cache import Cache
from ddgl.cli._options import (
    CACHE_DIR,
    job_filter_options,
    output_options,
    pipeline_resolution_options,
)
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.constants import JobStatus
from ddgl.core.jobs import filter_jobs, get_job, list_jobs
from ddgl.core.logs import get_log
from ddgl.core.pipeline import resolve_pipeline
from ddgl.exceptions import ConfigError, NoPipelineFoundError, NotFoundError
from ddgl.format import TraceOptions, format_trace, strip_ansi
from ddgl.format._options import trace_format_options
from ddgl.render._console import console
from ddgl.render.log import render_log_section


@click.command()
@pipeline_resolution_options
@job_filter_options
@click.option("--job", "job_id", default=None, type=int, help="Fetch log for a specific job by ID.")
@click.option("--output", "output_path", default=None, type=click.Path(), help="Output path (file or directory).")
@output_options
@trace_format_options
@click.pass_context
def logs(
    ctx: click.Context,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    stage: str | None,
    name_pattern: str | None,
    job_id: int | None,
    output_path: str | None,
    output_json: bool,
    no_pager: bool,
    raw: bool,
    sections: bool,
    strip: bool,
    timestamps: bool,
    highlight: bool,
    color: bool,
) -> None:
    """Fetch job logs.

    With `--job`: fetch a single job log by ID.
    Otherwise: resolve the pipeline, filter jobs, fetch all matching logs.
    """
    skip_confirm = (ctx.obj or {}).get("yes", False) or output_json
    no_cache = (ctx.obj or {}).get("no_cache", False)
    has_filters = job_id is not None or failed_only or stage or name_pattern
    if not has_filters and not skip_confirm and sys.stdin.isatty():
        click.confirm(
            "No job filter specified — this will fetch logs for every job in the pipeline. Continue?",
            abort=True,
        )

    try:
        results = asyncio.run(
            _fetch_logs(ref, pipeline_id, depth, failed_only, stage, name_pattern, job_id, quiet=output_json, no_cache=no_cache)
        )
    except (ConfigError, NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not results:
        has_filters = failed_only or stage or name_pattern
        click.echo("No jobs match the given filters." if has_filters else "No jobs found.")
        return

    if output_json:
        click.echo(json.dumps(dict(results)))
        return

    if output_path is not None:
        for name, text in results:
            _write_log_to_path(name, text, output_path)
        return

    # Build TraceOptions from flags.
    if raw:
        options = TraceOptions.raw()
    else:
        options = TraceOptions(
            sections=sections,
            strip=strip,
            timestamps=timestamps,
            highlight=highlight,
            color=color and console.is_terminal,
        )

    use_pager = not no_pager and console.is_terminal
    with console.pager(styles=True) if use_pager else nullcontext():
        for name, text in results:
            if raw:
                render_log_section(name, [Text(text)])
            else:
                render_log_section(name, format_trace(text, options))
            console.print()


async def _fetch_logs(
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    stage: str | None,
    name_pattern: str | None,
    job_id: int | None,
    *,
    quiet: bool = False,
    no_cache: bool = False,
) -> list[tuple[str, str]]:
    """Fetch logs and return [(job_name, log_text), ...]."""
    with Cache.open(CACHE_DIR, bypass=no_cache) as cache:
        config = await load_config(cache=cache)
        async with GitLabClient(config, cache=cache) as client:
            if job_id is not None:
                with nullcontext() if quiet else console.status("Fetching log…"):
                    job, text = await asyncio.gather(
                        get_job(client, job_id, cache=cache),
                        get_log(client, job_id, cache=cache),
                    )
                return [(job.name, text)]

            with nullcontext() if quiet else console.status("Resolving pipeline…"):
                pipeline = await resolve_pipeline(
                    client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache
                )
            scope = JobStatus.FAILED if failed_only else None

            with nullcontext() if quiet else console.status("Fetching job list…"):
                log_tasks: dict[int, tuple[str, asyncio.Task[str]]] = {}
                async for job in filter_jobs(
                    list_jobs(client, pipeline.id, scope=scope, cache=cache),
                    failed_only=failed_only,
                    name_pattern=name_pattern,
                    stage=stage,
                ):
                    log_tasks[job.id] = (
                        job.name,
                        asyncio.create_task(get_log(client, job.id, cache=cache)),
                    )

            if not log_tasks:
                return []

            # Tasks must be awaited while the client is still open.
            results: list[tuple[str, str]] = []
            futures = [_await_with_name(name, task) for _, (name, task) in log_tasks.items()]

            if quiet:
                for coro in asyncio.as_completed(futures):
                    results.append(await coro)
            else:
                from ddgl.render._console import err_console
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    MofNCompleteColumn(),
                    console=err_console,
                    transient=True,
                ) as progress:
                    task_id = progress.add_task("Fetching logs", total=len(futures))
                    for coro in asyncio.as_completed(futures):
                        results.append(await coro)
                        progress.advance(task_id)

    return results


async def _await_with_name(name: str, task: asyncio.Task[str]) -> tuple[str, str]:
    return name, await task


def _write_log_to_path(name: str, text: str, output_path: str) -> None:
    """Write a single job log to a file or directory path (ANSI stripped)."""
    out = Path(output_path)
    clean = strip_ansi(text)
    if out.is_dir():
        # Sanitize the job name to make sure it is a valid filename
        name = re.sub(r'[^\w\-.]', '_', name)
        p = out.joinpath(f"{name}.log")
        p.write_text(clean)
    else:
        with open(out, "a") as f:
            f.write(f"─── {name} ───\n{clean}\n")
