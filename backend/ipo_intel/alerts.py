"""Telegram alerts. Each is sent at most once: alerts_log (ipo_id, kind) is the dedupe,
written only after Telegram accepted the message, so a failed send is retried by
the next run.

Per IPO, in IST, and only inside its window (a late start never sends stale news):
* opens_tomorrow  from 18:00 on the day before bidding opens
* closes_today    12:00 to 16:30 on the last bidding day: subscription, GMP and the odds
* allotment_day   from 17:00 on the allotment day, with the registrar's link
* listing_day     09:00 to 15:30 on the listing day while no listing price is in: the last GMP
* listing_result  once a listing price is in (up to 3 days later): actual gain against the GMP's

About the system (pseudo IPO id 'system'):
* IndianAPI quota below the backfill-pause or essential-only threshold, once a month each
* ipoalerts blocked after a 429, once a day; ipoalerts monthly use at 90%, once a month
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ipo_intel import queries
from ipo_intel.analysis import analyse
from ipo_intel.api.views import latest_subscriptions
from ipo_intel.budget import BudgetUsage
from ipo_intel.clients.base import ApiError
from ipo_intel.config import Settings
from ipo_intel.db import Conn, Row
from ipo_intel.engine.ev import GmpConfidence
from ipo_intel.engine.gmp import GmpReading, latest_gmp
from ipo_intel.engine.rules import Rules, cached_rules
from ipo_intel.gmp import gmp_rows, observations
from ipo_intel.ingest.jobs import JobContext, JobResult, JobStatus
from ipo_intel.ingest.quota import QuotaLevel, QuotaState
from ipo_intel.lifecycle import Lifecycle, lifecycle_of
from ipo_intel.timeutil import ist_at, to_ist

log = logging.getLogger(__name__)

SYSTEM = "system"
MAX_PER_RUN = 25
LISTING_RESULT_DAYS = 3

# (from, until) in minutes after IST midnight
WINDOWS = {
    "opens_tomorrow": (18 * 60, 24 * 60),
    "closes_today": (12 * 60, 16 * 60 + 30),
    "allotment_day": (17 * 60, 24 * 60),
    "listing_day": (9 * 60, 15 * 60 + 30),
}


@dataclass(frozen=True)
class Alert:
    ipo_id: str
    kind: str
    text: str


# --- formatting -------------------------------------------------------------------------------


def _inr(value: float | None) -> str:
    """Rupees with Indian grouping (₹2,52,800), paise only when there are any (₹5.25)."""
    if value is None:
        return "—"
    rupees, paise = divmod(round(abs(value) * 100), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, groups = digits[:-3], [digits[-3:]]
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        digits = ",".join(([head] if head else []) + groups)
    return f"{'-' if value < 0 else ''}₹{digits}" + (f".{paise:02d}" if paise else "")


def _x(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}×" if value >= 100 else f"{value:.2f}×"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1f}%"


def _prob(low: float, high: float) -> str:
    def one(p: float) -> str:
        return "100%" if p >= 0.9995 else "<0.1%" if 0 < p < 0.001 else f"{p * 100:.1f}%"

    return one(low) if one(low) == one(high) else f"{one(low)}–{one(high)}"


def _day(value: date | None) -> str:
    return value.strftime("%a %d %b") if value else "—"


def _name(row: Row) -> str:
    board = " (SME)" if row["issue_type"] == "sme" else ""
    return f"<b>{html.escape(row['name'])}</b>{board}"


def _gmp_line(reading: GmpReading | None) -> str:
    if reading is None:
        return "No GMP yet."
    implied = f", {_pct(reading.gmp_pct * 100)} implied" if reading.gmp_pct is not None else ""
    confidence = "high confidence" if reading.confidence is GmpConfidence.HIGH else "low confidence"
    return f"GMP {_inr(reading.gmp_rs)}{implied} ({confidence})."


def _subs_line(sub: dict | None) -> str:
    if not sub:
        return "No subscription figures yet."
    parts = [f"{label} {_x(sub.get(key))}" for key, label in (("retail", "retail"), ("nii", "NII"), ("qib", "QIB"), ("overall", "overall"))]
    return f"Subscribed (day {sub['day']}): " + ", ".join(parts) + "."


def _link(settings: Settings, ipo_id: str) -> str:
    if not settings.dashboard_url:
        return ""
    return f'\n<a href="{html.escape(settings.dashboard_url.rstrip("/"))}/ipo/{html.escape(ipo_id)}">Open in the dashboard</a>'


def _odds_line(conn: Conn, row: Row, rules: Rules, today: date, now: datetime) -> str:
    try:
        a = analyse(conn, row["id"], rules=rules, today=today, now=now)
    except (LookupError, ValueError):
        return ""
    odds = a.odds_final or a.odds_now
    if odds is None:
        return ""
    basis = "at the projected final" if odds.basis == "projected" else "now"
    category = "minimum bid" if a.min_category.value == "retail" else f"minimum bid (NII: {a.min_category.label})"
    return f"\nYour odds at the {category}: {_prob(odds.p_low, odds.p_high)} {basis}."


# --- what is due ----------------------------------------------------------------------------------


def _in_window(kind: str, minutes: int) -> bool:
    start, end = WINDOWS[kind]
    return start <= minutes < end


def due_alerts(
    conn: Conn,
    *,
    now: datetime,
    settings: Settings,
    rules: Rules,
    quota: QuotaState | None = None,
    gmp_usage: BudgetUsage | None = None,
) -> list[Alert]:
    local = to_ist(now)
    today = local.date()
    minutes = local.hour * 60 + local.minute
    sent = {(r["ipo_id"], r["kind"]) for r in conn.execute("SELECT ipo_id, kind FROM alerts_log")}
    alerts: list[Alert] = []

    def add(ipo_id: str, kind: str, text: str) -> None:
        if (ipo_id, kind) not in sent:
            alerts.append(Alert(ipo_id, kind, text))

    rows = [row for row in queries.list_ipos(conn) if row["bid_start"] is not None and not row["dormant"]]
    subs = latest_subscriptions(conn)
    points = gmp_rows(conn, [row["id"] for row in rows])

    def reading(row: Row, as_of: datetime) -> GmpReading | None:
        cutoff = float(row["cutoff_price"]) if row["cutoff_price"] is not None else None
        return latest_gmp(observations(points[row["id"]]), board=row["issue_type"], cutoff=cutoff, as_of=as_of)

    for row in rows:
        ipo_id = row["id"]
        stage = lifecycle_of(row, today)
        link = _link(settings, ipo_id)
        band = f"{_inr(float(row['price_min']))}–{_inr(float(row['price_max']))}" if row["price_min"] and row["price_max"] else "price band not out yet"
        minimum = f"minimum bid {_inr(float(row['min_investment_rs']))}" if row["min_investment_rs"] is not None else ""

        if row["bid_start"] == today + timedelta(days=1) and _in_window("opens_tomorrow", minutes):
            add(ipo_id, "opens_tomorrow", (
                f"🟢 {_name(row)} opens tomorrow, {_day(row['bid_start'])}.\n"
                f"Price {band}{', ' + minimum if minimum else ''}. Closes {_day(row['bid_end'])}, lists {_day(row['listing_date'])}.\n"
                f"{_gmp_line(reading(row, now))}{link}"
            ))

        if stage is Lifecycle.OPEN and row["bid_end"] == today and _in_window("closes_today", minutes):
            add(ipo_id, "closes_today", (
                f"⏰ {_name(row)} closes today.\n{_subs_line(subs.get(ipo_id))}\n"
                f"{_gmp_line(reading(row, now))}{_odds_line(conn, row, rules, today, now)}{link}"
            ))

        if row["allotment_date"] == today and _in_window("allotment_day", minutes):
            check = f'\nCheck: <a href="{html.escape(row["allotment_link"])}">{html.escape(row["registrar_name"] or "registrar")}</a>' if row["allotment_link"] else ""
            add(ipo_id, "allotment_day", (
                f"📋 Allotment day for {_name(row)}.\n{_subs_line(subs.get(ipo_id))}\n"
                f"Lists {_day(row['listing_date'])}. {_gmp_line(reading(row, now))}{check}{link}"
            ))

        if row["listing_date"] == today and row["listing_price"] is None and _in_window("listing_day", minutes):
            add(ipo_id, "listing_day", f"🔔 {_name(row)} lists today.\n{_gmp_line(reading(row, now))}{link}")

        if (
            row["listing_price"] is not None
            and row["listing_date"] is not None
            and row["listing_date"] <= today <= row["listing_date"] + timedelta(days=LISTING_RESULT_DAYS)
        ):
            before = reading(row, ist_at(row["listing_date"], 9, 59))
            actual = float(row["listing_gain_pct"]) if row["listing_gain_pct"] is not None else None
            implied = before.gmp_pct * 100 if before is not None and before.gmp_pct is not None else None
            versus = (
                f"GMP implied {_pct(implied)}, so it was off by {implied - actual:+.1f} points."
                if implied is not None and actual is not None
                else "No GMP was recorded before listing."
            )
            flag = "\n⚠️ A gain of exactly 0% or −20% may be a default value, not the real listing." if row["listing_flag"] else ""
            add(ipo_id, "listing_result", (
                f"📈 {_name(row)} listed at {_inr(float(row['listing_price']))} ({_pct(actual)} on {_inr(float(row['cutoff_price'] or 0))}).\n"
                f"{versus}{flag}{link}"
            ))

    month = today.strftime("%Y-%m")
    if quota is not None and quota.remaining is not None:
        if quota.level in (QuotaLevel.ESSENTIAL_ONLY, QuotaLevel.EXHAUSTED):
            add(SYSTEM, f"indianapi_below_{settings.indianapi_essential_only_below}:{month}", (
                f"⚠️ IndianAPI quota low: about {quota.remaining:,} requests left (below "
                f"{settings.indianapi_essential_only_below}). Only the open IPO list and open IPO details are fetched now."
            ))
        if quota.level is not QuotaLevel.NORMAL:
            add(SYSTEM, f"indianapi_below_{settings.indianapi_pause_backfill_below}:{month}", (
                f"⚠️ IndianAPI: about {quota.remaining:,} requests left (below "
                f"{settings.indianapi_pause_backfill_below}). The history backfill is paused."
            ))
    if gmp_usage is not None:
        if gmp_usage.blocked_until is not None and gmp_usage.blocked_until > now:
            until = to_ist(gmp_usage.blocked_until).strftime("%d %b %H:%M IST")
            add(SYSTEM, f"ipoalerts_blocked:{today.isoformat()}", f"⚠️ ipoalerts answered 429, so GMP polls are paused until {until}.")
        if gmp_usage.caps.monthly and gmp_usage.used_month >= 0.9 * gmp_usage.caps.monthly:
            add(SYSTEM, f"ipoalerts_month_90:{month}", (
                f"⚠️ ipoalerts: {gmp_usage.used_month} of this month's {gmp_usage.caps.monthly} GMP calls used."
            ))
    return alerts


# --- the job -------------------------------------------------------------------------------------------


def send_alerts(ctx: JobContext) -> JobResult:
    if ctx.telegram is None:
        return JobResult(JobStatus.SKIPPED, "skipped: Telegram is not set up (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
    now = ctx.clock.now()
    with ctx.db.connection() as conn:
        alerts = due_alerts(
            conn,
            now=now,
            settings=ctx.settings,
            rules=cached_rules(ctx.settings.rules_path),
            quota=ctx.quota(),
            gmp_usage=ctx.budget.usage() if ctx.budget is not None else None,
        )
    if not alerts:
        return JobResult(JobStatus.OK, "nothing to send")
    sent = failed = consecutive = 0
    for alert in alerts[:MAX_PER_RUN]:
        try:
            ctx.telegram.send(alert.text)
        except ApiError as exc:
            failed += 1
            consecutive += 1
            log.warning("telegram: %s/%s not sent: %s", alert.ipo_id, alert.kind, exc)
            if consecutive >= 3:
                break
            continue
        consecutive = 0
        sent += 1
        with ctx.db.connection() as conn:
            conn.execute(
                "INSERT INTO alerts_log (ipo_id, kind, sent_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (alert.ipo_id, alert.kind, ctx.clock.now()),
            )
    left = len(alerts) - sent
    summary = f"sent {sent} of {len(alerts)} alerts" + (f", {failed} failed (retried next run)" if failed else "")
    if left - failed > 0:
        summary += f", {left - failed} left for the next run"
    status = JobStatus.OK if sent == len(alerts) else JobStatus.FAILED if sent == 0 else JobStatus.PARTIAL
    return JobResult(status, summary)


ALERT_JOBS = {"alerts": send_alerts}
