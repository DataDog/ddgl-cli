# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import logging

import pytest

from ddgl.cli import setup_logging
from ddgl.exceptions import ShellError
from ddgl.shell import run, run_async


@pytest.fixture(autouse=True)
def _allow_caplog() -> None:
    """Enable propagation so caplog can capture ddgl.* logs."""
    ddgl_logger = logging.getLogger("ddgl")
    ddgl_logger.propagate = True
    yield  # type: ignore[misc]
    ddgl_logger.propagate = False


class TestSetupLogging:
    def test_default_level_is_warning(self) -> None:
        setup_logging(0)
        assert logging.getLogger("ddgl").level == logging.WARNING

    def test_verbose_sets_info(self) -> None:
        setup_logging(1)
        assert logging.getLogger("ddgl").level == logging.INFO

    def test_double_verbose_sets_debug(self) -> None:
        setup_logging(2)
        assert logging.getLogger("ddgl").level == logging.DEBUG

    def test_no_propagation_to_root(self) -> None:
        setup_logging(0)
        # setup_logging sets propagate=False, but our fixture restores it
        # after the test. We verify setup_logging itself sets it.
        ddgl_logger = logging.getLogger("ddgl")
        setup_logging(0)
        # Check that setup_logging explicitly sets propagate=False
        assert ddgl_logger.propagate is False

    def test_child_logger_inherits(self) -> None:
        setup_logging(1)
        child = logging.getLogger("ddgl.test")
        assert child.getEffectiveLevel() == logging.INFO

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDGL_LOG_LEVEL", "DEBUG")
        setup_logging(0)
        assert logging.getLogger("ddgl").level == logging.DEBUG

    def test_verbose_overrides_env_var(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DDGL_LOG_LEVEL", "DEBUG")
        setup_logging(1)
        assert logging.getLogger("ddgl").level == logging.INFO

    def test_invalid_env_var_defaults_to_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DDGL_LOG_LEVEL", "NONSENSE")
        setup_logging(0)
        assert logging.getLogger("ddgl").level == logging.WARNING


class TestRunSync:
    def test_captures_stdout(self) -> None:
        stdout, _ = run("echo", "hello")
        assert stdout == "hello"

    def test_raises_on_failure(self) -> None:
        with pytest.raises(ShellError, match="rc="):
            run("false")

    def test_check_false_no_raise(self) -> None:
        run("false", check=False)

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            run("nonexistent_command_xyz_12345")

    def test_logs_command(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="ddgl"):
            run("echo", "hi")
        assert "$ echo hi" in caplog.text
        assert '"hi"' in caplog.text

    def test_logs_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="ddgl"):
            with pytest.raises(ShellError):
                run("false")
        assert "rc=" in caplog.text


class TestRunAsync:
    async def test_captures_stdout(self) -> None:
        stdout, _ = await run_async("echo", "hello")
        assert stdout == "hello"

    async def test_raises_on_failure(self) -> None:
        with pytest.raises(ShellError):
            await run_async("false")

    async def test_check_false_no_raise(self) -> None:
        await run_async("false", check=False)

    async def test_logs_command(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="ddgl"):
            await run_async("echo", "hi")
        assert "$ echo hi" in caplog.text
        assert '"hi"' in caplog.text
