"""Tests for src/ddgl/cli/attach.py."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx
from click.testing import CliRunner
from httpx import Response

from ddgl.cache import Cache
from ddgl.cli import main
from ddgl.cli.attach import _attach, _exit_code, _use_live
from ddgl.config import Config
from ddgl.model.attach import AttachEvent

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
        result = AttachEvent(kind="result", ts="x", status="success", reason="terminal")
        assert _exit_code(result) == 0

    def test_failed(self) -> None:
        result = AttachEvent(kind="result", ts="x", status="failed", reason="terminal")
        assert _exit_code(result) == 1

    def test_canceled(self) -> None:
        result = AttachEvent(kind="result", ts="x", status="canceled", reason="terminal")
        assert _exit_code(result) == 1

    def test_timeout_takes_priority_over_status(self) -> None:
        # A timeout result carries whatever status the pipeline had when we
        # gave up (often "running") — reason, not status, decides the code.
        result = AttachEvent(kind="result", ts="x", status="running", reason="timeout")
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


async def _fake_load_config() -> Config:
    return _TEST_CONFIG


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
            output_json=False, plain=True, force_live=False, no_cache=True,
        )
        assert exit_code == 2
