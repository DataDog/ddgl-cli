# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import logging
import sqlite3
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

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kv ("
            "  key        TEXT PRIMARY KEY,"
            "  value      TEXT NOT NULL,"
            "  expires_at REAL NOT NULL"
            ")"
        )
        conn.commit()

    def get(self, key: Key) -> object | None:
        k = str(key)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM kv WHERE key = ? AND expires_at > ?",
                (k, time.time()),
            ).fetchone()
        if row:
            logger.debug("get(%r) hit", k)
            return row[0]
        logger.debug("get(%r) miss", k)
        return None

    def set(self, key: Key, value: object, ttl: float) -> None:
        k = str(key)
        logger.debug("set(%r) ttl=%.0fs", k, ttl)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv (key, value, expires_at) VALUES (?, ?, ?)",
                (k, str(value), time.time() + ttl),
            )
            conn.commit()
