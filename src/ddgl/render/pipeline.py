# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from rich.box import MINIMAL
from rich.table import Table

from ddgl.model.pipeline import Pipeline
from ddgl.render._console import console
from ddgl.render._styles import (
    format_datetime,
    format_duration,
    format_sha,
    format_status,
)


def render_pipeline_table(pipelines: list[Pipeline], *, ref: str | None = None) -> None:
    """Print a Rich table of pipelines."""
    if ref:
        console.print(f"Pipelines for [bold]{ref}[/bold]\n")

    table = Table(box=MINIMAL, show_header=True, header_style="bold")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Ref")
    table.add_column("SHA", no_wrap=True)
    table.add_column("Created", no_wrap=True)

    for p in pipelines:
        table.add_row(
            str(p.id),
            format_status(p.status),
            p.ref,
            format_sha(p.sha),
            format_datetime(p.created_at, short=True),
        )

    console.print(table)


def render_pipeline_detail(pipeline: Pipeline) -> None:
    """Print a key-value detail view for a single pipeline (all fields)."""
    console.print(f"[bold]Pipeline #{pipeline.id}[/bold]\n")

    elapsed = pipeline.elapsed
    duration_str = format_duration(int(elapsed.total_seconds()) if elapsed else None)

    fields = [
        ("Status", format_status(pipeline.status)),
        ("Ref", pipeline.ref or "—"),
        ("SHA", format_sha(pipeline.sha) if pipeline.sha else "—"),
        ("Source", pipeline.source or "—"),
        ("Duration", duration_str),
        ("Created", pipeline.created_at or "—"),
        ("Finished", pipeline.finished_at or "—"),
        ("URL", pipeline.web_url or "—"),
    ]

    for label, value in fields:
        console.print(f"  [dim]{label:<10}[/dim]  {value}")
