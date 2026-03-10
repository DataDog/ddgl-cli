from __future__ import annotations

import atexit
import json
import time
from contextlib import suppress
from pathlib import Path

from ddgl.cache.backends.base import Key


class JsonBackend:
    """In-memory JSON dict; flushed to disk on close() or process exit.

    Keys are 1-tuples of strings.  Values are arbitrary JSON-serialisable
    objects.  TTL is stored internally alongside the value and checked on
    read.
    """

    # Disk format: {"<key>": {"value": <any>, "expires_at": <float|null>}}

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, dict] = {}
        if path.exists():
            with suppress(Exception):
                self._data = json.loads(path.read_text())
        atexit.register(self.close)

    def get(self, key: Key) -> object | None:
        k = str(key[0])
        entry = self._data.get(k)
        if entry is None:
            return None
        expires_at = entry.get("expires_at")
        if expires_at is not None and expires_at < time.time():
            del self._data[k]
            return None
        return entry["value"]

    def set(self, key: Key, value: object, ttl: float | None = None) -> None:
        k = str(key[0])
        self._data[k] = {
            "value": value,
            "expires_at": time.time() + ttl if ttl is not None else None,
        }

    def close(self) -> None:
        with suppress(Exception):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2))
        atexit.unregister(self.close)
