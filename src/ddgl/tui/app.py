from __future__ import annotations

import datetime
import webbrowser

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Footer, Header, LoadingIndicator

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.core.jobs import list_jobs
from ddgl.core.pipeline import get_pipeline
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.tui.widgets.filter_buttons import FilterButton
from ddgl.tui.widgets.job_list import JobListPanel
from ddgl.tui.widgets.pipeline_info import PipelineInfoPanel
from ddgl.tui.widgets.search_bar import FilterSpec, FuzzySearchInput, parse_query

_REFRESH_INTERVAL = 20  # seconds between auto-refreshes for running pipelines


class PipelineViewer(App[None]):
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "blur_search", "Blur search", show=False),
        Binding("ctrl+k", "clear_search", "Clear search"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open_url", "Open URL"),
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
        self._dropdown_statuses: set[str] = set()
        self._dropdown_stages: set[str] = set()
        # Refresh state.
        self._refresh_timer: Timer | None = None
        self._last_updated: datetime.datetime | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield PipelineInfoPanel(id="pipeline-info")
            with Vertical(id="job-panel"):
                yield LoadingIndicator(id="loading")
                yield JobListPanel(id="job-table")
        with Horizontal(id="filter-bar"):
            yield FuzzySearchInput(id="search")
            yield FilterButton("status", "Status", id="status-filter")
            yield FilterButton("stage", "Stage", id="stage-filter")
        yield Footer()

    def on_mount(self) -> None:
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
        # Sync dropdown button labels to reflect tokens typed in the search box.
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
        spec = FilterSpec(
            text=self._text_filter.text,
            statuses=self._text_filter.statuses | self._dropdown_statuses,
            stages=self._text_filter.stages | self._dropdown_stages,
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
        statuses = sorted({str(j.status) for j in jobs})
        stages = sorted({j.stage for j in jobs})
        self.query_one("#status-filter", FilterButton).update_options(statuses)
        self.query_one("#stage-filter", FilterButton).update_options(stages)

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

    def action_focus_search(self) -> None:
        self.query_one(FuzzySearchInput).focus()

    def action_blur_search(self) -> None:
        self.query_one(JobListPanel).focus()

    def action_clear_search(self) -> None:
        search = self.query_one(FuzzySearchInput)
        search.clear()
        self.query_one(JobListPanel).focus()

    def action_cycle_sort(self) -> None:
        job_list = self.query_one(JobListPanel)
        job_list.sort_mode = job_list.sort_mode.next()

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
