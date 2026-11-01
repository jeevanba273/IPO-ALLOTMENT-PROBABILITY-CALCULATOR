"""Logging with IST timestamps and secrets scrubbed from every line."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from datetime import datetime

from ipo_intel.config import Settings
from ipo_intel.redaction import redact
from ipo_intel.timeutil import IST

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_NOISY_LOGGERS = ("httpx", "httpcore", "psycopg.pool")


class RedactingFormatter(logging.Formatter):
    """Writes times in IST and removes secret values from the final text, tracebacks included."""

    def __init__(self, fmt: str = LOG_FORMAT, *, secrets: Iterable[str] = ()) -> None:
        super().__init__(fmt)
        self._secrets = tuple(secrets)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        return datetime.fromtimestamp(record.created, IST).strftime(datefmt or "%Y-%m-%d %H:%M:%S IST")

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self._secrets)


class _StderrHandler(logging.StreamHandler):
    """Always writes to the current sys.stderr, even after it has been swapped or reconfigured."""

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_logging(settings: Settings) -> logging.Handler:
    """Install one redacting stderr handler on the root logger, replacing any previous one of ours."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, _StderrHandler):
            root.removeHandler(handler)
    handler = _StderrHandler()
    handler.setFormatter(RedactingFormatter(secrets=settings.secret_values()))
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler
