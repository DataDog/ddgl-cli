# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import DataTable

from ddgl.model.job import Job
from ddgl.tui.gradient import gradient_text
from ddgl.tui.widgets.search_bar import FilterSpec, job_text_matches
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


def _fmt_started_at(started_at: str | None) -> str:
    """Return HH:MM from an ISO-8601 started_at string, or '—'."""
    if not started_at:
        return "—"
    t_idx = started_at.find("T")
    if t_idx == -1 or len(started_at) < t_idx + 6:
        return "—"
    return started_at[t_idx + 1 : t_idx + 6]


def _truncate(s: str, max_len: int) -> str:
    """Truncate *s* to *max_len* characters, appending '…' if truncated."""
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


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


def _group_min_started_at(jobs: list[Job]) -> str | None:
    """Return the earliest started_at among *jobs*, or None if none started."""
    started = [j.started_at for j in jobs if j.started_at]
    return min(started) if started else None


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
        result = [j for j in result if job_text_matches(j.name, spec)]
    return result


def _filter_is_active(spec: FilterSpec) -> bool:
    """Return True when a text search is active and grouping should be suppressed.

    Status/stage dropdown filters still apply but don't flatten the view —
    matrix groups are shown within the filtered result set.
    """
    return bool(spec.text)


# ---------------------------------------------------------------------------
# Matrix grouping
# ---------------------------------------------------------------------------


