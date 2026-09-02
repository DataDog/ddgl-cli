# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/cli/attach.py."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner
from httpx import Response

from ddgl.cache import Cache
from ddgl.cli import main
from ddgl.cli.attach import _attach, _exit_code, _use_live
from ddgl.client import GitLabClient
from ddgl.config import Config
from ddgl.constants import DEFAULT_JOB_RETRY_ATTEMPTS, DEFAULT_JOB_RETRY_TOTAL
from ddgl.exceptions import ConfigError
from ddgl.model.attach import ResultEvent, RetryPolicy

_TEST_CONFIG = Config(
    gitlab_url="https://gitlab.example.com",
    private_token="tok",
    project_id="grp/proj",
)
_ENCODED_PROJECT = "grp%2Fproj"

# ---------------------------------------------------------------------------
# _use_live — TTY auto-detect with explicit overrides
# ---------------------------------------------------------------------------


class TestUseLive:
    def test_force_live_wins_over_everything(self) -> None:
        assert _use_live(output_json=True, plain=True, force_live=True) is True

    def test_plain_forces_non_live(self) -> None:
        assert _use_live(output_json=False, plain=True, force_live=False) is False

    def test_json_forces_non_live_even_in_a_tty(self) -> None:
        assert _use_live(output_json=True, plain=False, force_live=False) is False

    def test_defaults_to_tty_detection(self) -> None:
        # pytest's stdout is never a TTY, so the fallback path is False here —
        # this exercises the console.is_terminal branch itself, not its value.
        assert _use_live(output_json=False, plain=False, force_live=False) is False


# ---------------------------------------------------------------------------
# _exit_code — GNU `timeout`-style exit code mapping
# ---------------------------------------------------------------------------


class TestExitCode:
    def test_success(self) -> None:
        result = ResultEvent(ts="x", status="success", reason="terminal")
        assert _exit_code(result) == 0

    def test_failed(self) -> None:
        result = ResultEvent(ts="x", status="failed", reason="terminal")
        assert _exit_code(result) == 1

    def test_canceled(self) -> None:
        result = ResultEvent(ts="x", status="canceled", reason="terminal")
        assert _exit_code(result) == 1

    def test_timeout_takes_priority_over_status(self) -> None:
        # A timeout result carries whatever status the pipeline had when we
        # gave up (often "running") — reason, not status, decides the code.
        result = ResultEvent(ts="x", status="running", reason="timeout")
        assert _exit_code(result) == 124


# ---------------------------------------------------------------------------
# CLI wiring: the one thing worth a full Click invocation — mutually
# exclusive flags, checked synchronously before any async work starts.
# ---------------------------------------------------------------------------


class TestAttachCliFlags:
    def test_plain_and_live_are_mutually_exclusive(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["attach", "--plain", "--live"])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# Error handling: a GitLab API error (e.g. a 500 mid-pagination) must map to
# exit 2, not crash with an uncaught traceback.
# ---------------------------------------------------------------------------


@pytest.fixture()
def _mock_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[respx.MockRouter]:
    Cache._instance = None
    monkeypatch.setattr("ddgl.cli.attach.load_config", _fake_load_config)
    with respx.mock(base_url=_TEST_CONFIG.api_url) as router:
        yield router


async def _fake_load_config(cache: Cache | None = None) -> Config:
    return _TEST_CONFIG


class TestAttachConfig:
    async def test_loads_config_with_open_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        received_cache: Cache | None = None

        async def fail_after_capturing_cache(cache: Cache | None = None) -> Config:
            nonlocal received_cache
            received_cache = cache
            raise ConfigError("stop")

        Cache._instance = None
        monkeypatch.setattr("ddgl.cli.attach.CACHE_DIR", tmp_path)
        monkeypatch.setattr("ddgl.cli.attach.load_config", fail_after_capturing_cache)

        exit_code = await _attach(
            ref="main", pipeline_id=None, depth=10, interval=1, heartbeat=False,
            detail="normal", wait_for_start=True, follow=False, timeout=None,
            retry_policy=RetryPolicy(), output_json=False, plain=True, force_live=False, no_cache=False,
        )

        assert exit_code == 2
        assert received_cache is not None


