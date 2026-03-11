from __future__ import annotations

from enum import StrEnum

DEFAULT_GITLAB_URL = "https://gitlab.ddbuild.io"

MAX_PAGES = 50

CACHE_TTL_DDTOOL_TOKEN = 3600.0            # 1 h   — ddtool-issued GitLab tokens
CACHE_TTL_FINISHED_PIPELINE = 604800.0    # 1 w   — finished pipelines don't change
CACHE_TTL_FINISHED_JOB = 604800.0         # 1 w   — finished jobs don't change
CACHE_TTL_API_RESPONSE_SINGLE = 300.0     # 5 min — single-resource GETs (/pipelines/123)
CACHE_TTL_API_RESPONSE_LIST = 30.0        # 30 s  — list endpoints (/pipelines, /jobs)


class PipelineScope(StrEnum):
    """Valid values for the `scope` query parameter on GET /projects/:id/pipelines.

    Note: despite sharing the name, the job endpoint uses a *different* scope
    vocabulary (see JobStatus).  GitLab models these as two unrelated parameters.
    """

    RUNNING = "running"
    PENDING = "pending"
    FINISHED = "finished"
    BRANCHES = "branches"
    TAGS = "tags"


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
    """Job status values — also used as the `scope[]` filter on GET /projects/:id/pipelines/:id/jobs.

    Note: despite sharing the name, the pipeline list endpoint uses a *different*
    scope vocabulary (see PipelineScope).  GitLab models these as two unrelated parameters.
    """

    CREATED = "created"
    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    CANCELING = "canceling"
    SKIPPED = "skipped"
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    WAITING_FOR_RESOURCE = "waiting_for_resource"
    WAITING_FOR_CALLBACK = "waiting_for_callback"


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
