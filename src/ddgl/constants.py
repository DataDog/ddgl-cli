from __future__ import annotations

from enum import StrEnum

DEFAULT_GITLAB_URL = "https://gitlab.ddbuild.io"

MAX_PAGES = 50

CACHE_TTL_DDTOOL_TOKEN = 3600.0            # 1 h   — ddtool-issued GitLab tokens
CACHE_TTL_FINISHED_PIPELINE = 604800.0    # 1 w   — finished pipelines don't change
CACHE_TTL_FINISHED_JOB = 604800.0         # 1 w   — finished jobs don't change
CACHE_TTL_API_RESPONSE_SINGLE = 300.0     # 5 min — single-resource GETs (/pipelines/123)
CACHE_TTL_API_RESPONSE_LIST = 30.0        # 30 s  — list endpoints (/pipelines, /jobs)


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
