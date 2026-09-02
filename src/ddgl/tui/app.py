# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import datetime
import webbrowser

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.theme import Theme
from textual.timer import Timer
from textual.widgets import Footer, Header, LoadingIndicator

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.core.jobs import list_jobs
from ddgl.core.pipeline import get_pipeline
from ddgl.core.retry import retry_job
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.tui.screens.job_detail import JobDetailScreen
from ddgl.tui.widgets.confirm import ConfirmModal
from ddgl.tui.widgets.filter_buttons import FilterButton, SortButton
from ddgl.tui.widgets.help import HelpModal
from ddgl.tui.widgets.job_list import JobListPanel, SortMode, status_token
from ddgl.tui.widgets.pipeline_info import PipelineInfoPanel
from ddgl.tui.widgets.pipeline_list import PipelineListPanel
from ddgl.tui.widgets.search_bar import FilterSpec, FuzzySearchInput, parse_query

_REFRESH_INTERVAL = 20  # seconds between auto-refreshes for running pipelines

_DD_THEME = Theme(
    name="datadog",
    primary="#774AA4",    # DataDog purple
    secondary="#5A3E8E",  # darker purple variant
    accent="#FC6D26",     # GitLab orange accent
    warning="#FAB800",
    error="#DD2B0E",
    success="#2DA160",
    dark=True,
)


