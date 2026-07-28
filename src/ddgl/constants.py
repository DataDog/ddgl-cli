# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from enum import StrEnum

DEFAULT_GITLAB_URL = "https://gitlab.ddbuild.io"

MAX_PAGES = 50
MAX_CONCURRENT_PAGE_FETCHES = 10  # cap on simultaneous in-flight page requests in _get_all

RETRY_ATTEMPTS = 3  # 1 initial + 2 retries, for a single client GET call
RETRY_BACKOFF_INITIAL_SECONDS = 0.5  # delay before retry 1
RETRY_BACKOFF_MULTIPLIER = 2.0  # each subsequent retry's delay is multiplied by this

# attach()'s poll loop: give up after this many CONSECUTIVE poll ticks fail
# (even after the client's own per-call retries are exhausted), rather than
# warning and skipping forever with no --timeout set.
MAX_CONSECUTIVE_POLL_FAILURES = 5

CACHE_TTL_DDTOOL_TOKEN = 3600.0            # 1 h   — ddtool-issued GitLab tokens
CACHE_TTL_FINISHED_PIPELINE = 604800.0    # 1 w   — finished pipelines don't change
CACHE_TTL_FINISHED_JOB = 604800.0         # 1 w   — finished jobs don't change
CACHE_TTL_API_PIPELINE_LIST = 10.0        # 10 s  — pipeline list browse
CACHE_TTL_API_PIPELINE = 30.0             # 30 s  — single pipeline detail
CACHE_TTL_API_JOB_LIST = 15.0             # 15 s  — job list (burst dedup for history tab)
CACHE_TTL_API_JOB = 60.0                  # 60 s  — single job detail


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
