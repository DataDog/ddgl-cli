# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/cli/logs.py."""
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

# See tests/cli/test_jobs.py for why this can't be a plain
# `import ddgl.cli.logs` — `ddgl.cli`'s `from ddgl.cli.logs import logs`
# shadows the submodule attribute with the click command of the same name.
logs_module = importlib.import_module("ddgl.cli.logs")

_TEST_CONFIG = Config(
    gitlab_url="https://gitlab.example.com",
    private_token="tok",
    project_id="grp/proj",
)
_ENCODED_PROJECT = "grp%2Fproj"


async def _fake_load_config(cache: Cache | None = None) -> Config:
    return _TEST_CONFIG


@pytest.fixture()
def mock_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[respx.MockRouter]:
    Cache._instance = None
    monkeypatch.setattr(logs_module, "load_config", _fake_load_config)
    # assert_all_called=False: the allowed-failure job's trace route is
    # mocked for both tests, but the exclusion test never fetches it —
    # that's the behavior under test, not an unused mock.
    with respx.mock(base_url=_TEST_CONFIG.api_url, assert_all_called=False) as router:
        yield router


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


class TestLogsEmptyJson:
    def test_json_flag_outputs_empty_object_not_a_sentence(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_fetch_logs(*args: object, **kwargs: object) -> list[tuple[str, str]]:
            return []

        monkeypatch.setattr(logs_module, "_fetch_logs", fake_fetch_logs)

        result = CliRunner().invoke(main, ["logs", "--job", "1", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == {}

    def test_without_json_flag_prints_human_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_fetch_logs(*args: object, **kwargs: object) -> list[tuple[str, str]]:
            return []

        monkeypatch.setattr(logs_module, "_fetch_logs", fake_fetch_logs)

        result = CliRunner().invoke(main, ["logs", "--job", "1"])

        assert result.exit_code == 0
        assert "No jobs found." in result.output


# ---------------------------------------------------------------------------
# --include-allowed-failures: exposed and threaded through `logs`
# ---------------------------------------------------------------------------


class TestLogsIncludeAllowedFailures:
    def _mock(self, mock_api: respx.MockRouter) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1").mock(
            return_value=Response(200, json={"id": 1, "ref": "main", "status": "failed", "sha": "abc"})
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(200, json=[
                _job_payload(1, name="unit-tests", allow_failure=False),
                _job_payload(2, name="flaky-e2e", allow_failure=True),
            ])
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/jobs/1/trace").mock(
            return_value=Response(200, text="unit log")
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/jobs/2/trace").mock(
            return_value=Response(200, text="flaky log")
        )

    def test_include_allowed_failures_threads_through(
        self, mock_api: respx.MockRouter
    ) -> None:
        self._mock(mock_api)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--no-cache", "-y", "logs", "--pipeline", "1", "-f", "--include-allowed-failures", "--json"],
        )
        assert result.exit_code == 0, result.output
        assert set(json.loads(result.output)) == {"unit-tests", "flaky-e2e"}

    def test_failed_only_excludes_allowed_failures_by_default(
        self, mock_api: respx.MockRouter
    ) -> None:
        self._mock(mock_api)
        runner = CliRunner()
        result = runner.invoke(
            main, ["--no-cache", "-y", "logs", "--pipeline", "1", "-f", "--json"]
        )
        assert result.exit_code == 0, result.output
        assert set(json.loads(result.output)) == {"unit-tests"}
