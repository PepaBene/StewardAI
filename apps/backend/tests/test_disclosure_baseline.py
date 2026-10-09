"""P0.5: the baseline reports what is absent as absent, and never invents a rate."""

from __future__ import annotations

from steward.disclosure.baseline import Inventory, render


def test_a_missing_table_is_reported_as_absent_not_zero() -> None:
    text = render(Inventory({"13F filings": None, "Insider filings": 0}, {}, {}, 0, 0))
    assert "| 13F filings | not in this database |" in text
    assert "| Insider filings | 0 |" in text


def test_the_link_share_is_stated_with_its_counts_and_none_without_instruments() -> None:
    inv = Inventory({}, {"SEC": 12}, {"suggested": 3}, 40, 30)
    assert inv.link_share == 0.75
    assert "30 of 40 (75%)" in render(inv)
    assert Inventory({}, {}, {}, 0, 0).link_share is None
    assert "no instruments" in render(Inventory({}, {}, {}, 0, 0))


def test_the_false_positive_rate_is_never_estimated_here() -> None:
    assert "needs a person's labels" in render(Inventory({}, {}, {}, 0, 0))
