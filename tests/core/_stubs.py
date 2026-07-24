# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""Shared test stubs/fakes for tests/core/.

These are local scaffolding types — not production imports.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import msgspec

from ddgl.cache.cache_config import CacheNS
from ddgl.config import Config

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

TEST_CONFIG = Config(
    gitlab_url="https://gitlab.example.com",
    private_token="tok",
    project_id="grp/proj",
)
PROJECT_ID = "grp/proj"


# ---------------------------------------------------------------------------
# FakeCache stub
# ---------------------------------------------------------------------------


class _FakeNSProxy:
    """Minimal proxy stub mirroring _NamespaceProxy semantics.

    Returns a sub-proxy when the accumulated key is shorter than the namespace
    arity.  Returns the stored value (or None) when the full key is reached.
    """

    def __init__(self, store: dict[tuple, Any], prefix: tuple, arity: int) -> None:
        self._store = store
        self._prefix = prefix
        self._arity = arity

    def _extend(self, raw: Any) -> tuple:
        if isinstance(raw, tuple):
            return self._prefix + raw
        return self._prefix + (raw,)

    def __getitem__(self, key: Any) -> _FakeNSProxy | Any:
        extended = self._extend(key)
        if len(extended) < self._arity:
            return _FakeNSProxy(self._store, extended, self._arity)
        return self._store.get(extended)

    def set(self, key: Any, value: Any, ttl: float = 0.0) -> None:
        stored = msgspec.to_builtins(value) if isinstance(value, msgspec.Struct) else value
        self._store[self._extend(key)] = stored

    def get_many(
        self, ids: Sequence[int | str], cls: type | None = None
    ) -> Sequence[Any]:
        result = []
        for oid in ids:
            key = self._prefix + (oid,)
            val = self._store.get(key)
            if val is None:
                continue
            if cls is None:
                result.append(val)
            else:
                try:
                    result.append(msgspec.convert(val, cls, strict=False))
                except (msgspec.ValidationError, TypeError):
                    pass
        return result


class FakeCache:
    """In-memory cache stub for tests."""

    def __init__(self) -> None:
        self._stores: dict[CacheNS, dict[tuple, Any]] = {}

    def __getitem__(self, ns: CacheNS) -> _FakeNSProxy:
        arity = len(ns.value.key_class._fields)  # type: ignore[attr-defined]
        if ns not in self._stores:
            self._stores[ns] = {}
        return _FakeNSProxy(self._stores[ns], prefix=(), arity=arity)
