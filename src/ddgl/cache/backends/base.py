from __future__ import annotations

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
    ``CacheNS.key_arity``).  TTL is an optional concern surfaced in ``set``
    and enforced internally by each backend.
    """

    def get(self, key: Key) -> object | None: ...
    def set(self, key: Key, value: object, ttl: float | None = None) -> None: ...
    def close(self) -> None: ...
