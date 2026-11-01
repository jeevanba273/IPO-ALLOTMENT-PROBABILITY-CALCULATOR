from datetime import date

from ipo_intel.indianapi.models import BucketRecord
from ipo_intel.ingest.symbols import IpoMatcher, Match


def _row(ipo_id, name, *, issue_type="mainboard", bid_start=None, listing_date=None, symbols=()):
    return {
        "id": ipo_id, "name": name, "issue_type": issue_type, "bid_start": bid_start,
        "listing_date": listing_date, "cutoff_price": None, "symbols": list(symbols),
    }


def _record(name, *, symbol=None, is_sme=None, bid_start=None, listing_date=None, bucket="listed"):
    return BucketRecord(bucket, name, symbol, is_sme, None, None, None, listing_date, bid_start)


ROWS = [
    _row("core4", "Core4 Engineers", issue_type="sme", bid_start=date(2026, 10, 23), symbols=["CORE4"]),
    _row("acme", "Acme Tech Ltd", bid_start=date(2026, 11, 2), symbols=["ACMETECH"]),
    _row("vedant", "Vedant Poly Fabs", issue_type="sme", bid_start=date(2026, 11, 2)),
    _row("twin-a", "Twin Power Ltd", listing_date=date(2026, 9, 1)),
    _row("twin-b", "Twin Power Limited", listing_date=date(2026, 10, 1)),
]


def test_symbol_wins_even_when_names_differ():
    row, match = IpoMatcher(ROWS).match(_record("Acme Technologies", symbol="ACMETECH"))
    assert (row["id"], match) == ("acme", Match.MATCHED)


def test_exact_normalised_name():
    row, _ = IpoMatcher(ROWS).match(_record("CORE4 ENGINEERS LTD", symbol="OTHER"))
    assert row["id"] == "core4"


def test_shortened_name_needs_a_matching_date():
    matcher = IpoMatcher(ROWS)
    row, match = matcher.match(_record("Vedant Poly Fabs Solutions", bid_start=date(2026, 11, 2)))
    assert (row["id"], match) == ("vedant", Match.MATCHED)
    # Same prefix but no date to confirm it, or a date that disagrees: no match.
    assert matcher.match(_record("Vedant Poly Fabs Solutions"))[1] is Match.UNMATCHED
    assert matcher.match(_record("Vedant Poly Fabs Solutions", bid_start=date(2026, 5, 1)))[1] is Match.UNMATCHED


def test_short_names_never_prefix_match():
    rows = [_row("ab", "AB Cotspin", bid_start=date(2026, 1, 5))]
    assert IpoMatcher(rows).match(_record("AB", bid_start=date(2026, 1, 5)))[1] is Match.UNMATCHED


def test_duplicate_names_are_split_by_date_or_reported_ambiguous():
    matcher = IpoMatcher(ROWS)
    row, _ = matcher.match(_record("Twin Power", listing_date=date(2026, 10, 1)))
    assert row["id"] == "twin-b"
    assert matcher.match(_record("Twin Power"))[1] is Match.AMBIGUOUS


def test_a_symbol_shared_by_two_ipos_falls_back_to_the_name():
    rows = [_row("x1", "Xeno Labs", symbols=["XENO"]), _row("x2", "Xeno Foods", symbols=["XENO"])]
    row, _ = IpoMatcher(rows).match(_record("Xeno Foods Ltd", symbol="XENO"))
    assert row["id"] == "x2"
