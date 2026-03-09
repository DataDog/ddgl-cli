from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ddgl.git import (
    detect_project_path,
    get_current_branch,
    get_remote_url,
    parse_project_path,
)


class TestParseProjectPath:
    """Pure-function tests — no git repo needed."""

    @pytest.mark.parametrize(
        "url, expected",
        [
            ("git@gitlab.com:group/project.git", "group/project"),
            ("git@gitlab.com:group/project", "group/project"),
            ("git@gitlab.example.com:org/sub-group/repo.git", "org/sub-group/repo"),
            ("https://gitlab.com/group/project.git", "group/project"),
            ("https://gitlab.com/group/project", "group/project"),
            ("https://gitlab.com/group/sub/project.git", "group/sub/project"),
            ("ssh://git@gitlab.com/group/project.git", "group/project"),
        ],
    )
    def test_parse(self, url: str, expected: str) -> None:
        assert parse_project_path(url) == expected


class TestGitIntegration:
    """Tests that need a real (temporary) git repo."""

    @pytest.fixture(autouse=True)
    def _chdir(self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_git_repo)

    async def test_get_current_branch(self) -> None:
        branch = await get_current_branch()
        assert branch == "my-feature"

    async def test_get_remote_url(self) -> None:
        url = await get_remote_url()
        assert url == "git@gitlab.com:my-group/my-project.git"


class TestDetectProjectPath:
    async def test_detects_gitlab_remote(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_git_repo)
        path = await detect_project_path()
        assert path == "my-group/my-project"

    async def test_detects_github_remote_codesync(
        self, tmp_github_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_github_repo)
        path = await detect_project_path()
        assert path == "DataDog/my-repo"

    async def test_prefers_gitlab_over_github(
        self, tmp_github_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_github_repo)
        subprocess.run(
            ["git", "remote", "add", "gitlab",
             "git@gitlab.ddbuild.io:DataDog/my-repo.git"],
            cwd=tmp_github_repo, check=True, capture_output=True,
        )
        path = await detect_project_path()
        # gitlab remote wins over github remote
        assert path == "DataDog/my-repo"

    async def test_returns_none_outside_git_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        path = await detect_project_path()
        assert path is None

    async def test_returns_none_no_remotes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo = tmp_path / "bare"
        repo.mkdir()
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.co",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.co"}
        subprocess.run(["git", "init"], cwd=repo, check=True,
                       capture_output=True, env=env)
        monkeypatch.chdir(repo)
        path = await detect_project_path()
        assert path is None
