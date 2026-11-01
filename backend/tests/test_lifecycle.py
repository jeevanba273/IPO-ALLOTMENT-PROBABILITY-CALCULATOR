from datetime import date, datetime

import pytest

from ipo_intel.lifecycle import Lifecycle, final_numbers_due_after, lifecycle, needs_final_detail
from ipo_intel.timeutil import IST

BID_START = date(2026, 11, 2)
BID_END = date(2026, 11, 4)
LISTING = date(2026, 11, 9)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 1), Lifecycle.PRE_APPLY),
        (date(2026, 11, 2), Lifecycle.OPEN),
        (date(2026, 11, 4), Lifecycle.OPEN),
        (date(2026, 11, 5), Lifecycle.CLOSED),
        (date(2026, 11, 8), Lifecycle.CLOSED),
        (date(2026, 11, 9), Lifecycle.LISTED),
    ],
)
def test_lifecycle_follows_the_calendar(today, expected):
    assert lifecycle(today=today, bid_start=BID_START, bid_end=BID_END, listing_date=LISTING) is expected


def test_no_dates_is_pre_apply():
    assert lifecycle(today=date(2026, 11, 3), bid_start=None, bid_end=None, listing_date=None) is Lifecycle.PRE_APPLY


def test_a_listing_price_means_listed_even_before_the_listing_date():
    stage = lifecycle(today=date(2026, 11, 5), bid_start=BID_START, bid_end=BID_END, listing_date=LISTING,
                      has_listing_price=True)
    assert stage is Lifecycle.LISTED


def test_final_numbers_are_due_at_midnight_ist_after_the_last_day():
    assert final_numbers_due_after(BID_END) == datetime(2026, 11, 5, 0, 0, tzinfo=IST)
    assert needs_final_detail(BID_END, None)
    assert needs_final_detail(BID_END, datetime(2026, 11, 4, 21, 0, tzinfo=IST))
    assert not needs_final_detail(BID_END, datetime(2026, 11, 5, 0, 0, tzinfo=IST))
    assert not needs_final_detail(None, None)
