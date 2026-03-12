from __future__ import annotations

from rich.text import Text

_STATUS_MAP: dict[str, tuple[str, str]] = {
    "success": ("✓", "#2DA160"),
    "failed": ("✗", "#DD2B0E"),
    "running": ("●", "#1F75CB"),
    "pending": ("○", "#C17D10"),
    "canceled": ("⊘", "#737278"),
    "skipped": ("→", "#737278"),
    "manual": ("▶", "#6B4FBB"),
    "created": ("○", "#AAAAAA"),
    "preparing": ("◌", "#1F75CB"),
    "waiting_for_resource": ("◌", "#C17D10"),
    "waiting_for_callback": ("◌", "#C17D10"),
    "scheduled": ("⏱", "#6B4FBB"),
    "canceling": ("⊘", "#C17D10"),
}

_DEFAULT: tuple[str, str] = ("?", "white")


def status_icon(status: str) -> str:
    icon, _ = _STATUS_MAP.get(status, _DEFAULT)
    return icon


def status_color(status: str) -> str:
    _, color = _STATUS_MAP.get(status, _DEFAULT)
    return color


def status_text(status: str) -> Text:
    icon, color = _STATUS_MAP.get(status, _DEFAULT)
    return Text(f"{icon} {status}", style=color)
