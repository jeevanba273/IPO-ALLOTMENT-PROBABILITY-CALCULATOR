"""Writes for ingestion. Every write is an upsert on a natural key, so re-running a job never duplicates.

Merging rule for IPO fields: a new non-null value replaces the stored one; a
null never erases a known value, because one endpoint often lacks fields the
other has.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from ipo_intel.db import Conn, Row
from ipo_intel.indianapi.models import IpoRecord, SubscriptionDay, Usage

PRICE_BAND_DEFAULT = "possible_price_band_default"
# Listing gains that usually mean the source filled in a default, not a real listing price.
_SUSPECT_LISTING_GAINS = (Decimal("0"), Decimal("-20"))

_MERGED = (
    "name", "issue_type", "symbol", "isin", "industry", "price_min", "price_max", "cutoff_price",
    "face_value", "issue_size_cr", "lot_size", "min_qty", "pre_apply_start", "bid_start", "bid_end",
    "allotment_date", "listing_date", "registrar_name", "allotment_link",
)

_UPSERT_IPO = f"""
INSERT INTO ipos (id, {", ".join(_MERGED)}, created_at, updated_at)
VALUES (%(id)s, {", ".join(f"%({column})s" for column in _MERGED)}, %(now)s, %(now)s)
ON CONFLICT (id) DO UPDATE SET
    {", ".join(f"{column} = COALESCE(EXCLUDED.{column}, ipos.{column})" for column in _MERGED)},
    updated_at = EXCLUDED.updated_at
RETURNING (xmax = 0) AS inserted
"""

# For GET /ipo/{id}, which may lack the name or issue type an INSERT needs.
_UPDATE_IPO = f"""
UPDATE ipos SET
    {", ".join(f"{column} = COALESCE(%({column})s, {column})" for column in _MERGED)},
    updated_at = %(now)s
WHERE id = %(id)s
"""

_UPSERT_SYMBOL = """
INSERT INTO symbols (ipo_id, symbol, source, updated_at) VALUES (%s, %s, %s, %s)
ON CONFLICT (ipo_id, source) DO UPDATE SET symbol = EXCLUDED.symbol, updated_at = EXCLUDED.updated_at
WHERE symbols.symbol IS DISTINCT FROM EXCLUDED.symbol
"""

_UPSERT_RESERVATION = """
INSERT INTO ipo_categories (ipo_id, category, reserved_shares, updated_at) VALUES (%s, %s, %s, %s)
ON CONFLICT (ipo_id, category) DO UPDATE SET reserved_shares = EXCLUDED.reserved_shares, updated_at = EXCLUDED.updated_at
"""

_UPSERT_SUBSCRIPTION = """
INSERT INTO subscription_snapshots (ipo_id, bid_date, category, applied_shares, reserved_shares, times, fetched_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ipo_id, bid_date, category) DO UPDATE SET
    applied_shares = EXCLUDED.applied_shares,
    reserved_shares = EXCLUDED.reserved_shares,
    times = EXCLUDED.times,
    fetched_at = EXCLUDED.fetched_at
"""

# The id-keyed detail endpoint wins over a name-matched GET /ipo record.
_RECORD_LISTING = """
UPDATE ipos SET
    listing_price = %(price)s,
    listing_gain_pct = %(gain)s,
    listing_flag = %(flag)s,
    listing_source = %(source)s,
    listing_date = COALESCE(listing_date, %(listing_date)s),
    updated_at = %(now)s
WHERE id = %(id)s
  AND (%(source)s = 'indianapi_detail' OR listing_source IS DISTINCT FROM 'indianapi_detail')
"""

_REFRESH_DORMANT = """
WITH flags AS (
    SELECT id,
           bid_start IS NULL AND bid_end IS NULL AND listing_date IS NULL AND listing_price IS NULL
           AND (created_at < %(seen_before)s OR COALESCE(pre_apply_start < %(day_before)s, false)) AS dormant
    FROM ipos
)
UPDATE ipos SET dormant = flags.dormant
FROM flags
WHERE ipos.id = flags.id AND ipos.dormant <> flags.dormant
"""

_DETAIL_CANDIDATES = """
SELECT id, name, issue_type, bid_start, bid_end, listing_date, listing_price, cutoff_price,
       detail_fetched_at, detail_failures
