# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

if TYPE_CHECKING:
    from ddgl.model.job import Job

_STATUS_MAP: dict[str, tuple[str, str]] = {
    "success": ("✓", "#2DA160"),
    "failed": ("✗", "#DD2B0E"),
    "allowed-failure": ("⚠", "#C17D10"),
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


def job_status_key(job: Job) -> str:
    """Pseudo-status for a job, folding an allowed failure into its own key.

    "Allowed failure" isn't a status GitLab reports — it's `status ==
    failed AND allow_failure` — so it can't be answered from a bare status
    string. Callers that need to distinguish it take the `Job`.

    This is THE canonical "allowed-failure" pseudo-status — every module
    that needs one (icon/color lookups here, `job_list.status_token` for
    filtering/sorting/grouping, `pipeline_info`'s per-status counts, ...)
    calls this rather than re-deriving the same `has_failed and not
    is_blocking` check locally.
    """
    if job.is_allowed_failure:
        return "allowed-failure"
    return str(job.status)


def job_status_icon(job: Job) -> str:
    return status_icon(job_status_key(job))


def job_status_color(job: Job) -> str:
    return status_color(job_status_key(job))


def job_status_label(job: Job) -> str:
    """Human-readable status label for a job, rendering an allowed failure as "warning".

    A single place for the "warning" text so every job-status renderer
    (job list, DAG, history, job detail, ...) shows the same label instead
    of each re-deriving it locally.
    """
    return "warning" if job_status_key(job) == "allowed-failure" else str(job.status)
