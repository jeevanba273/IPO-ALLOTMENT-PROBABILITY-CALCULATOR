"""Test doubles. Nothing here touches the network."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

import httpx

from ipo_intel.budget import BudgetDecision, Purpose, Refusal
from ipo_intel.clients.base import SourceRun
from ipo_intel.config import Settings
from ipo_intel.timeutil import UTC, ist_today, require_aware

Reply = Callable[[httpx.Request], httpx.Response] | Exception


def make_settings(**overrides: Any) -> Settings:
    """Settings that never read the developer's .env file."""
    return Settings(_env_file=None, **overrides)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = require_aware(now).astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = require_aware(now).astimezone(UTC)

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)


class TickingClock(FixedClock):
    """A fixed clock that moves 1 ms forward on every read, so events keep their order."""

    def now(self) -> datetime:
        current = self._now
        self._now += timedelta(milliseconds=1)
        return current


def ok(payload: Any, *, headers: dict[str, str] | None = None) -> Reply:
    return status(200, payload, headers=headers)


def status(code: int, payload: Any = None, *, headers: dict[str, str] | None = None) -> Reply:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    return raw(code, body, headers={"content-type": "application/json", **(headers or {})})


def raw(code: int, body: bytes, *, headers: dict[str, str] | None = None) -> Reply:
    return lambda request: httpx.Response(code, content=body, headers=headers or {})


class FakeTransport(httpx.BaseTransport):
    """Scripted httpx transport. Each request takes the next reply, then `default`.

    Running out of replies fails the test instead of reaching the network.
    """

    def __init__(self, replies: Iterable[Reply] = (), *, default: Reply | None = None) -> None:
        self._replies: deque[Reply] = deque(replies)
        self._default = default
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._replies:
            reply = self._replies.popleft()
        elif self._default is not None:
            reply = self._default
        else:
            raise AssertionError(f"unexpected HTTP request: {request.method} {request.url}")
        if isinstance(reply, Exception):
            raise reply
        return reply(request)


class RouteTransport(httpx.BaseTransport):
    """Answers by path and query ('/ipo/v2?status=open'). A route can be a JSON payload,
    a Reply or an exception; a request with no route fails the test."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = dict(routes)
        self.requests: list[httpx.Request] = []

    @staticmethod
    def key(request: httpx.Request) -> str:
        return request.url.raw_path.decode("ascii")  # still percent-encoded, query included

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = self.key(request)
        if key not in self.routes:
            raise AssertionError(f"unexpected HTTP request: {request.method} {key}")
        reply = self.routes[key]
        if isinstance(reply, Exception):
            raise reply
        if not callable(reply):
            reply = ok(reply)
        return reply(request)

    def paths(self) -> list[str]:
        return [self.key(request) for request in self.requests]


class MemoryRunRecorder:
    def __init__(self) -> None:
        self.runs: list[SourceRun] = []

    def record(self, run: SourceRun) -> None:
        self.runs.append(run)


class RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FakeBudget:
    """Allows the first `allow` reservations, then refuses with `refusal`."""

    def __init__(self, allow: int, refusal: Refusal = Refusal.DAILY_CAP, *, clock: FixedClock | None = None) -> None:
        self._remaining = allow
        self._refusal = refusal
        self._clock = clock or FixedClock(datetime(2026, 11, 2, 5, 0, tzinfo=UTC))
        self.reserved: list[Purpose] = []

    def try_acquire(self, purpose: Purpose) -> BudgetDecision:
        day = ist_today(self._clock.now())
        used = len(self.reserved)
        if self._remaining <= 0:
            return BudgetDecision(False, self._refusal, day, used, used, used)
        self._remaining -= 1
        self.reserved.append(purpose)
        return BudgetDecision(True, None, day, used + 1, used + 1, used + 1)
