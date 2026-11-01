"""A local Postgres for development, without Docker.

Runs the Postgres 16 binaries bundled with the pgserver package (a dev
dependency). The data directory lives outside the repo, because OneDrive and
other sync tools corrupt live database files: %LOCALAPPDATA%\\ipo-intel\\pgdata
on Windows, ~/.local/share/ipo-intel/pgdata elsewhere. The server listens on
127.0.0.1 only with trust auth, so it is reachable from this machine only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import tzdata
from psycopg import sql

DEFAULT_PORT = 54329
DEFAULT_DBNAME = "ipo_intel"
SUPERUSER = "postgres"


class LocalDbError(RuntimeError):
    """The local server could not be set up, started or stopped."""


def install_root() -> Path:
    try:
        import pgserver
    except ImportError as exc:
        raise LocalDbError('the local database needs the dev extras: pip install -e "backend[dev]"') from exc
    return Path(pgserver.__file__).parent


def ensure_timezone_files(root: Path) -> None:
    """Give a bundled Postgres a time zone database if its build lacks one.

    pgserver's Windows wheel ships share/postgresql/timezonesets but not
    share/postgresql/timezone, so 'UTC' and 'Asia/Kolkata' are unknown to the
    server. Production Postgres always has them. The tzdata package holds the
    same compiled IANA (TZif) files, so copy those in once.
    """
    for timezonesets in root.rglob("timezonesets"):
        target = timezonesets.parent / "timezone"
        if (target / "UTC").is_file():
            return
        source = Path(tzdata.__file__).parent / "zoneinfo"
        for path in source.rglob("*"):
            if path.is_file() and path.read_bytes()[:4] == b"TZif":
                destination = target / path.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
        return


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "ipo-intel" / "pgdata"


def local_url(port: int = DEFAULT_PORT, dbname: str = DEFAULT_DBNAME) -> str:
    return f"postgresql://{SUPERUSER}@127.0.0.1:{port}/{dbname}"


@dataclass
class LocalDb:
    data_dir: Path = field(default_factory=default_data_dir)
    port: int = DEFAULT_PORT

    @property
    def log_file(self) -> Path:
        return self.data_dir.parent / "postgres.log"

    def _exe(self, name: str) -> str:
        return str(install_root() / "pginstall" / "bin" / name)

    def _run(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        # Never pipe pg_ctl's output: the server it spawns inherits the pipes
        # and the call would hang until the server exits.
        return subprocess.run(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout
        )

    def is_initialised(self) -> bool:
        return (self.data_dir / "PG_VERSION").is_file()

    def is_running(self) -> bool:
        if not self.is_initialised():
            return False
        return self._run(self._exe("pg_ctl"), "status", "-D", str(self.data_dir)).returncode == 0

    def start(self, dbname: str = DEFAULT_DBNAME) -> str:
        """Initialise if needed, start if stopped, create `dbname` if missing. Returns its URL."""
        ensure_timezone_files(install_root())
        if not self.is_initialised():
            self.data_dir.parent.mkdir(parents=True, exist_ok=True)
            result = self._run(
                self._exe("initdb"),
                "-D", str(self.data_dir),
                "-U", SUPERUSER,
                "--auth=trust",
                "--encoding=UTF8",
                "--locale=C",
            )
            if result.returncode != 0:
                raise LocalDbError(f"initdb failed for {self.data_dir}")
        if not self.is_running():
            result = self._run(
                self._exe("pg_ctl"),
                "start", "-w",
                "-D", str(self.data_dir),
                "-l", str(self.log_file),
                "-o", f"-h 127.0.0.1 -p {self.port}",
            )
            if result.returncode != 0:
                raise LocalDbError(f"Postgres did not start; see {self.log_file}")
        with psycopg.connect(local_url(self.port, "postgres"), autocommit=True) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
            if exists is None:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        return local_url(self.port, dbname)

    def stop(self) -> bool:
        """Stop the server. Returns False if it was not running."""
        if not self.is_running():
            return False
        result = self._run(self._exe("pg_ctl"), "stop", "-w", "-m", "fast", "-D", str(self.data_dir))
        if result.returncode != 0:
            raise LocalDbError("pg_ctl stop failed")
        return True
