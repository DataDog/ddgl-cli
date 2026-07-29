# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/cli/config.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ddgl.cli import main
from ddgl.config import Config
from ddgl.model import ConfigFile


class TestConfigPath:
    def test_prints_config_file_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_path = tmp_path / "config.toml"
        monkeypatch.setattr(
            "ddgl.cli.config.get_config_file_path", lambda: config_path,
        )

        result = CliRunner().invoke(main, ["config", "path"])

        assert result.exit_code == 0
        assert result.output.strip() == str(config_path)

    def test_prints_absolute_path_as_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_path = tmp_path / "config.toml"
        monkeypatch.setattr(
            "ddgl.cli.config.get_config_file_path", lambda: config_path,
        )

        result = CliRunner().invoke(main, ["config", "path", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == str(config_path.resolve())


class TestConfigShow:
    def test_prints_censored_resolved_config_as_toml(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def load_config() -> Config:
            return Config(
                gitlab_url="https://gitlab.example.com",
                private_token="secret-token",
                project_id=None,
            )

        monkeypatch.setattr("ddgl.cli.config.load_config", load_config)

        result = CliRunner().invoke(main, ["config", "show"])

        assert result.exit_code == 0
        assert 'gitlab_url = "https://gitlab.example.com"' in result.output
        assert 'private_token = "**********"' in result.output
        assert 'project_id = "N/A"' in result.output
        assert "secret-token" not in result.output

    def test_prints_uncensored_resolved_config_as_json(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def load_config() -> Config:
            return Config(
                gitlab_url="https://gitlab.example.com",
                private_token="secret-token",
                project_id="group/project",
            )

        monkeypatch.setattr("ddgl.cli.config.load_config", load_config)

        result = CliRunner().invoke(
            main, ["config", "show", "--json", "--no-censor"],
        )

        assert result.exit_code == 0
        assert json.loads(result.output) == {
            "gitlab_url": "https://gitlab.example.com",
            "private_token": "secret-token",
            "project_id": "group/project",
        }

    def test_prints_raw_config_file(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        raw_config = ConfigFile(
            gitlab_url="https://raw.gitlab.example.com",
            token_command=("get-token",),
            token_file="/run/secrets/gitlab-token",
            github_fallback=True,
        )
        monkeypatch.setattr(
            "ddgl.cli.config.load_config_file", lambda: raw_config,
        )

        result = CliRunner().invoke(main, ["config", "show", "--raw", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == {
            "gitlab_url": "https://raw.gitlab.example.com",
            "token_command": ["get-token"],
            "token_file": "/run/secrets/gitlab-token",
            "github_fallback": True,
        }
