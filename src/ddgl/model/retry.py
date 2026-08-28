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
