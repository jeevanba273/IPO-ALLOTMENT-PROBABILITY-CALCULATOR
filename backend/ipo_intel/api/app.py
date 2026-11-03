"""The dashboard's backend: one process that serves the API and the built frontend and runs the scheduler.

Starting it is all that is needed: migrations are applied, the IndianAPI quota
is checked, and every job then runs on its IST schedule. Nothing a browser
does here calls ipoalerts; GMP is only fetched by the schedule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import BaseScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from ipo_intel.api.signin import add_sign_in
from ipo_intel.budget import BudgetGuard
from ipo_intel.clients.factory import build_ipoalerts_budget
from ipo_intel.config import REPO_ROOT, ConfigError, Settings, get_settings, require
from ipo_intel.db import Database, connect
from ipo_intel.engine.rules import Rules, cached_rules
from ipo_intel.ingest.runner import JobRunner
from ipo_intel.migrate import apply_migrations
from ipo_intel.passwords import check_hash
from ipo_intel.scheduler import SchedulerLock, build_scheduler
from ipo_intel.timeutil import IST, Clock, SystemClock

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

_NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>IPO Intelligence</title>
<body style="font-family:system-ui;background:#0e131c;color:#e7eaf1;padding:3rem">
<h1>The dashboard has not been built yet</h1>
<p>Build it with <code>npm install</code> and <code>npm run build</code> in the <code>frontend</code> folder.
The API is running at <a style="color:#f0a63a" href="/api/health">/api/health</a>.</p></body>"""


@dataclass
class Services:
    settings: Settings
    db: Database
    rules: Rules
    clock: Clock
    runner: JobRunner
    budget: BudgetGuard
    scheduler: BaseScheduler | None
    # True when another copy of the app holds the schedule lock on this database.
    scheduler_elsewhere: bool = False


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    runner: JobRunner | None = None,
    clock: Clock | None = None,
    start_scheduler: bool = True,
    migrate: bool = True,
    frontend_dist: Path = FRONTEND_DIST,
) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if migrate:
            with connect(require(settings.database_url, "DATABASE_URL")) as conn:
                apply_migrations(conn, settings.migrations_dir)
        owns_db = db is None
        database = db or Database(require(settings.database_url, "DATABASE_URL"), max_size=8)
        if owns_db:
            database.open()
        owns_runner = runner is None
        job_runner = runner or JobRunner(settings, db=database, clock=clock)
        scheduler = lock = None
        elsewhere = False
        if start_scheduler:
            lock = SchedulerLock(require(settings.database_url, "DATABASE_URL"))
            if lock.acquire():
                scheduler = build_scheduler(job_runner, BackgroundScheduler(timezone=IST))
                scheduler.start()
            else:
                elsewhere = True
        app.state.services = Services(
            settings=settings,
            db=database,
            rules=cached_rules(settings.rules_path),
            clock=clock,
            runner=job_runner,
            budget=build_ipoalerts_budget(database, settings, clock=clock),
            scheduler=scheduler,
            scheduler_elsewhere=elsewhere,
        )
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            if lock is not None:
                lock.release()
            if owns_runner:
                job_runner.close()
            if owns_db:
                database.close()

    app = FastAPI(title="IPO Intelligence", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

    password_hash = None
    if settings.dashboard_password_hash is not None:
        password_hash = settings.dashboard_password_hash.get_secret_value()
        try:
            check_hash(password_hash)
        except ValueError as exc:
            raise ConfigError(f"DASHBOARD_PASSWORD_HASH: {exc}") from None
    add_sign_in(app, settings.dashboard_user, password_hash, clock)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        """For the host's health checks; open even when sign-in is on, and reveals nothing."""
        return {"ok": True}

    from ipo_intel.api.routes import router

    app.include_router(router, prefix="/api")

    dist = frontend_dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "no such API route")
        if path:
            target = (dist / path).resolve()
            if target.is_file() and dist in target.parents:
                return FileResponse(target)
        index = dist / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(_NOT_BUILT, status_code=503)

    return app
