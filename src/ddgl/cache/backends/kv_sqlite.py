from __future__ import annotations

import logging
import time

from ddgl.cache.backends.base import Key
from ddgl.cache.backends.sqlite_base import _SqliteBackend

logger = logging.getLogger(__name__)


class KvSqliteBackend(_SqliteBackend):
    """SQLite KV store with per-entry TTL (API response caching).

    Keys are 1-tuples of strings (typically a request hash).
    Values are raw strings (JSON-serialised API responses).
    TTL is required on every write.
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
        if row:
            logger.debug("get(%r) hit", key[0])
            return row[0]
        logger.debug("get(%r) miss", key[0])
        return None

    def set(self, key: Key, value: object, ttl: float) -> None:
        logger.debug("set(%r) ttl=%.0fs", key[0], ttl)
        self._conn.execute(
            "INSERT OR REPLACE INTO kv (key, value, expires_at) VALUES (?, ?, ?)",
            (str(key[0]), str(value), time.time() + ttl),
        )
        self._conn.commit()
