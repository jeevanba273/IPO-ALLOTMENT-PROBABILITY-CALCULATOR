"""One IPO through the probability engine: the database's figures in, odds, projections and EV out.

Used by the CLI now and by the HTTP API in Phase 5. All the maths is in
ipo_intel.engine; this module only gathers its inputs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from ipo_intel import queries
from ipo_intel.db import Conn
from ipo_intel.engine.allotment import any_of, nii_outcome, retail_odds
from ipo_intel.engine.category import MIN_BID_IS_NII, Category, application_amount, category_for
from ipo_intel.engine.ev import ExpectedValue, GmpConfidence, expected_value
from ipo_intel.engine.gmp import GmpReading
from ipo_intel.engine.projection import History, Projection, ProjectionStatus, multipliers, project
from ipo_intel.gmp import reading_for
from ipo_intel.timeutil import utc_now
from ipo_intel.engine.reservation import VERIFY_RHP, CategoryFigures, apply_overrides
from ipo_intel.engine.rules import Rules
from ipo_intel.lifecycle import Lifecycle, lifecycle_of, needs_final_detail

SPLIT_ASSUMED = "IndianAPI reports one NII figure, so sNII and bNII are taken to be subscribed alike."
NO_SUBSCRIPTION_YET = "No subscription figures yet, so no odds."
PARTIAL_DAY = "Today's figures are still coming in; the projection uses the last complete day."


@dataclass(frozen=True)
class DayFigures:
    day: int
    bid_date: date
    complete: bool
    figures: dict[str, CategoryFigures]


@dataclass(frozen=True)
class OddsResult:
    """Odds for the chosen application. `*_low` assumes the most competition (times_high), `*_high` the least."""

    category: Category
    basis: str  # 'current', 'final' or 'projected'
    times_low: float
    times_high: float
    p_low: float  # one application, at least one allotment
    p_high: float
    any_low: float  # all applications together
    any_high: float
    shares_low: float  # expected shares, all applications together
    shares_high: float


@dataclass(frozen=True)
class Analysis:
    ipo_id: str
    name: str
    board: str
    stage: Lifecycle
    lot_size: int
    min_qty: int
    cutoff: float
    min_investment: float
    min_category: Category
    lots: int
    applications: int
    amount_rs: float
    category: Category
    latest: DayFigures | None
    base: DayFigures | None
    projections: dict[str, Projection] = field(default_factory=dict)
    odds_now: OddsResult | None = None
    odds_final: OddsResult | None = None
    gmp: GmpReading | None = None  # the stored GMP (ipoalerts or manual), if any
    gmp_used: float | None = None  # the GMP the EV was computed with: given, else stored
    ev: ExpectedValue | None = None
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# --- loading --------------------------------------------------------------------------


def load_histories(conn: Conn, today: date) -> list[History]:
    """Listed IPOs whose subscription was fetched after bidding closed, i.e. complete histories."""
    rows = conn.execute(
        "SELECT i.id, i.issue_type, i.bid_start, i.bid_end, i.listing_date, i.listing_price, i.detail_fetched_at, "
        "       s.bid_date, s.category, s.times "
        "FROM ipos i JOIN subscription_snapshots s ON s.ipo_id = i.id "
        "ORDER BY i.id, s.bid_date"
    ).fetchall()
    days: dict[str, dict[date, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    meta: dict[str, dict] = {}
    for row in rows:
        days[row["id"]][row["bid_date"]][row["category"]] = float(row["times"])
        meta[row["id"]] = row
    histories = []
    for ipo_id, by_day in days.items():
        row = meta[ipo_id]
        if lifecycle_of(row, today) is not Lifecycle.LISTED:
            continue
        if needs_final_detail(row["bid_end"], row["detail_fetched_at"]):
            continue
        histories.append(History(ipo_id, row["issue_type"], tuple(by_day[d] for d in sorted(by_day))))
    return histories


def reservation_overrides(conn: Conn, ipo_id: str) -> dict[str, int]:
    rows = conn.execute("SELECT category, reserved_shares FROM reservation_overrides WHERE ipo_id = %s", (ipo_id,))
    return {row["category"]: row["reserved_shares"] for row in rows}


def set_reservation_override(conn: Conn, ipo_id: str, category: str, shares: int, note: str | None, now: datetime) -> None:
    if category not in ("retail", "nii", "qib"):
        raise ValueError("category must be retail, nii or qib")
    if shares <= 0:
        raise ValueError("reserved shares must be positive")
    if conn.execute("SELECT 1 FROM ipos WHERE id = %s", (ipo_id,)).fetchone() is None:
        raise LookupError(f"no IPO with id {ipo_id!r}")
    conn.execute(
        "INSERT INTO reservation_overrides (ipo_id, category, reserved_shares, note, updated_at) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (ipo_id, category) DO UPDATE SET "
        "reserved_shares = EXCLUDED.reserved_shares, note = EXCLUDED.note, updated_at = EXCLUDED.updated_at",
        (ipo_id, category, shares, note, now),
    )


def clear_reservation_override(conn: Conn, ipo_id: str, category: str | None = None) -> int:
    if category is None:
        return conn.execute("DELETE FROM reservation_overrides WHERE ipo_id = %s", (ipo_id,)).rowcount
    return conn.execute(
        "DELETE FROM reservation_overrides WHERE ipo_id = %s AND category = %s", (ipo_id, category)
    ).rowcount


def _days(conn: Conn, ipo_id: str, overrides: dict[str, int], today: date, bidding_over: bool) -> list[DayFigures]:
    rows = conn.execute(
        "SELECT bid_date, category, applied_shares, reserved_shares, times FROM subscription_snapshots "
        "WHERE ipo_id = %s ORDER BY bid_date",
        (ipo_id,),
    ).fetchall()
    by_day: dict[date, dict[str, tuple[int, int, float]]] = defaultdict(dict)
    for row in rows:
        by_day[row["bid_date"]][row["category"]] = (row["applied_shares"], row["reserved_shares"], float(row["times"]))
    result = []
    for number, bid_date in enumerate(sorted(by_day), start=1):
        figures, _ = apply_overrides(by_day[bid_date], overrides)
        result.append(DayFigures(number, bid_date, bidding_over or bid_date < today, figures))
    return result


# --- the analysis ---------------------------------------------------------------------


def _odds(
    category: Category,
    basis: str,
    times_low: float,
    times_high: float,
    *,
    board: str,
    lots: int,
    lot_size: int,
    min_qty: int,
    applications: int,
    rules: Rules,
) -> OddsResult:
    applied = lots * lot_size
    if category is Category.RETAIL:
        k = rules.retail.avg_min_bids_per_applicant.for_board(board)
        p_low = retail_odds(times_high, k).low
        p_high = retail_odds(times_low, k).high
        # Not oversubscribed: allotted in full. Oversubscribed: the lottery allots the minimum bid.
        shares_low = applied if times_high <= 1 else p_low * min_qty
        shares_high = applied if times_low <= 1 else p_high * min_qty
    else:
        worst = nii_outcome(applied, lot_size, times_high)
        best = nii_outcome(applied, lot_size, times_low)
        p_low, p_high = worst.p_at_least_one_lot, best.p_at_least_one_lot
        shares_low, shares_high = worst.expected_shares, best.expected_shares
    return OddsResult(
        category=category,
        basis=basis,
        times_low=times_low,
        times_high=times_high,
        p_low=p_low,
        p_high=p_high,
        any_low=any_of(p_low, applications),
        any_high=any_of(p_high, applications),
        shares_low=shares_low * applications,
        shares_high=shares_high * applications,
    )


def analyse(
    conn: Conn,
    ipo_id: str,
    *,
    rules: Rules,
    today: date,
    lots: int | None = None,
    applications: int = 1,
    gmp_rs: float | None = None,
    gmp_confidence: GmpConfidence | None = None,
    histories: list[History] | None = None,
    now: datetime | None = None,
) -> Analysis:
    """`gmp_rs` overrides the stored GMP for a what-if; without it the latest stored GMP is used."""
    row = queries.get_ipo(conn, ipo_id)
    if row is None:
        raise LookupError(f"no IPO with id {ipo_id!r}")
    if not row["lot_size"] or not row["min_qty"] or row["cutoff_price"] is None:
        raise ValueError(f"{row['name']}: price band or lot size not announced yet")
    if applications < 1:
        raise ValueError("at least one application")

    board = row["issue_type"]
    lot_size, min_qty, cutoff = row["lot_size"], row["min_qty"], float(row["cutoff_price"])
    min_lots = max(1, min_qty // lot_size)
    lots = min_lots if lots is None else lots
    if lots < min_lots:
        raise ValueError(f"the minimum bid is {min_lots} lot(s) ({min_qty} shares)")

    stage = lifecycle_of(row, today)
    min_investment = float(row["min_investment_rs"])
    min_category = category_for(min_investment, rules)
    amount = application_amount(lots, lot_size, cutoff)
    category = category_for(amount, rules)
    notes: list[str] = []
    if min_category is not Category.RETAIL:
        notes.append(MIN_BID_IS_NII)
    if category is not Category.RETAIL:
        notes.append(SPLIT_ASSUMED)

    overrides = reservation_overrides(conn, ipo_id)
    bidding_over = stage in (Lifecycle.CLOSED, Lifecycle.LISTED)
    days = _days(conn, ipo_id, overrides, today, bidding_over)
    latest = days[-1] if days else None
    complete = [day for day in days if day.complete]
    base = complete[-1] if complete else latest
    # Until every category is checked against the RHP, say so.
    warnings = [] if {"retail", "nii", "qib"} <= overrides.keys() else [VERIFY_RHP]
    if latest is None:
        notes.append(NO_SUBSCRIPTION_YET)
    if base is not None and latest is not None and base is not latest:
        notes.append(PARTIAL_DAY)

    common = dict(board=board, lots=lots, lot_size=lot_size, min_qty=min_qty, applications=applications, rules=rules)
    projections: dict[str, Projection] = {}
    odds_now = odds_final = None
    if base is not None:
        final = bidding_over or (base.complete and row["bid_end"] is not None and base.bid_date >= row["bid_end"])
        histories = histories if histories is not None else load_histories(conn, today)
        for name, figures in base.figures.items():
            samples = multipliers(
                histories, board=board, category=name, day=base.day, min_base=rules.projection.min_base_times
            )
            projections[name] = project(figures.times, day=base.day, final=final, samples=samples, rules=rules)

        book = latest.figures.get(category.book)
        if book is not None:
            odds_now = _odds(category, "final" if bidding_over else "current", book.times, book.times, **common)
        projected = projections.get(category.book)
        if not bidding_over and projected is not None and projected.status is ProjectionStatus.OK:
            # The middle half of past IPOs; the extremes come from a few outliers.
            odds_final = _odds(category, "projected", projected.q1, projected.q3, **common)

    reading = reading_for(conn, row, now or utc_now())
    if gmp_rs is None and reading is not None:
        gmp_rs, gmp_confidence = reading.gmp_rs, reading.confidence
    ev = None
    if gmp_rs is not None:
        odds = odds_final or odds_now
        if odds is not None:
            ev = expected_value(
                odds.shares_low, odds.shares_high, gmp_rs, gmp_confidence or GmpConfidence.LOW, rules.ev.gmp_multipliers
            )

    return Analysis(
        ipo_id=ipo_id,
        name=row["name"],
        board=board,
        stage=stage,
        lot_size=lot_size,
        min_qty=min_qty,
        cutoff=cutoff,
        min_investment=min_investment,
        min_category=min_category,
        lots=lots,
        applications=applications,
        amount_rs=amount,
        category=category,
        latest=latest,
        base=base,
        projections=projections,
        odds_now=odds_now,
        odds_final=odds_final,
        gmp=reading,
        gmp_used=gmp_rs,
        ev=ev,
        notes=tuple(notes),
        warnings=tuple(warnings),
    )
