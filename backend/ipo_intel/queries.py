"""Read-side queries shared by the CLI and (from Phase 5) the HTTP API."""

from __future__ import annotations

from ipo_intel.db import Conn, Row

_IPOS = """
SELECT i.*, s.symbol AS gmp_symbol, s.source AS symbol_source,
       sub.times AS latest_times, sub.bid_date AS latest_bid_date
FROM ipos i
LEFT JOIN effective_symbols s ON s.ipo_id = i.id
LEFT JOIN LATERAL (
    SELECT times, bid_date FROM subscription_snapshots
    WHERE ipo_id = i.id AND category = 'overall'
    ORDER BY bid_date DESC
    LIMIT 1
) sub ON true
"""


def list_ipos(conn: Conn) -> list[Row]:
    """Every IPO with its effective symbol and latest overall subscription, newest bidding first."""
    return conn.execute(_IPOS + " ORDER BY COALESCE(i.bid_start, i.listing_date) DESC NULLS LAST, i.name").fetchall()


def get_ipo(conn: Conn, ipo_id: str) -> Row | None:
    return conn.execute(_IPOS + " WHERE i.id = %s", (ipo_id,)).fetchone()


def reserved_shares(conn: Conn, ipo_id: str) -> dict[str, int]:
    rows = conn.execute("SELECT category, reserved_shares FROM ipo_categories WHERE ipo_id = %s", (ipo_id,))
    return {row["category"]: row["reserved_shares"] for row in rows}


def subscription_history(conn: Conn, ipo_id: str) -> list[Row]:
    return conn.execute(
        "SELECT bid_date, category, applied_shares, reserved_shares, times FROM subscription_snapshots "
        "WHERE ipo_id = %s ORDER BY bid_date, category",
        (ipo_id,),
    ).fetchall()


def symbols_for(conn: Conn, ipo_id: str) -> list[Row]:
    return conn.execute(
        "SELECT symbol, source, updated_at FROM symbols WHERE ipo_id = %s ORDER BY source", (ipo_id,)
    ).fetchall()


def last_job_runs(conn: Conn) -> list[Row]:
    """The latest run of each job."""
    return conn.execute(
        "SELECT DISTINCT ON (job) job, trigger, status, summary, started_at, finished_at "
        "FROM job_runs ORDER BY job, started_at DESC, id DESC"
    ).fetchall()


def latest_usage(conn: Conn, source: str) -> Row | None:
    return conn.execute(
        "SELECT checked_at, hard_limit, remaining, total_requests FROM api_usage "
        "WHERE source = %s ORDER BY checked_at DESC LIMIT 1",
        (source,),
    ).fetchone()
