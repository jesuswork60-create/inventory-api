"""Keyset pagination: clients pass back the ``next_cursor`` of the previous page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence, TypeVar

from .http.request import Request

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(frozen=True, slots=True)
class Page:
    limit: int
    cursor: int | None

    @property
    def fetch_size(self) -> int:
        """One row more than requested, which tells whether another page exists."""
        return self.limit + 1


def page_from_request(request: Request) -> Page:
    return Page(
        limit=request.query_int("limit", DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT),
        cursor=request.query_int("cursor", None, minimum=0),
    )


def paginate(
    rows: Sequence[T], page: Page, *, cursor_of: Callable[[T], int], render: Callable[[T], Any]
) -> dict[str, Any]:
    visible = list(rows[: page.limit])
    has_more = len(rows) > page.limit
    return {
        "items": [render(row) for row in visible],
        "next_cursor": cursor_of(visible[-1]) if has_more and visible else None,
    }
