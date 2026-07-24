# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from typing import Any

import msgspec

from ddgl.constants import JOB_RUNNING, JobStatus


class Job(msgspec.Struct):
    """Instrumented GitLab job."""

    id: int
    name: str
    stage: str
    status: JobStatus
    ref: str = ""
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
        return cls(
            id=data["id"],
            name=data["name"],
            stage=data["stage"],
            status=JobStatus(data["status"]),
            ref=data.get("ref", ""),
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
        return self.status == JobStatus.FAILED
