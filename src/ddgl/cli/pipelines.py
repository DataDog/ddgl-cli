from __future__ import annotations

import asyncio
import sys

import click

from ddgl.cache import Cache
from ddgl.cli._options import CACHE_DIR, pipeline_resolution_options
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.constants import PipelineScope
from ddgl.core.pipeline import list_pipelines, resolve_pipeline
from ddgl.exceptions import ConfigError, NoPipelineFoundError, NotFoundError


@click.group()
def pipelines() -> None:
    """Inspect GitLab pipelines."""


@pipelines.command("list")
@click.option("--ref", default=None, help="Git ref (default: current branch).")
@click.option("-n", "--count", default=20, show_default=True, help="Max pipelines to show.")
@click.option(
    "--scope",
    type=click.Choice([s.value for s in PipelineScope], case_sensitive=False),
    default=None,
    help="Pipeline scope filter.",
)
def pipelines_list(ref: str | None, count: int, scope: str | None) -> None:
    """List recent pipelines for a ref."""
    asyncio.run(_list(ref, count, PipelineScope(scope) if scope else None))


async def _list(ref: str | None, count: int, scope: PipelineScope | None) -> None:
    try:
        config = await load_config()
    except ConfigError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if ref is None:
        from ddgl.git import get_current_branch
        ref = await get_current_branch()

    with Cache.open(CACHE_DIR) as cache:
        async with GitLabClient(config) as client:
            result = await list_pipelines(client, ref, scope=scope, count=count, cache=cache)

    if not result:
        click.echo(f"No pipelines found for ref '{ref}'.")
        return

    for p in result:
        click.echo(f"#{p.id:<10} {p.status:<12} {p.ref}")


@pipelines.command("get")
@pipeline_resolution_options
def pipelines_get(ref: str | None, pipeline_id: int | None, depth: int) -> None:
    """Resolve and display the latest pipeline (or a specific one by ID)."""
    asyncio.run(_get(ref, pipeline_id, depth))


async def _get(ref: str | None, pipeline_id: int | None, depth: int) -> None:
    try:
        config = await load_config()
    except ConfigError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    try:
        with Cache.open(CACHE_DIR) as cache:
            async with GitLabClient(config) as client:
                p = await resolve_pipeline(
                    client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache
                )
    except (ConfigError, NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    elapsed = p.elapsed
    if elapsed:
        total = int(elapsed.total_seconds())
        duration_str = f"{total // 60}m {total % 60}s"
    else:
        duration_str = "—"

    click.echo(f"Pipeline #{p.id}")
    click.echo(f"  Status:   {p.status}")
    click.echo(f"  Ref:      {p.ref}")
    click.echo(f"  SHA:      {p.sha[:12] if p.sha else '—'}")
    click.echo(f"  Duration: {duration_str}")
    if p.web_url:
        click.echo(f"  URL:      {p.web_url}")
