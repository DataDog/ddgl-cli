from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx


def _int_header(resp: httpx.Response, name: str) -> int | None:
    val = resp.headers.get(name)
    if val is None or val == "":
        return None
    return int(val)


@dataclass
class Page[T]:
    """A single page of paginated GitLab API results."""

    items: list[T]
    page: int
    next_page: int | None
    total_pages: int | None
    total: int | None

    @property
    def has_next(self) -> bool:
        return self.next_page is not None

    @classmethod
    def from_response(
        cls,
        resp: httpx.Response,
        item_factory: Callable[[dict], T],
    ) -> Page[T]:
        """Build a Page from an httpx response with GitLab pagination headers."""
        data = resp.json()
        items = [item_factory(item) for item in data]
        return cls(
            items=items,
            page=_int_header(resp, "x-page") or 1,
            next_page=_int_header(resp, "x-next-page"),
            total_pages=_int_header(resp, "x-total-pages"),
            total=_int_header(resp, "x-total"),
        )
