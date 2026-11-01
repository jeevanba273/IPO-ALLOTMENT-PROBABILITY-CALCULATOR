"""The one ipoalerts call this app makes. The client is budgeted and never retries."""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from ipo_intel.budget import Purpose
from ipo_intel.clients.base import ApiClient
from ipo_intel.ipoalerts.parse import GmpSeries, parse_series

INTERVALS = ("hourly", "daily")


class IpoAlerts:
    def __init__(self, client: ApiClient) -> None:
        self._client = client

    def gmp(self, symbol: str, *, interval: str, from_day: date, purpose: Purpose) -> GmpSeries:
        """GET /ipos/{symbol}/gmp?interval=...&from=YYYY-MM-DD. Spends one unit of `purpose` budget first."""
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {INTERVALS}")
        payload = self._client.get_json(
            f"/ipos/{quote(symbol, safe='')}/gmp",
            params={"interval": interval, "from": from_day.isoformat()},
            purpose=Purpose(purpose),
        )
        return parse_series(payload)
