"""Reconstructed portfolio history: ledger quantities and cash, valued at the stored closes."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from steward.fx.history import FxHistory
from steward.portfolio.reconstruct import Tx, rebuild, weekdays

D = Decimal


def day(n: int) -> dt.date:
    return dt.date(2026, 3, n)  # 2026-03-02 is a Monday


def fx(eur_per_usd: str = "0.8", eur_per_czk: str = "0.04") -> FxHistory:
    """Stored ECB rates are EUR per unit: 1 USD = 0.8 EUR = 20 CZK."""
    rows = []
    for n in range(1, 15):
        rows += [("USD", day(n), D(eur_per_usd)), ("CZK", day(n), D(eur_per_czk))]
    return FxHistory("ecb", rows)


LEDGER = [
    Tx(day(2), "cash", "DEPOSIT", None, D(0), D(0), D(1000)),
    Tx(day(3), "order", "BUY", "AAA_US_EQ", D(2), D(10), D(400)),  # 2 x 10 USD = 400 CZK
    Tx(day(4), "dividend", "DIVIDEND", "AAA_US_EQ", D(2), D(0), D(5)),
]


def test_quantities_and_cash_follow_the_ledger_and_closes_value_the_day() -> None:
    closes = {"AAA_US_EQ": {day(2): D(10), day(3): D(11), day(4): D(12)}}
    built = rebuild(
        LEDGER, closes, {"AAA_US_EQ": "USD"}, {"AAA_US_EQ": "USD"}, fx(), "CZK",
        weekdays(day(2), day(5)),
    )  # fmt: skip
    by_day = {a.day: a for a in built.accounts}
    assert by_day[day(2)].cash == D(1000) and by_day[day(2)].invested == 0
    # day 3: cash 1000 - 400; 2 shares at 11 USD x 20 CZK per USD = 440
    assert by_day[day(3)].cash == D(600) and by_day[day(3)].invested == D(440)
    assert by_day[day(3)].total == D(1040)
    assert by_day[day(4)].cash == D(605)  # the dividend lands on its day
    assert by_day[day(4)].cost == D(400)
    row = next(p for p in built.positions if p.day == day(4))
    assert row.quantity == D(2) and row.avg_price == D(10) and row.price_basis == "close"


def test_a_holding_without_a_stored_series_is_valued_at_cost_and_says_so() -> None:
    built = rebuild(
        LEDGER, {}, {}, {"AAA_US_EQ": "USD"}, fx(), "CZK", weekdays(day(3), day(3))
    )  # fmt: skip
    (row,) = built.positions
    assert row.price_basis == "cost" and row.price == D(10)
    assert built.accounts[0].invested == D(400)


def test_a_missing_rate_skips_the_day_instead_of_guessing() -> None:
    built = rebuild(
        LEDGER, {}, {}, {"AAA_US_EQ": "USD"}, FxHistory("ecb", []), "CZK", [day(3)]
    )  # fmt: skip
    assert built.accounts == [] and day(3) in built.skipped


def test_a_stale_close_is_not_carried_past_the_window() -> None:
    closes = {"AAA_US_EQ": {day(2): D(10)}}
    built = rebuild(
        LEDGER, closes, {"AAA_US_EQ": "USD"}, {"AAA_US_EQ": "USD"}, fx(), "CZK", [day(13)]
    )  # fmt: skip
    assert built.positions[0].price_basis == "cost"


def test_ledger_kinds_that_are_not_reconstructed_stop_the_rebuild() -> None:
    sell = Tx(day(3), "order", "SELL", "AAA_US_EQ", D(1), D(10), D(200))
    with pytest.raises(ValueError, match="not reconstructed"):
        rebuild([sell], {}, {}, {"AAA_US_EQ": "USD"}, fx(), "CZK", [day(3)])
