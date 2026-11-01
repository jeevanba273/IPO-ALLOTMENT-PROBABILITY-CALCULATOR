"""Builds the configured clients. Each API's adapter takes one of these."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from ipo_intel.budget import BudgetCaps, BudgetGuard, Purpose
from ipo_intel.clients.base import NO_RETRIES, ApiClient, RetryPolicy, RunRecorder
from ipo_intel.config import Settings, require
from ipo_intel.db import Database
from ipo_intel.timeutil import Clock

INDIANAPI = "indianapi"
IPOALERTS = "ipoalerts"
TELEGRAM = "telegram"


def ipoalerts_caps(settings: Settings) -> BudgetCaps:
    return BudgetCaps(
        daily=settings.ipoalerts_daily_cap,
        monthly=settings.ipoalerts_monthly_cap,
        purpose_daily={Purpose.BACKFILL: settings.ipoalerts_backfill_daily_cap},
    )


def build_ipoalerts_budget(db: Database, settings: Settings, *, clock: Clock | None = None) -> BudgetGuard:
    return BudgetGuard(db, IPOALERTS, ipoalerts_caps(settings), clock)


def build_indianapi_client(
    settings: Settings,
    recorder: RunRecorder,
    *,
    transport: httpx.BaseTransport | None = None,
    clock: Clock | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ApiClient:
    key = require(settings.indianapi_key, "INDIANAPI_KEY")
    return ApiClient(
        source=INDIANAPI,
        base_url=settings.indianapi_base_url,
        recorder=recorder,
        headers={"X-API-Key": key},
        secrets=[key],
        timeout_seconds=settings.http_timeout_seconds,
        retry=RetryPolicy(
            max_retries=settings.http_max_retries,
            backoff_base_seconds=settings.http_backoff_base_seconds,
            backoff_max_seconds=settings.http_backoff_max_seconds,
        ),
        transport=transport,
        clock=clock,
        sleep=sleep,
    )


def build_telegram_client(
    settings: Settings,
    recorder: RunRecorder,
    *,
    transport: httpx.BaseTransport | None = None,
    clock: Clock | None = None,
) -> ApiClient:
    """The bot token is part of Telegram's URL, so it sits in the base URL (never in the
    recorded endpoint) and is redacted from every error. A failed send is not retried
    here; the alert simply stays due and the next alerts run sends it."""
    token = require(settings.telegram_bot_token, "TELEGRAM_BOT_TOKEN")
    return ApiClient(
        source=TELEGRAM,
        base_url=f"{settings.telegram_base_url.rstrip('/')}/bot{token}",
        recorder=recorder,
        secrets=[token],
        timeout_seconds=settings.http_timeout_seconds,
        retry=NO_RETRIES,
        transport=transport,
        clock=clock,
    )


def build_ipoalerts_client(
    settings: Settings,
    recorder: RunRecorder,
    budget: BudgetGuard,
    *,
    transport: httpx.BaseTransport | None = None,
    clock: Clock | None = None,
) -> ApiClient:
    """ipoalerts is budgeted: every call reserves a unit first and nothing is ever retried."""
    key = require(settings.ipoalerts_key, "IPOALERTS_KEY")
    return ApiClient(
        source=IPOALERTS,
        base_url=settings.ipoalerts_base_url,
        recorder=recorder,
        headers={"x-api-key": key},
        secrets=[key],
        timeout_seconds=settings.http_timeout_seconds,
        retry=NO_RETRIES,
        budget=budget,
        transport=transport,
        clock=clock,
    )
