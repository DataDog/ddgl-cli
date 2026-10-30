# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Tests for src/ddgl/render/_console.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from ddgl.render._console import EnvPager

STYLED = "\x1b[1;36m146447932\x1b[0m\n"


def test_env_pager_uses_pager_and_ignores_manpager(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "out.txt"
    monkeypatch.setenv("MANPAGER", f"echo MANPAGER_USED > {out}")
    monkeypatch.setenv("PAGER", f"cat > {out}")

    EnvPager().show(STYLED)

    assert out.read_text() == STYLED


@pytest.mark.parametrize(("less", "expected"), [(None, "FRX"), ("-S", "-S")])
def test_env_pager_defaults_less_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, less: str | None, expected: str
) -> None:
    out = tmp_path / "out.txt"
    monkeypatch.setenv("PAGER", f'cat > /dev/null; printf %s "$LESS" > {out}')
    if less is None:
        monkeypatch.delenv("LESS", raising=False)
    else:
        monkeypatch.setenv("LESS", less)

    EnvPager().show(STYLED)

    assert out.read_text() == expected


def test_env_pager_falls_back_to_less(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.delenv("PAGER", raising=False)
    monkeypatch.setenv("MANPAGER", "col -bx")
    monkeypatch.setattr("pydoc.pipepager", lambda text, cmd: calls.append((text, cmd)))

    EnvPager().show(STYLED)

    assert calls == [(STYLED, "less")]
