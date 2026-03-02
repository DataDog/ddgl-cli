from __future__ import annotations

import os
from dataclasses import dataclass


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


def load_config() -> Config:
    """Load configuration from environment variables.

    Required:
        GITLAB_PRIVATE_TOKEN — GitLab personal access token

    Optional:
        GITLAB_URL         — GitLab instance URL (default: https://gitlab.com)
        GITLAB_PROJECT_ID  — Default project ID to operate on
    """
    token = os.environ.get("GITLAB_PRIVATE_TOKEN", "")
    if not token:
        raise ConfigError(
            "GITLAB_PRIVATE_TOKEN environment variable is required. "
            "Create a token at Settings > Access Tokens in your GitLab instance."
        )

    return Config(
        gitlab_url=os.environ.get("GITLAB_URL", "https://gitlab.com"),
        private_token=token,
        project_id=os.environ.get("GITLAB_PROJECT_ID"),
    )
