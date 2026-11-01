"""Telegram alerts: what is due when, sent once, retried after a failure, never leaking the token."""

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from ipo_intel import alerts
from ipo_intel.clients.factory import build_ipoalerts_budget
from ipo_intel.ingest import store
from ipo_intel.ingest.jobs import JobStatus
from ipo_intel.timeutil import IST
from tests.fakes import ok, status
from tests.gmp_world import gmp_world
from tests.indianapi_world import World

BOT = "123456:test-bot-token-ABCdef"
CHAT = "424242"
SEND = f"/bot{BOT}/sendMessage"


@pytest.fixture
def world(database, migrated_db_url):
    w = gmp_world(database, migrated_db_url, telegram_bot_token=BOT, telegram_chat_id=CHAT,
                  dashboard_url="https://ipo.example.app")
    w.run("open-details")
    w.run("gmp-live", slot="morning")
    w.transport.routes[SEND] = ok({"ok": True, "result": {"message_id": 1}})
    w.transport.requests.clear()
    yield w
    w.close()


def at(world, *args):
    world.clock.set(datetime(*args, tzinfo=IST))


def sent(world) -> list[str]:
    bodies = [json.loads(r.content) for r in world.transport.requests if r.url.raw_path.decode() == SEND]
    assert all(b["chat_id"] == CHAT and b["parse_mode"] == "HTML" for b in bodies)
    return [b["text"] for b in bodies]


def logged(world):
    return {(r["ipo_id"], r["kind"]) for r in world.rows("SELECT ipo_id, kind FROM alerts_log")}


def test_closes_today_carries_subscription_gmp_and_odds_and_goes_once(world):
    at(world, 2026, 11, 4, 12, 30)
    result = world.run("alerts")
    assert result.status is JobStatus.OK, result.summary
    [text] = sent(world)
    assert "⏰ <b>Acme Tech Ltd</b> closes today." in text
    assert "Subscribed (day 2): retail 1.45×, NII 1.20×, QIB 0.50×, overall 0.94×." in text
    assert "GMP ₹" in text and "implied" in text
    assert "Your odds at the minimum bid:" in text
    assert '<a href="https://ipo.example.app/ipo/acme-tech">Open in the dashboard</a>' in text
    assert logged(world) == {("acme-tech", "closes_today")}

    world.transport.requests.clear()
    assert world.run("alerts").summary == "nothing to send"
    assert sent(world) == []


def test_closes_today_waits_for_noon_and_stops_once_bidding_is_nearly_over(world):
    at(world, 2026, 11, 4, 11, 0)
    assert world.run("alerts").summary == "nothing to send"
    at(world, 2026, 11, 4, 16, 45)
    assert world.run("alerts").summary == "nothing to send"


def test_opens_tomorrow_allotment_day_and_listing_day(world):
    at(world, 2026, 11, 9, 18, 30)
    world.run("alerts")
    texts = sent(world)
    assert any("🟢 <b>Meridian Power Ltd</b> opens tomorrow, Tue 10 Nov." in t for t in texts)
    assert any("Price ₹210–₹222, minimum bid ₹14,874" in t for t in texts)

    world.transport.requests.clear()
    at(world, 2026, 11, 5, 9, 30)
    world.run("alerts")
    [text] = sent(world)
    assert "🔔 <b>Bluewave Logistics Ltd</b> lists today." in text

    world.transport.requests.clear()
    at(world, 2026, 11, 5, 17, 30)
    world.run("alerts")
    [text] = sent(world)
    assert "📋 Allotment day for <b>Acme Tech Ltd</b>." in text
    assert '<a href="https://ipostatus.kfintech.example/">KFin Technologies Ltd</a>' in text


def test_listing_result_compares_the_gain_with_the_gmp(world):
    at(world, 2026, 11, 5, 20, 30)
    with world.db.connection() as conn:
        store.record_listing(conn, "bluewave-logistics", price=Decimal("228"), gain_pct=Decimal("20.00"),
                             source="indianapi_list", listing_date=None, now=world.clock.now())
    world.run("alerts")
    texts = [t for t in sent(world) if "Bluewave" in t]
    assert len(texts) == 1
    text = texts[0]
    assert "📈 <b>Bluewave Logistics Ltd</b> listed at ₹228 (+20.0% on ₹190)." in text
    assert "GMP implied +" in text and "off by" in text


def test_a_failed_send_stays_due_and_goes_out_on_the_next_run(world):
    at(world, 2026, 11, 4, 12, 30)
    world.transport.routes[SEND] = status(502, {"ok": False, "description": "Bad Gateway"})
    failed = world.run("alerts")
    assert failed.status is JobStatus.FAILED and "retried next run" in failed.summary
    assert logged(world) == set()
    world.transport.routes[SEND] = ok({"ok": True, "result": {}})
    assert world.run("alerts").status is JobStatus.OK
    assert logged(world) == {("acme-tech", "closes_today")}
    # The bot token is in Telegram's URL; it must never reach the database.
    dump = repr(world.rows("SELECT * FROM source_runs")) + repr(world.rows("SELECT * FROM job_runs"))
    assert BOT not in dump and "123456:" not in dump


def test_telegram_saying_not_ok_counts_as_a_failure(world):
    at(world, 2026, 11, 4, 12, 30)
    world.transport.routes[SEND] = ok({"ok": False, "description": "chat not found"})
    assert world.run("alerts").status is JobStatus.FAILED
    assert logged(world) == set()


def test_quota_warnings_go_out_once_a_month(world):
    world.set_usage(250)
    world.run("alerts")
    texts = sent(world)
    assert any("IndianAPI quota low: about 250 requests left (below 300)" in t for t in texts)
    assert any("below 1000). The history backfill is paused." in t for t in texts)
    assert ("system", "indianapi_below_300:2026-11") in logged(world)
    world.transport.requests.clear()
    world.run("alerts")
    assert not any("IndianAPI" in t for t in sent(world))


def test_a_429_block_is_announced(world):
    budget = build_ipoalerts_budget(world.db, world.settings, clock=world.clock)
    budget.block_until(world.clock.now() + timedelta(hours=10), "HTTP 429 from ipoalerts")
    world.run("alerts")
    assert any("ipoalerts answered 429, so GMP polls are paused until" in t for t in sent(world))


def test_without_telegram_the_job_skips(database, migrated_db_url):
    world = World(database, migrated_db_url)
    try:
        result = world.run("alerts")
        assert result.status is JobStatus.SKIPPED and "Telegram is not set up" in result.summary
    finally:
        world.close()


def test_formatting_helpers():
    assert alerts._name({"name": "A & B <Ltd>", "issue_type": "sme"}) == "<b>A &amp; B &lt;Ltd&gt;</b> (SME)"
    assert alerts._inr(252800) == "₹2,52,800"
    assert alerts._inr(5.25) == "₹5.25"
    assert alerts._inr(-1500.0) == "-₹1,500"
    assert alerts._x(1.456) == "1.46×" and alerts._x(1234) == "1,234×"
    assert alerts._prob(0.102, 0.247) == "10.2%–24.7%" and alerts._prob(1, 1) == "100%"
