"""A fake ipoalerts on top of the IndianAPI test world. Symbols come from running master + listed."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ipo_intel.timeutil import UTC
from tests.indianapi_world import World

ALERTS_KEY = "ipoalerts-test-key-987654"


def gmp_path(symbol: str, interval: str, from_day: str) -> str:
    return f"/ipos/{symbol}/gmp?interval={interval}&from={from_day}"


def series(start: datetime, end: datetime, *, every: timedelta, price: float, step: float = 0.5) -> list[dict]:
    points, t, i = [], start, 0
    while t <= end:
        points.append({"timestamp": t.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "price": round(price + step * (i % 4), 2)})
        t += every
        i += 1
    return points


def payload(symbol: str, interval: str, points: list[dict]) -> dict:
    return {"ipoId": f"id-{symbol}", "symbol": symbol, "interval": interval, "field": "mean",
            "series": points, "meta": {"count": len(points)}}


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


HOUR = timedelta(hours=1)
DAY = timedelta(days=1)

# Acme: hourly from 01 Nov 00:00 UTC to 03 Nov 04:00 UTC (09:30 IST), one empty bucket.
ACME_POINTS = series(utc(2026, 11, 1), utc(2026, 11, 3, 4), every=HOUR, price=20.0)
ACME_POINTS[5]["price"] = None
ACME_STORED = len(ACME_POINTS) - 1

# Northstar listed on 21 Oct; the API repeats the last value after that, which must be cut.
NORTHSTAR_POINTS = series(utc(2026, 10, 4), utc(2026, 10, 20), every=DAY, price=60.0) + series(
    utc(2026, 10, 21), utc(2026, 11, 2), every=DAY, price=80.0, step=0
)
NORTHSTAR_BEFORE_LISTING = 17  # 04..20 Oct


def gmp_routes() -> dict[str, Any]:
    return {
        gmp_path("ACMETECH", "hourly", "2026-10-23"): payload("ACMETECH", "hourly", ACME_POINTS),
        gmp_path("SUNRISE", "hourly", "2026-10-24"): payload(
            "SUNRISE", "hourly", series(utc(2026, 11, 2), utc(2026, 11, 3, 4), every=HOUR, price=12.0)
        ),
        gmp_path("BLUEWAVE", "hourly", "2026-10-19"): payload(
            "BLUEWAVE", "hourly", series(utc(2026, 10, 25), utc(2026, 11, 3, 4), every=HOUR, price=31.0)
        ),
        gmp_path("NORTHSTAR", "daily", "2026-10-04"): payload("NORTHSTAR", "daily", NORTHSTAR_POINTS),
        gmp_path("KESAR", "daily", "2026-10-03"): payload(
            "KESAR", "daily", series(utc(2026, 10, 3), utc(2026, 10, 19), every=DAY, price=20.0)
        ),
        gmp_path("FLATLINE", "daily", "2026-09-27"): payload(
            "FLATLINE", "daily", series(utc(2026, 9, 27), utc(2026, 10, 13), every=DAY, price=3.0)
        ),
        gmp_path("DROPCAP", "daily", "2026-09-26"): payload("DROPCAP", "daily", []),
    }


def gmp_world(database, db_url: str, **settings: Any) -> World:
    world = World(database, db_url, ipoalerts_key=ALERTS_KEY, **settings)
    world.run("master")
    world.run("listed")
    world.transport.routes.update(gmp_routes())
    world.transport.requests.clear()
    return world


def gmp_paths(world: World) -> list[str]:
    return [path for path in world.transport.paths() if path.startswith("/ipos/")]
