# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from typing import Any

import msgspec

from ddgl.constants import JOB_RETRYABLE, JOB_RUNNING, JobStatus


class Job(msgspec.Struct):
    """Instrumented GitLab job."""

    id: int
    name: str
    stage: str
    status: JobStatus
    ref: str = ""
    pipeline_id: int | None = None
    """The containing pipeline's ID, from the job payload's nested
    `pipeline` object. None only when a payload omits it."""
    web_url: str = ""
    allow_failure: bool = False
    failure_reason: str | None = None
    duration: float | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    log: str | None = None
    # Enriched fields — only populated from the single-job API (get_job),
    # not from the list-pipeline-jobs endpoint.
    runner_description: str | None = None
    runner_tags: tuple[str, ...] = ()
    queued_duration: float | None = None
    needs: tuple[str, ...] = ()

    @classmethod
    def from_api(cls, data: dict[str, Any], **kwargs: Any) -> Job:
        runner = data.get("runner") or {}
        needs_raw = data.get("needs") or []
        pipeline = data.get("pipeline") or {}
        return cls(
            id=data["id"],
            name=data["name"],
            stage=data["stage"],
            status=JobStatus(data["status"]),
            ref=data.get("ref", ""),
            pipeline_id=pipeline.get("id"),
            web_url=data.get("web_url", ""),
            allow_failure=data.get("allow_failure", False),
            failure_reason=data.get("failure_reason"),
            duration=data.get("duration"),
            created_at=data.get("created_at", ""),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            runner_description=runner.get("description"),
            runner_tags=tuple(runner.get("tags") or []),
            queued_duration=data.get("queued_duration"),
            needs=tuple(n["name"] for n in needs_raw if isinstance(n, dict) and "name" in n),
            **kwargs,
        )

    @property
    def is_running(self) -> bool:
        return self.status in JOB_RUNNING

    @property
    def has_failed(self) -> bool:
        """Status is FAILED. Does not account for `allow_failure` — see `is_blocking`."""
        return self.status == JobStatus.FAILED

    @property
    def is_blocking(self) -> bool:
        """Failed in a way that actually fails the pipeline."""
        return self.has_failed and not self.allow_failure

    @property
    def is_allowed_failure(self) -> bool:
        """Failed, but explicitly permitted to — doesn't fail the pipeline.

        Equivalent to `has_failed and not is_blocking`. The canonical check
        for "should this render/filter/group as an allowed failure" —
        render, TUI status, and job-list filtering all call this instead
        of each re-deriving the same boolean.
        """
        return self.has_failed and not self.is_blocking

    @property
    def is_retryable(self) -> bool:
        """Failed or canceled — the default candidate set for auto-retry
        and `ddgl retry`'s targeted mode.

        A deliberate narrowing of GitLab's actual rule, which also permits
        retrying a *successful* job — `--force` (cli/retry.py) is the
        escape hatch for that wider set.
        """
        return self.status in JOB_RETRYABLE
