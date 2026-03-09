from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from ddgl.constants import DEFAULT_GITLAB_URL


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
        return token

    try:
        result = subprocess.run(
            ["ddtool", "auth", "gitlab", "token"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
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
    return Config(
        gitlab_url=os.environ.get("GITLAB_URL", DEFAULT_GITLAB_URL),
        private_token=_resolve_token(),
        project_id=os.environ.get("GITLAB_PROJECT_ID"),
    )
