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

    @classmethod
    def from_api(cls, data: dict[str, Any], **kwargs: Any) -> Job:
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
            **kwargs,
        )

    @property
    def is_running(self) -> bool:
        return self.status in JOB_RUNNING

    @property
    def has_failed(self) -> bool:
        return self.status == JobStatus.FAILED
