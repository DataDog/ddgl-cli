# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/cli/retry.py."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
import respx
import rich_click as click
from click.testing import CliRunner
from httpx import Response

from ddgl.cache import Cache
from ddgl.cli import main
from ddgl.config import Config

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
    monkeypatch.setattr("ddgl.cli.retry.load_config", _fake_load_config)
    # assert_all_called=False: several tests register a retry route precisely
    # to assert it is *never* called (a skipped job, a declined prompt).
    with respx.mock(base_url=_TEST_CONFIG.api_url, assert_all_called=False) as router:
        yield router


# ---------------------------------------------------------------------------
# API response helpers
# ---------------------------------------------------------------------------


def _pipeline_payload(pipeline_id: int = 100, status: str = "failed") -> dict[str, Any]:
    return {"id": pipeline_id, "ref": "main", "status": status, "sha": "abc"}


def _job_payload(
    job_id: int,
    *,
    name: str,
    status: str = "failed",
    stage: str = "test",
    allow_failure: bool = False,
    pipeline_id: int = 100,
) -> dict[str, Any]:
    return {
        "id": job_id, "name": name, "stage": stage, "status": status,
        "ref": "main", "allow_failure": allow_failure,
        "pipeline": {"id": pipeline_id},
    }


# ---------------------------------------------------------------------------
# Usage errors — checked synchronously, before any API call
# ---------------------------------------------------------------------------


class TestUsageErrors:
    def test_force_without_a_filter_is_an_error(self) -> None:
        result = CliRunner().invoke(main, ["-y", "retry", "--force"])
        assert result.exit_code == 2
        assert "--force requires a job filter" in result.output

    def test_non_tty_without_yes_is_an_error(self) -> None:
        # CliRunner's stdin is never a TTY, which is exactly the case this
        # command refuses to auto-confirm (unlike `jobs get`/`logs`).
        result = CliRunner().invoke(main, ["retry", "--pipeline", "100"])
        assert result.exit_code == 2
        assert "refusing to retry without confirmation" in result.output

    def test_non_tty_without_yes_makes_no_api_call(
        self, mock_api: respx.MockRouter
    ) -> None:
        route = mock_api.post(
            f"/projects/{_ENCODED_PROJECT}/pipelines/100/retry"
        ).mock(return_value=Response(200, json=_pipeline_payload(status="running")))
        CliRunner().invoke(main, ["retry", "--pipeline", "100"])
        assert not route.called


# ---------------------------------------------------------------------------
# Bulk mode — no job filter, one pipeline-level POST
# ---------------------------------------------------------------------------


