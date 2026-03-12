from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, LoadingIndicator

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.core.jobs import list_jobs
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.tui.widgets.filter_buttons import FilterButton
from ddgl.tui.widgets.job_list import JobListPanel
from ddgl.tui.widgets.pipeline_info import PipelineInfoPanel
from ddgl.tui.widgets.search_bar import FilterSpec, FuzzySearchInput, parse_query


class PipelineViewer(App[None]):
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "blur_search", "Blur search", show=False),
        Binding("ctrl+k", "clear_search", "Clear search"),
        Binding("s", "cycle_sort", "Sort"),
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
            self.query_one(PipelineInfoPanel).pipeline = value

    def load_pipeline(self, pipeline: Pipeline) -> None:
        """Switch to a new pipeline: update info panel and reload jobs."""
        self.pipeline = pipeline

        job_list = self.query_one(JobListPanel)
        loading = self.query_one("#loading", LoadingIndicator)
        job_list.display = False
        job_list.jobs = []
        loading.display = True

        self._load_jobs(pipeline)

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
    async def _load_jobs(self, pipeline: Pipeline) -> None:
        try:
            jobs: list[Job] = []
            async for job in list_jobs(self._client, pipeline.id, cache=self._cache):
                jobs.append(job)
        except Exception as e:
            self.query_one("#loading", LoadingIndicator).display = False
            self.notify(f"Failed to load jobs: {e}", severity="error")
            return

        loading = self.query_one("#loading", LoadingIndicator)
        job_list = self.query_one(JobListPanel)
        loading.display = False
        job_list.display = True
        job_list.jobs = jobs

        # Populate filter button options from the loaded job list.
        statuses = sorted({str(j.status) for j in jobs})
        stages = sorted({j.stage for j in jobs})
        self.query_one("#status-filter", FilterButton).update_options(statuses)
        self.query_one("#stage-filter", FilterButton).update_options(stages)

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
