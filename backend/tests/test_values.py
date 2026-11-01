from datetime import date
from decimal import Decimal

import pytest

from ipo_intel.values import (
    name_key,
    to_bool,
    to_date,
    to_decimal,
    to_int,
    to_issue_type,
    to_positive_int,
    to_price,
    to_symbol,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (12, Decimal("12")),
        (0.1, Decimal("0.1")),
        ("12.34", Decimal("12.34")),
        (" 1,23,45,678 ", Decimal("12345678")),
        ("-20", Decimal("-20")),
        (".5", Decimal("0.5")),
        (Decimal("7.25"), Decimal("7.25")),
    ],
)
def test_to_decimal_accepts_plain_numbers(raw, expected):
    assert to_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, True, "", "₹1,200 Cr", "12.34x", "1.2e5", "NaN", "abc", float("nan"), float("inf"), [], {}],
)
def test_to_decimal_rejects_formatted_or_odd_values(raw):
    assert to_decimal(raw) is None


def test_integers_and_prices():
    assert to_int("1234567") == 1234567
    assert to_int("1234.0") == 1234
    assert to_int("1234.5") is None
    assert to_positive_int("0") is None
    assert to_positive_int(150) == 150
    # A zero price means "not announced yet".
    assert to_price(0) is None
    assert to_price("76") == Decimal("76")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("05-11-2026", date(2026, 11, 5)),
        ("2026-11-05", date(2026, 11, 5)),
        # /ipo/v2 sends naive midnight timestamps that already name the IST day.
        ("2024-10-28T00:00:00.000", date(2024, 10, 28)),
        # 18:30 UTC is midnight IST the next day.
        ("2026-11-04T18:30:00Z", date(2026, 11, 5)),
        ("05 Nov 2026", date(2026, 11, 5)),
        ("Nov 05, 2026", date(2026, 11, 5)),
    ],
)
def test_to_date_formats(raw, expected):
    assert to_date(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "TBA", "31-02-2026", 20261105])
def test_to_date_rejects_unknowns(raw):
    assert to_date(raw) is None


def test_booleans_symbols_and_issue_types():
    assert to_bool("true") is True and to_bool("No") is False and to_bool(1) is True
    assert to_bool("maybe") is None
    assert to_symbol("  acmetech ") == "ACMETECH"
    assert to_symbol("") is None
    assert to_issue_type("Mainboard") == "mainboard"
    assert to_issue_type("Main Board") == "mainboard"
    assert to_issue_type("SME") == "sme"
    assert to_issue_type("Debt") is None


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("S&S Power Ltd.", "S & S Power Limited"),
        ("Northstar Optics Ltd.", "Northstar Optics Limited"),
        ("Kesar Spices", "KESAR SPICES LTD"),
        ("Acme Tech Ltd IPO", "Acme Tech Limited"),
        ("Shree (Ram) Agro-Foods", "Shree Ram Agro Foods"),
    ],
)
def test_name_key_matches_spelling_variants(left, right):
    assert name_key(left) == name_key(right)


def test_name_key_keeps_distinct_companies_apart():
    assert name_key("Acme Tech Ltd") != name_key("Acme Technologies Ltd")
    # Only whole words are dropped, not 'ltd' inside a word.
    assert name_key("Altdata Ltd") == "altdata"
