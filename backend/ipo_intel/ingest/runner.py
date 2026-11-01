"""Runs ingestion jobs with shared resources and records every run in job_runs."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from ipo_intel.alerts import ALERT_JOBS
from ipo_intel.clients.factory import (
    build_indianapi_client,
    build_ipoalerts_budget,
    build_ipoalerts_client,
    build_telegram_client,
)
from ipo_intel.clients.runlog import PgRunRecorder
from ipo_intel.config import Settings, require
from ipo_intel.db import Database
from ipo_intel.indianapi.adapter import IndianApi
from ipo_intel.ingest import gmp_jobs, jobs
from ipo_intel.ingest.jobs import JobContext, JobResult, JobStatus
from ipo_intel.ipoalerts.adapter import IpoAlerts
from ipo_intel.redaction import redact
from ipo_intel.telegram import Telegram
from ipo_intel.timeutil import Clock, SystemClock

log = logging.getLogger(__name__)

SUMMARY_LIMIT = 500
# Jobs that call ipoalerts. They spend the scarce daily budget, so only the
# scheduler runs them, or a person who explicitly says so.
BUDGETED_JOBS = frozenset(gmp_jobs.GMP_JOBS)


def all_jobs() -> dict[str, Callable[..., JobResult]]:
    return {**jobs.JOBS, **gmp_jobs.GMP_JOBS, **ALERT_JOBS}


class JobRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        db: Database | None = None,
        transport: httpx.BaseTransport | None = None,
        gmp_transport: httpx.BaseTransport | None = None,
        telegram_transport: httpx.BaseTransport | None = None,
        clock: Clock | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock or SystemClock()
        self._secrets = settings.secret_values()
        self._owns_db = db is None
        self.db = db or Database(require(settings.database_url, "DATABASE_URL"))
        if self._owns_db:
            self.db.open()
        recorder = PgRunRecorder(self.db)
        self._gmp_client = self._telegram_client = None
        try:
            self._client = build_indianapi_client(settings, recorder, transport=transport, clock=self._clock, sleep=sleep)
            gmp = budget = telegram = None
            if settings.ipoalerts_key is not None:
                budget = build_ipoalerts_budget(self.db, settings, clock=self._clock)
                self._gmp_client = build_ipoalerts_client(
                    settings, recorder, budget, transport=gmp_transport or transport, clock=self._clock
                )
                gmp = IpoAlerts(self._gmp_client)
            if settings.telegram_bot_token is not None and settings.telegram_chat_id is not None:
                self._telegram_client = build_telegram_client(
                    settings, recorder, transport=telegram_transport or transport, clock=self._clock
                )
                telegram = Telegram(self._telegram_client, settings.telegram_chat_id.get_secret_value())
        except Exception:
            self._close_db()
            raise
        self.context = JobContext(
            db=self.db, api=IndianApi(self._client), settings=settings, clock=self._clock, sleep=sleep,
            gmp=gmp, budget=budget, telegram=telegram,
        )

    def close(self) -> None:
        for client in (self._client, self._gmp_client, self._telegram_client):
            if client is not None:
                client.close()
        self._close_db()

    def _close_db(self) -> None:
        if self._owns_db:
            self.db.close()

    def __enter__(self) -> JobRunner:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def run(self, job: str, *, trigger: str = "cli", **kwargs: Any) -> JobResult:
        handler = all_jobs()[job]
        run_id = self._start(job, trigger)
        try:
            result = handler(self.context, **kwargs)
        except Exception as exc:  # one broken run must not take the scheduler down
            log.exception("job %s crashed", job)
            result = JobResult(JobStatus.FAILED, f"crashed: {type(exc).__name__}: {exc}")
        result = JobResult(result.status, redact(result.summary, self._secrets)[:SUMMARY_LIMIT])
        self._finish(run_id, result)
        level = logging.WARNING if result.status is JobStatus.FAILED else logging.INFO
        log.log(level, "job %s %s: %s", job, result.status, result.summary)
        return result

    def _start(self, job: str, trigger: str) -> int:
        with self.db.connection() as conn:
            row = conn.execute(
                "INSERT INTO job_runs (job, trigger, status, started_at) VALUES (%s, %s, 'running', %s) RETURNING id",
                (job, trigger, self._clock.now()),
            ).fetchone()
        return row["id"]

    def _finish(self, run_id: int, result: JobResult) -> None:
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE job_runs SET status = %s, summary = %s, finished_at = %s WHERE id = %s",
                (result.status.value, result.summary, self._clock.now(), run_id),
            )
