from __future__ import annotations

from pathlib import Path

import pytest

from ddgl.git import get_current_branch, get_remote_url, parse_project_path


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
