from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse


async def get_current_branch() -> str:
    """Return the current git branch name."""
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--abbrev-ref", "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def get_remote_url(remote: str = "origin") -> str:
    """Return the URL of the given git remote."""
    proc = await asyncio.create_subprocess_exec(
        "git", "remote", "get-url", remote,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git remote get-url failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


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
