"""Time helpers: store UTC, display IST.

Rules used across the codebase:
* Every datetime is timezone-aware. Naive datetimes are rejected, never guessed.
* Instants (fetch times, GMP timestamps, log times) are stored in UTC.
* Calendar days that matter to the Indian market (bidding, allotment, listing,
  the ipoalerts budget day) are IST dates.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")

_DDMMYYYY = re.compile(r"(\d{2})-(\d{2})-(\d{4})")


class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant as an aware UTC datetime."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt


def to_utc(dt: datetime) -> datetime:
    return require_aware(dt).astimezone(UTC)


def to_ist(dt: datetime) -> datetime:
    return require_aware(dt).astimezone(IST)


def ist_today(now: datetime | None = None) -> date:
    """The IST calendar date at `now` (default: the current instant)."""
    return to_ist(now if now is not None else utc_now()).date()


def month_start(day: date) -> date:
    return day.replace(day=1)


def ist_at(day: date, hour: int, minute: int = 0) -> datetime:
    """The UTC instant of an IST wall-clock time on `day`."""
    return datetime.combine(day, time(hour, minute), tzinfo=IST).astimezone(UTC)


def ist_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """[start, end) of an IST calendar day, as UTC instants."""
    return ist_at(day, 0), ist_at(day + timedelta(days=1), 0)


def next_ist_midnight(now: datetime) -> datetime:
    """The next 00:00 IST strictly after `now`, as a UTC instant."""
    return ist_at(ist_today(now) + timedelta(days=1), 0)


def parse_ddmmyyyy(value: str) -> date:
    """Parse IndianAPI's DD-MM-YYYY dates, e.g. '05-11-2025'."""
    match = _DDMMYYYY.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"expected DD-MM-YYYY, got {value!r}")
    day, month, year = (int(part) for part in match.groups())
    return date(year, month, day)


def parse_utc_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp to an aware UTC datetime.

    A timestamp without an offset is taken as UTC, which is what ipoalerts sends.
    """
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_ist(dt: datetime, fmt: str = "%d %b %Y, %H:%M IST") -> str:
    return to_ist(dt).strftime(fmt)
