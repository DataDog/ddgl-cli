from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

# Tuple used as a cache key.  Single-level stores use 1-tuples; nested
# stores (e.g. StructSqliteBackend) use longer tuples.
Key = tuple[str | int, ...]


@runtime_checkable
class _CacheBackend(Protocol):
    """Structural protocol shared by all cache backends.

    All access goes through a single ``get`` / ``set`` pair that accepts a
    ``Key`` tuple.  Backends interpret the tuple however suits their storage
    model; the caller must use the correct arity for each namespace (see
    ``CacheNS.key_arity``).  TTL is required on every ``set`` call.
    """

    def get(self, key: Key) -> object | None: ...
    def set(self, key: Key, value: object, ttl: float) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class _BulkBackend(Protocol):
    """Optional extension for backends that support multi-row reads.

    ``key_prefix`` is a partial key tuple.  The backend filters rows that
    match all provided prefix components and returns every matching row.

    Implementations of ``_CacheBackend`` that also implement ``_BulkBackend``
    advertise bulk-read support; those that don't will cause
    ``_NamespaceProxy.get_all`` to raise ``NotImplementedError``.
    """

    def get_many(
        self, key_prefix: Key, cls: type | None = None
    ) -> Sequence[object]: ...
