# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""``ddgl retry`` — retry failed jobs, or every failed job in a pipeline."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from contextlib import nullcontext

import httpx
import msgspec
import rich_click as click

from ddgl.cache import Cache
from ddgl.cli._options import (
    CACHE_DIR,
    job_filter_options,
    pipeline_resolution_options,
)
from ddgl.client import GitLabClient
from ddgl.config import load_config
from ddgl.constants import JobStatus
from ddgl.core.jobs import filter_jobs, get_jobs, list_jobs
from ddgl.core.pipeline import resolve_pipeline
from ddgl.core.retry import retry_jobs, retry_pipeline
from ddgl.exceptions import (
    ConfigError,
    GitLabAPIError,
    NoPipelineFoundError,
    NotFoundError,
)
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.model.retry import RetryOutcome
from ddgl.render._console import console
from ddgl.render.retry import (
    render_retried_pipeline,
    render_retry_outcomes,
    render_retry_preview,
)


def _stdin_is_tty() -> bool:
    """Whether stdin can carry an interactive answer.

    A named seam rather than an inline `sys.stdin.isatty()`: this is the
    gate that stops a write from firing unconfirmed, so it needs to be
    substitutable in tests, where stdin is never a TTY.
    """
    return sys.stdin.isatty()


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
        sys.exit(2)

    skip_confirm = (ctx.obj or {}).get("yes", False)
    # A deliberate divergence from `jobs get`/`logs`, which auto-confirm when
    # stdin isn't a TTY: those are reads. Firing writes from a script that
    # merely happened to lose its TTY is a different risk class, so this is a
    # hard error instead. --json doesn't bypass it either, for the same reason.
    if not skip_confirm and not _stdin_is_tty():
        click.echo(
            "Error: refusing to retry without confirmation; pass -y/--yes.",
            err=True,
        )
        sys.exit(2)

    no_cache = (ctx.obj or {}).get("no_cache", False)
    exit_code = asyncio.run(
        _retry(
            ref=ref, pipeline_id=pipeline_id, depth=depth, failed_only=failed_only,
            include_allowed_failures=include_allowed_failures, stage=stage,
            name_pattern=name_pattern, job_ids=job_ids, force=force,
            output_json=output_json, skip_confirm=skip_confirm, no_cache=no_cache,
        )
    )
    sys.exit(exit_code)


def _target_of(
    jobs: Sequence[Job], pipeline: Pipeline | None
) -> tuple[int | None, str | None]:
    """The pipeline id and ref to name in output, or (None, None).

    `--job ID` skips pipeline resolution, so the target is recovered from
    the jobs themselves. Jobs spanning several pipelines (or missing the
    back-reference) yield None rather than naming one of them and implying
    the rest belong to it too.
    """
    if pipeline is not None:
        return pipeline.id, pipeline.ref

    pipeline_ids = {job.pipeline_id for job in jobs}
    if len(pipeline_ids) != 1:
        return None, None
    only_id = pipeline_ids.pop()
    if only_id is None:
        return None, None

    refs = {job.ref for job in jobs if job.ref}
    return only_id, refs.pop() if len(refs) == 1 else None


