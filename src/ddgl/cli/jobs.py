from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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
                pipeline = await resolve_pipeline(
                    client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache
                )
                scope = JobStatus.FAILED if failed_only else None
                all_jobs = [
                    j async for j in list_jobs(client, pipeline.id, scope=scope, cache=cache)
                ]
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


@jobs.command("logs")
@pipeline_resolution_options
@job_filter_options
@click.option("--job", "job_id", default=None, type=int, help="Fetch log for a specific job by ID.")
@click.option("--output", "output_path", default=None, type=click.Path(), help="Output path (file or directory).")
def jobs_logs(
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    stage: str | None,
    name_pattern: str | None,
    job_id: int | None,
    output_path: str | None,
) -> None:
    """Fetch job logs.

    With --job: fetch a single job log by ID.
    Otherwise: resolve the pipeline, filter jobs, fetch all matching logs.
    """
    asyncio.run(_jobs_logs(ref, pipeline_id, depth, failed_only, stage, name_pattern, job_id, output_path))


async def _jobs_logs(
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    stage: str | None,
    name_pattern: str | None,
    job_id: int | None,
    output_path: str | None,
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
                    logs: dict[int, str] = {job_id: await client.get_job_log(job_id)}
                    job_names: dict[int, str] = {job_id: str(job_id)}
                else:
                    pipeline = await resolve_pipeline(
                        client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache
                    )
                    scope = JobStatus.FAILED if failed_only else None
                    all_jobs = [
                        j async for j in list_jobs(client, pipeline.id, scope=scope, cache=cache)
                    ]
                    target_jobs = filter_jobs(
                        all_jobs, failed_only=failed_only, name_pattern=name_pattern, stage=stage
                    )
                    if not target_jobs:
                        msg = (
                            "No jobs match the given filters."
                            if (failed_only or stage or name_pattern)
                            else "No jobs found."
                        )
                        click.echo(msg)
                        return

                    if len(target_jobs) > 1 and sys.stdin.isatty() and output_path is None:
                        click.confirm(f"Fetch logs for all {len(target_jobs)} jobs?", abort=True)

                    log_texts = await asyncio.gather(
                        *[client.get_job_log(j.id) for j in target_jobs]
                    )
                    logs = {j.id: text for j, text in zip(target_jobs, log_texts)}
                    job_names = {j.id: j.name for j in target_jobs}
    except (NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    _write_logs(logs, job_names, output_path)


def _write_logs(
    logs: dict[int, str],
    job_names: dict[int, str],
    output_path: str | None,
) -> None:
    sep = "\n─── {name} ───\n"

    if output_path is not None:
        out = Path(output_path)
        if out.is_dir():
            for jid, text in logs.items():
                (out / f"{job_names[jid]}.log").write_text(text)
        else:
            lines = []
            for jid, text in logs.items():
                lines.append(sep.format(name=job_names[jid]))
                lines.append(text)
            out.write_text("".join(lines))
        return

    if len(logs) == 1:
        click.echo(next(iter(logs.values())))
    else:
        for jid, text in logs.items():
            click.echo(sep.format(name=job_names[jid]))
            click.echo(text)