def _group_jobs(jobs: list[Job]) -> list[_DisplayRow]:
    """Group matrix jobs into GroupRows; singletons become plain JobRows.

    The position of each group in the output is determined by its *first*
    member in the input, so the caller's sort order is preserved.  Members
    belonging to the same (stage, base_name) pair are collected together
    regardless of where they sit in the input list.
    """
    seen_order: list[tuple[str, str]] = []
    groups: dict[tuple[str, str], list[Job]] = {}
    for job in jobs:
        gkey = (job.stage, matrix_base_name(job.name))
        if gkey not in groups:
            seen_order.append(gkey)
            groups[gkey] = []
        groups[gkey].append(job)

    rows: list[_DisplayRow] = []
    for stage, base in seen_order:
        member_list = groups[(stage, base)]
        if len(member_list) == 1:
            rows.append(_JobRow(member_list[0]))
        else:
            row_key = f"group:{base}:{stage}"
            rows.append(
                _GroupRow(key=row_key, base_name=base, stage=stage, jobs=member_list)
            )
    return rows


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class JobListPanel(DataTable):
    """Right panel: job list as a DataTable with structured filtering and sort modes."""

    class JobSelected(Message):
        """Posted when the user presses Enter on a plain job row."""

        def __init__(self, job: Job) -> None:
            super().__init__()
            self.job = job

    class SortModeChanged(Message):
        """Posted when the sort mode changes (e.g. via the `s` key)."""

        def __init__(self, mode: SortMode) -> None:
            super().__init__()
            self.mode = mode

    BINDINGS = [
        Binding("s", "cycle_sort_mode", "Sort"),
        Binding("space", "toggle_group", "Expand/Collapse", show=False),
    ]

    jobs: reactive[list[Job]] = reactive([], always_update=True)
    filter_spec: reactive[FilterSpec] = reactive(FilterSpec, always_update=True)
    sort_mode: reactive[SortMode] = reactive(SortMode.START_TIME)

    _filter_timer: Timer | None = None
    _job_by_row_key: dict[str, Job]
    _expanded_groups: set[str]
    _sep_keys: set[str]  # row keys for separator rows (non-interactive)

    def __init__(self, **kwargs: object) -> None:
        super().__init__(  # type: ignore[arg-type]
            cell_padding=2,
            cursor_type="row",
            show_row_labels=False,
            **kwargs,
        )
        self._job_by_row_key = {}
        self._expanded_groups = set()
        self._sep_keys = set()

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
        self.add_column("Status", key="status", width=14)
        self.add_column("Stage", key="stage", width=20)
        self.add_column("Started", key="started", width=7)
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

    def action_cycle_sort_mode(self) -> None:
        new_mode = self.sort_mode.next()
        self.sort_mode = new_mode
        self.post_message(JobListPanel.SortModeChanged(new_mode))

    def action_toggle_group(self) -> None:
        """Toggle expand/collapse of the group row under the cursor."""
        if not self.rows:
            return
        try:
            cell_key = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return
        key = str(cell_key.row_key.value)
        if not key.startswith("group:"):
            return
        if key in self._expanded_groups:
            self._expanded_groups.discard(key)
        else:
            self._expanded_groups.add(key)
        self._recompute()

    def action_cursor_down(self) -> None:
        """Move down, skipping separator rows."""
        ordered = self.ordered_rows
        new_row = self.cursor_row + 1
        while new_row < len(ordered):
            if str(ordered[new_row].key.value) not in self._sep_keys:
                self.move_cursor(row=new_row)
                return
            new_row += 1

    def action_cursor_up(self) -> None:
        """Move up, skipping separator rows."""
        ordered = self.ordered_rows
        new_row = self.cursor_row - 1
        while new_row >= 0:
            if str(ordered[new_row].key.value) not in self._sep_keys:
                self.move_cursor(row=new_row)
                return
            new_row -= 1

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """If a mouse click lands the cursor on a separator, jump past it."""
        if str(event.row_key.value) not in self._sep_keys:
            return
        ordered = self.ordered_rows
        for new_row in range(self.cursor_row + 1, len(ordered)):
            if str(ordered[new_row].key.value) not in self._sep_keys:
                self.move_cursor(row=new_row)
                return
        for new_row in range(self.cursor_row - 1, -1, -1):
            if str(ordered[new_row].key.value) not in self._sep_keys:
                self.move_cursor(row=new_row)
                return

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Toggle group rows on Enter; post JobSelected for plain job rows."""
        key = str(event.row_key.value)
        if key in self._sep_keys:
            event.stop()
            return
        if key.startswith("group:"):
            event.stop()
            if key in self._expanded_groups:
                self._expanded_groups.discard(key)
            else:
                self._expanded_groups.add(key)
            self._recompute()
            return
        # Plain job row — post to the app.
        job = self._job_by_row_key.get(key)
        if job:
            self.post_message(JobListPanel.JobSelected(job))

    def _update_border_title(
        self, visible: int | None = None, total: int | None = None
    ) -> None:
        if total is None:
            text = "Jobs"
        elif visible == total:
            text = f"Jobs  ·  {total} jobs"
        else:
            text = f"Jobs  ·  {visible} / {total}"
        title = gradient_text(text)
        self.border_title = title
        try:
            self.app.query_one("#job-panel").border_title = title
        except Exception:
            pass

    def _recompute(self) -> None:
        # Save cursor so we can restore it after the table is rebuilt.
        saved_key: str | None = None
        if self.rows:
            try:
                ck = self.coordinate_to_cell_key(self.cursor_coordinate)
                k = str(ck.row_key.value)
                if k not in self._sep_keys:
                    saved_key = k
            except Exception:
                pass

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

        if saved_key is not None:
            self._restore_cursor(saved_key)

    def _repopulate(self, display_rows: list[_DisplayRow]) -> None:
        self.clear()
        self._job_by_row_key = {}
        self._sep_keys = set()

        if not self.jobs:
            self.border_subtitle = "No jobs"
            return
        if not display_rows:
            self.border_subtitle = "No jobs match your filter"
            return
        self.border_subtitle = ""

        for row in display_rows:
            if isinstance(row, _JobRow):
                self._add_job_row(row.job)
            else:
                self._add_group_row(row)
                if row.key in self._expanded_groups:
                    children = row.jobs
                    for j, job in enumerate(children):
                        self._add_job_row(
                            job, indent=True, is_last=(j == len(children) - 1)
                        )

    def _restore_cursor(self, key: str) -> None:
        """Move the cursor back to the row with *key* after a repopulate."""
        for i, row in enumerate(self.ordered_rows):
            if str(row.key.value) == key:
                self.move_cursor(row=i)
                return

    def _add_job_row(
        self, job: Job, *, indent: bool = False, is_last: bool = False
    ) -> None:
        color = status_color(job.status)
        key = str(job.id)

        if indent:
            # Status cell gets the tree connector; all cells indented
            connector = "└─ " if is_last else "├─ "
            status_cell = Text()
            status_cell.append(connector, style=f"dim {color}")
            status_cell.append(f"{status_icon(job.status)} {job.status}", style=color)
            stage_cell = Text(f"  {_truncate(job.stage, 18)}", style=color)
            started_cell = Text(f"  {_fmt_started_at(job.started_at)}", style=color)
            duration_cell = Text(f"  {_fmt_duration(job.duration)}", style=color)
            name_cell = Text(job.name, style=color)
        else:
            status_cell = Text(f"{status_icon(job.status)} {job.status}", style=color)
            stage_cell = Text(_truncate(job.stage, 20), style=color)
            started_cell = Text(_fmt_started_at(job.started_at), style=color)
            duration_cell = Text(_fmt_duration(job.duration), style=color)
            name_cell = Text(job.name, style=color)

        self.add_row(
            status_cell, stage_cell, started_cell, duration_cell, name_cell, key=key
        )
        self._job_by_row_key[key] = job

    def _add_group_row(self, group: _GroupRow) -> None:
        expanded = group.key in self._expanded_groups
        toggle = "▼" if expanded else "▶"
        worst = _worst_status(group.jobs)
        color = status_color(worst)
        bold = f"bold {color}"

        # Merged status: toggle + worst icon + per-status counts (all bold)
        status_cell = Text()
        status_cell.append(f"{toggle} ", style="dim")
        status_cell.append(f"{status_icon(worst)} ", style=bold)
        status_cell.append_text(_status_summary(group.jobs))

        stage_cell = Text(_truncate(group.stage, 20), style=bold)
        started_cell = Text(
            _fmt_started_at(_group_min_started_at(group.jobs)), style=bold
        )
        duration_cell = Text(_fmt_duration(_sum_duration(group.jobs)), style=bold)

        name_cell = Text()
        name_cell.append(group.base_name, style=bold)
        name_cell.append(f"  ({len(group.jobs)} jobs)", style=bold)

        self.add_row(
            status_cell,
            stage_cell,
            started_cell,
            duration_cell,
            name_cell,
            key=group.key,
        )
