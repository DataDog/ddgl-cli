from __future__ import annotations

import sqlite3
from pathlib import Path

from ddgl.cache.backends.kv_sqlite import KvSqliteBackend


class TestKvSqliteBackend:
    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        b = KvSqliteBackend(tmp_path / "kv.db")
        assert b.get(("missing",)) is None

    def test_set_and_get(self, tmp_path: Path) -> None:
        b = KvSqliteBackend(tmp_path / "kv.db")
        b.set(("k",), "hello", ttl=60.0)
        assert b.get(("k",)) == "hello"

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

        KvSqliteBackend(path)  # GC runs here
        conn = sqlite3.connect(str(path))
        row_count = conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0]
        conn.close()
        assert row_count == 1  # only the fresh row survives

    def test_close_is_safe(self, tmp_path: Path) -> None:
        b = KvSqliteBackend(tmp_path / "kv.db")
        b.close()  # should not raise

    def test_concurrent_access(self, tmp_path: Path) -> None:
        """Two backends on the same DB file can interleave writes without locking errors."""
        path = tmp_path / "kv.db"
        b1 = KvSqliteBackend(path)
        b2 = KvSqliteBackend(path)

        for i in range(20):
            b1.set((f"a{i}",), f"val_a{i}", ttl=60.0)
            b2.set((f"b{i}",), f"val_b{i}", ttl=60.0)

        assert b1.get(("b5",)) == "val_b5"
        assert b2.get(("a5",)) == "val_a5"
