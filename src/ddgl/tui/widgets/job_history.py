# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Same-job history widget for the job detail screen."""
from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import work
from textual.widgets import DataTable, LoadingIndicator, Static

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.core.jobs import list_jobs
from ddgl.core.pipeline import list_pipelines
from ddgl.model.job import Job
from ddgl.tui.widgets.status import job_status_color, job_status_icon, job_status_label

from .job_list import _fmt_duration

_HISTORY_PIPELINE_COUNT = 10

# ---------------------------------------------------------------------------
# Pure helpers (testable)
# ---------------------------------------------------------------------------


@dataclass
class HistoryEntry:
    pipeline_id: int
    job: Job
    pipeline_created_at: str


async def fetch_job_history(
    client: GitLabClient,
    job_name: str,
    ref: str,
    *,
    cache: Cache | None = None,
) -> list[HistoryEntry]:
    """Fetch same-named job across recent pipelines on *ref*."""
    pipelines = await list_pipelines(client, ref, count=_HISTORY_PIPELINE_COUNT, cache=cache)

    entries: list[HistoryEntry] = []
    for pipeline in pipelines:
        async for job in list_jobs(client, pipeline.id, cache=cache):
            if job.name == job_name:
                entries.append(
                    HistoryEntry(
                        pipeline_id=pipeline.id,
                        job=job,
                        pipeline_created_at=pipeline.created_at,
                    )
                )
                break  # found the match for this pipeline
    return entries


def _fmt_date(iso: str) -> str:
    """Extract 'Mar 12' from an ISO-8601 timestamp."""
    if not iso or len(iso) < 10:
        return "—"
    months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    try:
        month = months[int(iso[5:7]) - 1]
        day = iso[8:10].lstrip("0")
        return f"{month} {day}"
    except (ValueError, IndexError):
        return iso[:10]


def _status_cell(job: Job) -> Text:
    """Coloured status cell for a history row: icon + label."""
    return Text(f"{job_status_icon(job)} {job_status_label(job)}", style=job_status_color(job))


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class JobHistoryPanel(Static):
    """Container that fetches and displays same-job history."""

    def __init__(
        self,
        job: Job,
        client: GitLabClient,
        cache: Cache | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._job = job
        self._client = client
        self._cache = cache

    def compose(self):  # noqa: ANN201
        yield LoadingIndicator(id="history-loading")

    def on_mount(self) -> None:
        self._load_history()

    @work
    async def _load_history(self) -> None:
        loading = self.query_one("#history-loading", LoadingIndicator)
        try:
            entries = await fetch_job_history(
                self._client,
                self._job.name,
                self._job.ref,
                cache=self._cache,
            )
        except Exception as e:
            loading.display = False
            self.mount(Static(f"Failed to load history: {e}"))
            return

        loading.display = False

        if not entries:
            self.mount(Static("No history found for this job.", id="history-empty"))
            return

        table = DataTable(id="history-table", cursor_type="row")
        self.mount(table)
        table.add_column("Pipeline", key="pipeline", width=12)
        table.add_column("Status", key="status", width=14)
        table.add_column("Duration", key="duration", width=10)
        table.add_column("Date", key="date", width=8)

        for entry in entries:
            color = job_status_color(entry.job)
            table.add_row(
                Text(f"#{entry.pipeline_id}", style="dim"),
                _status_cell(entry.job),
                Text(_fmt_duration(entry.job.duration), style=color),
                Text(_fmt_date(entry.pipeline_created_at)),
                key=str(entry.pipeline_id),
            )