def _outcomes_payload(
    outcomes: Sequence[RetryOutcome],
    *,
    mode: str,
    pipeline_id: int | None,
    ref: str | None,
) -> dict[str, object]:
    return {
        "pipeline_id": pipeline_id,
        "ref": ref,
        "mode": mode,
        "retried": [
            {
                "old_job_id": o.old_job_id,
                "job_name": o.job_name,
                "new_job_id": o.new_job.id,
                "status": str(o.new_job.status),
            }
            for o in outcomes
            if o.new_job is not None
        ],
        "errors": [
            {"old_job_id": o.old_job_id, "job_name": o.job_name, "error": o.error}
            for o in outcomes
            if o.new_job is None
        ],
    }


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
    output_json: bool,
    skip_confirm: bool,
    no_cache: bool,
) -> int:
    targeted = bool(job_ids or failed_only or stage or name_pattern)
    try:
        with Cache.open(CACHE_DIR, bypass=no_cache) as cache:
            config = await load_config(cache=cache)
            async with GitLabClient(config, cache=cache) as client:
                if targeted:
                    return await _retry_targeted(
                        client,
                        ref=ref, pipeline_id=pipeline_id, depth=depth,
                        failed_only=failed_only,
                        include_allowed_failures=include_allowed_failures,
                        stage=stage, name_pattern=name_pattern, job_ids=job_ids,
                        force=force, output_json=output_json,
                        skip_confirm=skip_confirm, cache=cache,
                    )
                return await _retry_bulk(
                    client,
                    ref=ref, pipeline_id=pipeline_id, depth=depth,
                    output_json=output_json, skip_confirm=skip_confirm, cache=cache,
                )
    # Everything reachable here is a failure to work out *what* to retry —
    # bad config, unresolvable ref, unreachable API. The retry calls
    # themselves are caught further in, so they can exit 1 instead.
    except (
        ConfigError,
        NoPipelineFoundError,
        NotFoundError,
        GitLabAPIError,
        httpx.TransportError,
    ) as e:
        click.echo(f"Error: {e}", err=True)
        return 2


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
    with nullcontext() if output_json else console.status("Resolving pipeline…"):
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
        return 1

    if output_json:
        click.echo(
            msgspec.json.encode(
                {
                    "pipeline_id": retried.id,
                    "ref": retried.ref,
                    "mode": "bulk",
                    "status": str(retried.status),
                    # GitLab's pipeline-retry response doesn't say which jobs it
                    # restarted, so these stay empty — present to keep the key
                    # set identical to targeted mode's.
                    "retried": [],
                    "errors": [],
                }
            ).decode()
        )
    else:
        render_retried_pipeline(retried)
    return 0


async def _retry_targeted(
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
    output_json: bool,
    skip_confirm: bool,
    cache: Cache,
) -> int:
    """Retry individually selected jobs, one POST each."""
    pipeline: Pipeline | None = None
    if job_ids:
        # --job wins outright, skipping ref resolution and the other filters,
        # matching how `jobs get` and `logs` already treat it.
        with nullcontext() if output_json else console.status("Fetching jobs…"):
            jobs = await get_jobs(client, job_ids, cache=cache)
    else:
        with nullcontext() if output_json else console.status("Resolving pipeline…"):
            pipeline = await resolve_pipeline(
                client, ref=ref, pipeline_id=pipeline_id, depth=depth, cache=cache,
            )
        scope = JobStatus.FAILED if failed_only else None
        with nullcontext() if output_json else console.status("Fetching jobs…"):
            jobs = [
                j async for j in filter_jobs(
                    list_jobs(client, pipeline.id, scope=scope, cache=cache),
                    failed_only=failed_only,
                    include_allowed_failures=include_allowed_failures,
                    name_pattern=name_pattern,
                    stage=stage,
                )
            ]

    matched = len(jobs)
    if not force:
        jobs = [job for job in jobs if job.is_retryable]

    target_id, target_ref = _target_of(jobs or [], pipeline)

    if not jobs:
        if output_json:
            click.echo(
                msgspec.json.encode(
                    _outcomes_payload(
                        [], mode="targeted",
                        pipeline_id=pipeline.id if pipeline else None,
                        ref=pipeline.ref if pipeline else None,
                    )
                ).decode()
            )
        elif matched:
            click.echo(
                f"{matched} job(s) matched, none retryable (already succeeded or"
                " still running). Use --force to retry anyway."
            )
        else:
            click.echo("No jobs match the given filters.")
        return 0

    if not skip_confirm:
        render_retry_preview(jobs, pipeline_id=target_id, ref=target_ref)
        click.confirm("Continue?", abort=True, err=True)

    outcomes = await retry_jobs(client, jobs)

    if output_json:
        click.echo(
            msgspec.json.encode(
                _outcomes_payload(
                    outcomes, mode="targeted", pipeline_id=target_id, ref=target_ref,
                )
            ).decode()
        )
    else:
        render_retry_outcomes(outcomes, pipeline_id=target_id, ref=target_ref)

    return 1 if any(o.new_job is None for o in outcomes) else 0
