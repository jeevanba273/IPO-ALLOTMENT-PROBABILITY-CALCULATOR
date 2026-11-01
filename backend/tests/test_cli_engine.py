from datetime import date

import pytest

import ipo_intel.cli as cli
from ipo_intel.cli import main
from tests.indianapi_world import World


@pytest.fixture
def world(database, migrated_db_url, monkeypatch):
    w = World(database, migrated_db_url)
    w.run("master")
    w.run("open-details")
    w.run("backfill")
    monkeypatch.setattr(cli, "ist_today", lambda: date(2026, 11, 3))
    yield w
    w.close()


def test_odds_for_a_retail_ipo(world, capsys):
    assert main(["odds", "acme-tech", "--apps", "2", "--gmp", "20"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert "Acme Tech Ltd  ·  Mainboard  ·  open" in out
    assert "Minimum bid   150 shares (1 lot) = ₹15,000  →  Retail" in out
    assert "day 2 (03 Nov, still bidding)" in out
    assert "P(allotment) per application  69.0% – 100%" in out
    assert "P(at least one of 2 applications)" in out
    assert "not enough history" in out
    assert "(no single figure for low-confidence GMP)" in out
    assert "verify them against the RHP" in out


def test_odds_for_an_sme_ipo_says_the_minimum_bid_is_nii(world, capsys):
    assert main(["odds", "sunrise-agro"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert "→  sNII (₹2–10 lakh)" in out
    assert "Note: your minimum bid is NII for this IPO" in out
    assert "P(at least 1 lot) per application  100%" in out


def test_odds_errors(world, capsys):
    assert main(["odds", "sunrise-agro", "--lots", "1"], settings=world.settings) == 1
    assert "minimum bid is 2 lot" in capsys.readouterr().err
    assert main(["odds", "nope"], settings=world.settings) == 1


def test_multipliers_table(world, capsys):
    assert main(["multipliers"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert "Complete histories: 3 mainboard, 1 SME" in out
    assert "not enough history" in out


def test_reservation_commands(world, capsys):
    assert main(["reservation", "set", "acme-tech", "retail", "4500000", "--note", "RHP"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert "retail reserved shares set to 4,500,000" in out
    assert "4,200,000" in out and "4,500,000" in out
    assert main(["reservation", "clear", "acme-tech", "retail"], settings=world.settings) == 0
    assert "1 override(s) removed" in capsys.readouterr().out
    assert main(["reservation", "set", "acme-tech"], settings=world.settings) == 2
    assert main(["reservation", "set", "nope", "retail", "5"], settings=world.settings) == 1
