# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, SelectionList

from ddgl.tui.widgets.job_list import SortMode


class FilterModalScreen(ModalScreen[set[str]]):
    """A modal that lets the user multi-select values from a list.

    Dismissed with the selected set (possibly empty).
    """

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", show=False),
        Binding("enter", "confirm", "Confirm", show=False),
    ]

    CSS = """
    FilterModalScreen {
        align: center bottom;
    }
    #modal-container {
        width: 40;
        height: auto;
        max-height: 20;
        margin-bottom: 5;
        background: $panel;
        border: round $accent;
        padding: 1 2;
    }
    SelectionList {
        height: auto;
        max-height: 15;
        border: none;
    }
    #confirm-btn {
        margin-top: 1;
        width: 100%;
    }
    """

    def __init__(self, title: str, options: list[str], selected: set[str]) -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._selected = selected

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield SelectionList(
                *[(opt, opt, opt in self._selected) for opt in sorted(self._options)],
                id="selection",
            )
            yield Button("Confirm", id="confirm-btn", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-btn":
            self._do_confirm()

    def action_confirm(self) -> None:
        self._do_confirm()

    def action_dismiss_cancel(self) -> None:
        self.dismiss(self._selected)

    def _do_confirm(self) -> None:
        sl: SelectionList = self.query_one("#selection", SelectionList)
        self.dismiss(set(sl.selected))


class FilterButton(Button):
    """A button that opens a FilterModalScreen to multi-select filter values.

    Posts a ``FiltersChanged`` message when the selection is confirmed.
    """

    class FiltersChanged(Message):
        def __init__(self, kind: str, selected: set[str]) -> None:
            super().__init__()
            self.kind = kind  # "status" or "stage"
            self.selected = selected

    def __init__(self, kind: str, label_base: str, **kwargs: object) -> None:
        super().__init__(label_base, **kwargs)  # type: ignore[arg-type]
        self._kind = kind
        self._label_base = label_base
        self._selected: set[str] = set()
        self._options: list[str] = []

    def update_options(self, options: list[str]) -> None:
        """Refresh the available options (called when jobs load)."""
        self._options = options
        self._selected &= set(options)
        self._refresh_label()

    def set_selected(self, selected: set[str]) -> None:
        """Programmatically sync selection (e.g. from text-box tokens)."""
        self._selected = selected & set(self._options)
        self._refresh_label()

    def _refresh_label(self) -> None:
        count = len(self._selected)
        self.label = (
            f"{self._label_base} ({count}) ▼" if count else f"{self._label_base} ▼"
        )

    async def _on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if not self._options:
            return

        def _on_dismiss(result: set[str] | None) -> None:
            if result is None:
                return
            self._selected = result
            self._refresh_label()
            self.post_message(self.FiltersChanged(self._kind, result))

        self.app.push_screen(
            FilterModalScreen(self._label_base, self._options, self._selected),
            _on_dismiss,
        )


class SortButton(Button):
    """Cycles through sort modes on click; mirrors the job list's sort_mode.

    Posts a ``SortChanged`` message so the app can sync the job list.
    """

    class SortChanged(Message):
        def __init__(self, mode: SortMode) -> None:
            super().__init__()
            self.mode = mode

    def __init__(self, **kwargs: object) -> None:
        super().__init__(SortMode.STAGE.label(), **kwargs)  # type: ignore[arg-type]
        self._mode = SortMode.STAGE

    def set_mode(self, mode: SortMode) -> None:
        """Update the button label to reflect an externally-driven sort change."""
        self._mode = mode
        self.label = mode.label()

    async def _on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        new_mode = self._mode.next()
        self.set_mode(new_mode)
        self.post_message(self.SortChanged(new_mode))
