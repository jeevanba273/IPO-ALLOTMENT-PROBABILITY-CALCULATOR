"""The probability engine's pure functions."""

import math

import pytest

from ipo_intel.engine.allotment import (
    Odds,
    any_of,
    min_bid_lottery_shares,
    nii_outcome,
    retail_applications,
    retail_odds,
    retail_winners,
)
from ipo_intel.engine.category import (
    MIN_BID_IS_NII,
    Category,
    application_amount,
    category_for,
    max_retail_lots,
    min_nii_lots,
    nii_share,
)
from ipo_intel.engine.ev import GmpConfidence, expected_value, gmp_pct
from ipo_intel.engine.projection import (
    NOT_ENOUGH_HISTORY,
    History,
    ProjectionStatus,
    multiplier_table,
    multipliers,
    project,
)
from ipo_intel.engine.reservation import VERIFY_RHP, apply_overrides
from ipo_intel.engine.rules import Projection as ProjectionRules
from ipo_intel.engine.rules import load_rules

RULES = load_rules()


def rules_with(min_samples=8, min_base_times=0.05):
    return RULES.model_copy(update={"projection": ProjectionRules(min_samples=min_samples, min_base_times=min_base_times)})


# --- categories ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(14_924, Category.RETAIL), (200_000, Category.RETAIL), (200_001, Category.SNII),
     (1_000_000, Category.SNII), (1_000_001, Category.BNII)],
)
def test_category_by_amount(amount, expected):
    assert category_for(amount, RULES) is expected


def test_category_helpers():
    assert application_amount(2, 800, 158) == 252_800
    assert max_retail_lots(150, 100, RULES) == 13  # 13 x 15,000 = 1,95,000; 14 lots would be 2,10,000
    assert max_retail_lots(1600, 158, RULES) == 0  # one SME lot is already above 2 lakh
    assert nii_share(Category.SNII, RULES) == pytest.approx(1 / 3, abs=0.001)
    assert nii_share(Category.BNII, RULES) == pytest.approx(2 / 3, abs=0.001)
    assert Category.SNII.book == Category.BNII.book == "nii"
    assert MIN_BID_IS_NII == "your minimum bid is NII for this IPO"
    with pytest.raises(ValueError):
        category_for(0, RULES)


# --- retail ------------------------------------------------------------------------------


@pytest.mark.parametrize("times", [0.0, 0.5, 1.0])
def test_retail_not_oversubscribed_means_full_allotment(times):
    assert retail_odds(times, 1.5) == Odds(1.0, 1.0)


def test_retail_odds_are_a_range_from_the_lower_bound():
    odds = retail_odds(10, 1.5)
    assert odds.low == pytest.approx(0.1)  # 1/times: everyone bid the minimum
    assert odds.high == pytest.approx(0.15)  # average applicant bid 1.5 minimums
    assert retail_odds(10, 1.0) == Odds(0.1, 0.1)  # SME default: no spread
    assert retail_odds(1.2, 1.5).high == 1.0  # capped


def test_retail_odds_reject_nonsense():
    with pytest.raises(ValueError):
        retail_odds(-1, 1.5)
    with pytest.raises(ValueError):
        retail_odds(5, 0.5)


def test_retail_application_counts():
    # 10x on a 4,256,017-share book with a 21-share minimum.
    assert retail_winners(4_256_017, 21) == 202_667
    fewest, most = retail_applications(42_560_170, 21, 1.5)
    assert most == pytest.approx(2_026_675.7, rel=1e-6)
    assert fewest == pytest.approx(most / 1.5)
    # Winners / applications brackets the same odds as retail_odds.
    assert 202_667 / most == pytest.approx(retail_odds(10, 1.5).low, rel=1e-4)


# --- NII ------------------------------------------------------------------------------------


def test_nii_proportionate():
    outcome = nii_outcome(1000, 100, 20)
    assert outcome.expected_shares == pytest.approx(50)
    assert outcome.expected_lots == pytest.approx(0.5)
    assert outcome.p_at_least_one_lot == pytest.approx(0.5)
    assert nii_outcome(1000, 100, 5).p_at_least_one_lot == 1.0  # 2 lots expected
    full = nii_outcome(1000, 100, 0.8)
    assert (full.expected_shares, full.p_at_least_one_lot) == (1000.0, 1.0)


