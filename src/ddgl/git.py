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


async def get_recent_shas(depth: int = 10, start: str = "HEAD") -> list[str]:
    """Return the last `depth` commit SHAs starting from `start` (`start` first).

    Uses: git log --format=%H -n {depth} {start}
    Raises ShellError if not in a git repo, or if `start` doesn't resolve
    (e.g. an `origin/<branch>` that hasn't been fetched locally).
    """
    stdout, _ = await run_async("git", "log", "--format=%H", f"-n{depth}", start)
    return [sha for sha in stdout.splitlines() if sha]


_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


def looks_like_sha(ref: str) -> bool:
    """Heuristic: does `ref` look like a (possibly abbreviated) commit SHA?

    Matches 7-40 hex characters — git's usual abbreviation length up to a
    full SHA. Not foolproof (a branch literally named e.g. "deadbeef"
    would misfire), but a reasonable heuristic in practice.
    """
    return bool(_SHA_RE.fullmatch(ref))


async def get_remote_url(remote: str = "origin") -> str:
    """Return the URL of the given git remote."""
    stdout, _ = await run_async("git", "remote", "get-url", remote)
    return stdout


def parse_project_path(remote_url: str) -> str:
    """Extract the project path (org/repo) from a git remote URL.

    Handles SSH and HTTPS formats for any host:
        git@github.com:DataDog/ddgl.git         -> DataDog/ddgl
        git@gitlab.example.com:DataDog/ddgl.git -> DataDog/ddgl
        https://github.com/DataDog/ddgl.git     -> DataDog/ddgl
        ssh://git@gitlab.com/group/project.git  -> group/project
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


async def detect_project_path(allow_github_fallback: bool = False) -> str | None:
    """Auto-detect the GitLab project path from the current repo's git remotes.

    Resolution order across all remotes:
      1. First remote with a GitLab URL — parsed directly.
      2. If `allow_github_fallback` is True, the first remote with a GitHub
         URL — on the (opt-in) assumption that the org/repo path is
         identical on both hosts, e.g. GitHub-as-source with GitLab as a CI
         mirror. This convention is not assumed by default.

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
        if allow_github_fallback and _is_github_url(url) and github_path is None:
            github_path = parse_project_path(url)

    return github_path
