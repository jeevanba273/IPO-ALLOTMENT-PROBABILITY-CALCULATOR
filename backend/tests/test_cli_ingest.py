from datetime import date

import pytest

import ipo_intel.cli as cli
from ipo_intel.cli import _inr, main
from tests.indianapi_world import TEST_KEY, World


@pytest.fixture
def world(database, migrated_db_url, monkeypatch):
    w = World(database, migrated_db_url)
    monkeypatch.setattr(cli, "make_runner", lambda settings: _NoClose(w.runner))
    monkeypatch.setattr(cli, "ist_today", lambda: date(2026, 11, 3))
    yield w
    w.close()


class _NoClose:
    """Lets the CLI use the test runner without closing the shared pool."""

    def __init__(self, runner):
        self._runner = runner

    def __enter__(self):
        return self._runner

    def __exit__(self, *exc_info):
        return None


def test_run_prints_the_job_result(world, capsys):
    assert main(["run", "master"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert out.startswith("master: ok - upcoming 3, open 2, closed 1, listed 4; 10 new")
    assert TEST_KEY not in out


def test_run_backfill_takes_a_limit(world, capsys):
    main(["run", "master"], settings=world.settings)
    assert main(["run", "backfill", "--limit", "1"], settings=world.settings) == 0
    assert "fetched 1 of 1; 3 listed IPOs still to backfill" in capsys.readouterr().out


def test_failed_job_exits_nonzero(world, capsys):
    world.transport.routes["/usage"] = {"unexpected": True}
    assert main(["run", "usage"], settings=world.settings) == 1
    assert "usage: failed" in capsys.readouterr().out


def test_ipos_lists_open_first_with_mapping_status(world, capsys):
    main(["run", "master"], settings=world.settings)
    main(["run", "open-details"], settings=world.settings)
    capsys.readouterr()
    assert main(["ipos"], settings=world.settings) == 0
    lines = capsys.readouterr().out.splitlines()
    body = [line for line in lines[1:] if line.strip() and not line.startswith(("1", "2", "3", "4", "5", "6", "7", "8", "9"))]
    assert body[0].startswith("open")
    acme = next(line for line in lines if "Acme Tech" in line)
    assert "ACMETECH" in acme and "₹15,000" in acme and "0.94x" in acme and "02 Nov–04 Nov" in acme
    sunrise = next(line for line in lines if "Sunrise Agro" in line)
    assert "no GMP mapping" in sunrise and "SME" in sunrise and "₹2,43,200" in sunrise
    assert not any("Quietpeak" in line for line in lines)  # dormant, hidden by default
    assert "hidden" in lines[-1]

    assert main(["ipos", "--all"], settings=world.settings) == 0
    assert "Quietpeak Retail Limited" in capsys.readouterr().out
    assert main(["ipos", "--lifecycle", "listed"], settings=world.settings) == 0
    listed = capsys.readouterr().out
    assert "Northstar" in listed and "Acme" not in listed


def test_ipo_detail_shows_daywise_subscription(world, capsys):
    main(["run", "master"], settings=world.settings)
    main(["run", "open-details"], settings=world.settings)
    main(["run", "listed"], settings=world.settings)
    capsys.readouterr()
    assert main(["ipo", "acme-tech"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert "Acme Tech Ltd  (acme-tech)  mainboard · open" in out
    assert "KFin Technologies Ltd (allotment link stored)" in out
    assert "retail 42,00,000".replace("42,00,000", "4,200,000") in out
    day2 = next(line for line in out.splitlines() if line.strip().startswith("03 Nov"))
    assert day2.split() == ["03", "Nov", "1.45x", "1.20x", "0.50x", "0.94x"]

    assert main(["ipo", "dropcap-engineering"], settings=world.settings) == 0
    assert "[possible price-band/default]" in capsys.readouterr().out
    assert main(["ipo", "nope"], settings=world.settings) == 1


def test_symbol_override_commands(world, capsys):
    main(["run", "master"], settings=world.settings)
    capsys.readouterr()
    assert main(["symbol", "set", "sunrise-agro", "sunrise"], settings=world.settings) == 0
    assert "symbol set to SUNRISE (manual" in capsys.readouterr().out
    main(["ipo", "sunrise-agro"], settings=world.settings)
    assert "SUNRISE via manual" in capsys.readouterr().out
    assert main(["symbol", "clear", "sunrise-agro"], settings=world.settings) == 0
    assert "manual symbol removed" in capsys.readouterr().out
    assert main(["symbol", "set", "nope", "X"], settings=world.settings) == 1
    assert main(["symbol", "set", "sunrise-agro"], settings=world.settings) == 2


def test_jobs_shows_last_runs_and_quota(world, capsys):
    main(["run", "usage"], settings=world.settings)
    main(["run", "master"], settings=world.settings)
    capsys.readouterr()
    assert main(["jobs"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert "master        ok" in out
    assert "backfill      never run" in out
    assert "IndianAPI quota: about" in out and "of 10000" in out


def test_inr_uses_indian_grouping():
    assert _inr(243200) == "₹2,43,200"
    assert _inr(15000) == "₹15,000"
    assert _inr(999) == "₹999"
    assert _inr(12345678) == "₹1,23,45,678"
    assert _inr(-1500) == "-₹1,500"
    assert _inr(None) == "-"
