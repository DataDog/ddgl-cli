# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

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
    """Optional extension for backends that support bulk reads by ID.

    ``key_prefix`` is a partial key tuple (at minimum the table name).
    ``ids`` are the object_id values to fetch.  The backend generates a
    single ``WHERE object_id IN (...)`` query rather than N individual reads.

    Implementations of ``_CacheBackend`` that also implement ``_BulkBackend``
    advertise bulk-read support; those that don't will cause
    ``_NamespaceProxy.get_many`` to raise ``NotImplementedError``.
    """

    def get_many(
        self, key_prefix: Key, ids: Sequence[int | str], cls: type | None = None
    ) -> Sequence[object]: ...
