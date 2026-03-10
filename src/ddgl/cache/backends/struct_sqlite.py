from __future__ import annotations

from pathlib import Path

from ddgl.cache.backends.base import Key


class StructSqliteBackend:
    """SQLite backend with one table per domain object type.

    Keys are 3-tuples: ``(table_name, project_id, object_id)``.
    Values are ``Pipeline`` or ``Job`` instances.
    TTL is required.
    """

    def __init__(self, path: Path) -> None:
        raise NotImplementedError

    def get(self, key: Key) -> object | None:
        raise NotImplementedError

    def set(self, key: Key, value: object, ttl: float | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass
