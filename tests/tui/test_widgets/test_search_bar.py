"""Tests for ddgl/tui/widgets/search_bar.py."""
from __future__ import annotations

import pytest

from ddgl.tui.widgets.search_bar import fuzzy_match


@pytest.mark.parametrize(
    "query,target,expected",
    [
        # empty query matches everything
        ("", "anything", True),
        ("", "", True),
        # exact substring
        ("test", "unit-test", True),
        # subsequence (not contiguous)
        ("bld", "build-app", True),
        # first chars
        ("b", "build", True),
        # case insensitive
        ("BUILD", "build-app", True),
        ("build", "BUILD-APP", True),
        # full match
        ("deploy", "deploy", True),
        # no match
        ("xyz", "build", False),
        ("az", "alpha", False),
        # query longer than target
        ("toolong", "too", False),
    ],
)
def test_fuzzy_match(query: str, target: str, expected: bool) -> None:
    assert fuzzy_match(query, target) == expected


def test_fuzzy_match_order_matters() -> None:
    # chars must appear in order
    assert fuzzy_match("ba", "ab") is False
    assert fuzzy_match("ab", "ab") is True


def test_fuzzy_match_repeated_chars() -> None:
    assert fuzzy_match("aaa", "aababc") is True
    assert fuzzy_match("aaaa", "aababc") is False
