"""Ingestion jobs end to end: fake IndianAPI (JSON fixtures) -> real Postgres."""

import logging
from decimal import Decimal

import pytest

from ipo_intel.ingest import jobs
from ipo_intel.ingest.jobs import JobStatus
from ipo_intel.ingest.symbols import clear_manual_symbol, set_manual_symbol
from tests.fakes import status
from tests.indianapi_world import TEST_KEY, World, load

ALL_IPOS = {
    "meridian-power", "quietpeak-retail", "badband-tech", "acme-tech", "sunrise-agro",
    "bluewave-logistics", "northstar-optics", "kesar-spices", "flatline-industries", "dropcap-engineering",
}


@pytest.fixture
def world(database, migrated_db_url):
    w = World(database, migrated_db_url)
    yield w
    w.close()


def _effective_symbol(world, ipo_id):
    rows = world.rows("SELECT symbol, source FROM effective_symbols WHERE ipo_id = %s", ipo_id)
    return (rows[0]["symbol"], rows[0]["source"]) if rows else None


# --- master list -----------------------------------------------------------------


def test_master_list_stores_every_status_and_falls_back_to_active(world):
    result = world.run("master")
    assert result.status is JobStatus.OK, result.summary
    assert world.transport.paths() == [
        "/ipo/v2?status=upcoming",
        "/ipo/v2?status=open",
        "/ipo/v2?status=active",
        "/ipo/v2?status=closed",
        "/ipo/v2?status=listed",
    ]
    assert {row["id"] for row in world.rows("SELECT id FROM ipos")} == ALL_IPOS
    assert "10 new" in result.summary

    sunrise = world.ipo("sunrise-agro")
    assert sunrise["issue_type"] == "sme"
    assert sunrise["cutoff_price"] == Decimal("76.00")
    assert sunrise["min_qty"] == 3200
    assert sunrise["min_investment_rs"] == Decimal("243200.00")  # min_qty * cut-off, not lot * cut-off
    badband = world.ipo("badband-tech")
    assert badband["price_min"] is None and badband["min_investment_rs"] is None
    assert _effective_symbol(world, "acme-tech") == ("ACMETECH", "indianapi_v2")
    assert _effective_symbol(world, "sunrise-agro") is None  # no GMP mapping yet


def test_master_list_is_idempotent(world):
    world.run("master")
    second = world.run("master")
    assert "0 new" in second.summary
    assert world.rows("SELECT count(*) AS n FROM ipos")[0]["n"] == len(ALL_IPOS)
    assert world.rows("SELECT count(*) AS n FROM symbols")[0]["n"] == 1
    runs = world.rows("SELECT job, trigger, status FROM job_runs ORDER BY id")
    assert runs == [{"job": "master", "trigger": "cli", "status": "ok"}] * 2


def test_a_missing_field_never_erases_a_known_value(world):
    world.run("master")
    active = load("v2_active")
    for item in active["ipos"]:
        item.update(maximumPrice=None, cutOffPrice=None, lotSize=None, isin=None)
    world.transport.routes["/ipo/v2?status=active"] = active
    world.run("master")
    acme = world.ipo("acme-tech")
    assert acme["price_max"] == Decimal("100.00")
    assert acme["lot_size"] == 150
    assert acme["isin"] == "INE0ACM01015"


def test_upcoming_ipos_without_dates_become_dormant(world):
    world.run("master")
    # Quietpeak's pre-application date is over 30 days old and it still has no bidding dates.
    assert world.ipo("quietpeak-retail")["dormant"] is True
    assert world.ipo("meridian-power")["dormant"] is False

    # Without any dates at all, it turns dormant 30 days after we first saw it.
    upcoming = load("v2_upcoming")
    upcoming["ipos"][1]["timeline"] = {}
    upcoming["ipos"][1]["id"] = "silent-co"
    world.transport.routes["/ipo/v2?status=upcoming"] = upcoming
    world.run("master")
    assert world.ipo("silent-co")["dormant"] is False
    world.clock.advance(days=31)
    world.run("master")
    assert world.ipo("silent-co")["dormant"] is True


def test_low_quota_fetches_only_the_open_list(world):
    world.set_usage(250)
    result = world.run("master")
    assert world.transport.paths() == ["/ipo/v2?status=open", "/ipo/v2?status=active"]
    assert "open list only" in result.summary


def test_exhausted_quota_skips_everything(world):
    world.set_usage(0)
    for job in ("master", "open-details", "listed"):
        assert world.run(job).status is JobStatus.SKIPPED
    assert world.transport.requests == []


def test_one_failed_status_makes_a_partial_run(world):
    world.transport.routes["/ipo/v2?status=closed"] = status(500)
    result = world.run("master")
    assert result.status is JobStatus.PARTIAL
    assert "failed: closed" in result.summary
    assert "bluewave-logistics" not in {row["id"] for row in world.rows("SELECT id FROM ipos")}


# --- open details ----------------------------------------------------------------