class TestBulkMode:
    def test_retries_the_pipeline(self, mock_api: respx.MockRouter) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100").mock(
            return_value=Response(200, json=_pipeline_payload())
        )
        retry_route = mock_api.post(
            f"/projects/{_ENCODED_PROJECT}/pipelines/100/retry"
        ).mock(return_value=Response(200, json=_pipeline_payload(status="running")))

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100"]
        )

        assert result.exit_code == 0
        assert retry_route.call_count == 1
        assert "Retried pipeline" in result.output

    def test_does_not_fetch_the_job_list(self, mock_api: respx.MockRouter) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100").mock(
            return_value=Response(200, json=_pipeline_payload())
        )
        jobs_route = mock_api.get(
            f"/projects/{_ENCODED_PROJECT}/pipelines/100/jobs"
        ).mock(return_value=Response(200, json=[]))
        mock_api.post(f"/projects/{_ENCODED_PROJECT}/pipelines/100/retry").mock(
            return_value=Response(200, json=_pipeline_payload(status="running"))
        )

        CliRunner().invoke(main, ["--no-cache", "-y", "retry", "--pipeline", "100"])

        assert not jobs_route.called

    def test_rejected_retry_exits_1(self, mock_api: respx.MockRouter) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100").mock(
            return_value=Response(200, json=_pipeline_payload())
        )
        mock_api.post(f"/projects/{_ENCODED_PROJECT}/pipelines/100/retry").mock(
            return_value=Response(403, json={"message": "403 Forbidden"})
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100"]
        )

        assert result.exit_code == 1
        assert "could not retry pipeline" in result.output

    def test_unresolvable_pipeline_exits_2(self, mock_api: respx.MockRouter) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100").mock(
            return_value=Response(404, json={"message": "404 Not found"})
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100"]
        )

        assert result.exit_code == 2

    def test_json_output(self, mock_api: respx.MockRouter) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100").mock(
            return_value=Response(200, json=_pipeline_payload())
        )
        mock_api.post(f"/projects/{_ENCODED_PROJECT}/pipelines/100/retry").mock(
            return_value=Response(200, json=_pipeline_payload(status="running"))
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "--json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["mode"] == "bulk"
        assert payload["pipeline_id"] == 100
        assert payload["status"] == "running"
        # GitLab doesn't report which jobs it restarted.
        assert payload["retried"] == []
        assert payload["errors"] == []


# ---------------------------------------------------------------------------
# Targeted mode — a filter selects jobs, one POST each
# ---------------------------------------------------------------------------


class TestTargetedMode:
    def _mock_pipeline_with_jobs(
        self, mock_api: respx.MockRouter, jobs: list[dict[str, Any]]
    ) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100").mock(
            return_value=Response(200, json=_pipeline_payload())
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100/jobs").mock(
            return_value=Response(200, json=jobs)
        )

    def test_failed_filter_retries_each_job(self, mock_api: respx.MockRouter) -> None:
        self._mock_pipeline_with_jobs(mock_api, [
            _job_payload(1, name="unit-tests"),
            _job_payload(2, name="lint"),
        ])
        r1 = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )
        r2 = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/2/retry").mock(
            return_value=Response(200, json=_job_payload(12, name="lint", status="pending"))
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "-f"]
        )

        assert result.exit_code == 0
        assert r1.call_count == 1
        assert r2.call_count == 1
        assert "Retried 2 job(s)" in result.output

    def test_uses_per_job_endpoint_not_pipeline_retry(
        self, mock_api: respx.MockRouter
    ) -> None:
        self._mock_pipeline_with_jobs(mock_api, [_job_payload(1, name="unit-tests")])
        bulk = mock_api.post(
            f"/projects/{_ENCODED_PROJECT}/pipelines/100/retry"
        ).mock(return_value=Response(200, json=_pipeline_payload(status="running")))
        mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )

        CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "--stage", "test"]
        )

        assert not bulk.called

    def test_stage_filter_narrows_the_set(self, mock_api: respx.MockRouter) -> None:
        self._mock_pipeline_with_jobs(mock_api, [
            _job_payload(1, name="unit-tests", stage="test"),
            _job_payload(2, name="docker", stage="build"),
        ])
        wanted = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )
        unwanted = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/2/retry").mock(
            return_value=Response(200, json=_job_payload(12, name="docker", status="pending"))
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "--stage", "test"]
        )

        assert result.exit_code == 0
        assert wanted.call_count == 1
        assert not unwanted.called

    def test_skips_jobs_that_are_not_retryable(self, mock_api: respx.MockRouter) -> None:
        self._mock_pipeline_with_jobs(mock_api, [
            _job_payload(1, name="unit-tests", status="success"),
            _job_payload(2, name="lint", status="running"),
        ])
        posted = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "--stage", "test"]
        )

        assert result.exit_code == 0
        assert not posted.called
        assert "none retryable" in result.output

    def test_no_match_at_all(self, mock_api: respx.MockRouter) -> None:
        self._mock_pipeline_with_jobs(mock_api, [
            _job_payload(1, name="unit-tests", stage="test"),
        ])

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "--stage", "nope"]
        )

        assert result.exit_code == 0
        assert "No jobs match the given filters." in result.output

    def test_canceled_jobs_are_retryable(self, mock_api: respx.MockRouter) -> None:
        self._mock_pipeline_with_jobs(mock_api, [
            _job_payload(1, name="unit-tests", status="canceled"),
        ])
        posted = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "--stage", "test"]
        )

        assert result.exit_code == 0
        assert posted.call_count == 1

    def test_partial_failure_exits_1(self, mock_api: respx.MockRouter) -> None:
        self._mock_pipeline_with_jobs(mock_api, [
            _job_payload(1, name="unit-tests"),
            _job_payload(2, name="lint"),
        ])
        mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )
        mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/2/retry").mock(
            return_value=Response(403, json={"message": "403 Forbidden"})
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "-f"]
        )

        assert result.exit_code == 1
        assert "could not be retried" in result.output

    def test_json_output(self, mock_api: respx.MockRouter) -> None:
        self._mock_pipeline_with_jobs(mock_api, [
            _job_payload(1, name="unit-tests"),
            _job_payload(2, name="lint"),
        ])
        mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )
        mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/2/retry").mock(
            return_value=Response(403, json={"message": "403 Forbidden"})
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "-f", "--json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["mode"] == "targeted"
        assert payload["pipeline_id"] == 100
        assert payload["ref"] == "main"
        assert payload["retried"] == [
            {
                "old_job_id": 1,
                "job_name": "unit-tests",
                "new_job_id": 11,
                "status": "pending",
            }
        ]
        assert len(payload["errors"]) == 1
        assert payload["errors"][0]["job_name"] == "lint"
        assert payload["errors"][0]["error"]

    def test_json_output_when_nothing_to_do(self, mock_api: respx.MockRouter) -> None:
        """--json always emits a parseable object, even for a no-op."""
        self._mock_pipeline_with_jobs(mock_api, [
            _job_payload(1, name="unit-tests", status="success"),
        ])

        result = CliRunner().invoke(
            main,
            ["--no-cache", "-y", "retry", "--pipeline", "100", "--stage", "test", "--json"],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["retried"] == []
        assert payload["errors"] == []


# ---------------------------------------------------------------------------
# --include-allowed-failures: same semantics as `jobs list`/`logs`, so that
# previewing with `jobs list -f` and acting with `retry -f` select the same
# set rather than differing by allowed failures.
# ---------------------------------------------------------------------------


class TestAllowedFailures:
    def _mock(self, mock_api: respx.MockRouter) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100").mock(
            return_value=Response(200, json=_pipeline_payload())
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100/jobs").mock(
            return_value=Response(200, json=[
                _job_payload(1, name="unit-tests", allow_failure=False),
                _job_payload(2, name="flaky-e2e", allow_failure=True),
            ])
        )

    def test_failed_excludes_allowed_failures_by_default(
        self, mock_api: respx.MockRouter
    ) -> None:
        self._mock(mock_api)
        blocking = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )
        allowed = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/2/retry").mock(
            return_value=Response(200, json=_job_payload(12, name="flaky-e2e", status="pending"))
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "-f"]
        )

        assert result.exit_code == 0
        assert blocking.call_count == 1
        assert not allowed.called

    def test_include_allowed_failures_widens_the_set(
        self, mock_api: respx.MockRouter
    ) -> None:
        self._mock(mock_api)
        blocking = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )
        allowed = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/2/retry").mock(
            return_value=Response(200, json=_job_payload(12, name="flaky-e2e", status="pending"))
        )

        result = CliRunner().invoke(
            main,
            [
                "--no-cache", "-y", "retry", "--pipeline", "100", "-f",
                "--include-allowed-failures",
            ],
        )

        assert result.exit_code == 0
        assert blocking.call_count == 1
        assert allowed.call_count == 1


