"""Turn database rows and engine results into JSON-ready dicts.

Dates are 'YYYY-MM-DD' (Indian calendar days), instants ISO-8601 UTC (the
browser shows them in IST), money and multiples plain numbers.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ipo_intel.analysis import Analysis, OddsResult
from ipo_intel.db import Conn, Row
from ipo_intel.engine.category import category_for, max_retail_lots
from ipo_intel.engine.gmp import GmpReading, latest_gmp
from ipo_intel.engine.rules import Rules
from ipo_intel.gmp import gmp_rows, observations
from ipo_intel.lifecycle import lifecycle_of
from ipo_intel.timeutil import UTC

CATEGORIES = ("retail", "nii", "qib", "overall")


def num(value: Any) -> float | None:
    if value is None:
        return None
    return float(value) if isinstance(value, Decimal) else value


def day(value: date | None) -> str | None:
    return value.isoformat() if value else None


def instant(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def gmp_view(reading: GmpReading | None) -> dict | None:
    if reading is None:
        return None
    return {
        "gmp_rs": reading.gmp_rs,
        "gmp_pct": reading.gmp_pct,
        "confidence": reading.confidence.value,
        "source": reading.source,
        "ts": instant(reading.ts_utc),
        "recent_points": reading.recent_points,
        "age_hours": reading.age.total_seconds() / 3600,
    }


def latest_subscriptions(conn: Conn) -> dict[str, dict]:
    """ipo_id -> {day, bid_date, retail, nii, qib, overall} for the latest bidding day."""
    days = {
        row["ipo_id"]: row
        for row in conn.execute(
            "SELECT ipo_id, count(DISTINCT bid_date) AS days, max(bid_date) AS last FROM subscription_snapshots GROUP BY ipo_id"
        )
    }
    result: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT s.ipo_id, s.category, s.times FROM subscription_snapshots s "
        "JOIN (SELECT ipo_id, max(bid_date) AS last FROM subscription_snapshots GROUP BY ipo_id) m "
        "  ON m.ipo_id = s.ipo_id AND m.last = s.bid_date"
    ):
        entry = result.setdefault(
            row["ipo_id"], {"day": days[row["ipo_id"]]["days"], "bid_date": day(days[row["ipo_id"]]["last"])}
        )
        entry[row["category"]] = num(row["times"])
    return result


def ipo_summary(row: Row, *, today: date, rules: Rules, reading: GmpReading | None, subscription: dict | None) -> dict:
    cutoff = num(row["cutoff_price"])
    min_investment = num(row["min_investment_rs"])
    min_category = category_for(min_investment, rules).value if min_investment else None
    listing = None
    if row["listing_price"] is not None:
        listing = {
            "price": num(row["listing_price"]),
            "gain_pct": num(row["listing_gain_pct"]),
            "flagged": row["listing_flag"] is not None,
            "source": row["listing_source"],
        }
    retail_lots = bnii_lots = None
    if row["lot_size"] and cutoff:
        lot_value = row["lot_size"] * cutoff
        retail_lots = max_retail_lots(row["lot_size"], cutoff, rules)
        bnii_lots = int(rules.categories.snii_max_rs // lot_value) + 1
    return {
        "id": row["id"],
        "name": row["name"],
        "board": row["issue_type"],
        "industry": row["industry"],
        "stage": lifecycle_of(row, today).value,
        "dormant": row["dormant"],
        "pre_apply_start": day(row["pre_apply_start"]),
        "bid_start": day(row["bid_start"]),
        "bid_end": day(row["bid_end"]),
        "allotment_date": day(row["allotment_date"]),
        "listing_date": day(row["listing_date"]),
        "price_min": num(row["price_min"]),
        "price_max": num(row["price_max"]),
        "cutoff": cutoff,
        "face_value": num(row["face_value"]),
        "issue_size_cr": num(row["issue_size_cr"]),
        "lot_size": row["lot_size"],
        "min_qty": row["min_qty"],
        "min_lots": (row["min_qty"] // row["lot_size"]) if row["min_qty"] and row["lot_size"] else None,
        "max_retail_lots": retail_lots,
        "min_snii_lots": retail_lots + 1 if retail_lots is not None else None,
        "min_bnii_lots": bnii_lots,
        "min_investment": min_investment,
        "min_category": min_category,
        "symbol": row["gmp_symbol"],
        "symbol_source": row["symbol_source"],
        "registrar_name": row["registrar_name"],
        "allotment_link": row["allotment_link"],
        "subscription": subscription,
        "gmp": gmp_view(reading),
        "listing": listing,
        "detail_fetched_at": instant(row["detail_fetched_at"]),
    }


def summaries(conn: Conn, rows: list[Row], *, today: date, now: datetime, rules: Rules) -> list[dict]:
    subs = latest_subscriptions(conn)
    points = gmp_rows(conn, [row["id"] for row in rows])
    result = []
    for row in rows:
        reading = latest_gmp(
            observations(points[row["id"]]),
            board=row["issue_type"],
            cutoff=num(row["cutoff_price"]),
            as_of=now,
        )
        result.append(ipo_summary(row, today=today, rules=rules, reading=reading, subscription=subs.get(row["id"])))
    return result


def subscription_history(conn: Conn, ipo_id: str) -> list[dict]:
    by_day: dict[date, dict] = defaultdict(dict)
    for row in conn.execute(
        "SELECT bid_date, category, applied_shares, reserved_shares, times FROM subscription_snapshots "
        "WHERE ipo_id = %s ORDER BY bid_date",
        (ipo_id,),
    ):
        by_day[row["bid_date"]][row["category"]] = num(row["times"])
    return [{"day": n, "bid_date": day(d), **by_day[d]} for n, d in enumerate(sorted(by_day), start=1)]


def gmp_series(conn: Conn, ipo_id: str) -> list[dict]:
    return [
        {"ts": instant(row["ts_utc"]), "gmp_rs": num(row["gmp_rs"]), "source": row["source"], "interval": row["interval"]}
        for row in gmp_rows(conn, [ipo_id])[ipo_id]
    ]


def _odds(odds: OddsResult | None) -> dict | None:
    if odds is None:
        return None
    data = asdict(odds)
    data["category"] = odds.category.value
    return data


def analysis_view(a: Analysis) -> dict:
    def figures(day_figures):
        if day_figures is None:
            return None
        return {
            "day": day_figures.day,
            "bid_date": day(day_figures.bid_date),
            "complete": day_figures.complete,
            "figures": {
                name: {"applied": f.applied_shares, "reserved": f.reserved_shares, "times": f.times, "source": f.source}
                for name, f in day_figures.figures.items()
            },
        }

    ev = None
    if a.ev is not None:
        ev = {
            "low": a.ev.low,
            "high": a.ev.high,
            "point": a.ev.point,
            "confidence": a.ev.confidence.value,
            "bands": [{"multiplier": b.multiplier, "low": b.low, "high": b.high} for b in a.ev.bands],
        }
    return {
        "ipo_id": a.ipo_id,
        "stage": a.stage.value,
        "board": a.board,
        "lot_size": a.lot_size,
        "min_qty": a.min_qty,
        "min_lots": max(1, a.min_qty // a.lot_size),
        "cutoff": a.cutoff,
        "min_investment": a.min_investment,
        "min_category": a.min_category.value,
        "lots": a.lots,
        "applications": a.applications,
        "amount_rs": a.amount_rs,
        "category": a.category.value,
        "category_label": a.category.label,
        "method": "lottery" if a.category.value == "retail" else "proportionate",
        "latest": figures(a.latest),
        "base": figures(a.base),
        "projections": {
            name: {
                "status": p.status.value, "message": p.message, "current": p.current, "day": p.day,
                "samples": p.samples, "low": p.low, "q1": p.q1, "median": p.median, "q3": p.q3, "high": p.high,
            }
            for name, p in a.projections.items()
        },
        "odds_now": _odds(a.odds_now),
        "odds_final": _odds(a.odds_final),
        "gmp": gmp_view(a.gmp),
        "gmp_used": a.gmp_used,
        "ev": ev,
        "notes": list(a.notes),
        "warnings": list(a.warnings),
    }
