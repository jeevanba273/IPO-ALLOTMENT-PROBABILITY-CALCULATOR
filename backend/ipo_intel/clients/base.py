"""The single wrapper every external HTTP call goes through.

Per attempt it reserves a budget unit first (budgeted sources only), applies a
timeout, writes one source_runs row (skipped attempts included) and scrubs
secrets from anything it records or raises. JSON bodies are always decoded as
UTF-8, whatever the Content-Type header claims.

Retries with exponential backoff cover transient failures only: timeouts,
connection errors and 5xx. 4xx is never retried. 429 is never retried either;
the caller gets RateLimitedError with the parsed body (e.g. meta.expiresIn).
A budgeted source may not retry at all, because every attempt spends budget.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, NoReturn, Protocol
from urllib.parse import urlencode

import httpx

from ipo_intel.budget import BudgetDecision, CallBudget, Purpose, Refusal
from ipo_intel.redaction import redact
from ipo_intel.timeutil import Clock, SystemClock

log = logging.getLogger(__name__)

# Longest response snippet or error text kept in source_runs and exceptions.
TEXT_LIMIT = 300


class RunStatus(StrEnum):
    OK = "ok"
    HTTP_ERROR = "http_error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    DECODE_ERROR = "decode_error"
    SKIPPED_BUDGET = "skipped_budget"
    SKIPPED_BLOCKED = "skipped_blocked"


@dataclass(frozen=True)
class SourceRun:
    source: str
    endpoint: str
    purpose: str | None
    attempt: int
    status: RunStatus
    http_status: int | None
    duration_ms: int
    error: str | None
    ran_at: datetime


class RunRecorder(Protocol):
    def record(self, run: SourceRun) -> None: ...


class ApiError(Exception):
    """A failed or skipped external call. Messages never contain secrets."""

    def __init__(self, message: str, *, source: str, endpoint: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.source = source
        self.endpoint = endpoint
        self.http_status = http_status


class HttpStatusError(ApiError):
    """A non-2xx response that is not worth retrying. `payload` is the body if it was JSON."""

    def __init__(self, message: str, *, source: str, endpoint: str, http_status: int, payload: Any = None) -> None:
        super().__init__(message, source=source, endpoint=endpoint, http_status=http_status)
        self.payload = payload


class RateLimitedError(HttpStatusError):
    """HTTP 429. Never retried."""


class RetriesExhaustedError(ApiError):
    """Every attempt failed with a transient error (timeout, connection error or 5xx)."""


class DecodeError(ApiError):
    """A 2xx response whose body is not valid UTF-8 JSON."""


class CallSkipped(ApiError):
    """The budget refused the call, so no network I/O happened."""

    def __init__(self, message: str, *, source: str, endpoint: str, decision: BudgetDecision) -> None:
        super().__init__(message, source=source, endpoint=endpoint)
        self.decision = decision


class BudgetExhausted(CallSkipped):
    """A daily, monthly or per-purpose cap is used up."""


class SourceBlocked(CallSkipped):
    """The source is blocked (after a 429) until its quota resets."""


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    jitter_ratio: float = 0.25

    def delay(self, retry_number: int, rng: random.Random) -> float:
        """Seconds to wait before retry `retry_number` (1-based): base * 2^(n-1), capped, plus jitter."""
        delay = min(self.backoff_max_seconds, self.backoff_base_seconds * 2 ** (retry_number - 1))
        return delay * (1 + rng.uniform(0, self.jitter_ratio)) if self.jitter_ratio else delay


NO_RETRIES = RetryPolicy(max_retries=0)


def _ms_since(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _snippet(response: httpx.Response) -> str:
    text = response.content[:TEXT_LIMIT].decode("utf-8", errors="replace")
    return " ".join(text.split())


def _decode_json(response: httpx.Response) -> Any:
    # utf-8-sig: still strict UTF-8, but tolerates a leading byte-order mark.
    return json.loads(response.content.decode("utf-8-sig"))


def _try_json(response: httpx.Response) -> Any:
    try:
        return _decode_json(response)
    except ValueError:
        return None


class ApiClient:
    def __init__(
        self,
        *,
        source: str,
        base_url: str,
        recorder: RunRecorder,
        headers: Mapping[str, str] | None = None,
        secrets: Iterable[str] = (),
        timeout_seconds: float = 20.0,
        retry: RetryPolicy = RetryPolicy(),
        budget: CallBudget | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Clock | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if budget is not None and retry.max_retries:
            raise ValueError(f"{source}: a budgeted source must not retry; every attempt spends budget")
        self.source = source
        self._recorder = recorder
        self._secrets = tuple(s for s in secrets if s)
        self._retry = retry
        self._budget = budget
        self._clock = clock or SystemClock()
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._http = httpx.Client(
            base_url=base_url,
            headers=dict(headers or {}),
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_json(self, path: str, *, params: Mapping[str, Any] | None = None, purpose: str | None = None) -> Any:
        return self.request_json("GET", path, params=params, purpose=purpose)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        purpose: str | None = None,
    ) -> Any:
        """Make the call and return the decoded JSON body.

        `purpose` is a free-form label for source_runs, except on a budgeted
        source, where it must be a Purpose and decides which cap is charged.
        """
        endpoint = self._endpoint(path, params)
        if self._budget is not None and not isinstance(purpose, Purpose):
            raise ValueError(f"{self.source}: budgeted call to {endpoint} needs a Purpose")
        label = str(purpose) if purpose is not None else None
        attempts = self._retry.max_retries + 1

        for attempt in range(1, attempts + 1):
            if self._budget is not None:
                self._reserve(endpoint, Purpose(purpose), attempt)
            started = time.perf_counter()
            try:
                response = self._http.request(method, path, params=params, json=json_body)
            except httpx.TimeoutException as exc:
                status, http_status, error = RunStatus.TIMEOUT, None, f"timeout ({type(exc).__name__})"
            except httpx.TransportError as exc:
                status, http_status, error = RunStatus.NETWORK_ERROR, None, f"{type(exc).__name__}: {exc}"
            else:
                elapsed = _ms_since(started)
                if response.is_success:
                    return self._decode(response, endpoint, label, attempt, elapsed)
                if response.status_code < 500:
                    self._fail_fast(response, endpoint, label, attempt, elapsed)
                status, http_status = RunStatus.HTTP_ERROR, response.status_code
                error = f"HTTP {response.status_code}: {_snippet(response)}"

            self._record(endpoint, label, attempt, status, http_status, _ms_since(started), error)
            if attempt < attempts:
                delay = self._retry.delay(attempt, self._rng)
                log.warning(
                    "%s %s attempt %d/%d failed (%s); retrying in %.1fs",
                    self.source, endpoint, attempt, attempts, status, delay,
                )
                self._sleep(delay)

        raise RetriesExhaustedError(
            self._redact(f"{self.source} {endpoint}: gave up after {attempts} attempt(s): {error}"),
            source=self.source,
            endpoint=endpoint,
            http_status=http_status,
        )

    def _reserve(self, endpoint: str, purpose: Purpose, attempt: int) -> None:
        decision = self._budget.try_acquire(purpose)
        if decision.allowed:
            return
        blocked = decision.refusal is Refusal.BLOCKED
        status = RunStatus.SKIPPED_BLOCKED if blocked else RunStatus.SKIPPED_BUDGET
        self._record(endpoint, purpose.value, attempt, status, None, 0, decision.reason)
        log.info(
            "%s %s skipped: %s (today %d, month %d)",
            self.source, endpoint, decision.reason, decision.used_today, decision.used_month,
        )
        error_type = SourceBlocked if blocked else BudgetExhausted
        raise error_type(
            f"{self.source} {endpoint}: skipped ({decision.reason})",
            source=self.source,
            endpoint=endpoint,
            decision=decision,
        )

    def _decode(self, response: httpx.Response, endpoint: str, purpose: str | None, attempt: int, elapsed: int) -> Any:
        try:
            data = _decode_json(response)
        except ValueError as exc:  # UnicodeDecodeError and JSONDecodeError are both ValueErrors
            self._record(endpoint, purpose, attempt, RunStatus.DECODE_ERROR, response.status_code, elapsed, str(exc))
            raise DecodeError(
                f"{self.source} {endpoint}: response is not UTF-8 JSON",
                source=self.source,
                endpoint=endpoint,
                http_status=response.status_code,
            ) from None
        self._record(endpoint, purpose, attempt, RunStatus.OK, response.status_code, elapsed, None)
        return data

    def _fail_fast(
        self, response: httpx.Response, endpoint: str, purpose: str | None, attempt: int, elapsed: int
    ) -> NoReturn:
        code = response.status_code
        detail = f"HTTP {code}: {_snippet(response)}"
        rate_limited = code == 429
        status = RunStatus.RATE_LIMITED if rate_limited else RunStatus.HTTP_ERROR
        self._record(endpoint, purpose, attempt, status, code, elapsed, detail)
        error_type = RateLimitedError if rate_limited else HttpStatusError
        raise error_type(
            self._redact(f"{self.source} {endpoint}: {detail}"),
            source=self.source,
            endpoint=endpoint,
            http_status=code,
            payload=_try_json(response),
        )

    def _record(
        self,
        endpoint: str,
        purpose: str | None,
        attempt: int,
        status: RunStatus,
        http_status: int | None,
        duration_ms: int,
        error: str | None,
    ) -> None:
        run = SourceRun(
            source=self.source,
            endpoint=endpoint,
            purpose=purpose,
            attempt=attempt,
            status=status,
            http_status=http_status,
            duration_ms=duration_ms,
            error=self._redact(error)[:TEXT_LIMIT] if error else None,
            ran_at=self._clock.now(),
        )
        try:
            self._recorder.record(run)
        except Exception:  # a broken audit log must never change the outcome of the call
            log.exception("could not record source run for %s %s", self.source, endpoint)

    def _endpoint(self, path: str, params: Mapping[str, Any] | None) -> str:
        query = urlencode(sorted((key, str(value)) for key, value in params.items())) if params else ""
        return self._redact(f"{path}?{query}" if query else path)

    def _redact(self, text: str) -> str:
        return redact(text, self._secrets)
