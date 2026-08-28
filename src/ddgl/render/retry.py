# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from collections.abc import Sequence

from rich.box import MINIMAL
from rich.table import Table

from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.model.retry import RetryOutcome
from ddgl.render._console import console, err_console
from ddgl.render._styles import format_job_status, format_status


def target_suffix(pipeline_id: int | None, ref: str | None) -> str:
    """" in pipeline #123 (main)", or "" when the pipeline isn't known.

    `ddgl retry --job ID` skips pipeline resolution, so the pipeline is
    only known there via `Job.pipeline_id` — and not at all if the payload
    omitted it. Shared by the confirmation prompt and the result header so
    the two always name the same target the same way.
    """
    if pipeline_id is None:
        return ""
    ref_part = f" ({ref})" if ref else ""
    return f" in pipeline #{pipeline_id}{ref_part}"


def _table(*columns: str) -> Table:
    """A job table styled like render/job.py's, with the given columns.

    First column is dimmed (stage), the rest plain — matches the
    stage-as-secondary-information treatment in render_job_table.
    """
    table = Table(box=MINIMAL, show_header=True, header_style="bold", padding=(0, 1))
    table.add_column(columns[0], style="dim", no_wrap=True)
    for column in columns[1:]:
        table.add_column(column, no_wrap=True)
    return table


def render_retry_preview(jobs: Sequence[Job], *, pipeline_id: int | None, ref: str | None) -> None:
    """Print the job list shown above the confirmation prompt.

    Goes to stderr, like the prompt it introduces: it's interactive UI, not
    output, and keeping it off stdout is what lets `--json` stay a clean
    single parseable object even when the prompt is shown.
    """
    err_console.print(
        f"This will retry {len(jobs)} job(s){target_suffix(pipeline_id, ref)}:"
    )
    table = _table("Stage", "Job", "Status")
    for job in jobs:
        table.add_row(job.stage, job.name, format_job_status(job))
    err_console.print(table)


def render_retry_outcomes(
    outcomes: Sequence[RetryOutcome], *, pipeline_id: int | None, ref: str | None
) -> None:
    """Print the post-retry result table, then any per-job failures."""
    # Pair each success with its new job up front, so the row builder below
    # needs no None-narrowing on a field the filter already guaranteed.
    retried = [(o, o.new_job) for o in outcomes if o.new_job is not None]
    failed = [o for o in outcomes if o.new_job is None]

    if retried:
        console.print(
            f"Retried {len(retried)} job(s){target_suffix(pipeline_id, ref)}\n"
        )
        table = _table("Stage", "Job", "Old", "New", "Status")
        for outcome, new_job in retried:
            table.add_row(
                new_job.stage,
                outcome.job_name,
                str(outcome.old_job_id),
                str(new_job.id),
                format_status(str(new_job.status)),
            )
        console.print(table)

    if failed:
        console.print(f"\n{len(failed)} job(s) could not be retried:")
        for outcome in failed:
            console.print(f"  [red]{outcome.job_name}[/red]  {_one_line(outcome.error)}")


def _one_line(error: str | None) -> str:
    """Collapse whitespace so one failure occupies one list entry.

    Client errors embed a slice of the raw response body; a 5xx served by
    a proxy is often a multi-line HTML page, which would otherwise scatter
    a single failure across the list.
    """
    return " ".join(error.split()) if error else "unknown error"


def render_retried_pipeline(pipeline: Pipeline) -> None:
    """Print bulk mode's result.

    GitLab's pipeline-retry endpoint returns only the pipeline — it does
    not report which jobs it restarted — so this deliberately reports
    nothing per-job, and points at `ddgl attach` for what happens next.
    """
    console.print(
        f"Retried pipeline [bold]#{pipeline.id}[/bold]"
        f" ({pipeline.ref}) — now {format_status(str(pipeline.status))}"
    )
    if pipeline.web_url:
        console.print(f"  {pipeline.web_url}")
    console.print(f"  Watch it with:  [dim]ddgl attach --pipeline {pipeline.id}[/dim]")
