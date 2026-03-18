from __future__ import annotations

import re
from dataclasses import dataclass, field

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Input


def fuzzy_match(query: str, target: str) -> bool:
    """Subsequence fuzzy match: every char of query must appear in order in target."""
    if not query:
        return True
    query = query.lower()
    qi = 0
    for char in target.lower():
        if qi < len(query) and char == query[qi]:
            qi += 1
    return qi == len(query)


@dataclass
class FilterSpec:
    """Compiled filter state passed to JobListPanel."""

    text: str = ""
    statuses: set[str] = field(default_factory=set)
    stages: set[str] = field(default_factory=set)
    regex: bool = False


def parse_query(raw: str) -> FilterSpec:
    """Parse a raw search string into a FilterSpec.

    Tokens of the form ``status:<value>`` and ``stage:<value>`` are extracted
    and placed into the corresponding sets; the remaining tokens form the
    free-text fuzzy-match query.

    Example::

        parse_query("status:failed stage:build lint")
        # FilterSpec(text="lint", statuses={"failed"}, stages={"build"})
    """
    statuses: set[str] = set()
    stages: set[str] = set()
    remainder: list[str] = []
    for token in raw.split():
        if token.startswith("status:"):
            statuses.add(token[len("status:"):].lower())
        elif token.startswith("stage:"):
            stages.add(token[len("stage:"):].lower())
        else:
            remainder.append(token)
    return FilterSpec(text=" ".join(remainder), statuses=statuses, stages=stages)


class FuzzySearchInput(Widget):
    """Search input with an inline regex mode toggle button.

    Posts ``SearchChanged`` on every keystroke and on regex toggle.
    The ``.*`` button highlights when regex mode is active.
    """

    DEFAULT_CSS = """
    FuzzySearchInput {
        layout: horizontal;
        height: auto;
        width: 1fr;
    }
    FuzzySearchInput Input {
        width: 1fr;
    }
    FuzzySearchInput Button {
        width: auto;
        min-width: 5;
        margin-left: 1;
    }
    """

    class SearchChanged(Message):
        def __init__(self, query: str, *, regex: bool = False) -> None:
            super().__init__()
            self.query = query
            self.regex = regex

    _regex: reactive[bool] = reactive(False)

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter jobs...", id="fuzzy-input")
        yield Button(".*", id="regex-toggle", variant="default")

    def focus(self, scroll_visible: bool = True) -> Widget:  # type: ignore[override]
        self.query_one("#fuzzy-input", Input).focus(scroll_visible=scroll_visible)
        return self

    def clear(self) -> None:
        self.query_one("#fuzzy-input", Input).clear()

    def watch__regex(self, value: bool) -> None:
        try:
            self.query_one("#regex-toggle", Button).variant = (
                "primary" if value else "default"
            )
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "regex-toggle":
            self._regex = not self._regex
            query = self.query_one("#fuzzy-input", Input).value
            self.post_message(self.SearchChanged(query, regex=self._regex))

    def on_input_changed(self, event: Input.Changed) -> None:
        self.post_message(self.SearchChanged(event.value, regex=self._regex))


def job_text_matches(text: str, spec: FilterSpec) -> bool:
    """Return True if *text* matches spec.text under the current mode."""
    if not spec.text:
        return True
    if spec.regex:
        try:
            return bool(re.search(spec.text, text, re.IGNORECASE))
        except re.error:
            return True  # invalid regex → don't filter anything out
    return fuzzy_match(spec.text, text)
