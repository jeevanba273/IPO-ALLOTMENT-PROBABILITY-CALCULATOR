"""IPO lifecycle, derived from dates only.

Status strings from IndianAPI differ between endpoints and lag reality, so
they are never used. The stages, for an IST calendar day:

* listed    - a listing price is known, or the listing date has arrived
* open      - bid_start <= today <= bid_end
* closed    - bidding has ended, not listed yet
* pre_apply - everything before bid_start, including IPOs with no dates yet
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum

from ipo_intel.timeutil import ist_at


class Lifecycle(StrEnum):
    PRE_APPLY = "pre_apply"
    OPEN = "open"
    CLOSED = "closed"
    LISTED = "listed"


def lifecycle(
    *,
    today: date,
    bid_start: date | None,
    bid_end: date | None,
    listing_date: date | None,
    has_listing_price: bool = False,
) -> Lifecycle:
    if has_listing_price or (listing_date is not None and today >= listing_date):
        return Lifecycle.LISTED
    if bid_start is not None and bid_end is not None and bid_start <= today <= bid_end:
        return Lifecycle.OPEN
    if bid_end is not None and today > bid_end:
        return Lifecycle.CLOSED
    return Lifecycle.PRE_APPLY


def lifecycle_of(row: dict, today: date) -> Lifecycle:
    """Lifecycle of an ipos table row."""
    return lifecycle(
        today=today,
        bid_start=row.get("bid_start"),
        bid_end=row.get("bid_end"),
        listing_date=row.get("listing_date"),
        has_listing_price=row.get("listing_price") is not None,
    )


def final_numbers_due_after(bid_end: date) -> datetime:
    """When the final subscription figures are surely out: 00:00 IST after the last bidding day."""
    return ist_at(bid_end + timedelta(days=1), 0)


def needs_final_detail(bid_end: date | None, detail_fetched_at: datetime | None) -> bool:
    """True if GET /ipo/{id} was never fetched after bidding closed, so the final day is missing."""
    if bid_end is None:
        return False
    return detail_fetched_at is None or detail_fetched_at < final_numbers_due_after(bid_end)
