from __future__ import annotations

import time

from ddgl.cache.backends.base import Key
from ddgl.cache.backends.sqlite_base import _SqliteBackend


class KvSqliteBackend(_SqliteBackend):
    """SQLite KV store with per-entry TTL (API response caching).

    Keys are 1-tuples of strings (typically a request hash).
    Values are raw strings (JSON-serialised API responses).
    TTL is required; entries without an expiry are not accepted.
    """

    def _create_tables(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kv ("
            "  key        TEXT PRIMARY KEY,"
            "  value      TEXT NOT NULL,"
            "  expires_at REAL NOT NULL"
            ")"
        )
        self._conn.commit()

    def get(self, key: Key) -> object | None:
        row = self._conn.execute(
            "SELECT value FROM kv WHERE key = ? AND expires_at > ?",
            (str(key[0]), time.time()),
        ).fetchone()
        return row[0] if row else None

    def set(self, key: Key, value: object, ttl: float | None = None) -> None:
        if ttl is None:
            raise ValueError("KvSqliteBackend requires a TTL")
        self._conn.execute(
            "INSERT OR REPLACE INTO kv (key, value, expires_at) VALUES (?, ?, ?)",
            (str(key[0]), str(value), time.time() + ttl),
        )
        self._conn.commit()
