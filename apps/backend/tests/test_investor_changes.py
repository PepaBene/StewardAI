from decimal import Decimal

from steward.ownership.investors import classify_change


def test_no_earlier_quarter_is_unknown() -> None:
    assert classify_change(Decimal(10), None, has_earlier=False) == ("unknown", None)


def test_new_and_sold_positions_have_no_percentage() -> None:
    assert classify_change(Decimal(10), None, has_earlier=True) == ("new", None)
    assert classify_change(None, Decimal(10), has_earlier=True) == ("sold", None)


def test_added_trimmed_and_unchanged_by_share_count() -> None:
    change, pct = classify_change(Decimal(150), Decimal(100), has_earlier=True)
    assert change == "added" and pct is not None and abs(pct - 0.5) < 1e-9
    change, pct = classify_change(Decimal(75), Decimal(100), has_earlier=True)
    assert change == "trimmed" and pct is not None and abs(pct + 0.25) < 1e-9
    assert classify_change(Decimal(1002), Decimal(1000), has_earlier=True) == ("unchanged", 0.0)
