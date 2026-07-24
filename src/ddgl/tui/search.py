# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import re

from rich.text import Text


def apply_search(
    text: Text,
    pattern: str,
    *,
    regex: bool = False,
) -> tuple[Text, list[tuple[int, int]]]:
    """Return a highlighted copy of *text* and the list of (start, end) match spans.

    When *regex* is True the pattern is compiled as a regex (re.IGNORECASE).
    When *regex* is False the pattern is matched literally (case-insensitive).
    Returns (original, []) for an empty pattern or an invalid regex.
    """
    if not pattern:
        return text, []
    if regex:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return text, []
    else:
        rx = re.compile(re.escape(pattern), re.IGNORECASE)

    spans = [(m.start(), m.end()) for m in rx.finditer(text.plain)]
    if not spans:
        return text, []
    result = text.copy()
    for start, end in spans:
        result.stylize("reverse bold", start, end)
    return result, spans
