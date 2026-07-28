# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import msgspec
from platformdirs import user_config_path

from ddgl.exceptions import ConfigError, ShellError
from ddgl.git import detect_project_path
from ddgl.model.config import ConfigFile
from ddgl.shell import run

logger = logging.getLogger("ddgl")

_CONFIG_FILE_ENV = "DDGL_CONFIG_FILE"


def _config_file_path() -> Path:
    override = os.environ.get(_CONFIG_FILE_ENV)
    if override:
        return Path(override)
    return user_config_path("ddgl") / "config.toml"


def load_config_file() -> ConfigFile:
    """Load the optional TOML config file. Returns ``ConfigFile()`` if absent."""
    path = _config_file_path()
    if not path.is_file():
        return ConfigFile()
    try:
        return ConfigFile.from_toml(path.read_bytes())
    except msgspec.DecodeError as e:
        raise ConfigError(f"Failed to parse config file {path}: {e}") from e


class Config(msgspec.Struct, frozen=True):
    """GitLab connection configuration."""

    gitlab_url: str
    private_token: str
    project_id: str | None = None

    @property
    def api_url(self) -> str:
        return f"{self.gitlab_url.rstrip('/')}/api/v4"


def _resolve_token(file_config: ConfigFile) -> str:
    """Resolve a GitLab token: env var, then token_file, then token_command."""
    token = os.environ.get("GITLAB_TOKEN", "")
    if token:
        logger.info("Token resolved via GITLAB_TOKEN env var")
        return token

    if file_config.token_file:
        path = Path(file_config.token_file)
        try:
            token = path.read_text().strip()
        except OSError as e:
            raise ConfigError(f"Failed to read token_file {path}: {e}") from e
        if token:
            logger.info("Token resolved via token_file")
            return token

    if file_config.token_command:
        try:
            stdout, _ = run(*file_config.token_command, check=True, timeout=10.0)
            if stdout:
                logger.info("Token resolved via token_command")
                return stdout
        except (FileNotFoundError, ShellError, subprocess.TimeoutExpired):
            pass

    raise ConfigError(
        "No GitLab token found. Set the GITLAB_TOKEN environment variable, "
        "or configure `token_file` or `token_command` in the config file."
    )


async def load_config() -> Config:
    """Load configuration from environment variables, an optional TOML config
    file, and git remote auto-detection.

    Token resolution (first match wins):
        1. GITLAB_TOKEN env var
        2. `token_file` in the config file
        3. `token_command` in the config file

    Project ID resolution (first match wins):
        1. GITLAB_PROJECT_ID env var
        2. GitLab remote URL in current repo
        3. GitHub remote URL in current repo (codesync: same org/repo path on GitLab)

    GitLab URL resolution (first match wins):
        1. GITLAB_URL env var
        2. `gitlab_url` in the config file
        3. DEFAULT_GITLAB_URL

    Config file location: DDGL_CONFIG_FILE env var, else the platform's
    standard config directory (see platformdirs) joined with "config.toml".
    """
    file_config = load_config_file()

    project_id = os.environ.get("GITLAB_PROJECT_ID")
    if project_id is None:
        project_id = await detect_project_path()
        if project_id is not None:
            logger.debug("Project ID detected from git remote: %s", project_id)

    config = Config(
        gitlab_url=os.environ.get("GITLAB_URL") or file_config.gitlab_url,
        private_token=_resolve_token(file_config),
        project_id=project_id,
    )
    logger.debug(
        "Config loaded: url=%s project_id=%s",
        config.gitlab_url, config.project_id,
    )
    return config
