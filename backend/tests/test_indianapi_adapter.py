import httpx
import pytest

from ipo_intel.clients.base import HttpStatusError, RateLimitedError
from ipo_intel.clients.factory import build_indianapi_client
from ipo_intel.indianapi.adapter import IndianApi
from tests.fakes import FakeTransport, MemoryRunRecorder, RecordingSleep, RouteTransport, make_settings, ok, status
from tests.indianapi_world import TEST_KEY, load


def _api(transport, recorder=None):
    settings = make_settings(indianapi_key=TEST_KEY)
    client = build_indianapi_client(settings, recorder or MemoryRunRecorder(), transport=transport, sleep=RecordingSleep())
    return IndianApi(client)


def test_empty_open_list_falls_back_to_active():
    transport = FakeTransport([ok(load("v2_open_empty")), ok(load("v2_active"))])
    records = _api(transport).list_ipos("open", purpose="master")
    assert [record.id for record in records] == ["acme-tech", "sunrise-agro"]
    assert [request.url.params["status"] for request in transport.requests] == ["open", "active"]
    assert all(request.headers["X-API-Key"] == TEST_KEY for request in transport.requests)
    assert all(request.url.host == "dev.indianapi.in" for request in transport.requests)


def test_rejected_open_status_also_falls_back_to_active():
    transport = FakeTransport([status(422, {"detail": "invalid status"}), ok(load("v2_active"))])
    assert len(_api(transport).list_ipos("open", purpose="master")) == 2


def test_other_statuses_do_not_fall_back():
    transport = FakeTransport([ok(load("v2_open_empty"))])
    assert _api(transport).list_ipos("closed", purpose="master") == []
    assert len(transport.requests) == 1
    with pytest.raises(HttpStatusError):
        _api(FakeTransport([status(422)])).list_ipos("closed", purpose="master")


def test_rate_limit_on_open_is_not_swallowed():
    with pytest.raises(RateLimitedError):
        _api(FakeTransport([status(429)])).list_ipos("open", purpose="master")


def test_detail_path_is_escaped_and_purpose_recorded():
    recorder = MemoryRunRecorder()
    transport = RouteTransport({"/ipo/odd%2Fid": load("detail_acme_open")})
    detail = _api(transport, recorder).detail("odd/id", purpose="backfill")
    assert detail.record.id == "odd/id"
    assert [(run.endpoint, run.purpose, run.status) for run in recorder.runs] == [("/ipo/odd%2Fid", "backfill", "ok")]


def test_buckets_and_usage():
    transport = RouteTransport({"/ipo": load("buckets"), "/usage": load("usage")})
    api = _api(transport)
    assert len(api.buckets(purpose="listed")) == 10
    assert api.usage(purpose="usage").remaining == 8750
    assert transport.paths() == ["/ipo", "/usage"]


def test_transient_errors_are_retried_then_succeed():
    sleep = RecordingSleep()
    settings = make_settings(indianapi_key=TEST_KEY)
    transport = FakeTransport([httpx.ConnectTimeout("slow"), status(503), ok(load("usage"))])
    client = build_indianapi_client(settings, MemoryRunRecorder(), transport=transport, sleep=sleep)
    assert IndianApi(client).usage(purpose="usage").remaining == 8750
    assert len(sleep.calls) == 2
