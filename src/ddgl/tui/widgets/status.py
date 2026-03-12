from __future__ import annotations

from rich.text import Text

_STATUS_MAP: dict[str, tuple[str, str]] = {
    "success": ("✓", "green"),
    "failed": ("✗", "red"),
    "running": ("●", "yellow"),
    "pending": ("○", "cyan"),
    "canceled": ("⊘", "dim"),
    "skipped": ("→", "dim"),
    "manual": ("▶", "blue"),
    "created": ("○", "white"),
    "preparing": ("◌", "cyan"),
    "waiting_for_resource": ("◌", "cyan"),
    "waiting_for_callback": ("◌", "cyan"),
    "scheduled": ("⏱", "blue"),
    "canceling": ("⊘", "yellow"),
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
