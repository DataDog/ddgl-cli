from __future__ import annotations

from enum import StrEnum

from rich.text import Text
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import DataTable

from ddgl.model.job import Job
from ddgl.tui.widgets.search_bar import fuzzy_match
from ddgl.tui.widgets.status import status_color, status_icon


class SortMode(StrEnum):
    STAGE = "stage"
    ALPHABETICAL = "alphabetical"
    START_TIME = "start_time"

    def next(self) -> SortMode:
        return _NEXT_SORT[self]

    def label(self) -> str:
        return {
            SortMode.STAGE: "Sort: Stage",
            SortMode.ALPHABETICAL: "Sort: A–Z",
            SortMode.START_TIME: "Sort: Start time",
        }[self]


_NEXT_SORT: dict[SortMode, SortMode] = {
    SortMode.STAGE: SortMode.ALPHABETICAL,
    SortMode.ALPHABETICAL: SortMode.START_TIME,
    SortMode.START_TIME: SortMode.STAGE,
}


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

    _search_timer: Timer | None = None

    def on_mount(self) -> None:
        self.add_column("", key="icon", width=3)
        self.add_column("Stage", key="stage")
        self.add_column("Status", key="status")
        self.add_column("Duration", key="duration", width=10)
        self.add_column("Name", key="name")
        self._update_border_title()

    def watch_jobs(self, value: list[Job]) -> None:
        self._recompute()

    def watch_search_query(self, value: str) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(0.08, self._recompute)

    def watch_sort_mode(self, value: SortMode) -> None:
        self._recompute()

    def _update_border_title(
        self, visible: int | None = None, total: int | None = None
    ) -> None:
        label = self.sort_mode.label()
        if total is None:
            self.border_title = label
        elif visible == total:
            self.border_title = f"{label}  ·  {total} jobs"
        else:
            self.border_title = f"{label}  ·  {visible} / {total}"

    def _recompute(self) -> None:
        query = self.search_query
        total = len(self.jobs)
        visible = (
            [j for j in self.jobs if fuzzy_match(query, j.name)]
            if query
            else list(self.jobs)
        )
        self._update_border_title(len(visible), total)
        self._repopulate(_apply_sort(visible, self.sort_mode))

    def _repopulate(self, jobs: list[Job]) -> None:
        self.clear()
        if not self.jobs:
            self.border_subtitle = "No jobs"
            return
        if not jobs:
            self.border_subtitle = "No jobs match your filter"
            return
        self.border_subtitle = ""
        for job in jobs:
            color = status_color(job.status)
            self.add_row(
                Text(status_icon(job.status), style=color),
                job.stage,
                Text(str(job.status), style=color),
                _fmt_duration(job.duration),
                job.name,
            )
