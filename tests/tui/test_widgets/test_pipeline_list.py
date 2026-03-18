from __future__ import annotations

from ddgl.tui.widgets.pipeline_list import _fmt_datetime


class TestFmtDatetime:
    def test_standard_iso(self) -> None:
        assert _fmt_datetime("2024-03-12T14:32:00.000Z") == "12 Mar 14:32"

    def test_with_offset(self) -> None:
        assert _fmt_datetime("2024-01-05T09:07:00+00:00") == "5 Jan 09:07"

    def test_empty_string(self) -> None:
        assert _fmt_datetime("") == "—"

    def test_invalid_string(self) -> None:
        result = _fmt_datetime("not-a-date")
        assert result == "—"

    def test_single_digit_day(self) -> None:
        assert _fmt_datetime("2024-07-03T08:00:00Z") == "3 Jul 08:00"
