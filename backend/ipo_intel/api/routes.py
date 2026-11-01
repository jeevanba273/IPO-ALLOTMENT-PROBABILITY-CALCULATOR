"""API routes. All reads come from the database; writes are the manual fixes (symbol, GMP, reserved shares)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ipo_intel import queries
from ipo_intel.analysis import analyse, clear_reservation_override, load_histories, set_reservation_override
from ipo_intel.api.views import analysis_view, day, gmp_series, instant, subscription_history, summaries
from ipo_intel.calibration import calibration_rows, summarise
from ipo_intel.clients.runlog import recent_runs
from ipo_intel.engine.ev import GmpConfidence
from ipo_intel.engine.projection import multiplier_table
from ipo_intel.engine.reservation import VERIFY_RHP
from ipo_intel.gmp import clear_manual_gmp, set_manual_gmp
from ipo_intel.ingest.quota import QuotaGate
from ipo_intel.ingest.runner import BUDGETED_JOBS, all_jobs
from ipo_intel.ingest.symbols import clear_manual_symbol, set_manual_symbol
from ipo_intel.scheduler import schedule
from ipo_intel.timeutil import ist_today

router = APIRouter()


def _svc(request: Request):
    return request.app.state.services


def _today(svc):
    return ist_today(svc.clock.now())


def _rows_to_summaries(svc, conn, rows):
    return summaries(conn, rows, today=_today(svc), now=svc.clock.now(), rules=svc.rules)


# --- reads -------------------------------------------------------------------------------


@router.get("/ipos")
def list_ipos(request: Request) -> dict:
    """Every IPO the app knows, newest bidding first. The page filters and groups them."""
    svc = _svc(request)
    with svc.db.connection() as conn:
        items = _rows_to_summaries(svc, conn, queries.list_ipos(conn))
    return {"today": day(_today(svc)), "now": instant(svc.clock.now()), "ipos": items}


def _analysis(svc, conn, ipo_id: str, **kwargs) -> dict | None:
    try:
        a = analyse(conn, ipo_id, rules=svc.rules, today=_today(svc), now=svc.clock.now(), **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        if kwargs:
            raise HTTPException(422, str(exc)) from None
        return None  # price band or lot size not announced yet
    return analysis_view(a)


@router.get("/ipos/{ipo_id}")
def ipo_detail(request: Request, ipo_id: str) -> dict:
    svc = _svc(request)
    with svc.db.connection() as conn:
        row = queries.get_ipo(conn, ipo_id)
        if row is None:
            raise HTTPException(404, f"no IPO with id {ipo_id!r}")
        summary = _rows_to_summaries(svc, conn, [row])[0]
        api_reserved = queries.reserved_shares(conn, ipo_id)
        overrides = {
            r["category"]: r
            for r in conn.execute(
                "SELECT category, reserved_shares, note, updated_at FROM reservation_overrides WHERE ipo_id = %s",
                (ipo_id,),
            )
        }
        detail = {
            **summary,
            "subscription_history": subscription_history(conn, ipo_id),
            "gmp_series": gmp_series(conn, ipo_id),
            "symbols": [{"symbol": s["symbol"], "source": s["source"]} for s in queries.symbols_for(conn, ipo_id)],
            "reservation": [
                {
                    "category": category,
                    "indianapi": api_reserved.get(category),
                    "manual": overrides[category]["reserved_shares"] if category in overrides else None,
                    "note": overrides[category]["note"] if category in overrides else None,
                }
                for category in ("retail", "nii", "qib")
            ],
            "reservation_warning": None if {"retail", "nii", "qib"} <= overrides.keys() else VERIFY_RHP,
            "analysis": _analysis(svc, conn, ipo_id),
        }
    return detail


@router.get("/ipos/{ipo_id}/odds")
def ipo_odds(
    request: Request,
    ipo_id: str,
    lots: int | None = Query(None, ge=1, le=100_000),
    apps: int = Query(1, ge=1, le=100),
    gmp: float | None = Query(None, ge=-100_000, le=100_000),
    confidence: Literal["high", "low"] | None = None,
) -> dict:
    """The calculator. Reads only; a GMP given here is a what-if and is not stored."""
    svc = _svc(request)
    with svc.db.connection() as conn:
        return _analysis(
            svc, conn, ipo_id, lots=lots, applications=apps, gmp_rs=gmp,
            gmp_confidence=GmpConfidence(confidence) if confidence else None,
        )


@router.get("/calibration")
def calibration(request: Request) -> dict:
    svc = _svc(request)
    today = _today(svc)
    with svc.db.connection() as conn:
        rows = calibration_rows(conn, today)
        histories = load_histories(conn, today)
    table = multiplier_table(histories, svc.rules)
    return {
        "rows": [
            {**r, "listing_date": day(r["listing_date"]), "gmp_ts": instant(r["gmp_ts"])} for r in rows
        ],
        "summary": summarise(rows),
        "histories": {board: sum(1 for h in histories if h.board == board) for board in ("mainboard", "sme")},
        "min_samples": svc.rules.projection.min_samples,
        "multipliers": [
            {"board": m.board, "category": m.category, "day": m.day, "samples": m.samples,
             "low": m.low, "median": m.median, "high": m.high}
            for m in table
        ],
    }


@router.get("/health")
def health(request: Request) -> dict:
    svc = _svc(request)
    now = svc.clock.now()
    quota = QuotaGate(svc.db, svc.settings).state()
    usage = svc.budget.usage()
    with svc.db.connection() as conn:
        last = {r["job"]: r for r in queries.last_job_runs(conn)}
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM ipos) AS ipos, "
            "       (SELECT count(*) FROM ipos WHERE detail_fetched_at IS NOT NULL) AS with_details, "
            "       (SELECT count(*) FROM subscription_snapshots) AS subscription_rows, "
            "       (SELECT count(*) FROM gmp_snapshots) AS gmp_points"
        ).fetchone()
    next_by_id = {}
    if svc.scheduler is not None:
        next_by_id = {job.id: job.next_run_time for job in svc.scheduler.get_jobs()}
    entries = schedule()
    next_by_job: dict[str, datetime] = {}
    for entry in entries:
        when = next_by_id.get(entry.id) or entry.trigger.get_next_fire_time(None, now)
        if when and (entry.job not in next_by_job or when < next_by_job[entry.job]):
            next_by_job[entry.job] = when
    jobs = []
    for name in all_jobs():
        run = last.get(name)
        jobs.append({
            "job": name,
            "budgeted": name in BUDGETED_JOBS,
            "scheduled": name in next_by_job,
            "next_run": instant(next_by_job.get(name)),
            "status": run["status"] if run else None,
            "trigger": run["trigger"] if run else None,
            "started_at": instant(run["started_at"]) if run else None,
            "finished_at": instant(run["finished_at"]) if run else None,
            "summary": run["summary"] if run else None,
        })
    calls = [
        {
            "source": r["source"], "endpoint": r["endpoint"], "purpose": r["purpose"], "status": r["status"],
            "http_status": r["http_status"], "duration_ms": r["duration_ms"], "attempt": r["attempt"],
            "ran_at": instant(r["ran_at"]),
        }
        for r in recent_runs(svc.db, limit=40)
    ]
    return {
        "now": instant(now),
        "today": day(_today(svc)),
        "scheduler_running": bool(svc.scheduler is not None and svc.scheduler.running),
        "scheduler_elsewhere": svc.scheduler_elsewhere,
        "telegram_configured": svc.settings.telegram_bot_token is not None and svc.settings.telegram_chat_id is not None,
        "indianapi": {
            "remaining": quota.remaining,
            "hard_limit": quota.hard_limit,
            "level": quota.level.value,
            "description": quota.describe(),
            "checked_at": instant(quota.checked_at),
            "pause_backfill_below": svc.settings.indianapi_pause_backfill_below,
            "essential_only_below": svc.settings.indianapi_essential_only_below,
        },
        "ipoalerts": {
            "configured": svc.settings.ipoalerts_key is not None,
            "day": day(usage.day),
            "used_today": usage.used_today,
            "daily_cap": usage.caps.daily,
            "live_today": usage.by_purpose.get("live", 0),
            "backfill_today": usage.by_purpose.get("backfill", 0),
            "backfill_cap": svc.settings.ipoalerts_backfill_daily_cap,
            "used_month": usage.used_month,
            "monthly_cap": usage.caps.monthly,
            "blocked_until": instant(usage.blocked_until),
            "block_reason": usage.block_reason,
        },
        "jobs": jobs,
        "recent_calls": calls,
        "counts": dict(counts),
    }


@router.get("/settings")
def settings_view(request: Request) -> dict:
    svc = _svc(request)
    return {"rules": svc.rules.model_dump(), "rules_path": str(svc.settings.rules_path)}


# --- manual fixes ----------------------------------------------------------------------------


class SymbolIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)


class GmpIn(BaseModel):
    gmp_rs: float = Field(ge=-100_000, le=100_000)
    at: datetime | None = None  # when it was seen; default now. Must carry a time zone.


class ReservationIn(BaseModel):
    shares: int = Field(gt=0, le=10**12)
    note: str | None = Field(default=None, max_length=200)


def _write(fn):
    try:
        return fn()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.put("/ipos/{ipo_id}/symbol")
def put_symbol(request: Request, ipo_id: str, body: SymbolIn) -> dict:
    svc = _svc(request)
    with svc.db.connection() as conn:
        symbol = _write(lambda: set_manual_symbol(conn, ipo_id, body.symbol, svc.clock.now()))
    return {"ipo_id": ipo_id, "symbol": symbol, "source": "manual"}


@router.delete("/ipos/{ipo_id}/symbol")
def delete_symbol(request: Request, ipo_id: str) -> dict:
    svc = _svc(request)
    with svc.db.connection() as conn:
        removed = clear_manual_symbol(conn, ipo_id)
    return {"ipo_id": ipo_id, "removed": removed}


@router.post("/ipos/{ipo_id}/gmp")
def post_gmp(request: Request, ipo_id: str, body: GmpIn) -> dict:
    svc = _svc(request)
    now = svc.clock.now()
    at = body.at or now
    if at.tzinfo is None:
        raise HTTPException(422, "at must include a time zone, e.g. 2026-11-03T09:45:00+05:30")
    if at > now:
        raise HTTPException(422, "a GMP cannot be from the future")
    with svc.db.connection() as conn:
        _write(lambda: set_manual_gmp(conn, ipo_id, Decimal(str(body.gmp_rs)), at, now))
    return {"ipo_id": ipo_id, "gmp_rs": body.gmp_rs, "at": instant(at), "source": "manual"}


@router.delete("/ipos/{ipo_id}/gmp")
def delete_gmp(request: Request, ipo_id: str) -> dict:
    svc = _svc(request)
    with svc.db.connection() as conn:
        removed = clear_manual_gmp(conn, ipo_id)
    return {"ipo_id": ipo_id, "removed": removed}


@router.put("/ipos/{ipo_id}/reservation/{category}")
def put_reservation(request: Request, ipo_id: str, category: Literal["retail", "nii", "qib"], body: ReservationIn) -> dict:
    svc = _svc(request)
    with svc.db.connection() as conn:
        _write(lambda: set_reservation_override(conn, ipo_id, category, body.shares, body.note, svc.clock.now()))
    return {"ipo_id": ipo_id, "category": category, "shares": body.shares}


@router.delete("/ipos/{ipo_id}/reservation/{category}")
def delete_reservation(request: Request, ipo_id: str, category: Literal["retail", "nii", "qib"]) -> dict:
    svc = _svc(request)
    with svc.db.connection() as conn:
        removed = clear_reservation_override(conn, ipo_id, category)
    return {"ipo_id": ipo_id, "category": category, "removed": removed}


@router.post("/jobs/{job}/run", status_code=202)
def run_job(request: Request, job: str) -> dict:
    """Start an IndianAPI job now. GMP jobs are refused: only their schedule may spend ipoalerts calls."""
    svc = _svc(request)
    if job not in all_jobs():
        raise HTTPException(404, f"no job called {job!r}")
    if job in BUDGETED_JOBS:
        raise HTTPException(403, f"{job} spends the ipoalerts budget, so only its schedule runs it")
    if svc.scheduler is not None and svc.scheduler.running:
        svc.scheduler.add_job(
            svc.runner.run, args=[job], kwargs={"trigger": "dashboard"}, id=f"dashboard-{job}", replace_existing=True
        )
        return {"job": job, "queued": True}
    result = svc.runner.run(job, trigger="dashboard")
    return {"job": job, "queued": False, "status": result.status.value, "summary": result.summary}
