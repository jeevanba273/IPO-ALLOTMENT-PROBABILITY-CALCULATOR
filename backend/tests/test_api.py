"""The dashboard API over the fixture world (IndianAPI + ipoalerts fakes, real Postgres)."""

import pytest
from fastapi.testclient import TestClient

from ipo_intel.api.app import create_app
from ipo_intel.engine.reservation import VERIFY_RHP
from tests.gmp_world import gmp_paths, gmp_world


@pytest.fixture
def world(database, migrated_db_url):
    w = gmp_world(database, migrated_db_url)
    w.run("open-details")
    w.run("backfill")
    w.run("gmp-live", slot="morning")
    w.run("gmp-backfill")
    w.transport.requests.clear()
    yield w
    w.close()


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>IPO</title><div id=root></div>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    return root


@pytest.fixture
def client(world, dist):
    app = create_app(
        world.settings, db=world.db, runner=world.runner, clock=world.clock,
        start_scheduler=False, migrate=False, frontend_dist=dist,
    )
    with TestClient(app) as test_client:
        yield test_client


def _ipo(payload, ipo_id):
    return next(item for item in payload["ipos"] if item["id"] == ipo_id)


def test_ipo_list(client):
    payload = client.get("/api/ipos").json()
    assert payload["today"] == "2026-11-03"
    acme = _ipo(payload, "acme-tech")
    assert (acme["stage"], acme["board"], acme["min_category"]) == ("open", "mainboard", "retail")
    assert (acme["min_lots"], acme["max_retail_lots"], acme["min_investment"]) == (1, 13, 15000.0)
    assert acme["subscription"]["day"] == 2 and acme["subscription"]["overall"] == pytest.approx(0.94)
    assert acme["gmp"]["confidence"] == "high" and acme["gmp"]["source"] == "ipoalerts"
    assert acme["symbol"] == "ACMETECH"
    sunrise = _ipo(payload, "sunrise-agro")
    assert sunrise["min_category"] == "snii" and sunrise["gmp"]["confidence"] == "low"
    northstar = _ipo(payload, "northstar-optics")
    assert northstar["stage"] == "listed" and northstar["listing"]["gain_pct"] == pytest.approx(32.0)
    dropcap = _ipo(payload, "dropcap-engineering")
    assert dropcap["listing"]["flagged"] is True


def test_ipo_detail(client):
    detail = client.get("/api/ipos/acme-tech").json()
    assert [d["bid_date"] for d in detail["subscription_history"]] == ["2026-11-02", "2026-11-03"]
    assert detail["subscription_history"][1]["retail"] == pytest.approx(1.45)
    assert len(detail["gmp_series"]) > 40 and detail["gmp_series"][0]["ts"].endswith("Z")
    assert detail["reservation"][0] == {"category": "retail", "indianapi": 4_200_000, "manual": None, "note": None}
    assert detail["reservation_warning"] == VERIFY_RHP
    assert detail["registrar_name"] == "KFin Technologies Ltd"
    analysis = detail["analysis"]
    assert analysis["category"] == "retail" and analysis["method"] == "lottery"
    assert analysis["odds_now"]["p_low"] == pytest.approx(1 / 1.45)
    assert analysis["gmp"]["confidence"] == "high" and analysis["ev"]["point"] is not None
    assert client.get("/api/ipos/nope").status_code == 404
    # An IPO without a price band still has a page, just no calculator.
    assert client.get("/api/ipos/quietpeak-retail").json()["analysis"] is None


def test_calculator(client):
    odds = client.get("/api/ipos/acme-tech/odds", params={"lots": 14, "apps": 2, "gmp": 10, "confidence": "high"}).json()
    assert (odds["lots"], odds["applications"], odds["category"]) == (14, 2, "snii")
    assert odds["method"] == "proportionate" and odds["gmp_used"] == 10
    assert odds["ev"]["confidence"] == "high" and odds["ev"]["point"] is not None
    bad = client.get("/api/ipos/sunrise-agro/odds", params={"lots": 1})
    assert bad.status_code == 422 and "minimum bid is 2 lot" in bad.json()["detail"]
    assert client.get("/api/ipos/nope/odds").status_code == 404
    assert client.get("/api/ipos/acme-tech/odds", params={"apps": 0}).status_code == 422


def test_calibration(client):
    payload = client.get("/api/calibration").json()
    northstar = next(r for r in payload["rows"] if r["id"] == "northstar-optics")
    assert northstar["actual_pct"] == pytest.approx(32.0)
    assert northstar["gmp_rs"] is not None and northstar["gmp_ts"] < "2026-10-21"
    assert northstar["implied_pct"] == pytest.approx(northstar["gmp_rs"] / 250 * 100)
    assert northstar["error_pp"] == pytest.approx(northstar["implied_pct"] - 32.0)
    assert set(payload["summary"]) == {"mainboard", "sme"}
    assert payload["summary"]["mainboard"]["with_gmp"] >= 1
    assert payload["histories"] == {"mainboard": 3, "sme": 1}
    assert any(m["board"] == "mainboard" and m["category"] == "overall" for m in payload["multipliers"])


