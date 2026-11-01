"""Parse IndianAPI responses into models.

Field names are the ones in live responses (checked 01 Nov 2026), which
differ from the published examples (https://dev.indianapi.in/openapi.json) in
places; older or documented names are kept as fallbacks.

* /ipo/v2: biddingStartDate/biddingEndDate (YYYY-MM-DD), timeline
  {preApplyStartDate, applicationStartDate, applicationEndDate, allotmentDate,
  listingDate, ...}, allotmentLinkUrl
* /ipo/{id}: data.basic, data.pricing.priceRange, data.issue, data.dates,
  data.timeline {preApplicationStart, biddingStart, allotmentFinalization,
  listingDay, ...}, data.reservation, data.subscription.daily[] (DD-MM-YYYY,
  numbers as strings, newest first), data.links.allotment, data.registrar.name,
  data.performance
* /ipo: buckets of {symbol, name, is_sme, issue_price, listing_price,
  listing_gains (an unrounded float), bidding_start_date, listing_date}

Never read: formatted strings (sizeFormatted, minimumInvestment,
timesFormatted, overallFormatted, totalSubscription), RHP/DRHP and SEBI
document links, and registrar contact details (data.registrar.contact).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ipo_intel.indianapi.models import (
    BucketRecord,
    CategorySubscription,
    IpoDetail,
    IpoRecord,
    Listing,
    SubscriptionDay,
    Usage,
)
from ipo_intel.values import (
    to_bool,
    to_date,
    to_decimal,
    to_int,
    to_issue_type,
    to_positive_int,
    to_price,
    to_symbol,
    to_text,
)

log = logging.getLogger(__name__)

# API category key -> our category.
SUBSCRIPTION_CATEGORIES = {
    "retailIndividual": "retail",
    "nonInstitutional": "nii",
    "qualifiedInstitutional": "qib",
    "overall": "overall",
}
RESERVATION_CATEGORIES = {
    "retail": "retail",
    "nonInstitutional": "nii",
    "qualifiedInstitutional": "qib",
}

# Query parameters that mark a pre-signed, expiring URL (S3, GCS, Azure SAS, CloudFront).
_SIGNED_PARAM_PREFIXES = ("x-amz-", "x-goog-")
_SIGNED_PARAMS = frozenset({"signature", "sig", "expires", "token", "se", "sp", "sv", "st", "policy", "key-pair-id"})


class ParseError(ValueError):
    """A response does not have the expected shape."""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(*values: object) -> object:
    return next((value for value in values if value is not None and value != ""), None)


def _first_date(*values: object) -> date | None:
    return next((day for day in map(to_date, values) if day is not None), None)


def _cutoff(cutoff: object, upper_band: object) -> Decimal | None:
    # Retail applicants bid at the cut-off, which is the upper band until the
    # issue price is fixed.
    return to_price(cutoff) or to_price(upper_band)


_CENT = Decimal("0.01")


def to_gain(value: object) -> Decimal | None:
    """A percentage rounded to 2 places: /ipo sends -19.999999999999993 for a 20.00% loss."""
    number = to_decimal(value)
    return None if number is None else number.quantize(_CENT)


def percent_gain(listing_price: Decimal | None, issue_price: Decimal | None) -> Decimal | None:
    if listing_price is None or not issue_price:
        return None
    return ((listing_price - issue_price) / issue_price * 100).quantize(_CENT)


def safe_link(value: object) -> str | None:
    """An http(s) URL that is not pre-signed, or None."""
    text = to_text(value)
    if text is None:
        return None
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    names = {name.lower() for name, _ in parse_qsl(parts.query, keep_blank_values=True)}
    if any(name.startswith(_SIGNED_PARAM_PREFIXES) or name in _SIGNED_PARAMS for name in names):
        return None
    return text


def validate_record(record: IpoRecord) -> IpoRecord:
    """Drop values that contradict each other instead of storing them."""
    changes: dict[str, object] = {}
    if record.price_min and record.price_max and record.price_min > record.price_max:
        log.warning("%s: price band %s > %s, ignoring it", record.id, record.price_min, record.price_max)
        changes.update(price_min=None, price_max=None)
        if record.cutoff_price in (record.price_min, record.price_max):
            changes["cutoff_price"] = None  # it came from the band we no longer trust
    if record.bid_start and record.bid_end and record.bid_start > record.bid_end:
        log.warning("%s: bidding starts %s after it ends %s, ignoring both", record.id, record.bid_start, record.bid_end)
        changes.update(bid_start=None, bid_end=None)
    return replace(record, **changes) if changes else record


# --- GET /ipo/v2 -------------------------------------------------------------


def v2_items(payload: object) -> list[object]:
    if not isinstance(payload, Mapping):
        raise ParseError("/ipo/v2: expected a JSON object")
    items = payload.get("ipos")
    if items is None:
        log.warning("/ipo/v2: response has no 'ipos' list; treating it as empty")
        return []
    if not isinstance(items, list):
        raise ParseError("/ipo/v2: 'ipos' is not a list")
    return items


def parse_v2_item(item: object) -> IpoRecord | None:
    if not isinstance(item, Mapping):
        return None
    ipo_id = to_text(item.get("id"))
    name = to_text(item.get("name"))
    issue_type = to_issue_type(item.get("issueType"))
    if ipo_id is None or name is None or issue_type is None:
        log.warning("/ipo/v2: skipping an item without id, name or issue type (id=%r)", item.get("id"))
        return None
    timeline = _mapping(item.get("timeline"))
    lot_size = to_positive_int(item.get("lotSize"))
    return validate_record(
        IpoRecord(
            id=ipo_id,
            name=name,
            issue_type=issue_type,
            symbol=to_symbol(item.get("symbol")),
            isin=to_text(item.get("isin")),
            industry=to_text(item.get("industry")),
            price_min=to_price(item.get("minimumPrice")),
            price_max=to_price(item.get("maximumPrice")),
            cutoff_price=_cutoff(item.get("cutOffPrice"), item.get("maximumPrice")),
            face_value=to_price(item.get("faceValue")),
            issue_size_cr=to_price(item.get("issueSize")),
            lot_size=lot_size,
            min_qty=to_positive_int(item.get("minimumQuantity")) or lot_size,
            pre_apply_start=_first_date(timeline.get("preApplyStartDate"), timeline.get("preApplicationStart")),
            bid_start=_first_date(
                item.get("biddingStartDate"), timeline.get("applicationStartDate"), timeline.get("biddingStart")
            ),
            bid_end=_first_date(
                item.get("biddingEndDate"), timeline.get("applicationEndDate"), timeline.get("biddingEnd")
            ),
            allotment_date=_first_date(
                timeline.get("allotmentDate"), timeline.get("allotmentFinalization"),
                timeline.get("allotmentStartDate"), timeline.get("allotmentProcessStart"),
            ),
            listing_date=_first_date(timeline.get("listingDate"), timeline.get("listingDay"), item.get("listingDate")),
            allotment_link=safe_link(item.get("allotmentLinkUrl")),
        )
    )


# --- GET /ipo/{id} -----------------------------------------------------------


def _category(category: str, raw: Mapping[str, Any]) -> CategorySubscription | None:
    applied = to_int(raw.get("applied"))
    reserved = to_int(raw.get("reserved"))
    if applied is None or reserved is None or applied < 0 or reserved <= 0:
        return None
    times = to_decimal(raw.get("times"))
    if times is None or times < 0:
        times = (Decimal(applied) / Decimal(reserved)).quantize(Decimal("0.0001"))
    return CategorySubscription(category, applied, reserved, times)


def _categories(raw: Mapping[str, Any]) -> tuple[CategorySubscription, ...]:
    parsed = (_category(ours, _mapping(raw.get(theirs))) for theirs, ours in SUBSCRIPTION_CATEGORIES.items())
    return tuple(item for item in parsed if item is not None)


def _subscription(raw: Mapping[str, Any], ipo_id: str) -> tuple[tuple[SubscriptionDay, ...], tuple[CategorySubscription, ...]]:
    days: dict[date, SubscriptionDay] = {}
    daily = raw.get("daily")
    for item in daily if isinstance(daily, list) else ():
        item = _mapping(item)
        bid_date = to_date(item.get("date"))
        categories = _categories(_mapping(item.get("categories")))
        if bid_date is None or not categories:
            log.warning("/ipo/%s: skipping a subscription day without a date or numbers (%r)", ipo_id, item.get("date"))
            continue
        days[bid_date] = SubscriptionDay(bid_date, categories)  # a repeated date: the later entry wins

    overall = _mapping(raw.get("overall"))
    latest = _categories(overall)
    if not latest:
        single = _category("overall", overall)
        latest = (single,) if single else ()
    return tuple(days[day] for day in sorted(days)), latest


def _reservation(raw: Mapping[str, Any]) -> dict[str, int]:
    reserved: dict[str, int] = {}
    for theirs, ours in RESERVATION_CATEGORIES.items():
        value = raw.get(theirs)
        if isinstance(value, Mapping):
            value = value.get("shares")
        shares = to_positive_int(value)  # zeros before bidding opens are left out
        if shares:
            reserved[ours] = shares
    return reserved


def _registrar_name(data: Mapping[str, Any]) -> str | None:
    registrar = data.get("registrar")
    if isinstance(registrar, Mapping):
        return to_text(registrar.get("name"))  # contact person, email and phone are never read
    return to_text(registrar) or to_text(data.get("registrarName"))


def _allotment_link(data: Mapping[str, Any]) -> str | None:
    links = _mapping(data.get("links"))
    documents = _mapping(data.get("documents"))
    for candidate in (links.get("allotment"), documents.get("allotmentStatus")):
        link = safe_link(candidate)
        if link is not None:
            return link
    return None


def _listing(data: Mapping[str, Any], cutoff: Decimal | None) -> Listing | None:
    performance = _mapping(data.get("performance"))
    price = _mapping(performance.get("price"))
    listing_price = to_price(price.get("listing"))
    if listing_price is None:
        return None
    gain = to_gain(_mapping(performance.get("gain")).get("percentage"))
    if gain is None:
        gain = percent_gain(listing_price, to_price(price.get("ipo")) or cutoff)
    return Listing(listing_price, gain)


def parse_detail(payload: object, ipo_id: str) -> IpoDetail:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping) or not data:
        raise ParseError(f"/ipo/{ipo_id}: response has no data object")
    basic = _mapping(data.get("basic"))
    pricing = _mapping(data.get("pricing"))
    price_range = _mapping(pricing.get("priceRange"))
    issue = _mapping(data.get("issue"))
    timeline = _mapping(data.get("timeline"))
    dates = _mapping(data.get("dates"))
    bidding = _mapping(dates.get("bidding"))
    lot_size = to_positive_int(issue.get("lotSize"))

    record = validate_record(
        IpoRecord(
            id=ipo_id,
            name=to_text(basic.get("name")),
            issue_type=to_issue_type(_first(basic.get("type"), basic.get("issueType"))),
            symbol=to_symbol(basic.get("symbol")),
            isin=to_text(_first(basic.get("isin"), data.get("isin"))),
            industry=to_text(basic.get("industry")),
            price_min=to_price(price_range.get("min")),
            price_max=to_price(price_range.get("max")),
            cutoff_price=_cutoff(_first(price_range.get("cutOff"), pricing.get("cutOff")), price_range.get("max")),
            face_value=to_price(pricing.get("faceValue")),
            issue_size_cr=to_price(issue.get("size")),
            lot_size=lot_size,
            min_qty=to_positive_int(issue.get("minimumQuantity")) or lot_size,
            pre_apply_start=_first_date(timeline.get("preApplicationStart")),
            bid_start=_first_date(timeline.get("biddingStart"), bidding.get("start")),
            bid_end=_first_date(timeline.get("biddingEnd"), bidding.get("end")),
            allotment_date=_first_date(
                timeline.get("allotmentFinalization"), timeline.get("allotmentProcessStart"), dates.get("allotment")
            ),
            listing_date=_first_date(timeline.get("listingDay"), dates.get("listing")),
            registrar_name=_registrar_name(data),
            allotment_link=_allotment_link(data),
        )
    )
    days, latest = _subscription(_mapping(data.get("subscription")), ipo_id)
    return IpoDetail(
        record=record,
        reserved_shares=_reservation(_mapping(data.get("reservation"))),
        subscription=days,
        latest=latest,
        listing=_listing(data, record.cutoff_price),
    )


# --- GET /ipo ----------------------------------------------------------------


def parse_buckets(payload: object) -> list[BucketRecord]:
    """Every item of every bucket (pre_apply, active, upcoming, closed, listed, ...)."""
    if isinstance(payload, Mapping) and isinstance(payload.get("data"), Mapping):
        payload = payload["data"]
    if not isinstance(payload, Mapping):
        raise ParseError("/ipo: expected an object of IPO buckets")
    records: list[BucketRecord] = []
    for bucket, items in payload.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            name = to_text(item.get("name"))
            if name is None:
                continue
            records.append(
                BucketRecord(
                    bucket=str(bucket),
                    name=name,
                    symbol=to_symbol(item.get("symbol")),
                    is_sme=to_bool(item.get("is_sme")),
                    issue_price=to_price(item.get("issue_price")),
                    listing_price=to_price(item.get("listing_price")),
                    listing_gain_pct=to_gain(item.get("listing_gains")),
                    listing_date=to_date(item.get("listing_date")),
                    bid_start=to_date(item.get("bidding_start_date")),
                )
            )
    return records


# --- GET /usage --------------------------------------------------------------


def parse_usage(payload: object) -> Usage:
    body = _mapping(payload)
    if "remaining_requests" not in body and isinstance(body.get("data"), Mapping):
        body = body["data"]
    remaining = to_int(body.get("remaining_requests"))
    if remaining is None:
        raise ParseError("/usage: response has no remaining_requests")
    return Usage(
        remaining=remaining,
        hard_limit=to_int(body.get("hard_limit")),
        total_requests=to_int(body.get("total_requests")),
    )
