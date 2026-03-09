from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from ddgl.constants import DEFAULT_GITLAB_URL
from ddgl.shell import ShellError, get_logger, run

logger = get_logger("ddgl")


@dataclass(frozen=True)
class Config:
    """GitLab connection configuration."""

    gitlab_url: str
    private_token: str
    project_id: str | None = None

    @property
    def api_url(self) -> str:
        return f"{self.gitlab_url.rstrip('/')}/api/v4"


class ConfigError(Exception):
    """Raised when required configuration is missing."""


def _resolve_token() -> str:
    """Resolve a GitLab token: env var first, then ddtool."""
    token = os.environ.get("GITLAB_TOKEN", "")
    if token:
        logger.info("Token resolved via GITLAB_TOKEN env var")
        return token

    try:
        stdout, _ = run(
            "ddtool", "auth", "gitlab", "token",
            check=True,
            timeout=10.0,
        )
        if stdout:
            logger.info("Token resolved via ddtool")
            return stdout
    except (FileNotFoundError, ShellError, subprocess.TimeoutExpired):
        pass

    raise ConfigError(
        "No GitLab token found. Set the GITLAB_TOKEN environment variable "
        "or ensure `ddtool auth gitlab token` is available."
    )


def load_config() -> Config:
    """Load configuration from environment variables.

    Token resolution (first match wins):
        1. GITLAB_TOKEN env var
        2. `ddtool auth gitlab token` command

    Optional:
        GITLAB_URL         — GitLab instance URL (default: gitlab.ddbuild.io)
        GITLAB_PROJECT_ID  — Default project ID to operate on
    """
    config = Config(
        gitlab_url=os.environ.get("GITLAB_URL", DEFAULT_GITLAB_URL),
        private_token=_resolve_token(),
        project_id=os.environ.get("GITLAB_PROJECT_ID"),
    )
    logger.debug(
        "Config loaded: url=%s project_id=%s",
        config.gitlab_url, config.project_id,
    )
    return config
