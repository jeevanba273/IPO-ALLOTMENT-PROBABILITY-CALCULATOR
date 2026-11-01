import random
from datetime import date, datetime

import httpx
import pytest

from ipo_intel.budget import Purpose, Refusal
from ipo_intel.clients.base import (
    NO_RETRIES,
    ApiClient,
    BudgetExhausted,
    DecodeError,
    HttpStatusError,
    RateLimitedError,
    RetriesExhaustedError,
    RetryPolicy,
    RunStatus,
    SourceBlocked,
    SourceRun,
)
from ipo_intel.clients.factory import build_indianapi_client, build_ipoalerts_budget, build_ipoalerts_client
from ipo_intel.clients.runlog import PgRunRecorder, recent_runs
from ipo_intel.timeutil import UTC, ist_at
from tests.fakes import FakeBudget, FakeTransport, FixedClock, MemoryRunRecorder, RecordingSleep, make_settings, ok, raw, status

KEY = "ia-SECRET-key-1234567890"
NO_JITTER = RetryPolicy(max_retries=3, backoff_base_seconds=1.0, backoff_max_seconds=30.0, jitter_ratio=0)


def make_client(transport, recorder, *, retry=NO_JITTER, budget=None, sleep=None):
    return ApiClient(
        source="indianapi",
        base_url="https://dev.indianapi.in",
        recorder=recorder,
        headers={"X-API-Key": KEY},
        secrets=[KEY],
        retry=retry,
        budget=budget,
        transport=transport,
        clock=FixedClock(ist_at(date(2026, 11, 2), 10, 30)),
        sleep=sleep or RecordingSleep(),
    )


def test_success_returns_json_and_logs_one_row():
    transport, recorder = FakeTransport([ok({"summary": {}, "ipos": []})]), MemoryRunRecorder()
    client = make_client(transport, recorder)

    assert client.get_json("/ipo/v2", params={"status": "open"}, purpose="master_list") == {"summary": {}, "ipos": []}

    [request] = transport.requests
    assert request.headers["X-API-Key"] == KEY
    assert request.url.path == "/ipo/v2"
    assert request.url.params["status"] == "open"
    [run] = recorder.runs
    assert (run.status, run.http_status, run.attempt) == (RunStatus.OK, 200, 1)
    assert run.endpoint == "/ipo/v2?status=open"
    assert run.purpose == "master_list"
    assert run.duration_ms >= 0
    assert run.ran_at == ist_at(date(2026, 11, 2), 10, 30)


def test_body_is_decoded_as_utf8_whatever_the_header_says():
    body = '{"name": "Śrī Lotus ₹ Ltd"}'.encode("utf-8")
    transport = FakeTransport([raw(200, body, headers={"content-type": "application/json; charset=iso-8859-1"})])
    assert make_client(transport, MemoryRunRecorder()).get_json("/ipo")["name"] == "Śrī Lotus ₹ Ltd"


def test_utf8_byte_order_mark_is_tolerated():
    transport = FakeTransport([raw(200, b"\xef\xbb\xbf" + b'{"ok": true}')])
    assert make_client(transport, MemoryRunRecorder()).get_json("/ipo") == {"ok": True}


def test_5xx_is_retried_with_exponential_backoff():
    transport, recorder, sleep = FakeTransport([status(503), status(502), ok({"ok": True})]), MemoryRunRecorder(), RecordingSleep()
    assert make_client(transport, recorder, sleep=sleep).get_json("/ipo") == {"ok": True}
    assert sleep.calls == [1.0, 2.0]
    assert [(r.attempt, r.status, r.http_status) for r in recorder.runs] == [
        (1, RunStatus.HTTP_ERROR, 503),
        (2, RunStatus.HTTP_ERROR, 502),
        (3, RunStatus.OK, 200),
    ]


def test_gives_up_after_three_retries():
    transport = FakeTransport(default=httpx.ReadTimeout("read timed out"))
    recorder, sleep = MemoryRunRecorder(), RecordingSleep()
    with pytest.raises(RetriesExhaustedError, match="gave up after 4 attempt"):
        make_client(transport, recorder, sleep=sleep).get_json("/ipo/tonbo-imaging")
    assert len(transport.requests) == 4
    assert sleep.calls == [1.0, 2.0, 4.0]
    assert {r.status for r in recorder.runs} == {RunStatus.TIMEOUT}


