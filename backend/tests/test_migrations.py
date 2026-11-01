from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg import errors

from ipo_intel.db import connect
from ipo_intel.migrate import MigrationError, apply_migrations, discover, migration_status
from tests.conftest import MIGRATIONS_DIR

EXPECTED_TABLES = {
    "ipos",
    "ipo_categories",
    "subscription_snapshots",
    "gmp_snapshots",
    "symbols",
    "source_runs",
    "api_budget",
    "api_blocks",
    "alerts_log",
    "api_usage",
    "job_runs",
    "reservation_overrides",
    "schema_migrations",
}


def _write(directory, name, sql):
    (directory / name).write_text(sql, encoding="utf-8", newline="\n")


def _insert_ipo(conn, ipo_id="tonbo-imaging", **overrides):
    values = {
        "id": ipo_id,
        "name": "Tonbo Imaging India Limited",
        "issue_type": "mainboard",
        "lot_size": 30,
        "min_qty": 30,
        "cutoff_price": Decimal("475.00"),
        **overrides,
    }
    columns = ", ".join(values)
    placeholders = ", ".join(f"%({key})s" for key in values)
    conn.execute(f"INSERT INTO ipos ({columns}) VALUES ({placeholders})", values)


def test_schema_has_every_table(database):
    with database.connection() as conn:
        rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
    assert EXPECTED_TABLES <= {row["tablename"] for row in rows}


def test_migrations_apply_once(fresh_db_url):
    with connect(fresh_db_url) as conn:
        expected = [migration.name for migration in discover(MIGRATIONS_DIR)]
        assert expected[:3] == ["0001_init.sql", "0002_ingestion.sql", "0003_reservation_overrides.sql"]
        assert apply_migrations(conn, MIGRATIONS_DIR) == expected
        assert apply_migrations(conn, MIGRATIONS_DIR) == []
        states = migration_status(conn, MIGRATIONS_DIR)
    assert [s.migration.name for s in states] == expected
    assert all(state.applied_at is not None for state in states)


def test_status_before_any_migration(fresh_db_url):
    with connect(fresh_db_url) as conn:
        states = migration_status(conn, MIGRATIONS_DIR)
    assert all(state.applied_at is None for state in states)


def test_editing_an_applied_migration_is_rejected(fresh_db_url, tmp_path):
    _write(tmp_path, "0001_first.sql", "CREATE TABLE probe_a (id int);")
    with connect(fresh_db_url) as conn:
        apply_migrations(conn, tmp_path)
        _write(tmp_path, "0001_first.sql", "CREATE TABLE probe_a (id bigint);")
        with pytest.raises(MigrationError, match="changed after it was applied"):
            apply_migrations(conn, tmp_path)


def test_failed_migration_is_rolled_back_and_not_recorded(fresh_db_url, tmp_path):
    _write(tmp_path, "0001_good.sql", "CREATE TABLE probe_good (id int);")
    _write(tmp_path, "0002_bad.sql", "CREATE TABLE probe_half (id int);\nSELECT * FROM no_such_table;")
    with connect(fresh_db_url) as conn:
        with pytest.raises(MigrationError, match="0002_bad.sql failed"):
            apply_migrations(conn, tmp_path)
        tables = {
            row["tablename"]
            for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        versions = [row["version"] for row in conn.execute("SELECT version FROM schema_migrations")]
    assert "probe_good" in tables
    assert "probe_half" not in tables
    assert versions == ["0001"]


def test_checksum_ignores_line_endings(tmp_path):
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    lf.mkdir()
    crlf.mkdir()
    (lf / "0001_x.sql").write_bytes(b"CREATE TABLE x (id int);\nSELECT 1;\n")
    (crlf / "0001_x.sql").write_bytes(b"CREATE TABLE x (id int);\r\nSELECT 1;\r\n")
    assert discover(lf)[0].checksum == discover(crlf)[0].checksum


@pytest.mark.parametrize("files", [["1_init.sql"], ["0001_Init.sql"], ["0001_a.sql", "0001_b.sql"]])
def test_bad_migration_files_are_rejected(tmp_path, files):
    for name in files:
        _write(tmp_path, name, "SELECT 1;")
    with pytest.raises(MigrationError):
        discover(tmp_path)


def test_min_investment_is_derived_from_min_qty(database):
    # SME with a 2-lot minimum: 2 x 1200 shares at Rs 120.
    with database.connection() as conn:
        _insert_ipo(conn, "sollfege", issue_type="sme", lot_size=1200, min_qty=2400, cutoff_price=Decimal("120"))
        row = conn.execute("SELECT min_investment_rs FROM ipos WHERE id = 'sollfege'").fetchone()
    assert row["min_investment_rs"] == Decimal("288000.00")


def test_natural_keys_prevent_duplicate_snapshots(database):
    snapshot = (
        "INSERT INTO subscription_snapshots (ipo_id, bid_date, category, applied_shares, reserved_shares, times) "
        "VALUES ('tonbo-imaging', %s, 'retail', 1000, 500, 2.0) "
    )
    with database.connection() as conn:
        _insert_ipo(conn)
        conn.execute(snapshot, (date(2026, 11, 2),))
        conn.execute(snapshot + "ON CONFLICT DO NOTHING", (date(2026, 11, 2),))
        assert conn.execute("SELECT count(*) AS n FROM subscription_snapshots").fetchone()["n"] == 1
        with pytest.raises(errors.UniqueViolation):
            conn.execute(snapshot, (date(2026, 11, 2),))


@pytest.mark.parametrize(
    ("statement", "violation"),
    [
        (
            "INSERT INTO gmp_snapshots (ipo_id, ts_utc, interval, gmp_rs, source) "
            "VALUES ('tonbo-imaging', now(), 'daily', 10, 'manual')",
            errors.CheckViolation,
        ),
        (
            "INSERT INTO api_budget (source, day, month, purpose, count) "
            "VALUES ('ipoalerts', '2026-11-02', '2026-10-01', 'live', 1)",
            errors.CheckViolation,
        ),
        (
            "INSERT INTO ipos (id, name, issue_type, price_min, price_max) "
            "VALUES ('bad-band', 'Bad Band Ltd', 'mainboard', 110, 100)",
            errors.CheckViolation,
        ),
        (
            "INSERT INTO ipos (id, name, issue_type) VALUES ('bad-type', 'Bad Type Ltd', 'Mainboard')",
            errors.CheckViolation,
        ),
        (
            "INSERT INTO symbols (ipo_id, symbol, source) VALUES ('tonbo-imaging', '', 'manual')",
            errors.CheckViolation,
        ),
    ],
)
def test_constraints_reject_bad_rows(database, statement, violation):
    with database.connection() as conn:
        _insert_ipo(conn)
        with pytest.raises(violation):
            conn.execute(statement)


def test_sessions_run_in_utc_with_utf8(database):
    with database.connection() as conn:
        row = conn.execute("SELECT current_setting('TimeZone') AS tz, current_setting('client_encoding') AS enc").fetchone()
        _insert_ipo(conn, "shri-ganesh", name="श्री गणेश Industries Ltd")
        name = conn.execute("SELECT name FROM ipos WHERE id = 'shri-ganesh'").fetchone()["name"]
    assert row == {"tz": "UTC", "enc": "UTF8"}
    assert name == "श्री गणेश Industries Ltd"


def test_database_rolls_back_a_failed_block(database):
    with pytest.raises(psycopg.errors.UndefinedTable):
        with database.connection() as conn:
            _insert_ipo(conn)
            conn.execute("SELECT * FROM no_such_table")
    with database.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ipos").fetchone()["n"] == 0
