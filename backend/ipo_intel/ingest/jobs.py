"""IndianAPI ingestion jobs. Each runs once and returns a JobResult; the scheduler and the CLI both call them.

* usage        GET /usage: record the remaining quota, warn when it runs low
* master       GET /ipo/v2 for every status: upsert IPOs, flag dormant ones
* open-details GET /ipo/{id} for open IPOs, plus closed ones whose final day is missing
* listed       GET /ipo: symbols (by name match) and listing prices/gains
* backfill     GET /ipo/{id} for listed IPOs never fetched after they closed,
               paced, resumable, and stopped by the quota thresholds
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

import psycopg

from ipo_intel.budget import BudgetGuard
from ipo_intel.clients.base import ApiError, RateLimitedError, RetriesExhaustedError
from ipo_intel.clients.factory import INDIANAPI
from ipo_intel.config import Settings
from ipo_intel.db import Conn, Database, Row
from ipo_intel.indianapi.adapter import LIST_STATUSES, IndianApi
from ipo_intel.indianapi.models import IpoDetail, SubscriptionDay
from ipo_intel.indianapi.parse import ParseError, percent_gain
from ipo_intel.ingest import store
from ipo_intel.ingest.quota import CallKind, QuotaGate, QuotaLevel, QuotaState
from ipo_intel.ingest.symbols import IpoMatcher, Match
from ipo_intel.ipoalerts.adapter import IpoAlerts
from ipo_intel.telegram import Telegram
from ipo_intel.lifecycle import Lifecycle, lifecycle_of, needs_final_detail
from ipo_intel.timeutil import Clock, SystemClock, ist_today

log = logging.getLogger(__name__)

# Stop a run of detail calls after this many failures in a row (the API is probably down).
MAX_CONSECUTIVE_FAILURES = 3
# An IPO whose detail failed this many times is left out of the backfill.
MAX_DETAIL_FAILURES = 3
# How far back open-details looks for closed, not yet listed IPOs whose final day was missed.
FINAL_CAPTURE_DAYS = 7


class JobStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class JobResult:
    status: JobStatus
    summary: str


@dataclass
class JobContext:
    db: Database
    api: IndianApi
    settings: Settings
    clock: Clock = field(default_factory=SystemClock)
    sleep: Callable[[float], None] = time.sleep
    # ipoalerts, only when IPOALERTS_KEY is set
    gmp: IpoAlerts | None = None
    budget: BudgetGuard | None = None
    # Telegram, only when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set
    telegram: Telegram | None = None

    def quota(self) -> QuotaState:
        return QuotaGate(self.db, self.settings).state()


def _skipped(state: QuotaState) -> JobResult:
    return JobResult(JobStatus.SKIPPED, f"skipped: {state.describe()}")


# --- usage --------------------------------------------------------------------


def check_usage(ctx: JobContext) -> JobResult:
    try:
        usage = ctx.api.usage(purpose="usage")
    except (ApiError, ParseError) as exc:
        return JobResult(JobStatus.FAILED, f"usage check failed: {exc}")
    with ctx.db.connection() as conn:
        store.record_usage(conn, INDIANAPI, usage, ctx.clock.now())
    state = ctx.quota()
    settings = ctx.settings
    if state.level in (QuotaLevel.ESSENTIAL_ONLY, QuotaLevel.EXHAUSTED):
        log.warning(
            "IndianAPI quota low: %s left (below %d); only essential calls will run",
            state.remaining, settings.indianapi_essential_only_below,
        )
    elif state.level is QuotaLevel.BACKFILL_PAUSED:
        log.warning(
            "IndianAPI quota: %s left (below %d); backfill paused",
            state.remaining, settings.indianapi_pause_backfill_below,
        )
    return JobResult(JobStatus.OK, state.describe())


# --- master list --------------------------------------------------------------


def sync_master_list(ctx: JobContext) -> JobResult:
    state = ctx.quota()
    if not state.allows(CallKind.ESSENTIAL):
        return _skipped(state)
    statuses = LIST_STATUSES if state.allows(CallKind.ROUTINE) else ("open",)
    now = ctx.clock.now()
    per_status: dict[str, int] = {}
    new = bad = 0
    failed: list[str] = []

    for status in statuses:
        try:
            records = ctx.api.list_ipos(status, purpose="master")
        except (ApiError, ParseError) as exc:
            log.warning("master list: status=%s failed: %s", status, exc)
            failed.append(status)
            continue
        per_status[status] = len(records)
        with ctx.db.connection() as conn:
            for record in records:
                try:
                    with conn.transaction():
                        new += store.upsert_ipo(conn, record, now)
                        if record.symbol:
                            store.upsert_symbol(conn, record.id, record.symbol, "indianapi_v2", now)
                except psycopg.Error as exc:
                    bad += 1
                    log.warning("master list: could not store %s: %s", record.id, exc)

    with ctx.db.connection() as conn:
        dormant = store.refresh_dormant(
            conn, now=now, today=ist_today(now), after_days=ctx.settings.dormant_after_days
        )

    parts = [", ".join(f"{status} {count}" for status, count in per_status.items()) or "no lists fetched"]
    parts.append(f"{new} new")
    parts.append(f"{dormant} dormant")
    if bad:
        parts.append(f"{bad} not stored")
    if failed:
        parts.append(f"failed: {', '.join(failed)}")
    if len(statuses) < len(LIST_STATUSES):
        parts.append(f"open list only ({state.describe()})")
    if len(failed) == len(statuses):
        status = JobStatus.FAILED
    elif failed or bad:
        status = JobStatus.PARTIAL
    else:
        status = JobStatus.OK
    return JobResult(status, "; ".join(parts))


# --- IPO details (shared by open-details and backfill) -----------------------------


def _subscription_days(detail: IpoDetail, today: date, row: Row) -> list[SubscriptionDay]:
    """Day-wise figures, plus the undated cumulative ones filed under the latest bidding day if missing."""
    days = list(detail.subscription)
    bid_start = detail.record.bid_start or row["bid_start"]
    bid_end = detail.record.bid_end or row["bid_end"]
    if detail.latest and bid_start and bid_end and bid_start <= today:
        latest_day = min(today, bid_end)
        if all(day.bid_date != latest_day for day in days):
            days.append(SubscriptionDay(latest_day, detail.latest))
    return days


def ingest_detail(conn: Conn, row: Row, detail: IpoDetail, now: datetime) -> None:
    ipo_id = row["id"]
    record = detail.record
    store.update_ipo(conn, record, now)
    store.upsert_reservation(conn, ipo_id, detail.reserved_shares, now)
    store.upsert_subscription(conn, ipo_id, _subscription_days(detail, ist_today(now), row), now)
    if record.symbol:
        store.upsert_symbol(conn, ipo_id, record.symbol, "indianapi_detail", now)
    if detail.listing is not None:
        store.record_listing(
            conn,
            ipo_id,
            price=detail.listing.price,
            gain_pct=detail.listing.gain_pct,
            source="indianapi_detail",
            listing_date=None,
            now=now,
        )
    store.mark_detail_fetched(conn, ipo_id, now)


@dataclass
class _DetailRun:
    fetched: int = 0
    failed: int = 0
    stopped: str | None = None


def _fetch_details(ctx: JobContext, targets: list[Row], *, purpose: str, kind: CallKind, pace: float) -> _DetailRun:
    run = _DetailRun()
    consecutive = 0
    for index, row in enumerate(targets):
        state = ctx.quota()
        if not state.allows(kind):
            run.stopped = f"paused: {state.describe()}"
            break
        if index and pace:
            ctx.sleep(pace)
        ipo_id = row["id"]
        try:
            detail = ctx.api.detail(ipo_id, purpose=purpose)
        except RateLimitedError:
            run.failed += 1
            run.stopped = "IndianAPI answered 429 (rate limited)"
            break
        except (ApiError, ParseError) as exc:
            run.failed += 1
            consecutive += 1
            log.warning("%s: GET /ipo/%s failed: %s", purpose, ipo_id, exc)
            # Timeouts and 5xx are not the IPO's fault; a 4xx or a malformed body is.
            if not isinstance(exc, RetriesExhaustedError):
                with ctx.db.connection() as conn:
                    store.mark_detail_failed(conn, ipo_id)
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                run.stopped = f"stopped after {consecutive} failures in a row"
                break
            continue
        try:
            with ctx.db.connection() as conn:
                ingest_detail(conn, row, detail, ctx.clock.now())
        except psycopg.Error as exc:
            run.failed += 1
            log.warning("%s: could not store details of %s: %s", purpose, ipo_id, exc)
            continue
        run.fetched += 1
        consecutive = 0
    return run


def _details_result(run: _DetailRun, total: int, extra: str = "") -> JobResult:
    summary = f"fetched {run.fetched} of {total}"
    if run.failed:
        summary += f", {run.failed} failed"
    if extra:
        summary += f"; {extra}"
    if run.stopped:
        summary += f"; {run.stopped}"
    if run.fetched == total:
        status = JobStatus.OK
    elif run.fetched == 0 and run.failed == 0:
        status = JobStatus.SKIPPED
    elif run.fetched == 0:
        status = JobStatus.FAILED
    else:
        status = JobStatus.PARTIAL
    return JobResult(status, summary)


# --- open details -------------------------------------------------------------------


def _is_live_target(row: Row, today: date) -> bool:
    stage = lifecycle_of(row, today)
    if stage is Lifecycle.OPEN:
        return True
    return stage is Lifecycle.CLOSED and needs_final_detail(row["bid_end"], row["detail_fetched_at"])


def refresh_open_details(ctx: JobContext) -> JobResult:
    state = ctx.quota()
    if not state.allows(CallKind.ESSENTIAL):
        return _skipped(state)
    today = ist_today(ctx.clock.now())
    with ctx.db.connection() as conn:
        candidates = store.detail_candidates(conn, bid_end_from=today - timedelta(days=FINAL_CAPTURE_DAYS))
    targets = [row for row in candidates if _is_live_target(row, today)]
    if not targets:
        return JobResult(JobStatus.OK, "no open IPOs and no missing final days")
    run = _fetch_details(ctx, targets, purpose="open_details", kind=CallKind.ESSENTIAL, pace=0)
    return _details_result(run, len(targets))


# --- listed snapshot (GET /ipo) ----------------------------------------------------


def snapshot_listed(ctx: JobContext) -> JobResult:
    state = ctx.quota()
    if not state.allows(CallKind.ROUTINE):
        return _skipped(state)
    try:
        records = ctx.api.buckets(purpose="listed")
    except (ApiError, ParseError) as exc:
        return JobResult(JobStatus.FAILED, f"GET /ipo failed: {exc}")
    now = ctx.clock.now()
    counts: Counter[str] = Counter()
    unmatched: list[str] = []

    with ctx.db.connection() as conn:
        matcher = IpoMatcher(store.ipo_match_rows(conn))
        for record in records:
            listed = record.bucket == "listed"
            if listed and record.listing_price is None:
                counts["null_price"] += 1  # nothing to learn about the listing; the symbol may still help
            row, match = matcher.match(record)
            if row is None:
                counts[match.value] += 1
                # The 'upcoming' bucket is mostly DRHP-stage companies that /ipo/v2 does not list yet.
                if match is Match.UNMATCHED and record.bucket != "upcoming":
                    unmatched.append(record.name)
                continue
            counts["matched"] += 1
            with conn.transaction():
                if record.symbol:
                    store.upsert_symbol(conn, row["id"], record.symbol, "indianapi_list", now)
                if not listed or record.listing_price is None:
                    continue
                gain = record.listing_gain_pct
                if gain is None:
                    gain = percent_gain(record.listing_price, record.issue_price or row["cutoff_price"])
                if store.record_listing(
                    conn,
                    row["id"],
                    price=record.listing_price,
                    gain_pct=gain,
                    source="indianapi_list",
                    listing_date=record.listing_date,
                    now=now,
                ):
                    counts["listings"] += 1
                    if store.listing_flag(gain):
                        counts["flagged"] += 1

    if unmatched:
        log.info("GET /ipo: no IPO matches these names: %s", "; ".join(sorted(unmatched)))
    summary = (
        f"{len(records)} records: {counts['matched']} matched, {counts['unmatched']} unmatched, "
        f"{counts['ambiguous']} ambiguous; {counts['listings']} listings stored "
        f"({counts['flagged']} flagged as possible price-band/default), "
        f"{counts['null_price']} listed without a listing price skipped"
    )
    return JobResult(JobStatus.OK, summary)


# --- backfill -------------------------------------------------------------------------


def backfill_targets(conn: Conn, today: date) -> list[Row]:
    """Listed IPOs whose final subscription was never fetched, newest first."""
    rows = [
        row
        for row in store.detail_candidates(conn)
        if lifecycle_of(row, today) is Lifecycle.LISTED
        and row["bid_end"] < today
        and row["detail_failures"] < MAX_DETAIL_FAILURES
        and needs_final_detail(row["bid_end"], row["detail_fetched_at"])
    ]
    rows.sort(key=lambda row: (row["listing_date"] or row["bid_end"], row["id"]), reverse=True)
    return rows


def backfill_listed(ctx: JobContext, limit: int | None = None) -> JobResult:
    limit = limit or ctx.settings.indianapi_backfill_limit
    # A fresh reading first, so the thresholds are judged on real numbers.
    try:
        usage = ctx.api.usage(purpose="backfill")
    except (ApiError, ParseError) as exc:
        log.warning("backfill: usage check failed (%s); using the last known reading", exc)
    else:
        with ctx.db.connection() as conn:
            store.record_usage(conn, INDIANAPI, usage, ctx.clock.now())
    state = ctx.quota()
    if not state.allows(CallKind.BACKFILL):
        return JobResult(JobStatus.SKIPPED, f"paused: {state.describe()}")

    with ctx.db.connection() as conn:
        pending = backfill_targets(conn, ist_today(ctx.clock.now()))
    if not pending:
        return JobResult(JobStatus.OK, "nothing to backfill")
    targets = pending[:limit]
    run = _fetch_details(
        ctx, targets, purpose="backfill", kind=CallKind.BACKFILL, pace=ctx.settings.indianapi_backfill_pace_seconds
    )
    return _details_result(run, len(targets), f"{len(pending) - run.fetched} listed IPOs still to backfill")


JOBS: dict[str, Callable[..., JobResult]] = {
    "usage": check_usage,
    "master": sync_master_list,
    "open-details": refresh_open_details,
    "listed": snapshot_listed,
    "backfill": backfill_listed,
}
