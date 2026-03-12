from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import rich_click as click

from ddgl.cache import Cache
from ddgl.cli._options import CACHE_DIR, job_filter_options, pipeline_resolution_options
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.constants import JobStatus
from ddgl.core.jobs import filter_jobs, get_job, list_jobs
from ddgl.core.logs import get_log
from ddgl.core.pipeline import resolve_pipeline
from ddgl.exceptions import ConfigError, NoPipelineFoundError, NotFoundError


@click.command()
@pipeline_resolution_options
@job_filter_options
@click.option("--job", "job_id", default=None, type=int, help="Fetch log for a specific job by ID.")
@click.option("--output", "output_path", default=None, type=click.Path(), help="Output path (file or directory).")
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
) -> None:
    """Fetch job logs.

    With --job: fetch a single job log by ID.
    Otherwise: resolve the pipeline, filter jobs, fetch all matching logs.
    """
    # No filters at all: warn the user this will fetch every job's log.
    skip_confirm = (ctx.obj or {}).get("yes", False)
    has_filters = job_id is not None or failed_only or stage or name_pattern
    if not has_filters and not skip_confirm and sys.stdin.isatty():
        click.confirm(
            "No job filter specified — this will fetch logs for every job in the pipeline. Continue?",
            abort=True,
        )
    asyncio.run(_logs(ref, pipeline_id, depth, failed_only, stage, name_pattern, job_id, output_path))


async def _logs(
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
                    # Fetch job metadata and log concurrently.
                    job, text = await asyncio.gather(
                        get_job(client, job_id, cache=cache),
                        get_log(client, job_id, cache=cache),
                    )
                    _write_log(job.name, text, output_path)
                    return

                pipeline = await resolve_pipeline(
                    client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache
                )
                scope = JobStatus.FAILED if failed_only else None

                # Fire a log-fetch task for each matching job as the iterator
                # streams in, without materialising all jobs into a list.
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
                    has_filters = failed_only or stage or name_pattern
                    msg = "No jobs match the given filters." if has_filters else "No jobs found."
                    click.echo(msg)
                    return

                # Print each log as soon as its fetch completes.
                futures = [
                    _await_with_name(name, task)
                    for _, (name, task) in log_tasks.items()
                ]
                for coro in asyncio.as_completed(futures):
                    name, text = await coro
                    _write_log(name, text, output_path)
    except (ConfigError, NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


async def _await_with_name(name: str, task: asyncio.Task[str]) -> tuple[str, str]:
    return name, await task


def _write_log(name: str, text: str, output_path: str | None) -> None:
    """Write a single job log to its destination."""
    if output_path is not None:
        out = Path(output_path)
        if out.is_dir():
            (out / f"{name}.log").write_text(text)
        else:
            with open(out, "a") as f:
                f.write(f"─── {name} ───\n{text}\n")
        return
    click.echo(f"─── {name} ───")
    click.echo(text)
