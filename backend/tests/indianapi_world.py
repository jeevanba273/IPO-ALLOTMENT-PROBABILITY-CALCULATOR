"""A fake IndianAPI built from the JSON fixtures, plus helpers to run jobs against it."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ipo_intel.db import Database
from ipo_intel.indianapi.models import Usage
from ipo_intel.ingest import store
from ipo_intel.ingest.runner import JobRunner
from ipo_intel.timeutil import IST
from tests.fakes import RecordingSleep, RouteTransport, TickingClock, make_settings

FIXTURES = Path(__file__).parent / "fixtures" / "indianapi"

# Tuesday 03 Nov 2026, 10:00 IST
TODAY_10AM = datetime(2026, 11, 3, 10, 0, tzinfo=IST)

TEST_KEY = "ia-test-key-0123456789"


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def day(date_ddmmyyyy: str, retail: str, nii: str, qib: str, overall: str) -> dict:
    """A subscription day where every category has 1,000,000 shares reserved."""
    reserved = "1000000"

    def cell(times: str) -> dict:
        return {"applied": str(round(float(times) * 1_000_000)), "reserved": reserved, "times": times}

    return {
        "date": date_ddmmyyyy,
        "categories": {
            "retailIndividual": cell(retail),
            "nonInstitutional": cell(nii),
            "qualifiedInstitutional": cell(qib),
            "overall": cell(overall),
        },
    }


def minimal_detail(ipo_id: str, name: str, days: list[dict] = (), performance: dict | None = None) -> dict:
    return {
        "status": "success",
        "data": {
            "basic": {"id": ipo_id, "name": name},
            "subscription": {"daily": list(days)},
            "performance": performance,
        },
    }


def routes(**overrides: Any) -> dict[str, Any]:
    world = {
        "/usage": load("usage"),
        "/ipo/v2?status=upcoming": load("v2_upcoming"),
        "/ipo/v2?status=open": load("v2_open_empty"),
        "/ipo/v2?status=active": load("v2_active"),
        "/ipo/v2?status=closed": load("v2_closed"),
        "/ipo/v2?status=listed": load("v2_listed"),
        "/ipo": load("buckets"),
        "/ipo/acme-tech": load("detail_acme_open"),
        "/ipo/northstar-optics": load("detail_northstar_listed"),
        "/ipo/sunrise-agro": minimal_detail(
            "sunrise-agro", "Sunrise Agro Foods Limited", [day("03-11-2026", "2.10", "0.80", "0.40", "1.30")]
        ),
        "/ipo/bluewave-logistics": minimal_detail(
            "bluewave-logistics",
            "Bluewave Logistics Ltd",
            [day("29-10-2026", "0.40", "0.20", "0.10", "0.20"), day("02-11-2026", "4.10", "9.80", "31.00", "17.20")],
        ),
        "/ipo/kesar-spices": minimal_detail(
            "kesar-spices", "Kesar Spices Ltd", [day("15-10-2026", "120.00", "210.00", "60.00", "140.00")]
        ),
        "/ipo/flatline-industries": minimal_detail(
            "flatline-industries", "Flatline Industries Ltd", [day("09-10-2026", "1.10", "0.90", "1.30", "1.15")]
        ),
        "/ipo/dropcap-engineering": minimal_detail(
            "dropcap-engineering", "Dropcap Engineering Limited", [day("08-10-2026", "0.60", "0.40", "1.05", "0.80")]
        ),
    }
    world.update({key.replace("__", "/"): value for key, value in overrides.items()})
    return world


class World:
    """A JobRunner wired to a RouteTransport, a fixed clock and a recording sleep."""

    def __init__(self, database: Database, db_url: str, *, now: datetime = TODAY_10AM, **settings: Any) -> None:
        self.db = database
        self.clock = TickingClock(now)
        self.sleep = RecordingSleep()
        self.transport = RouteTransport(routes())
        self.settings = make_settings(indianapi_key=TEST_KEY, database_url=db_url, **settings)
        self.runner = JobRunner(
            self.settings, db=database, transport=self.transport, clock=self.clock, sleep=self.sleep
        )

    def run(self, job: str, **kwargs: Any):
        return self.runner.run(job, **kwargs)

    def set_usage(self, remaining: int, hard_limit: int = 10000) -> None:
        """Record a /usage reading (also served by the fake API from now on)."""
        self.transport.routes["/usage"] = {"hard_limit": hard_limit, "remaining_requests": remaining}
        with self.db.connection() as conn:
            store.record_usage(conn, "indianapi", Usage(remaining, hard_limit, None), self.clock.now())

    def rows(self, sql: str, *params: Any) -> list[dict]:
        with self.db.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def ipo(self, ipo_id: str) -> dict:
        return self.rows("SELECT * FROM ipos WHERE id = %s", ipo_id)[0]

    def close(self) -> None:
        self.runner.close()
