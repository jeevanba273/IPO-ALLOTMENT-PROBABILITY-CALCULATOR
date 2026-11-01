"""GMP-implied listing gain against the actual listing gain, for listed IPOs.

The implied gain is the last GMP seen before listing divided by the cut-off.
Listings flagged as a possible price-band/default value are shown but left out
of the summary figures, since their 'actual' gain may not be real.
"""

from __future__ import annotations

import statistics
from datetime import date

from ipo_intel import queries
from ipo_intel.db import Conn
from ipo_intel.engine.gmp import latest_gmp
from ipo_intel.gmp import gmp_rows, observations
from ipo_intel.lifecycle import Lifecycle, lifecycle_of
from ipo_intel.timeutil import ist_at

# Listing-day trading opens at 10:00 IST; a GMP seen before then still counts.
LISTING_OPEN = (9, 59)


def calibration_rows(conn: Conn, today: date) -> list[dict]:
    ipos = [
        row
        for row in queries.list_ipos(conn)
        if lifecycle_of(row, today) is Lifecycle.LISTED
        and row["listing_gain_pct"] is not None
        and row["listing_date"] is not None
        and row["cutoff_price"]
    ]
    points = gmp_rows(conn, [row["id"] for row in ipos])
    rows = []
    for row in ipos:
        cutoff = float(row["cutoff_price"])
        reading = latest_gmp(
            observations(points[row["id"]]),
            board=row["issue_type"],
            cutoff=cutoff,
            as_of=ist_at(row["listing_date"], *LISTING_OPEN),
        )
        actual = float(row["listing_gain_pct"])
        implied = reading.gmp_pct * 100 if reading is not None and reading.gmp_pct is not None else None
        rows.append({
            "id": row["id"],
            "name": row["name"],
            "board": row["issue_type"],
            "listing_date": row["listing_date"],
            "cutoff": cutoff,
            "listing_price": float(row["listing_price"]) if row["listing_price"] is not None else None,
            "actual_pct": actual,
            "flagged": row["listing_flag"] is not None,
            "subscription": float(row["latest_times"]) if row["latest_times"] is not None else None,
            "gmp_rs": reading.gmp_rs if reading else None,
            "gmp_ts": reading.ts_utc if reading else None,
            "gmp_source": reading.source if reading else None,
            "confidence": reading.confidence.value if reading else None,
            "implied_pct": implied,
            "error_pp": implied - actual if implied is not None else None,
        })
    rows.sort(key=lambda r: (r["listing_date"], r["name"]), reverse=True)
    return rows


def summarise(rows: list[dict]) -> dict[str, dict]:
    """Per board, over unflagged listings that had a GMP: how far the GMP was off, in percentage points."""
    result = {}
    for board in ("mainboard", "sme"):
        errors = [r["error_pp"] for r in rows if r["board"] == board and r["error_pp"] is not None and not r["flagged"]]
        result[board] = {
            "listings": sum(1 for r in rows if r["board"] == board),
            "with_gmp": len(errors),
            "median_error_pp": statistics.median(errors) if errors else None,
            "median_abs_error_pp": statistics.median(abs(e) for e in errors) if errors else None,
            "within_5pp": (sum(1 for e in errors if abs(e) <= 5) / len(errors)) if errors else None,
        }
    return result
