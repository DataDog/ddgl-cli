# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass

from ddgl.constants import DEFAULT_GITLAB_URL
from ddgl.exceptions import ConfigError, ShellError
from ddgl.git import detect_project_path
from ddgl.shell import run

logger = logging.getLogger("ddgl")


@dataclass(frozen=True)
class Config:
    """GitLab connection configuration."""

    gitlab_url: str
    private_token: str
    project_id: str | None = None

    @property
    def api_url(self) -> str:
        return f"{self.gitlab_url.rstrip('/')}/api/v4"


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


async def load_config() -> Config:
    """Load configuration from environment variables with git remote auto-detection.

    Token resolution (first match wins):
        1. GITLAB_TOKEN env var
        2. `ddtool auth gitlab token` command

    Project ID resolution (first match wins):
        1. GITLAB_PROJECT_ID env var
        2. GitLab remote URL in current repo
        3. GitHub remote URL in current repo (codesync: same org/repo path on GitLab)

    Optional:
        GITLAB_URL  — GitLab instance URL (default: gitlab.ddbuild.io)
    """
    project_id = os.environ.get("GITLAB_PROJECT_ID")
    if project_id is None:
        project_id = await detect_project_path()
        if project_id is not None:
            logger.debug("Project ID detected from git remote: %s", project_id)

    config = Config(
        gitlab_url=os.environ.get("GITLAB_URL", DEFAULT_GITLAB_URL),
        private_token=_resolve_token(),
        project_id=project_id,
    )
    logger.debug(
        "Config loaded: url=%s project_id=%s",
        config.gitlab_url, config.project_id,
    )
    return config
