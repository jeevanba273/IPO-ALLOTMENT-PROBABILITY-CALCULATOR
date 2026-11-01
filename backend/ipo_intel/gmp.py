"""GMP storage: ipoalerts series, manual entries and per-IPO fetch bookkeeping."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from ipo_intel.db import Conn, Row
from ipo_intel.engine.gmp import GmpObservation, GmpReading, latest_gmp
from ipo_intel.ipoalerts.parse import GmpPoint
from ipo_intel.timeutil import ist_today, require_aware

IPOALERTS = "ipoalerts"
MANUAL = "manual"

_UPSERT_POINT = """
INSERT INTO gmp_snapshots (ipo_id, ts_utc, interval, gmp_rs, source, fetched_at)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (ipo_id, ts_utc, interval, source) DO UPDATE SET gmp_rs = EXCLUDED.gmp_rs, fetched_at = EXCLUDED.fetched_at
"""


def cut_at_listing(points: Iterable[GmpPoint], listing_date: date | None) -> list[GmpPoint]:
    """Drop points on or after the listing day: from then on the API only repeats the last value."""
    points = list(points)
    if listing_date is None:
        return points
    return [point for point in points if ist_today(point.ts_utc) < listing_date]


def store_series(conn: Conn, ipo_id: str, interval: str, points: Iterable[GmpPoint], now: datetime) -> int:
    rows = [(ipo_id, p.ts_utc, interval, p.gmp_rs, IPOALERTS, now) for p in points]
    if rows:
        with conn.cursor() as cursor:
            cursor.executemany(_UPSERT_POINT, rows)
    return len(rows)


def set_manual_gmp(conn: Conn, ipo_id: str, gmp_rs: Decimal, at: datetime, now: datetime) -> None:
    if conn.execute("SELECT 1 FROM ipos WHERE id = %s", (ipo_id,)).fetchone() is None:
        raise LookupError(f"no IPO with id {ipo_id!r}")
    conn.execute(_UPSERT_POINT, (ipo_id, require_aware(at), MANUAL, gmp_rs, MANUAL, now))


def clear_manual_gmp(conn: Conn, ipo_id: str) -> int:
    return conn.execute("DELETE FROM gmp_snapshots WHERE ipo_id = %s AND source = %s", (ipo_id, MANUAL)).rowcount


def gmp_rows(conn: Conn, ipo_ids: Iterable[str]) -> dict[str, list[Row]]:
    ids = list(ipo_ids)
    result: dict[str, list[Row]] = {ipo_id: [] for ipo_id in ids}
    if not ids:
        return result
    for row in conn.execute(
        "SELECT ipo_id, ts_utc, interval, gmp_rs, source FROM gmp_snapshots WHERE ipo_id = ANY(%s) ORDER BY ts_utc",
        (ids,),
    ):
        result[row["ipo_id"]].append(row)
    return result


def observations(rows: Iterable[Row]) -> list[GmpObservation]:
    return [GmpObservation(row["ts_utc"], float(row["gmp_rs"]), row["source"]) for row in rows]


def reading_for(conn: Conn, ipo: Row, as_of: datetime) -> GmpReading | None:
    rows = gmp_rows(conn, [ipo["id"]])[ipo["id"]]
    cutoff = float(ipo["cutoff_price"]) if ipo.get("cutoff_price") is not None else None
    return latest_gmp(observations(rows), board=ipo["issue_type"], cutoff=cutoff, as_of=as_of)


# --- fetch bookkeeping ------------------------------------------------------------------

_STATE_UPSERT = """
INSERT INTO gmp_fetch_state (ipo_id, symbol, last_attempt_at, last_success_at, failures, last_error, backfilled_at)
VALUES (%(ipo_id)s, %(symbol)s, %(at)s, %(success)s, %(failures)s, %(error)s, %(backfilled)s)
ON CONFLICT (ipo_id) DO UPDATE SET
    symbol = EXCLUDED.symbol,
    last_attempt_at = EXCLUDED.last_attempt_at,
    last_success_at = COALESCE(EXCLUDED.last_success_at, gmp_fetch_state.last_success_at),
    failures = EXCLUDED.failures,
    last_error = EXCLUDED.last_error,
    backfilled_at = COALESCE(EXCLUDED.backfilled_at, gmp_fetch_state.backfilled_at)
"""


def record_success(conn: Conn, ipo_id: str, symbol: str, now: datetime, *, backfill: bool) -> None:
    conn.execute(_STATE_UPSERT, {
        "ipo_id": ipo_id, "symbol": symbol, "at": now, "success": now, "failures": 0, "error": None,
        "backfilled": now if backfill else None,
    })


def record_failure(conn: Conn, ipo_id: str, symbol: str, failures: int, error: str, now: datetime) -> None:
    conn.execute(_STATE_UPSERT, {
        "ipo_id": ipo_id, "symbol": symbol, "at": now, "success": None, "failures": min(failures, 1000),
        "error": error[:300], "backfilled": None,
    })


def failures_for_symbol(state: Row, symbol: str) -> int:
    """Failures so far, counting from zero again when the symbol has changed."""
    if state.get("state_symbol") != symbol:
        return 0
    return state.get("failures") or 0
