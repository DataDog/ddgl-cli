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
from ddgl.tui.widgets.job_list import JobListPanel
from ddgl.tui.widgets.pipeline_info import PipelineInfoPanel
from ddgl.tui.widgets.search_bar import FuzzySearchInput


class PipelineViewer(App[None]):
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "clear_search", "Clear"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("enter", "job_detail", "Detail", show=False),
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

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield PipelineInfoPanel(id="pipeline-info")
            with Vertical(id="job-panel"):
                yield LoadingIndicator(id="loading")
                yield JobListPanel(id="job-table")
        yield FuzzySearchInput(id="search")
        yield Footer()

    def on_mount(self) -> None:
        self.load_pipeline(self._initial_pipeline)

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
        self.query_one(JobListPanel).search_query = message.query

    @work(exclusive=True)
    async def _load_jobs(self, pipeline: Pipeline) -> None:
        jobs: list[Job] = []
        async for job in list_jobs(self._client, pipeline.id, cache=self._cache):
            jobs.append(job)

        loading = self.query_one("#loading", LoadingIndicator)
        job_list = self.query_one(JobListPanel)
        loading.display = False
        job_list.display = True
        job_list.jobs = jobs

    def action_focus_search(self) -> None:
        self.query_one(FuzzySearchInput).focus()

    def action_clear_search(self) -> None:
        search = self.query_one(FuzzySearchInput)
        search.clear()
        self.query_one(JobListPanel).focus()

    def action_cycle_sort(self) -> None:
        job_list = self.query_one(JobListPanel)
        job_list.sort_mode = job_list.sort_mode.next()

    def action_job_detail(self) -> None:
        pass  # future
