# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class _SqliteBackend:
    """Shared base for SQLite-backed cache backends.

    Subclasses implement ``_create_tables()`` to set up their schema,
    ``get`` / ``set`` to define access semantics, and optionally ``_gc()``
    to declare which tables to prune on open.  Connection lifecycle,
    ``_gc_table()``, and ``close()`` are handled here.

    Connections are opened per-operation (via ``_connect()``) rather than held
    for the process lifetime, avoiding SQLite lock contention when multiple
    ``ddgl`` instances share the same DB file.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with self._connect() as conn:
            self._create_tables(conn)
            self._gc(conn)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self._path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        raise NotImplementedError  # pragma: no cover

    def _gc(self, conn: sqlite3.Connection) -> None:
        """Delete expired rows from every table that has an ``expires_at`` column."""
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        now = time.time()
        for table in tables:
            cols = {
                row[1]
                for row in conn.execute(
                    f"PRAGMA table_info({table})"  # noqa: S608
                ).fetchall()
            }
            if "expires_at" in cols:
                conn.execute(
                    f"DELETE FROM {table} WHERE expires_at < ?",  # noqa: S608
                    (now,),
                )
        conn.commit()

    def close(self) -> None:
        pass  # connections are per-operation; nothing to close
