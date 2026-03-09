from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import msgspec

from ddgl.constants import PIPELINE_FINISHED, PIPELINE_RUNNING, PipelineStatus


class Pipeline(msgspec.Struct):
    """Instrumented GitLab pipeline."""

    id: int
    ref: str
    status: PipelineStatus
    sha: str = ""
    web_url: str = ""
    source: str = ""
    created_at: str = ""
    finished_at: str | None = None
    duration: int | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any], **kwargs: Any) -> Pipeline:
        return cls(
            id=data["id"],
            ref=data["ref"],
            status=PipelineStatus(data["status"]),
            sha=data.get("sha", ""),
            web_url=data.get("web_url", ""),
            source=data.get("source", ""),
            created_at=data.get("created_at", ""),
            finished_at=data.get("finished_at"),
            duration=data.get("duration"),
            **kwargs,
        )

    @property
    def is_running(self) -> bool:
        return self.status in PIPELINE_RUNNING

    @property
    def is_finished(self) -> bool:
        return self.status in PIPELINE_FINISHED

    @property
    def elapsed(self) -> timedelta | None:
        if not self.created_at:
            return None
        start = datetime.fromisoformat(self.created_at)
        if self.finished_at:
            end = datetime.fromisoformat(self.finished_at)
        elif self.is_running:
            end = datetime.now(tz=start.tzinfo)
        else:
            return None
        return end - start
