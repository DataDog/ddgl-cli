# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import logging
import time
from pathlib import Path

from ddgl.cache.backends.base import Key

logger = logging.getLogger(__name__)


class TextFileBackend:
    """Plain text files in a directory (job logs).

    Keys are 1-tuples of strings, used directly as the filename stem.
    Values are raw strings.  TTL is required and stored as a sidecar
    ``<key>.expires`` file containing the expiry timestamp as a float.

    GC (deletion of expired files) runs on ``__init__`` and removes both
    the content file and the sidecar for any expired entry.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)
        self._gc()
        logger.debug("TextFileBackend opened: %s", directory)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _content_path(self, key: str) -> Path:
        return self._dir / key

    def _expires_path(self, key: str) -> Path:
        return self._dir / f"{key}.expires"

    def _read_expires(self, key: str) -> float | None:
        p = self._expires_path(key)
        try:
            return float(p.read_text())
        except (FileNotFoundError, ValueError):
            return None

    def _gc(self) -> None:
        now = time.time()
        removed = 0
        for expires_file in self._dir.glob("*.expires"):
            try:
                expires_at = float(expires_file.read_text())
            except (ValueError, OSError):
                expires_at = 0.0
            if expires_at < now:
                stem = expires_file.stem
                self._content_path(stem).unlink(missing_ok=True)
                expires_file.unlink(missing_ok=True)
                removed += 1
        if removed:
            logger.debug("TextFileBackend GC: removed %d expired entries", removed)

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def get(self, key: Key) -> object | None:
        k = str(key)
        expires_at = self._read_expires(k)
        if expires_at is not None and expires_at < time.time():
            logger.debug("get(%r) expired", k)
            return None
        content_file = self._content_path(k)
        if not content_file.exists():
            logger.debug("get(%r) miss", k)
            return None
        logger.debug("get(%r) hit", k)
        return content_file.read_text()

    def set(self, key: Key, value: object, ttl: float) -> None:
        k = str(key)
        self._content_path(k).write_text(str(value))
        self._expires_path(k).write_text(str(time.time() + ttl))
        logger.debug("set(%r) ttl=%.0fs", k, ttl)

    def close(self) -> None:
        pass
