"""M7.2 change feed and M7.3 sector map: hand-worked cases for the pure parts."""

import datetime as dt

from steward.changes.feed import (
    Record,
    Ref,
    compare,
    holding_changes,
    price_moves,
    rank_items,
    weight_of,
)
from steward.sectors.map import MIN_SAMPLE, Profile, build_map, spread

NOW = dt.datetime(2026, 10, 7, tzinfo=dt.UTC)


def rec(value: float, i: str = "1") -> Record:
    return Record(value, Ref(table="t", id=i, known_at=NOW))


def test_equal_values_are_not_changes_and_a_first_record_is_new_not_revised() -> None:
    old = {("A", "rev"): rec(100.0, "1"), ("A", "ni"): rec(10.0, "2")}
    new = {("A", "rev"): rec(100.0, "3"), ("A", "ni"): rec(12.0, "4"), ("A", "gp"): rec(5.0, "5")}
    kinds = {key: kind for key, kind, _, _ in compare(old, new)}
    assert kinds == {("A", "ni"): "revised", ("A", "gp"): "new"}


def test_a_revision_points_at_both_records() -> None:
    ((_, kind, before, after),) = compare({("A",): rec(1.0, "old")}, {("A",): rec(2.0, "new")})
    assert kind == "revised"
    assert before is not None and after is not None
    assert (before.ref.id, after.ref.id) == ("old", "new")


def test_weight_matches_the_broker_ticker_or_the_bare_symbol() -> None:
    weights = {"AAPL_US_EQ": 0.1}
    assert weight_of("AAPL", weights) == 0.1
    assert weight_of("AAPL_US_EQ", weights) == 0.1
    assert weight_of("MSFT", weights) is None


def test_prices_report_only_moves_of_three_percent_and_sort_by_weight() -> None:
    weights = {"A_US_EQ": 0.05, "B_US_EQ": 0.2, "C_US_EQ": 0.3}
    old = {"A_US_EQ": (100.0, "d1"), "B_US_EQ": (100.0, "d1"), "C_US_EQ": (100.0, "d1")}
    new = {"A_US_EQ": (110.0, "d2"), "B_US_EQ": (90.0, "d2"), "C_US_EQ": (101.0, "d2")}
    items = rank_items(price_moves(old, new, weights))
    assert [i.subject for i in items] == ["B_US_EQ", "A_US_EQ"]  # C moved 1%: left out
    assert items[0].after is not None and "-10.0%" in items[0].after


def test_items_outside_the_portfolio_sort_last() -> None:
    held = price_moves({"A": (1.0, "x")}, {"A": (2.0, "y")}, {"A": 0.01})
    other = price_moves({"Z": (1.0, "x")}, {"Z": (2.0, "y")}, {})
    assert [i.subject for i in rank_items(other + held)] == ["A", "Z"]


def test_relevance_weighs_the_kind_of_change_as_well_as_the_weight() -> None:
    from steward.changes.feed import ChangeItem, relevance

    # fundamentals revised at 2%: 60 x 0.2 + 40 x 1.0 x 1.0 = 52; a price move at 3%: 18 + 19.2 = 37
    assert relevance("fundamentals", "revised", 0.02)[0] == 52
    assert relevance("price", "moved", 0.03)[0] == 37
    assert relevance("macro", "new", None)[0] == 11  # no weight: only the area and kind parts
    assert relevance("fundamentals", "revised", 0.5)[0] == 100  # weight part is capped
    price = ChangeItem(
        area="price", kind="moved", subject="P", what="w", before=None, after=None,
        before_ref=None, after_ref=None, weight=0.03,
    )  # fmt: skip
    fund = ChangeItem(
        area="fundamentals", kind="revised", subject="F", what="w", before=None, after=None,
        before_ref=None, after_ref=None, weight=0.02,
    )  # fmt: skip
    assert [i.subject for i in rank_items([price, fund])] == ["F", "P"]
    assert fund.relevance_reason


def test_fund_constituents_added_dropped_and_moved() -> None:
    refs = (
        Ref(table="etf_snapshot", id="1", known_at=NOW),
        Ref(table="etf_snapshot", id="2", known_at=NOW),
    )
    old = {"X": 0.05, "Y": 0.02, "Z": 0.001}
    new = {"X": 0.07, "W": 0.03, "Z": 0.001}
    out = {(i.kind, i.what) for i in holding_changes("F", old, new, refs, 0.1)}
    assert out == {("moved", "X"), ("added", "W"), ("dropped", "Y")}


def test_a_spread_withholds_the_median_below_the_minimum_sample() -> None:
    few = spread([0.1, None, 0.3], of=3)
    assert (few.n, few.of, few.median, few.low, few.high) == (2, 3, None, 0.1, 0.3)
    enough = spread([0.1, 0.2, 0.6], of=4)
    assert MIN_SAMPLE == 3 and enough.median == 0.2 and enough.n == 3


def test_the_map_counts_companies_markets_and_held_weight_per_group() -> None:
    profiles = [
        Profile("A_US_EQ", "Technology", "Semiconductors", "US"),
        Profile("B_US_EQ", "Technology", "Semiconductors", "TW"),
        Profile("C_US_EQ", "Technology", "Software", "US"),
        Profile("D_US_EQ", "Healthcare", "Drugs", "GB"),
    ]
    growth = {"A_US_EQ": 0.1, "B_US_EQ": 0.2, "C_US_EQ": 0.3, "D_US_EQ": None}
    weights = {"A_US_EQ": 0.1, "D_US_EQ": 0.05, "E_US_EQ": 0.02}
    links = {("A_US_EQ", "B_US_EQ"), ("A_US_EQ", "D_US_EQ")}
    out = build_map(profiles, growth, {}, {}, weights, links)
    tech = next(s for s in out.sectors if s.name == "Technology")
    assert (tech.companies, tech.markets, tech.held, tech.held_weight) == (3, ["TW", "US"], 1, 0.1)
    assert tech.revenue_growth.median == 0.2 and tech.revenue_growth.n == 3
    assert tech.operating_margin.n == 0 and tech.operating_margin.median is None
    assert tech.confirmed_peer_links == 1  # A-D crosses sectors
    semis = next(i for i in out.industries if i.name == "Semiconductors")
    assert semis.parent == "Technology" and semis.revenue_growth.median is None  # two companies
    assert out.unclassified == ["E_US_EQ"]
