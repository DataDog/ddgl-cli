from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, LoadingIndicator, Static

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.core.jobs import list_jobs
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.tui.widgets.status import status_color, status_icon


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    return f"{total // 60}m {total % 60}s"


class PipelineViewer(App[None]):
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "clear_search", "Clear"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("enter", "job_detail", "Detail", show=False),
    ]

    def __init__(
        self,
        pipeline: Pipeline,
        client: GitLabClient,
        cache: Cache | None = None,
    ) -> None:
        super().__init__()
        self._pipeline = pipeline
        self._client = client
        self._cache = cache
        self._all_jobs: list[Job] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield Static(id="pipeline-info")
            with Vertical(id="job-panel"):
                yield LoadingIndicator(id="loading")
                yield DataTable(id="job-table")
        yield Footer()

    def on_mount(self) -> None:
        self._render_pipeline_info()

        table = self.query_one("#job-table", DataTable)
        table.add_column("", key="icon", width=3)
        table.add_column("Name", key="name")
        table.add_column("Stage", key="stage")
        table.add_column("Status", key="status")
        table.add_column("Duration", key="duration", width=10)
        table.display = False

        self.load_jobs()

    def _render_pipeline_info(self) -> None:
        p = self._pipeline
        elapsed = p.elapsed
        if elapsed:
            total = int(elapsed.total_seconds())
            duration_str = f"{total // 60}m {total % 60}s"
        else:
            duration_str = "—"

        color = status_color(p.status)
        icon = status_icon(p.status)

        content = Text()
        content.append(f"Pipeline #{p.id}\n", style="bold")
        content.append("\n")
        content.append("Status:   ")
        content.append(f"{icon} {p.status}\n", style=color)
        content.append(f"Ref:      {p.ref}\n")
        content.append(f"SHA:      {p.sha[:12] if p.sha else '—'}\n")
        content.append(f"Source:   {p.source or '—'}\n")
        content.append(f"Duration: {duration_str}\n")
        if p.web_url:
            content.append(f"\nURL:\n{p.web_url}\n")

        self.query_one("#pipeline-info", Static).update(content)

    @work(exclusive=True)
    async def load_jobs(self) -> None:
        jobs: list[Job] = []
        async for job in list_jobs(self._client, self._pipeline.id, cache=self._cache):
            jobs.append(job)

        self._all_jobs = sorted(jobs, key=lambda j: (j.stage, j.name))

        loading = self.query_one("#loading", LoadingIndicator)
        table = self.query_one("#job-table", DataTable)
        loading.display = False
        table.display = True
        self._populate_table()

    def _populate_table(self) -> None:
        table = self.query_one("#job-table", DataTable)
        table.clear()
        for job in self._all_jobs:
            color = status_color(job.status)
            table.add_row(
                Text(status_icon(job.status), style=color),
                job.name,
                job.stage,
                Text(str(job.status), style=color),
                _fmt_duration(job.duration),
            )

    def action_focus_search(self) -> None:
        pass  # wired in commit 4

    def action_clear_search(self) -> None:
        pass  # wired in commit 4

    def action_cycle_sort(self) -> None:
        pass  # wired in commit 5

    def action_job_detail(self) -> None:
        pass  # future
