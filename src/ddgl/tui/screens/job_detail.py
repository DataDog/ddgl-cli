# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import (
    Footer,
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
from ddgl.core.logs import get_log
from ddgl.core.retry import retry_job
from ddgl.format import parse_trace
from ddgl.model.job import Job
from ddgl.model.trace import LogLine, Section, Trace
from ddgl.tui.gradient import gradient_text
from ddgl.tui.search import apply_search
from ddgl.tui.widgets.confirm import ConfirmModal
from ddgl.tui.widgets.job_dag import JobDAGPanel
from ddgl.tui.widgets.search_bar import FuzzySearchInput
from ddgl.tui.widgets.status import job_status_color, job_status_icon, job_status_label

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
    color = job_status_color(job)
    t = Text()
    t.append(f"{job.name}\n\n", style="bold")
    t.append("Stage\n", style="dim")
    t.append(f"{job.stage}\n\n")
    t.append("Status\n", style="dim")
    t.append(f"{job_status_icon(job)} {job_status_label(job)}\n\n", style=color)
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


_SECTION_COLOR = "dark_orange"
_SECTION_INDENT = "  "


def _set_collapsed(nodes: list[Section | LogLine], collapsed: bool) -> None:
    """Recursively set the *collapsed* flag on every Section in *nodes*."""
    for node in nodes:
        if isinstance(node, Section):
            node.collapsed = collapsed
            _set_collapsed(node.children, collapsed)


def _walk_trace(trace: Trace) -> list[Text]:
    """Convert a Trace IR to a flat list of Rich Text objects for RichLog display.

    Sections become styled header lines; collapsed sections skip their children.
    Log lines are rendered with ANSI color preserved and an optional dim timestamp.
    """
    out: list[Text] = []
    _collect_nodes(trace.children, out, depth=0)
    return out


def _collect_nodes(
    nodes: list[Section | LogLine],
    out: list[Text],
    depth: int,
) -> None:
    for node in nodes:
        if isinstance(node, Section):
            indent = _SECTION_INDENT * depth
            icon = "\u25b8" if node.collapsed else "\u25be"
            header = Text()
            header.append(indent)
            header.append(f"{icon} {node.name}", style=f"{_SECTION_COLOR} bold")
            if node.duration is not None:
                header.append(f"  {node.duration}s", style="dim")
            out.append(header)
            if not node.collapsed:
                _collect_nodes(node.children, out, depth + 1)
        else:
            txt = Text.from_ansi(node.text)
            if node.iso_timestamp:
                txt = Text.assemble(Text(f"{node.iso_timestamp} ", style="dim"), txt)
            if depth > 0:
                txt = Text.assemble(Text(_SECTION_INDENT * depth), txt)
            out.append(txt)


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


