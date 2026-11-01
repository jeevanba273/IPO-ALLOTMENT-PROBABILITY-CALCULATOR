"""Command line: `ipo-intel <command>` (or `python -m ipo_intel <command>`)."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg

from ipo_intel import queries
from ipo_intel.analysis import (
    METHOD_LABELS,
    Analysis,
    OddsResult,
    analyse,
    clear_reservation_override,
    load_histories,
    reservation_overrides,
    set_reservation_override,
)
from ipo_intel.clients.factory import INDIANAPI, IPOALERTS, build_ipoalerts_budget
from ipo_intel.engine.category import Category
from ipo_intel.engine.ev import GmpConfidence
from ipo_intel.engine.gmp import GmpReading, latest_gmp
from ipo_intel.engine.projection import ProjectionStatus, multiplier_table
from ipo_intel.engine.rules import RulesError, cached_rules
from ipo_intel.gmp import clear_manual_gmp, gmp_rows, observations, reading_for, set_manual_gmp
from ipo_intel.ingest.gmp_jobs import SLOTS
from ipo_intel.ingest.runner import BUDGETED_JOBS, all_jobs
from ipo_intel.timeutil import IST
from ipo_intel.clients.runlog import recent_runs
from ipo_intel.config import ConfigError, Settings, get_settings, require
from ipo_intel.db import Database, connect
from ipo_intel.devdb import LocalDb, LocalDbError
from ipo_intel.ingest.jobs import JobStatus
from ipo_intel.ingest.quota import QuotaGate
from ipo_intel.ingest.runner import JobRunner
from ipo_intel.ingest.symbols import clear_manual_symbol, set_manual_symbol
from ipo_intel.lifecycle import Lifecycle, lifecycle_of
from ipo_intel.logging_setup import configure_logging
from ipo_intel.migrate import MigrationError, apply_migrations, migration_status
from ipo_intel.redaction import redact
from ipo_intel.timeutil import format_ist, ist_today, utc_now

Handler = Callable[[argparse.Namespace, Settings], int]

# Stage order in `ipos` output: what needs attention first.
_STAGE_ORDER = {Lifecycle.OPEN: 0, Lifecycle.CLOSED: 1, Lifecycle.PRE_APPLY: 2, Lifecycle.LISTED: 3}
# `ipos` hides listings older than this unless --all is given.
_RECENT_LISTING_DAYS = 30
_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_FLAG_LABELS = {"possible_price_band_default": "possible price-band/default"}


def make_runner(settings: Settings) -> JobRunner:
    return JobRunner(settings)


def _database(settings: Settings) -> Database:
    return Database(require(settings.database_url, "DATABASE_URL"), max_size=1)


def _inr(amount: Decimal | int | None, *, paise: bool = False) -> str:
    """Rupees with Indian digit grouping: 1,50,000."""
    if amount is None:
        return "-"
    value = Decimal(amount).quantize(Decimal("0.01") if paise else Decimal("1"))
    whole, _, fraction = f"{abs(value):f}".partition(".")
    if len(whole) > 3:
        head, groups = whole[:-3], [whole[-3:]]
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join([head, *groups] if head else groups)
    sign = "-" if value < 0 else ""
    return f"{sign}₹{whole}" + (f".{fraction}" if fraction else "")


def _count(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _day(value: date | None, fmt: str = "%d %b") -> str:
    return value.strftime(fmt) if value else "-"


def _times(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:.2f}x"


def _pct(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:+.2f}%"


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _age(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{max(0, round(hours * 60))} min"
    if hours < 48:
        return f"{hours:.0f} h"
    return f"{hours / 24:.0f} days"


def _rs(value: float | Decimal) -> str:
    """Rupees, with paise only when there are any (GMP is often ₹22.50)."""
    amount = Decimal(str(value))
    return _inr(amount, paise=amount != amount.to_integral_value())


def _gmp_cell(reading: GmpReading | None) -> str:
    if reading is None:
        return "-"
    pct = f" {reading.gmp_pct * 100:+.0f}%" if reading.gmp_pct is not None else ""
    low = " low" if reading.confidence is GmpConfidence.LOW else ""
    return f"{_rs(reading.gmp_rs)}{pct}{low}"


def _price_band(row: dict) -> str:
    low, high = row["price_min"], row["price_max"]
    if low is None and high is None:
        return "-"
    if low is None or high is None or low == high:
        return _inr(high or low)
    return f"{_inr(low)}–{_inr(high).removeprefix('₹')}"


def _cmd_config(args: argparse.Namespace, settings: Settings) -> int:
    print("Secrets (values are never shown):")
    for env_var, is_set in settings.secret_status().items():
        print(f"  {env_var:<22} {'set' if is_set else 'missing'}")
    print("Settings:")
    rows = [
        ("indianapi_base_url", settings.indianapi_base_url),
        ("ipoalerts_base_url", settings.ipoalerts_base_url),
        ("http timeout / retries", f"{settings.http_timeout_seconds:g}s / {settings.http_max_retries}"),
        (
            "ipoalerts caps",
            f"{settings.ipoalerts_daily_cap}/day (backfill max {settings.ipoalerts_backfill_daily_cap}), "
            f"{settings.ipoalerts_monthly_cap}/month",
        ),
        (
            "indianapi thresholds",
            f"pause backfill < {settings.indianapi_pause_backfill_below}, "
            f"essential only < {settings.indianapi_essential_only_below}",
        ),
        (
            "indianapi backfill",
            f"{settings.indianapi_backfill_limit} IPOs per run, "
            f"{settings.indianapi_backfill_pace_seconds:g}s apart",
        ),
        ("dormant after", f"{settings.dormant_after_days} days without bidding dates"),
        ("migrations_dir", str(settings.migrations_dir)),
        ("log_level", settings.log_level),
    ]
    for name, value in rows:
        print(f"  {name:<22} {value}")
    return 0


def _cmd_migrate(args: argparse.Namespace, settings: Settings) -> int:
    with connect(require(settings.database_url, "DATABASE_URL")) as conn:
        applied = apply_migrations(conn, settings.migrations_dir)
    if not applied:
        print("database is up to date")
    for name in applied:
        print(f"applied {name}")
    return 0


def _cmd_migrations(args: argparse.Namespace, settings: Settings) -> int:
    with connect(require(settings.database_url, "DATABASE_URL")) as conn:
        states = migration_status(conn, settings.migrations_dir)
    for state in states:
        when = format_ist(state.applied_at) if state.applied_at else "pending"
        print(f"  {state.migration.name:<40} {when}")
    return 0


def _cmd_budget(args: argparse.Namespace, settings: Settings) -> int:
    with Database(require(settings.database_url, "DATABASE_URL"), max_size=1) as db:
        usage = build_ipoalerts_budget(db, settings).usage()
    caps = usage.caps
    live = usage.by_purpose.get("live", 0)
    backfill = usage.by_purpose.get("backfill", 0)
    print(f"{IPOALERTS} budget for {usage.day:%d %b %Y} (IST day)")
    print(f"  today      {usage.used_today}/{caps.daily}  (live {live}, backfill {backfill}/{settings.ipoalerts_backfill_daily_cap})")
    print(f"  this month {usage.used_month}/{caps.monthly}")
    if usage.blocked_until is not None:
        print(f"  BLOCKED until {format_ist(usage.blocked_until)}: {usage.block_reason}")
    return 0


def _cmd_runs(args: argparse.Namespace, settings: Settings) -> int:
    with Database(require(settings.database_url, "DATABASE_URL"), max_size=1) as db:
        rows = recent_runs(db, source=args.source, limit=args.limit)
    if not rows:
        print("no source runs recorded")
    for row in rows:
        code = row["http_status"] if row["http_status"] is not None else "-"
        print(
            f"  {format_ist(row['ran_at'], '%d %b %H:%M:%S')}  {row['source']:<10} {row['status']:<15} "
            f"{code!s:>4} {row['duration_ms']:>6}ms  #{row['attempt']}  {row['endpoint']}"
        )
    return 0


def _cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    if args.job in BUDGETED_JOBS and not args.spend_budget:
        print(
            f"{args.job} spends the ipoalerts budget ({settings.ipoalerts_daily_cap} calls a day) and normally runs "
            "only on its schedule. Add --spend-budget to run it now anyway.",
            file=sys.stderr,
        )
        return 2
    kwargs: dict = {}
    if args.limit and args.job in ("backfill", "gmp-backfill"):
        kwargs["limit"] = args.limit
    if args.job == "gmp-live":
        kwargs["slot"] = args.slot
    with make_runner(settings) as runner:
        result = runner.run(args.job, trigger="cli", **kwargs)
    print(f"{args.job}: {result.status} - {result.summary}")
    return 1 if result.status is JobStatus.FAILED else 0


def _cmd_scheduler(args: argparse.Namespace, settings: Settings) -> int:
    from ipo_intel.scheduler import SchedulerLock, build_scheduler, next_runs

    lock = SchedulerLock(require(settings.database_url, "DATABASE_URL"))
    if not lock.acquire():
        print("error: another copy of the app is already running the schedule on this database", file=sys.stderr)
        return 2
    with make_runner(settings) as runner:
        scheduler = build_scheduler(runner)
        print("Scheduler running (Ctrl+C to stop). Next runs:")
        for name, when in next_runs(utc_now()).items():
            print(f"  {name:<13} {format_ist(when)}")
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            if scheduler.running:
                scheduler.shutdown(wait=False)
            lock.release()
    return 0


def _cmd_ipos(args: argparse.Namespace, settings: Settings) -> int:
    today = ist_today()
    now = utc_now()
    with _database(settings) as db, db.connection() as conn:
        rows = queries.list_ipos(conn)
        points = gmp_rows(conn, [row["id"] for row in rows])
    readings = {
        row["id"]: latest_gmp(
            observations(points[row["id"]]),
            board=row["issue_type"],
            cutoff=float(row["cutoff_price"]) if row["cutoff_price"] is not None else None,
            as_of=now,
        )
        for row in rows
    }
    recent = today - timedelta(days=_RECENT_LISTING_DAYS)
    shown = []
    for row in rows:
        stage = lifecycle_of(row, today)
        if args.lifecycle:
            if stage.value != args.lifecycle:
                continue
        elif not args.all:
            if row["dormant"] or (stage is Lifecycle.PRE_APPLY and row["bid_start"] is None):
                continue
            if stage is Lifecycle.LISTED and (row["listing_date"] or row["bid_end"] or today) < recent:
                continue
        shown.append((stage, row))
    if not shown:
        print("no IPOs to show" + ("" if rows else " (run `ipo-intel run master` first)"))
        return 0
    shown.sort(key=lambda item: _STAGE_ORDER[item[0]])

    print(
        f"{'STAGE':<9} {'NAME':<30} {'BOARD':<5} {'BIDDING':<15} {'PRICE':<12} {'LOT':>5} "
        f"{'MIN ₹':>9} {'SUBS':>8} {'GMP':>14} {'LISTING':>9}  SYMBOL"
    )
    for stage, row in shown:
        bidding = f"{_day(row['bid_start'])}–{_day(row['bid_end'])}" if row["bid_start"] else "-"
        listing = _pct(row["listing_gain_pct"]) + ("!" if row["listing_flag"] else "")
        symbol = row["gmp_symbol"] or "no GMP mapping"
        if row["dormant"]:
            symbol += "  (dormant)"
        print(
            f"{stage.value:<9} {_clip(row['name'], 30):<30} {'SME' if row['issue_type'] == 'sme' else 'main':<5} "
            f"{bidding:<15} {_price_band(row):<12} {_count(row['lot_size']):>5} "
            f"{_inr(row['min_investment_rs']):>9} {_times(row['latest_times']):>8} "
            f"{_gmp_cell(readings[row['id']]):>14} {listing:>9}  {symbol}"
        )
    hidden = len(rows) - len(shown)
    note = (
        f", {hidden} hidden (no bidding dates yet, dormant, or listed over {_RECENT_LISTING_DAYS} days ago;"
        " --all shows them)"
        if hidden
        else ""
    )
    print(f"\n{len(shown)} IPOs{note}. Stages come from dates, as of {today:%d %b %Y} IST. "
          "'!' marks a possible price-band/default listing gain; 'low' a low-confidence GMP.")
    return 0


def _cmd_ipo(args: argparse.Namespace, settings: Settings) -> int:
    with _database(settings) as db, db.connection() as conn:
        row = queries.get_ipo(conn, args.ipo_id)
        if row is None:
            print(f"no IPO with id {args.ipo_id!r}", file=sys.stderr)
            return 1
        reserved = queries.reserved_shares(conn, args.ipo_id)
        history = queries.subscription_history(conn, args.ipo_id)
        symbols = queries.symbols_for(conn, args.ipo_id)
    today = ist_today()
    board = "SME" if row["issue_type"] == "sme" else "mainboard"
    print(f"{row['name']}  ({row['id']})  {board} · {lifecycle_of(row, today).value}")
    print(f"  Bidding      {_day(row['bid_start'], '%d %b %Y')} – {_day(row['bid_end'], '%d %b %Y')}   "
          f"allotment {_day(row['allotment_date'])}   listing {_day(row['listing_date'])}")
    print(f"  Price band   {_price_band(row)}  (cut-off {_inr(row['cutoff_price'], paise=True)})   "
          f"lot {_count(row['lot_size'])}   min {_count(row['min_qty'])} shares = {_inr(row['min_investment_rs'])}")
    if row["issue_size_cr"] is not None:
        print(f"  Issue size   {_inr(row['issue_size_cr'], paise=True)} cr")
    if row["registrar_name"] or row["allotment_link"]:
        link = "allotment link stored" if row["allotment_link"] else "no allotment link"
        print(f"  Registrar    {row['registrar_name'] or '-'} ({link})")
    if symbols:
        listed = ", ".join(f"{s['symbol']} ({s['source']})" for s in symbols)
        print(f"  Symbol       {row['gmp_symbol']} via {row['symbol_source']}   [all: {listed}]")
    else:
        print("  Symbol       no GMP mapping (set one with `ipo-intel symbol set`)")
    if reserved:
        parts = [f"{category} {_count(reserved[category])}" for category in ("retail", "nii", "qib") if category in reserved]
        print(f"  Reserved     {' · '.join(parts)} shares")
    if history:
        by_day: dict[date, dict[str, Decimal]] = {}
        for item in history:
            by_day.setdefault(item["bid_date"], {})[item["category"]] = item["times"]
        print("  Subscription (times, cumulative)")
        print(f"     {'day':<8} {'retail':>8} {'nii':>8} {'qib':>8} {'overall':>8}")
        for day, values in by_day.items():
            cells = " ".join(f"{_times(values.get(category)):>8}" for category in ("retail", "nii", "qib", "overall"))
            print(f"     {_day(day):<8} {cells}")
    if row["listing_price"] is not None:
        flag = f"  [{_FLAG_LABELS.get(row['listing_flag'], row['listing_flag'])}]" if row["listing_flag"] else ""
        print(f"  Listing      {_inr(row['listing_price'], paise=True)} ({_pct(row['listing_gain_pct'])}) "
              f"via {row['listing_source']}{flag}")
    if row["dormant"]:
        print("  Flags        dormant: still no bidding dates")
    fetched = format_ist(row["detail_fetched_at"]) if row["detail_fetched_at"] else "never"
    print(f"  Details      last fetched {fetched}")
    return 0


def _cmd_symbol(args: argparse.Namespace, settings: Settings) -> int:
    with _database(settings) as db, db.connection() as conn:
        if args.action == "set":
            if not args.symbol:
                print("usage: ipo-intel symbol set <ipo_id> <SYMBOL>", file=sys.stderr)
                return 2
            try:
                symbol = set_manual_symbol(conn, args.ipo_id, args.symbol, utc_now())
            except (LookupError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"{args.ipo_id}: symbol set to {symbol} (manual, overrides IndianAPI)")
        else:
            removed = clear_manual_symbol(conn, args.ipo_id)
            print(f"{args.ipo_id}: manual symbol {'removed' if removed else 'was not set'}")
    return 0


def _cmd_jobs(args: argparse.Namespace, settings: Settings) -> int:
    with _database(settings) as db:
        with db.connection() as conn:
            runs = queries.last_job_runs(conn)
            usage = queries.latest_usage(conn, INDIANAPI)
        quota = QuotaGate(db, settings).state()
    print("Last run of each job:")
    if not runs:
        print("  none yet")
    for run in runs:
        when = format_ist(run["started_at"], "%d %b %H:%M IST")
        print(f"  {run['job']:<13} {run['status']:<8} {when}  ({run['trigger']})  {run['summary'] or ''}")
    for name in all_jobs():
        if all(run["job"] != name for run in runs):
            print(f"  {name:<13} never run")
    checked = f", last /usage {format_ist(usage['checked_at'], '%d %b %H:%M IST')}" if usage else ""
    print(f"IndianAPI quota: {quota.describe()}{checked}")
    return 0


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import threading
    import webbrowser

    import uvicorn

    from ipo_intel.api.app import create_app
    from ipo_intel.devdb import local_url

    if args.host not in _LOOPBACK and settings.dashboard_password is None:
        print(
            f"error: set DASHBOARD_PASSWORD before serving on {args.host}; without it anyone who can reach "
            "this machine could open the dashboard",
            file=sys.stderr,
        )
        return 2
    database_url = settings.database_url.get_secret_value() if settings.database_url else None
    if database_url == local_url():
        LocalDb().start()  # the local Postgres, if that is what .env points at
    url = f"http://{args.host}:{args.port}"
    print(f"IPO Intelligence Dashboard: {url}  (the schedule runs inside it; Ctrl+C stops it)")
    if args.open:
        threading.Timer(2.5, webbrowser.open, args=[url]).start()
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_config=None, access_log=False)
    return 0


def _telegram(settings: Settings):
    from ipo_intel.clients.factory import build_telegram_client
    from ipo_intel.telegram import Telegram

    client = build_telegram_client(settings, _NoRecord())
    chat = settings.telegram_chat_id.get_secret_value() if settings.telegram_chat_id else ""
    return client, Telegram(client, chat)


class _NoRecord:
    """Setup calls to Telegram are not worth a source_runs row (and may run without a database)."""

    def record(self, run) -> None:
        return None


def _cmd_telegram(args: argparse.Namespace, settings: Settings) -> int:
    from ipo_intel.clients.base import ApiError

    client, telegram = _telegram(settings)
    with client:
        try:
            if args.action == "chats":
                chats = telegram.chats()
                if not chats:
                    print("No chats yet: open your bot in Telegram, send it any message, then run this again.")
                for chat in chats:
                    print(f"  TELEGRAM_CHAT_ID={chat['id']}   ({chat['type']}: {chat['name']})")
                return 0
            require(settings.telegram_chat_id, "TELEGRAM_CHAT_ID")
            telegram.send("✅ IPO Intelligence can reach you here. Alerts will arrive in this chat.", purpose="setup")
        except ApiError as exc:
            print(f"error: {redact(str(exc), settings.secret_values())}", file=sys.stderr)
            return 1
    print("Test message sent.")
    return 0


def _cmd_alerts(args: argparse.Namespace, settings: Settings) -> int:
    """Show which alerts are due right now, without sending anything."""
    from ipo_intel.alerts import due_alerts
    from ipo_intel.clients.factory import build_ipoalerts_budget

    with _database(settings) as db:
        quota = QuotaGate(db, settings).state()
        usage = build_ipoalerts_budget(db, settings).usage()
        with db.connection() as conn:
            alerts = due_alerts(conn, now=utc_now(), settings=settings, rules=cached_rules(settings.rules_path),
                                quota=quota, gmp_usage=usage)
    if not alerts:
        print("No alerts due right now.")
    for alert in alerts:
        print(f"--- {alert.ipo_id} / {alert.kind}")
        print(alert.text)
    return 0


def _cmd_localdb(args: argparse.Namespace, settings: Settings) -> int:
    local = LocalDb()
    if args.action == "stop":
        print("local Postgres stopped" if local.stop() else "local Postgres was not running")
        return 0
    if args.action == "status":
        state = "running" if local.is_running() else "stopped"
        print(f"local Postgres {state}; data in {local.data_dir}")
        return 0
    url = local.start()
    with connect(url) as conn:
        applied = apply_migrations(conn, settings.migrations_dir)
    print(f"local Postgres running at {url}")
    print(f"data in {local.data_dir}")
    for name in applied:
        print(f"applied {name}")
    current = settings.database_url.get_secret_value() if settings.database_url else None
    if current != url:
        print(f"to use it, set DATABASE_URL={url} in .env")
    return 0


def _p(value: float) -> str:
    if value >= 0.9995:
        return "100%"
    if 0 < value < 0.001:
        return "<0.1%"
    return f"{value * 100:.1f}%"


def _p_range(low: float, high: float) -> str:
    return _p(low) if _p(low) == _p(high) else f"{_p(low)} – {_p(high)}"


def _x(value: float) -> str:
    return f"{value:,.0f}x" if value >= 100 else f"{value:.2f}x"


def _x_range(low: float, high: float) -> str:
    return _x(low) if abs(high - low) < 0.005 else f"{_x(low)} – {_x(high)}"


def _shares(value: float) -> str:
    return f"{value:,.0f}" if value >= 10 else f"{value:,.1f}"


def _odds_line(odds: OddsResult, analysis: Analysis) -> list[str]:
    where = {
        "current": "now",
        "final": "final",
        "projected": "at the projected final (middle half of past IPOs)",
    }[odds.basis]
    lots_low, lots_high = odds.shares_low / analysis.lot_size, odds.shares_high / analysis.lot_size
    lines = [f"    {where}, {_x_range(odds.times_low, odds.times_high)} subscribed:"]
    if odds.category is Category.RETAIL:
        lines.append(f"      P(allotment) per application  {_p_range(odds.p_low, odds.p_high)}"
                     "  (the low end assumes everyone bid the minimum)")
    else:
        lines.append(f"      P(at least 1 lot) per application  {_p_range(odds.p_low, odds.p_high)}")
    if analysis.applications > 1:
        lines.append(f"      P(at least one of {analysis.applications} applications)  {_p_range(odds.any_low, odds.any_high)}")
    if abs(odds.shares_high - odds.shares_low) < 0.05:
        shares, lots = _shares(odds.shares_low), f"{lots_low:.2f}"
    else:
        shares = f"{_shares(odds.shares_low)} – {_shares(odds.shares_high)}"
        lots = f"{lots_low:.2f} – {lots_high:.2f}"
    lines.append(f"      expected shares  {shares}  ({lots} lots)")
    return lines


def _cmd_odds(args: argparse.Namespace, settings: Settings) -> int:
    rules = cached_rules(settings.rules_path)
    confidence = GmpConfidence(args.gmp_confidence) if args.gmp_confidence else None
    with _database(settings) as db, db.connection() as conn:
        try:
            a = analyse(
                conn, args.ipo_id, rules=rules, today=ist_today(), lots=args.lots, applications=args.apps,
                gmp_rs=args.gmp, gmp_confidence=confidence, now=utc_now(),
            )
        except (LookupError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    board = "SME" if a.board == "sme" else "Mainboard"
    min_lots = a.min_qty // a.lot_size
    print(f"{a.name}  ·  {board}  ·  {a.stage.value}")
    print(f"  Minimum bid   {a.min_qty:,} shares ({min_lots} lot{'s' if min_lots > 1 else ''}) = {_inr(a.min_investment)}"
          f"  →  {a.min_category.label}")
    print(f"  You apply     {a.lots} lot{'s' if a.lots > 1 else ''} = {a.lots * a.lot_size:,} shares = {_inr(a.amount_rs)}"
          f"  →  {a.category.label}, {a.applications} application{'s' if a.applications > 1 else ''}")
    if a.latest is not None:
        cells = " · ".join(f"{c} {_times(Decimal(str(a.latest.figures[c].times)))}"
                           for c in ("retail", "nii", "qib", "overall") if c in a.latest.figures)
        state = "" if a.latest.complete else ", still bidding"
        print(f"  Subscription  day {a.latest.day} ({_day(a.latest.bid_date)}{state}): {cells}")
    if a.projections:
        first = next(iter(a.projections.values()))
        if first.status is ProjectionStatus.FINAL:
            print("  Final subscription is in; nothing to project.")
        else:
            print(f"  Projected final, from day {a.base.day} ({_day(a.base.bid_date)}), {board} history:")
            for name in ("retail", "nii", "qib", "overall"):
                p = a.projections.get(name)
                if p is None:
                    continue
                if p.status is ProjectionStatus.OK:
                    print(f"    {name:<8} median {_x(p.median):>8}   middle half {_x_range(p.q1, p.q3):<20}"
                          f" min–max {_x_range(p.low, p.high):<20} {p.message}")
                else:
                    print(f"    {name:<8} {p.message} ({p.samples} past IPOs)")
    print(f"  Your odds ({a.category.label}, {METHOD_LABELS[a.method]})")
    if a.method == "sebi_2022":
        print(f"    the minimum NII bid here is {a.allotment_unit_lots} lots "
              f"({_inr(a.allotment_unit_lots * a.lot_size * a.cutoff)})")
    for odds in (a.odds_now, a.odds_final):
        if odds is not None:
            for line in _odds_line(odds, a):
                print(line)
    if a.odds_now is None:
        print("    no subscription figures yet")
    if a.gmp is not None:
        badge = "HIGH confidence" if a.gmp.confidence is GmpConfidence.HIGH else "low confidence"
        pct = f" ({a.gmp.gmp_pct * 100:+.1f}% of the cut-off)" if a.gmp.gmp_pct is not None else ""
        print(f"  GMP           {_rs(a.gmp.gmp_rs)}{pct} from {a.gmp.source}, {_age(a.gmp.age)} old, "
              f"{a.gmp.recent_points} points in 48 h  [{badge}]")
    if a.ev is not None:
        label = "high" if a.ev.confidence is GmpConfidence.HIGH else "low"
        given = " (as entered)" if args.gmp is not None else ""
        print(f"  Expected value at GMP {_rs(a.gmp_used)}{given} ({label} confidence)")
        point = f", most likely about {_inr(a.ev.point)}" if a.ev.point is not None else " (no single figure for low-confidence GMP)"
        print(f"    {_inr(a.ev.low)} to {_inr(a.ev.high)}{point}")
        for band in a.ev.bands:
            print(f"    at {band.multiplier:g} × GMP: {_inr(band.low)} to {_inr(band.high)}")
    else:
        print("  Expected value  no GMP yet: add one with `ipo-intel gmp set`, or --gmp <rupees> for a what-if")
    for note in a.notes:
        print(f"  Note: {note}")
    for warning in a.warnings:
        print(f"  ! {warning} Set the RHP figure with `ipo-intel reservation set {a.ipo_id} <category> <shares>`.")
    return 0


def _cmd_multipliers(args: argparse.Namespace, settings: Settings) -> int:
    rules = cached_rules(settings.rules_path)
    with _database(settings) as db, db.connection() as conn:
        histories = load_histories(conn, ist_today())
    boards = {board: sum(1 for h in histories if h.board == board) for board in ("mainboard", "sme")}
    print(f"Complete histories: {boards['mainboard']} mainboard, {boards['sme']} SME "
          f"(projections need {rules.projection.min_samples} per board, category and day)")
    print(f"  {'BOARD':<10} {'CATEGORY':<8} {'DAY':>3} {'IPOS':>5} {'MIN':>9} {'MEDIAN':>9} {'MAX':>9}")
    for row in multiplier_table(histories, rules):
        if row.samples < rules.projection.min_samples:
            figures = f"{'not enough history':>29}"
        else:
            figures = f"{row.low:8.2f}x {row.median:8.2f}x {row.high:8.2f}x"
        print(f"  {row.board:<10} {row.category:<8} {row.day:>3} {row.samples:>5} {figures}")
    print("Multiplier = final times subscribed / times at the end of that bidding day.")
    return 0


def _cmd_reservation(args: argparse.Namespace, settings: Settings) -> int:
    with _database(settings) as db, db.connection() as conn:
        try:
            if args.action == "set":
                if args.category is None or args.shares is None:
                    print("usage: ipo-intel reservation set <ipo_id> <retail|nii|qib> <shares> [--note TEXT]", file=sys.stderr)
                    return 2
                set_reservation_override(conn, args.ipo_id, args.category, args.shares, args.note, utc_now())
                print(f"{args.ipo_id}: {args.category} reserved shares set to {args.shares:,} (from the RHP)")
            elif args.action == "clear":
                removed = clear_reservation_override(conn, args.ipo_id, args.category)
                print(f"{args.ipo_id}: {removed} override(s) removed")
            overrides = reservation_overrides(conn, args.ipo_id)
            api = queries.reserved_shares(conn, args.ipo_id)
        except (LookupError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(f"  {'CATEGORY':<8} {'INDIANAPI':>14} {'RHP (MANUAL)':>14}")
    for category in ("retail", "nii", "qib"):
        print(f"  {category:<8} {_count(api.get(category)):>14} {_count(overrides.get(category)):>14}")
    return 0


def _cmd_gmp(args: argparse.Namespace, settings: Settings) -> int:
    now = utc_now()
    with _database(settings) as db, db.connection() as conn:
        row = queries.get_ipo(conn, args.ipo_id)
        if row is None:
            print(f"error: no IPO with id {args.ipo_id!r}", file=sys.stderr)
            return 1
        if args.action == "set":
            if args.gmp_rs is None:
                print("usage: ipo-intel gmp set <ipo_id> <rupees> [--at 'YYYY-MM-DD HH:MM' (IST)]", file=sys.stderr)
                return 2
            try:
                at = datetime.strptime(args.at, "%Y-%m-%d %H:%M").replace(tzinfo=IST) if args.at else now
            except ValueError:
                print("error: --at must look like '2026-11-01 14:30' (IST)", file=sys.stderr)
                return 2
            set_manual_gmp(conn, args.ipo_id, Decimal(str(args.gmp_rs)), at, now)
            print(f"{row['name']}: manual GMP {_rs(args.gmp_rs)} at {format_ist(at)}")
        elif args.action == "clear":
            print(f"{row['name']}: {clear_manual_gmp(conn, args.ipo_id)} manual GMP entries removed")
        reading = reading_for(conn, row, now)
        points = gmp_rows(conn, [args.ipo_id])[args.ipo_id]
        state = conn.execute("SELECT * FROM gmp_fetch_state WHERE ipo_id = %s", (args.ipo_id,)).fetchone()
    print(f"{row['name']}  ({row['id']})  symbol {row['gmp_symbol'] or 'none: no GMP mapping'}")
    if reading is None:
        print("  no GMP yet")
    else:
        badge = "HIGH confidence" if reading.confidence is GmpConfidence.HIGH else "low confidence"
        pct = f", {reading.gmp_pct * 100:+.1f}% of the cut-off" if reading.gmp_pct is not None else ""
        print(f"  latest   {_rs(reading.gmp_rs)}{pct} ({reading.source}, {format_ist(reading.ts_utc)}, "
              f"{_age(reading.age)} old)  [{badge}]")
        print(f"  points   {len(points)} stored, {reading.recent_points} in the last 48 h")
        for point in points[-8:]:
            kind = "manual" if point["source"] == "manual" else f"ipoalerts {point['interval']}"
            print(f"    {format_ist(point['ts_utc'], '%d %b %H:%M')}  {_rs(point['gmp_rs']):>8}  {kind}")
    if state is not None:
        last = format_ist(state["last_attempt_at"]) if state["last_attempt_at"] else "never"
        error = f", last error: {state['last_error']}" if state["last_error"] else ""
        print(f"  ipoalerts  last asked {last}, {state['failures']} failure(s){error}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ipo-intel", description="IPO Intelligence Dashboard backend")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    def add(name: str, handler: Handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(handler=handler)
        return sub

    add("config", _cmd_config, "show settings and which secrets are set (never their values)")
    add("migrate", _cmd_migrate, "apply pending database migrations")
    add("migrations", _cmd_migrations, "list migrations and when each was applied")
    add("budget", _cmd_budget, "show today's and this month's ipoalerts call budget")
    runs = add("runs", _cmd_runs, "show the most recent external calls (source_runs)")
    runs.add_argument("--source", help="only this source, e.g. indianapi or ipoalerts")
    runs.add_argument("--limit", type=int, default=20)

    run = add("run", _cmd_run, "run one ingestion job now")
    run.add_argument("job", choices=list(all_jobs()))
    run.add_argument("--limit", type=int, help="backfill and gmp-backfill: how many listed IPOs to fetch")
    run.add_argument("--slot", choices=SLOTS, default="morning", help="gmp-live: which daily poll to run")
    run.add_argument("--spend-budget", action="store_true", help="allow a gmp-* job to spend ipoalerts calls now")
    add("scheduler", _cmd_scheduler, "run the IndianAPI jobs on their IST schedule until Ctrl+C")
    serve = add("serve", _cmd_serve, "run the dashboard and its schedule (http://127.0.0.1:8000)")
    serve.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to serve beyond this machine (needs DASHBOARD_PASSWORD)")
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")), help="default: $PORT or 8000")
    serve.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    telegram = add("telegram", _cmd_telegram, "Telegram setup: list the chats that messaged your bot, or send a test")
    telegram.add_argument("action", choices=["chats", "test"])
    alerts = add("alerts", _cmd_alerts, "show the Telegram alerts due right now, without sending them")
    alerts.add_argument("action", choices=["preview"])
    ipos = add("ipos", _cmd_ipos, "list IPOs: open first, with subscription and symbol mapping")
    ipos.add_argument("--lifecycle", choices=[stage.value for stage in Lifecycle])
    ipos.add_argument("--all", action="store_true", help="include dormant and older listed IPOs")
    ipo = add("ipo", _cmd_ipo, "show one IPO in detail, with day-wise subscription")
    ipo.add_argument("ipo_id")
    symbol = add("symbol", _cmd_symbol, "set or clear a manual symbol override (used for GMP lookups)")
    symbol.add_argument("action", choices=["set", "clear"])
    symbol.add_argument("ipo_id")
    symbol.add_argument("symbol", nargs="?")
    add("jobs", _cmd_jobs, "show the last run of each job and the IndianAPI quota")
    localdb = add("localdb", _cmd_localdb, "start, stop or check the local development Postgres (no Docker)")
    localdb.add_argument("action", choices=["start", "stop", "status"])

    odds = add("odds", _cmd_odds, "allotment odds, projected subscription and expected value for one IPO")
    odds.add_argument("ipo_id")
    odds.add_argument("--lots", type=int, help="lots per application (default: the minimum bid)")
    odds.add_argument("--apps", type=int, default=1, help="number of applications (one per PAN)")
    odds.add_argument("--gmp", type=float, help="grey market premium in rupees per share")
    odds.add_argument("--gmp-confidence", choices=[c.value for c in GmpConfidence], help="default: low")
    add("multipliers", _cmd_multipliers, "day-N to final subscription multipliers from listed IPOs")
    reservation = add("reservation", _cmd_reservation, "show, set or clear reserved shares typed in from the RHP")
    reservation.add_argument("action", choices=["show", "set", "clear"])
    reservation.add_argument("ipo_id")
    reservation.add_argument("category", nargs="?", choices=["retail", "nii", "qib"])
    reservation.add_argument("shares", nargs="?", type=int)
    reservation.add_argument("--note", help="where the figure came from, e.g. 'RHP p. 12'")
    gmp = add("gmp", _cmd_gmp, "show an IPO's GMP, or set or clear a manual GMP (never calls ipoalerts)")
    gmp.add_argument("action", choices=["show", "set", "clear"])
    gmp.add_argument("ipo_id")
    gmp.add_argument("gmp_rs", nargs="?", type=float, metavar="rupees")
    gmp.add_argument("--at", help="when the manual GMP was seen, 'YYYY-MM-DD HH:MM' in IST (default: now)")
    return parser


def _force_utf8_stdio() -> None:
    # Windows consoles default to a legacy code page; rupee signs and Indic names need UTF-8.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    _force_utf8_stdio()
    args = build_parser().parse_args(argv)
    settings = settings or get_settings()
    configure_logging(settings)
    try:
        return args.handler(args, settings)
    except (ConfigError, MigrationError, LocalDbError, RulesError) as exc:
        print(f"error: {redact(str(exc), settings.secret_values())}", file=sys.stderr)
        return 2
    except psycopg.Error as exc:
        print(f"database error: {redact(str(exc), settings.secret_values())}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
