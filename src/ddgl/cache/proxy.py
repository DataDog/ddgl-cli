from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ddgl.cache.backends.base import Key, _BulkBackend, _CacheBackend
from ddgl.cache.cache_config import _KeyClass


class _NamespaceProxy:
    """Proxy returned by ``Cache.__getitem__``.

    Supports both flat and nested key access.  Intermediate accesses return a
    new proxy with the prefix accumulated; the final access (when the prefix
    reaches the arity of ``key_class``) calls the backend.

        # flat (1-arity namespace)
        cache[CacheNS.PROJECTS]["git_root"]

        # nested (3-arity namespace)
        cache[CacheNS.OBJECTS]["pipelines"]["proj"]["id"]
        # equivalent to:
        cache[CacheNS.OBJECTS][("pipelines", "proj", "id")]

    Bypass semantics: reads return ``None`` when bypass is active; writes
    always go through.

    All writes require an explicit TTL — use ``.set(key, value, ttl)``.
    """

    def __init__(
        self,
        backend: _CacheBackend,
        bypass: bool,
        key_class: _KeyClass,
        prefix: Key = (),
    ) -> None:
        self._backend = backend
        self._bypass = bypass
        self._key_class = key_class
        self._prefix = prefix

    @property
    def _arity(self) -> int:
        return len(self._key_class._fields)  # type: ignore[attr-defined]

    def _extend(self, raw: str | int | Key) -> Key:
        """Append raw component(s) to the current prefix."""
        if isinstance(raw, tuple):
            return self._prefix + raw
        return self._prefix + (raw,)

    def _full_key(self, key: str | int | Key) -> Key:
        """Extend the prefix with *key* and wrap in the key class NamedTuple."""
        return self._key_class(*self._extend(key))  # type: ignore[return-value, arg-type]

    def __getitem__(self, key: str | int | Key) -> Any:
        extended = self._extend(key)
        if len(extended) < self._arity:
            return _NamespaceProxy(
                self._backend, self._bypass, self._key_class, extended
            )
        if self._bypass:
            return None
        return self._backend.get(self._full_key(key))

    def set(self, key: str | int | Key, value: object, ttl: float) -> None:
        """Write *value* at *key* with an explicit TTL (in seconds)."""
        self._backend.set(self._full_key(key), value, ttl)

    def get_many(
        self, ids: Sequence[int | str], cls: type | None = None
    ) -> Sequence[object]:
        """Bulk-fetch cached values for the given IDs.

        Only available for backends that implement ``_BulkBackend`` (e.g.
        ``StructSqliteBackend``).  The current prefix must contain at least the
        table-name component.

        Generates a single ``WHERE object_id IN (...)`` query instead of N
        individual reads.  Bypass mode returns an empty list.
        """
        if self._bypass:
            return []
        if not self._prefix:
            raise ValueError("get_many() requires at least a table-name prefix")
        if not isinstance(self._backend, _BulkBackend):
            raise NotImplementedError(
                f"{type(self._backend).__name__} does not support bulk reads"
            )
        return self._backend.get_many(self._prefix, ids, cls)
