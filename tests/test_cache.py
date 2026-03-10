from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import NamedTuple

import pytest

from ddgl.cache import Cache
from ddgl.cache.backends.base import Key
from ddgl.cache.proxy import _NamespaceProxy

# ---------------------------------------------------------------------------
# Local key types and namespace enum — fully hermetic, no production imports.
# ---------------------------------------------------------------------------


class FlatKey(NamedTuple):
    key: str


class NestedKey(NamedTuple):
    table: str
    project: str
    obj_id: int


class _MockNSConfig(NamedTuple):
    filename: str
    key_class: type


class MockNS(Enum):
    FLAT = _MockNSConfig("flat.json", FlatKey)
    NESTED = _MockNSConfig("nested.db", NestedKey)


# ---------------------------------------------------------------------------
# Shared mock backend
# ---------------------------------------------------------------------------


class MockBackend:
    """In-memory backend that records every call for assertion."""

    def __init__(self) -> None:
        self.store: dict[Key, object] = {}
        self.set_calls: list[tuple[Key, object, float | None]] = []
        self.closed = False

    def get(self, key: Key) -> object | None:
        return self.store.get(key)

    def set(self, key: Key, value: object, ttl: float | None = None) -> None:
        self.store[key] = value
        self.set_calls.append((key, value, ttl))

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# _NamespaceProxy — flat (1-arity) namespace
# ---------------------------------------------------------------------------


class TestNamespaceProxyFlat:
    def _proxy(self, bypass: bool = False) -> tuple[MockBackend, _NamespaceProxy]:
        backend = MockBackend()
        return backend, _NamespaceProxy(backend, bypass=bypass, key_class=FlatKey)

    def test_read_existing(self) -> None:
        backend, proxy = self._proxy()
        backend.store[("git_root",)] = {"project": "my-project"}
        assert proxy["git_root"] == {"project": "my-project"}

    def test_read_missing_returns_none(self) -> None:
        _, proxy = self._proxy()
        assert proxy["missing"] is None

    def test_setitem_writes(self) -> None:
        backend, proxy = self._proxy()
        proxy["git_root"] = "value"
        assert backend.store[("git_root",)] == "value"

    def test_set_with_ttl(self) -> None:
        backend, proxy = self._proxy()
        proxy.set("git_root", "value", ttl=3600.0)
        assert backend.set_calls == [(("git_root",), "value", 3600.0)]

    def test_set_without_ttl(self) -> None:
        backend, proxy = self._proxy()
        proxy.set("git_root", "value")
        assert backend.set_calls == [(("git_root",), "value", None)]

    def test_bypass_read_returns_none(self) -> None:
        backend, proxy = self._proxy(bypass=True)
        backend.store[("git_root",)] = "cached"
        assert proxy["git_root"] is None

    def test_bypass_write_goes_through(self) -> None:
        backend, proxy = self._proxy(bypass=True)
        proxy["git_root"] = "value"
        assert backend.store[("git_root",)] == "value"


# ---------------------------------------------------------------------------
# _NamespaceProxy — nested (3-arity) namespace
# ---------------------------------------------------------------------------


