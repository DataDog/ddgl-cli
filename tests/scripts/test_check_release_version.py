# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from check_release_version import check, version_from_tag


def make_wheel(
    dist: Path,
    *,
    version: str = "0.4.0",
    metadata_version: str | None = None,
    name: str = "ddgl",
) -> Path:
    """Write a wheel containing only the dist-info METADATA the checker reads."""
    path = dist / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: {name}\nVersion: {metadata_version or version}\n",
        )
    return path


def make_sdist(dist: Path, *, version: str = "0.4.0", name: str = "ddgl") -> Path:
    path = dist / f"{name}-{version}.tar.gz"
    path.write_bytes(b"")
    return path


class TestVersionFromTag:
    def test_strips_leading_v(self):
        assert version_from_tag("v0.4.0") == "0.4.0"

    def test_accepts_a_tag_without_the_prefix(self):
        assert version_from_tag("0.4.0") == "0.4.0"

    def test_strips_surrounding_whitespace(self):
        assert version_from_tag("  v0.4.0\n") == "0.4.0"

    def test_keeps_a_prerelease_suffix(self):
        assert version_from_tag("v0.4.0rc1") == "0.4.0rc1"


class TestCheck:
    def test_matching_wheel_and_sdist_pass(self, tmp_path):
        make_wheel(tmp_path)
        make_sdist(tmp_path)
        assert check(tmp_path, "v0.4.0") == []

    def test_prerelease_passes(self, tmp_path):
        make_wheel(tmp_path, version="0.4.0rc1")
        make_sdist(tmp_path, version="0.4.0rc1")
        assert check(tmp_path, "v0.4.0rc1") == []

    def test_tag_not_matching_the_build_is_reported(self, tmp_path):
        make_wheel(tmp_path, version="0.4.0")
        make_sdist(tmp_path, version="0.4.0")
        problems = check(tmp_path, "v0.5.0")
        assert problems
        assert any("0.5.0" in p and "0.4.0" in p for p in problems)

    def test_dev_version_from_an_untagged_checkout_is_reported(self, tmp_path):
        # hatch-vcs produces this shape when the tag is not on HEAD, and it
        # would otherwise be published as a real release.
        make_wheel(tmp_path, version="0.3.1.dev5+g1234abc")
        make_sdist(tmp_path, version="0.3.1.dev5+g1234abc")
        problems = check(tmp_path, "v0.4.0")
        assert problems
        assert any("dev5" in p for p in problems)

    def test_missing_sdist_is_reported(self, tmp_path):
        make_wheel(tmp_path)
        problems = check(tmp_path, "v0.4.0")
        assert problems
        assert any("sdist" in p.lower() for p in problems)

    def test_missing_wheel_is_reported(self, tmp_path):
        make_sdist(tmp_path)
        problems = check(tmp_path, "v0.4.0")
        assert problems
        assert any("wheel" in p.lower() for p in problems)

    def test_empty_directory_is_reported(self, tmp_path):
        problems = check(tmp_path, "v0.4.0")
        assert len(problems) == 2

    def test_more_than_one_wheel_is_reported(self, tmp_path):
        make_wheel(tmp_path, version="0.4.0")
        make_wheel(tmp_path, version="0.4.1")
        make_sdist(tmp_path)
        problems = check(tmp_path, "v0.4.0")
        assert problems
        assert any("wheel" in p.lower() for p in problems)

    def test_metadata_disagreeing_with_the_filename_is_reported(self, tmp_path):
        # A filename says 0.4.0 while the packaged metadata says otherwise;
        # importlib.metadata, and so `ddgl --version`, reports the metadata.
        make_wheel(tmp_path, version="0.4.0", metadata_version="0.3.9")
        make_sdist(tmp_path)
        problems = check(tmp_path, "v0.4.0")
        assert problems
        assert any("0.3.9" in p for p in problems)

    def test_wheel_without_metadata_is_reported(self, tmp_path):
        path = tmp_path / "ddgl-0.4.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("ddgl/__init__.py", "")
        make_sdist(tmp_path)
        problems = check(tmp_path, "v0.4.0")
        assert problems
        assert any("METADATA" in p for p in problems)

    def test_missing_directory_is_reported(self, tmp_path):
        problems = check(tmp_path / "nope", "v0.4.0")
        assert problems
        assert any("nope" in p for p in problems)


class TestMain:
    def test_exits_zero_when_the_build_matches(self, tmp_path, capsys):
        from check_release_version import main

        make_wheel(tmp_path)
        make_sdist(tmp_path)
        assert main(["--tag", "v0.4.0", str(tmp_path)]) == 0

    def test_exits_nonzero_and_reports_every_problem(self, tmp_path, capsys):
        from check_release_version import main

        assert main(["--tag", "v0.4.0", str(tmp_path)]) != 0
        err = capsys.readouterr().err
        assert "wheel" in err.lower()
        assert "sdist" in err.lower()

    def test_tag_is_required(self, tmp_path):
        from check_release_version import main

        with pytest.raises(SystemExit):
            main([str(tmp_path)])