class PipelineViewer(App[None]):
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "blur_search", "Blur search", show=False),
        Binding("ctrl+k", "clear_search", "Clear search"),
        Binding("r", "retry_job", "Retry job"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("o", "open_url", "Open URL"),
        Binding("p", "switch_pipeline", "Switch pipeline"),
        Binding("question_mark", "help", "Help", key_display="?"),
    ]

    pipeline: reactive[Pipeline | None] = reactive(None)

    def __init__(
        self,
        pipeline: Pipeline,
        client: GitLabClient,
        cache: Cache | None = None,
    ) -> None:
        super().__init__()
        self._initial_pipeline = pipeline
        self._client = client
        self._cache = cache
        # Filter state: text tokens from search box + explicit dropdown selections.
        self._text_filter = FilterSpec()
        self._dropdown_statuses: set[str] = {"running", "failed", "allowed-failure", "success"}
        self._dropdown_stages: set[str] = set()
        # Refresh state.
        self._refresh_timer: Timer | None = None
        self._last_updated: datetime.datetime | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="left-col"):
                yield PipelineInfoPanel(id="pipeline-info")
                yield PipelineListPanel(
                    self._client,
                    self._cache,
                    initial_ref=self._initial_pipeline.ref,
                    id="pipeline-list",
                )
            with Vertical(id="job-panel"):
                yield LoadingIndicator(id="loading")
                yield JobListPanel(id="job-table")
        with Horizontal(id="filter-bar"):
            yield FuzzySearchInput(id="search")
            yield FilterButton("status", "Status", id="status-filter")
            yield FilterButton("stage", "Stage", id="stage-filter")
            yield SortButton(id="sort-button")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(_DD_THEME)
        self.theme = "datadog"
        self.query_one("#sort-button", SortButton).set_mode(SortMode.START_TIME)
        self.load_pipeline(self._initial_pipeline)
        self.query_one(JobListPanel).focus()

    def watch_pipeline(self, value: Pipeline | None) -> None:
        if value is not None:
            info = self.query_one(PipelineInfoPanel)
            info.pipeline = value
            info.job_stats = []

    def load_pipeline(self, pipeline: Pipeline, *, keep_existing: bool = False) -> None:
        """Switch to a new pipeline: update info panel and reload jobs.

        When ``keep_existing=True`` the current job list stays visible while
        new jobs are fetched in the background (used for auto- and manual
        refresh so the user can keep browsing during the reload).
        """
        # Cancel any running auto-refresh timer before starting a new load.
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

        self.pipeline = pipeline

        job_list = self.query_one(JobListPanel)
        loading = self.query_one("#loading", LoadingIndicator)
        if keep_existing and job_list.jobs:
            # Background refresh: keep the table interactive, signal via subtitle.
            self.sub_title = "Refreshing…"
        else:
            job_list.display = False
            job_list.jobs = []
            loading.display = True

        self._load_jobs(pipeline, keep_existing=keep_existing)

    def on_fuzzy_search_input_search_changed(
        self, message: FuzzySearchInput.SearchChanged
    ) -> None:
        self._text_filter = parse_query(message.query)
        self._text_filter.regex = message.regex
        # Sync the dropdown buttons' own *display* state to reflect tokens
        # typed in the search box — but never `_dropdown_statuses`/
        # `_dropdown_stages` themselves. Those stay independent, driven
        # only by the dropdown modal (`on_filter_button_filters_changed`).
        # `_update_job_filter()` gives typed tokens precedence when
        # present, so this button-label sync is purely cosmetic; keeping
        # `_dropdown_*` untouched here means clearing a typed token falls
        # back to whatever the dropdown was actually set to, rather than
        # a value overwritten (and stuck) from the last thing you typed.
        self.query_one("#status-filter", FilterButton).set_selected(
            self._text_filter.statuses
        )
        self.query_one("#stage-filter", FilterButton).set_selected(
            self._text_filter.stages
        )
        self._update_job_filter()

    def on_filter_button_filters_changed(
        self, message: FilterButton.FiltersChanged
    ) -> None:
        if message.kind == "status":
            self._dropdown_statuses = message.selected
        else:
            self._dropdown_stages = message.selected
        self._update_job_filter()

    def _update_job_filter(self) -> None:
        # A typed status:/stage: token takes over as the authoritative
        # filter for that dimension; only fall back to the dropdown's
        # selection when nothing was typed.
        spec = FilterSpec(
            text=self._text_filter.text,
            statuses=self._text_filter.statuses or self._dropdown_statuses,
            stages=self._text_filter.stages or self._dropdown_stages,
            regex=self._text_filter.regex,
        )
        self.query_one(JobListPanel).filter_spec = spec

    @work(exclusive=True)
    async def _load_jobs(self, pipeline: Pipeline, keep_existing: bool = False) -> None:
        try:
            jobs: list[Job] = []
            async for job in list_jobs(self._client, pipeline.id, cache=self._cache):
                jobs.append(job)
        except Exception as e:
            if not keep_existing:
                self.query_one("#loading", LoadingIndicator).display = False
            self.notify(f"Failed to load jobs: {e}", severity="error")
            self._update_sub_title()
            return

        loading = self.query_one("#loading", LoadingIndicator)
        job_list = self.query_one(JobListPanel)
        if not keep_existing:
            loading.display = False
            job_list.display = True
        job_list.jobs = jobs

        # Populate filter button options from the loaded job list.
        statuses = sorted({status_token(j) for j in jobs})
        stages = sorted({j.stage for j in jobs})
        status_btn = self.query_one("#status-filter", FilterButton)
        status_btn.update_options(statuses)
        status_btn.set_selected(self._dropdown_statuses)
        self.query_one("#stage-filter", FilterButton).update_options(stages)
        self._update_job_filter()

        # Update pipeline info panel with job stats.
        self.query_one(PipelineInfoPanel).job_stats = jobs

        # Record the update time and schedule auto-refresh if pipeline is live.
        self._last_updated = datetime.datetime.now()
        self._update_sub_title()
        if pipeline.is_running:
            self._refresh_timer = self.set_interval(
                _REFRESH_INTERVAL, self._do_auto_refresh
            )

    def _update_sub_title(self) -> None:
        if self._last_updated is None:
            self.sub_title = ""
            return
        ts = self._last_updated.strftime("%H:%M:%S")
        if self.pipeline and self.pipeline.is_running:
            self.sub_title = f"Auto-refreshing · last updated {ts}"
        else:
            self.sub_title = f"Last updated {ts}"

    async def _do_auto_refresh(self) -> None:
        if self.pipeline is None:
            return
        try:
            fresh = await get_pipeline(
                self._client, self.pipeline.id, cache=self._cache
            )
        except Exception as e:
            self.notify(f"Auto-refresh failed: {e}", severity="warning")
            return
        self.load_pipeline(fresh, keep_existing=True)

    def action_refresh(self) -> None:
        if self.pipeline is not None:
            self._manual_refresh(self.pipeline)

    @work(exclusive=False)
    async def _manual_refresh(self, pipeline: Pipeline) -> None:
        try:
            fresh = await get_pipeline(self._client, pipeline.id, cache=self._cache)
        except Exception as e:
            self.notify(f"Refresh failed: {e}", severity="error")
            return
        self.load_pipeline(fresh, keep_existing=True)

    def action_retry_job(self) -> None:
        job = self.query_one(JobListPanel).get_selected_job()
        if job is None or not job.is_retryable:
            self.notify("Select a failed or canceled job to retry.", severity="warning")
            return

        def _on_dismiss(confirmed: bool) -> None:
            if confirmed:
                self._retry_job(job)

        self.push_screen(
            ConfirmModal("Retry job?", f"{job.stage}/{job.name} — {job.status}"),
            _on_dismiss,
        )

    @work(exclusive=False)
    async def _retry_job(self, job: Job) -> None:
        try:
            new_job = await retry_job(self._client, job.id)
        except Exception as e:
            self.notify(f"Retry failed: {e}", severity="error")
            return
        self.notify(f"Retried {job.name} → job #{new_job.id}")
        if self.pipeline is not None:
            self._manual_refresh(self.pipeline)

    def action_focus_search(self) -> None:
        self.query_one(FuzzySearchInput).focus()

    def action_blur_search(self) -> None:
        self.query_one(JobListPanel).focus()

    def action_clear_search(self) -> None:
        search = self.query_one(FuzzySearchInput)
        search.clear()
        self.query_one(JobListPanel).focus()

    def on_job_list_panel_sort_mode_changed(
        self, message: JobListPanel.SortModeChanged
    ) -> None:
        """Keep the sort button label in sync when s is pressed."""
        self.query_one("#sort-button", SortButton).set_mode(message.mode)

    def on_sort_button_sort_changed(self, message: SortButton.SortChanged) -> None:
        """Apply a sort mode selected by clicking the sort button."""
        self.query_one(JobListPanel).sort_mode = message.mode

    def action_switch_pipeline(self) -> None:
        self.query_one("#pipeline-list", PipelineListPanel).focus_table()

    def on_pipeline_list_panel_pipeline_selected(
        self, message: PipelineListPanel.PipelineSelected
    ) -> None:
        self.load_pipeline(message.pipeline)

    def on_job_list_panel_job_selected(
        self, message: JobListPanel.JobSelected
    ) -> None:
        all_jobs = self.query_one(JobListPanel).jobs
        self.push_screen(
            JobDetailScreen(
                message.job, self._client, self._cache, all_jobs=all_jobs
            )
        )

    def on_job_detail_screen_job_retried(
        self, message: JobDetailScreen.JobRetried
    ) -> None:
        """Refresh behind the still-open job detail screen after a retry there."""
        if self.pipeline is not None:
            self._manual_refresh(self.pipeline)

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def action_open_url(self) -> None:
        # Prefer the selected job's URL; fall back to the pipeline URL.
        job = self.query_one(JobListPanel).get_selected_job()
        if job and job.web_url:
            webbrowser.open(job.web_url)
            return
        if self.pipeline and self.pipeline.web_url:
            webbrowser.open(self.pipeline.web_url)
            return
        self.notify("No URL available.", severity="warning")
