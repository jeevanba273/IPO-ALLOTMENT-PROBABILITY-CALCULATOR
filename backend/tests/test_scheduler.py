from datetime import datetime

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from ipo_intel.scheduler import build_scheduler, job_triggers, next_runs
from ipo_intel.timeutil import IST


def ist(*args):
    return datetime(*args, tzinfo=IST)


def _next(job, after):
    return job_triggers()[job].get_next_fire_time(None, after)


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        # Tuesday: every 2h inside 09:00-17:00 ...
        (ist(2026, 11, 3, 8, 0), ist(2026, 11, 3, 9, 0)),
        (ist(2026, 11, 3, 9, 0, 1), ist(2026, 11, 3, 11, 0)),
        (ist(2026, 11, 3, 15, 30), ist(2026, 11, 3, 17, 0)),
        # ... then every 6h outside it.
        (ist(2026, 11, 3, 17, 0, 1), ist(2026, 11, 3, 21, 0)),
        (ist(2026, 11, 3, 21, 0, 1), ist(2026, 11, 4, 3, 0)),
        (ist(2026, 11, 4, 3, 0, 1), ist(2026, 11, 4, 9, 0)),
        # Weekend: every 6h all day.
        (ist(2026, 11, 6, 21, 0, 1), ist(2026, 11, 7, 3, 0)),
        (ist(2026, 11, 7, 9, 0, 1), ist(2026, 11, 7, 15, 0)),
        (ist(2026, 11, 8, 21, 0, 1), ist(2026, 11, 9, 3, 0)),
    ],
)
def test_open_details_schedule(after, expected):
    assert _next("open-details", after) == expected


def test_master_listed_and_usage_schedules():
    assert _next("master", ist(2026, 11, 3, 9, 0)) == ist(2026, 11, 3, 14, 45)
    assert _next("master", ist(2026, 11, 3, 21, 0)) == ist(2026, 11, 4, 2, 45)
    assert _next("listed", ist(2026, 11, 3, 19, 0)) == ist(2026, 11, 3, 20, 0)
    assert _next("usage", ist(2026, 11, 3, 8, 0)) == ist(2026, 11, 4, 7, 0)


def test_times_are_ist_even_when_asked_in_utc():
    utc_morning = datetime.fromisoformat("2026-11-03T03:00:00+00:00")  # 08:30 IST
    assert next_runs(utc_morning)["open-details"] == ist(2026, 11, 3, 9, 0)


class _Runner:
    def run(self, job, *, trigger="cli", **kwargs):
        raise AssertionError("not started in this test")


def test_build_scheduler_registers_every_job_plus_a_startup_usage_check():
    scheduler = build_scheduler(_Runner(), BackgroundScheduler(timezone=IST))
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {
        "usage", "master", "open-details", "listed", "usage-startup",
        "gmp-live-morning", "gmp-live-afternoon", "gmp-backfill", "alerts", "alerts-startup",
    }
    assert jobs["usage-startup"].args == ("usage",)
    assert jobs["usage-startup"].kwargs == {"trigger": "startup"}
    assert jobs["master"].kwargs == {"trigger": "schedule"}
    assert jobs["gmp-live-morning"].args == ("gmp-live",)
    assert jobs["gmp-live-morning"].kwargs == {"trigger": "schedule", "slot": "morning"}
    assert jobs["gmp-live-afternoon"].kwargs == {"trigger": "schedule", "slot": "afternoon"}
    assert "backfill" not in jobs


def test_ipoalerts_is_never_called_at_startup():
    scheduler = build_scheduler(_Runner(), BackgroundScheduler(timezone=IST))
    one_off = [job for job in scheduler.get_jobs() if job.trigger.__class__.__name__ == "DateTrigger"]
    assert sorted(job.args for job in one_off) == [("alerts",), ("usage",)]
    assert not any(job.args[0].startswith("gmp") for job in one_off)


def test_alert_times():
    assert _next("alerts", ist(2026, 11, 3, 10, 0)) == ist(2026, 11, 3, 12, 5)
    assert _next("alerts", ist(2026, 11, 3, 18, 6)) == ist(2026, 11, 3, 20, 20)
    assert _next("alerts", ist(2026, 11, 3, 20, 21)) == ist(2026, 11, 4, 7, 10)


def test_gmp_schedule():
    assert _next("gmp-live-morning", ist(2026, 11, 3, 9, 0)) == ist(2026, 11, 3, 10, 30)
    assert _next("gmp-live-afternoon", ist(2026, 11, 3, 10, 31)) == ist(2026, 11, 3, 15, 30)
    # Weekends too: an IPO that opened on Friday is still open on Saturday.
    assert _next("gmp-live-morning", ist(2026, 11, 7, 11, 0)) == ist(2026, 11, 8, 10, 30)
    assert _next("gmp-backfill", ist(2026, 11, 3, 16, 0)) == ist(2026, 11, 3, 21, 0)
