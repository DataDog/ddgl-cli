from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import respx

from ddgl.client import GitLabClient
from ddgl.config import Config

TEST_CONFIG = Config(
    gitlab_url="https://gitlab.example.com",
    private_token="test-token",
    project_id="my-group/my-project",
)


@pytest.fixture()
def config() -> Config:
    return TEST_CONFIG


@pytest.fixture()
def mock_api() -> respx.MockRouter:
    """Pre-configured respx mock targeting the test GitLab API base URL."""
    with respx.mock(base_url=TEST_CONFIG.api_url) as router:
        yield router


@pytest.fixture()
async def client(mock_api: respx.MockRouter) -> GitLabClient:
    async with GitLabClient(TEST_CONFIG) as c:
        yield c


def _make_git_repo(path: Path, remote_name: str, remote_url: str) -> Path:
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.co",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.co"}
    subprocess.run(["git", "init", "-b", "my-feature"], cwd=path, check=True, env=env,
                   capture_output=True)
    subprocess.run(["git", "remote", "add", remote_name, remote_url],
                   cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, env=env,
                   capture_output=True)
    return path


@pytest.fixture()
def tmp_git_repo(tmp_path: Path) -> Path:
    """Temporary git repo with a GitLab remote (origin)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return _make_git_repo(repo, "origin", "git@gitlab.com:my-group/my-project.git")


@pytest.fixture()
def tmp_github_repo(tmp_path: Path) -> Path:
    """Temporary git repo with a GitHub remote only (codesync setup)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return _make_git_repo(repo, "origin", "git@github.com:DataDog/my-repo.git")
