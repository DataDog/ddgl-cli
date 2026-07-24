# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import atexit
import json
import logging
import time
from contextlib import suppress
from pathlib import Path

from ddgl.cache.backends.base import Key

logger = logging.getLogger(__name__)


class JsonBackend:
    """In-memory JSON dict; flushed to disk on close() or process exit.

    Keys are 1-tuples of strings.  Values are arbitrary JSON-serialisable
    objects.  TTL is required and stored as ``expires_at`` alongside the value.
    """

    # Disk format: {"<key>": {"value": <any>, "expires_at": <float>}}

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, dict] = {}
        if path.exists():
            with suppress(Exception):
                self._data = json.loads(path.read_text())
        logger.debug("JsonBackend opened: %s (%d entries)", path, len(self._data))
        atexit.register(self.close)

    def get(self, key: Key) -> object | None:
        k = str(key)
        entry = self._data.get(k)
        if entry is None:
            logger.debug("get(%r) miss", k)
            return None
        expires_at = entry.get("expires_at")
        if expires_at is not None and expires_at < time.time():
            logger.debug("get(%r) expired", k)
            del self._data[k]
            return None
        logger.debug("get(%r) hit", k)
        return entry["value"]

    def set(self, key: Key, value: object, ttl: float) -> None:
        k = str(key)
        self._data[k] = {
            "value": value,
            "expires_at": time.time() + ttl,
        }
        logger.debug("set(%r) ttl=%.0fs", k, ttl)

    def close(self) -> None:
        with suppress(Exception):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2))
            logger.debug("JsonBackend flushed: %s", self._path)
        atexit.unregister(self.close)
