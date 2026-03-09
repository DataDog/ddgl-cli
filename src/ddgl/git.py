from __future__ import annotations

import re
from urllib.parse import urlparse

from ddgl.shell import run_async


async def get_current_branch() -> str:
    """Return the current git branch name."""
    stdout, _ = await run_async("git", "rev-parse", "--abbrev-ref", "HEAD")
    return stdout


async def get_remote_url(remote: str = "origin") -> str:
    """Return the URL of the given git remote."""
    stdout, _ = await run_async("git", "remote", "get-url", remote)
    return stdout


def parse_project_path(remote_url: str) -> str:
    """Extract the GitLab project path from a remote URL.

    Handles both SSH and HTTPS formats:
        git@gitlab.com:group/project.git  -> group/project
        https://gitlab.com/group/project.git -> group/project
        https://gitlab.com/group/sub/project   -> group/sub/project
    """
    # SSH format: git@host:group/project.git
    ssh_match = re.match(r"^[\w.-]+@[\w.-]+:(.+?)(?:\.git)?$", remote_url)
    if ssh_match:
        return ssh_match.group(1)

    # HTTPS format
    parsed = urlparse(remote_url)
    path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path
