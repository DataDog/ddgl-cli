from __future__ import annotations

import asyncio
import logging
import os
import sys

import click

from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.exceptions import ConfigError
from ddgl.git import get_current_branch

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"


def setup_logging(verbosity: int = 0) -> None:
    """Configure the ddgl logger hierarchy.

    Level resolution (first match wins):
        1. verbosity >= 2 -> DEBUG
        2. verbosity == 1 -> INFO
        3. DDGL_LOG_LEVEL env var
        4. Default: WARNING
    """
    logging.addLevelName(logging.DEBUG, "DEBG")
    logging.addLevelName(logging.INFO, "INFO")
    logging.addLevelName(logging.WARNING, "WARN")
    logging.addLevelName(logging.ERROR, "ERRO")
    logging.addLevelName(logging.CRITICAL, "CRIT")

    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    else:
        env_level = os.environ.get("DDGL_LOG_LEVEL", "").upper()
        env_val = getattr(logging, env_level, None) if env_level else None
        level = env_val if isinstance(env_val, int) else logging.WARNING

    ddgl_logger = logging.getLogger("ddgl")
    ddgl_logger.setLevel(level)

    if not ddgl_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
        ddgl_logger.addHandler(handler)

    ddgl_logger.propagate = False


@click.group()
@click.version_option(package_name="ddgl")
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v/-vv).")
def main(verbose: int) -> None:
    """ddgl — Terminal-based GitLab client."""
    setup_logging(verbose)


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
        page = await client.fetch_pipelines(ref=ref, per_page=count)

    if not page.items:
        click.echo(f"No pipelines found for ref '{ref}'.")
        return

    for p in page.items:
        click.echo(f"#{p.id:<12} {p.status:<12} {p.ref}")


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
