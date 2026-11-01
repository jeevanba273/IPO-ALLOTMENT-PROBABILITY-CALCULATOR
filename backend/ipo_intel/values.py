"""Turn raw API values into numbers, dates and match keys.

Only numeric fields are read. A string counts as a number only when it is a
plain one ("1234567", "12.34", optionally with thousands commas); anything
with letters or currency signs is rejected rather than guessed, so formatted
fields such as sizeFormatted can never sneak in.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ipo_intel.timeutil import IST, parse_ddmmyyyy

_PLAIN_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
_DATE_FORMATS = ("%d %b %Y", "%d-%b-%Y", "%b %d, %Y")
_NAME_STOPWORDS = frozenset({"limited", "ltd", "ipo"})
_TRUE = frozenset({"true", "yes", "y", "1"})
_FALSE = frozenset({"false", "no", "n", "0"})


def to_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # repr() is the shortest string that round-trips, so 0.1 stays 0.1.
        return Decimal(repr(value)) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if _PLAIN_NUMBER.fullmatch(text):
            try:
                return Decimal(text)
            except InvalidOperation:
                return None
    return None


def to_int(value: object) -> int | None:
    """A whole number, e.g. a share count. '1234.0' is fine, '1234.5' is not."""
    number = to_decimal(value)
    if number is None or number != number.to_integral_value():
        return None
    return int(number)


def to_positive_int(value: object) -> int | None:
    number = to_int(value)
    return number if number is not None and number > 0 else None


def to_price(value: object) -> Decimal | None:
    """A rupee amount. Zero means 'not known yet' in IndianAPI data, so it becomes None."""
    number = to_decimal(value)
    return number if number is not None and number > 0 else None


def to_date(value: object) -> date | None:
    """An Indian calendar day from DD-MM-YYYY, YYYY-MM-DD, an ISO timestamp or '05 Nov 2026'.

    A timestamp without an offset ('2024-10-28T00:00:00.000', as /ipo/v2
    sends) already names an IST day, so its date part is used as is. One with
    an offset is converted to IST first.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return parse_ddmmyyyy(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None
    return parsed.date() if parsed.tzinfo is None else parsed.astimezone(IST).date()


def to_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


def to_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text or None


def to_symbol(value: object) -> str | None:
    text = to_text(value)
    return text.upper() if text else None


def to_issue_type(value: object) -> str | None:
    """'Mainboard' / 'SME' (any case or spacing) to 'mainboard' / 'sme'."""
    text = to_text(value)
    if text is None:
        return None
    key = text.lower().replace(" ", "").replace("-", "")
    if key in ("mainboard", "main", "mainline"):
        return "mainboard"
    if key == "sme":
        return "sme"
    return None


def name_key(name: str) -> str:
    """Normalised company name for joining sources that share no id.

    Lowercase, '&' and punctuation removed, the words 'limited', 'ltd' and
    'ipo' dropped, spaces removed: 'S&S Power Ltd.' and 'S & S Power Limited'
    both become 'sspower'.
    """
    text = unicodedata.normalize("NFKC", name).casefold().replace("&", " ")
    words = re.sub(r"[^\w\s]|_", " ", text).split()
    return "".join(word for word in words if word not in _NAME_STOPWORDS)
