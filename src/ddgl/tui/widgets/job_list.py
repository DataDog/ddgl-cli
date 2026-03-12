from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import groupby

from rich.text import Text
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import DataTable

from ddgl.model.job import Job
from ddgl.tui.widgets.search_bar import FilterSpec, fuzzy_match
from ddgl.tui.widgets.status import status_color, status_icon

# ---------------------------------------------------------------------------
# Sort modes
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Display row types
# ---------------------------------------------------------------------------


@dataclass
class _JobRow:
    job: Job
    indent: bool = False  # True when shown as a child of an expanded group


@dataclass
class _GroupRow:
    key: str  # stable unique key for this group, e.g. "group:build-image:build"
    base_name: str
    stage: str
    jobs: list[Job] = field(default_factory=list)


_DisplayRow = _JobRow | _GroupRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    return f"{total // 60}m {total % 60}s"


def matrix_base_name(name: str) -> str:
    """Strip matrix parameter suffix: 'build: [x86, arm]' → 'build'."""
    for sep in (": [", ":[", " ["):
        if sep in name:
            return name[: name.index(sep)].strip()
    return name


_STATUS_PRIORITY: dict[str, int] = {
    "failed": 0,
    "canceled": 1,
    "canceling": 2,
    "running": 3,
    "pending": 4,
    "manual": 5,
    "skipped": 6,
    "success": 7,
    "created": 8,
}


def _worst_status(jobs: list[Job]) -> str:
    return min(
        (str(j.status) for j in jobs),
        key=lambda s: _STATUS_PRIORITY.get(s, 99),
    )


def _status_summary(jobs: list[Job]) -> Text:
    """Compact coloured count-per-status: '✗1 ✓2'."""
    counts = Counter(str(j.status) for j in jobs)
    line = Text()
    for status in ("failed", "canceled", "running", "pending", "skipped", "success"):
        n = counts.get(status, 0)
        if n:
            line.append(f"{status_icon(status)}{n} ", style=status_color(status))
    return line


def _sum_duration(jobs: list[Job]) -> float | None:
    durations = [j.duration for j in jobs if j.duration is not None]
    return sum(durations) if durations else None


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def _apply_filter(jobs: list[Job], spec: FilterSpec) -> list[Job]:
    """Return jobs that pass all three filter predicates in *spec*."""
    result = jobs
    if spec.statuses:
        result = [j for j in result if str(j.status).lower() in spec.statuses]
    if spec.stages:
        result = [j for j in result if j.stage.lower() in spec.stages]
    if spec.text:
        result = [j for j in result if fuzzy_match(spec.text, j.name)]
    return result


def _filter_is_active(spec: FilterSpec) -> bool:
    return bool(spec.text or spec.statuses or spec.stages)


# ---------------------------------------------------------------------------
# Matrix grouping
# ---------------------------------------------------------------------------


def _group_jobs(jobs: list[Job]) -> list[_DisplayRow]:
    """Group matrix jobs into GroupRows; singletons become plain JobRows.

    Jobs are first sorted by (stage, base_name) so that members of the same
    matrix group are adjacent, then grouped by (matrix_base_name, stage).
    Groups with a single member are emitted as a flat _JobRow.
    """
    # Use a stable sort so the external sort order is preserved within groups.
    keyed = sorted(jobs, key=lambda j: (j.stage, matrix_base_name(j.name)))
    rows: list[_DisplayRow] = []
    for (stage, base), members in groupby(
        keyed, key=lambda j: (j.stage, matrix_base_name(j.name))
    ):
        member_list = list(members)
        if len(member_list) == 1:
            rows.append(_JobRow(member_list[0]))
        else:
            key = f"group:{base}:{stage}"
            rows.append(
                _GroupRow(key=key, base_name=base, stage=stage, jobs=member_list)
            )
    return rows


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class JobListPanel(DataTable):
    """Right panel: job list as a DataTable with structured filtering and sort modes."""

    jobs: reactive[list[Job]] = reactive([], always_update=True)
    filter_spec: reactive[FilterSpec] = reactive(FilterSpec, always_update=True)
    sort_mode: reactive[SortMode] = reactive(SortMode.STAGE)

    _filter_timer: Timer | None = None
    _job_by_row_key: dict[str, Job]
    _expanded_groups: set[str]

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._job_by_row_key = {}
        self._expanded_groups = set()

    def get_selected_job(self) -> Job | None:
        """Return the Job under the cursor, or None (including for group rows)."""
        if not self.rows:
            return None
        try:
            cell_key = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return None
        return self._job_by_row_key.get(str(cell_key.row_key.value))

    def on_mount(self) -> None:
        self.add_column("", key="icon", width=3)
        self.add_column("Stage", key="stage")
        self.add_column("Status", key="status")
        self.add_column("Duration", key="duration", width=10)
        self.add_column("Name", key="name")
        self._update_border_title()

    def watch_jobs(self, value: list[Job]) -> None:
        self._recompute()

    def watch_filter_spec(self, value: FilterSpec) -> None:
        if self._filter_timer is not None:
            self._filter_timer.stop()
        self._filter_timer = self.set_timer(0.08, self._recompute)

    def watch_sort_mode(self, value: SortMode) -> None:
        self._recompute()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Toggle group rows; individual job rows are handled by the app."""
        key = str(event.row_key.value)
        if not key.startswith("group:"):
            return
        event.stop()
        if key in self._expanded_groups:
            self._expanded_groups.discard(key)
        else:
            self._expanded_groups.add(key)
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
        total = len(self.jobs)
        visible = _apply_filter(self.jobs, self.filter_spec)
        self._update_border_title(len(visible), total)

        if _filter_is_active(self.filter_spec):
            # Show flat when any filter is active — easier to scan results.
            display_rows: list[_DisplayRow] = [
                _JobRow(j) for j in _apply_sort(visible, self.sort_mode)
            ]
        else:
            display_rows = _group_jobs(_apply_sort(visible, self.sort_mode))

        self._repopulate(display_rows)

    def _repopulate(self, display_rows: list[_DisplayRow]) -> None:
        self.clear()
        self._job_by_row_key = {}

        if not self.jobs:
            self.border_subtitle = "No jobs"
            return
        if not display_rows:
            self.border_subtitle = "No jobs match your filter"
            return
        self.border_subtitle = ""

        for row in display_rows:
            if isinstance(row, _JobRow):
                self._add_job_row(row.job, indent=row.indent)
            else:
                self._add_group_row(row)
                if row.key in self._expanded_groups:
                    for job in row.jobs:
                        self._add_job_row(job, indent=True)

    def _add_job_row(self, job: Job, *, indent: bool = False) -> None:
        color = status_color(job.status)
        key = str(job.id)
        name = f"  {job.name}" if indent else job.name
        self.add_row(
            Text(status_icon(job.status), style=color),
            job.stage,
            Text(str(job.status), style=color),
            _fmt_duration(job.duration),
            name,
            key=key,
        )
        self._job_by_row_key[key] = job

    def _add_group_row(self, group: _GroupRow) -> None:
        expanded = group.key in self._expanded_groups
        toggle = "▼" if expanded else "▶"
        worst = _worst_status(group.jobs)
        color = status_color(worst)
        self.add_row(
            Text(f"{toggle} {status_icon(worst)}", style=color),
            group.stage,
            _status_summary(group.jobs),
            _fmt_duration(_sum_duration(group.jobs)),
            f"{group.base_name}  ({len(group.jobs)} jobs)",
            key=group.key,
        )
