"""Tests for src/ddgl/cli/attach.py."""
from __future__ import annotations

from click.testing import CliRunner

from ddgl.cli import main
from ddgl.cli.attach import _exit_code, _use_live
from ddgl.model.attach import AttachEvent

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
