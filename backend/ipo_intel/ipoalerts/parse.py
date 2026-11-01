"""Parse ipoalerts GMP responses.

GET /ipos/{identifier}/gmp answers
{ipoId, symbol, interval, field, series: [{timestamp, price}], meta: {from, to, count}}
with ISO-8601 UTC timestamps ("2026-06-01T10:00:00.000Z") and price in rupees
per share, or null for a bucket without trades. A 429 body carries
meta.expiresIn: seconds until the window resets.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ipo_intel.timeutil import parse_utc_timestamp
from ipo_intel.values import to_decimal, to_int, to_symbol

log = logging.getLogger(__name__)


class ParseError(ValueError):
    """A GMP response does not have the expected shape."""


@dataclass(frozen=True)
class GmpPoint:
    ts_utc: datetime
    gmp_rs: Decimal


@dataclass(frozen=True)
class GmpSeries:
    symbol: str | None
    interval: str | None
    points: tuple[GmpPoint, ...]


def parse_series(payload: object) -> GmpSeries:
    if not isinstance(payload, Mapping):
        raise ParseError("GMP response is not a JSON object")
    series = payload.get("series")
    if series is None and isinstance(payload.get("data"), Mapping):
        payload = payload["data"]
        series = payload.get("series")
    if not isinstance(series, list):
        raise ParseError("GMP response has no 'series' list")
    points: dict[datetime, GmpPoint] = {}
    for item in series:
        if not isinstance(item, Mapping):
            continue
        price = to_decimal(item.get("price"))
        raw_ts = item.get("timestamp")
        if price is None or not isinstance(raw_ts, str):
            continue  # an empty bucket
        try:
            ts = parse_utc_timestamp(raw_ts)
        except ValueError:
            log.warning("ipoalerts: skipping a GMP point with a bad timestamp %r", raw_ts)
            continue
        points[ts] = GmpPoint(ts, price.quantize(Decimal("0.01")))
    interval = payload.get("interval") if isinstance(payload.get("interval"), str) else None
    return GmpSeries(to_symbol(payload.get("symbol")), interval, tuple(points[ts] for ts in sorted(points)))


def expires_in_seconds(payload: object) -> int | None:
    """meta.expiresIn from a 429 body, in seconds."""
    if not isinstance(payload, Mapping):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, Mapping):
        return None
    seconds = to_int(meta.get("expiresIn"))
    return seconds if seconds is not None and seconds >= 0 else None
