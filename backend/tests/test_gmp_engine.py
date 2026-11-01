from datetime import datetime, timedelta

import pytest

from ipo_intel.engine.ev import GmpConfidence
from ipo_intel.engine.gmp import GmpObservation, gmp_confidence, latest_gmp
from ipo_intel.timeutil import UTC

NOW = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)


def hours_ago(*hours):
    return [NOW - timedelta(hours=h) for h in hours]


def test_mainboard_with_three_fresh_points_is_high():
    assert gmp_confidence("mainboard", hours_ago(1, 10, 30), NOW) is GmpConfidence.HIGH


@pytest.mark.parametrize(
    ("board", "stamps", "why"),
    [
        ("sme", hours_ago(1, 2, 3, 4), "SME is always low"),
        ("mainboard", hours_ago(1, 2), "fewer than 3 points"),
        ("mainboard", hours_ago(1, 2, 50), "the third point is outside 48 h"),
        ("mainboard", hours_ago(25, 30, 40), "latest point over 24 h old"),
        ("mainboard", [], "no points"),
    ],
)
def test_low_confidence(board, stamps, why):
    assert gmp_confidence(board, stamps, NOW) is GmpConfidence.LOW, why


def test_duplicate_timestamps_count_once_and_future_points_are_ignored():
    stamps = hours_ago(1, 1, 2) + [NOW + timedelta(hours=1)]
    assert gmp_confidence("mainboard", stamps, NOW) is GmpConfidence.LOW


def test_newest_wins_whatever_its_source():
    obs = [
        GmpObservation(NOW - timedelta(hours=5), 30.0, "ipoalerts"),
        GmpObservation(NOW - timedelta(hours=2), 42.0, "manual"),
        GmpObservation(NOW - timedelta(hours=3), 35.0, "ipoalerts"),
    ]
    reading = latest_gmp(obs, board="mainboard", cutoff=400.0, as_of=NOW)
    assert (reading.gmp_rs, reading.source) == (42.0, "manual")
    assert reading.gmp_pct == pytest.approx(42 / 400)
    assert reading.age == timedelta(hours=2)
    assert reading.recent_points == 3 and reading.confidence is GmpConfidence.HIGH

    newer = obs + [GmpObservation(NOW - timedelta(hours=1), 38.0, "ipoalerts")]
    assert latest_gmp(newer, board="mainboard", cutoff=400.0, as_of=NOW).source == "ipoalerts"


def test_a_tie_goes_to_the_manual_entry_and_as_of_hides_later_points():
    at = NOW - timedelta(hours=1)
    obs = [GmpObservation(at, 10.0, "ipoalerts"), GmpObservation(at, 12.0, "manual")]
    assert latest_gmp(obs, board="sme", cutoff=None, as_of=NOW).gmp_rs == 12.0
    assert latest_gmp(obs, board="sme", cutoff=None, as_of=NOW).gmp_pct is None
    assert latest_gmp(obs, board="sme", cutoff=None, as_of=at - timedelta(minutes=1)) is None


def test_naive_times_are_rejected():
    with pytest.raises(ValueError):
        gmp_confidence("mainboard", [], datetime(2026, 11, 3))