def test_minimum_nii_bid_in_lots():
    # 150-share lots at ₹100: 14 lots (₹2,10,000) is the smallest sNII bid, 67 lots (₹10,05,000) the smallest bNII.
    assert min_nii_lots(Category.SNII, 150, 100, 1, RULES) == 14
    assert min_nii_lots(Category.BNII, 150, 100, 1, RULES) == 67
    # An SME whose 2-lot minimum is already above ₹2 lakh: the minimum bid is the minimum NII bid.
    assert min_nii_lots(Category.SNII, 800, 158, 2, RULES) == 2


def test_sebi_2022_nii_is_a_lottery_for_the_minimum_bid():
    # Heavily oversubscribed: a winner gets only the minimum bid.
    low, high = min_bid_lottery_shares(5000, 1000, 10, 2.0)
    assert (low, high) == (pytest.approx(100), pytest.approx(200))
    # Not oversubscribed: allotted in full.
    assert min_bid_lottery_shares(5000, 1000, 0.8, 2.0) == (5000, 5000)
    # Lightly oversubscribed: at the high end everyone gets the minimum bid and the leftover
    # (1/1.5 - 1/2) / (1 - 1/2) = 1/3 of the excess demand is shared proportionately.
    low, high = min_bid_lottery_shares(3000, 1000, 1.5, 2.0)
    assert low == pytest.approx(1000 / 1.5)
    assert high == pytest.approx(1000 + 2000 / 3)
    with pytest.raises(ValueError):
        min_bid_lottery_shares(500, 1000, 2, 1.5)


def test_sebi_2022_odds_sit_below_proportionate_for_big_bids():
    # 14 lots of 150 at 50x: proportionate says 28% chance of a lot; the SEBI rule gives 2-3%.
    proportionate = nii_outcome(14 * 150, 150, 50).p_at_least_one_lot
    sebi = retail_odds(50, 1.5)
    assert proportionate == pytest.approx(0.28)
    assert sebi.low == pytest.approx(0.02) and sebi.high == pytest.approx(0.03)


def test_nii_rejects_nonsense():
    with pytest.raises(ValueError):
        nii_outcome(0, 100, 2)
    with pytest.raises(ValueError):
        nii_outcome(100, 100, -2)


# --- several applications --------------------------------------------------------------------


def test_several_applications():
    assert any_of(0.1, 1) == pytest.approx(0.1)
    assert any_of(0.1, 3) == pytest.approx(1 - 0.9**3)
    assert any_of(0.0, 5) == 0.0
    assert any_of(1.0, 2) == 1.0
    with pytest.raises(ValueError):
        any_of(0.5, 0)
    with pytest.raises(ValueError):
        any_of(1.5, 1)


# --- expected value -----------------------------------------------------------------------


def test_ev_is_a_range_and_low_confidence_has_no_single_figure():
    ev = expected_value(10, 20, 50, GmpConfidence.LOW, (0.5, 0.75, 1.0))
    assert (ev.low, ev.high, ev.point) == (250, 1000, None)
    assert [(b.multiplier, b.low, b.high) for b in ev.bands] == [(0.5, 250, 500), (0.75, 375, 750), (1.0, 500, 1000)]


def test_ev_high_confidence_has_a_point_at_the_middle_multiplier():
    ev = expected_value(10, 20, 50, GmpConfidence.HIGH, (0.5, 0.75, 1.0))
    assert ev.point == pytest.approx(15 * 50 * 0.75)


def test_ev_with_negative_gmp_stays_ordered():
    ev = expected_value(10, 20, -30, GmpConfidence.HIGH, (0.5, 1.0))
    assert ev.low == pytest.approx(-600) and ev.high == pytest.approx(-150)
    assert all(band.low <= band.high for band in ev.bands)


def test_ev_matches_the_spec_formula():
    # EV = P x lot x cut-off x gmp_pct, at multiplier 1.
    p, lot, cutoff, gmp = 0.05, 21, 700.0, 460.0
    ev = expected_value(p * lot, p * lot, gmp, GmpConfidence.HIGH, (1.0,))
    assert ev.point == pytest.approx(p * lot * cutoff * gmp_pct(gmp, cutoff))
    with pytest.raises(ValueError):
        gmp_pct(10, 0)
    with pytest.raises(ValueError):
        expected_value(5, 1, 10, GmpConfidence.LOW, (1.0,))


