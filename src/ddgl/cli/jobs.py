# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import asyncio
import sys
from contextlib import nullcontext

import msgspec
import rich_click as click

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
from ddgl.core.pipeline import resolve_pipeline
from ddgl.exceptions import ConfigError, NoPipelineFoundError, NotFoundError
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.render._console import console
from ddgl.render.job import render_job_detail, render_job_table


@click.group(invoke_without_command=True)
@click.pass_context
def jobs(ctx: click.Context) -> None:
    """Inspect GitLab jobs."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@jobs.command("list")
@pipeline_resolution_options
@job_filter_options
@output_options
@click.pass_context
def jobs_list(
    ctx: click.Context,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    include_allowed_failures: bool,
    stage: str | None,
    name_pattern: str | None,
    output_json: bool,
    no_pager: bool,
) -> None:
    """List jobs for a pipeline."""
    no_cache = (ctx.obj or {}).get("no_cache", False)
    try:
        pipeline, result = asyncio.run(
            _jobs_list(
                ref, pipeline_id, depth, failed_only, include_allowed_failures,
                stage, name_pattern, quiet=output_json, no_cache=no_cache,
            )
        )
    except (ConfigError, NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not result:
        if output_json:
            click.echo("[]")
        else:
            has_filters = failed_only or stage or name_pattern
            click.echo("No jobs match the given filters." if has_filters else "No jobs found.")
        return

    if output_json:
        click.echo(msgspec.json.encode(result).decode())
        return

    # Skip pager when filters are active — result is likely short.
    has_filters = bool(failed_only or stage or name_pattern)
    use_pager = not no_pager and console.is_terminal and not has_filters
    with console.pager(styles=True) if use_pager else nullcontext():
        render_job_table(result, pipeline=pipeline)


async def _jobs_list(
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    include_allowed_failures: bool,
    stage: str | None,
    name_pattern: str | None,
    *,
    quiet: bool = False,
    no_cache: bool = False,
) -> tuple[Pipeline, list[Job]]:
    with Cache.open(CACHE_DIR, bypass=no_cache) as cache:
        config = await load_config(cache=cache)
        async with GitLabClient(config, cache=cache) as client:
            with nullcontext() if quiet else console.status("Resolving pipeline…"):
                pipeline = await resolve_pipeline(
                    client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache,
                )
            scope = JobStatus.FAILED if failed_only else None
            with nullcontext() if quiet else console.status("Fetching jobs…"):
                all_jobs = [
                    j async for j in list_jobs(client, pipeline.id, scope=scope, cache=cache)
                ]

    result = filter_jobs(
        all_jobs,
        failed_only=failed_only,
        include_allowed_failures=include_allowed_failures,
        name_pattern=name_pattern,
        stage=stage,
    )
    return pipeline, result


@jobs.command("get")
@pipeline_resolution_options
@job_filter_options
@click.option(
    "--job", "job_id", default=None, type=int,
    help="Show a specific job by ID.",
)
@output_options
@click.pass_context
def jobs_get(
    ctx: click.Context,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    include_allowed_failures: bool,
    stage: str | None,
    name_pattern: str | None,
    job_id: int | None,
    output_json: bool,
    no_pager: bool,
) -> None:
    """Show details for jobs in a pipeline.

    With `--job`: show a single job by ID.
    Otherwise: resolve the pipeline, filter jobs, show details for all matches.
    """
    skip_confirm = (ctx.obj or {}).get("yes", False)
    no_cache = (ctx.obj or {}).get("no_cache", False)
    has_filters = job_id is not None or failed_only or stage or name_pattern
    if not has_filters and not skip_confirm and sys.stdin.isatty():
        click.confirm(
            "No job filter specified — this will show details"
            " for every job in the pipeline. Continue?",
            abort=True,
        )

    try:
        matched = asyncio.run(
            _jobs_get(
                ref, pipeline_id, depth, failed_only, include_allowed_failures,
                stage, name_pattern, job_id, quiet=output_json, no_cache=no_cache,
            )
        )
    except (ConfigError, NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not matched:
        if output_json:
            click.echo("[]")
        else:
            has_filters = failed_only or stage or name_pattern
            click.echo("No jobs match the given filters." if has_filters else "No jobs found.")
        return

    if output_json:
        click.echo(msgspec.json.encode(matched).decode())
        return

    # Skip pager when any filter narrows the result.
    has_filters = bool(failed_only or stage or name_pattern or job_id is not None)
    use_pager = not no_pager and console.is_terminal and not has_filters
    with console.pager(styles=True) if use_pager else nullcontext():
        for j in matched:
            render_job_detail(j)
            click.echo()


async def _jobs_get(
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    include_allowed_failures: bool,
    stage: str | None,
    name_pattern: str | None,
    job_id: int | None,
    *,
    quiet: bool = False,
    no_cache: bool = False,
) -> list[Job]:
    with Cache.open(CACHE_DIR, bypass=no_cache) as cache:
        config = await load_config(cache=cache)
        async with GitLabClient(config, cache=cache) as client:
            if job_id is not None:
                with nullcontext() if quiet else console.status("Fetching job…"):
                    return [await get_job(client, job_id, cache=cache)]

            with nullcontext() if quiet else console.status("Resolving pipeline…"):
                pipeline = await resolve_pipeline(
                    client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache,
                )
            scope = JobStatus.FAILED if failed_only else None
            with nullcontext() if quiet else console.status("Fetching jobs…"):
                return [
                    j async for j in filter_jobs(
                        list_jobs(client, pipeline.id, scope=scope, cache=cache),
                        failed_only=failed_only,
                        include_allowed_failures=include_allowed_failures,
                        name_pattern=name_pattern,
                        stage=stage,
                    )
                ]