# ---------------------------------------------------------------------------
# --job: retries IDs directly, overriding the other filters
# ---------------------------------------------------------------------------


class TestJobIds:
    def test_retries_the_given_ids(self, mock_api: respx.MockRouter) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/jobs/1").mock(
            return_value=Response(200, json=_job_payload(1, name="unit-tests"))
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/jobs/2").mock(
            return_value=Response(200, json=_job_payload(2, name="lint"))
        )
        r1 = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )
        r2 = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/2/retry").mock(
            return_value=Response(200, json=_job_payload(12, name="lint", status="pending"))
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--job", "1", "--job", "2"]
        )

        assert result.exit_code == 0
        assert r1.call_count == 1
        assert r2.call_count == 1

    def test_skips_pipeline_resolution(self, mock_api: respx.MockRouter) -> None:
        pipeline_route = mock_api.get(
            f"/projects/{_ENCODED_PROJECT}/pipelines/100"
        ).mock(return_value=Response(200, json=_pipeline_payload()))
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/jobs/1").mock(
            return_value=Response(200, json=_job_payload(1, name="unit-tests"))
        )
        mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )

        CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--pipeline", "100", "--job", "1"]
        )

        assert not pipeline_route.called

    def test_overrides_other_filters(self, mock_api: respx.MockRouter) -> None:
        """A --stage that matches nothing must not narrow --job's set."""
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/jobs/1").mock(
            return_value=Response(200, json=_job_payload(1, name="unit-tests", stage="test"))
        )
        posted = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )

        result = CliRunner().invoke(
            main,
            ["--no-cache", "-y", "retry", "--job", "1", "--stage", "nonexistent"],
        )

        assert result.exit_code == 0
        assert posted.call_count == 1

    def test_reports_the_pipeline_from_the_job(self, mock_api: respx.MockRouter) -> None:
        """Job.pipeline_id is what lets --job name its pipeline in output."""
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/jobs/1").mock(
            return_value=Response(200, json=_job_payload(1, name="unit-tests", pipeline_id=100))
        )
        mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--job", "1", "--json"]
        )

        payload = json.loads(result.stdout)
        assert payload["pipeline_id"] == 100
        assert payload["ref"] == "main"

    def test_force_retries_a_successful_job(self, mock_api: respx.MockRouter) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/jobs/1").mock(
            return_value=Response(200, json=_job_payload(1, name="unit-tests", status="success"))
        )
        posted = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--job", "1", "--force"]
        )

        assert result.exit_code == 0
        assert posted.call_count == 1

    def test_without_force_a_successful_job_is_skipped(
        self, mock_api: respx.MockRouter
    ) -> None:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/jobs/1").mock(
            return_value=Response(200, json=_job_payload(1, name="unit-tests", status="success"))
        )
        posted = mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )

        result = CliRunner().invoke(
            main, ["--no-cache", "-y", "retry", "--job", "1"]
        )

        assert result.exit_code == 0
        assert not posted.called
        assert "none retryable" in result.output


