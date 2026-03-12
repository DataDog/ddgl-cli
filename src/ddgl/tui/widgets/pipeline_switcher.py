from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, LoadingIndicator

from ddgl.cache.cache import Cache
from ddgl.client import GitLabClient
from ddgl.model.pipeline import Pipeline
from ddgl.tui.gradient import gradient_text
from ddgl.tui.widgets.status import status_color, status_icon


def _fmt_ts(ts: str) -> str:
    """Return HH:MM from an ISO-8601 timestamp string, or '—'."""
    if not ts:
        return "—"
    t = ts.find("T")
    if t == -1 or len(ts) < t + 6:
        return "—"
    return ts[t + 1 : t + 6]


class PipelineSwitcherModal(ModalScreen[Pipeline | None]):
    """Modal to browse and switch pipelines, optionally changing ref."""

    BINDINGS = [Binding("escape", "dismiss_cancel", "Cancel")]

    def __init__(
        self,
        client: GitLabClient,
        cache: Cache | None = None,
        current_ref: str = "",
    ) -> None:
        super().__init__()
        self._client = client
        self._cache = cache
        self._current_ref = current_ref
        self._pipelines: list[Pipeline] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="switcher"):
            yield Input(
                value=self._current_ref,
                placeholder="Ref (branch / tag / SHA) — Enter to search",
                id="ref-input",
            )
            yield LoadingIndicator(id="switcher-loading")
            yield DataTable(
                id="pipeline-table",
                cursor_type="row",
                show_row_labels=False,
                cell_padding=1,
            )

    def on_mount(self) -> None:
        self.border_title = gradient_text("Switch Pipeline")
        table = self.query_one("#pipeline-table", DataTable)
        table.add_column("Status", key="status", width=14)
        table.add_column("ID", key="id", width=8)
        table.add_column("Ref", key="ref", width=28)
        table.add_column("SHA", key="sha", width=10)
        table.add_column("Created", key="created", width=7)
        self._fetch(self._current_ref or None)
        self.query_one("#pipeline-table", DataTable).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._fetch(event.value.strip() or None)

    @work(exclusive=True)
    async def _fetch(self, ref: str | None) -> None:
        loading = self.query_one("#switcher-loading")
        table = self.query_one("#pipeline-table", DataTable)
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
                Text(p.sha[:8] if p.sha else "—", style=color),
                Text(_fmt_ts(p.created_at), style=color),
                key=str(p.id),
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        pipeline = next((p for p in self._pipelines if str(p.id) == key), None)
        if pipeline:
            self.dismiss(pipeline)

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)
