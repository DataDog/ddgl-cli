# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import asyncio
import sys
from contextlib import nullcontext

import msgspec
import rich_click as click

from ddgl.cache import Cache
from ddgl.cli._options import CACHE_DIR, output_options, pipeline_resolution_options
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.constants import PipelineScope
from ddgl.core.pipeline import list_pipelines, resolve_pipeline
from ddgl.exceptions import ConfigError, NoPipelineFoundError, NotFoundError
from ddgl.render._console import console
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
@output_options
@click.pass_context
def pipelines_list(
    ctx: click.Context, ref: str | None, count: int, scope: str | None, output_json: bool, no_pager: bool
) -> None:
    """List recent pipelines for a ref."""
    no_cache = (ctx.obj or {}).get("no_cache", False)
    try:
        result, resolved_ref = asyncio.run(
            _list(ref, count, PipelineScope(scope) if scope else None, quiet=output_json, no_cache=no_cache)
        )
    except ConfigError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not result:
        if output_json:
            click.echo("[]")
        else:
            click.echo(f"No pipelines found for ref '{resolved_ref}'.")
        return

    if output_json:
        click.echo(msgspec.json.encode(result).decode())
        return

    # +5: title line, table header, separator, bottom border, shell prompt
    use_pager = not no_pager and console.is_terminal and len(result) + 5 > console.height
    with console.pager(styles=True) if use_pager else nullcontext():
        render_pipeline_table(result, ref=resolved_ref)


async def _list(
    ref: str | None, count: int, scope: PipelineScope | None, *, quiet: bool = False, no_cache: bool = False
) -> tuple[list, str]:
    if ref is None:
        from ddgl.git import get_current_branch
        ref = await get_current_branch()

    with Cache.open(CACHE_DIR, bypass=no_cache) as cache:
        config = await load_config(cache=cache)
        async with GitLabClient(config, cache=cache) as client:
            spinner = nullcontext() if quiet else console.status("Fetching pipelines…")
            with spinner:
                result = await list_pipelines(client, ref, scope=scope, count=count, cache=cache)

    return result, ref


@pipelines.command("get")
@pipeline_resolution_options
@output_options
@click.pass_context
def pipelines_get(
    ctx: click.Context, ref: str | None, pipeline_id: int | None, depth: int, output_json: bool, no_pager: bool
) -> None:
    """Resolve and display the latest pipeline (or a specific one by ID)."""
    no_cache = (ctx.obj or {}).get("no_cache", False)
    try:
        pipeline = asyncio.run(_get(ref, pipeline_id, depth, quiet=output_json, no_cache=no_cache))
    except (ConfigError, NoPipelineFoundError, NotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if output_json:
        click.echo(msgspec.json.encode(pipeline).decode())
        return

    render_pipeline_detail(pipeline)  # always short (~10 field lines), no pager needed


async def _get(ref: str | None, pipeline_id: int | None, depth: int, *, quiet: bool = False, no_cache: bool = False):
    with Cache.open(CACHE_DIR, bypass=no_cache) as cache:
        config = await load_config(cache=cache)
        async with GitLabClient(config, cache=cache) as client:
            spinner = nullcontext() if quiet else console.status("Resolving pipeline…")
            with spinner:
                return await resolve_pipeline(
                    client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache
                )
