import logging
import sys
from datetime import datetime

from ipo_intel.logging_setup import RedactingFormatter, configure_logging
from ipo_intel.redaction import redact
from ipo_intel.timeutil import UTC
from tests.fakes import make_settings

SECRET = "tg-SECRET-token-987654"


def _record(msg, *args, exc_info=None):
    return logging.LogRecord("test", logging.ERROR, __file__, 1, msg, args, exc_info)


def test_redact_replaces_every_occurrence():
    assert redact(f"a {SECRET} b {SECRET}", [SECRET]) == "a *** b ***"


def test_redact_ignores_empty_and_very_short_values():
    assert redact("ipo intel", ["", "ipo"]) == "ipo intel"


def test_redact_removes_the_longest_match_whole():
    assert redact("key=abcd1234XYZ", ["abcd1234", "abcd1234XYZ"]) == "key=***"


def test_formatter_redacts_message_arguments():
    formatter = RedactingFormatter(secrets=[SECRET])
    line = formatter.format(_record("calling with key=%s", SECRET))
    assert SECRET not in line
    assert "key=***" in line


def test_formatter_redacts_tracebacks():
    formatter = RedactingFormatter(secrets=[SECRET])
    try:
        raise RuntimeError(f"boom {SECRET}")
    except RuntimeError:
        line = formatter.format(_record("failed", exc_info=sys.exc_info()))
    assert SECRET not in line
    assert "RuntimeError: boom ***" in line


def test_formatter_timestamps_are_ist():
    record = _record("hello")
    record.created = datetime(2026, 11, 1, 4, 30, tzinfo=UTC).timestamp()
    assert RedactingFormatter().format(record).startswith("2026-11-01 10:00:00 IST")


def test_configure_logging_keeps_a_single_handler_of_ours(capsys):
    settings = make_settings(indianapi_key=SECRET)
    root = logging.getLogger()
    first = configure_logging(settings)
    second = configure_logging(settings)
    try:
        assert first not in root.handlers
        assert second in root.handlers
        logging.getLogger("ipo_intel.test").warning("key is %s", SECRET)
        err = capsys.readouterr().err
        assert "key is ***" in err
        assert SECRET not in err
    finally:
        root.removeHandler(second)
