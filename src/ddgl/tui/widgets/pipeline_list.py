# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable, Input, LoadingIndicator

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.model.pipeline import Pipeline
from ddgl.tui.gradient import gradient_text
from ddgl.tui.widgets.status import status_color, status_icon


def _fmt_datetime(ts: str) -> str:
    """Return 'D Mon HH:MM' from an ISO-8601 timestamp string, or '—'."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%-d %b %H:%M")
    except ValueError:
        return ts[:16] if len(ts) >= 16 else "—"


class PipelineListPanel(Widget):
    """Persistent left panel: browse and switch pipelines, optionally by ref."""

    class PipelineSelected(Message):
        def __init__(self, pipeline: Pipeline) -> None:
            super().__init__()
            self.pipeline = pipeline

    def __init__(
        self,
        client: GitLabClient,
        cache: Cache | None = None,
        initial_ref: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._client = client
        self._cache = cache
        self._initial_ref = initial_ref
        self._pipelines: list[Pipeline] = []

    def compose(self) -> ComposeResult:
        yield Input(
            value=self._initial_ref,
            placeholder="Ref — Enter to search",
            id="pipeline-ref-input",
        )
        yield LoadingIndicator(id="pipeline-list-loading")
        yield DataTable(
            id="pipeline-list-table",
            cursor_type="row",
            show_row_labels=False,
            cell_padding=1,
        )

    def on_mount(self) -> None:
        self.border_title = gradient_text("Pipelines")
        table = self.query_one("#pipeline-list-table", DataTable)
        table.add_column("Status", key="status", width=12)
        table.add_column("ID", key="id", width=7)
        table.add_column("Ref", key="ref")
        table.add_column("At", key="time", width=12)
        self._fetch(self._initial_ref or None)

    def focus_table(self) -> None:
        self.query_one("#pipeline-list-table", DataTable).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._fetch(event.value.strip() or None)

    @work(exclusive=True)
    async def _fetch(self, ref: str | None) -> None:
        loading = self.query_one("#pipeline-list-loading")
        table = self.query_one("#pipeline-list-table", DataTable)
        loading.display = True
        table.clear()
        try:
            page = await self._client.fetch_pipelines(ref=ref, per_page=20)
            self._pipelines = page.items
        except Exception as e:
            self.notify(f"Failed to load pipelines: {e}", severity="error")
            self._pipelines = []
        loading.display = False
        for p in self._pipelines:
            color = status_color(p.status)
            table.add_row(
                Text(f"{status_icon(p.status)} {p.status}", style=color),
                Text(str(p.id), style=color),
                Text(p.ref, style=color),
                Text(_fmt_datetime(p.created_at), style=color),
                key=str(p.id),
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        pipeline = next((p for p in self._pipelines if str(p.id) == key), None)
        if pipeline:
            self.post_message(PipelineListPanel.PipelineSelected(pipeline))
