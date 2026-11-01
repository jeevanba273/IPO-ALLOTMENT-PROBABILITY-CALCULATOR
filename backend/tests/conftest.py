"""Shared fixtures.

Tests never touch the network: HTTP goes through tests.fakes.FakeTransport and
pytest-socket only lets sockets reach loopback. Database tests run against a
real, throwaway Postgres 16 that pgserver starts in a temp directory, or
against TEST_DATABASE_URL if you set it (its database name must contain
"test", its tables get truncated, and the role needs CREATEDB).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from ipo_intel.config import REPO_ROOT, Settings
from ipo_intel.db import Database, connect
from ipo_intel.devdb import ensure_timezone_files, install_root
from ipo_intel.migrate import apply_migrations

MIGRATIONS_DIR = REPO_ROOT / "migrations"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real keys and overrides out of every test."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture(scope="session")
def pg_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        dbname = str(conninfo_to_dict(url).get("dbname") or "")
        if "test" not in dbname:
            pytest.exit(
                f"refusing to use database {dbname!r}: TEST_DATABASE_URL must name a database containing 'test'",
                returncode=2,
            )
        yield url
        return

    import pgserver

    ensure_timezone_files(install_root())
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="delete")
    try:
        with psycopg.connect(server.get_uri(), autocommit=True) as conn:
            conn.execute("CREATE DATABASE ipo_intel_test")
        yield make_conninfo(server.get_uri(), dbname="ipo_intel_test")
    finally:
        server.cleanup()


@pytest.fixture(scope="session")
def migrated_db_url(pg_url: str) -> str:
    with connect(pg_url) as conn:
        apply_migrations(conn, MIGRATIONS_DIR)
    return pg_url


@pytest.fixture
def database(migrated_db_url: str) -> Iterator[Database]:
    """A pool on the migrated test database, with every table emptied first."""
    db = Database(migrated_db_url, min_size=1, max_size=8)
    db.open()
    try:
        with db.connection() as conn:
            tables = [
                row["tablename"]
                for row in conn.execute(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'schema_migrations'"
                )
            ]
            if tables:
                conn.execute(
                    sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                        sql.SQL(", ").join(sql.Identifier(t) for t in tables)
                    )
                )
        yield db
    finally:
        db.close()


@pytest.fixture
def fresh_db_url(pg_url: str) -> Iterator[str]:
    """An empty, unmigrated database that is dropped afterwards."""
    name = f"ipo_intel_test_{uuid.uuid4().hex[:12]}"
    admin_url = make_conninfo(pg_url, dbname="postgres")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(pg_url, dbname=name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
