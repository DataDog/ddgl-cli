# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ddgl.cache import Cache, CacheNS
from ddgl.config import Config, load_config
from ddgl.config.loader import load_config_file
from ddgl.exceptions import ConfigError, ShellError
from ddgl.model.config import ConfigFile


@pytest.fixture(autouse=True)
def config_file_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point DDGL_CONFIG_FILE at a fresh, non-existent path for every test.

    Keeps tests hermetic: without this, load_config() would fall back to the
    real platformdirs config path and could pick up a config file that
    happens to exist on the machine running the tests.
    """
    path = tmp_path / "config.toml"
    monkeypatch.setenv("DDGL_CONFIG_FILE", str(path))
    return path


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


class TestLoadConfigFile:
    def test_missing_file_returns_defaults(self) -> None:
        assert load_config_file() == ConfigFile()

    def test_valid_file_is_parsed(self, config_file_path: Path) -> None:
        config_file_path.write_text('gitlab_url = "https://gitlab.example.com"\n')
        assert load_config_file().gitlab_url == "https://gitlab.example.com"

    def test_malformed_toml_raises(self, config_file_path: Path) -> None:
        config_file_path.write_text("this is not valid toml [[[")
        with pytest.raises(ConfigError, match="Failed to parse config file"):
            load_config_file()

    def test_unknown_key_raises(self, config_file_path: Path) -> None:
        config_file_path.write_text('bogus_key = "x"\n')
        with pytest.raises(ConfigError, match="Failed to parse config file"):
            load_config_file()


class TestLoadConfig:
    async def test_loads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "my-token")
        monkeypatch.setenv("GITLAB_URL", "https://my-gitlab.internal")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "123")

        cfg = await load_config()
        assert cfg.private_token == "my-token"
        assert cfg.gitlab_url == "https://my-gitlab.internal"
        assert cfg.project_id == "123"

    async def test_default_url_is_public_gitlab(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_URL", raising=False)
        monkeypatch.delenv("GITLAB_PROJECT_ID", raising=False)

        with patch(
            "ddgl.config.loader.detect_project_path", AsyncMock(return_value=None),
        ):
            cfg = await load_config()
        assert cfg.gitlab_url == "https://gitlab.com"

    async def test_token_command_fallback(
        self, config_file_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")
        config_file_path.write_text(
            'token_command = ["my-auth-tool", "print-token"]\n'
        )

        with patch("ddgl.config.loader.run", return_value=("cmd-token", "")):
            cfg = await load_config()

        assert cfg.private_token == "cmd-token"

    async def test_token_command_failure_raises(
        self, config_file_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")
        config_file_path.write_text(
            'token_command = ["my-auth-tool", "print-token"]\n'
        )

        with (
            patch(
                "ddgl.config.loader.run",
                side_effect=ShellError(["my-auth-tool"], 1, "fail"),
            ),
            pytest.raises(ConfigError, match="No GitLab token found"),
        ):
            await load_config()

    async def test_token_command_not_found_raises(
        self, config_file_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")
        config_file_path.write_text(
            'token_command = ["my-auth-tool", "print-token"]\n'
        )

        with (
            patch(
                "ddgl.config.loader.run",
                side_effect=FileNotFoundError,
            ),
            pytest.raises(ConfigError, match="No GitLab token found"),
        ):
            await load_config()

    async def test_no_token_source_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")

        with pytest.raises(ConfigError, match="No GitLab token found"):
            await load_config()

    async def test_token_file_fallback(
        self,
        config_file_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")
        token_path = tmp_path / "token.txt"
        token_path.write_text("file-token\n")
        config_file_path.write_text(f'token_file = "{token_path}"\n')

        cfg = await load_config()

        assert cfg.private_token == "file-token"

    async def test_token_file_missing_raises(
        self,
        config_file_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")
        missing_path = tmp_path / "nonexistent-token.txt"
        config_file_path.write_text(f'token_file = "{missing_path}"\n')

        with pytest.raises(ConfigError, match="Failed to read token_file"):
            await load_config()

    async def test_token_file_takes_precedence_over_token_command(
        self,
        config_file_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")
        token_path = tmp_path / "token.txt"
        token_path.write_text("file-token\n")
        config_file_path.write_text(
            f'token_file = "{token_path}"\n'
            'token_command = ["my-auth-tool"]\n'
        )

        with patch("ddgl.config.loader.run") as mock_run:
            cfg = await load_config()

        assert cfg.private_token == "file-token"
        mock_run.assert_not_called()

    async def test_env_token_takes_precedence_over_token_file(
        self,
        config_file_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "env-token")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")
        token_path = tmp_path / "token.txt"
        token_path.write_text("file-token\n")
        config_file_path.write_text(f'token_file = "{token_path}"\n')

        cfg = await load_config()

        assert cfg.private_token == "env-token"

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

        with patch(
            "ddgl.config.loader.detect_project_path", AsyncMock(return_value=None),
        ):
            cfg = await load_config()

        assert cfg.project_id is None


class TestGithubFallback:
    """github_fallback resolution and plumbing into detect_project_path()."""

    async def test_disabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_PROJECT_ID", raising=False)
        monkeypatch.delenv("DDGL_GITHUB_FALLBACK", raising=False)

        detect = AsyncMock(return_value=None)
        with patch("ddgl.config.loader.detect_project_path", detect):
            await load_config()

        detect.assert_called_once_with(allow_github_fallback=False)

    async def test_enabled_via_env_var(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_PROJECT_ID", raising=False)
        monkeypatch.setenv("DDGL_GITHUB_FALLBACK", "true")

        detect = AsyncMock(return_value=None)
        with patch("ddgl.config.loader.detect_project_path", detect):
            await load_config()

        detect.assert_called_once_with(allow_github_fallback=True)

    async def test_enabled_via_config_file(
        self, config_file_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_PROJECT_ID", raising=False)
        monkeypatch.delenv("DDGL_GITHUB_FALLBACK", raising=False)
        config_file_path.write_text("github_fallback = true\n")

        detect = AsyncMock(return_value=None)
        with patch("ddgl.config.loader.detect_project_path", detect):
            await load_config()

        detect.assert_called_once_with(allow_github_fallback=True)

    async def test_env_var_takes_precedence_over_config_file(
        self, config_file_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_PROJECT_ID", raising=False)
        monkeypatch.setenv("DDGL_GITHUB_FALLBACK", "false")
        config_file_path.write_text("github_fallback = true\n")

        detect = AsyncMock(return_value=None)
        with patch("ddgl.config.loader.detect_project_path", detect):
            await load_config()

        detect.assert_called_once_with(allow_github_fallback=False)


class TestConfigFileIntegration:
    """load_config() consulting the TOML config file for gitlab_url."""

    async def test_gitlab_url_from_config_file(
        self, config_file_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_URL", raising=False)
        config_file_path.write_text('gitlab_url = "https://gitlab.example.com"\n')

        with patch(
            "ddgl.config.loader.detect_project_path", AsyncMock(return_value=None),
        ):
            cfg = await load_config()

        assert cfg.gitlab_url == "https://gitlab.example.com"

    async def test_env_var_takes_precedence_over_config_file(
        self, config_file_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://from-env.example.com")
        config_file_path.write_text('gitlab_url = "https://from-file.example.com"\n')

        with patch(
            "ddgl.config.loader.detect_project_path", AsyncMock(return_value=None),
        ):
            cfg = await load_config()

        assert cfg.gitlab_url == "https://from-env.example.com"

    async def test_missing_config_file_behaves_like_before(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.delenv("GITLAB_URL", raising=False)

        with patch(
            "ddgl.config.loader.detect_project_path", AsyncMock(return_value=None),
        ):
            cfg = await load_config()

        assert cfg.gitlab_url == "https://gitlab.com"

    async def test_malformed_config_file_raises(
        self, config_file_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        config_file_path.write_text("this is not valid toml [[[")

        with pytest.raises(ConfigError, match="Failed to parse config file"):
            await load_config()


class TestTokenCaching:
    """_resolve_token() caching via the CacheNS.TOKENS namespace."""

    @pytest.fixture(autouse=True)
    def _reset_cache_singleton(self) -> None:
        Cache._instance = None
        yield
        Cache._instance = None

    async def test_token_command_result_is_cached(
        self,
        config_file_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")
        config_file_path.write_text('token_command = ["my-auth-tool"]\n')

        with (
            Cache.open(tmp_path / "cache") as cache,
            patch(
                "ddgl.config.loader.run", return_value=("cmd-token", ""),
            ) as run_mock,
        ):
            cfg1 = await load_config(cache=cache)
            cfg2 = await load_config(cache=cache)

        assert cfg1.private_token == "cmd-token"
        assert cfg2.private_token == "cmd-token"
        run_mock.assert_called_once()  # second load hit the cache

    async def test_env_token_is_not_cached(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "env-token")
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")

        with Cache.open(tmp_path / "cache") as cache:
            cfg = await load_config(cache=cache)
            assert cache[CacheNS.TOKENS][cfg.gitlab_url] is None

    async def test_no_cache_behaves_like_before(
        self, config_file_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_PROJECT_ID", "p/r")
        config_file_path.write_text('token_command = ["my-auth-tool"]\n')

        with patch(
            "ddgl.config.loader.run", return_value=("cmd-token", ""),
        ) as run_mock:
            cfg1 = await load_config()
            cfg2 = await load_config()

        assert cfg1.private_token == "cmd-token"
        assert cfg2.private_token == "cmd-token"
        assert run_mock.call_count == 2  # no cache => re-run every time
