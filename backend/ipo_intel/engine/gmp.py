"""Which GMP to use and how far to trust it.

The latest point wins, whatever its source, so a manual entry beats ipoalerts
only while it is the newer one. Confidence:

* HIGH: a mainboard IPO with 3 or more points in the 48 hours before `as_of`
  and the latest one at most 24 hours old
* LOW: any SME IPO (SME grey markets are thin: Sollfege's GMP implied 18%
  against an actual 90%), or fewer than 3 recent points, or a stale latest point
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ipo_intel.engine.ev import GmpConfidence
from ipo_intel.timeutil import require_aware

HIGH_MIN_POINTS = 3
RECENT_WINDOW = timedelta(hours=48)
STALE_AFTER = timedelta(hours=24)


@dataclass(frozen=True)
class GmpObservation:
    ts_utc: datetime
    gmp_rs: float
    source: str  # 'ipoalerts' or 'manual'


@dataclass(frozen=True)
class GmpReading:
    gmp_rs: float
    ts_utc: datetime
    source: str
    confidence: GmpConfidence
    recent_points: int  # distinct timestamps in the 48 hours before as_of
    gmp_pct: float | None  # gmp / cut-off
    age: timedelta


def gmp_confidence(board: str, timestamps: Iterable[datetime], as_of: datetime) -> GmpConfidence:
    as_of = require_aware(as_of)
    seen = sorted({ts for ts in timestamps if ts <= as_of})
    if board == "sme" or not seen:
        return GmpConfidence.LOW
    recent = [ts for ts in seen if ts >= as_of - RECENT_WINDOW]
    if len(recent) < HIGH_MIN_POINTS or as_of - seen[-1] > STALE_AFTER:
        return GmpConfidence.LOW
    return GmpConfidence.HIGH


def latest_gmp(
    observations: Iterable[GmpObservation], *, board: str, cutoff: float | None, as_of: datetime
) -> GmpReading | None:
    """The newest observation at or before `as_of`; on a tie the manual entry wins."""
    as_of = require_aware(as_of)
    usable = [o for o in observations if o.ts_utc <= as_of]
    if not usable:
        return None
    newest = max(usable, key=lambda o: (o.ts_utc, o.source == "manual"))
    timestamps = [o.ts_utc for o in usable]
    recent = len({ts for ts in timestamps if ts >= as_of - RECENT_WINDOW})
    return GmpReading(
        gmp_rs=newest.gmp_rs,
        ts_utc=newest.ts_utc,
        source=newest.source,
        confidence=gmp_confidence(board, timestamps, as_of),
        recent_points=recent,
        gmp_pct=newest.gmp_rs / cutoff if cutoff else None,
        age=as_of - newest.ts_utc,
    )
