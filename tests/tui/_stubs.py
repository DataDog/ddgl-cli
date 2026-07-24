# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Local test fakes for tests/tui/.

These are local scaffolding types — not production imports.
"""
from __future__ import annotations

from ddgl.constants import JobStatus, PipelineStatus
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline


def make_pipeline(
    id: int = 1,
    ref: str = "main",
    status: PipelineStatus = PipelineStatus.SUCCESS,
    **kwargs: object,
) -> Pipeline:
    return Pipeline(id=id, ref=ref, status=status, **kwargs)  # type: ignore[arg-type]


def make_job(
    id: int = 1,
    name: str = "test",
    stage: str = "test",
    status: JobStatus = JobStatus.SUCCESS,
    **kwargs: object,
) -> Job:
    return Job(id=id, name=name, stage=stage, status=status, **kwargs)  # type: ignore[arg-type]
