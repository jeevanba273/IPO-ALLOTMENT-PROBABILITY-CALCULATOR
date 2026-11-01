"""Call budget for rate-limited external APIs (ipoalerts).

One unit is reserved *before* every budgeted network call. The check and the
increment run in one transaction under a per-source advisory lock, so
concurrent workers can never overshoot a cap. Usage is counted per IST day and
purpose and summed per IST calendar month; a new IST day starts new rows,
which is the 00:00 IST reset.

A reserved unit stays spent even if the call then fails, because the remote
side may have counted it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol

from ipo_intel.db import Database
from ipo_intel.timeutil import Clock, SystemClock, ist_today, month_start, require_aware


class Purpose(StrEnum):
    LIVE = "live"
    BACKFILL = "backfill"


class Refusal(StrEnum):
    BLOCKED = "blocked"
    MONTHLY_CAP = "monthly_cap"
    DAILY_CAP = "daily_cap"
    PURPOSE_CAP = "purpose_cap"


@dataclass(frozen=True)
class BudgetCaps:
    daily: int
    monthly: int
    # Ceilings inside `daily` for specific purposes. Purposes not listed may
    # use whatever is left of the daily cap.
    purpose_daily: Mapping[Purpose, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.daily < 0 or self.monthly < 0:
            raise ValueError("budget caps must be >= 0")
        for purpose, cap in self.purpose_daily.items():
            if not 0 <= cap <= self.daily:
                raise ValueError(f"{purpose} cap {cap} must be between 0 and the daily cap {self.daily}")


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    refusal: Refusal | None
    day: date
    used_today: int
    used_month: int
    used_by_purpose: int
    blocked_until: datetime | None = None

    @property
    def reason(self) -> str:
        return "ok" if self.refusal is None else self.refusal.value


@dataclass(frozen=True)
class BudgetUsage:
    source: str
    day: date
    used_today: int
    used_month: int
    by_purpose: dict[str, int]
    caps: BudgetCaps
    blocked_until: datetime | None
    block_reason: str | None


class CallBudget(Protocol):
    def try_acquire(self, purpose: Purpose) -> BudgetDecision: ...


_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
_ACTIVE_BLOCK = "SELECT blocked_until, reason FROM api_blocks WHERE source = %s AND blocked_until > %s"
_DAY_COUNTS = "SELECT purpose, count FROM api_budget WHERE source = %s AND day = %s"
_MONTH_TOTAL = "SELECT COALESCE(SUM(count), 0) AS total FROM api_budget WHERE source = %s AND month = %s"
_INCREMENT = """
INSERT INTO api_budget (source, day, month, purpose, count)
VALUES (%s, %s, %s, %s, 1)
ON CONFLICT (source, day, purpose)
DO UPDATE SET count = api_budget.count + 1, updated_at = now()
"""
_BLOCK = """
INSERT INTO api_blocks (source, blocked_until, reason)
VALUES (%s, %s, %s)
ON CONFLICT (source) DO UPDATE SET
    reason = CASE WHEN EXCLUDED.blocked_until >= api_blocks.blocked_until
                  THEN EXCLUDED.reason ELSE api_blocks.reason END,
    blocked_until = GREATEST(api_blocks.blocked_until, EXCLUDED.blocked_until),
    created_at = now()
"""


class BudgetGuard:
    def __init__(self, db: Database, source: str, caps: BudgetCaps, clock: Clock | None = None) -> None:
        self._db = db
        self.source = source
        self.caps = caps
        self._clock = clock or SystemClock()

    def try_acquire(self, purpose: Purpose) -> BudgetDecision:
        """Reserve one call for `purpose`, or explain why not. A refusal spends nothing."""
        purpose = Purpose(purpose)
        now = self._clock.now()
        day = ist_today(now)
        month = month_start(day)
        with self._db.connection() as conn, conn.transaction():
            conn.execute(_LOCK, (f"api_budget:{self.source}",))
            block = conn.execute(_ACTIVE_BLOCK, (self.source, now)).fetchone()
            by_purpose = {row["purpose"]: row["count"] for row in conn.execute(_DAY_COUNTS, (self.source, day))}
            used_today = sum(by_purpose.values())
            used_by_purpose = by_purpose.get(purpose.value, 0)
            used_month = conn.execute(_MONTH_TOTAL, (self.source, month)).fetchone()["total"]

            refusal = self._refusal(purpose, block is not None, used_today, used_by_purpose, used_month)
            if refusal is not None:
                return BudgetDecision(
                    allowed=False,
                    refusal=refusal,
                    day=day,
                    used_today=used_today,
                    used_month=used_month,
                    used_by_purpose=used_by_purpose,
                    blocked_until=block["blocked_until"] if block else None,
                )
            conn.execute(_INCREMENT, (self.source, day, month, purpose.value))
        return BudgetDecision(
            allowed=True,
            refusal=None,
            day=day,
            used_today=used_today + 1,
            used_month=used_month + 1,
            used_by_purpose=used_by_purpose + 1,
        )

    def _refusal(
        self, purpose: Purpose, blocked: bool, used_today: int, used_by_purpose: int, used_month: int
    ) -> Refusal | None:
        if blocked:
            return Refusal.BLOCKED
        if used_month >= self.caps.monthly:
            return Refusal.MONTHLY_CAP
        if used_today >= self.caps.daily:
            return Refusal.DAILY_CAP
        purpose_cap = self.caps.purpose_daily.get(purpose)
        if purpose_cap is not None and used_by_purpose >= purpose_cap:
            return Refusal.PURPOSE_CAP
        return None

    def block_until(self, until: datetime, reason: str) -> None:
        """Refuse every call until `until`. An existing later block is kept."""
        with self._db.connection() as conn:
            conn.execute(_BLOCK, (self.source, require_aware(until), reason))

    def usage(self) -> BudgetUsage:
        now = self._clock.now()
        day = ist_today(now)
        with self._db.connection() as conn:
            by_purpose = {row["purpose"]: row["count"] for row in conn.execute(_DAY_COUNTS, (self.source, day))}
            used_month = conn.execute(_MONTH_TOTAL, (self.source, month_start(day))).fetchone()["total"]
            block = conn.execute(_ACTIVE_BLOCK, (self.source, now)).fetchone()
        return BudgetUsage(
            source=self.source,
            day=day,
            used_today=sum(by_purpose.values()),
            used_month=used_month,
            by_purpose=by_purpose,
            caps=self.caps,
            blocked_until=block["blocked_until"] if block else None,
            block_reason=block["reason"] if block else None,
        )
