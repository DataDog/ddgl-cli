from __future__ import annotations

from enum import StrEnum

DEFAULT_GITLAB_URL = "https://gitlab.ddbuild.io"


class PipelineStatus(StrEnum):
    CREATED = "created"
    WAITING = "waiting_for_resource"
    PREPARING = "preparing"
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    SKIPPED = "skipped"
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class JobStatus(StrEnum):
    CREATED = "created"
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    SKIPPED = "skipped"
    MANUAL = "manual"


PIPELINE_RUNNING = frozenset(
    {
        PipelineStatus.RUNNING,
        PipelineStatus.PENDING,
    }
)

PIPELINE_FINISHED = frozenset(
    {
        PipelineStatus.SUCCESS,
        PipelineStatus.FAILED,
        PipelineStatus.CANCELED,
        PipelineStatus.SKIPPED,
    }
)

JOB_RUNNING = frozenset(
    {
        JobStatus.RUNNING,
        JobStatus.PENDING,
    }
)
