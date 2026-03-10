from __future__ import annotations

from pathlib import Path

import pytest

from ddgl.cache.backends.kv_sqlite import KvSqliteBackend


class TestKvSqliteBackend:
    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        b = KvSqliteBackend(tmp_path / "kv.db")
        assert b.get(("missing",)) is None

    def test_set_and_get(self, tmp_path: Path) -> None:
        b = KvSqliteBackend(tmp_path / "kv.db")
        b.set(("k",), "hello", ttl=60.0)
        assert b.get(("k",)) == "hello"

    def test_set_without_ttl_raises(self, tmp_path: Path) -> None:
        b = KvSqliteBackend(tmp_path / "kv.db")
        with pytest.raises(ValueError, match="TTL"):
            b.set(("k",), "v")

    def test_expired_entry_returns_none(self, tmp_path: Path) -> None:
        b = KvSqliteBackend(tmp_path / "kv.db")
        b.set(("k",), "v", ttl=-1.0)  # already expired
        assert b.get(("k",)) is None

    def test_set_replaces_existing(self, tmp_path: Path) -> None:
        b = KvSqliteBackend(tmp_path / "kv.db")
        b.set(("k",), "first", ttl=60.0)
        b.set(("k",), "second", ttl=60.0)
        assert b.get(("k",)) == "second"

    def test_persists_across_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "kv.db"
        b1 = KvSqliteBackend(path)
        b1.set(("k",), "persisted", ttl=3600.0)
        b1.close()

        b2 = KvSqliteBackend(path)
        assert b2.get(("k",)) == "persisted"

    def test_gc_prunes_expired_rows_on_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "kv.db"
        b1 = KvSqliteBackend(path)
        b1.set(("expired",), "old", ttl=-1.0)
        b1.set(("fresh",), "new", ttl=3600.0)
        b1.close()

        b2 = KvSqliteBackend(path)  # GC runs here
        row_count = b2._conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0]
        assert row_count == 1  # only the fresh row survives

    def test_close_is_safe(self, tmp_path: Path) -> None:
        b = KvSqliteBackend(tmp_path / "kv.db")
        b.close()  # should not raise
