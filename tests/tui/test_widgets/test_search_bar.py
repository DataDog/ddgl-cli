# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for ddgl/tui/widgets/search_bar.py."""
from __future__ import annotations

import pytest

from ddgl.tui.widgets.search_bar import FilterSpec, fuzzy_match, parse_query


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


# ---------------------------------------------------------------------------
# parse_query
# ---------------------------------------------------------------------------


def test_parse_query_empty() -> None:
    spec = parse_query("")
    assert spec == FilterSpec(text="", statuses=set(), stages=set())


def test_parse_query_plain_text() -> None:
    spec = parse_query("lint build")
    assert spec.text == "lint build"
    assert spec.statuses == set()
    assert spec.stages == set()


def test_parse_query_status_token() -> None:
    spec = parse_query("status:failed")
    assert spec.statuses == {"failed"}
    assert spec.stages == set()
    assert spec.text == ""


def test_parse_query_stage_token() -> None:
    spec = parse_query("stage:build")
    assert spec.stages == {"build"}
    assert spec.statuses == set()
    assert spec.text == ""


def test_parse_query_combined() -> None:
    spec = parse_query("status:failed stage:build lint")
    assert spec.statuses == {"failed"}
    assert spec.stages == {"build"}
    assert spec.text == "lint"


def test_parse_query_multiple_status_tokens() -> None:
    spec = parse_query("status:failed status:canceled")
    assert spec.statuses == {"failed", "canceled"}


def test_parse_query_status_lowercased() -> None:
    spec = parse_query("status:FAILED")
    assert spec.statuses == {"failed"}


def test_parse_query_stage_lowercased() -> None:
    spec = parse_query("stage:Build")
    assert spec.stages == {"build"}


def test_parse_query_remainder_preserves_order() -> None:
    spec = parse_query("a status:failed b stage:build c")
    assert spec.text == "a b c"
