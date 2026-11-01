"""Symbol mapping. GET /ipo has symbols but no IPO ids, so its records are joined to our IPOs by:

1. symbol, when GET /ipo/v2 or GET /ipo/{id} already gave that IPO the same symbol
2. normalised name ('Kesar Spices' = 'KESAR SPICES LTD')
3. a unique normalised-name prefix of at least 8 characters, and only when a
   bidding or listing date agrees too (GET /ipo shortens names: 'Core4 Engineer'
   for 'Core4 Engineers')

A manual symbol (set with `ipo-intel symbol set`) beats every API source; see
the effective_symbols view.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from ipo_intel.db import Conn, Row
from ipo_intel.indianapi.models import BucketRecord
from ipo_intel.values import name_key, to_symbol

MANUAL = "manual"
MIN_PREFIX = 8


class Match(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"


def _dates_agree(row: Row, record: BucketRecord) -> bool:
    pairs = ((row["bid_start"], record.bid_start), (row["listing_date"], record.listing_date))
    known = [(ours, theirs) for ours, theirs in pairs if ours is not None and theirs is not None]
    return bool(known) and all(ours == theirs for ours, theirs in known)


class IpoMatcher:
    def __init__(self, rows: Iterable[Row]) -> None:
        self._rows = list(rows)
        self._by_key: dict[str, list[Row]] = defaultdict(list)
        self._by_symbol: dict[str, list[Row]] = defaultdict(list)
        for row in self._rows:
            self._by_key[name_key(row["name"])].append(row)
            for symbol in row.get("symbols") or ():
                if row not in self._by_symbol[symbol]:
                    self._by_symbol[symbol].append(row)

    def match(self, record: BucketRecord) -> tuple[Row | None, Match]:
        if record.symbol and len(self._by_symbol.get(record.symbol, ())) == 1:
            return self._by_symbol[record.symbol][0], Match.MATCHED
        key = name_key(record.name)
        candidates = self._by_key.get(key, [])
        if not candidates and len(key) >= MIN_PREFIX:
            candidates = [
                row
                for name, rows in self._by_key.items()
                if len(name) >= MIN_PREFIX and (name.startswith(key) or key.startswith(name))
                for row in rows
                if _dates_agree(row, record)
            ]
        if not candidates:
            return None, Match.UNMATCHED
        # Same normalised name more than once: narrow by board, then dates.
        if len(candidates) > 1 and record.is_sme is not None:
            board = "sme" if record.is_sme else "mainboard"
            candidates = [row for row in candidates if row["issue_type"] == board] or candidates
        for column, value in (("listing_date", record.listing_date), ("bid_start", record.bid_start)):
            if len(candidates) > 1 and value is not None:
                candidates = [row for row in candidates if row[column] == value] or candidates
        if len(candidates) > 1:
            return None, Match.AMBIGUOUS
        return candidates[0], Match.MATCHED


def set_manual_symbol(conn: Conn, ipo_id: str, symbol: str, now: datetime) -> str:
    """Override the symbol for one IPO. Raises LookupError for an unknown IPO, ValueError for a blank symbol."""
    clean = to_symbol(symbol)
    if clean is None:
        raise ValueError("symbol must not be blank")
    if conn.execute("SELECT 1 FROM ipos WHERE id = %s", (ipo_id,)).fetchone() is None:
        raise LookupError(f"no IPO with id {ipo_id!r}")
    conn.execute(
        "INSERT INTO symbols (ipo_id, symbol, source, updated_at) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (ipo_id, source) DO UPDATE SET symbol = EXCLUDED.symbol, updated_at = EXCLUDED.updated_at",
        (ipo_id, clean, MANUAL, now),
    )
    return clean


def clear_manual_symbol(conn: Conn, ipo_id: str) -> bool:
    """Remove a manual override. Returns False if there was none."""
    return conn.execute("DELETE FROM symbols WHERE ipo_id = %s AND source = %s", (ipo_id, MANUAL)).rowcount > 0
