from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Input,
    LoadingIndicator,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

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


def _highlight_text(original: Text, query: str) -> Text:
    """Return a copy of *original* with all case-insensitive *query* matches highlighted."""
    plain_lower = original.plain.lower()
    query_lower = query.lower()
    if query_lower not in plain_lower:
        return original
    result = original.copy()
    start = 0
    while True:
        idx = plain_lower.find(query_lower, start)
        if idx == -1:
            break
        result.stylize("reverse bold", idx, idx + len(query))
        start = idx + 1
    return result


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


class JobDetailScreen(Screen[None]):
    """Full-screen job detail view with tabbed content area."""

    BINDINGS = [
        Binding("q", "app.pop_screen", "Close"),
        Binding("escape", "dismiss_or_close", "Close", show=False),
        Binding("slash", "open_search", "Search", key_display="/"),
        Binding("n", "next_match", "Next match", show=False),
        Binding("shift+n", "prev_match", "Prev match", show=False),
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
        self._log_lines: list[Text] = []
        self._match_lines: list[int] = []
        self._current_match: int = -1

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
                        yield Input(
                            id="log-search",
                            placeholder="Search…",
                        )

    def on_mount(self) -> None:
        self.title = gradient_text(f"Job #{self._job.id}")
        self.sub_title = self._job.name
        self.query_one("#job-meta", Static).update(_render_meta(self._job))
        self.query_one("#job-log").display = False
        self.query_one("#log-search").display = False
        self._enrich_job()
        self._fetch_log()

    # -- Job enrichment & log streaming ------------------------------------

    @work
    async def _enrich_job(self) -> None:
        """Re-fetch the job from the single-job API to get runner/needs/queued info."""
        try:
            enriched = await get_job(self._client, self._job.id, cache=self._cache)
        except Exception:
            return
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
                text = Text.from_ansi(line)
                self._log_lines.append(text)
                log_widget.write(text)
                if not log_widget.display:
                    loading.display = False
                    log_widget.display = True
                    log_widget.focus()
        except Exception as e:
            loading.display = False
            log_widget.display = True
            log_widget.write(Text(f"Failed to load log: {e}", style="red"))
        if not log_widget.display:
            loading.display = False
            log_widget.display = True
        log_widget.focus()

    # -- Search ------------------------------------------------------------

    def action_open_search(self) -> None:
        search = self.query_one("#log-search", Input)
        search.display = True
        search.focus()

    def action_dismiss_or_close(self) -> None:
        search = self.query_one("#log-search", Input)
        if search.display:
            self._close_search()
        else:
            self.app.pop_screen()

    def _close_search(self) -> None:
        search = self.query_one("#log-search", Input)
        search.display = False
        search.value = ""
        self._apply_search("")
        self.query_one("#job-log", RichLog).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "log-search":
            self._apply_search(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "log-search":
            self.action_next_match()

    def _apply_search(self, query: str) -> None:
        log_widget = self.query_one("#job-log", RichLog)
        log_widget.clear()
        self._match_lines.clear()
        self._current_match = -1

        if not query:
            for line in self._log_lines:
                log_widget.write(line)
            self._update_search_indicator("")
            return

        query_lower = query.lower()
        for i, line in enumerate(self._log_lines):
            log_widget.write(_highlight_text(line, query))
            if query_lower in line.plain.lower():
                self._match_lines.append(i)

        if self._match_lines:
            self._current_match = 0
            self._scroll_to_match()
        self._update_search_indicator(query)

    def action_next_match(self) -> None:
        if not self._match_lines:
            return
        self._current_match = (self._current_match + 1) % len(self._match_lines)
        self._scroll_to_match()
        self._update_search_indicator(self.query_one("#log-search", Input).value)

    def action_prev_match(self) -> None:
        if not self._match_lines:
            return
        self._current_match = (self._current_match - 1) % len(self._match_lines)
        self._scroll_to_match()
        self._update_search_indicator(self.query_one("#log-search", Input).value)

    def _scroll_to_match(self) -> None:
        if self._current_match < 0 or not self._match_lines:
            return
        line_idx = self._match_lines[self._current_match]
        log_widget = self.query_one("#job-log", RichLog)
        log_widget.scroll_to(y=line_idx, animate=False)

    def _update_search_indicator(self, query: str) -> None:
        search = self.query_one("#log-search", Input)
        if not query:
            search.border_title = ""
        elif self._match_lines:
            search.border_title = (
                f"{self._current_match + 1} of {len(self._match_lines)}"
            )
        else:
            search.border_title = "No matches"
