"""IndianAPI quota gate (10,000-request hard limit).

The remaining count comes from the last GET /usage reading minus every
IndianAPI attempt recorded in source_runs since then (counted even if it
failed, to stay on the safe side). Below INDIANAPI_PAUSE_BACKFILL_BELOW the
backfill stops; below INDIANAPI_ESSENTIAL_ONLY_BELOW only essential calls run
(the usage check, the open list and open IPO details).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ipo_intel.clients.factory import INDIANAPI
from ipo_intel.config import Settings
from ipo_intel.db import Database


class CallKind(StrEnum):
    ESSENTIAL = "essential"  # open list, open IPO details
    ROUTINE = "routine"  # other lists, the daily GET /ipo snapshot
    BACKFILL = "backfill"


class QuotaLevel(StrEnum):
    UNKNOWN = "unknown"  # no /usage reading yet
    NORMAL = "normal"
    BACKFILL_PAUSED = "backfill_paused"
    ESSENTIAL_ONLY = "essential_only"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class QuotaState:
    level: QuotaLevel
    remaining: int | None  # estimate
    hard_limit: int | None
    checked_at: datetime | None

    def allows(self, kind: CallKind) -> bool:
        if self.level is QuotaLevel.EXHAUSTED:
            return False
        if kind is CallKind.ESSENTIAL:
            return True
        if kind is CallKind.ROUTINE:
            return self.level is not QuotaLevel.ESSENTIAL_ONLY
        # The backfill only runs on a known, healthy quota.
        return self.level is QuotaLevel.NORMAL

    def describe(self) -> str:
        if self.remaining is None:
            return "IndianAPI quota unknown (no /usage reading yet)"
        limit = f" of {self.hard_limit}" if self.hard_limit else ""
        return f"about {self.remaining}{limit} IndianAPI requests left ({self.level.value})"


_ESTIMATE = """
SELECT u.checked_at, u.hard_limit, u.remaining,
       (SELECT count(*) FROM source_runs r
        WHERE r.source = u.source AND r.ran_at > u.checked_at AND r.endpoint <> '/usage'
          AND r.status NOT IN ('skipped_budget', 'skipped_blocked')) AS spent_since
FROM api_usage u
WHERE u.source = %s
ORDER BY u.checked_at DESC
LIMIT 1
"""


class QuotaGate:
    def __init__(self, db: Database, settings: Settings, source: str = INDIANAPI) -> None:
        self._db = db
        self._source = source
        self._pause_backfill_below = settings.indianapi_pause_backfill_below
        self._essential_only_below = settings.indianapi_essential_only_below

    def state(self) -> QuotaState:
        with self._db.connection() as conn:
            row = conn.execute(_ESTIMATE, (self._source,)).fetchone()
        if row is None:
            return QuotaState(QuotaLevel.UNKNOWN, None, None, None)
        remaining = row["remaining"] - row["spent_since"]
        return QuotaState(self._level(remaining), remaining, row["hard_limit"], row["checked_at"])

    def _level(self, remaining: int) -> QuotaLevel:
        if remaining <= 0:
            return QuotaLevel.EXHAUSTED
        if remaining < self._essential_only_below:
            return QuotaLevel.ESSENTIAL_ONLY
        if remaining < self._pause_backfill_below:
            return QuotaLevel.BACKFILL_PAUSED
        return QuotaLevel.NORMAL
