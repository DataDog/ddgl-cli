# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from rich.box import MINIMAL
from rich.table import Table
from rich.text import Text

from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.render._console import console
from ddgl.render._styles import format_datetime, format_duration, format_status


def render_job_table(jobs: list[Job], *, pipeline: Pipeline | None = None) -> None:
    """Print a Rich table of jobs, grouped by stage."""
    if pipeline is not None:
        console.print(
            f"Jobs for pipeline [bold]#{pipeline.id}[/bold]"
            f" — {pipeline.ref} ({format_status(pipeline.status)})\n"
        )

    # Group jobs by stage, preserving order of first appearance.
    stages: dict[str, list[Job]] = {}
    for job in jobs:
        stages.setdefault(job.stage, []).append(job)

    for stage, stage_jobs in stages.items():
        console.print(f"  [dim]{stage}[/dim]")

        table = Table(box=MINIMAL, show_header=True, header_style="bold", padding=(0, 1))
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Name")
        table.add_column("Status", no_wrap=True)
        table.add_column("Duration", no_wrap=True, justify="right")
        table.add_column("Started", no_wrap=True)

        for j in stage_jobs:
            name_cell = Text(j.name)
            if j.failure_reason:
                name_cell.append(f"  {j.failure_reason}", style="dim")

            table.add_row(
                str(j.id),
                name_cell,
                format_status(j.status),
                format_duration(j.duration),
                format_datetime(j.started_at, short=True),
            )

        console.print(table)
        console.print()


def render_job_detail(job: Job) -> None:
    """Print a key-value detail view for a single job (all fields)."""
    console.print(f"[bold]Job #{job.id} — {job.name}[/bold]\n")

    fields = [
        ("Stage", job.stage or "—"),
        ("Status", format_status(job.status)),
        ("Ref", job.ref or "—"),
        ("Duration", format_duration(job.duration)),
        ("Allow failure", "yes" if job.allow_failure else "no"),
        ("Failure reason", job.failure_reason or "—"),
        ("Created", job.created_at or "—"),
        ("Started", job.started_at or "—"),
        ("Finished", job.finished_at or "—"),
        ("URL", job.web_url or "—"),
    ]

    for label, value in fields:
        console.print(f"  [dim]{label:<16}[/dim]  {value}")
