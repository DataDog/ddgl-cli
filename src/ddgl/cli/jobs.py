from __future__ import annotations

import asyncio
import sys

import click

from ddgl.cache import Cache
from ddgl.cli._options import CACHE_DIR, job_filter_options, pipeline_resolution_options
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.constants import JobStatus
from ddgl.core.jobs import filter_jobs, get_job, list_jobs
from ddgl.core.pipeline import resolve_pipeline
from ddgl.exceptions import ConfigError, NoPipelineFoundError, NotFoundError
from ddgl.model.job import Job


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
    asyncio.run(_jobs_list(ref, pipeline_id, depth, failed_only, stage, name_pattern))


async def _jobs_list(
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    stage: str | None,
    name_pattern: str | None,
) -> None:
    try:
        config = await load_config()
    except ConfigError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    try:
        with Cache.open(CACHE_DIR) as cache:
            async with GitLabClient(config) as client:
                pipeline = await resolve_pipeline(
                    client, ref=ref,
                    pipeline_id=pipeline_id,
                    depth=depth, cache=cache,
                )
                scope = JobStatus.FAILED if failed_only else None
                all_jobs = [
                    j async for j in list_jobs(
                        client, pipeline.id,
                        scope=scope, cache=cache,
                    )
                ]
    except (NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    result = filter_jobs(
        all_jobs, failed_only=failed_only,
        name_pattern=name_pattern, stage=stage,
    )

    if not result:
        has_filters = failed_only or stage or name_pattern
        msg = (
            "No jobs match the given filters."
            if has_filters else "No jobs found."
        )
        click.echo(msg)
        return

    for j in result:
        reason = f"  {j.failure_reason}" if j.failure_reason else ""
        click.echo(f"#{j.id:<10} {j.name:<40} {j.stage:<15} {j.status}{reason}")


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

    With --job: show a single job by ID.
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
    asyncio.run(
        _jobs_get(
            ref, pipeline_id, depth,
            failed_only, stage, name_pattern, job_id,
        )
    )


async def _jobs_get(
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    stage: str | None,
    name_pattern: str | None,
    job_id: int | None,
) -> None:
    try:
        config = await load_config()
    except ConfigError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    try:
        with Cache.open(CACHE_DIR) as cache:
            async with GitLabClient(config) as client:
                if job_id is not None:
                    matched = [await get_job(client, job_id, cache=cache)]
                else:
                    pipeline = await resolve_pipeline(
                        client, ref=ref,
                        pipeline_id=pipeline_id,
                        depth=depth, cache=cache,
                    )
                    scope = JobStatus.FAILED if failed_only else None
                    matched = []
                    async for job in filter_jobs(
                        list_jobs(client, pipeline.id, scope=scope, cache=cache),
                        failed_only=failed_only,
                        name_pattern=name_pattern,
                        stage=stage,
                    ):
                        matched.append(job)
    except (NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not matched:
        has_filters = failed_only or stage or name_pattern
        msg = "No jobs match the given filters." if has_filters else "No jobs found."
        click.echo(msg)
        return

    for j in matched:
        _print_job_detail(j)
        click.echo()


def _print_job_detail(j: Job) -> None:
    duration_str = f"{int(j.duration)}s" if j.duration is not None else "—"

    click.echo(f"Job #{j.id}")
    click.echo(f"  Name:     {j.name}")
    click.echo(f"  Stage:    {j.stage}")
    click.echo(f"  Status:   {j.status}")
    click.echo(f"  Ref:      {j.ref or '—'}")
    click.echo(f"  Duration: {duration_str}")
    if j.allow_failure:
        click.echo("  Allow failure: yes")
    if j.failure_reason:
        click.echo(f"  Failure reason: {j.failure_reason}")
    if j.web_url:
        click.echo(f"  URL:      {j.web_url}")
