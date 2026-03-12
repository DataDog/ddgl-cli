from __future__ import annotations

import asyncio
import sys

import rich_click as click

from ddgl.cache import Cache
from ddgl.cli._options import CACHE_DIR, job_filter_options, pipeline_resolution_options
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.constants import JobStatus
from ddgl.core.jobs import filter_jobs, get_job, list_jobs
from ddgl.core.pipeline import resolve_pipeline
from ddgl.exceptions import ConfigError, NoPipelineFoundError, NotFoundError
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
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
def jobs_list(
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    stage: str | None,
    name_pattern: str | None,
) -> None:
    """List jobs for a pipeline."""
    try:
        pipeline, result = asyncio.run(
            _jobs_list(ref, pipeline_id, depth, failed_only, stage, name_pattern)
        )
    except (ConfigError, NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not result:
        has_filters = failed_only or stage or name_pattern
        click.echo("No jobs match the given filters." if has_filters else "No jobs found.")
        return

    render_job_table(result, pipeline=pipeline)


async def _jobs_list(
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    stage: str | None,
    name_pattern: str | None,
) -> tuple[Pipeline, list[Job]]:
    config = await load_config()
    with Cache.open(CACHE_DIR) as cache:
        async with GitLabClient(config) as client:
            pipeline = await resolve_pipeline(
                client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache,
            )
            scope = JobStatus.FAILED if failed_only else None
            all_jobs = [
                j async for j in list_jobs(client, pipeline.id, scope=scope, cache=cache)
            ]

    result = filter_jobs(all_jobs, failed_only=failed_only, name_pattern=name_pattern, stage=stage)
    return pipeline, result


@jobs.command("get")
@pipeline_resolution_options
@job_filter_options
@click.option(
    "--job", "job_id", default=None, type=int,
    help="Show a specific job by ID.",
)
@click.pass_context
def jobs_get(
    ctx: click.Context,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    stage: str | None,
    name_pattern: str | None,
    job_id: int | None,
) -> None:
    """Show details for jobs in a pipeline.

    With `--job`: show a single job by ID.
    Otherwise: resolve the pipeline, filter jobs, show details for all matches.
    """
    skip_confirm = (ctx.obj or {}).get("yes", False)
    has_filters = job_id is not None or failed_only or stage or name_pattern
    if not has_filters and not skip_confirm and sys.stdin.isatty():
        click.confirm(
            "No job filter specified — this will show details"
            " for every job in the pipeline. Continue?",
            abort=True,
        )

    try:
        matched = asyncio.run(
            _jobs_get(ref, pipeline_id, depth, failed_only, stage, name_pattern, job_id)
        )
    except (ConfigError, NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not matched:
        has_filters = failed_only or stage or name_pattern
        click.echo("No jobs match the given filters." if has_filters else "No jobs found.")
        return

    for j in matched:
        render_job_detail(j)
        click.echo()


async def _jobs_get(
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    stage: str | None,
    name_pattern: str | None,
    job_id: int | None,
) -> list[Job]:
    config = await load_config()
    with Cache.open(CACHE_DIR) as cache:
        async with GitLabClient(config) as client:
            if job_id is not None:
                return [await get_job(client, job_id, cache=cache)]

            pipeline = await resolve_pipeline(
                client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache,
            )
            scope = JobStatus.FAILED if failed_only else None
            return [
                j async for j in filter_jobs(
                    list_jobs(client, pipeline.id, scope=scope, cache=cache),
                    failed_only=failed_only,
                    name_pattern=name_pattern,
                    stage=stage,
                )
            ]
