"""Plain-SQL migrations.

Files in migrations/ named NNNN_description.sql are applied in version order,
each in its own transaction, and recorded in schema_migrations with a SHA-256
checksum. Editing an applied file is an error: add a new migration instead.
Checksums ignore line endings so Windows and Linux checkouts agree.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

MIGRATION_FILENAME = re.compile(r"(\d{4})_([a-z0-9_]+)\.sql")

# Session advisory lock so two processes starting together cannot both migrate.
_LOCK_KEY = 4_812_330_017

_CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    name        TEXT        NOT NULL,
    checksum    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str
    checksum: str


@dataclass(frozen=True)
class MigrationState:
    migration: Migration
    applied_at: datetime | None


def discover(directory: Path) -> list[Migration]:
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")
    found: dict[str, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"bad migration filename {path.name!r}; expected NNNN_description.sql")
        version = match.group(1)
        if version in found:
            raise MigrationError(f"duplicate migration version {version}: {found[version].name}, {path.name}")
        sql = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        found[version] = Migration(version=version, name=path.name, sql=sql, checksum=checksum)
    return [found[version] for version in sorted(found)]


def _applied(conn: psycopg.Connection) -> dict[str, dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT version, name, checksum, applied_at FROM schema_migrations")
        return {row["version"]: row for row in cur.fetchall()}


def apply_migrations(conn: psycopg.Connection, directory: Path) -> list[str]:
    """Apply pending migrations and return the names of the ones applied."""
    migrations = discover(directory)
    previous_autocommit = conn.autocommit
    conn.autocommit = True  # each migration gets its own real transaction below
    try:
        conn.execute(_CREATE_TRACKING_TABLE)
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            applied = _applied(conn)
            on_disk = {m.version for m in migrations}
            for version in sorted(set(applied) - on_disk):
                log.warning("database has migration %s which is not on disk", applied[version]["name"])

            done: list[str] = []
            for migration in migrations:
                record = applied.get(migration.version)
                if record is not None:
                    if record["checksum"] != migration.checksum:
                        raise MigrationError(
                            f"{migration.name} changed after it was applied; add a new migration instead"
                        )
                    continue
                try:
                    with conn.transaction():
                        conn.execute(migration.sql)
                        conn.execute(
                            "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                            (migration.version, migration.name, migration.checksum),
                        )
                except psycopg.Error as exc:
                    raise MigrationError(f"{migration.name} failed and was rolled back: {exc}") from exc
                log.info("applied migration %s", migration.name)
                done.append(migration.name)
            return done
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    finally:
        conn.autocommit = previous_autocommit


def migration_status(conn: psycopg.Connection, directory: Path) -> list[MigrationState]:
    migrations = discover(directory)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT to_regclass('schema_migrations') IS NOT NULL AS present")
        present = cur.fetchone()["present"]
    applied = _applied(conn) if present else {}
    return [
        MigrationState(m, applied[m.version]["applied_at"] if m.version in applied else None)
        for m in migrations
    ]
