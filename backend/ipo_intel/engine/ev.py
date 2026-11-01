"""Expected value of applying: P(allotment) x shares x cut-off x GMP%, i.e. expected shares x GMP in rupees.

Shown as a range over rules_v1.yaml's GMP multipliers (default 0.5, 0.75, 1.0),
because GMP tends to overstate the listing gain. With LOW confidence GMP there
is no single figure, only the range.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import StrEnum


class GmpConfidence(StrEnum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class EvBand:
    multiplier: float
    low: float
    high: float


@dataclass(frozen=True)
class ExpectedValue:
    low: float
    high: float
    point: float | None  # None for LOW confidence GMP
    bands: tuple[EvBand, ...]
    confidence: GmpConfidence


def gmp_pct(gmp_rs: float, cutoff: float) -> float:
    if cutoff <= 0:
        raise ValueError("cut-off price must be positive")
    return gmp_rs / cutoff


def expected_value(
    shares_low: float,
    shares_high: float,
    gmp_rs: float,
    confidence: GmpConfidence,
    multipliers: tuple[float, ...],
) -> ExpectedValue:
    """`shares_low`..`shares_high` is the expected number of allotted shares (already x P)."""
    if not 0 <= shares_low <= shares_high:
        raise ValueError("expected shares must be a range from 0")
    if not multipliers:
        raise ValueError("at least one GMP multiplier")
    bands = []
    for m in multipliers:
        a, b = shares_low * gmp_rs * m, shares_high * gmp_rs * m
        bands.append(EvBand(m, min(a, b), max(a, b)))
    point = None
    if confidence is GmpConfidence.HIGH:
        point = (shares_low + shares_high) / 2 * gmp_rs * statistics.median(multipliers)
    return ExpectedValue(
        low=min(band.low for band in bands),
        high=max(band.high for band in bands),
        point=point,
        bands=tuple(bands),
        confidence=GmpConfidence(confidence),
    )