def test_connection_errors_are_retried():
    transport = FakeTransport([httpx.ConnectError("connection refused"), ok({"ok": True})])
    recorder = MemoryRunRecorder()
    assert make_client(transport, recorder).get_json("/ipo") == {"ok": True}
    assert [r.status for r in recorder.runs] == [RunStatus.NETWORK_ERROR, RunStatus.OK]


def test_backoff_is_capped_and_jitter_bounded():
    capped = RetryPolicy(backoff_base_seconds=10, backoff_max_seconds=15, jitter_ratio=0)
    assert [capped.delay(n, random.Random(1)) for n in (1, 2, 3)] == [10, 15, 15]
    jittered = RetryPolicy(backoff_base_seconds=2, jitter_ratio=0.25)
    rng = random.Random(7)
    assert all(4 <= jittered.delay(2, rng) <= 5 for _ in range(200))


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_4xx_is_not_retried(code):
    transport = FakeTransport([status(code, {"error": "ERR:PRMFBNGMP"})])
    recorder, sleep = MemoryRunRecorder(), RecordingSleep()
    with pytest.raises(HttpStatusError) as caught:
        make_client(transport, recorder, sleep=sleep).get_json("/ipo")
    assert caught.value.http_status == code
    assert caught.value.payload == {"error": "ERR:PRMFBNGMP"}
    assert len(transport.requests) == 1 and sleep.calls == []
    assert [r.status for r in recorder.runs] == [RunStatus.HTTP_ERROR]


def test_429_is_not_retried_and_exposes_the_body():
    transport = FakeTransport([status(429, {"meta": {"expiresIn": 3600}})])
    recorder, sleep = MemoryRunRecorder(), RecordingSleep()
    with pytest.raises(RateLimitedError) as caught:
        make_client(transport, recorder, sleep=sleep).get_json("/ipo")
    assert caught.value.payload["meta"]["expiresIn"] == 3600
    assert len(transport.requests) == 1 and sleep.calls == []
    assert [(r.status, r.http_status) for r in recorder.runs] == [(RunStatus.RATE_LIMITED, 429)]


def test_invalid_json_is_a_decode_error_and_not_retried():
    transport = FakeTransport([raw(200, b"<html>maintenance</html>")])
    recorder = MemoryRunRecorder()
    with pytest.raises(DecodeError):
        make_client(transport, recorder).get_json("/ipo")
    assert len(transport.requests) == 1
    assert [r.status for r in recorder.runs] == [RunStatus.DECODE_ERROR]


def test_invalid_utf8_is_a_decode_error():
    with pytest.raises(DecodeError):
        make_client(FakeTransport([raw(200, b'{"name": "\xff"}')]), MemoryRunRecorder()).get_json("/ipo")


def test_secrets_never_reach_runs_or_errors():
    transport = FakeTransport([httpx.ConnectError(f"proxy rejected header X-API-Key: {KEY}")])
    recorder = MemoryRunRecorder()
    client = make_client(transport, recorder, retry=NO_RETRIES)
    with pytest.raises(RetriesExhaustedError) as caught:
        client.get_json("/ipo", params={"token": KEY})
    [run] = recorder.runs
    assert KEY not in run.error and "***" in run.error
    assert KEY not in run.endpoint and run.endpoint == "/ipo?token=***"
    assert KEY not in str(caught.value)


def test_a_broken_recorder_does_not_break_the_call():
    class Broken:
        def record(self, run: SourceRun) -> None:
            raise RuntimeError("db down")

    assert make_client(FakeTransport([ok([1])]), Broken()).get_json("/ipo") == [1]


def test_budgeted_source_may_not_retry():
    with pytest.raises(ValueError, match="must not retry"):
        make_client(FakeTransport(), MemoryRunRecorder(), budget=FakeBudget(allow=1))


