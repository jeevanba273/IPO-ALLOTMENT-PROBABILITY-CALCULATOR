"""Projected final subscription from day-N to final multipliers of listed IPOs.

For each listed IPO with a complete history, the multiplier for bidding day N
and a category is final times / day-N times. A live IPO's day-N figure times
those multipliers (same board, category and day) gives the projected final:
min, median and max. Only IPOs whose bidding ran past day N count, so a
3-day issue is not compared with 2-day ones that had already closed.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from ipo_intel.engine.rules import Rules

NOT_ENOUGH_HISTORY = "not enough history"
CATEGORIES = ("retail", "nii", "qib", "overall")


@dataclass(frozen=True)
class History:
    """One listed IPO's cumulative subscription at the end of each bidding day, oldest first."""

    ipo_id: str
    board: str  # 'mainboard' or 'sme'
    days: tuple[Mapping[str, float], ...]


class ProjectionStatus(StrEnum):
    OK = "ok"
    FINAL = "final"  # bidding is over (or this is the last day): the figure is final
    NOT_ENOUGH_HISTORY = "not_enough_history"
    NO_BIDS_YET = "no_bids_yet"


@dataclass(frozen=True)
class Projection:
    """Projected final times subscribed: min / median / max, plus the middle half (q1..q3).

    The extremes come from a handful of outliers, so odds are quoted over q1..q3.
    """

    status: ProjectionStatus
    current: float
    day: int
    samples: int
    low: float | None = None
    median: float | None = None
    high: float | None = None
    q1: float | None = None
    q3: float | None = None

    @property
    def message(self) -> str:
        return {
            ProjectionStatus.OK: f"from {self.samples} listed IPOs",
            ProjectionStatus.FINAL: "final figure",
            ProjectionStatus.NOT_ENOUGH_HISTORY: NOT_ENOUGH_HISTORY,
            ProjectionStatus.NO_BIDS_YET: "too few bids yet to project",
        }[self.status]


def multipliers(histories: Iterable[History], *, board: str, category: str, day: int, min_base: float) -> list[float]:
    """Day `day` (1-based) to final multipliers for one board and category."""
    if day < 1:
        raise ValueError("bidding days start at 1")
    found = []
    for history in histories:
        if history.board != board or len(history.days) <= day:
            continue
        base = history.days[day - 1].get(category)
        final = history.days[-1].get(category)
        if base is not None and final is not None and base >= min_base:
            found.append(final / base)
    return found


def project(current: float, *, day: int, final: bool, samples: list[float], rules: Rules) -> Projection:
    if current < 0:
        raise ValueError("times subscribed cannot be negative")
    if final:
        return Projection(ProjectionStatus.FINAL, current, day, 0, current, current, current, current, current)
    if current < rules.projection.min_base_times:
        return Projection(ProjectionStatus.NO_BIDS_YET, current, day, len(samples))
    if len(samples) < max(2, rules.projection.min_samples):
        return Projection(ProjectionStatus.NOT_ENOUGH_HISTORY, current, day, len(samples))
    q1, _, q3 = statistics.quantiles(samples, n=4, method="inclusive")
    return Projection(
        ProjectionStatus.OK,
        current,
        day,
        len(samples),
        low=current * min(samples),
        median=current * statistics.median(samples),
        high=current * max(samples),
        q1=current * q1,
        q3=current * q3,
    )


@dataclass(frozen=True)
class MultiplierRow:
    board: str
    category: str
    day: int
    samples: int
    low: float | None
    median: float | None
    high: float | None


def multiplier_table(histories: list[History], rules: Rules, max_day: int = 4) -> list[MultiplierRow]:
    """Every board x category x day, with its sample count, for calibration views."""
    rows = []
    for board in ("mainboard", "sme"):
        for category in CATEGORIES:
            for day in range(1, max_day + 1):
                found = multipliers(
                    histories, board=board, category=category, day=day, min_base=rules.projection.min_base_times
                )
                if not found and day > 1:
                    continue
                rows.append(
                    MultiplierRow(
                        board, category, day, len(found),
                        min(found) if found else None,
                        statistics.median(found) if found else None,
                        max(found) if found else None,
                    )
                )
    return rows
