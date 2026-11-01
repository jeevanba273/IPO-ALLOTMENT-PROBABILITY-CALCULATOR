"""Persistence for SourceRun rows (the source_runs table)."""

from __future__ import annotations

from ipo_intel.clients.base import SourceRun
from ipo_intel.db import Database, Row

_INSERT = """
INSERT INTO source_runs (source, endpoint, purpose, attempt, status, http_status, duration_ms, error, ran_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class PgRunRecorder:
    """Writes each run in its own short transaction, so it survives a failing job."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, run: SourceRun) -> None:
        with self._db.connection() as conn:
            conn.execute(
                _INSERT,
                (
                    run.source,
                    run.endpoint,
                    run.purpose,
                    run.attempt,
                    run.status.value,
                    run.http_status,
                    run.duration_ms,
                    run.error,
                    run.ran_at,
                ),
            )


def recent_runs(db: Database, *, source: str | None = None, limit: int = 20) -> list[Row]:
    with db.connection() as conn:
        return conn.execute(
            """
            SELECT source, endpoint, purpose, attempt, status, http_status, duration_ms, error, ran_at
            FROM source_runs
            WHERE %(source)s::text IS NULL OR source = %(source)s
            ORDER BY ran_at DESC, id DESC
            LIMIT %(limit)s
            """,
            {"source": source, "limit": limit},
        ).fetchall()
