"""Job dependency DAG widget for the job detail screen."""
from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import work
from textual.widgets import LoadingIndicator, Static, Tree

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.core.jobs import get_jobs
from ddgl.model.job import Job
from ddgl.tui.widgets.status import status_color, status_icon

from .job_list import _fmt_duration

# ---------------------------------------------------------------------------
# Pure helpers (testable)
# ---------------------------------------------------------------------------


@dataclass
class DAGResult:
    """The neighbourhood of a single job in the pipeline DAG."""

    upstream: list[Job]  # jobs this one depends on (its `needs`)
    downstream: list[Job]  # jobs that depend on this one


def build_dag(current: Job, all_jobs: list[Job]) -> DAGResult:
    """Extract the immediate upstream and downstream neighbours of *current*.

    *all_jobs* must have their ``needs`` fields populated (from the single-job API).
    """
    by_name: dict[str, Job] = {j.name: j for j in all_jobs}

    upstream = [by_name[n] for n in current.needs if n in by_name]

    downstream = [
        j for j in all_jobs if j.name != current.name and current.name in j.needs
    ]

    return DAGResult(upstream=upstream, downstream=downstream)


def _job_label(job: Job) -> Text:
    """Coloured label for a tree node: icon + name + (status, duration)."""
    color = status_color(job.status)
    t = Text()
    t.append(f"{status_icon(job.status)} ", style=color)
    t.append(job.name, style=f"bold {color}")
    t.append(f"  ({job.status}, {_fmt_duration(job.duration)})", style="dim")
    return t


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class JobDAGPanel(Static):
    """Container that fetches enriched jobs and displays a dependency tree."""

    def __init__(
        self,
        job: Job,
        all_jobs: list[Job],
        client: GitLabClient,
        cache: Cache | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._job = job
        self._all_jobs = all_jobs
        self._client = client
        self._cache = cache

    def compose(self):  # noqa: ANN201
        yield LoadingIndicator(id="dag-loading")

    def on_mount(self) -> None:
        self._load_dag()

    @work
    async def _load_dag(self) -> None:
        loading = self.query_one("#dag-loading", LoadingIndicator)
        try:
            enriched = await get_jobs(
                self._client,
                [j.id for j in self._all_jobs],
                cache=self._cache,
            )
        except Exception as e:
            loading.display = False
            self.mount(Static(f"Failed to load dependencies: {e}"))
            return

        dag = build_dag(
            next((j for j in enriched if j.id == self._job.id), self._job),
            enriched,
        )

        loading.display = False

        if not dag.upstream and not dag.downstream:
            self.mount(Static("No dependencies found for this job.", id="dag-empty"))
            return

        tree: Tree[str] = Tree(_job_label(self._job), id="dag-tree")
        tree.root.expand()

        if dag.upstream:
            up_node = tree.root.add("⬆ Depends on", expand=True)
            for job in dag.upstream:
                up_node.add_leaf(_job_label(job))

        if dag.downstream:
            down_node = tree.root.add("⬇ Required by", expand=True)
            for job in dag.downstream:
                down_node.add_leaf(_job_label(job))

        self.mount(tree)
