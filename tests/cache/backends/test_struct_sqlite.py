# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import sqlite3
from pathlib import Path

import msgspec
import pytest

from ddgl.cache.backends.struct_sqlite import (
    StructSqliteBackend,
    _schema_hash,
    _sqlite_type,
)

# ---------------------------------------------------------------------------
# Local stub structs — hermetic, not imported from production code
# ---------------------------------------------------------------------------


class Widget(msgspec.Struct):
    name: str
    count: int
    score: float
    active: bool
    note: str | None = None


class WidgetV2(msgspec.Struct):
    """Simulates a schema-evolved version of Widget (new optional field)."""

    name: str
    count: int
    score: float
    active: bool
    note: str | None = None
    tag: str = ""


class WidgetV3(msgspec.Struct):
    """Simulates a breaking schema change (required field, no default)."""

    name: str
    count: int
    score: float
    active: bool
    required_new: int  # no default — conversion will fail if column is absent
    note: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

KEY = ("widgets", "proj1", 42)
WIDGET = Widget(name="sprocket", count=3, score=9.5, active=True, note="nice")
TTL = 3600.0


def make_backend(tmp_path: Path) -> StructSqliteBackend:
    return StructSqliteBackend(tmp_path / "structs.db")


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


class TestSqliteType:
    def test_int(self) -> None:
        assert _sqlite_type(int) == "INTEGER"

    def test_bool(self) -> None:
        assert _sqlite_type(bool) == "INTEGER"

    def test_float(self) -> None:
        assert _sqlite_type(float) == "REAL"

    def test_str(self) -> None:
        assert _sqlite_type(str) == "TEXT"

    def test_optional_int(self) -> None:
        assert _sqlite_type(int | None) == "INTEGER"

    def test_optional_str(self) -> None:
        assert _sqlite_type(str | None) == "TEXT"


class TestSchemaHash:
    def test_same_class_stable(self) -> None:
        assert _schema_hash(Widget) == _schema_hash(Widget)

    def test_different_classes_differ(self) -> None:
        assert _schema_hash(Widget) != _schema_hash(WidgetV2)


# ---------------------------------------------------------------------------
# StructSqliteBackend behaviour
# ---------------------------------------------------------------------------


