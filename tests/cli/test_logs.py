# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/cli/logs.py."""
from __future__ import annotations

import json
import sys

import pytest
from click.testing import CliRunner

from ddgl.cli import main

# `ddgl.cli` does `from ddgl.cli.logs import logs`, which shadows the
# `ddgl.cli.logs` *attribute* with the click command named "logs". The real
# submodule (whose globals the `logs` command function resolves `_fetch_logs`
# against) is only reachable via sys.modules.
_logs_module = sys.modules["ddgl.cli.logs"]


class TestLogsEmptyJson:
    def test_json_flag_outputs_empty_object_not_a_sentence(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_fetch_logs(*args: object, **kwargs: object) -> list[tuple[str, str]]:
            return []

        monkeypatch.setattr(_logs_module, "_fetch_logs", fake_fetch_logs)

        result = CliRunner().invoke(main, ["logs", "--job", "1", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == {}

    def test_without_json_flag_prints_human_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_fetch_logs(*args: object, **kwargs: object) -> list[tuple[str, str]]:
            return []

        monkeypatch.setattr(_logs_module, "_fetch_logs", fake_fetch_logs)

        result = CliRunner().invoke(main, ["logs", "--job", "1"])

        assert result.exit_code == 0
        assert "No jobs found." in result.output
