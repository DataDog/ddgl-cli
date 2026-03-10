from __future__ import annotations

from ddgl.cache.backends.base import Key, _CacheBackend
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

    def __getitem__(self, key: str | int | Key) -> _NamespaceProxy | object | None:
        full_key = self._extend(key)
        if len(full_key) < self._arity:
            return _NamespaceProxy(
                self._backend, self._bypass, self._key_class, full_key
            )
        if self._bypass:
            return None
        return self._backend.get(full_key)

    def set(self, key: str | int | Key, value: object, ttl: float) -> None:
        """Write *value* at *key* with an explicit TTL (in seconds)."""
        self._backend.set(self._extend(key), value, ttl)
