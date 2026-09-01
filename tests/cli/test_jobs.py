# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/cli/jobs.py."""
from __future__ import annotations

import json
import sys

import pytest
from click.testing import CliRunner

from ddgl.cli import main
from ddgl.constants import PipelineStatus
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline

# `ddgl.cli` does `from ddgl.cli.jobs import jobs`, which shadows the
# `ddgl.cli.jobs` *attribute* with the click Group named "jobs". The real
# submodule (whose globals the command functions below actually resolve
# `_jobs_list`/`_jobs_get` against) is only reachable via sys.modules.
_jobs_module = sys.modules["ddgl.cli.jobs"]

_PIPELINE = Pipeline(id=1, ref="main", status=PipelineStatus.SUCCESS)


class TestJobsListEmptyJson:
    def test_json_flag_outputs_empty_array_not_a_sentence(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_jobs_list(*args: object, **kwargs: object) -> tuple[Pipeline, list[Job]]:
            return _PIPELINE, []

        monkeypatch.setattr(_jobs_module, "_jobs_list", fake_jobs_list)

        result = CliRunner().invoke(main, ["jobs", "list", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_without_json_flag_prints_human_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_jobs_list(*args: object, **kwargs: object) -> tuple[Pipeline, list[Job]]:
            return _PIPELINE, []

        monkeypatch.setattr(_jobs_module, "_jobs_list", fake_jobs_list)

        result = CliRunner().invoke(main, ["jobs", "list"])

        assert result.exit_code == 0
        assert "No jobs found." in result.output


class TestJobsGetEmptyJson:
    def test_json_flag_outputs_empty_array_not_a_sentence(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_jobs_get(*args: object, **kwargs: object) -> list[Job]:
            return []

        monkeypatch.setattr(_jobs_module, "_jobs_get", fake_jobs_get)

        result = CliRunner().invoke(main, ["jobs", "get", "--job", "1", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_without_json_flag_prints_human_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_jobs_get(*args: object, **kwargs: object) -> list[Job]:
            return []

        monkeypatch.setattr(_jobs_module, "_jobs_get", fake_jobs_get)

        result = CliRunner().invoke(main, ["jobs", "get", "--job", "1"])

        assert result.exit_code == 0
        assert "No jobs found." in result.output
