"""Switches read from Trading212 fills (plan G91): a sale and a nearby buy of another stock."""

import datetime as dt

from steward.journal.switches import Fill, pair_switches

D = dt.date


def test_a_sale_and_a_close_buy_of_another_stock_is_a_switch() -> None:
    fills = [
        Fill(ticker="A", side="SELL", on=D(2026, 9, 1), amount=10_000),
        Fill(ticker="B", side="BUY", on=D(2026, 9, 2), amount=9_800),
    ]
    found = pair_switches(fills, set())
    assert [(s.sold_ticker, s.bought_ticker, s.switched_on) for s in found] == [
        ("A", "B", D(2026, 9, 2))
    ]


def test_a_buy_funded_by_a_deposit_or_far_away_is_not_a_switch() -> None:
    fills = [
        Fill(ticker="A", side="SELL", on=D(2026, 9, 1), amount=10_000),
        Fill(ticker="B", side="BUY", on=D(2026, 9, 2), amount=2_000),  # far smaller
        Fill(ticker="C", side="BUY", on=D(2026, 9, 20), amount=10_000),  # too late
        Fill(ticker="A", side="BUY", on=D(2026, 9, 1), amount=10_000),  # same stock
    ]
    assert pair_switches(fills, set()) == []


def test_each_buy_is_used_once_and_logged_pairs_are_left_out() -> None:
    fills = [
        Fill(ticker="A", side="SELL", on=D(2026, 9, 1), amount=10_000),
        Fill(ticker="C", side="SELL", on=D(2026, 9, 1), amount=10_000),
        Fill(ticker="B", side="BUY", on=D(2026, 9, 1), amount=10_000),
    ]
    assert len(pair_switches(fills, set())) == 1
    assert pair_switches(fills, {("A", "B", D(2026, 9, 1))}) == []
