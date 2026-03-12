from __future__ import annotations

from dataclasses import dataclass, field

from textual.message import Message
from textual.widgets import Input


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
            statuses.add(token[len("status:") :].lower())
        elif token.startswith("stage:"):
            stages.add(token[len("stage:") :].lower())
        else:
            remainder.append(token)
    return FilterSpec(text=" ".join(remainder), statuses=statuses, stages=stages)


class FuzzySearchInput(Input):
    """fzf-style search input that posts SearchChanged on every keystroke."""

    class SearchChanged(Message):
        def __init__(self, query: str) -> None:
            super().__init__()
            self.query = query

    def __init__(self, **kwargs: object) -> None:
        super().__init__(placeholder="Filter jobs...", **kwargs)  # type: ignore[arg-type]

    def on_input_changed(self, event: Input.Changed) -> None:
        self.post_message(self.SearchChanged(event.value))
