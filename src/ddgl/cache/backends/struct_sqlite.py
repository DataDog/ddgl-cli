from __future__ import annotations

import hashlib
import logging
import time
import types
from typing import Any, Union, get_args, get_origin

import msgspec
import msgspec.structs

from ddgl.cache.backends.base import Key
from ddgl.cache.backends.sqlite_base import _SqliteBackend

logger = logging.getLogger(__name__)


def _sqlite_type(field_type: Any) -> str:
    """Map a Python type annotation to a SQLite column affinity."""
    origin = get_origin(field_type)
    if origin is Union or isinstance(field_type, types.UnionType):
        non_none = [a for a in get_args(field_type) if a is not type(None)]
        return _sqlite_type(non_none[0]) if non_none else "TEXT"
    if field_type in (int, bool):
        return "INTEGER"
    if field_type is float:
        return "REAL"
    return "TEXT"


def _schema_hash(cls: type) -> str:
    """Stable hash of a msgspec.Struct field layout."""
    fields = msgspec.structs.fields(cls)
    descriptor = ";".join(f"{f.name}:{f.type}" for f in fields)
    return hashlib.sha256(descriptor.encode()).hexdigest()


class StructSqliteBackend(_SqliteBackend):
    """SQLite backend with one table per domain object type.

    Keys are 3-tuples: ``(table_name, project_id, object_id)``.
    Values must be ``msgspec.Struct`` instances.
    TTL is required.

    Tables are created lazily on first write.  If the struct schema changes
    between versions, the stale table is dropped and recreated automatically.
    Schema mismatches detected on read are logged and treated as cache misses.
    """

    def _create_tables(self) -> None:
        # This actually only creates the _schema_versions table, all others are created lazily on first write.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS _schema_versions (  table_name TEXT PRIMARY KEY,  schema_hash TEXT NOT NULL)"
        )
        self._conn.commit()

    def _ensure_table(self, table_name: str, cls: type) -> None:
        """Create (or recreate on schema change) the table for *cls*."""
        current_hash = _schema_hash(cls)
        row = self._conn.execute(
            "SELECT schema_hash FROM _schema_versions WHERE table_name = ?",
            (table_name,),
        ).fetchone()

        if row is not None and row[0] == current_hash:
            return  # table exists and schema is current

        if row is not None:
            logger.warning(
                "Schema change detected for table %r — dropping stale cache",
                table_name,
            )
            self._conn.execute(f"DROP TABLE IF EXISTS {table_name}")  # noqa: S608

        fields = msgspec.structs.fields(cls)
        col_defs = ", ".join(f"{f.name} {_sqlite_type(f.type)}" for f in fields)
        self._conn.execute(
            f"CREATE TABLE {table_name} ("  # noqa: S608
            f"  project_id TEXT NOT NULL,"
            f"  object_id TEXT NOT NULL,"
            f"  {col_defs},"
            f"  expires_at REAL NOT NULL,"
            f"  PRIMARY KEY (project_id, object_id)"
            f")"
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO _schema_versions (table_name, schema_hash) VALUES (?, ?)",
            (table_name, current_hash),
        )
        self._conn.commit()

    def get(self, key: Key, cls: type | None = None) -> object | None:  # type: ignore[override]
        table_name, project_id, object_id = key

        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return None

        cursor = self._conn.execute(
            f"SELECT * FROM {table_name}"  # noqa: S608
            f" WHERE project_id=? AND object_id=? AND expires_at>?",
            (str(project_id), str(object_id), time.time()),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        col_names = [desc[0] for desc in cursor.description]
        row_dict = {
            col: val for col, val in zip(col_names, row) if col not in ("project_id", "object_id", "expires_at")
        }

        if cls is None:
            return row_dict

        try:
            return msgspec.convert(row_dict, cls, strict=False)
        except (msgspec.ValidationError, TypeError):
            logger.warning(
                "Failed to deserialize row from %r into %s — treating as cache miss",
                table_name,
                cls.__name__,
            )
            return None

    def set(self, key: Key, value: object, ttl: float | None = None) -> None:
        if ttl is None:
            raise ValueError("StructSqliteBackend requires a TTL")

        table_name, project_id, object_id = key
        struct_cls = type(value)
        self._ensure_table(table_name, struct_cls)

        fields = msgspec.structs.fields(struct_cls)
        builtins = msgspec.to_builtins(value)
        col_names = [f.name for f in fields]
        values = [builtins[f.name] for f in fields]

        placeholders = ", ".join("?" * len(col_names))
        col_list = ", ".join(col_names)
        self._conn.execute(
            f"INSERT OR REPLACE INTO {table_name}"  # noqa: S608
            f" (project_id, object_id, {col_list}, expires_at)"
            f" VALUES (?, ?, {placeholders}, ?)",
            [str(project_id), str(object_id), *values, time.time() + ttl],
        )
        self._conn.commit()
