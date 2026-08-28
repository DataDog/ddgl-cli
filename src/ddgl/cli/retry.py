# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""``ddgl retry`` — retry failed jobs, or every failed job in a pipeline."""

from __future__ import annotations

import asyncio
import sys

import httpx
import rich_click as click

from ddgl.cache import Cache
from ddgl.cli._options import (
    CACHE_DIR,
    job_filter_options,
    pipeline_resolution_options,
    status_spinner,
    stdin_is_tty,
)
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.core.pipeline import resolve_pipeline
from ddgl.core.retry import (
    retry_jobs,
    retry_pipeline,
    select_by_id,
    select_in_pipeline,
)
from ddgl.exceptions import (
    ConfigError,
    GitLabAPIError,
    NoPipelineFoundError,
    NotFoundError,
)
from ddgl.model.retry import RetrySelection
from ddgl.render.retry import (
    render_nothing_to_retry,
    render_retried_pipeline,
    render_retry_outcomes,
    render_retry_preview,
)

# Exit codes. 1 is reserved for "GitLab refused a retry" so a script can tell
# a rejected write apart from never having got as far as attempting one.
_OK = 0
_RETRY_REJECTED = 1
_USAGE_ERROR = 2


@click.command()
@pipeline_resolution_options
@job_filter_options
@click.option(
    "--job", "job_ids", multiple=True, type=int,
    help="Retry a specific job by ID (repeatable). Overrides `-f`/`--stage`/`--name`.",
)
@click.option(
    "--force", is_flag=True, default=False,
    help=(
        "Retry every matched job, including ones that already succeeded or are "
        "still running. Requires a job filter."
    ),
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON.")
@click.pass_context
def retry_cmd(
    ctx: click.Context,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    include_allowed_failures: bool,
    stage: str | None,
    name_pattern: str | None,
    job_ids: tuple[int, ...],
    force: bool,
    output_json: bool,
) -> None:
    """Retry failed jobs.

    With **no** job filter, retries every failed *and canceled* job in the
    pipeline in one call, exactly like the web UI's Retry button — GitLab
    picks the set.

    With any of `--job`/`-f`/`--stage`/`--name`, retries just the matching
    jobs. Only failed or canceled jobs are retried; `--force` widens that
    to whatever the filter matched.

    Always asks for confirmation unless `-y`/`--yes` is given.

    Exit codes: 0 retried (or nothing to do), 1 GitLab rejected a retry
    (or the prompt was declined), 2 usage/config/resolution error.
    """
    targeted = bool(job_ids or failed_only or stage or name_pattern)

    if force and not targeted:
        click.echo(
            "Error: --force requires a job filter (--job/-f/--stage/--name)."
            " Without one, GitLab picks which jobs to retry.",
            err=True,
        )
        sys.exit(_USAGE_ERROR)

    skip_confirm = (ctx.obj or {}).get("yes", False)
    # A deliberate divergence from `jobs get`/`logs`, which auto-confirm when
    # stdin isn't a TTY: those are reads. Firing writes from a script that
    # merely happened to lose its TTY is a different risk class, so this is a
    # hard error instead. --json doesn't bypass it either, for the same reason.
    if not skip_confirm and not stdin_is_tty():
        click.echo(
            "Error: refusing to retry without confirmation; pass -y/--yes.",
            err=True,
        )
        sys.exit(_USAGE_ERROR)

    exit_code = asyncio.run(
        _retry(
            ref=ref, pipeline_id=pipeline_id, depth=depth, failed_only=failed_only,
            include_allowed_failures=include_allowed_failures, stage=stage,
            name_pattern=name_pattern, job_ids=job_ids, force=force, targeted=targeted,
            output_json=output_json, skip_confirm=skip_confirm,
            no_cache=(ctx.obj or {}).get("no_cache", False),
        )
    )
    sys.exit(exit_code)


async def _retry(
    *,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    include_allowed_failures: bool,
    stage: str | None,
    name_pattern: str | None,
    job_ids: tuple[int, ...],
    force: bool,
    targeted: bool,
    output_json: bool,
    skip_confirm: bool,
    no_cache: bool,
) -> int:
    try:
        with Cache.open(CACHE_DIR, bypass=no_cache) as cache:
            config = await load_config(cache=cache)
            async with GitLabClient(config, cache=cache) as client:
                if not targeted:
                    return await _retry_bulk(
                        client, ref=ref, pipeline_id=pipeline_id, depth=depth,
                        output_json=output_json, skip_confirm=skip_confirm, cache=cache,
                    )

                selection = await _select(
                    client, ref=ref, pipeline_id=pipeline_id, depth=depth,
                    failed_only=failed_only,
                    include_allowed_failures=include_allowed_failures,
                    stage=stage, name_pattern=name_pattern, job_ids=job_ids,
                    force=force, quiet=output_json, cache=cache,
                )
                return await _retry_selected(
                    client, selection,
                    output_json=output_json, skip_confirm=skip_confirm,
                )
    # Everything reaching here is a failure to work out *what* to retry — bad
    # config, unresolvable ref, unreachable API. The retry calls themselves are
    # caught further in, so a rejected write exits 1 rather than 2.
    except (
        ConfigError,
        NoPipelineFoundError,
        NotFoundError,
        GitLabAPIError,
        httpx.TransportError,
    ) as e:
        click.echo(f"Error: {e}", err=True)
        return _USAGE_ERROR


async def _retry_bulk(
    client: GitLabClient,
    *,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    output_json: bool,
    skip_confirm: bool,
    cache: Cache,
) -> int:
    """Retry a whole pipeline via GitLab's pipeline-retry endpoint.

    Deliberately does not pre-fetch the job list: GitLab decides the set,
    so listing jobs first would only show a set we can't promise matches.
    """
    with status_spinner("Resolving pipeline…", quiet=output_json):
        pipeline = await resolve_pipeline(
            client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache,
        )

    if not skip_confirm:
        click.confirm(
            f"This will retry ALL failed and canceled jobs in pipeline"
            f" #{pipeline.id} ({pipeline.ref}). Continue?",
            abort=True,
            err=True,
        )

    try:
        retried = await retry_pipeline(client, pipeline.id)
    except (NotFoundError, GitLabAPIError) as e:
        click.echo(f"Error: could not retry pipeline #{pipeline.id}: {e}", err=True)
        return _RETRY_REJECTED

    render_retried_pipeline(retried, as_json=output_json)
    return _OK


async def _select(
    client: GitLabClient,
    *,
    ref: str | None,
    pipeline_id: int | None,
    depth: int,
    failed_only: bool,
    include_allowed_failures: bool,
    stage: str | None,
    name_pattern: str | None,
    job_ids: tuple[int, ...],
    force: bool,
    quiet: bool,
    cache: Cache,
) -> RetrySelection:
    """Work out which jobs to retry, from IDs or from a pipeline's jobs."""
    if job_ids:
        # --job wins outright, skipping ref resolution and the other filters,
        # matching how `jobs get` and `logs` already treat it.
        with status_spinner("Fetching jobs…", quiet=quiet):
            return await select_by_id(client, job_ids, force=force, cache=cache)

    with status_spinner("Resolving pipeline…", quiet=quiet):
        pipeline = await resolve_pipeline(
            client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache,
        )
    with status_spinner("Fetching jobs…", quiet=quiet):
        return await select_in_pipeline(
            client, pipeline,
            failed_only=failed_only,
            include_allowed_failures=include_allowed_failures,
            stage=stage, name_pattern=name_pattern, force=force, cache=cache,
        )


async def _retry_selected(
    client: GitLabClient,
    selection: RetrySelection,
    *,
    output_json: bool,
    skip_confirm: bool,
) -> int:
    """Confirm, retry, report."""
    if not selection.jobs:
        render_nothing_to_retry(selection, as_json=output_json)
        return _OK

    if not skip_confirm:
        render_retry_preview(
            selection.jobs, pipeline_id=selection.pipeline_id, ref=selection.ref,
        )
        click.confirm("Continue?", abort=True, err=True)

    outcomes = await retry_jobs(client, selection.jobs)
    render_retry_outcomes(
        outcomes,
        pipeline_id=selection.pipeline_id,
        ref=selection.ref,
        as_json=output_json,
    )
    return _RETRY_REJECTED if any(o.new_job is None for o in outcomes) else _OK
