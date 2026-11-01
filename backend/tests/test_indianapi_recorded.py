"""Parsers against real IndianAPI responses recorded on 01 Nov 2026.

The files in fixtures/indianapi/recorded are live bodies with document links
and registrar contact details replaced by placeholders; nothing else changed.
"""

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ipo_intel.indianapi.parse import parse_buckets, parse_detail, parse_usage, parse_v2_item, v2_items
from ipo_intel.ingest.store import PRICE_BAND_DEFAULT, listing_flag

RECORDED = Path(__file__).parent / "fixtures" / "indianapi" / "recorded"


def load(name):
    return json.loads((RECORDED / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["v2_upcoming.json", "v2_open.json", "v2_closed.json"])
def test_every_v2_item_parses(name):
    items = v2_items(load(name))
    records = [parse_v2_item(item) for item in items]
    assert records and all(record is not None for record in records)
    assert all(record.issue_type in ("mainboard", "sme") for record in records)


def test_open_and_closed_ipos_have_full_dates_and_lots():
    for name in ("v2_open.json", "v2_closed.json"):
        for record in map(parse_v2_item, v2_items(load(name))):
            assert record.bid_start and record.bid_end and record.bid_start <= record.bid_end, record.id
            assert record.allotment_date and record.listing_date, record.id
            assert record.lot_size and record.min_qty and record.min_qty >= record.lot_size, record.id
            assert record.cutoff_price and record.price_max and record.cutoff_price <= record.price_max, record.id
            if record.issue_type == "sme":
                assert record.min_qty >= 2 * record.lot_size, record.id  # SME minimum is 2 lots


def test_v2_allotment_links_are_registrar_pages():
    links = {parse_v2_item(item).allotment_link for item in v2_items(load("v2_open.json"))}
    assert links - {None}
    assert all(link is None or "redacted" not in link for link in links)


def test_closed_ipo_detail():
    detail = parse_detail(load("detail_bls_polymers.json"), "bls-polymers-limited-ipo")
    record = detail.record
    assert (record.name, record.issue_type, record.symbol) == ("BLS Polymers", "mainboard", "BLSPOLY")
    assert (record.bid_start, record.bid_end) == (date(2026, 10, 27), date(2026, 10, 29))
    assert record.listing_date == date(2026, 11, 3)
    assert record.registrar_name == "KFin Technologies Limited"
    assert record.allotment_link == "https://ipostatus.kfintech.com/"
    assert detail.reserved_shares == {"retail": 5_950_000, "nii": 2_550_000, "qib": 3_400_000}
    assert [day.bid_date for day in detail.subscription] == [date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29)]
    final = {item.category: item for item in detail.subscription[-1].categories}
    assert final["overall"].times == Decimal("239.02")
    assert final["retail"].times == Decimal("124.5")
    assert final["retail"].applied_shares == 740_759_300
    flat = repr(asdict(detail))
    assert "REDACTED" not in flat and "redacted" not in flat


def test_open_ipo_detail():
    detail = parse_detail(load("detail_maxwell_engineering.json"), "maxwell-engineering-solutions-limited-ipo")
    assert detail.record.issue_type == "sme"
    assert detail.subscription, "an open IPO with bids should have day-wise figures"
    for day in detail.subscription:
        assert {item.category for item in day.categories} >= {"retail", "overall"}


def test_buckets_round_gains_so_the_outlier_flag_works():
    records = {record.symbol: record for record in parse_buckets(load("ipo_buckets.json"))}
    core4 = records["CORE4"]  # "Listed at 74.4 for 20.00% loss", sent as -19.999999999999993
    assert core4.listing_gain_pct == Decimal("-20.00")
    assert listing_flag(core4.listing_gain_pct) == PRICE_BAND_DEFAULT
    assert records["TONBO"].listing_gain_pct == Decimal("65.71")
    assert listing_flag(records["TONBO"].listing_gain_pct) is None
    assert all("redacted" not in repr(record) for record in records.values())


def test_usage():
    usage = parse_usage(load("usage.json"))
    assert usage.hard_limit == 10000
    assert 0 < usage.remaining <= usage.hard_limit
