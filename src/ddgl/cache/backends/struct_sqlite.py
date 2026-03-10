from __future__ import annotations

from ddgl.cache.backends.base import Key
from ddgl.cache.backends.sqlite_base import _SqliteBackend


class StructSqliteBackend(_SqliteBackend):
    """SQLite backend with one table per domain object type.

    Keys are 3-tuples: ``(table_name, project_id, object_id)``.
    Values are ``Pipeline`` or ``Job`` instances.
    TTL is required.
    """

    def _create_tables(self) -> None:
        raise NotImplementedError

    def get(self, key: Key) -> object | None:
        raise NotImplementedError

    def set(self, key: Key, value: object, ttl: float | None = None) -> None:
        raise NotImplementedError
