from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class _SqliteBackend:
    """Shared base for SQLite-backed cache backends.

    Subclasses implement ``_create_tables()`` to set up their schema,
    ``get`` / ``set`` to define access semantics, and optionally ``_gc()``
    to declare which tables to prune on open.  Connection lifecycle,
    ``_gc_table()``, and ``close()`` are handled here.
    """

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._gc()

    def _create_tables(self) -> None:
        raise NotImplementedError  # pragma: no cover

    def _gc(self) -> None:
        """Delete expired rows from every table that has an ``expires_at`` column."""
        tables = [
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        now = time.time()
        for table in tables:
            cols = {
                row[1]
                for row in self._conn.execute(
                    f"PRAGMA table_info({table})"  # noqa: S608
                ).fetchall()
            }
            if "expires_at" in cols:
                self._conn.execute(
                    f"DELETE FROM {table} WHERE expires_at < ?",  # noqa: S608
                    (now,),
                )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
