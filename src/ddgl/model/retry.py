# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import msgspec

from ddgl.model.job import Job


class RetryOutcome(msgspec.Struct, frozen=True):
    """The result of retrying one job, as part of a `core.retry.retry_jobs`
    batch.

    `new_job` and `error` are mutually exclusive: exactly one is set,
    depending on whether the retry POST succeeded.
    """

    old_job_id: int
    job_name: str
    new_job: Job | None = None
    error: str | None = None


class RetrySelection(msgspec.Struct, frozen=True):
    """Which jobs a retry will act on, and what they were narrowed from.

    `matched` counts what the filters selected *before* the retryable
    gate, so a caller can tell "nothing matched your filter" apart from
    "things matched but none of them can be retried" — two situations that
    want different advice.

    `pipeline_id`/`ref` name the pipeline the jobs belong to, for output.
    Both are None when the jobs span more than one pipeline, which only
    `--job ID` can produce.
    """

    jobs: list[Job]
    matched: int
    pipeline_id: int | None = None
    ref: str | None = None
