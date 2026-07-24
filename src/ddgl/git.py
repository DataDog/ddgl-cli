# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import re
from urllib.parse import urlparse

from ddgl.exceptions import ShellError
from ddgl.shell import run_async


async def get_current_branch() -> str:
    """Return the current git branch name."""
    stdout, _ = await run_async("git", "rev-parse", "--abbrev-ref", "HEAD")
    return stdout


async def get_recent_shas(depth: int = 10) -> list[str]:
    """Return the last `depth` commit SHAs starting from HEAD (HEAD first).

    Uses: git log --format=%H -n {depth}
    Raises ShellError if not in a git repo.
    """
    stdout, _ = await run_async("git", "log", "--format=%H", f"-n{depth}")
    return [sha for sha in stdout.splitlines() if sha]


async def get_remote_url(remote: str = "origin") -> str:
    """Return the URL of the given git remote."""
    stdout, _ = await run_async("git", "remote", "get-url", remote)
    return stdout


def parse_project_path(remote_url: str) -> str:
    """Extract the project path (org/repo) from a git remote URL.

    Handles SSH and HTTPS formats for any host:
        git@github.com:DataDog/ddgl.git       -> DataDog/ddgl
        git@gitlab.ddbuild.io:DataDog/ddgl.git -> DataDog/ddgl
        https://github.com/DataDog/ddgl.git   -> DataDog/ddgl
        ssh://git@gitlab.com/group/project.git -> group/project
    """
    # SSH format: git@host:group/project.git
    ssh_match = re.match(r"^[\w.-]+@[\w.-]+:(.+?)(?:\.git)?$", remote_url)
    if ssh_match:
        return ssh_match.group(1)

    # HTTPS / ssh:// format
    parsed = urlparse(remote_url)
    path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path


def _is_gitlab_url(url: str) -> bool:
    return "gitlab" in url.lower()


def _is_github_url(url: str) -> bool:
    return "github" in url.lower()


async def detect_project_path() -> str | None:
    """Auto-detect the GitLab project path from the current repo's git remotes.

    Works for repos where GitHub is the source and GitLab is the CI mirror
    (Datadog's codesync setup): the org/repo path is identical on both hosts,
    so a GitHub remote is sufficient to locate the GitLab project.

    Resolution order across all remotes:
      1. First remote with a GitLab URL — parsed directly.
      2. First remote with a GitHub URL — same org/repo path used on GitLab.

    Returns None if no git repo or no suitable remote is found.
    """
    try:
        stdout, _ = await run_async("git", "remote", check=True)
    except (ShellError, FileNotFoundError):
        return None

    remotes = stdout.splitlines()
    if not remotes:
        return None

    github_path: str | None = None
    for name in remotes:
        try:
            url, _ = await run_async("git", "remote", "get-url", name)
        except ShellError:
            continue

        if _is_gitlab_url(url):
            return parse_project_path(url)
        if _is_github_url(url) and github_path is None:
            github_path = parse_project_path(url)

    return github_path