FROM ipos
WHERE bid_start IS NOT NULL AND bid_end IS NOT NULL AND (%(from)s::date IS NULL OR bid_end >= %(from)s::date)
ORDER BY bid_end, id
"""


def listing_flag(gain_pct: Decimal | None) -> str | None:
    return PRICE_BAND_DEFAULT if gain_pct is not None and gain_pct in _SUSPECT_LISTING_GAINS else None


def upsert_ipo(conn: Conn, record: IpoRecord, now: datetime) -> bool:
    """Insert or merge an IPO from GET /ipo/v2. Returns True if it is new."""
    row = conn.execute(_UPSERT_IPO, {**asdict(record), "now": now}).fetchone()
    return bool(row["inserted"])


def update_ipo(conn: Conn, record: IpoRecord, now: datetime) -> bool:
    """Merge GET /ipo/{id} fields into an existing IPO. Returns False if the IPO is unknown."""
    return conn.execute(_UPDATE_IPO, {**asdict(record), "now": now}).rowcount > 0


def upsert_symbol(conn: Conn, ipo_id: str, symbol: str, source: str, now: datetime) -> None:
    conn.execute(_UPSERT_SYMBOL, (ipo_id, symbol, source, now))


def upsert_reservation(conn: Conn, ipo_id: str, reserved: Mapping[str, int], now: datetime) -> None:
    for category, shares in reserved.items():
        conn.execute(_UPSERT_RESERVATION, (ipo_id, category, shares, now))


def upsert_subscription(conn: Conn, ipo_id: str, days: Iterable[SubscriptionDay], now: datetime) -> int:
    rows = [
        (ipo_id, day.bid_date, item.category, item.applied_shares, item.reserved_shares, item.times, now)
        for day in days
        for item in day.categories
    ]
    if rows:
        with conn.cursor() as cursor:
            cursor.executemany(_UPSERT_SUBSCRIPTION, rows)
    return len(rows)


def record_listing(
    conn: Conn,
    ipo_id: str,
    *,
    price: Decimal,
    gain_pct: Decimal | None,
    source: str,
    listing_date: date | None,
    now: datetime,
) -> bool:
    """Store a listing price and gain, with its outlier flag. False if a detail-sourced value was kept."""
    params = {
        "id": ipo_id,
        "price": price,
        "gain": gain_pct,
        "flag": listing_flag(gain_pct),
        "source": source,
        "listing_date": listing_date,
        "now": now,
    }
    return conn.execute(_RECORD_LISTING, params).rowcount > 0


def refresh_dormant(conn: Conn, *, now: datetime, today: date, after_days: int) -> int:
    """Flag IPOs that still have no bidding dates `after_days` after we first saw them. Returns how many are dormant."""
    params = {"seen_before": now - timedelta(days=after_days), "day_before": today - timedelta(days=after_days)}
    conn.execute(_REFRESH_DORMANT, params)
    return conn.execute("SELECT count(*) AS n FROM ipos WHERE dormant").fetchone()["n"]


def detail_candidates(conn: Conn, *, bid_end_from: date | None = None) -> list[Row]:
    """IPOs with a bidding window (ending on or after `bid_end_from`, if given)."""
    return conn.execute(_DETAIL_CANDIDATES, {"from": bid_end_from}).fetchall()


def mark_detail_fetched(conn: Conn, ipo_id: str, now: datetime) -> None:
    conn.execute("UPDATE ipos SET detail_fetched_at = %s, detail_failures = 0 WHERE id = %s", (now, ipo_id))


def mark_detail_failed(conn: Conn, ipo_id: str) -> None:
    conn.execute("UPDATE ipos SET detail_failures = LEAST(detail_failures + 1, 1000) WHERE id = %s", (ipo_id,))


def ipo_match_rows(conn: Conn) -> list[Row]:
    """Every IPO with the symbols IndianAPI (or you) gave it by id, for joining GET /ipo records."""
    return conn.execute(
        "SELECT i.id, i.name, i.issue_type, i.bid_start, i.listing_date, i.cutoff_price, "
        "       COALESCE(array_agg(s.symbol) FILTER (WHERE s.symbol IS NOT NULL), '{}') AS symbols "
        "FROM ipos i LEFT JOIN symbols s ON s.ipo_id = i.id AND s.source <> 'indianapi_list' "
        "GROUP BY i.id"
    ).fetchall()


def record_usage(conn: Conn, source: str, usage: Usage, now: datetime) -> None:
    conn.execute(
        "INSERT INTO api_usage (source, checked_at, hard_limit, remaining, total_requests) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (source, now, usage.hard_limit, usage.remaining, usage.total_requests),
    )
