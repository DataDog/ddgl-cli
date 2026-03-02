from __future__ import annotations

import asyncio
import sys

import click

from ddgl.client import GitLabClient
from ddgl.config import ConfigError, load_config
from ddgl.git import get_current_branch


@click.group()
@click.version_option(package_name="ddgl")
def main() -> None:
    """ddgl — Terminal-based GitLab client."""


@main.command()
@click.option("--ref", default=None, help="Git ref (default: current branch).")
@click.option("-n", "--count", default=20, help="Number of pipelines to show.")
def pipelines(ref: str | None, count: int) -> None:
    """List recent pipelines for the current branch."""
    asyncio.run(_pipelines(ref, count))


async def _pipelines(ref: str | None, count: int) -> None:
    config = load_config()
    if ref is None:
        ref = await get_current_branch()

    async with GitLabClient(config) as client:
        items = await client.get_pipelines(ref=ref, per_page=count)

    if not items:
        click.echo(f"No pipelines found for ref '{ref}'.")
        return

    for p in items:
        status = p["status"]
        pid = p["id"]
        click.echo(f"#{pid:<12} {status:<12} {p.get('ref', '')}")


@main.command()
@click.argument("job_id", type=int)
def logs(job_id: int) -> None:
    """Fetch the log output of a GitLab job."""
    asyncio.run(_logs(job_id))


async def _logs(job_id: int) -> None:
    try:
        config = load_config()
    except ConfigError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    async with GitLabClient(config) as client:
        log = await client.get_job_log(job_id)

    click.echo(log)
