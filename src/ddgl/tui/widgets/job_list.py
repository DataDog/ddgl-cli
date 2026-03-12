from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import DataTable

from ddgl.model.job import Job
from ddgl.tui.widgets.search_bar import fuzzy_match
from ddgl.tui.widgets.status import status_color, status_icon


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    return f"{total // 60}m {total % 60}s"


def _sort_by_stage(jobs: list[Job]) -> list[Job]:
    return sorted(jobs, key=lambda j: (j.stage, j.name))


class JobListPanel(DataTable):
    """Right panel: job list as a DataTable, sorted by stage, with fuzzy filtering."""

    jobs: reactive[list[Job]] = reactive([], always_update=True)
    search_query: reactive[str] = reactive("")

    def on_mount(self) -> None:
        self.add_column("", key="icon", width=3)
        self.add_column("Name", key="name")
        self.add_column("Stage", key="stage")
        self.add_column("Status", key="status")
        self.add_column("Duration", key="duration", width=10)

    def watch_jobs(self, value: list[Job]) -> None:
        self._recompute()

    def watch_search_query(self, value: str) -> None:
        self._recompute()

    def _recompute(self) -> None:
        query = self.search_query
        visible = [
            j for j in self.jobs if fuzzy_match(query, j.name)
        ] if query else list(self.jobs)
        self._repopulate(_sort_by_stage(visible))

    def _repopulate(self, jobs: list[Job]) -> None:
        self.clear()
        for job in jobs:
            color = status_color(job.status)
            self.add_row(
                Text(status_icon(job.status), style=color),
                job.name,
                job.stage,
                Text(str(job.status), style=color),
                _fmt_duration(job.duration),
            )
