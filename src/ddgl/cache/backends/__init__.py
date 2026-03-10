from __future__ import annotations

from pathlib import Path

from ddgl.cache.backends.base import Key, _CacheBackend
from ddgl.cache.cache_config import CacheNS

__all__ = [
    "Key",
    "_CacheBackend",
    "open_backend",
]


def open_backend(ns: CacheNS, cache_dir: Path) -> _CacheBackend:
    raise NotImplementedError
