# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/cli/pipelines.py."""
from __future__ import annotations

import json
import sys

import pytest
from click.testing import CliRunner

from ddgl.cli import main
from ddgl.model.pipeline import Pipeline

# `ddgl.cli` does `from ddgl.cli.pipelines import pipelines`, which shadows
# the `ddgl.cli.pipelines` *attribute* with the click Group named
# "pipelines". The real submodule (whose globals the `pipelines_list`
# command function resolves `_list` against) is only reachable via
# sys.modules.
_pipelines_module = sys.modules["ddgl.cli.pipelines"]


class TestPipelinesListEmptyJson:
    def test_json_flag_outputs_empty_array_not_a_sentence(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_list(*args: object, **kwargs: object) -> tuple[list[Pipeline], str]:
            return [], "main"

        monkeypatch.setattr(_pipelines_module, "_list", fake_list)

        result = CliRunner().invoke(main, ["pipelines", "list", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_without_json_flag_prints_human_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_list(*args: object, **kwargs: object) -> tuple[list[Pipeline], str]:
            return [], "main"

        monkeypatch.setattr(_pipelines_module, "_list", fake_list)

        result = CliRunner().invoke(main, ["pipelines", "list"])

        assert result.exit_code == 0
        assert "No pipelines found for ref 'main'." in result.output
