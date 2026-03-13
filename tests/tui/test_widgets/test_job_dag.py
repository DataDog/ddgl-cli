"""Tests for ddgl/tui/widgets/job_dag.py — pure-function tests only."""
from __future__ import annotations

from ddgl.tui.widgets.job_dag import build_dag

from .._stubs import make_job


def test_no_dependencies() -> None:
    current = make_job(id=1, name="build", needs=())
    others = [make_job(id=2, name="test", needs=())]
    dag = build_dag(current, [current, *others])
    assert dag.upstream == []
    assert dag.downstream == []


def test_upstream_from_needs() -> None:
    setup = make_job(id=1, name="setup", needs=())
    lint = make_job(id=2, name="lint", needs=())
    current = make_job(id=3, name="build", needs=("setup", "lint"))
    dag = build_dag(current, [setup, lint, current])
    assert [j.name for j in dag.upstream] == ["setup", "lint"]
    assert dag.downstream == []


def test_downstream_from_others_needs() -> None:
    current = make_job(id=1, name="build", needs=())
    deploy = make_job(id=2, name="deploy", needs=("build",))
    test = make_job(id=3, name="test", needs=("build",))
    dag = build_dag(current, [current, deploy, test])
    assert dag.upstream == []
    assert sorted(j.name for j in dag.downstream) == ["deploy", "test"]


def test_both_directions() -> None:
    setup = make_job(id=1, name="setup", needs=())
    current = make_job(id=2, name="build", needs=("setup",))
    deploy = make_job(id=3, name="deploy", needs=("build",))
    dag = build_dag(current, [setup, current, deploy])
    assert [j.name for j in dag.upstream] == ["setup"]
    assert [j.name for j in dag.downstream] == ["deploy"]


def test_missing_needs_name_ignored() -> None:
    current = make_job(id=1, name="build", needs=("nonexistent",))
    dag = build_dag(current, [current])
    assert dag.upstream == []


def test_current_not_in_own_downstream() -> None:
    """A job that needs itself (pathological) should not appear in downstream."""
    current = make_job(id=1, name="build", needs=("build",))
    dag = build_dag(current, [current])
    assert dag.downstream == []
