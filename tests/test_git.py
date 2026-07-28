# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ddgl.exceptions import ShellError
from ddgl.git import (
    detect_project_path,
    get_current_branch,
    get_recent_shas,
    get_remote_url,
    looks_like_sha,
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


class TestGetRecentShas:
    """Tests for get_recent_shas()."""

    @pytest.fixture()
    def repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Repo with 3 commits."""
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.co",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.co"}
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True,
                       env=env, capture_output=True)
        for i in range(3):
            (tmp_path / f"file{i}.txt").write_text(str(i))
            subprocess.run(["git", "add", "."], cwd=tmp_path, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-m", f"commit {i}"], cwd=tmp_path,
                           check=True, env=env, capture_output=True)
        monkeypatch.chdir(tmp_path)
        return tmp_path

    async def test_returns_shas(self, repo: Path) -> None:
        shas = await get_recent_shas(depth=3)
        assert len(shas) == 3
        assert all(len(s) == 40 for s in shas)

    async def test_depth_limits_results(self, repo: Path) -> None:
        shas = await get_recent_shas(depth=2)
        assert len(shas) == 2

    async def test_depth_larger_than_history(self, repo: Path) -> None:
        shas = await get_recent_shas(depth=100)
        assert len(shas) == 3  # only 3 commits in repo

    async def test_head_first(self, repo: Path) -> None:
        shas = await get_recent_shas(depth=3)
        # HEAD is first — get HEAD sha via git
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
        )
        head_sha = result.stdout.decode().strip()
        assert shas[0] == head_sha

    async def test_start_walks_from_given_revision_not_head(self, repo: Path) -> None:
        shas = await get_recent_shas(depth=3, start="HEAD~1")
        assert len(shas) == 2  # HEAD~1 and its one ancestor
        result = subprocess.run(
            ["git", "rev-parse", "HEAD~1"], cwd=repo, check=True, capture_output=True,
        )
        assert shas[0] == result.stdout.decode().strip()

    async def test_unresolvable_start_raises_shell_error(self, repo: Path) -> None:
        with pytest.raises(ShellError):
            await get_recent_shas(depth=3, start="origin/does-not-exist")


class TestLooksLikeSha:
    @pytest.mark.parametrize(
        "ref, expected",
        [
            ("abc1234", True),          # 7 hex chars, minimum abbreviation
            ("a" * 40, True),           # full-length SHA
            ("ABC1234", True),          # case-insensitive
            ("main", False),            # branch name
            ("main^", False),           # revision expression
            ("feature/foo", False),     # branch with slash
            ("abc123", False),          # too short (6 hex chars)
            ("a" * 41, False),          # too long
            ("deadbee", True),          # hex-looking but is a real word — still matches (documented limitation)
        ],
    )
    def test_heuristic(self, ref: str, expected: bool) -> None:
        assert looks_like_sha(ref) is expected


class TestDetectProjectPath:
    async def test_detects_gitlab_remote(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_git_repo)
        path = await detect_project_path()
        assert path == "my-group/my-project"

    async def test_github_remote_ignored_by_default(
        self, tmp_github_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_github_repo)
        path = await detect_project_path()
        assert path is None

    async def test_detects_github_remote_when_fallback_enabled(
        self, tmp_github_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_github_repo)
        path = await detect_project_path(allow_github_fallback=True)
        assert path == "DataDog/my-repo"

    async def test_prefers_gitlab_over_github_fallback(
        self, tmp_github_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_github_repo)
        subprocess.run(
            ["git", "remote", "add", "gitlab",
             "git@gitlab.example.com:DataDog/my-repo.git"],
            cwd=tmp_github_repo, check=True, capture_output=True,
        )
        path = await detect_project_path(allow_github_fallback=True)
        # gitlab remote wins over the github fallback
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
