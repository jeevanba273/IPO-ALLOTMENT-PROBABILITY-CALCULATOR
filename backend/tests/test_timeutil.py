from datetime import date, datetime, timedelta, timezone

import pytest

from ipo_intel.timeutil import (
    UTC,
    format_ist,
    ist_at,
    ist_day_bounds_utc,
    ist_today,
    month_start,
    next_ist_midnight,
    parse_ddmmyyyy,
    parse_utc_timestamp,
    to_ist,
    to_utc,
)


def test_ist_is_utc_plus_five_thirty():
    assert to_ist(datetime(2026, 11, 1, tzinfo=UTC)).utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 10, 31, 18, 29, 59, tzinfo=UTC), date(2026, 10, 31)),
        (datetime(2026, 10, 31, 18, 30, tzinfo=UTC), date(2026, 11, 1)),
        (datetime(2026, 11, 1, 23, 59, tzinfo=UTC), date(2026, 11, 2)),
    ],
)
def test_ist_day_rolls_over_at_1830_utc(instant, expected):
    assert ist_today(instant) == expected


def test_ist_today_accepts_any_offset():
    new_york = timezone(timedelta(hours=-5))
    assert ist_today(datetime(2026, 11, 1, 14, 0, tzinfo=new_york)) == date(2026, 11, 2)


def test_ist_at_converts_wall_time_to_utc():
    assert ist_at(date(2026, 11, 2), 10, 30) == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)
    assert ist_at(date(2026, 11, 2), 0) == datetime(2026, 11, 1, 18, 30, tzinfo=UTC)


def test_ist_day_bounds():
    start, end = ist_day_bounds_utc(date(2026, 11, 2))
    assert start == datetime(2026, 11, 1, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 11, 2, 18, 30, tzinfo=UTC)


def test_next_ist_midnight_is_strictly_later():
    assert next_ist_midnight(datetime(2026, 11, 2, 5, 0, tzinfo=UTC)) == datetime(2026, 11, 2, 18, 30, tzinfo=UTC)
    exactly_midnight = datetime(2026, 11, 2, 18, 30, tzinfo=UTC)
    assert next_ist_midnight(exactly_midnight) == datetime(2026, 11, 3, 18, 30, tzinfo=UTC)


def test_month_start():
    assert month_start(date(2026, 11, 30)) == date(2026, 11, 1)


@pytest.mark.parametrize("func", [to_ist, to_utc, ist_today, format_ist, next_ist_midnight])
def test_naive_datetimes_are_rejected(func):
    with pytest.raises(ValueError, match="naive"):
        func(datetime(2026, 11, 1, 10, 0))


def test_parse_ddmmyyyy():
    assert parse_ddmmyyyy("05-11-2025") == date(2025, 11, 5)
    assert parse_ddmmyyyy(" 31-12-2025 ") == date(2025, 12, 31)


@pytest.mark.parametrize("bad", ["2025-11-05", "5-11-2025", "05/11/2025", "31-02-2025", "", "05-13-2025"])
def test_parse_ddmmyyyy_rejects_other_shapes(bad):
    with pytest.raises(ValueError):
        parse_ddmmyyyy(bad)


@pytest.mark.parametrize(
    "text",
    ["2025-10-08T04:30:00.000Z", "2025-10-08T04:30:00Z", "2025-10-08T10:00:00+05:30", "2025-10-08T04:30:00"],
)
def test_parse_utc_timestamp(text):
    parsed = parse_utc_timestamp(text)
    assert parsed == datetime(2025, 10, 8, 4, 30, tzinfo=UTC)
    assert parsed.utcoffset() == timedelta(0)


def test_format_ist():
    assert format_ist(datetime(2026, 11, 1, 4, 30, tzinfo=UTC)) == "01 Nov 2026, 10:00 IST"
