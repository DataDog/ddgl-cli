from __future__ import annotations

from unittest.mock import patch

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
    def test_loads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "my-token")
        monkeypatch.setenv("GITLAB_URL", "https://my-gitlab.internal")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "123")

        cfg = load_config()
        assert cfg.private_token == "my-token"
        assert cfg.gitlab_url == "https://my-gitlab.internal"
        assert cfg.project_id == "123"

    def test_default_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_URL", raising=False)

        cfg = load_config()
        assert cfg.gitlab_url == "https://gitlab.ddbuild.io"

    def test_ddtool_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)

        with patch("ddgl.config.run", return_value=("ddtool-token", "")):
            cfg = load_config()

        assert cfg.private_token == "ddtool-token"

    def test_ddtool_failure_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)

        with (
            patch(
                "ddgl.config.run",
                side_effect=ShellError(["ddtool"], 1, "fail"),
            ),
            pytest.raises(ConfigError, match="No GitLab token found"),
        ):
            load_config()

    def test_ddtool_not_found_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)

        with (
            patch(
                "ddgl.config.run",
                side_effect=FileNotFoundError,
            ),
            pytest.raises(ConfigError, match="No GitLab token found"),
        ):
            load_config()
