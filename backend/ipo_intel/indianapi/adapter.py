"""Typed access to the IndianAPI endpoints this app uses. Every call goes through ApiClient."""

from __future__ import annotations

import logging
from urllib.parse import quote

from ipo_intel.clients.base import ApiClient, HttpStatusError, RateLimitedError
from ipo_intel.indianapi.models import BucketRecord, IpoDetail, IpoRecord, Usage
from ipo_intel.indianapi.parse import parse_buckets, parse_detail, parse_usage, parse_v2_item, v2_items

log = logging.getLogger(__name__)

# Statuses fetched from GET /ipo/v2. They only choose which list to fetch;
# the lifecycle stored and shown is always derived from dates.
LIST_STATUSES = ("upcoming", "open", "closed", "listed")


class IndianApi:
    def __init__(self, client: ApiClient) -> None:
        self._client = client

    def list_ipos(self, status: str, *, purpose: str) -> list[IpoRecord]:
        """GET /ipo/v2?status=..., parsed. If 'open' comes back empty or rejected, tries 'active'."""
        items = self._v2_items(status, purpose, tolerate_rejection=status == "open")
        if status == "open" and not items:
            log.info("/ipo/v2?status=open returned nothing; trying status=active")
            items = self._v2_items("active", purpose)
        return [record for record in map(parse_v2_item, items) if record is not None]

    def _v2_items(self, status: str, purpose: str, *, tolerate_rejection: bool = False) -> list[object]:
        try:
            payload = self._client.get_json("/ipo/v2", params={"status": status}, purpose=purpose)
        except HttpStatusError as exc:
            if tolerate_rejection and not isinstance(exc, RateLimitedError) and exc.http_status in (400, 404, 422):
                return []
            raise
        return v2_items(payload)

    def detail(self, ipo_id: str, *, purpose: str) -> IpoDetail:
        payload = self._client.get_json(f"/ipo/{quote(ipo_id, safe='')}", purpose=purpose)
        return parse_detail(payload, ipo_id)

    def buckets(self, *, purpose: str) -> list[BucketRecord]:
        return parse_buckets(self._client.get_json("/ipo", purpose=purpose))

    def usage(self, *, purpose: str) -> Usage:
        return parse_usage(self._client.get_json("/usage", purpose=purpose))
