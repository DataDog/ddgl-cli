from __future__ import annotations

from textual.message import Message
from textual.widgets import Input


def fuzzy_match(query: str, target: str) -> bool:
    """Subsequence fuzzy match: every char of query must appear in order in target."""
    if not query:
        return True
    query = query.lower()
    qi = 0
    for char in target.lower():
        if char == query[qi]:
            qi += 1
            if qi == len(query):
                return True
    return False


class FuzzySearchInput(Input):
    """fzf-style search input that posts SearchChanged on every keystroke."""

    class SearchChanged(Message):
        def __init__(self, query: str) -> None:
            super().__init__()
            self.query = query

    def __init__(self, **kwargs: object) -> None:
        super().__init__(placeholder="Filter jobs...", **kwargs)  # type: ignore[arg-type]

    def on_input_changed(self, event: Input.Changed) -> None:
        self.post_message(self.SearchChanged(event.value.lower()))