def test_budgeted_call_needs_a_purpose():
    transport = FakeTransport()
    client = make_client(transport, MemoryRunRecorder(), retry=NO_RETRIES, budget=FakeBudget(allow=1))
    with pytest.raises(ValueError, match="needs a Purpose"):
        client.get_json("/ipos/TONBO/gmp", purpose="live")
    assert transport.requests == []


def test_refused_budget_means_no_request():
    transport, recorder = FakeTransport(), MemoryRunRecorder()
    client = make_client(transport, recorder, retry=NO_RETRIES, budget=FakeBudget(allow=0))
    with pytest.raises(BudgetExhausted) as caught:
        client.get_json("/ipos/TONBO/gmp", purpose=Purpose.LIVE)
    assert caught.value.decision.refusal is Refusal.DAILY_CAP
    assert transport.requests == []
    assert [(r.status, r.error, r.duration_ms) for r in recorder.runs] == [(RunStatus.SKIPPED_BUDGET, "daily_cap", 0)]


def test_blocked_source_raises_source_blocked():
    transport, recorder = FakeTransport(), MemoryRunRecorder()
    client = make_client(transport, recorder, retry=NO_RETRIES, budget=FakeBudget(allow=0, refusal=Refusal.BLOCKED))
    with pytest.raises(SourceBlocked):
        client.get_json("/ipos/TONBO/gmp", purpose=Purpose.LIVE)
    assert transport.requests == []
    assert recorder.runs[0].status is RunStatus.SKIPPED_BLOCKED


def test_indianapi_factory_uses_settings():
    settings = make_settings(indianapi_key=KEY, http_max_retries=2, http_backoff_base_seconds=0.5)
    transport, sleep = FakeTransport(default=status(500)), RecordingSleep()
    client = build_indianapi_client(settings, MemoryRunRecorder(), transport=transport, sleep=sleep)
    with pytest.raises(RetriesExhaustedError):
        client.get_json("/usage")
    assert len(transport.requests) == 3
    assert transport.requests[0].url.host == "dev.indianapi.in"
    assert transport.requests[0].headers["X-API-Key"] == KEY
    assert len(sleep.calls) == 2 and 0.5 <= sleep.calls[0] <= 0.625


def test_thirteenth_ipoalerts_call_of_the_day_is_refused_without_network(database):
    alerts_key = "ipoalerts-SECRET-key-42"
    settings = make_settings(ipoalerts_key=alerts_key)
    clock = FixedClock(ist_at(date(2026, 11, 2), 10, 30))
    budget = build_ipoalerts_budget(database, settings, clock=clock)
    transport = FakeTransport(default=ok({"series": [], "meta": {}}))
    client = build_ipoalerts_client(settings, PgRunRecorder(database), budget, transport=transport, clock=clock)

    for _ in range(12):
        client.get_json("/ipos/TONBO/gmp", params={"interval": "daily", "from": "2026-10-28"}, purpose=Purpose.LIVE)
    with pytest.raises(BudgetExhausted):
        client.get_json("/ipos/TONBO/gmp", params={"interval": "daily", "from": "2026-10-28"}, purpose=Purpose.LIVE)

    assert len(transport.requests) == 12
    assert all(r.headers["x-api-key"] == alerts_key for r in transport.requests)
    rows = recent_runs(database, source="ipoalerts", limit=50)
    assert sorted(row["status"] for row in rows) == ["ok"] * 12 + ["skipped_budget"]
    assert all(alerts_key not in (row["endpoint"] + (row["error"] or "")) for row in rows)
    assert budget.usage().used_today == 12


def test_pg_recorder_writes_utc_rows(database):
    PgRunRecorder(database).record(
        SourceRun(
            source="indianapi",
            endpoint="/usage",
            purpose="usage_check",
            attempt=1,
            status=RunStatus.OK,
            http_status=200,
            duration_ms=123,
            error=None,
            ran_at=datetime(2026, 11, 2, 5, 0, tzinfo=UTC),
        )
    )
    [row] = recent_runs(database)
    assert (row["source"], row["status"], row["duration_ms"]) == ("indianapi", "ok", 123)
    assert row["ran_at"] == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)
    assert row["ran_at"].utcoffset().total_seconds() == 0
