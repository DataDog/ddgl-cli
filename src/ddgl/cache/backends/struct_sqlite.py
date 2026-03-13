from __future__ import annotations

import hashlib
import json
import logging
import time
import types
from collections.abc import Sequence
from pathlib import Path
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
    fields = msgspec.structs.fields(cls)  # type: ignore[arg-type]
    descriptor = ";".join(f"{f.name}:{f.type}" for f in fields)
    return hashlib.sha256(descriptor.encode()).hexdigest()


class StructSqliteBackend(_SqliteBackend):
    """SQLite backend with one table per domain object type.

    Keys are 3-tuples: ``(table_name, project_id, object_id)``.
    Values must be ``msgspec.Struct`` instances.
    TTL is required on every write.

    Tables are created lazily on first write.  If the struct schema changes
    between versions, the stale table is dropped and recreated automatically.
    Schema mismatches detected on read are logged and treated as cache misses.

    ``_known_hashes`` caches verified schema hashes in memory so that
    ``_ensure_table`` only hits the DB on the first write per table per session.
    """

    def __init__(self, path: Path) -> None:
        self._known_hashes: dict[str, str] = {}
        super().__init__(path)

    def _create_tables(self) -> None:
        # Only the schema-version registry is created eagerly; data tables are
        # created lazily via _ensure_table() on the first write.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS _schema_versions ("
            "  table_name TEXT PRIMARY KEY,"
            "  schema_hash TEXT NOT NULL"
            ")"
        )
        self._conn.commit()
        logger.debug("StructSqliteBackend initialised")

    def _ensure_table(self, table_name: str, cls: type) -> None:
        """Create (or recreate on schema change) the table for *cls*."""
        current_hash = _schema_hash(cls)

        # Fast path: hash already verified this session.
        if self._known_hashes.get(table_name) == current_hash:
            logger.debug("_ensure_table(%r) — in-memory cache hit", table_name)
            return

        row = self._conn.execute(
            "SELECT schema_hash FROM _schema_versions WHERE table_name = ?",
            (table_name,),
        ).fetchone()

        if row is not None and row[0] == current_hash:
            logger.debug("_ensure_table(%r) — DB hash matches", table_name)
            self._known_hashes[table_name] = current_hash
            return

        if row is not None:
            logger.info(
                "Schema change detected for table %r — dropping stale cache",
                table_name,
            )
            self._conn.execute(f"DROP TABLE IF EXISTS {table_name}")  # noqa: S608

        fields = msgspec.structs.fields(cls)  # type: ignore[arg-type]
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
            "INSERT OR REPLACE INTO _schema_versions (table_name, schema_hash)"
            " VALUES (?, ?)",
            (table_name, current_hash),
        )
        self._conn.commit()
        self._known_hashes[table_name] = current_hash
        logger.debug("_ensure_table(%r) — table created/recreated", table_name)

    _EXCLUDED_COLS = frozenset({"project_id", "object_id", "expires_at"})

    def _table_exists(self, table_name: str) -> bool:
        return bool(
            self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
        )

    def _row_to_dict(
        self, col_names: list[str], row: tuple[Any, ...]
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for col, val in zip(col_names, row):
            if col in self._EXCLUDED_COLS:
                continue
            # JSON-encoded collection fields are stored as TEXT; decode them
            # back so msgspec.convert can turn them into the right types.
            if isinstance(val, str) and val and val[0] in ("[", "{"):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    pass
            result[col] = val
        return result

    def _deserialize(
        self, row_dict: dict[str, object], cls: type, table_name: str
    ) -> object | None:
        try:
            return msgspec.convert(row_dict, cls, strict=False)
        except (msgspec.ValidationError, TypeError):
            logger.warning(
                "Failed to deserialize row from %r into %s — treating as cache miss",
                table_name,
                cls.__name__,
            )
            return None

    def get(self, key: Key, cls: type | None = None) -> object | None:  # type: ignore[override]
        if len(key) != 3:
            raise ValueError(
                f"StructSqliteBackend requires a 3-tuple key, got {len(key)}"
            )
        table_name = str(key[0])
        project_id = str(key[1])
        object_id = str(key[2])

        if not self._table_exists(table_name):
            logger.debug("get(%r) — table does not exist", table_name)
            return None

        cursor = self._conn.execute(
            f"SELECT * FROM {table_name}"  # noqa: S608
            f" WHERE project_id=? AND object_id=? AND expires_at>?",
            (project_id, object_id, time.time()),
        )
        row = cursor.fetchone()
        if row is None:
            logger.debug("get(%r/%s/%s) miss", table_name, project_id, object_id)
            return None

        col_names = [desc[0] for desc in cursor.description]
        row_dict = self._row_to_dict(col_names, row)

        if cls is None:
            logger.debug("get(%r/%s/%s) hit (dict)", table_name, project_id, object_id)
            return row_dict

        result = self._deserialize(row_dict, cls, table_name)
        if result is not None:
            logger.debug("get(%r/%s/%s) hit (%s)", table_name, project_id, object_id, cls.__name__)  # noqa: E501
        return result

    def set(self, key: Key, value: object, ttl: float) -> None:
        if len(key) != 3:
            raise ValueError(
                f"StructSqliteBackend requires a 3-tuple key, got {len(key)}"
            )
        if not isinstance(value, msgspec.Struct):
            raise TypeError(f"Expected msgspec.Struct, got {type(value)}")
        table_name = str(key[0])
        project_id = str(key[1])
        object_id = str(key[2])

        struct_cls = type(value)
        self._ensure_table(table_name, struct_cls)

        fields = msgspec.structs.fields(struct_cls)  # type: ignore[arg-type]
        builtins = msgspec.to_builtins(value)
        col_names = [f.name for f in fields]
        values = [
            json.dumps(v) if isinstance(v, (list, tuple, dict)) else v
            for v in (builtins[f.name] for f in fields)
        ]

        placeholders = ", ".join("?" * len(col_names))
        col_list = ", ".join(col_names)
        self._conn.execute(
            f"INSERT OR REPLACE INTO {table_name}"  # noqa: S608
            f" (project_id, object_id, {col_list}, expires_at)"
            f" VALUES (?, ?, {placeholders}, ?)",
            [project_id, object_id, *values, time.time() + ttl],
        )
        self._conn.commit()
        logger.debug("set(%r/%s/%s) ttl=%.0fs", table_name, project_id, object_id, ttl)

    def get_many(
        self,
        key_prefix: Key,
        ids: Sequence[int | str],
        cls: type | None = None,
    ) -> Sequence[object]:
        """Bulk-fetch rows by ID list using a single SQL query.

        ``key_prefix[0]`` is the table name (required).
        ``key_prefix[1]`` is the project_id (optional, adds a WHERE filter).
        ``ids`` are the object_id values to fetch.

        Generates: ``SELECT * FROM {table} WHERE object_id IN (?, ...)
        [AND project_id = ?] AND expires_at > ?``

        Rows that fail deserialisation into *cls* are logged and skipped.
        """
        if not ids:
            return []

        table_name = str(key_prefix[0])

        if not self._table_exists(table_name):
            logger.debug("get_many(%r) — table does not exist", table_name)
            return []

        placeholders = ", ".join("?" * len(ids))
        conditions: list[str] = [f"object_id IN ({placeholders})", "expires_at > ?"]
        params: list[object] = [str(i) for i in ids]
        params.append(time.time())

        if len(key_prefix) > 1:
            conditions.insert(0, "project_id = ?")
            params.insert(0, str(key_prefix[1]))

        where = " AND ".join(conditions)
        cursor = self._conn.execute(
            f"SELECT * FROM {table_name} WHERE {where}",  # noqa: S608
            params,
        )
        col_names = [desc[0] for desc in cursor.description]

        results: list[object] = []
        for row in cursor.fetchall():
            row_dict = self._row_to_dict(col_names, row)
            if cls is None:
                results.append(row_dict)
            else:
                result = self._deserialize(row_dict, cls, table_name)
                if result is not None:
                    results.append(result)

        logger.debug(
            "get_many(%r, %d ids) returned %d rows", table_name, len(ids), len(results)
        )
        return results
