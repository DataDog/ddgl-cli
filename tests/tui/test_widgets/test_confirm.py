# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for ddgl/tui/widgets/confirm.py."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Static

from ddgl.tui.widgets.confirm import ConfirmModal


class _ModalHostApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.result: bool | None = None

    def compose(self) -> ComposeResult:
        return iter(())

    def show(self) -> None:
        self.push_screen(ConfirmModal("Retry job?", "build/test — failed"), self._on_dismiss)

    def _on_dismiss(self, result: bool) -> None:
        self.result = result


async def test_renders_title_and_body() -> None:
    app = _ModalHostApp()
    async with app.run_test() as pilot:
        app.show()
        await pilot.pause()
        modal = app.screen
        assert modal.border_title == "Retry job?"
        assert modal.query_one("#confirm-body", Static).content == "build/test — failed"


async def test_enter_confirms() -> None:
    app = _ModalHostApp()
    async with app.run_test() as pilot:
        app.show()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.result is True


async def test_y_confirms() -> None:
    app = _ModalHostApp()
    async with app.run_test() as pilot:
        app.show()
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert app.result is True


async def test_escape_cancels() -> None:
    app = _ModalHostApp()
    async with app.run_test() as pilot:
        app.show()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.result is False


async def test_n_cancels() -> None:
    app = _ModalHostApp()
    async with app.run_test() as pilot:
        app.show()
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert app.result is False


async def test_clicking_confirm_button_confirms() -> None:
    app = _ModalHostApp()
    async with app.run_test() as pilot:
        app.show()
        await pilot.pause()
        await pilot.click("#confirm-btn")
        await pilot.pause()
        assert app.result is True


async def test_clicking_cancel_button_cancels() -> None:
    app = _ModalHostApp()
    async with app.run_test() as pilot:
        app.show()
        await pilot.pause()
        await pilot.click("#cancel-btn")
        await pilot.pause()
        assert app.result is False
