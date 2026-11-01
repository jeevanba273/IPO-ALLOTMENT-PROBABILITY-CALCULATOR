from datetime import date

import pytest

import ipo_intel.cli as cli
from ipo_intel.cli import main
from tests.gmp_world import gmp_paths, gmp_world


class _Shared:
    def __init__(self, runner):
        self._runner = runner

    def __enter__(self):
        return self._runner

    def __exit__(self, *exc_info):
        return None


@pytest.fixture
def world(database, migrated_db_url, monkeypatch):
    w = gmp_world(database, migrated_db_url)
    monkeypatch.setattr(cli, "make_runner", lambda settings: _Shared(w.runner))
    monkeypatch.setattr(cli, "ist_today", lambda: date(2026, 11, 3))
    monkeypatch.setattr(cli, "utc_now", w.clock.now)
    yield w
    w.close()


def test_gmp_jobs_refuse_to_run_by_hand_without_saying_so(world, capsys):
    assert main(["run", "gmp-live"], settings=world.settings) == 2
    assert "--spend-budget" in capsys.readouterr().err
    assert main(["run", "gmp-backfill"], settings=world.settings) == 2
    assert gmp_paths(world) == []
    assert main(["run", "gmp-live", "--spend-budget"], settings=world.settings) == 0
    assert "gmp-live: ok - fetched 3 of 3" in capsys.readouterr().out


def test_reading_screens_never_call_ipoalerts(world, capsys):
    main(["run", "gmp-live", "--spend-budget"], settings=world.settings)
    world.transport.requests.clear()
    for argv in (["ipos"], ["ipo", "acme-tech"], ["odds", "acme-tech"], ["gmp", "show", "acme-tech"], ["budget"]):
        assert main(argv, settings=world.settings) == 0
    assert world.transport.requests == []
    out = capsys.readouterr().out
    assert "this month 3/400" in out  # the three calls of the live poll, and nothing since


def test_odds_and_ipos_show_gmp_with_its_confidence(world, capsys):
    main(["run", "open-details"], settings=world.settings)
    main(["run", "gmp-live", "--spend-budget"], settings=world.settings)
    capsys.readouterr()
    assert main(["odds", "acme-tech"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert "from ipoalerts" in out and "[HIGH confidence]" in out
    assert "most likely about" in out
    assert main(["odds", "sunrise-agro"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert "[low confidence]" in out and "no single figure for low-confidence GMP" in out
    assert main(["ipos"], settings=world.settings) == 0
    lines = capsys.readouterr().out.splitlines()
    sunrise = next(line for line in lines if "Sunrise Agro" in line)
    assert "₹12 +16% low" in sunrise  # ₹12 on a ₹76 cut-off


def test_manual_gmp_commands(world, capsys):
    assert main(["gmp", "set", "acme-tech", "22.5", "--at", "2026-11-03 09:45"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert "manual GMP ₹22.50 at 03 Nov 2026, 09:45 IST" in out
    assert "latest   ₹22.50" in out and "manual" in out
    assert main(["gmp", "clear", "acme-tech"], settings=world.settings) == 0
    out = capsys.readouterr().out
    assert "1 manual GMP entries removed" in out and "no GMP yet" in out
    assert main(["gmp", "set", "acme-tech"], settings=world.settings) == 2
    assert main(["gmp", "set", "acme-tech", "5", "--at", "tomorrow"], settings=world.settings) == 2
    assert main(["gmp", "show", "nope"], settings=world.settings) == 1