def test_health(client):
    payload = client.get("/api/health").json()
    assert payload["indianapi"]["remaining"] is not None
    assert payload["ipoalerts"]["configured"] is True
    assert payload["ipoalerts"]["used_today"] == payload["ipoalerts"]["live_today"] + payload["ipoalerts"]["backfill_today"]
    assert payload["ipoalerts"]["daily_cap"] == 12
    jobs = {j["job"]: j for j in payload["jobs"]}
    assert jobs["gmp-live"]["budgeted"] and jobs["gmp-live"]["scheduled"] and jobs["gmp-live"]["next_run"]
    assert jobs["backfill"]["scheduled"] is False
    assert jobs["master"]["status"] == "ok"
    assert payload["recent_calls"] and payload["counts"]["gmp_points"] > 0
    assert payload["scheduler_running"] is False


def test_manual_fixes(client):
    assert client.put("/api/ipos/sunrise-agro/symbol", json={"symbol": "sunr1"}).json()["symbol"] == "SUNR1"
    assert client.get("/api/ipos/sunrise-agro").json()["symbol"] == "SUNR1"
    assert client.delete("/api/ipos/sunrise-agro/symbol").json()["removed"] is True
    assert client.put("/api/ipos/nope/symbol", json={"symbol": "X"}).status_code == 404

    posted = client.post("/api/ipos/acme-tech/gmp", json={"gmp_rs": 33.5})
    assert posted.status_code == 200
    gmp = client.get("/api/ipos/acme-tech").json()["gmp"]
    assert (gmp["source"], gmp["gmp_rs"]) == ("manual", 33.5)
    assert client.post("/api/ipos/acme-tech/gmp", json={"gmp_rs": 1, "at": "2026-11-03T09:00:00"}).status_code == 422
    assert client.post("/api/ipos/acme-tech/gmp", json={"gmp_rs": 1, "at": "2030-01-01T09:00:00+05:30"}).status_code == 422
    assert client.delete("/api/ipos/acme-tech/gmp").json()["removed"] == 1

    for category, shares in (("retail", 4_000_000), ("nii", 1_800_000), ("qib", 6_000_000)):
        assert client.put(f"/api/ipos/acme-tech/reservation/{category}", json={"shares": shares, "note": "RHP"}).status_code == 200
    detail = client.get("/api/ipos/acme-tech").json()
    assert detail["reservation_warning"] is None and detail["analysis"]["warnings"] == []
    assert detail["reservation"][0]["manual"] == 4_000_000
    assert client.delete("/api/ipos/acme-tech/reservation/retail").json()["removed"] == 1
    assert client.put("/api/ipos/acme-tech/reservation/employee", json={"shares": 5}).status_code == 422


def test_jobs_from_the_dashboard(client, world):
    refused = client.post("/api/jobs/gmp-live/run")
    assert refused.status_code == 403 and "only its schedule" in refused.json()["detail"]
    assert client.post("/api/jobs/gmp-backfill/run").status_code == 403
    assert client.post("/api/jobs/nope/run").status_code == 404
    ran = client.post("/api/jobs/master/run")
    assert ran.status_code == 202 and ran.json()["status"] == "ok"
    assert world.rows("SELECT trigger FROM job_runs ORDER BY id DESC LIMIT 1")[0]["trigger"] == "dashboard"


def test_browsing_never_calls_ipoalerts(client, world):
    for path in ("/api/ipos", "/api/ipos/acme-tech", "/api/ipos/acme-tech/odds", "/api/calibration", "/api/health"):
        assert client.get(path).status_code == 200
    assert gmp_paths(world) == []
    assert world.transport.requests == []


def test_serves_the_built_dashboard(client):
    assert "<div id=root>" in client.get("/").text
    assert "<div id=root>" in client.get("/ipo/acme-tech").text  # client-side route
    assert client.get("/assets/app.js").text == "console.log('ok')"
    assert client.get("/api/nope").status_code == 404
    assert "nope" not in client.get("/../secret.txt").text


def test_explains_when_the_dashboard_is_not_built(world, tmp_path):
    app = create_app(world.settings, db=world.db, runner=world.runner, clock=world.clock,
                     start_scheduler=False, migrate=False, frontend_dist=tmp_path / "missing")
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 503 and "npm run build" in response.text
