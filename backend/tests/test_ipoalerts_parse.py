from datetime import datetime
from decimal import Decimal

import pytest

from ipo_intel.ipoalerts.parse import ParseError, expires_in_seconds, parse_series
from ipo_intel.timeutil import UTC

# The documented example (https://ipoalerts.in/docs/api-reference/endpoints/get-gmp-trends).
DOCS_EXAMPLE = {
    "ipoId": "ipo-123",
    "symbol": "MILKYMIST",
    "interval": "daily",
    "field": "mean",
    "series": [
        {"timestamp": "2026-06-01T10:00:00.000Z", "price": 10},
        {"timestamp": "2026-06-02T10:00:00.000Z", "price": 20},
    ],
    "meta": {"from": "2026-06-01T00:00:00.000Z", "to": "2026-06-02T23:59:59.999Z", "count": 2},
}


def test_documented_example():
    series = parse_series(DOCS_EXAMPLE)
    assert series.symbol == "MILKYMIST" and series.interval == "daily"
    assert [(p.ts_utc, p.gmp_rs) for p in series.points] == [
        (datetime(2026, 6, 1, 10, tzinfo=UTC), Decimal("10.00")),
        (datetime(2026, 6, 2, 10, tzinfo=UTC), Decimal("20.00")),
    ]


def test_empty_buckets_bad_points_and_duplicates_are_dropped():
    payload = {
        "symbol": "x",
        "series": [
            {"timestamp": "2026-06-03T10:00:00.000Z", "price": "12.5"},
            {"timestamp": "2026-06-01T10:00:00.000Z", "price": None},
            {"timestamp": "not a time", "price": 5},
            {"price": 5},
            "...",
            {"timestamp": "2026-06-02T10:00:00.000Z", "price": -3},
            {"timestamp": "2026-06-03T10:00:00.000Z", "price": 13},
        ],
    }
    series = parse_series(payload)
    assert [str(p.gmp_rs) for p in series.points] == ["-3.00", "13.00"]  # sorted; the later duplicate wins
    assert series.symbol == "X"


def test_empty_series_is_fine_but_a_missing_one_is_not():
    assert parse_series({"series": [], "meta": {"count": 0}}).points == ()
    assert len(parse_series({"data": DOCS_EXAMPLE}).points) == 2
    for bad in (None, [], {"meta": {}}, {"series": "nope"}):
        with pytest.raises(ParseError):
            parse_series(bad)


def test_expires_in():
    body = {"status": "429", "title": "Too Many Requests", "meta": {"quotaPerMinute": 6, "expiresIn": 45}}
    assert expires_in_seconds(body) == 45
    assert expires_in_seconds({"meta": {"expiresIn": "120"}}) == 120
    for bad in (None, {}, {"meta": None}, {"meta": {"expiresIn": -1}}, {"meta": {"expiresIn": "soon"}}):
        assert expires_in_seconds(bad) is None
