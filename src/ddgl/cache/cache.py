from __future__ import annotations

import logging
from pathlib import Path

from ddgl.cache.backends import open_backend
from ddgl.cache.backends.base import _CacheBackend
from ddgl.cache.cache_config import CacheNS
from ddgl.cache.proxy import _NamespaceProxy

logger = logging.getLogger("ddgl.cache")


class Cache:
    """Unified cache handle.

    Opens backends lazily on first access and closes them on ``close()``.
    Backends are shared across the process via the singleton pattern.

    Usage::

        with Cache.open(Path("~/.cache/ddgl").expanduser()) as cache:
            # JSON (projects, tokens)
            entry  = cache[CacheNS.PROJECTS]["git_root"]
            cache[CacheNS.PROJECTS]["git_root"] = {"project_path": "..."}
            cache[CacheNS.TOKENS].set(gitlab_url, token, ttl=CACHE_TTL_DDTOOL_TOKEN)

            # KV SQLite (API responses)
            hit = cache[CacheNS.API_RESPONSES][req_hash]
            cache[CacheNS.API_RESPONSES].set(req_hash, json_str, ttl=30.0)

            # Struct SQLite (pipelines / jobs)
            pipeline = cache[CacheNS.OBJECTS][("pipelines", project_id, pipeline_id)]
            cache[CacheNS.OBJECTS].set(
                ("pipelines", project_id, pipeline_id), pipeline, ttl=CACHE_TTL_FINISHED_PIPELINE
            )

            # Text files (logs)
            log = cache[CacheNS.LOGS][str(job_id)]
            cache[CacheNS.LOGS][str(job_id)] = log_text
    """

    _instance: Cache | None = None

    def __init__(self, cache_dir: Path, bypass: bool = False) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir = cache_dir
        self._bypass = bypass
        self._handles: dict[str, _CacheBackend] = {}

    @classmethod
    def open(cls, cache_dir: Path, bypass: bool = False) -> Cache:
        """Return the singleton, creating it on first call."""
        if cls._instance is None:
            cls._instance = cls(cache_dir, bypass=bypass)
        return cls._instance

    def __getitem__(self, ns: CacheNS | str) -> _NamespaceProxy:
        if isinstance(ns, str):
            ns = CacheNS[ns.upper()]
        abs_path = str(self._cache_dir / ns.value.filename)
        if abs_path not in self._handles:
            self._handles[abs_path] = open_backend(ns, self._cache_dir)
        return _NamespaceProxy(self._handles[abs_path], bypass=self._bypass, key_class=ns.value.key_class)

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
        Cache._instance = None

    def __enter__(self) -> Cache:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
