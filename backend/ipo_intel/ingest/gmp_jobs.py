"""ipoalerts GMP jobs. Every call reserves one unit of the daily budget first (12/IST day,
400/month, backfill at most 8 a day), and nothing is ever retried.

* gmp-live, slot 'morning' (10:30 IST): every open IPO, then closed IPOs that
  have not listed yet; slot 'afternoon' (15:30 IST): open mainboard IPOs again.
  So open mainboard IPOs are polled twice a day and SME IPOs once, until listing.
* gmp-backfill (21:00 IST, after the live polls): daily GMP history of
  recently listed IPOs, newest first, for calibration.

A 429 blocks ipoalerts until 00:00 IST (or later, if meta.expiresIn says so).
A refused budget stops the run quietly. Points on or after the listing day are
dropped, because the API repeats the last value from then on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ipo_intel.budget import Purpose
from ipo_intel.clients.base import ApiError, CallSkipped, RateLimitedError
from ipo_intel.db import Row
from ipo_intel.gmp import cut_at_listing, failures_for_symbol, record_failure, record_success, store_series
from ipo_intel.ingest.jobs import JobContext, JobResult, JobStatus
from ipo_intel.ipoalerts.parse import ParseError, expires_in_seconds
from ipo_intel.lifecycle import Lifecycle, lifecycle_of
from ipo_intel.timeutil import format_ist, ist_today, next_ist_midnight

log = logging.getLogger(__name__)

# GMP usually starts trading about a week before bidding opens.
LEAD_DAYS = 10
# Never ask for the same IPO twice within this window (restarts, manual runs).
LIVE_REPEAT_AFTER = timedelta(hours=3)
# An IPO whose symbol failed this many times is left alone until the symbol changes.
MAX_GMP_FAILURES = 3
# A closed IPO with no listing date stops being polled this long after bidding ends.
CLOSED_WITHOUT_LISTING_DAYS = 7
# The backfill reaches back this far.
BACKFILL_DAYS = 120
SLOTS = ("morning", "afternoon")

_TARGETS = """
SELECT i.id, i.name, i.issue_type, i.bid_start, i.bid_end, i.listing_date, i.listing_price, i.cutoff_price,
       s.symbol, st.symbol AS state_symbol, st.last_attempt_at, st.failures, st.backfilled_at
