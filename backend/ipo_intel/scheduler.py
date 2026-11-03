"""APScheduler setup for every job. All times are IST.

IndianAPI:
* master        every 6h: 02:45, 08:45, 14:45, 20:45 (08:45 lands before the first open-details run)
* open-details  weekdays every 2h from 09:00 to 17:00, otherwise every 6h (03:00, 09:00, 15:00, 21:00)
* listed        daily at 20:00
* usage         daily at 07:00, and once at startup
* backfill      not scheduled; start it from the Health page or with `ipo-intel run backfill`

ipoalerts (budgeted; never at startup):
* gmp-live      10:30 every open and not-yet-listed IPO, 15:30 open mainboard IPOs again
* gmp-backfill  21:00, with what the live polls left (at most 8)

Telegram:
* alerts        07:10, 09:05, 12:05, 17:05, 18:05, 20:20, and a minute after startup

Only one process may run the schedule against a database: SchedulerLock holds a
Postgres advisory lock for as long as the schedule runs. A second copy of the
app still serves the dashboard but leaves the jobs to the first, and takes them
over once the first lets go (as the old copy does at the end of a deploy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

from ipo_intel.ingest.runner import JobRunner
from ipo_intel.timeutil import IST, utc_now

JOB_DEFAULTS = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 900}
# Key of the session-level advisory lock that marks the one schedule runner.
SCHEDULER_LOCK_KEY = 4_812_330_018


@dataclass(frozen=True)
class Scheduled:
    id: str
    job: str
    trigger: BaseTrigger
    kwargs: dict = field(default_factory=dict)


def _at(*times: tuple[int, int]) -> BaseTrigger:
    return OrTrigger([CronTrigger(hour=h, minute=m, timezone=IST) for h, m in times])


def schedule() -> list[Scheduled]:
    return [
        Scheduled("usage", "usage", CronTrigger(hour=7, minute=0, timezone=IST)),
        Scheduled("master", "master", CronTrigger(hour="2,8,14,20", minute=45, timezone=IST)),
        Scheduled(
            "open-details",
            "open-details",
            OrTrigger(
                [
                    CronTrigger(day_of_week="mon-fri", hour="9-17/2", minute=0, timezone=IST),
                    CronTrigger(day_of_week="mon-fri", hour="3,21", minute=0, timezone=IST),
                    CronTrigger(day_of_week="sat,sun", hour="3,9,15,21", minute=0, timezone=IST),
                ]
            ),
        ),
        Scheduled("listed", "listed", CronTrigger(hour=20, minute=0, timezone=IST)),
        Scheduled("gmp-live-morning", "gmp-live", CronTrigger(hour=10, minute=30, timezone=IST), {"slot": "morning"}),
        Scheduled("gmp-live-afternoon", "gmp-live", CronTrigger(hour=15, minute=30, timezone=IST), {"slot": "afternoon"}),
        Scheduled("gmp-backfill", "gmp-backfill", CronTrigger(hour=21, minute=0, timezone=IST)),
        Scheduled("alerts", "alerts", _at((7, 10), (9, 5), (12, 5), (17, 5), (18, 5), (20, 20))),
    ]


def job_triggers() -> dict[str, BaseTrigger]:
    return {entry.id: entry.trigger for entry in schedule()}


def next_runs(now: datetime) -> dict[str, datetime]:
    """When each scheduled entry fires next after `now` (aware), in IST."""
    return {name: trigger.get_next_fire_time(None, now) for name, trigger in job_triggers().items()}


def build_scheduler(runner: JobRunner, scheduler: BaseScheduler | None = None) -> BaseScheduler:
    scheduler = scheduler or BlockingScheduler(timezone=IST)
    scheduler.configure(job_defaults=JOB_DEFAULTS, timezone=IST)
    for entry in schedule():
        scheduler.add_job(
            runner.run, entry.trigger, args=[entry.job], kwargs={"trigger": "schedule", **entry.kwargs},
            id=entry.id, name=entry.id,
        )
    # The IndianAPI quota is checked on startup, and due alerts go out a minute later.
    # ipoalerts is never called on startup.
    scheduler.add_job(runner.run, "date", args=["usage"], kwargs={"trigger": "startup"}, id="usage-startup")
    scheduler.add_job(
        runner.run, "date", run_date=utc_now() + timedelta(minutes=1), args=["alerts"],
        kwargs={"trigger": "startup"}, id="alerts-startup",
    )
    return scheduler


class SchedulerLock:
    """Held while this process runs the schedule. The lock lives as long as its connection."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._conn: psycopg.Connection | None = None

    def acquire(self) -> bool:
        if self._conn is not None:
            return True
        conn = psycopg.connect(self._url, autocommit=True)
        got = conn.execute("SELECT pg_try_advisory_lock(%s)", (SCHEDULER_LOCK_KEY,)).fetchone()[0]
        if got:
            self._conn = conn
        else:
            conn.close()
        return bool(got)

    @property
    def held(self) -> bool:
        return self._conn is not None and not self._conn.closed

    def release(self) -> None:
        if self._conn is not None:
            self._conn.close()  # a session-level advisory lock ends with its connection
            self._conn = None