class TestAttachApiError:
    async def test_gitlab_500_maps_to_exit_2(
        self, _mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Resolve a running pipeline, then have the jobs endpoint 500 — the
        # exact failure the user hit on datadog-agent (500 mid-pagination).
        _mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines").mock(
            return_value=Response(200, json=[
                {"id": 1, "ref": "main", "status": "running", "sha": "abc",
                 "created_at": "2026-07-21T00:00:00.000Z"},
            ])
        )
        _mock_api.get(f"/projects/{_ENCODED_PROJECT}/pipelines/1/jobs").mock(
            return_value=Response(500, json={"message": "500 Internal Server Error"})
        )
        exit_code = await _attach(
            ref="main", pipeline_id=None, depth=10, interval=0.01, heartbeat=False,
            detail="normal", wait_for_start=True, follow=False, timeout=None,
            retry_policy=RetryPolicy(), output_json=False, plain=True, force_live=False, no_cache=True,
        )
        assert exit_code == 2

    async def test_transport_error_maps_to_exit_2(
        self, _mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def disconnected(*args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("connection reset")

        monkeypatch.setattr(GitLabClient, "_get_response", disconnected)

        exit_code = await _attach(
            ref="main", pipeline_id=None, depth=10, interval=0.01, heartbeat=False,
            detail="normal", wait_for_start=True, follow=False, timeout=None,
            retry_policy=RetryPolicy(), output_json=False, plain=True, force_live=False, no_cache=True,
        )

        assert exit_code == 2


# ---------------------------------------------------------------------------
# --retry flags
# ---------------------------------------------------------------------------


async def _unreachable(**kwargs: object) -> int:
    raise AssertionError("the flag guard should have exited before running attach")


class TestRetryFlags:
    def _policy_from(self, args: list[str], monkeypatch: pytest.MonkeyPatch) -> RetryPolicy:
        """Run `attach` far enough to capture the policy it built."""
        captured: dict[str, RetryPolicy] = {}

        async def fake_attach(**kwargs: object) -> int:
            captured["policy"] = kwargs["retry_policy"]  # type: ignore[assignment]
            return 0

        monkeypatch.setattr("ddgl.cli.attach._attach", fake_attach)
        result = CliRunner().invoke(main, ["attach", *args])
        assert result.exit_code == 0, result.output
        return captured["policy"]

    def test_disabled_without_the_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._policy_from([], monkeypatch).enabled is False

    def test_enabled_by_the_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        policy = self._policy_from(["--retry"], monkeypatch)
        assert policy.enabled is True
        assert policy.attempts_per_job == DEFAULT_JOB_RETRY_ATTEMPTS
        assert policy.total == DEFAULT_JOB_RETRY_TOTAL
        assert policy.exclude == ()

    def test_tuning_values_are_threaded_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        policy = self._policy_from(
            ["--retry", "--retry-attempts", "5", "--retry-total", "7",
             "--retry-exclude", "e2e", "--retry-exclude", "^deploy"],
            monkeypatch,
        )
        assert (policy.attempts_per_job, policy.total) == (5, 7)
        assert policy.exclude == ("e2e", "^deploy")

    def test_zero_means_unlimited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        policy = self._policy_from(
            ["--retry", "--retry-attempts", "0", "--retry-total", "0"], monkeypatch
        )
        assert (policy.attempts_per_job, policy.total) == (0, 0)

    @pytest.mark.parametrize(
        "flag", [
            ["--retry-attempts", "5"],
            ["--retry-total", "7"],
            ["--retry-exclude", "e2e"],
        ],
    )
    def test_tuning_without_retry_is_a_usage_error(
        self, flag: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Silently ignoring these would look like the cap was applied."""
        monkeypatch.setattr("ddgl.cli.attach._attach", _unreachable)
        result = CliRunner().invoke(main, ["attach", *flag])
        assert result.exit_code == 2
        assert "no effect without --retry" in result.output
        assert flag[0] in result.output

    def test_every_offending_flag_is_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ddgl.cli.attach._attach", _unreachable)
        result = CliRunner().invoke(
            main, ["attach", "--retry-attempts", "5", "--retry-total", "7"]
        )
        assert result.exit_code == 2
        assert "--retry-attempts" in result.output
        assert "--retry-total" in result.output

    def test_defaults_alone_are_not_treated_as_tuning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --retry and without tuning, the defaults must not trip
        the usage error."""
        assert self._policy_from([], monkeypatch).enabled is False

    def test_negative_values_are_rejected(self) -> None:
        result = CliRunner().invoke(main, ["attach", "--retry", "--retry-attempts", "-1"])
        assert result.exit_code == 2

    def test_invalid_exclude_regex_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Caught at parse time rather than as a traceback on whichever
        poll tick first had a candidate to match."""
        monkeypatch.setattr("ddgl.cli.attach._attach", _unreachable)
        result = CliRunner().invoke(main, ["attach", "--retry", "--retry-exclude", "("])
        assert result.exit_code == 2
        assert "not a valid regex" in result.output
