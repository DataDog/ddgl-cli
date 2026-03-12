from __future__ import annotations

import asyncio
import sys

import click

from ddgl.cache import Cache
from ddgl.cli._options import CACHE_DIR, job_filter_options, pipeline_resolution_options
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.constants import JobStatus
from ddgl.core.jobs import filter_jobs, list_jobs
from ddgl.core.pipeline import resolve_pipeline
from ddgl.exceptions import ConfigError, NoPipelineFoundError, NotFoundError


@click.group()
def jobs() -> None:
    """Inspect GitLab jobs."""


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
                pipeline = await resolve_pipeline(client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache)
                scope = JobStatus.FAILED if failed_only else None
                all_jobs = [j async for j in list_jobs(client, pipeline.id, scope=scope, cache=cache)]
    except (NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    result = filter_jobs(all_jobs, failed_only=failed_only, name_pattern=name_pattern, stage=stage)

    if not result:
        msg = "No jobs match the given filters." if (failed_only or stage or name_pattern) else "No jobs found."
        click.echo(msg)
        return

    for j in result:
        reason = f"  {j.failure_reason}" if j.failure_reason else ""
        click.echo(f"#{j.id:<10} {j.name:<40} {j.stage:<15} {j.status}{reason}")
