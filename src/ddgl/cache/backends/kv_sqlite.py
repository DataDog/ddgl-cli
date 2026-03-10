from __future__ import annotations

from pathlib import Path

from ddgl.cache.backends.base import Key


class KvSqliteBackend:
    """SQLite KV store with per-entry TTL (API response caching).

    Keys are 1-tuples of strings (typically a request hash).
    Values are raw strings (JSON-serialised API responses).
    TTL is required; entries without an expiry are not accepted.
    """

    def __init__(self, path: Path) -> None:
        raise NotImplementedError

    def get(self, key: Key) -> object | None:
        raise NotImplementedError

    def set(self, key: Key, value: object, ttl: float | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass
