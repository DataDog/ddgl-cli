# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from pathlib import Path

from ddgl.cache.backends.text_files import TextFileBackend

TTL = 3600.0
KEY = ("job-123",)


def make_backend(tmp_path: Path) -> TextFileBackend:
    return TextFileBackend(tmp_path / "logs")


class TestTextFileBackend:
    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        assert b.get(KEY) is None

    def test_set_and_get(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.set(KEY, "hello log", ttl=TTL)
        assert b.get(KEY) == "hello log"

    def test_expired_returns_none(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.set(KEY, "stale", ttl=-1.0)
        assert b.get(KEY) is None

    def test_set_replaces_existing(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.set(KEY, "first", ttl=TTL)
        b.set(KEY, "second", ttl=TTL)
        assert b.get(KEY) == "second"

    def test_persists_across_reopen(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        b1 = TextFileBackend(log_dir)
        b1.set(KEY, "persistent log", ttl=TTL)
        b1.close()

        b2 = TextFileBackend(log_dir)
        assert b2.get(KEY) == "persistent log"

    def test_gc_prunes_expired_on_reopen(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        b1 = TextFileBackend(log_dir)
        b1.set(("old-job",), "expired log", ttl=-1.0)
        b1.set(("fresh-job",), "fresh log", ttl=TTL)
        b1.close()

        b2 = TextFileBackend(log_dir)
        assert b2.get(("old-job",)) is None
        assert b2.get(("fresh-job",)) == "fresh log"
        # Content file for expired entry must be gone
        assert not (log_dir / "old-job").exists()
        assert not (log_dir / "old-job.expires").exists()

    def test_directory_created_automatically(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "logs"
        b = TextFileBackend(nested)
        assert nested.is_dir()
        b.close()

    def test_multiple_keys_independent(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.set(("job-1",), "log one", ttl=TTL)
        b.set(("job-2",), "log two", ttl=TTL)
        assert b.get(("job-1",)) == "log one"
        assert b.get(("job-2",)) == "log two"
        assert b.get(("job-3",)) is None

    def test_close_is_safe(self, tmp_path: Path) -> None:
        b = make_backend(tmp_path)
        b.close()  # should not raise
