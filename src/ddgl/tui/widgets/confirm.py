# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmModal(ModalScreen[bool]):
    """Yes/no confirmation. Enter/y confirms, Escape/n cancels.

    Dismissed with True on confirm, False on cancel.
    """

    # "enter" needs priority=True: without it, a focused Button consumes
    # the key to press itself before this binding ever sees it.
    BINDINGS = [
        Binding("escape,n", "dismiss_cancel", "Cancel", show=False),
        Binding("enter,y", "confirm", "Confirm", show=False, priority=True),
    ]

    CSS = """
    ConfirmModal {
        align: center middle;
    }
    #modal-container {
        width: auto;
        max-width: 60;
        height: auto;
        background: $panel;
        border: round $accent;
        padding: 1 2;
    }
    #confirm-body {
        margin-bottom: 1;
    }
    #button-row {
        height: auto;
        align: right middle;
    }
    #cancel-btn {
        margin-right: 1;
    }
    """

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Static(self._body, id="confirm-body")
            with Horizontal(id="button-row"):
                yield Button("Cancel", id="cancel-btn")
                yield Button("Confirm", id="confirm-btn", variant="primary")

    def on_mount(self) -> None:
        self.border_title = self._title

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-btn")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_dismiss_cancel(self) -> None:
        self.dismiss(False)
