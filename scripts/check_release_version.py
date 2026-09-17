#!/usr/bin/env python3
# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Check that built distributions carry the version a release tag implies.

    python scripts/check_release_version.py --tag v0.4.0 dist/

Exits non-zero, listing every mismatch, when they do not. A PyPI upload cannot
be replaced once accepted, so this runs before publishing rather than after.
"""

from __future__ import annotations

import argparse
import email
import sys
import zipfile
from pathlib import Path


def version_from_tag(tag: str) -> str:
    """Return the PEP 440 version that a release tag refers to."""
    return tag.strip().removeprefix("v")


def wheel_metadata_version(wheel: Path) -> str | None:
    """Return the version recorded in a wheel's dist-info METADATA.

    This is the value `importlib.metadata` resolves once installed, and so what
    `ddgl --version` reports. Returns None when the wheel has no METADATA.
    """
    with zipfile.ZipFile(wheel) as archive:
        entries = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if not entries:
            return None
        text = archive.read(entries[0]).decode("utf-8", errors="replace")
    return email.message_from_string(text)["Version"]


def check(dist_dir: Path, tag: str) -> list[str]:
    """Return every reason the distributions in dist_dir must not be published."""
    if not dist_dir.is_dir():
        return [f"{dist_dir} is not a directory"]

    expected = version_from_tag(tag)
    problems: list[str] = []

    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1:
        problems.append(
            f"expected exactly one wheel in {dist_dir}, found {len(wheels)}"
        )
    if len(sdists) != 1:
        problems.append(
            f"expected exactly one sdist in {dist_dir}, found {len(sdists)}"
        )

    for wheel in wheels:
        # ddgl-0.4.0-py3-none-any.whl -> 0.4.0
        filename_version = wheel.name.split("-")[1]
        if filename_version != expected:
            problems.append(
                f"wheel {wheel.name} is version {filename_version}, "
                f"expected {expected} from tag {tag}"
            )
        metadata_version = wheel_metadata_version(wheel)
        if metadata_version is None:
            problems.append(f"wheel {wheel.name} has no dist-info METADATA")
        elif metadata_version != expected:
            problems.append(
                f"wheel {wheel.name} METADATA records version {metadata_version}, "
                f"expected {expected} from tag {tag}"
            )

    for sdist in sdists:
        # ddgl-0.4.0.tar.gz -> 0.4.0
        filename_version = sdist.name.removesuffix(".tar.gz").split("-", 1)[1]
        if filename_version != expected:
            problems.append(
                f"sdist {sdist.name} is version {filename_version}, "
                f"expected {expected} from tag {tag}"
            )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check built distributions against a release tag."
    )
    parser.add_argument(
        "dist", type=Path, help="directory holding the built distributions"
    )
    parser.add_argument("--tag", required=True, help="release tag, for example v0.4.0")
    args = parser.parse_args(argv)

    problems = check(args.dist, args.tag)
    if problems:
        print(f"Refusing to publish {args.tag}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"{args.dist} matches tag {args.tag} (version {version_from_tag(args.tag)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
