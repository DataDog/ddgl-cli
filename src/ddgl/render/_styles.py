# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from datetime import datetime

_STATUS_COLORS: dict[str, str] = {
    "success": "green",
    "failed": "red",
    "running": "yellow",
    "canceled": "dim",
    "canceling": "dim",
    "pending": "cyan",
    "created": "cyan",
    "preparing": "cyan",
    "waiting_for_resource": "cyan",
    "waiting_for_callback": "cyan",
    "skipped": "dim italic",
    "manual": "blue",
    "scheduled": "blue",
}


def format_status(status: str) -> str:
    """Return '● <status>' with Rich markup for the status color."""
    color = _STATUS_COLORS.get(status, "")
    dot = f"[{color}]●[/{color}]" if color else "●"
    return f"{dot} {status}"


def format_duration(seconds: float | int | None) -> str:
    """Format seconds as 'Xm Ys', or '—' if unavailable."""
    if seconds is None:
        return "—"
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}m {secs:02d}s"


def format_sha(sha: str, length: int = 12) -> str:
    """Truncate a SHA to N characters."""
    return sha[:length] if sha else "—"


def format_datetime(iso: str | None, *, short: bool = False) -> str:
    """Parse an ISO datetime string.

    short=True  → 'Mar 12 14:30'
    short=False → full ISO string as-is
    Returns '—' for None/empty.
    """
    if not iso:
        return "—"
    if not short:
        return iso
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%b %d %H:%M")
    except ValueError:
        return iso
