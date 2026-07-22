# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
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


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero out the client's retry backoff for every test.

    Without this, any test that mocks a retryable status (408/429/5xx)
    would incur real multi-second sleeps via GitLabClient._get_response's
    retry logic — slow and unnecessary for a unit test suite. A test that
    specifically wants to verify retry TIMING can override this locally
    with its own monkeypatch.setattr call.
    """
    monkeypatch.setattr("ddgl.client.RETRY_BACKOFF_SECONDS", (0.0, 0.0))


@pytest.fixture()
def config() -> Config:
    return TEST_CONFIG


@pytest.fixture()
def mock_api() -> Iterator[respx.MockRouter]:
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