class TestNamespaceProxyNested:
    def _proxy(self, bypass: bool = False) -> tuple[MockBackend, _NamespaceProxy]:
        backend = MockBackend()
        return backend, _NamespaceProxy(backend, bypass=bypass, key_class=NestedKey)

    def test_intermediate_returns_proxy(self) -> None:
        _, proxy = self._proxy()
        sub = proxy["pipelines"]
        assert isinstance(sub, _NamespaceProxy)
        assert sub._prefix == ("pipelines",)

    def test_second_intermediate_returns_proxy(self) -> None:
        _, proxy = self._proxy()
        sub = proxy["pipelines"]["my-project"]
        assert isinstance(sub, _NamespaceProxy)
        assert sub._prefix == ("pipelines", "my-project")

    def test_chained_read(self) -> None:
        backend, proxy = self._proxy()
        backend.store[("pipelines", "my-project", 42)] = "pipeline"
        assert proxy["pipelines"]["my-project"][42] == "pipeline"

    def test_tuple_shortcut_read(self) -> None:
        backend, proxy = self._proxy()
        backend.store[("pipelines", "my-project", 42)] = "pipeline"
        assert proxy[("pipelines", "my-project", 42)] == "pipeline"

    def test_partial_tuple_returns_proxy(self) -> None:
        _, proxy = self._proxy()
        sub = proxy[("pipelines", "my-project")]
        assert isinstance(sub, _NamespaceProxy)
        assert sub._prefix == ("pipelines", "my-project")

    def test_chained_write(self) -> None:
        backend, proxy = self._proxy()
        proxy["pipelines"]["my-project"][42] = "pipeline"
        assert backend.store[("pipelines", "my-project", 42)] == "pipeline"

    def test_chained_set_with_ttl(self) -> None:
        backend, proxy = self._proxy()
        proxy["pipelines"]["my-project"].set(42, "pipeline", ttl=604800.0)
        expected = [(("pipelines", "my-project", 42), "pipeline", 604800.0)]
        assert backend.set_calls == expected

    def test_bypass_intermediate_then_read_returns_none(self) -> None:
        backend, proxy = self._proxy(bypass=True)
        backend.store[("pipelines", "my-project", 42)] = "pipeline"
        assert proxy["pipelines"]["my-project"][42] is None

    def test_bypass_write_goes_through(self) -> None:
        backend, proxy = self._proxy(bypass=True)
        proxy["pipelines"]["my-project"][42] = "pipeline"
        assert backend.store[("pipelines", "my-project", 42)] == "pipeline"


# ---------------------------------------------------------------------------
# Cache — singleton, handle lifecycle, bypass
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_cache_singleton() -> None:
    """Guarantee a clean singleton state around every test."""
    Cache._instance = None
    yield
    Cache._instance = None


@pytest.fixture()
def mock_backend(monkeypatch: pytest.MonkeyPatch) -> MockBackend:
    """Patch open_backend so Cache always returns a single MockBackend."""
    backend = MockBackend()
    monkeypatch.setattr("ddgl.cache.cache.open_backend", lambda ns, d: backend)
    return backend


class TestCacheSingleton:
    def test_open_returns_same_instance(self, tmp_path: Path) -> None:
        c1 = Cache.open(tmp_path)
        c2 = Cache.open(tmp_path)
        assert c1 is c2

    def test_close_resets_singleton(self, tmp_path: Path) -> None:
        cache = Cache.open(tmp_path)
        cache.close()
        assert Cache._instance is None

    def test_context_manager_closes(self, tmp_path: Path) -> None:
        with Cache.open(tmp_path) as cache:
            assert Cache._instance is cache
        assert Cache._instance is None


class TestCacheHandles:
    def test_handle_opened_lazily(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ddgl.cache.cache.open_backend", lambda ns, d: MockBackend()
        )
        cache = Cache.open(tmp_path)
        assert len(cache._handles) == 0
        _ = cache[MockNS.FLAT]
        assert len(cache._handles) == 1

    def test_same_handle_reused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0

        def counting_open(ns: MockNS, _d: Path) -> MockBackend:
            nonlocal call_count
            call_count += 1
            return MockBackend()

        monkeypatch.setattr("ddgl.cache.cache.open_backend", counting_open)
        with Cache.open(tmp_path) as cache:
            _ = cache[MockNS.FLAT]
            _ = cache[MockNS.FLAT]
        assert call_count == 1

    def test_handles_closed_on_close(
        self, tmp_path: Path, mock_backend: MockBackend
    ) -> None:
        with Cache.open(tmp_path) as cache:
            _ = cache[MockNS.FLAT]
        assert mock_backend.closed


class TestCacheBypass:
    def test_bypass_read_returns_none(
        self, tmp_path: Path, mock_backend: MockBackend
    ) -> None:
        mock_backend.store[("git_root",)] = "cached"
        with Cache.open(tmp_path, bypass=True) as cache:
            assert cache[MockNS.FLAT]["git_root"] is None

    def test_bypass_write_goes_through(
        self, tmp_path: Path, mock_backend: MockBackend
    ) -> None:
        with Cache.open(tmp_path, bypass=True) as cache:
            cache[MockNS.FLAT]["git_root"] = "value"
        assert mock_backend.store[("git_root",)] == "value"
