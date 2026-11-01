"""Postgres access through psycopg 3.

Every connection runs with TIME ZONE 'UTC' and UTF-8 client encoding, whatever
the server defaults are, and returns rows as dicts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

Row = dict[str, Any]
Conn = psycopg.Connection[Row]


def _configure(conn: psycopg.Connection[Any]) -> None:
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute("SET client_encoding TO 'UTF8'")
    conn.commit()


def connect(url: str) -> Conn:
    """A single connection, for one-off work like migrations."""
    conn = psycopg.connect(url, row_factory=dict_row)
    _configure(conn)
    return conn


class Database:
    """A small connection pool. Each `connection()` block commits on success and rolls back on error."""

    def __init__(self, url: str, *, min_size: int = 1, max_size: int = 5) -> None:
        self._pool = ConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            configure=_configure,
            open=False,
            name="ipo-intel",
        )

    def open(self, timeout: float = 10.0) -> None:
        self._pool.open(wait=True, timeout=timeout)

    def close(self) -> None:
        self._pool.close()

    def __enter__(self) -> Database:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def connection(self) -> Iterator[Conn]:
        with self._pool.connection() as conn:
            yield conn