# ---------------------------------------------------------------------------
# Confirmation — the prompt itself, with the TTY gate stubbed out
# ---------------------------------------------------------------------------


class TestConfirmation:
    def _mock(self, mock_api: respx.MockRouter) -> respx.Route:
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100").mock(
            return_value=Response(200, json=_pipeline_payload())
        )
        mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/100/jobs").mock(
            return_value=Response(200, json=[_job_payload(1, name="unit-tests")])
        )
        return mock_api.post(f"/projects/{_ENCODED_PROJECT}/jobs/1/retry").mock(
            return_value=Response(200, json=_job_payload(11, name="unit-tests", status="pending"))
        )

    def test_declining_the_prompt_makes_no_api_call(
        self, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        posted = self._mock(mock_api)
        monkeypatch.setattr("ddgl.cli.retry.stdin_is_tty", lambda: True)
        monkeypatch.setattr(click, "confirm", _decline)

        result = CliRunner().invoke(
            main, ["--no-cache", "retry", "--pipeline", "100", "-f"]
        )

        assert result.exit_code == 1  # Click's own abort code
        assert not posted.called

    def test_preview_lists_the_jobs_before_prompting(
        self, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        posted = self._mock(mock_api)
        monkeypatch.setattr("ddgl.cli.retry.stdin_is_tty", lambda: True)
        monkeypatch.setattr(click, "confirm", _accept)

        result = CliRunner().invoke(
            main, ["--no-cache", "retry", "--pipeline", "100", "-f"]
        )

        assert result.exit_code == 0
        assert posted.call_count == 1
        # The preview goes to stderr, so --json output stays parseable.
        assert "This will retry 1 job(s) in pipeline #100 (main)" in result.stderr
        assert "unit-tests" in result.stderr


def _decline(*args: Any, **kwargs: Any) -> bool:
    raise click.Abort()


def _accept(*args: Any, **kwargs: Any) -> bool:
    return True
