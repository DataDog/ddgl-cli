from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

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

    def render(self) -> Text:
        if self._pipeline is None:
            return Text("No pipeline loaded.")
        return _render(self._pipeline)

    def watch_pipeline(self, value: Pipeline | None) -> None:
        # Store under a private name to avoid clashing with the reactive descriptor.
        self._pipeline = value
        self.refresh()

    def on_mount(self) -> None:
        self._pipeline: Pipeline | None = None


def _render(p: Pipeline) -> Text:
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
    if p.web_url:
        content.append(f"\nURL:\n{p.web_url}\n")
    return content
