import logging
from dataclasses import asdict
from datetime import date
from decimal import Decimal

import pytest

from ipo_intel.indianapi.parse import (
    ParseError,
    parse_buckets,
    parse_detail,
    parse_usage,
    parse_v2_item,
    percent_gain,
    safe_link,
    v2_items,
)
from tests.indianapi_world import load


def _v2(name: str, ipo_id: str) -> dict:
    return next(item for item in load(name)["ipos"] if item["id"] == ipo_id)


def test_v2_item_uses_numeric_fields_and_iso_dates():
    record = parse_v2_item(_v2("v2_active", "acme-tech"))
    assert record.id == "acme-tech"
    assert record.issue_type == "mainboard"
    assert record.symbol == "ACMETECH"
    assert (record.price_min, record.price_max, record.cutoff_price) == (Decimal("95"), Decimal("100"), Decimal("100"))
    assert record.issue_size_cr == Decimal("1200.0")
    assert (record.lot_size, record.min_qty) == (150, 150)
    assert (record.bid_start, record.bid_end) == (date(2026, 11, 2), date(2026, 11, 4))
    assert record.allotment_date == date(2026, 11, 5)
    assert record.listing_date == date(2026, 11, 9)
    assert record.pre_apply_start == date(2026, 10, 30)
    # The registrar's allotment page is kept; the signed RHP link is not read at all.
    assert record.allotment_link == "https://ipostatus.kfintech.example/"


def test_v2_sme_minimum_is_min_qty_and_cutoff_falls_back_to_upper_band():
    record = parse_v2_item(_v2("v2_active", "sunrise-agro"))
    assert record.issue_type == "sme"
    assert record.symbol is None  # "" means no symbol yet
    assert record.cutoff_price == Decimal("76")  # cutOffPrice 0 -> upper band
    assert (record.lot_size, record.min_qty) == (1600, 3200)


def test_v2_zeros_mean_unknown():
    record = parse_v2_item(_v2("v2_upcoming", "quietpeak-retail"))
    assert record.price_min is record.price_max is record.cutoff_price is None
    assert record.lot_size is record.min_qty is None
    assert record.bid_start is record.bid_end is None


def test_v2_inverted_price_band_is_dropped(caplog):
    with caplog.at_level(logging.WARNING):
        record = parse_v2_item(_v2("v2_upcoming", "badband-tech"))
    assert record.price_min is None and record.price_max is None
    # The cut-off would have come from the untrusted upper band, so it goes too.
    assert record.cutoff_price is None
    assert "price band" in caplog.text


def test_v2_never_reads_formatted_strings():
    item = {**_v2("v2_upcoming", "meridian-power"), "issueSize": None, "lotSize": None}
    record = parse_v2_item(item)
    # sizeFormatted / minimumInvestment are present but must not be used.
    assert record.issue_size_cr is None
    assert record.lot_size is None and record.min_qty == 67


@pytest.mark.parametrize("item", [{"name": "No Id"}, {"id": "x", "issueType": "Mainboard"}, {"id": "x", "name": "Y", "issueType": "Debt"}, "..."])
def test_v2_items_without_identity_are_skipped(item):
    assert parse_v2_item(item) is None


def test_v2_items_shape_checks():
    assert v2_items({"summary": {"count": 0}}) == []
    with pytest.raises(ParseError):
        v2_items([])
    with pytest.raises(ParseError):
        v2_items({"ipos": "nope"})


def test_detail_parses_subscription_reservation_and_registrar():
    detail = parse_detail(load("detail_acme_open"), "acme-tech")
    assert detail.record.name == "Acme Tech Ltd"
    assert detail.record.issue_type == "mainboard"
    assert detail.record.face_value == Decimal("2")
    assert detail.record.allotment_date == date(2026, 11, 5)
    assert detail.reserved_shares == {"retail": 4_200_000, "nii": 1_800_000, "qib": 6_000_000}
    assert [day.bid_date for day in detail.subscription] == [date(2026, 11, 2), date(2026, 11, 3)]
    last = {item.category: item for item in detail.subscription[-1].categories}
    assert last["retail"].applied_shares == 6_090_000
    assert last["retail"].times == Decimal("1.45")
    assert last["overall"].reserved_shares == 12_000_000
    # overall is a bare number here, not per-category figures.
    assert detail.latest == ()
    assert detail.record.registrar_name == "KFin Technologies Ltd"
    assert detail.record.allotment_link == "https://ipostatus.kfintech.example/"
    assert detail.listing is None


