# F4 — Job Detail / Log View

## Goal

Pressing `Enter` on a non-group job row opens a modal overlay showing the job's
metadata (status, stage, duration, failure reason, URL) and its scrollable raw log.
`Escape` / `q` dismiss the modal.

---

## Prerequisites (already in place)

- `Job.failure_reason: str | None` — populated from API
- `Job.web_url: str` — populated from API
- `GitLabClient.get_job_log(job_id, project_id=None) -> str` — returns raw log text;
  `project_id` defaults to the client's configured project, so no extra argument needed
- `DataTable.on_data_table_row_selected` already exists in `JobListPanel` and handles
  separator rows and group-toggle; this plan extends it to also post `JobSelected`

---

## High-level design

### New file: `src/ddgl/tui/widgets/job_detail.py`

`JobDetailModal(ModalScreen[None])` — the full-screen modal.

```python
from textual.binding import Binding
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import LoadingIndicator, RichLog, Static
from textual.containers import Vertical
from textual import work
from rich.text import Text

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.model.job import Job
from ddgl.tui.gradient import gradient_text
from ddgl.tui.widgets.status import status_color, status_icon


class JobDetailModal(ModalScreen[None]):
    BINDINGS = [Binding("escape,q", "dismiss", "Close")]

    def __init__(self, job: Job, client: GitLabClient, cache: Cache | None = None):
        super().__init__()
        self._job = job
        self._client = client
        self._cache = cache

    def compose(self) -> ComposeResult:
        with Vertical(id="job-detail"):
            yield Static(id="job-meta")
            yield LoadingIndicator(id="log-loading")
            yield RichLog(id="job-log", markup=False, highlight=False)

    def on_mount(self) -> None:
        self.border_title = gradient_text(f"Job #{self._job.id}")
        self.query_one("#job-meta", Static).update(_render_meta(self._job))
        self.query_one("#job-log").display = False
        self._fetch_log()

    @work
    async def _fetch_log(self) -> None:
        try:
            raw = await self._client.get_job_log(self._job.id)
        except Exception as e:
            self.query_one("#log-loading").display = False
            log = self.query_one(RichLog)
            log.display = True
            log.write(f"[red]Failed to load log: {e}[/red]")
            return
        self.query_one("#log-loading").display = False
        log = self.query_one(RichLog)
        log.display = True
        for line in raw.splitlines():
            log.write(Text.from_ansi(line))   # preserves ANSI colour codes
```

**Pure helper (testable):**

```python
def _render_meta(job: Job) -> Text:
    """Rich Text block: name, stage, status, duration, failure_reason, URL."""
    color = status_color(job.status)
    content = Text()
    content.append(f"{job.name}\n", style="bold")
    content.append(f"Stage:    {job.stage}\n")
    content.append("Status:   ")
    content.append(f"{status_icon(job.status)} {job.status}\n", style=color)
    content.append(f"Duration: {_fmt_duration(job.duration)}\n")
    if job.failure_reason:
        content.append("Reason:   ")
        content.append(f"{job.failure_reason}\n", style="red")
    if job.web_url:
        content.append(f"\nURL: {job.web_url}\n", style="dim")
    return content
```

`_fmt_duration` can be imported from `job_list.py` (already public-enough) or
duplicated as a one-liner.

---

### Changes to `src/ddgl/tui/widgets/job_list.py`

Add `JobSelected` message:

```python
class JobSelected(Message):
    def __init__(self, job: Job) -> None:
        super().__init__()
        self.job = job
```

Update the **existing** `on_data_table_row_selected` (currently handles separators
and group-toggle) to also post `JobSelected` for plain job rows:

```python
def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
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
    # Plain job row — post to the app
    job = self._job_by_row_key.get(key)
    if job:
        self.post_message(JobListPanel.JobSelected(job))
```

---

### Changes to `src/ddgl/tui/app.py`

No new `BINDINGS` entry needed — `DataTable` fires `RowSelected` on `Enter` itself.

Import and handler:

```python
from ddgl.tui.widgets.job_detail import JobDetailModal

def on_job_list_panel_job_selected(
    self, message: JobListPanel.JobSelected
) -> None:
    self.push_screen(JobDetailModal(message.job, self._client, self._cache))
```

---

### CSS: `src/ddgl/tui/app.tcss`

```css
JobDetailModal {
    align: center middle;
}

#job-detail {
    width: 90%;
    height: 90%;
    background: $surface;
    border: round $primary;
    padding: 1 2;
}

#job-meta {
    height: auto;
    border-bottom: solid $panel;
    padding-bottom: 1;
    margin-bottom: 1;
}

#job-log {
    height: 1fr;
}
```

---

### Tests: `tests/tui/test_widgets/test_job_detail.py`

Pure-function tests for `_render_meta` (no Textual app needed):

- Contains job name in output
- Contains stage in output
- Contains status string and icon
- Shows duration via `_fmt_duration`
- Shows `failure_reason` when present; omits it when `None`
- Shows URL when present; omits it when empty
- Returns `Text` instance

ANSI stripping note: `Text.from_ansi` is a Rich function — no need to test it
directly; test that `_render_meta` returns correct plain text with known inputs.

---

## Implementation order

1. `job_detail.py` — `_render_meta` pure helper + `JobDetailModal` widget
2. Tests for `_render_meta` in `test_job_detail.py`
3. `job_list.py` — add `JobSelected` message, extend `on_data_table_row_selected`
4. `app.py` — import `JobDetailModal`, add `on_job_list_panel_job_selected`
5. `app.tcss` — modal styles

One commit: `feat(tui): job detail modal with log viewer (F4)`
