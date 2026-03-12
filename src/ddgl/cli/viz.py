from __future__ import annotations

import asyncio
import sys

import click

from ddgl.cache import Cache
from ddgl.cli._options import CACHE_DIR, pipeline_resolution_options
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.core.pipeline import resolve_pipeline
from ddgl.exceptions import ConfigError, NoPipelineFoundError, NotFoundError
from ddgl.tui.app import PipelineViewer


@click.command("viz")
@pipeline_resolution_options
def viz(ref: str | None, pipeline_id: int | None, depth: int) -> None:
    """Open the interactive pipeline viewer."""
    asyncio.run(_viz(ref, pipeline_id, depth))


async def _viz(ref: str | None, pipeline_id: int | None, depth: int) -> None:
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
                app = PipelineViewer(pipeline, client, cache)
                await app.run_async()
    except (NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
