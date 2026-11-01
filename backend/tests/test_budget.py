import threading
from datetime import date, datetime, timedelta

import pytest

from ipo_intel.budget import BudgetCaps, BudgetDecision, BudgetGuard, Purpose, Refusal
from ipo_intel.clients.factory import ipoalerts_caps
from ipo_intel.timeutil import UTC, ist_at
from tests.fakes import FixedClock, make_settings

CAPS = ipoalerts_caps(make_settings())  # 12/day, 400/month, backfill at most 8/day
LIVE, BACKFILL = Purpose.LIVE, Purpose.BACKFILL


@pytest.fixture
def clock():
    return FixedClock(ist_at(date(2026, 11, 2), 10, 30))


@pytest.fixture
def guard(database, clock):
    return BudgetGuard(database, "ipoalerts", CAPS, clock)


def acquire(guard, purpose, n):
    return [guard.try_acquire(purpose) for _ in range(n)]


def test_default_caps_come_from_settings():
    assert (CAPS.daily, CAPS.monthly, CAPS.purpose_daily[BACKFILL]) == (12, 400, 8)


def test_twelve_calls_a_day_then_refused(guard):
    decisions = acquire(guard, LIVE, 12)
    assert all(d.allowed for d in decisions)
    assert [d.used_today for d in decisions] == list(range(1, 13))

    thirteenth = guard.try_acquire(LIVE)
    assert not thirteenth.allowed
    assert thirteenth.refusal is Refusal.DAILY_CAP
    assert thirteenth.used_today == 12
    assert guard.usage().used_today == 12  # a refusal spends nothing


def test_backfill_gets_at_most_eight_and_live_gets_the_rest(guard):
    assert all(d.allowed for d in acquire(guard, BACKFILL, 8))
    assert guard.try_acquire(BACKFILL).refusal is Refusal.PURPOSE_CAP
    assert all(d.allowed for d in acquire(guard, LIVE, 4))
    assert guard.try_acquire(LIVE).refusal is Refusal.DAILY_CAP
    assert guard.usage().by_purpose == {"backfill": 8, "live": 4}


def test_live_may_use_the_whole_day_when_backfill_is_idle(guard):
    assert all(d.allowed for d in acquire(guard, LIVE, 12))
    assert guard.try_acquire(BACKFILL).refusal is Refusal.DAILY_CAP


def test_budget_resets_at_ist_midnight(database):
    clock = FixedClock(datetime(2026, 11, 2, 18, 29, 59, tzinfo=UTC))  # 23:59:59 IST, 2 Nov
    guard = BudgetGuard(database, "ipoalerts", CAPS, clock)
    acquire(guard, LIVE, 12)
    assert not guard.try_acquire(LIVE).allowed

    clock.set(datetime(2026, 11, 2, 18, 30, tzinfo=UTC))  # 00:00 IST, 3 Nov
    decision = guard.try_acquire(LIVE)
    assert decision.allowed
    assert decision.day == date(2026, 11, 3)
    assert (decision.used_today, decision.used_month) == (1, 13)


def test_utc_midnight_is_not_a_reset(database):
    clock = FixedClock(datetime(2026, 11, 2, 23, 0, tzinfo=UTC))  # 04:30 IST, 3 Nov
    guard = BudgetGuard(database, "ipoalerts", CAPS, clock)
    acquire(guard, LIVE, 12)
    clock.set(datetime(2026, 11, 3, 0, 30, tzinfo=UTC))  # new UTC day, still 3 Nov in IST
    assert guard.try_acquire(LIVE).refusal is Refusal.DAILY_CAP


def _seed(database, day, count, purpose="live"):
    with database.connection() as conn:
        conn.execute(
            "INSERT INTO api_budget (source, day, month, purpose, count) VALUES ('ipoalerts', %s, %s, %s, %s)",
            (day, day.replace(day=1), purpose, count),
        )


def test_monthly_cap_of_400(database):
    for day in range(1, 29):
        _seed(database, date(2026, 11, day), 14)  # 392
    _seed(database, date(2026, 11, 29), 7)  # 399
    clock = FixedClock(ist_at(date(2026, 11, 30), 10, 30))
    guard = BudgetGuard(database, "ipoalerts", CAPS, clock)

    last = guard.try_acquire(LIVE)
    assert last.allowed and last.used_month == 400
    refused = guard.try_acquire(LIVE)
    assert refused.refusal is Refusal.MONTHLY_CAP
    assert refused.used_today == 1

    clock.set(ist_at(date(2026, 12, 1), 0))  # 30 Nov 18:30 UTC is already December in IST
    december = guard.try_acquire(LIVE)
    assert december.allowed
    assert (december.day, december.used_today, december.used_month) == (date(2026, 12, 1), 1, 1)


def test_block_refuses_without_spending_budget(guard, clock):
    guard.block_until(clock.now() + timedelta(hours=3), "HTTP 429, meta.expiresIn=10800")
    blocked = guard.try_acquire(LIVE)
    assert blocked.refusal is Refusal.BLOCKED
    assert blocked.blocked_until == clock.now() + timedelta(hours=3)
    assert guard.usage().used_today == 0
    assert guard.usage().block_reason == "HTTP 429, meta.expiresIn=10800"

    clock.advance(hours=3, seconds=1)
    assert guard.try_acquire(LIVE).allowed
    assert guard.usage().blocked_until is None


def test_a_shorter_block_does_not_shorten_a_longer_one(guard, clock):
    long_until = clock.now() + timedelta(hours=5)
    guard.block_until(long_until, "first")
    guard.block_until(clock.now() + timedelta(hours=1), "second")
    usage = guard.usage()
    assert usage.blocked_until == long_until
    assert usage.block_reason == "first"


def test_block_requires_an_aware_datetime(guard):
    with pytest.raises(ValueError, match="naive"):
        guard.block_until(datetime(2026, 11, 2, 12, 0), "naive")


def test_sources_have_separate_budgets(database, clock):
    first = BudgetGuard(database, "ipoalerts", CAPS, clock)
    second = BudgetGuard(database, "other", CAPS, clock)
    acquire(first, LIVE, 12)
    assert second.try_acquire(LIVE).allowed


def test_concurrent_reservations_never_overshoot(database, clock):
    guard = BudgetGuard(database, "ipoalerts", CAPS, clock)
    workers = 24
    barrier = threading.Barrier(workers)
    results: list[BudgetDecision] = []
    lock = threading.Lock()

    def reserve():
        barrier.wait()
        decision = guard.try_acquire(LIVE)
        with lock:
            results.append(decision)

    threads = [threading.Thread(target=reserve) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(results) == workers
    assert sum(d.allowed for d in results) == 12
    assert sorted(d.used_today for d in results if d.allowed) == list(range(1, 13))
    assert guard.usage().used_today == 12


@pytest.mark.parametrize(
    "kwargs",
    [
        {"daily": -1, "monthly": 400},
        {"daily": 12, "monthly": -1},
        {"daily": 12, "monthly": 400, "purpose_daily": {BACKFILL: 13}},
    ],
)
def test_invalid_caps_are_rejected(kwargs):
    with pytest.raises(ValueError):
        BudgetCaps(**kwargs)
