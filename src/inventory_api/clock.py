"""Time handling. Everything stored is a UTC string, so it sorts chronologically."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(timezone.utc)


def to_stamp(moment: datetime) -> str:
    """Format as ``2024-03-01T12:00:00Z``. Sub-second precision is dropped."""
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(text: str) -> str:
    """Normalise an ISO 8601 date or time to the stored form, or raise ValueError.

    A bare date means midnight UTC. A time without an offset is refused, because
    guessing its zone would silently shift results.
    """
    value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    is_bare_date = len(text.strip()) == 10
    if value.tzinfo is None:
        if not is_bare_date:
            raise ValueError("a time needs an offset, for example 12:00:00Z")
        value = value.replace(tzinfo=timezone.utc)
    return to_stamp(value)


def add_seconds(stamp_source: datetime, seconds: int) -> str:
    return to_stamp(stamp_source + timedelta(seconds=seconds))