class JobDetailScreen(Screen[None]):
    """Full-screen job detail view with tabbed content area."""

    class JobRetried(Message):
        """Posted after a successful retry, so PipelineViewer can refresh."""

        def __init__(self, job: Job) -> None:
            super().__init__()
            self.job = job

    BINDINGS = [
        Binding("q", "app.pop_screen", "Close"),
        Binding("escape", "app.pop_screen", "Close", show=False),
        Binding("slash", "focus_search", "Search", key_display="/"),
        Binding("n", "next_match", "Next match", show=False),
        Binding("shift+n", "prev_match", "Prev match", show=False),
        Binding("pageup", "page_up_log", "Page up", show=False),
        Binding("pagedown", "page_down_log", "Page down", show=False),
        Binding("ctrl+up", "page_up_log", show=False),
        Binding("ctrl+down", "page_down_log", show=False),
        Binding("t", "toggle_sections", "Toggle sections"),
        Binding("r", "retry_job", "Retry job"),
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
        self._trace: Trace | None = None
        self._log_lines: list[Text] = []
        self._match_lines: list[int] = []
        self._current_match: int = -1
        self._search_regex: bool = False
        self._current_search: str = ""
        self._all_collapsed: bool = False

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
                        yield FuzzySearchInput(
                            id="log-search", placeholder="Search log…"
                        )
                    with TabPane("Deps", id="tab-deps"):
                        yield JobDAGPanel(
                            self._job,
                            self._all_jobs,
                            self._client,
                            self._cache,
                            id="job-dag",
                        )
                    with TabPane("History", id="tab-history"):
                        yield Static(
                            "[dim]Job history will be available in a future update.[/dim]",
                            id="history-placeholder",
                        )
                    with TabPane("Tests", id="tab-tests"):
                        yield Static(
                            "[dim]Test results will be available once the "
                            "Unified Test Format integration is ready.[/dim]",
                            id="tests-placeholder",
                        )
        yield Footer()

    def on_mount(self) -> None:
        self.title = gradient_text(f"Job #{self._job.id}")
        self.sub_title = self._job.name
        self.query_one("#job-meta", Static).update(_render_meta(self._job))
        self.query_one("#job-log").display = False
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
            raw = await get_log(self._client, self._job.id, cache=self._cache)
            self._trace = parse_trace(raw)
            self._render_log()
        except Exception as e:
            log_widget.write(Text(f"Failed to load log: {e}", style="red"))
        loading.display = False
        log_widget.display = True
        log_widget.focus()

    # -- Retry ---------------------------------------------------------

    def action_retry_job(self) -> None:
        if not self._job.is_retryable:
            self.notify("This job isn't failed or canceled.", severity="warning")
            return

        def _on_dismiss(confirmed: bool) -> None:
            if confirmed:
                self._retry_job()

        self.app.push_screen(
            ConfirmModal(
                "Retry job?", f"{self._job.stage}/{self._job.name} — {self._job.status}"
            ),
            _on_dismiss,
        )

    @work
    async def _retry_job(self) -> None:
        try:
            new_job = await retry_job(self._client, self._job.id)
        except Exception as e:
            self.notify(f"Retry failed: {e}", severity="error")
            return
        self._job = new_job
        self.title = gradient_text(f"Job #{new_job.id}")
        self.query_one("#job-meta", Static).update(_render_meta(new_job))
        self._reset_log_pane()
        self._fetch_log()
        self.notify(f"Retried {new_job.name} → job #{new_job.id}")
        self.post_message(self.JobRetried(new_job))

    def _reset_log_pane(self) -> None:
        """Clear the log tab back to its pre-fetch state, ready for _fetch_log."""
        log_widget = self.query_one("#job-log", RichLog)
        log_widget.clear()
        log_widget.display = False
        self.query_one("#log-loading", LoadingIndicator).display = True
        self.query_one("#log-search", FuzzySearchInput).clear()
        self._trace = None
        self._log_lines = []
        self._match_lines = []
        self._current_match = -1
        self._current_search = ""

    def _render_log(self) -> None:
        """Rebuild _log_lines from the current trace state and redraw the log widget."""
        if self._trace is None:
            return
        self._log_lines = _walk_trace(self._trace)
        self._apply_search(self._current_search)

    # -- Scroll ------------------------------------------------------------

    def action_page_up_log(self) -> None:
        self.query_one("#job-log", RichLog).scroll_page_up(animate=False)

    def action_page_down_log(self) -> None:
        self.query_one("#job-log", RichLog).scroll_page_down(animate=False)

    # -- Section toggle ----------------------------------------------------

    def action_toggle_sections(self) -> None:
        if self._trace is None:
            return
        self._all_collapsed = not self._all_collapsed
        _set_collapsed(self._trace.children, self._all_collapsed)
        self._render_log()

    # -- Search ------------------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#log-search", FuzzySearchInput).focus()

    def on_fuzzy_search_input_search_changed(
        self, event: FuzzySearchInput.SearchChanged
    ) -> None:
        event.stop()
        self._search_regex = event.regex
        self._current_search = event.query
        self._apply_search(event.query)

    def on_input_submitted(self, event: Input.Submitted) -> None:
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

        for i, line in enumerate(self._log_lines):
            highlighted, spans = apply_search(line, query, regex=self._search_regex)
            log_widget.write(highlighted)
            if spans:
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
        self._update_search_indicator(self._current_search)

    def action_prev_match(self) -> None:
        if not self._match_lines:
            return
        self._current_match = (self._current_match - 1) % len(self._match_lines)
        self._scroll_to_match()
        self._update_search_indicator(self._current_search)

    def _scroll_to_match(self) -> None:
        if self._current_match < 0 or not self._match_lines:
            return
        line_idx = self._match_lines[self._current_match]
        log_widget = self.query_one("#job-log", RichLog)
        log_widget.scroll_to(y=line_idx, animate=False)

    def _update_search_indicator(self, query: str) -> None:
        search = self.query_one("#log-search", FuzzySearchInput)
        if not query:
            search.set_search_indicator("")
        elif self._match_lines:
            search.set_search_indicator(
                f"{self._current_match + 1} of {len(self._match_lines)}"
            )
        else:
            search.set_search_indicator("No matches")