def test_detail_keeps_no_signed_links_or_registrar_contacts():
    detail = parse_detail(load("detail_acme_open"), "acme-tech")
    flat = repr(asdict(detail))
    for forbidden in ("X-Amz", "rhp.pdf", "drhp", "Jane Placeholder", "einward", "+91-40", "www.kfintech", "sebi"):
        assert forbidden not in flat


def test_detail_listed_ipo():
    detail = parse_detail(load("detail_northstar_listed"), "northstar-optics")
    # The dateless day is skipped, and days come out oldest first although the API sends newest first.
    assert [day.bid_date for day in detail.subscription] == [date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)]
    first = {item.category: item for item in detail.subscription[0].categories}
    assert first["nii"].times == Decimal("0.6000")  # computed from applied/reserved
    assert detail.listing.price == Decimal("330")
    assert detail.listing.gain_pct == Decimal("32.00")
    # This allotment link carries a signature parameter, so it is not kept.
    assert detail.record.allotment_link is None
    assert detail.record.registrar_name == "Link Intime India Pvt Ltd"


def test_detail_falls_back_to_dates_block_and_computes_gain():
    payload = {
        "data": {
            "basic": {"name": "X Ltd"},
            "pricing": {"priceRange": {"max": 100}},
            "dates": {"bidding": {"start": "02-11-2026", "end": "04-11-2026"}, "listing": "09-11-2026"},
            "performance": {"price": {"listing": 125}},
            "subscription": {"overall": {"applied": "300", "reserved": "100", "times": "3"}},
        }
    }
    detail = parse_detail(payload, "x")
    assert (detail.record.bid_start, detail.record.bid_end) == (date(2026, 11, 2), date(2026, 11, 4))
    assert detail.record.listing_date == date(2026, 11, 9)
    assert detail.listing.gain_pct == Decimal("25.00")
    assert [item.category for item in detail.latest] == ["overall"]


@pytest.mark.parametrize("payload", [None, [], {}, {"status": "error"}, {"data": {}}])
def test_detail_without_data_is_a_parse_error(payload):
    with pytest.raises(ParseError):
        parse_detail(payload, "x")


def test_buckets_read_every_bucket_and_ignore_placeholders():
    records = parse_buckets(load("buckets"))
    by_name = {record.name: record for record in records}
    assert {record.bucket for record in records} == {"active", "upcoming", "closed", "listed"}
    assert "..." not in by_name
    kesar = by_name["Kesar Spices"]
    assert kesar.is_sme is True  # "true" string
    assert kesar.listing_price == Decimal("228.00")
    assert kesar.listing_gain_pct is None
    assert by_name["Nullprice Ltd"].listing_price is None
    # -19.999999999999993 from the API is a 20.00% loss.
    assert by_name["DropCap Engineering Ltd"].listing_gain_pct == Decimal("-20.00")
    assert by_name["Northstar Optics Ltd."].listing_gain_pct == Decimal("24.00")
    assert all("sebi" not in repr(record) for record in records)


def test_usage_matches_the_documented_example():
    usage = parse_usage(load("usage"))
    assert (usage.remaining, usage.hard_limit, usage.total_requests) == (8750, 10000, 1250)
    assert parse_usage({"data": {"remaining_requests": "42"}}).remaining == 42
    with pytest.raises(ParseError):
        parse_usage({"hard_limit": 10000})


def test_safe_link_and_percent_gain():
    assert safe_link("https://registrar.example/allotment?ipo=acme") == "https://registrar.example/allotment?ipo=acme"
    assert safe_link("https://x.example/a.pdf?X-Amz-Signature=1") is None
    assert safe_link("https://x.example/a.pdf?sv=2020&sig=abc") is None
    assert safe_link("javascript:alert(1)") is None
    assert safe_link("not a url") is None
    assert percent_gain(Decimal("228"), Decimal("120")) == Decimal("90.00")
    assert percent_gain(Decimal("228"), None) is None
