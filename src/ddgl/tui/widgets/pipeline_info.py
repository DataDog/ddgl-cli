from __future__ import annotations

from collections import Counter

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline
from ddgl.tui.widgets.status import status_color, status_icon


def _fmt_elapsed(pipeline: Pipeline) -> str:
    elapsed = pipeline.elapsed
    if elapsed is None:
        return "—"
    total = int(elapsed.total_seconds())
    return f"{total // 60}m {total % 60}s"


class PipelineInfoPanel(Static):
    """Left panel: pipeline metadata."""

    pipeline: reactive[Pipeline | None] = reactive(None)
    job_stats: reactive[list[Job]] = reactive([], always_update=True)

    def render(self) -> Text:
        if self._pipeline is None:
            return Text("No pipeline loaded.")
        return _render(self._pipeline, self._job_stats)

    def watch_pipeline(self, value: Pipeline | None) -> None:
        # render() reads _pipeline directly to avoid the reactive descriptor proxy.
        self._pipeline = value
        self.refresh()

    def watch_job_stats(self, value: list[Job]) -> None:
        self._job_stats = value
        self.refresh()


def _render(p: Pipeline, jobs: list[Job]) -> Text:
    color = status_color(p.status)
    icon = status_icon(p.status)
    duration_str = _fmt_elapsed(p)

    content = Text()
    content.append(f"Pipeline #{p.id}\n", style="bold")
    content.append("\n")
    content.append("Status:   ")
    content.append(f"{icon} {p.status}\n", style=color)
    content.append(f"Ref:      {p.ref}\n")
    content.append(f"SHA:      {p.sha[:12] if p.sha else '—'}\n")
    content.append(f"Source:   {p.source or '—'}\n")
    content.append(f"Duration: {duration_str}\n")
    if jobs:
        content.append("\n")
        content.append(_render_job_stats(jobs))
    if p.web_url:
        content.append(f"\nURL:\n{p.web_url}\n")
    return content


def _render_job_stats(jobs: list[Job]) -> Text:
    counts = Counter(str(j.status) for j in jobs)
    line = Text()
    for status, sep in [
        ("success", "   "),
        ("failed", "   "),
        ("running", "   "),
        ("skipped", ""),
    ]:
        n = counts.get(status, 0)
        if n:
            line.append(
                f"{status_icon(status)} {n}{sep}", style=status_color(status)
            )
    # Any remaining statuses not in the short list
    shown = {"success", "failed", "running", "skipped"}
    for status, n in counts.items():
        if status not in shown and n:
            line.append(
                f"  {status_icon(status)} {n}", style=status_color(status)
            )
    line.append("\n")
    return line
