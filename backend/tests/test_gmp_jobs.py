"""ipoalerts GMP jobs end to end: fake ipoalerts -> budget guard -> real Postgres."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from ipo_intel.analysis import analyse
from ipo_intel.budget import Purpose
from ipo_intel.clients.factory import build_ipoalerts_budget
from ipo_intel.engine.ev import GmpConfidence
from ipo_intel.engine.rules import load_rules
from ipo_intel.gmp import reading_for, set_manual_gmp
from ipo_intel.ingest.jobs import JobStatus
from ipo_intel.ingest.symbols import set_manual_symbol
from ipo_intel.timeutil import ist_at
from tests.fakes import status
from tests.gmp_world import (
    ACME_STORED,
    ALERTS_KEY,
    NORTHSTAR_BEFORE_LISTING,
    gmp_path,
    gmp_paths,
    gmp_world,
    payload,
    series,
    utc,
    HOUR,
)
from tests.indianapi_world import TEST_KEY, World

ACME = gmp_path("ACMETECH", "hourly", "2026-10-23")
SUNRISE = gmp_path("SUNRISE", "hourly", "2026-10-24")
BLUEWAVE = gmp_path("BLUEWAVE", "hourly", "2026-10-19")


@pytest.fixture
def make_world(database, migrated_db_url):
    worlds = []

    def factory(**settings):
        world = gmp_world(database, migrated_db_url, **settings)
        worlds.append(world)
        return world

    yield factory
    for world in worlds:
        world.close()


@pytest.fixture
def world(make_world):
    return make_world()


def _budget(world):
    return build_ipoalerts_budget(world.db, world.settings, clock=world.clock)


def _count(world, sql, *params):
    return world.rows(sql, *params)[0]["n"]


def test_morning_poll_asks_for_open_then_waiting_ipos_and_stores_the_series(world):
    result = world.run("gmp-live", slot="morning")
    assert result.status is JobStatus.OK, result.summary
    # Open mainboard first, then open SME, then the closed IPO that has not listed yet.
    assert gmp_paths(world) == [ACME, SUNRISE, BLUEWAVE]
    gmp_requests = [r for r in world.transport.requests if r.url.path.startswith("/ipos/")]
    assert all(r.url.host == "api.ipoalerts.in" and r.headers["x-api-key"] == ALERTS_KEY for r in gmp_requests)
    assert all("X-API-Key" not in r.headers or r.headers["X-API-Key"] != TEST_KEY for r in gmp_requests)
    assert _count(world, "SELECT count(*) AS n FROM gmp_snapshots WHERE ipo_id = 'acme-tech'") == ACME_STORED
    rows = world.rows("SELECT DISTINCT interval, source FROM gmp_snapshots")
    assert rows == [{"interval": "hourly", "source": "ipoalerts"}]
    usage = _budget(world).usage()
    assert (usage.used_today, usage.by_purpose) == (3, {"live": 3})


def test_afternoon_repolls_only_open_mainboard_and_quick_repeats_are_skipped(world):
    world.run("gmp-live", slot="morning")
    world.transport.requests.clear()
    again = world.run("gmp-live", slot="morning")
    assert gmp_paths(world) == [] and "nothing to poll" in again.summary
    world.clock.advance(hours=5)  # 15:00 IST
    world.run("gmp-live", slot="afternoon")
    assert gmp_paths(world) == [ACME]
    assert _budget(world).usage().used_today == 4


def test_thirteenth_call_of_the_day_is_refused_without_network(world):
    budget = _budget(world)
    for _ in range(11):
        assert budget.try_acquire(Purpose.LIVE).allowed
    result = world.run("gmp-live", slot="morning")
    assert gmp_paths(world) == [ACME]  # call 12 goes out; call 13 never reaches the network
    assert result.status is JobStatus.PARTIAL
    assert "budget refused (daily_cap)" in result.summary and "2 not asked" in result.summary
    assert budget.usage().used_today == 12
    skipped = world.rows("SELECT endpoint, status FROM source_runs WHERE source = 'ipoalerts' AND status <> 'ok'")
    assert skipped == [{"endpoint": "/ipos/SUNRISE/gmp?from=2026-10-24&interval=hourly", "status": "skipped_budget"}]


def test_429_stops_the_run_and_blocks_ipoalerts_until_midnight_ist(world):
    world.transport.routes[ACME] = status(429, {"status": "429", "title": "Too Many Requests", "meta": {"expiresIn": 45}})
    result = world.run("gmp-live", slot="morning")
    assert gmp_paths(world) == [ACME]
    assert "rate limited" in result.summary and "2 not asked" in result.summary
    usage = _budget(world).usage()
    assert usage.blocked_until == ist_at(date(2026, 11, 4), 0)
    world.transport.requests.clear()
    world.clock.advance(hours=5)
    later = world.run("gmp-live", slot="afternoon")
    assert gmp_paths(world) == []
    assert "budget refused (blocked)" in later.summary


def test_a_long_expiry_blocks_for_longer_than_the_day(world):
    world.transport.routes[ACME] = status(429, {"meta": {"expiresIn": 3 * 86400}})
    world.run("gmp-live", slot="morning")
    blocked_until = _budget(world).usage().blocked_until
    assert blocked_until > ist_at(date(2026, 11, 5), 23)


def test_a_bad_symbol_rests_after_three_failures_until_the_symbol_changes(make_world):
    world = make_world(ipoalerts_daily_cap=24, ipoalerts_monthly_cap=700)
    world.transport.routes[ACME] = status(404, {"errors": [{"detail": "IPO not found"}]})
    for _ in range(3):
        world.run("gmp-live", slot="morning")
        world.clock.advance(hours=4)
    assert world.rows("SELECT failures FROM gmp_fetch_state WHERE ipo_id = 'acme-tech'")[0]["failures"] == 3
    world.transport.requests.clear()
    world.run("gmp-live", slot="morning")
    assert ACME not in gmp_paths(world)

    with world.db.connection() as conn:
        set_manual_symbol(conn, "acme-tech", "ACME1", world.clock.now())
    fixed = gmp_path("ACME1", "hourly", "2026-10-23")
    world.transport.routes[fixed] = payload("ACME1", "hourly", [])
    world.transport.requests.clear()
    world.clock.advance(hours=4)
    world.run("gmp-live", slot="morning")
    assert fixed in gmp_paths(world)


def test_backfill_is_daily_newest_first_cut_at_listing_and_resumable(world):
    result = world.run("gmp-backfill")
    assert result.status is JobStatus.OK, result.summary
    assert gmp_paths(world) == [
        gmp_path("NORTHSTAR", "daily", "2026-10-04"),
        gmp_path("KESAR", "daily", "2026-10-03"),
        gmp_path("FLATLINE", "daily", "2026-09-27"),
        gmp_path("DROPCAP", "daily", "2026-09-26"),
    ]
    stored = world.rows("SELECT ts_utc, gmp_rs FROM gmp_snapshots WHERE ipo_id = 'northstar-optics' ORDER BY ts_utc")
    assert len(stored) == NORTHSTAR_BEFORE_LISTING
    assert max(row["ts_utc"] for row in stored) < ist_at(date(2026, 10, 21), 0)
    assert _budget(world).usage().by_purpose == {"backfill": 4}
    world.transport.requests.clear()
    assert world.run("gmp-backfill").summary == "nothing to backfill"
    assert gmp_paths(world) == []


def test_backfill_keeps_to_its_share_and_live_still_gets_the_rest(world):
    budget = _budget(world)
    for _ in range(6):
        assert budget.try_acquire(Purpose.BACKFILL).allowed
    result = world.run("gmp-backfill")
    assert len(gmp_paths(world)) == 2
    assert "budget refused (purpose_cap)" in result.summary
    live = world.run("gmp-live", slot="morning")
    assert live.status is JobStatus.OK
    assert budget.usage().used_today == 11


def test_without_a_key_the_gmp_jobs_skip(database, migrated_db_url):
    world = World(database, migrated_db_url)
    try:
        for job in ("gmp-live", "gmp-backfill"):
            result = world.run(job)
            assert result.status is JobStatus.SKIPPED and "IPOALERTS_KEY is not set" in result.summary
        assert gmp_paths(world) == []
    finally:
        world.close()


def test_the_calculator_uses_the_stored_gmp_and_its_confidence(world):
    world.run("open-details")
    world.run("gmp-live", slot="morning")
    rules = load_rules()
    with world.db.connection() as conn:
        acme = analyse(conn, "acme-tech", rules=rules, today=date(2026, 11, 3), now=world.clock.now())
        sunrise = analyse(conn, "sunrise-agro", rules=rules, today=date(2026, 11, 3), now=world.clock.now())
    assert acme.gmp.source == "ipoalerts" and acme.gmp.confidence is GmpConfidence.HIGH
    assert acme.gmp_used == acme.gmp.gmp_rs and acme.ev.point is not None
    assert acme.gmp.gmp_pct == pytest.approx(acme.gmp.gmp_rs / 100)
    assert sunrise.gmp.confidence is GmpConfidence.LOW and sunrise.ev.point is None


def test_a_manual_gmp_wins_only_while_it_is_the_newest(world):
    world.run("gmp-live", slot="morning")
    with world.db.connection() as conn:
        set_manual_gmp(conn, "acme-tech", Decimal("25.5"), world.clock.now(), world.clock.now())
        ipo = world.ipo("acme-tech")
        reading = reading_for(conn, ipo, world.clock.now())
    assert (reading.source, reading.gmp_rs) == ("manual", 25.5)

    world.clock.advance(hours=5)
    newer = series(utc(2026, 11, 1), utc(2026, 11, 3, 9), every=HOUR, price=21.0)
    world.transport.routes[ACME] = payload("ACMETECH", "hourly", newer)
    world.run("gmp-live", slot="afternoon")
    with world.db.connection() as conn:
        reading = reading_for(conn, world.ipo("acme-tech"), world.clock.now())
    assert reading.source == "ipoalerts" and reading.ts_utc == utc(2026, 11, 3, 9)


def test_the_live_poll_stops_after_listing(world):
    world.clock.advance(days=3)  # Friday 06 Nov: Bluewave listed on the 5th, Acme and Sunrise have closed
    world.run("gmp-live", slot="morning")
    assert BLUEWAVE not in gmp_paths(world)
    assert gmp_paths(world) == [ACME, SUNRISE]
    world.transport.requests.clear()
    world.clock.advance(days=4, hours=4)  # both have listed (09 and 10 Nov)
    assert "nothing to poll" in world.run("gmp-live", slot="morning").summary
    assert timedelta(days=7) < world.clock.now() - utc(2026, 11, 3)
