from __future__ import annotations

from pathlib import Path

from ddgl.cache.backends.base import Key, _CacheBackend
from ddgl.cache.backends.json import JsonBackend
from ddgl.cache.backends.kv_sqlite import KvSqliteBackend
from ddgl.cache.backends.struct_sqlite import StructSqliteBackend
from ddgl.cache.backends.text_files import TextFileBackend
from ddgl.cache.cache_config import CacheNS

__all__ = [
    "Key",
    "_CacheBackend",
    "JsonBackend",
    "KvSqliteBackend",
    "StructSqliteBackend",
    "TextFileBackend",
    "open_backend",
]


def open_backend(ns: CacheNS, cache_dir: Path) -> _CacheBackend:
    config = ns.value
    path = cache_dir / config.filename
    match config.backend:
        case "json":
            return JsonBackend(path)
        case "kv_sqlite":
            return KvSqliteBackend(path)
        case "struct_sqlite":
            return StructSqliteBackend(path)
        case "text_files":
            return TextFileBackend(path)
    raise ValueError(f"Unknown backend type: {config.backend}")  # pragma: no cover
