from __future__ import annotations

from enum import StrEnum

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import DataTable

from ddgl.model.job import Job
from ddgl.tui.widgets.search_bar import fuzzy_match
from ddgl.tui.widgets.status import status_color, status_icon


class SortMode(StrEnum):
    STAGE = "stage"
    ALPHABETICAL = "alphabetical"
    START_TIME = "start_time"

    def next(self) -> SortMode:
        members = list(SortMode)
        return members[(members.index(self) + 1) % len(members)]

    def label(self) -> str:
        return {
            SortMode.STAGE: "Sort: Stage",
            SortMode.ALPHABETICAL: "Sort: A–Z",
            SortMode.START_TIME: "Sort: Start time",
        }[self]


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    return f"{total // 60}m {total % 60}s"


def _sort_by_stage(jobs: list[Job]) -> list[Job]:
    return sorted(jobs, key=lambda j: (j.stage, j.name))


def _sort_alphabetical(jobs: list[Job]) -> list[Job]:
    return sorted(jobs, key=lambda j: j.name)


def _sort_by_start_time(jobs: list[Job]) -> list[Job]:
    return sorted(jobs, key=lambda j: (j.started_at or "", j.name))


def _apply_sort(jobs: list[Job], mode: SortMode) -> list[Job]:
    if mode == SortMode.STAGE:
        return _sort_by_stage(jobs)
    if mode == SortMode.ALPHABETICAL:
        return _sort_alphabetical(jobs)
    return _sort_by_start_time(jobs)


class JobListPanel(DataTable):
    """Right panel: job list as a DataTable with fuzzy filtering and sort modes."""

    jobs: reactive[list[Job]] = reactive([], always_update=True)
    search_query: reactive[str] = reactive("")
    sort_mode: reactive[SortMode] = reactive(SortMode.STAGE)

    def on_mount(self) -> None:
        self.add_column("", key="icon", width=3)
        self.add_column("Name", key="name")
        self.add_column("Stage", key="stage")
        self.add_column("Status", key="status")
        self.add_column("Duration", key="duration", width=10)
        self._update_border_title()

    def watch_jobs(self, value: list[Job]) -> None:
        self._recompute()

    def watch_search_query(self, value: str) -> None:
        self._recompute()

    def watch_sort_mode(self, value: SortMode) -> None:
        self._update_border_title()
        self._recompute()

    def _update_border_title(self) -> None:
        self.border_title = self.sort_mode.label()

    def _recompute(self) -> None:
        query = self.search_query
        visible = (
            [j for j in self.jobs if fuzzy_match(query, j.name)]
            if query
            else list(self.jobs)
        )
        self._repopulate(_apply_sort(visible, self.sort_mode))

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