class TestStructSqliteBackend:
    def test_get_missing_table_returns_none(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        assert b.get(KEY) is None

    def test_set_and_get_raw_dict(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.set(KEY, WIDGET, ttl=TTL)
        result = b.get(KEY)
        assert isinstance(result, dict)
        assert result["name"] == "sprocket"
        assert result["count"] == 3

    def test_set_and_get_with_cls(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.set(KEY, WIDGET, ttl=TTL)
        result = b.get(KEY, cls=Widget)
        assert isinstance(result, Widget)
        assert result == WIDGET

    def test_expired_entry_returns_none(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.set(KEY, WIDGET, ttl=-1.0)
        assert b.get(KEY) is None

    def test_set_replaces_existing(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.set(KEY, WIDGET, ttl=TTL)
        updated = Widget(name="bolt", count=7, score=1.0, active=False)
        b.set(KEY, updated, ttl=TTL)
        result = b.get(KEY, cls=Widget)
        assert result is not None
        assert result.name == "bolt"

    def test_none_field_roundtrip(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        w = Widget(name="x", count=1, score=0.0, active=True, note=None)
        b.set(KEY, w, ttl=TTL)
        result = b.get(KEY, cls=Widget)
        assert result is not None
        assert result.note is None

    def test_persists_across_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "structs.db"
        b1 = StructSqliteBackend(path)
        b1.set(KEY, WIDGET, ttl=TTL)
        b1.close()

        b2 = StructSqliteBackend(path)
        result = b2.get(KEY, cls=Widget)
        assert result == WIDGET

    def test_gc_prunes_expired_rows_on_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "structs.db"
        b1 = StructSqliteBackend(path)
        b1.set(("widgets", "proj1", 1), WIDGET, ttl=-1.0)
        b1.set(("widgets", "proj1", 2), WIDGET, ttl=TTL)
        b1.close()

        StructSqliteBackend(path)  # GC runs here
        conn = sqlite3.connect(str(path))
        row_count = conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0]
        conn.close()
        assert row_count == 1

    def test_schema_change_drops_stale_table(self, tmp_path: Path) -> None:
        path = tmp_path / "structs.db"
        b1 = StructSqliteBackend(path)
        w1 = Widget(name="a", count=1, score=0.0, active=True)
        b1.set(KEY, w1, ttl=TTL)
        b1.close()

        # Reopen and write with the new schema — should not crash
        b2 = StructSqliteBackend(path)
        w2 = WidgetV2(name="b", count=2, score=0.0, active=False, tag="new")
        b2.set(("widgets", "proj1", 42), w2, ttl=TTL)
        result = b2.get(KEY, cls=WidgetV2)
        assert result is not None
        assert result.tag == "new"

    def test_schema_mismatch_on_read_returns_none(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If the row is missing a required field of the target cls,
        get() returns None and logs a warning."""
        import logging

        path = tmp_path / "structs.db"
        b = StructSqliteBackend(path)
        b.set(KEY, WIDGET, ttl=TTL)

        # Manually corrupt the schema_hash so _ensure_table won't drop the
        # table — we want to exercise the get() safety net, not the set() path.
        conn = sqlite3.connect(str(path))
        conn.execute(
            "UPDATE _schema_versions SET schema_hash=? WHERE table_name=?",
            (_schema_hash(WidgetV3), "widgets"),
        )
        conn.commit()
        conn.close()

        # WidgetV3 has a required field `required_new` not present in the
        # stored Widget row — msgspec.convert must fail.
        with caplog.at_level(logging.WARNING):
            result = b.get(KEY, cls=WidgetV3)
        assert result is None
        assert "cache miss" in caplog.text

    def test_multiple_tables_independent(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        w = Widget(name="gear", count=1, score=0.5, active=True)
        b.set(("widgets", "p", 1), w, ttl=TTL)
        b.set(("gadgets", "p", 1), w, ttl=TTL)
        assert b.get(("widgets", "p", 1), cls=Widget) is not None
        assert b.get(("gadgets", "p", 1), cls=Widget) is not None
        assert b.get(("other", "p", 1)) is None

    def test_close_is_safe(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.close()  # should not raise


class TestStructSqliteBackendGetMany:
    @staticmethod
    def _w(name: str, count: int, active: bool) -> Widget:
        return Widget(name=name, count=count, score=1.0, active=active)

    def _populate(self, b: StructSqliteBackend) -> None:
        b.set(("widgets", "proj1", 1), self._w("a", 1, True), ttl=TTL)
        b.set(("widgets", "proj1", 2), self._w("b", 2, False), ttl=TTL)
        b.set(("widgets", "proj2", 3), self._w("c", 3, True), ttl=TTL)

    def test_get_many_by_ids(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        self._populate(b)
        results = b.get_many(("widgets", "proj1"), [1, 2], cls=Widget)
        assert len(results) == 2
        names = {r.name for r in results}  # type: ignore[union-attr]
        assert names == {"a", "b"}

    def test_get_many_partial_ids(self, tmp_path: Path) -> None:
        """Only requested IDs are returned, even if more exist in the table."""
        b = make_backend(tmp_path)
        self._populate(b)
        results = b.get_many(("widgets", "proj1"), [1], cls=Widget)
        assert len(results) == 1
        assert results[0].name == "a"  # type: ignore[union-attr]

    def test_get_many_cross_project_isolation(self, tmp_path: Path) -> None:
        """project_id filter prevents returning rows from another project."""
        b = make_backend(tmp_path)
        self._populate(b)
        # id=3 exists but belongs to proj2, not proj1
        results = b.get_many(("widgets", "proj1"), [1, 3], cls=Widget)
        assert len(results) == 1
        assert results[0].name == "a"  # type: ignore[union-attr]

    def test_get_many_without_project_filter(self, tmp_path: Path) -> None:
        """Prefix with only table name: no project_id filter applied."""
        b = make_backend(tmp_path)
        self._populate(b)
        results = b.get_many(("widgets",), [1, 3], cls=Widget)
        assert len(results) == 2

    def test_get_many_empty_ids_returns_empty(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        self._populate(b)
        assert b.get_many(("widgets", "proj1"), []) == []

    def test_get_many_missing_table_returns_empty(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        assert b.get_many(("nonexistent",), [1, 2]) == []

    def test_get_many_excludes_expired(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        old = Widget(name="old", count=0, score=0.0, active=False)
        fresh = Widget(name="fresh", count=1, score=1.0, active=True)
        b.set(("widgets", "proj1", 1), old, ttl=-1.0)
        b.set(("widgets", "proj1", 2), fresh, ttl=TTL)
        results = b.get_many(("widgets", "proj1"), [1, 2], cls=Widget)
        assert len(results) == 1
        assert results[0].name == "fresh"  # type: ignore[union-attr]

    def test_get_many_raw_dict_without_cls(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.set(KEY, WIDGET, ttl=TTL)
        results = b.get_many(("widgets",), [42])
        assert len(results) == 1
        assert isinstance(results[0], dict)
        assert results[0]["name"] == "sprocket"  # type: ignore[index]


class TestStructSqliteConcurrentAccess:
    def test_concurrent_access(self, tmp_path: Path) -> None:
        """Two backends on the same DB file can interleave writes without locking errors."""
        path = tmp_path / "structs.db"
        b1 = StructSqliteBackend(path)
        b2 = StructSqliteBackend(path)

        for i in range(20):
            w = Widget(name=f"w{i}", count=i, score=float(i), active=True)
            b1.set(("widgets", "proj1", i), w, ttl=TTL)
            b2.set(("widgets", "proj2", i), w, ttl=TTL)

        r1 = b1.get(("widgets", "proj2", 5), cls=Widget)
        r2 = b2.get(("widgets", "proj1", 5), cls=Widget)
        assert r1 is not None and r1.name == "w5"
        assert r2 is not None and r2.name == "w5"