def test_open_details_fetch_open_ipos_and_the_final_day_of_a_just_closed_one(world):
    world.run("master")
    world.transport.requests.clear()
    result = world.run("open-details")
    assert result.status is JobStatus.OK, result.summary
    assert sorted(world.transport.paths()) == ["/ipo/acme-tech", "/ipo/bluewave-logistics", "/ipo/sunrise-agro"]

    subs = world.rows(
        "SELECT bid_date, category, applied_shares, reserved_shares, times FROM subscription_snapshots "
        "WHERE ipo_id = 'acme-tech' ORDER BY bid_date, category"
    )
    assert len(subs) == 8
    last_overall = [row for row in subs if row["category"] == "overall"][-1]
    assert (last_overall["applied_shares"], last_overall["reserved_shares"], last_overall["times"]) == (
        11_250_000, 12_000_000, Decimal("0.9400"),
    )
    reserved = world.rows("SELECT category, reserved_shares FROM ipo_categories WHERE ipo_id = 'acme-tech'")
    assert {row["category"]: row["reserved_shares"] for row in reserved} == {
        "retail": 4_200_000, "nii": 1_800_000, "qib": 6_000_000,
    }
    acme = world.ipo("acme-tech")
    assert acme["registrar_name"] == "KFin Technologies Ltd"
    assert acme["allotment_link"] == "https://ipostatus.kfintech.example/"
    assert acme["detail_fetched_at"] is not None
    assert _effective_symbol(world, "acme-tech") == ("ACMETECH", "indianapi_detail")

    # The just-closed IPO got its final day; on the next run only the open ones are fetched again.
    world.transport.requests.clear()
    world.run("open-details")
    assert sorted(world.transport.paths()) == ["/ipo/acme-tech", "/ipo/sunrise-agro"]


def test_nothing_sensitive_reaches_the_database(world):
    world.run("master")
    world.run("open-details")
    world.run("backfill")
    with world.db.connection() as conn:
        dump = []
        for table in ("ipos", "ipo_categories", "subscription_snapshots", "symbols", "source_runs", "job_runs"):
            dump.extend(repr(row) for row in conn.execute(f"SELECT * FROM {table}"))
    text = "\n".join(dump)
    for forbidden in ("X-Amz", "Signature", "rhp.pdf", "Jane Placeholder", "placeholder@example.com",
                      "+91-40", "sebi.gov.in", "sig=signedvalue", TEST_KEY):
        assert forbidden not in text


def test_detail_failures_are_counted_and_a_run_stops_after_three_in_a_row(world):
    world.run("master")
    for ipo_id in ("acme-tech", "sunrise-agro", "bluewave-logistics"):
        world.transport.routes[f"/ipo/{ipo_id}"] = status(404, {"detail": "not found"})
    result = world.run("open-details")
    assert result.status is JobStatus.FAILED
    assert "stopped after 3 failures in a row" in result.summary
    failures = world.rows("SELECT id, detail_failures FROM ipos WHERE detail_failures > 0 ORDER BY id")
    assert [row["id"] for row in failures] == ["acme-tech", "bluewave-logistics", "sunrise-agro"]


def test_server_errors_do_not_count_against_the_ipo(world):
    world.run("master")
    world.transport.routes["/ipo/acme-tech"] = status(503)
    world.run("open-details")
    assert world.transport.paths().count("/ipo/acme-tech") == 4  # 1 try + 3 retries
    assert world.ipo("acme-tech")["detail_failures"] == 0
    assert world.ipo("sunrise-agro")["detail_fetched_at"] is not None


# --- listed snapshot -----------------------------------------------------------------


def test_listed_snapshot_maps_symbols_by_name_and_flags_outliers(world):
    world.run("master")
    result = world.run("listed")
    assert result.status is JobStatus.OK
    assert "1 listed without a listing price skipped" in result.summary
    assert "4 listings stored (2 flagged" in result.summary

    assert _effective_symbol(world, "sunrise-agro") == ("SUNRISE", "indianapi_list")
    assert _effective_symbol(world, "northstar-optics") == ("NORTHSTAR", "indianapi_list")
    # v2 had a symbol for Acme, and v2 beats a name match.
    assert _effective_symbol(world, "acme-tech") == ("ACMETECH", "indianapi_v2")

    kesar = world.ipo("kesar-spices")
    assert (kesar["listing_price"], kesar["listing_gain_pct"]) == (Decimal("228.00"), Decimal("90.00"))
    assert kesar["listing_flag"] is None
    assert world.ipo("flatline-industries")["listing_flag"] == "possible_price_band_default"
    assert world.ipo("dropcap-engineering")["listing_flag"] == "possible_price_band_default"
    assert world.ipo("dropcap-engineering")["listing_gain_pct"] == Decimal("-20.00")


def test_listing_from_the_detail_endpoint_beats_the_name_matched_list(world):
    world.run("master")
    world.run("listed")
    assert world.ipo("northstar-optics")["listing_price"] == Decimal("310.00")
    world.run("backfill", limit=1)
    northstar = world.ipo("northstar-optics")
    assert (northstar["listing_price"], northstar["listing_gain_pct"]) == (Decimal("330.00"), Decimal("32.00"))
    assert northstar["listing_source"] == "indianapi_detail"
    world.run("listed")
    assert world.ipo("northstar-optics")["listing_price"] == Decimal("330.00")


