from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import LoadingIndicator, RichLog, Static, TabbedContent, TabPane

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.core.jobs import get_job
from ddgl.core.logs import stream_log
from ddgl.model.job import Job
from ddgl.tui.gradient import gradient_text
from ddgl.tui.widgets.status import status_color, status_icon

# ---------------------------------------------------------------------------
# Pure helpers (testable without a running app)
# ---------------------------------------------------------------------------


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    return f"{total // 60}m {total % 60}s"


def _render_meta(job: Job) -> Text:
    """Build the Rich Text block for the left-hand metadata panel."""
    color = status_color(job.status)
    t = Text()
    t.append(f"{job.name}\n\n", style="bold")
    t.append("Stage\n", style="dim")
    t.append(f"{job.stage}\n\n")
    t.append("Status\n", style="dim")
    t.append(f"{status_icon(job.status)} {job.status}\n\n", style=color)
    t.append("Duration\n", style="dim")
    t.append(f"{_fmt_duration(job.duration)}\n")
    if job.runner_description:
        t.append("\nRunner\n", style="dim")
        t.append(f"{job.runner_description}\n")
        if job.runner_tags:
            t.append(f"[{', '.join(job.runner_tags)}]\n", style="dim")
    if job.queued_duration is not None:
        t.append("\nQueued\n", style="dim")
        t.append(f"{_fmt_duration(job.queued_duration)}\n")
    if job.failure_reason:
        t.append("\nReason\n", style="dim")
        t.append(f"{job.failure_reason}\n", style="red")
    if job.web_url:
        t.append("\nURL\n", style="dim")
        t.append(f"{job.web_url}\n", style="dim underline")
    return t


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


class JobDetailScreen(Screen[None]):
    """Full-screen job detail view with tabbed content area."""

    BINDINGS = [
        Binding("escape,q", "app.pop_screen", "Close"),
    ]

    def __init__(
        self,
        job: Job,
        client: GitLabClient,
        cache: Cache | None = None,
        *,
        all_jobs: list[Job] | None = None,
    ) -> None:
        super().__init__()
        self._job = job
        self._client = client
        self._cache = cache
        self._all_jobs = all_jobs or []

    def compose(self) -> ComposeResult:
        with Horizontal(id="job-detail-root"):
            with Vertical(id="job-meta-panel"):
                yield Static(id="job-meta")
            with Vertical(id="job-content-panel"):
                with TabbedContent(id="job-tabs"):
                    with TabPane("Log", id="tab-log"):
                        yield LoadingIndicator(id="log-loading")
                        yield RichLog(
                            id="job-log", markup=False, highlight=False
                        )

    def on_mount(self) -> None:
        self.title = gradient_text(f"Job #{self._job.id}")
        self.sub_title = self._job.name
        self.query_one("#job-meta", Static).update(_render_meta(self._job))
        self.query_one("#job-log").display = False
        self._enrich_job()
        self._fetch_log()

    @work
    async def _enrich_job(self) -> None:
        """Re-fetch the job from the single-job API to get runner/needs/queued info."""
        try:
            enriched = await get_job(self._client, self._job.id, cache=self._cache)
        except Exception:
            return  # best-effort; meta panel keeps the basic info
        self._job = enriched
        self.query_one("#job-meta", Static).update(_render_meta(enriched))

    @work
    async def _fetch_log(self) -> None:
        log_widget = self.query_one("#job-log", RichLog)
        loading = self.query_one("#log-loading", LoadingIndicator)
        try:
            async for line in stream_log(
                self._client, self._job.id, cache=self._cache
            ):
                log_widget.write(Text.from_ansi(line))
                if not log_widget.display:
                    loading.display = False
                    log_widget.display = True
        except Exception as e:
            loading.display = False
            log_widget.display = True
            log_widget.write(Text(f"Failed to load log: {e}", style="red"))
        # If the log was empty or never produced a line, hide the spinner.
        if not log_widget.display:
            loading.display = False
            log_widget.display = True
