"""P4.4: UK PDMR rules that differ from Form 4 (role from position, amendment de-duplication)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from steward.disclosure import insiders as ins
from steward.disclosure.pdmr_activity import dedupe, role_from_position

UTC = dt.UTC


def tx(accession: str, known_day: int, *, price: str = "10") -> ins.InsiderTx:
    known = dt.datetime(2026, 5, known_day, tzinfo=UTC)
    return ins.InsiderTx(
        accession=accession,
        row_index=0,
        insider="jane doe",
        name="Jane Doe",
        role="officer",
        tx_date=dt.date(2026, 5, 1),
        filed=known.date(),
        known_at=known,
        economic_class="open_market_buy",
        shares=D(100),
        price=D(price),
        plan=None,
        class_confirmed=True,
    )


def test_an_executive_office_makes_an_officer_even_when_the_person_is_also_a_director() -> None:
    assert role_from_position("Chief Financial Officer and Director") == "officer"
    assert role_from_position("Non-Executive Chairman") == "director"
    assert role_from_position("Person closely associated") == "other"
    assert role_from_position(None) == "other"


def test_an_amendment_repeating_a_trade_counts_once_at_the_earliest_notice() -> None:
    kept = dedupe([tx("pdmr:2", 6), tx("pdmr:1", 2)])
    assert [t.accession for t in kept] == ["pdmr:1"]


def test_a_changed_price_is_a_different_trade() -> None:
    assert len(dedupe([tx("pdmr:1", 2), tx("pdmr:2", 6, price="11")])) == 2
