"""The engine over database figures: fixtures -> jobs -> analyse()."""

from datetime import date

import pytest

from ipo_intel.analysis import (
    NO_SUBSCRIPTION_YET,
    PARTIAL_DAY,
    SPLIT_ASSUMED,
    analyse,
    clear_reservation_override,
    load_histories,
    set_reservation_override,
)
from ipo_intel.engine.category import MIN_BID_IS_NII, Category
from ipo_intel.engine.ev import GmpConfidence
from ipo_intel.engine.projection import History, ProjectionStatus
from ipo_intel.engine.reservation import VERIFY_RHP
from ipo_intel.engine.rules import load_rules
from tests.indianapi_world import World

TODAY = date(2026, 11, 3)
RULES = load_rules()


@pytest.fixture
def world(database, migrated_db_url):
    w = World(database, migrated_db_url)
    w.run("master")
    w.run("open-details")
    yield w
    w.close()


def _analyse(world, ipo_id, **kwargs):
    with world.db.connection() as conn:
        return analyse(conn, ipo_id, rules=RULES, today=TODAY, **kwargs)


def test_retail_ipo_uses_todays_figures_now_and_yesterdays_for_the_projection(world):
    a = _analyse(world, "acme-tech")
    assert (a.category, a.min_category, a.lots, a.amount_rs) == (Category.RETAIL, Category.RETAIL, 1, 15_000)
    assert (a.latest.day, a.latest.complete) == (2, False)  # 03 Nov is still bidding
    assert (a.base.day, a.base.complete) == (1, True)
    assert PARTIAL_DAY in a.notes
    assert a.odds_now.basis == "current"
    assert a.odds_now.p_low == pytest.approx(1 / 1.45)
    assert a.odds_now.p_high == 1.0  # 1.5 / 1.45, capped
    assert a.warnings == (VERIFY_RHP,)
    # One mainboard history in the fixtures: too few to project.
    assert a.projections["retail"].status is ProjectionStatus.NOT_ENOUGH_HISTORY
    assert a.odds_final is None


def test_sme_minimum_bid_is_nii(world):
    a = _analyse(world, "sunrise-agro")
    assert a.min_category is Category.SNII
    assert MIN_BID_IS_NII in a.notes and SPLIT_ASSUMED in a.notes
    assert (a.lots, a.min_qty, a.amount_rs) == (2, 3200, 243_200)
    # NII is 0.80x subscribed: allotted in full.
    assert (a.odds_now.p_low, a.odds_now.shares_low) == (1.0, 3200)


def test_application_size_sets_the_category(world):
    assert _analyse(world, "acme-tech", lots=13).category is Category.RETAIL  # 1,95,000
    big = _analyse(world, "acme-tech", lots=14)  # 2,10,000
    assert big.category is Category.SNII and SPLIT_ASSUMED in big.notes
    assert _analyse(world, "acme-tech", lots=67).category is Category.BNII  # 10,05,000
    with pytest.raises(ValueError, match="minimum bid is 2 lot"):
        _analyse(world, "sunrise-agro", lots=1)
    with pytest.raises(LookupError):
        _analyse(world, "no-such-ipo")
    with pytest.raises(ValueError, match="not announced"):
        _analyse(world, "quietpeak-retail")


def test_sebi_2022_nii_method(world):
    sebi = RULES.model_copy(update={"nii": RULES.nii.model_copy(update={"method": "sebi_2022"})})
    with world.db.connection() as conn:
        a = analyse(conn, "acme-tech", rules=sebi, today=TODAY, lots=20)
        plain = analyse(conn, "acme-tech", rules=RULES, today=TODAY, lots=20)
    # NII is 1.20x; the minimum sNII bid is 14 lots (₹2,10,000), sNII applicants average 1.5 of them.
    assert (a.category, a.method, a.allotment_unit_lots) == (Category.SNII, "sebi_2022", 14)
    assert a.odds_now.p_low == pytest.approx(1 / 1.2)
    assert a.odds_now.p_high == 1.0
    assert a.odds_now.shares_low == pytest.approx(14 * 150 / 1.2)
    # Everyone gets the minimum bid at the high end; half the excess demand is met from the leftover.
    assert a.odds_now.shares_high == pytest.approx(14 * 150 + 6 * 150 * 0.5)
    assert (plain.method, plain.allotment_unit_lots) == ("proportionate", 1)
    assert plain.odds_now.shares_low == pytest.approx(20 * 150 / 1.2)