def test_manual_symbol_overrides_and_can_be_cleared(world):
    world.run("master")
    with world.db.connection() as conn:
        assert set_manual_symbol(conn, "acme-tech", " acme1 ", world.clock.now()) == "ACME1"
    assert _effective_symbol(world, "acme-tech") == ("ACME1", "manual")
    world.run("master")
    assert _effective_symbol(world, "acme-tech") == ("ACME1", "manual")
    with world.db.connection() as conn:
        assert clear_manual_symbol(conn, "acme-tech")
        with pytest.raises(LookupError):
            set_manual_symbol(conn, "no-such-ipo", "X", world.clock.now())
    assert _effective_symbol(world, "acme-tech") == ("ACMETECH", "indianapi_v2")


# --- usage and backfill ---------------------------------------------------------------


def test_usage_check_records_the_reading_and_warns_when_low(world, caplog):
    result = world.run("usage")
    assert "8750 of 10000" in result.summary
    assert world.rows("SELECT remaining FROM api_usage")[0]["remaining"] == 8750

    world.transport.routes["/usage"] = {"hard_limit": 10000, "remaining_requests": 900}
    with caplog.at_level(logging.WARNING):
        world.run("usage")
    assert "backfill paused" in caplog.text
    world.transport.routes["/usage"] = {"hard_limit": 10000, "remaining_requests": 299}
    with caplog.at_level(logging.WARNING):
        world.run("usage")
    assert "only essential calls will run" in caplog.text


def test_backfill_is_paced_resumable_and_newest_first(world):
    world.run("master")
    world.transport.requests.clear()

    first = world.run("backfill", limit=2)
    assert first.status is JobStatus.OK, first.summary
    assert world.transport.paths() == ["/usage", "/ipo/northstar-optics", "/ipo/kesar-spices"]
    assert world.sleep.calls == [3.0]  # paced between calls, not before the first
    assert "2 listed IPOs still to backfill" in first.summary
    history = world.rows("SELECT count(DISTINCT bid_date) AS n FROM subscription_snapshots WHERE ipo_id = 'northstar-optics'")
    assert history[0]["n"] == 3

    world.transport.requests.clear()
    second = world.run("backfill")
    assert world.transport.paths() == ["/usage", "/ipo/flatline-industries", "/ipo/dropcap-engineering"]
    assert "0 listed IPOs still to backfill" in second.summary
    assert world.run("backfill").summary == "nothing to backfill"


def test_backfill_pauses_below_the_threshold(world):
    world.run("master")
    world.transport.routes["/usage"] = {"hard_limit": 10000, "remaining_requests": 999}
    world.transport.requests.clear()
    result = world.run("backfill")
    assert result.status is JobStatus.SKIPPED
    assert "paused" in result.summary
    assert world.transport.paths() == ["/usage"]


def test_backfill_stops_when_its_own_calls_cross_the_threshold(world):
    world.run("master")
    world.transport.routes["/usage"] = {"hard_limit": 10000, "remaining_requests": 1001}
    world.transport.requests.clear()
    result = world.run("backfill")
    # 1001 -> 1000 after the first call (still allowed) -> 999 after the second: pause.
    assert world.transport.paths() == ["/usage", "/ipo/northstar-optics", "/ipo/kesar-spices"]
    assert result.status is JobStatus.PARTIAL
    assert "paused" in result.summary


def test_backfill_leaves_out_ipos_that_keep_failing(world):
    world.run("master")
    world.transport.routes["/ipo/northstar-optics"] = status(404)
    for _ in range(3):
        world.run("backfill", limit=1)
    assert world.ipo("northstar-optics")["detail_failures"] == 3
    world.transport.requests.clear()
    world.run("backfill", limit=1)
    assert world.transport.paths() == ["/usage", "/ipo/kesar-spices"]


def test_a_crashing_job_is_recorded_with_secrets_redacted(world, monkeypatch):
    def explode(ctx):
        raise RuntimeError(f"boom with {TEST_KEY}")

    monkeypatch.setitem(jobs.JOBS, "usage", explode)
    result = world.run("usage")
    assert result.status is JobStatus.FAILED
    assert TEST_KEY not in result.summary and "***" in result.summary
    stored = world.rows("SELECT status, summary FROM job_runs")[0]
    assert stored["status"] == "failed" and TEST_KEY not in stored["summary"]


def test_open_details_catch_up_on_missed_final_days_but_skip_listed_ipos(world):
    world.run("master")
    world.clock.advance(days=4)  # Saturday 07 Nov: nothing is open any more
    world.transport.requests.clear()
    world.run("open-details")
    # Acme and Sunrise closed without a fetch after their last day; Bluewave has already listed.
    assert sorted(world.transport.paths()) == ["/ipo/acme-tech", "/ipo/sunrise-agro"]
    world.transport.requests.clear()
    assert world.run("open-details").summary == "no open IPOs and no missing final days"
    assert world.transport.requests == []
