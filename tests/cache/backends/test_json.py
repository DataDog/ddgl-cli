from __future__ import annotations

from pathlib import Path

from ddgl.cache.backends.json import JsonBackend


class TestJsonBackend:
    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        b = JsonBackend(tmp_path / "cache.json")
        assert b.get(("missing",)) is None

    def test_set_and_get(self, tmp_path: Path) -> None:
        b = JsonBackend(tmp_path / "cache.json")
        b.set(("k",), {"hello": "world"})
        assert b.get(("k",)) == {"hello": "world"}

    def test_set_with_ttl_returns_value_before_expiry(self, tmp_path: Path) -> None:
        b = JsonBackend(tmp_path / "cache.json")
        b.set(("k",), "v", ttl=60.0)
        assert b.get(("k",)) == "v"

    def test_set_with_ttl_returns_none_after_expiry(self, tmp_path: Path) -> None:
        b = JsonBackend(tmp_path / "cache.json")
        b.set(("k",), "v", ttl=-1.0)  # already expired
        assert b.get(("k",)) is None

    def test_set_without_ttl_does_not_expire(self, tmp_path: Path) -> None:
        b = JsonBackend(tmp_path / "cache.json")
        b.set(("k",), "v")
        # Advance time conceptually — no TTL means it must still be there
        assert b.get(("k",)) == "v"

    def test_close_writes_to_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        b = JsonBackend(path)
        b.set(("k",), "persisted")
        b.close()
        assert path.exists()

    def test_reopen_reads_persisted_data(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        b1 = JsonBackend(path)
        b1.set(("k",), "persisted")
        b1.close()

        b2 = JsonBackend(path)
        assert b2.get(("k",)) == "persisted"

    def test_reopen_skips_expired_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        b1 = JsonBackend(path)
        b1.set(("k",), "old", ttl=-1.0)  # already expired at write time
        b1.close()

        b2 = JsonBackend(path)
        assert b2.get(("k",)) is None

    def test_corrupt_file_starts_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        path.write_text("not json{{{")
        b = JsonBackend(path)
        assert b.get(("k",)) is None

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        b = JsonBackend(tmp_path / "cache.json")
        b.set(("k",), "v")
        b.close()
        b.close()  # should not raise
