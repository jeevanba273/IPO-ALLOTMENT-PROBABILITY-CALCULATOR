"""Parsed IndianAPI data. Numbers are Decimal or int, dates are IST calendar days."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class IpoRecord:
    """One IPO from GET /ipo/v2 or GET /ipo/{id}. None means 'not known', never 'zero'."""

    id: str
    name: str | None
    issue_type: str | None  # 'mainboard' or 'sme'
    symbol: str | None = None
    isin: str | None = None
    industry: str | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    cutoff_price: Decimal | None = None
    face_value: Decimal | None = None
    issue_size_cr: Decimal | None = None
    lot_size: int | None = None
    min_qty: int | None = None
    pre_apply_start: date | None = None
    bid_start: date | None = None
    bid_end: date | None = None
    allotment_date: date | None = None
    listing_date: date | None = None
    registrar_name: str | None = None
    allotment_link: str | None = None  # the registrar's allotment-status page, never a signed document URL


@dataclass(frozen=True)
class CategorySubscription:
    category: str  # 'retail', 'nii', 'qib' or 'overall'
    applied_shares: int
    reserved_shares: int
    times: Decimal


@dataclass(frozen=True)
class SubscriptionDay:
    """Cumulative subscription at the end of one bidding day."""

    bid_date: date
    categories: tuple[CategorySubscription, ...]


@dataclass(frozen=True)
class Listing:
    price: Decimal
    gain_pct: Decimal | None


@dataclass(frozen=True)
class IpoDetail:
    record: IpoRecord
    reserved_shares: dict[str, int] = field(default_factory=dict)  # category -> shares, zeros left out
    subscription: tuple[SubscriptionDay, ...] = ()
    # Cumulative figures without a date (subscription.overall), if the source gives them per category.
    latest: tuple[CategorySubscription, ...] = ()
    listing: Listing | None = None


@dataclass(frozen=True)
class BucketRecord:
    """One item of GET /ipo. Only its symbol and listing figures are used."""

    bucket: str
    name: str
    symbol: str | None
    is_sme: bool | None
    issue_price: Decimal | None
    listing_price: Decimal | None
    listing_gain_pct: Decimal | None
    listing_date: date | None
    bid_start: date | None


@dataclass(frozen=True)
class Usage:
    remaining: int
    hard_limit: int | None
    total_requests: int | None
