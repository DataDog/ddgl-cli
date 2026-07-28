# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ddgl.config import Config, load_config
from ddgl.exceptions import ConfigError, ShellError


class TestConfig:
    def test_api_url(self) -> None:
        cfg = Config(gitlab_url="https://gitlab.example.com", private_token="tok")
        assert cfg.api_url == "https://gitlab.example.com/api/v4"

    def test_api_url_strips_trailing_slash(self) -> None:
        cfg = Config(gitlab_url="https://gitlab.example.com/", private_token="tok")
        assert cfg.api_url == "https://gitlab.example.com/api/v4"

    def test_project_id_defaults_to_none(self) -> None:
        cfg = Config(gitlab_url="https://gitlab.example.com", private_token="tok")
        assert cfg.project_id is None


class TestLoadConfig:
    async def test_loads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "my-token")
        monkeypatch.setenv("GITLAB_URL", "https://my-gitlab.internal")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "123")

        cfg = await load_config()
        assert cfg.private_token == "my-token"
        assert cfg.gitlab_url == "https://my-gitlab.internal"
        assert cfg.project_id == "123"

    async def test_default_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_URL", raising=False)
        monkeypatch.delenv("GITLAB_PROJECT_ID", raising=False)

        with patch("ddgl.config.loader.detect_project_path", AsyncMock(return_value=None)):
            cfg = await load_config()
        assert cfg.gitlab_url == "https://gitlab.ddbuild.io"

    async def test_ddtool_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")

        with patch("ddgl.config.loader.run", return_value=("ddtool-token", "")):
            cfg = await load_config()

        assert cfg.private_token == "ddtool-token"

    async def test_ddtool_failure_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")

        with (
            patch(
                "ddgl.config.loader.run",
                side_effect=ShellError(["ddtool"], 1, "fail"),
            ),
            pytest.raises(ConfigError, match="No GitLab token found"),
        ):
            await load_config()

    async def test_ddtool_not_found_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")

        with (
            patch(
                "ddgl.config.loader.run",
                side_effect=FileNotFoundError,
            ),
            pytest.raises(ConfigError, match="No GitLab token found"),
        ):
            await load_config()

    async def test_project_id_from_env_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "explicit/project")

        detect = AsyncMock(return_value="git/detected")
        with patch("ddgl.config.loader.detect_project_path", detect):
            cfg = await load_config()

        assert cfg.project_id == "explicit/project"
        detect.assert_not_called()

    async def test_project_id_auto_detected(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_PROJECT_ID", raising=False)

        detect = AsyncMock(return_value="DataDog/my-repo")
        with patch("ddgl.config.loader.detect_project_path", detect):
            cfg = await load_config()

        assert cfg.project_id == "DataDog/my-repo"
        detect.assert_called_once()

    async def test_project_id_none_when_no_remote(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_PROJECT_ID", raising=False)

        with patch("ddgl.config.loader.detect_project_path", AsyncMock(return_value=None)):
            cfg = await load_config()

        assert cfg.project_id is None
