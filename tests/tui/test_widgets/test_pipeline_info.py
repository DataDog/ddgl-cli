"""Tests for ddgl/tui/widgets/pipeline_info.py."""
from __future__ import annotations

from rich.text import Text
from .._stubs import make_pipeline

from ddgl.constants import PipelineStatus
from ddgl.tui.widgets.pipeline_info import _render


def test_render_contains_pipeline_id() -> None:
    p = make_pipeline(id=42)
    result = _render(p)
    assert "42" in result.plain


def test_render_contains_ref() -> None:
    p = make_pipeline(ref="my-branch")
    result = _render(p)
    assert "my-branch" in result.plain


def test_render_contains_status() -> None:
    p = make_pipeline(status=PipelineStatus.FAILED)
    result = _render(p)
    assert "failed" in result.plain


def test_render_status_uses_icon() -> None:
    p = make_pipeline(status=PipelineStatus.SUCCESS)
    result = _render(p)
    assert "✓" in result.plain


def test_render_shows_sha_truncated() -> None:
    p = make_pipeline(sha="abcdef1234567890")
    result = _render(p)
    assert "abcdef123456" in result.plain
    assert "7890" not in result.plain


def test_render_sha_missing() -> None:
    p = make_pipeline(sha="")
    result = _render(p)
    assert "—" in result.plain


def test_render_shows_source() -> None:
    p = make_pipeline(source="push")
    result = _render(p)
    assert "push" in result.plain


def test_render_source_missing() -> None:
    p = make_pipeline(source="")
    result = _render(p)
    assert "—" in result.plain


def test_render_shows_url_when_present() -> None:
    p = make_pipeline(web_url="https://gitlab.example.com/proj/-/pipelines/1")
    result = _render(p)
    assert "https://gitlab.example.com" in result.plain


def test_render_omits_url_when_absent() -> None:
    p = make_pipeline(web_url="")
    result = _render(p)
    assert "URL" not in result.plain


def test_render_returns_rich_text() -> None:
    p = make_pipeline()
    assert isinstance(_render(p), Text)
