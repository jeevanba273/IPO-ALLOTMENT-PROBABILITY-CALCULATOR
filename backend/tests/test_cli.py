import pytest

from ipo_intel.cli import main
from tests.fakes import make_settings


def test_config_shows_which_secrets_are_set_but_never_values(capsys):
    settings = make_settings(
        indianapi_key="ia-SECRET-abcdef",
        database_url="postgresql://ipo:pw-SECRET-123@localhost:5432/ipo_intel",
    )
    assert main(["config"], settings=settings) == 0
    out = capsys.readouterr().out
    assert "INDIANAPI_KEY" in out and "IPOALERTS_KEY" in out
    lines = {line.split()[0]: line.split()[-1] for line in out.splitlines() if line.startswith("  ") and line.split()}
    assert lines["INDIANAPI_KEY"] == "set"
    assert lines["IPOALERTS_KEY"] == "missing"
    assert "ia-SECRET-abcdef" not in out
    assert "pw-SECRET-123" not in out
    assert "12/day (backfill max 8), 400/month" in out


def test_migrate_then_up_to_date(fresh_db_url, capsys):
    settings = make_settings(database_url=fresh_db_url)
    assert main(["migrate"], settings=settings) == 0
    assert "applied 0001_init.sql" in capsys.readouterr().out
    assert main(["migrate"], settings=settings) == 0
    assert "database is up to date" in capsys.readouterr().out
    assert main(["migrations"], settings=settings) == 0
    assert "pending" not in capsys.readouterr().out


def test_commands_needing_a_database_fail_cleanly_without_one(capsys):
    assert main(["migrate"], settings=make_settings()) == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


def test_budget_and_runs_commands(database, migrated_db_url, capsys):
    settings = make_settings(database_url=migrated_db_url)
    assert main(["budget"], settings=settings) == 0
    out = capsys.readouterr().out
    assert "today      0/12" in out
    assert "this month 0/400" in out
    assert main(["runs", "--limit", "5"], settings=settings) == 0
    assert "no source runs recorded" in capsys.readouterr().out


def test_unknown_command_exits_with_usage():
    with pytest.raises(SystemExit) as caught:
        main(["nope"], settings=make_settings())
    assert caught.value.code == 2