def test_several_applications_and_ev(world):
    a = _analyse(world, "acme-tech", applications=3, gmp_rs=20, gmp_confidence=GmpConfidence.HIGH)
    p = 1 / 1.45
    assert a.odds_now.any_low == pytest.approx(1 - (1 - p) ** 3)
    assert a.odds_now.shares_low == pytest.approx(3 * p * 150)
    assert a.ev.point is not None and a.ev.low <= a.ev.point <= a.ev.high
    low = _analyse(world, "acme-tech", gmp_rs=20)
    assert low.ev.confidence is GmpConfidence.LOW and low.ev.point is None


def test_projection_with_enough_history_gives_projected_odds(world):
    # Ten mainboard histories where retail ends 10x higher than day 1.
    histories = [History(f"h{i}", "mainboard", ({"retail": 1.0}, {"retail": 4.0}, {"retail": 10.0 + i})) for i in range(10)]
    with world.db.connection() as conn:
        a = analyse(conn, "acme-tech", rules=RULES, today=TODAY, histories=histories)
    projection = a.projections["retail"]
    assert projection.status is ProjectionStatus.OK and projection.samples == 10
    assert projection.current == pytest.approx(0.70)  # day 1, the last complete day
    assert projection.median == pytest.approx(0.70 * 14.5)
    assert a.odds_final.basis == "projected"
    assert (a.odds_final.times_low, a.odds_final.times_high) == (pytest.approx(projection.q1), pytest.approx(projection.q3))
    assert a.odds_final.p_low == pytest.approx(1 / projection.q3)


def test_closed_and_listed_ipos_are_final(world):
    world.run("backfill")
    a = _analyse(world, "northstar-optics")
    assert a.stage.value == "listed"
    assert all(p.status is ProjectionStatus.FINAL for p in a.projections.values())
    assert a.odds_now.basis == "final" and a.odds_final is None
    assert a.odds_now.p_low == pytest.approx(1 / 8.40)
    closed = _analyse(world, "bluewave-logistics")
    assert closed.latest.complete and closed.odds_now.basis == "final"


def test_reservation_overrides_change_the_odds_and_clear_the_warning(world):
    with world.db.connection() as conn:
        set_reservation_override(conn, "acme-tech", "retail", 3_045_000, "RHP p. 9", world.clock.now())
    a = _analyse(world, "acme-tech")
    assert a.latest.figures["retail"].source == "manual"
    assert a.latest.figures["retail"].times == pytest.approx(6_090_000 / 3_045_000)
    assert a.odds_now.p_low == pytest.approx(0.5)
    assert a.warnings == (VERIFY_RHP,)
    with world.db.connection() as conn:
        set_reservation_override(conn, "acme-tech", "nii", 1_800_000, None, world.clock.now())
        set_reservation_override(conn, "acme-tech", "qib", 6_000_000, None, world.clock.now())
    assert _analyse(world, "acme-tech").warnings == ()
    with world.db.connection() as conn:
        assert clear_reservation_override(conn, "acme-tech") == 3
        with pytest.raises(ValueError):
            set_reservation_override(conn, "acme-tech", "employee", 10, None, world.clock.now())
        with pytest.raises(LookupError):
            set_reservation_override(conn, "nope", "retail", 10, None, world.clock.now())
    assert _analyse(world, "acme-tech").latest.figures["retail"].source == "indianapi"


def test_upcoming_ipo_has_no_odds_yet(world):
    a = _analyse(world, "meridian-power")
    assert a.latest is None and a.odds_now is None and a.projections == {}
    assert NO_SUBSCRIPTION_YET in a.notes


def test_histories_are_only_listed_ipos_with_a_complete_record(world):
    with world.db.connection() as conn:
        assert load_histories(conn, TODAY) == []  # listed IPOs not backfilled yet
    world.run("backfill")
    with world.db.connection() as conn:
        histories = {h.ipo_id: h for h in load_histories(conn, TODAY)}
    assert set(histories) == {"northstar-optics", "kesar-spices", "flatline-industries", "dropcap-engineering"}
    northstar = histories["northstar-optics"]
    assert northstar.board == "mainboard" and len(northstar.days) == 3
    assert northstar.days[-1]["overall"] == pytest.approx(28.91)
    assert histories["kesar-spices"].board == "sme"