FROM ipos i
JOIN effective_symbols s ON s.ipo_id = i.id
LEFT JOIN gmp_fetch_state st ON st.ipo_id = i.id
WHERE i.bid_start IS NOT NULL AND i.bid_end IS NOT NULL
"""


def _usable(row: Row) -> bool:
    return failures_for_symbol(row, row["symbol"]) < MAX_GMP_FAILURES


def live_targets(rows: list[Row], *, today: date, now: datetime, slot: str) -> list[Row]:
    """IPOs to poll in this slot, most useful first: open mainboard, open SME, closed mainboard, closed SME."""
    if slot not in SLOTS:
        raise ValueError(f"slot must be one of {SLOTS}")
    picked = []
    for row in rows:
        stage = lifecycle_of(row, today)
        mainboard = row["issue_type"] == "mainboard"
        if stage is Lifecycle.OPEN:
            group = 0 if mainboard else 1
        elif stage is Lifecycle.CLOSED:
            waiting = (
                today < row["listing_date"]
                if row["listing_date"] is not None
                else today <= row["bid_end"] + timedelta(days=CLOSED_WITHOUT_LISTING_DAYS)
            )
            if not waiting:
                continue
            group = 2 if mainboard else 3
        else:
            continue
        if slot == "afternoon" and group != 0:
            continue
        if not _usable(row):
            continue
        recent = row["last_attempt_at"] is not None and now - row["last_attempt_at"] < LIVE_REPEAT_AFTER
        if recent and row["state_symbol"] == row["symbol"]:
            continue
        picked.append((group, row["listing_date"] or row["bid_end"], row["bid_end"], row["id"], row))
    picked.sort(key=lambda item: item[:4])
    return [item[-1] for item in picked]


def backfill_targets(rows: list[Row], *, today: date) -> list[Row]:
    """Listed IPOs without GMP history, newest listing first."""
    since = today - timedelta(days=BACKFILL_DAYS)
    picked = [
        row
        for row in rows
        if lifecycle_of(row, today) is Lifecycle.LISTED
        and row["listing_date"] is not None
        and since <= row["listing_date"] <= today
        and row["backfilled_at"] is None
        and _usable(row)
    ]
    picked.sort(key=lambda row: (row["listing_date"], row["id"]), reverse=True)
    return picked


@dataclass
class _GmpRun:
    fetched: int = 0
    points: int = 0
    failed: int = 0
    not_asked: int = 0
    stopped: str | None = None


def _fetch_all(ctx: JobContext, targets: list[Row], *, purpose: Purpose, interval: str, backfill: bool) -> _GmpRun:
    run = _GmpRun()
    for index, row in enumerate(targets):
        symbol = row["symbol"]
        from_day = row["bid_start"] - timedelta(days=LEAD_DAYS)
        try:
            series = ctx.gmp.gmp(symbol, interval=interval, from_day=from_day, purpose=purpose)
        except CallSkipped as exc:
            # Out of budget or blocked: no network I/O happened. Stop quietly.
            run.not_asked = len(targets) - index
            run.stopped = f"budget refused ({exc.decision.reason})"
            break
        except RateLimitedError as exc:
            now = ctx.clock.now()
            seconds = expires_in_seconds(exc.payload) or 0
            until = max(next_ist_midnight(now), now + timedelta(seconds=seconds))
            ctx.budget.block_until(until, f"HTTP 429 from ipoalerts (expiresIn {seconds}s)")
            log.warning("ipoalerts answered 429; no more GMP calls until %s", format_ist(until))
            run.failed += 1
            run.not_asked = len(targets) - index - 1
            run.stopped = f"rate limited; ipoalerts blocked until {format_ist(until)}"
            break
        except (ApiError, ParseError) as exc:
            run.failed += 1
            log.warning("GMP for %s (%s) failed: %s", row["id"], symbol, exc)
            with ctx.db.connection() as conn:
                record_failure(conn, row["id"], symbol, failures_for_symbol(row, symbol) + 1, str(exc), ctx.clock.now())
            continue
        now = ctx.clock.now()
        with ctx.db.connection() as conn:
            run.points += store_series(conn, row["id"], interval, cut_at_listing(series.points, row["listing_date"]), now)
            record_success(conn, row["id"], symbol, now, backfill=backfill)
        run.fetched += 1
    return run


def _result(run: _GmpRun, total: int, extra: str = "") -> JobResult:
    summary = f"fetched {run.fetched} of {total} ({run.points} points)"
    if run.failed:
        summary += f", {run.failed} failed"
    if run.not_asked:
        summary += f", {run.not_asked} not asked"
    if run.stopped:
        summary += f"; {run.stopped}"
    if extra:
        summary += f"; {extra}"
    if run.fetched == total:
        status = JobStatus.OK
    elif run.fetched == 0 and run.failed == 0:
        status = JobStatus.SKIPPED
    elif run.fetched == 0 and not run.stopped:
        status = JobStatus.FAILED
    else:
        status = JobStatus.PARTIAL
    return JobResult(status, summary)


def _no_key() -> JobResult:
    return JobResult(JobStatus.SKIPPED, "skipped: IPOALERTS_KEY is not set")


def poll_live(ctx: JobContext, slot: str = "morning") -> JobResult:
    if ctx.gmp is None:
        return _no_key()
    now = ctx.clock.now()
    with ctx.db.connection() as conn:
        rows = conn.execute(_TARGETS).fetchall()
    targets = live_targets(rows, today=ist_today(now), now=now, slot=slot)
    if not targets:
        return JobResult(JobStatus.OK, f"{slot}: nothing to poll (no open or waiting IPO with a GMP mapping)")
    run = _fetch_all(ctx, targets, purpose=Purpose.LIVE, interval="hourly", backfill=False)
    return _result(run, len(targets), extra=slot)


def backfill_gmp(ctx: JobContext, limit: int | None = None) -> JobResult:
    if ctx.gmp is None:
        return _no_key()
    limit = limit or ctx.settings.ipoalerts_backfill_daily_cap
    with ctx.db.connection() as conn:
        rows = conn.execute(_TARGETS).fetchall()
    pending = backfill_targets(rows, today=ist_today(ctx.clock.now()))
    if not pending:
        return JobResult(JobStatus.OK, "nothing to backfill")
    targets = pending[:limit]
    run = _fetch_all(ctx, targets, purpose=Purpose.BACKFILL, interval="daily", backfill=True)
    return _result(run, len(targets), extra=f"{len(pending) - run.fetched} listed IPOs still without GMP history")


GMP_JOBS = {
    "gmp-live": poll_live,
    "gmp-backfill": backfill_gmp,
}
