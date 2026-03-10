from __future__ import annotations

from pathlib import Path

from ddgl.cache.backends.base import Key


class TextFileBackend:
    """Plain text files in a directory (job logs).

    Keys are 1-tuples of strings, used directly as the filename stem.
    Values are raw strings.  No TTL — log files are GC'd via ``Cache.gc_logs``.
    """

    def __init__(self, directory: Path) -> None:
        raise NotImplementedError

    def get(self, key: Key) -> object | None:
        raise NotImplementedError

    def set(self, key: Key, value: object, ttl: float | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass
