"""Allotment odds.

Retail (lottery for the minimum bid): with `times` the retail subscription, the
chance one application gets the minimum bid is 1/times if every applicant bid
the minimum. That is a LOWER bound: applicants who bid more mean fewer of
them, so the chance rises to avg_min_bids/times. times <= 1 means everyone is
allotted in full.

NII (proportionate, as rules_v1.yaml says): expected shares = shares applied /
times; whole lots are allotted and the fraction is settled by lottery, so
P(at least one lot) = min(1, expected lots).

Several applications (different PANs) are independent draws: P(any) = 1 - (1 - p)^n.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Odds:
    """A probability range: low assumes the most competition, high the least."""

    low: float
    high: float

    def __post_init__(self) -> None:
        if not 0 <= self.low <= self.high <= 1:
            raise ValueError(f"bad odds range {self.low!r}..{self.high!r}")


@dataclass(frozen=True)
class NiiOutcome:
    expected_shares: float
    expected_lots: float
    p_at_least_one_lot: float


def _check_times(times: float) -> None:
    if times < 0:
        raise ValueError("times subscribed cannot be negative")


def retail_odds(times: float, avg_min_bids_per_applicant: float) -> Odds:
    _check_times(times)
    if avg_min_bids_per_applicant < 1:
        raise ValueError("average application size cannot be below the minimum bid")
    if times <= 1:
        return Odds(1.0, 1.0)
    return Odds(1 / times, min(1.0, avg_min_bids_per_applicant / times))


def retail_applications(applied_shares: int, min_qty: int, avg_min_bids_per_applicant: float) -> tuple[float, float]:
    """Estimated number of retail applications: (fewest, most)."""
    most = applied_shares / min_qty
    return most / avg_min_bids_per_applicant, most


def retail_winners(reserved_shares: int, min_qty: int) -> int:
    """How many retail applications can get the minimum bid."""
    return reserved_shares // min_qty


def nii_outcome(shares_applied: int, lot_size: int, times: float) -> NiiOutcome:
    _check_times(times)
    if shares_applied <= 0 or lot_size <= 0:
        raise ValueError("shares applied and lot size must be positive")
    expected = float(shares_applied) if times <= 1 else shares_applied / times
    lots = expected / lot_size
    return NiiOutcome(expected_shares=expected, expected_lots=lots, p_at_least_one_lot=min(1.0, lots))


def any_of(p: float, applications: int) -> float:
    """Chance that at least one of `applications` independent applications is allotted."""
    if not 0 <= p <= 1:
        raise ValueError("p must be between 0 and 1")
    if applications < 1:
        raise ValueError("at least one application")
    return 1 - (1 - p) ** applications
