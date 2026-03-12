from __future__ import annotations

import asyncio
import sys

import rich_click as click

from ddgl.cache import Cache
from ddgl.cli._options import CACHE_DIR, pipeline_resolution_options
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.constants import PipelineScope
from ddgl.core.pipeline import list_pipelines, resolve_pipeline
from ddgl.exceptions import ConfigError, NoPipelineFoundError, NotFoundError
from ddgl.render.pipeline import render_pipeline_detail, render_pipeline_table


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
    try:
        result, resolved_ref = asyncio.run(_list(ref, count, PipelineScope(scope) if scope else None))
    except ConfigError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not result:
        click.echo(f"No pipelines found for ref '{resolved_ref}'.")
        return

    render_pipeline_table(result, ref=resolved_ref)


async def _list(
    ref: str | None, count: int, scope: PipelineScope | None
) -> tuple[list, str]:
    config = await load_config()

    if ref is None:
        from ddgl.git import get_current_branch
        ref = await get_current_branch()

    with Cache.open(CACHE_DIR) as cache:
        async with GitLabClient(config) as client:
            result = await list_pipelines(client, ref, scope=scope, count=count, cache=cache)

    return result, ref


@pipelines.command("get")
@pipeline_resolution_options
def pipelines_get(ref: str | None, pipeline_id: int | None, depth: int) -> None:
    """Resolve and display the latest pipeline (or a specific one by ID)."""
    try:
        pipeline = asyncio.run(_get(ref, pipeline_id, depth))
    except (ConfigError, NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    render_pipeline_detail(pipeline)


async def _get(ref: str | None, pipeline_id: int | None, depth: int):
    config = await load_config()
    with Cache.open(CACHE_DIR) as cache:
        async with GitLabClient(config) as client:
            return await resolve_pipeline(
                client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache
            )