# --- projection ------------------------------------------------------------------------------


def _histories():
    def h(ipo_id, board, *overall):
        return History(ipo_id, board, tuple({"overall": value, "retail": value / 2} for value in overall))

    return [
        h("a", "mainboard", 1, 4, 10),  # day 1 -> final x10, day 2 -> x2.5
        h("b", "mainboard", 2, 5, 20),  # x10, x4
        h("c", "mainboard", 0.01, 3, 30),  # day 1 base too small to scale from
        h("d", "mainboard", 5, 15),  # a 2-day issue: no day 2 -> final sample
        h("e", "sme", 1, 2, 100),  # another board
    ]


def test_multipliers_by_board_category_and_day():
    histories = _histories()
    assert sorted(multipliers(histories, board="mainboard", category="overall", day=1, min_base=0.05)) == [3, 10, 10]
    assert sorted(multipliers(histories, board="mainboard", category="overall", day=2, min_base=0.05)) == [2.5, 4, 10]
    assert multipliers(histories, board="sme", category="overall", day=1, min_base=0.05) == [100]
    assert multipliers(histories, board="mainboard", category="qib", day=1, min_base=0.05) == []
    with pytest.raises(ValueError):
        multipliers(histories, board="sme", category="overall", day=0, min_base=0.05)


def test_projection_min_median_max_and_middle_half():
    samples = [2.0, 3.0, 4.0, 5.0, 10.0]
    p = project(2.0, day=1, final=False, samples=samples, rules=rules_with(min_samples=5))
    assert p.status is ProjectionStatus.OK
    assert (p.low, p.median, p.high) == (4.0, 8.0, 20.0)
    assert p.q1 == pytest.approx(6.0) and p.q3 == pytest.approx(10.0)
    assert p.message == "from 5 listed IPOs"


def test_projection_statuses():
    rules = rules_with(min_samples=3)
    final = project(12.5, day=3, final=True, samples=[], rules=rules)
    assert final.status is ProjectionStatus.FINAL and final.median == final.low == final.high == 12.5
    assert project(0.01, day=1, final=False, samples=[2, 3, 4], rules=rules).status is ProjectionStatus.NO_BIDS_YET
    short = project(2, day=1, final=False, samples=[2, 3], rules=rules)
    assert short.status is ProjectionStatus.NOT_ENOUGH_HISTORY
    assert short.message == NOT_ENOUGH_HISTORY == "not enough history"
    assert short.median is None


def test_multiplier_table_lists_sample_sizes():
    rows = multiplier_table(_histories(), RULES, max_day=3)
    day1 = next(r for r in rows if (r.board, r.category, r.day) == ("mainboard", "overall", 1))
    assert (day1.samples, day1.low, day1.median, day1.high) == (3, 3, 10, 10)
    assert not any(r.day == 3 for r in rows)  # nobody bid past day 3


# --- reservation ---------------------------------------------------------------------------


def test_overrides_recompute_times_and_overall():
    figures = {
        "retail": (3_000_000, 1_000_000, 3.0),
        "nii": (500_000, 500_000, 1.0),
        "qib": (100_000, 1_000_000, 0.1),
        "overall": (3_600_000, 2_500_000, 1.44),
    }
    plain, warnings = apply_overrides(figures, {})
    assert plain["retail"].times == 3.0 and plain["retail"].source == "indianapi"
    assert warnings == [VERIFY_RHP]

    fixed, warnings = apply_overrides(figures, {"retail": 1_500_000})
    assert fixed["retail"].times == pytest.approx(2.0) and fixed["retail"].source == "manual"
    assert fixed["overall"].reserved_shares == 3_000_000
    assert fixed["overall"].times == pytest.approx(1.2)
    assert warnings == [VERIFY_RHP]  # nii and qib are still unchecked

    _, warnings = apply_overrides(figures, {"retail": 1_500_000, "nii": 500_000, "qib": 1_000_000})
    assert warnings == []
    assert math.isclose(fixed["nii"].times, 1.0)
