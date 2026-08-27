# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/cli/jobs.py."""
from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from typing import Any

import pytest
import respx
from click.testing import CliRunner
from httpx import Response

from ddgl.cache import Cache
from ddgl.cli import main
from ddgl.config import Config
from ddgl.constants import PipelineStatus
from ddgl.model.job import Job
from ddgl.model.pipeline import Pipeline

# `ddgl.cli`'s `from ddgl.cli.jobs import jobs` shadows the submodule
# attribute with the click Group of the same name — `import ddgl.cli.jobs`
# (in any form, including `as`) resolves via that same attribute chain and
# lands on the Group, not the module. `importlib.import_module` reads
# straight from `sys.modules` instead, sidestepping the collision.
jobs_module = importlib.import_module("ddgl.cli.jobs")

_TEST_CONFIG = Config(
    gitlab_url="https://gitlab.example.com",
    private_token="tok",
    project_id="grp/proj",
)
_ENCODED_PROJECT = "grp%2Fproj"

_PIPELINE = Pipeline(id=1, ref="main", status=PipelineStatus.SUCCESS)


async def _fake_load_config(cache: Cache | None = None) -> Config:
    return _TEST_CONFIG


@pytest.fixture()
def mock_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[respx.MockRouter]:
    Cache._instance = None
    # `ddgl.cli.jobs` (the module) and `ddgl.cli.jobs` (the click Group,
    # imported into ddgl.cli's namespace as `from ddgl.cli.jobs import
    # jobs`) share a name — patching via the string form resolves the
    # Group, not the module. Patch the module object directly instead.
    monkeypatch.setattr(jobs_module, "load_config", _fake_load_config)
    with respx.mock(base_url=_TEST_CONFIG.api_url) as router:
        yield router


def _pipeline_payload(pipeline_id: int = 1) -> dict[str, Any]:
    return {"id": pipeline_id, "ref": "main", "status": "failed", "sha": "abc"}


def _job_payload(
    job_id: int, *, name: str, status: str = "failed", allow_failure: bool = False,
) -> dict[str, Any]:
    return {
        "id": job_id, "name": name, "stage": "test", "status": status,
        "ref": "main", "allow_failure": allow_failure,
    }


# ---------------------------------------------------------------------------
# Empty-result output: JSON vs human-readable
# ---------------------------------------------------------------------------


class TestJobsListEmptyJson:
    def test_json_flag_outputs_empty_array_not_a_sentence(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_jobs_list(*args: object, **kwargs: object) -> tuple[Pipeline, list[Job]]:
            return _PIPELINE, []

        monkeypatch.setattr(jobs_module, "_jobs_list", fake_jobs_list)

        result = CliRunner().invoke(main, ["jobs", "list", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_without_json_flag_prints_human_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_jobs_list(*args: object, **kwargs: object) -> tuple[Pipeline, list[Job]]:
            return _PIPELINE, []

        monkeypatch.setattr(jobs_module, "_jobs_list", fake_jobs_list)

        result = CliRunner().invoke(main, ["jobs", "list"])

        assert result.exit_code == 0
        assert "No jobs found." in result.output


class TestJobsGetEmptyJson:
    def test_json_flag_outputs_empty_array_not_a_sentence(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_jobs_get(*args: object, **kwargs: object) -> list[Job]:
            return []

        monkeypatch.setattr(jobs_module, "_jobs_get", fake_jobs_get)

        result = CliRunner().invoke(main, ["jobs", "get", "--job", "1", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_without_json_flag_prints_human_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_jobs_get(*args: object, **kwargs: object) -> list[Job]:
            return []

        monkeypatch.setattr(jobs_module, "_jobs_get", fake_jobs_get)

        result = CliRunner().invoke(main, ["jobs", "get", "--job", "1"])

        assert result.exit_code == 0
        assert "No jobs found." in result.output


# ---------------------------------------------------------------------------
# --include-allowed-failures: exposed and threaded through `jobs list`
# ---------------------------------------------------------------------------


class TestJobsListIncludeAllowedFailures:
    def _mock_jobs(self, mock_api: respx.MockRouter) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1").mock(
            return_value=Response(200, json=_pipeline_payload())
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[
                _job_payload(1, name="unit-tests", allow_failure=False),
                _job_payload(2, name="flaky-e2e", allow_failure=True),
            ])
        )

    def test_option_is_recognized(self, mock_api: respx.MockRouter) -> None:
        """The option must exist on `jobs list` — a bare parse/usage check."""
        self._mock_jobs(mock_api)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--no-cache", "jobs", "list", "--pipeline", "1", "--include-allowed-failures", "--json"],
        )
        assert result.exit_code == 0, result.output
        assert "no such option" not in result.output.lower()

    def test_failed_only_excludes_allowed_failures_by_default(
        self, mock_api: respx.MockRouter
    ) -> None:
        self._mock_jobs(mock_api)
        runner = CliRunner()
        result = runner.invoke(
            main, ["--no-cache", "jobs", "list", "--pipeline", "1", "-f", "--json"]
        )
        assert result.exit_code == 0, result.output
        names = {j["name"] for j in json.loads(result.output)}
        assert names == {"unit-tests"}

    def test_include_allowed_failures_threads_through_to_filter_jobs(
        self, mock_api: respx.MockRouter
    ) -> None:
        self._mock_jobs(mock_api)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--no-cache", "jobs", "list", "--pipeline", "1", "-f", "--include-allowed-failures", "--json"],
        )
        assert result.exit_code == 0, result.output
        names = {j["name"] for j in json.loads(result.output)}
        assert names == {"unit-tests", "flaky-e2e"}
