"""Thesis price targets: schema and the post-sync check."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisPriceTarget
from steward.portfolio.snapshot import PositionRow
from steward.portfolio.targets import BUY_SEVERITY, SELL_SEVERITY, evaluate_targets

SET_ON = dt.date(2026, 10, 1)


def _target(**kw: Any) -> ThesisPriceTarget:
    return ThesisPriceTarget(currency="USD", method="DCF", set_on=SET_ON, **kw)


def _position(ticker: str, price: str, currency: str = "USD", quantity: str = "10") -> PositionRow:
    return PositionRow(
        ticker=ticker,
        name=ticker,
        isin=None,
        quantity=Decimal(quantity),
        avg_price=Decimal(1),
        current_price=Decimal(price),
        instrument_currency=currency,
        value=Decimal(0),
        account_currency="CZK",
        value_account=Decimal(0),
        cost_account=Decimal(0),
        ppl=Decimal(0),
        fx_impact_account=None,
        fx_rate=Decimal(1),
        fx_rate_date=None,
        value_base=Decimal(0),
        weight=None,
    )


def _theses(*pairs: tuple[str, ThesisPriceTarget | None]) -> ThesesConfig:
    return ThesesConfig(
        theses=[ThesisConfig(ticker=t, why_owned="x", price_target=p) for t, p in pairs]
    )


def test_target_needs_a_level_and_buy_below_sell() -> None:
    with pytest.raises(ValidationError, match="sell target, a buy target, scenarios"):
        _target()
    with pytest.raises(ValidationError, match="below the sell target"):
        _target(sell_target=100, buy_target=100)
    assert _target(buy_target=80).sell_target is None
    assert ThesisConfig(ticker="A", why_owned="x").price_target is None


def test_describe_lists_the_set_levels() -> None:
    assert _target(sell_target=260, buy_target=180.5).describe() == (
        "sell 260 USD, buy 180.5 USD (DCF, set 2026-10-01)"
    )
    assert _target(buy_target=90).describe() == "buy 90 USD (DCF, set 2026-10-01)"


def test_sell_target_reached_asks_for_research_then_sell() -> None:
    theses = _theses(("A", _target(sell_target=100, buy_target=60)))
    assert evaluate_targets([_position("A", "99.99")], theses).findings == []
    (f,) = evaluate_targets([_position("A", "100")], theses).findings
    assert f.key == "target:sell:A" and f.severity == SELL_SEVERITY and f.kind == "rule"
    assert f.observed == 100 and f.limit == 100
    assert "sell unless the new thesis raises the target" in f.detail
    assert "DCF, set 2026-10-01" in f.detail


def test_buy_target_flags_a_candidate() -> None:
    theses = _theses(("A", _target(sell_target=100, buy_target=60)))
    assert evaluate_targets([_position("A", "60.01")], theses).findings == []
    (f,) = evaluate_targets([_position("A", "55")], theses).findings
    assert f.key == "target:buy:A" and f.severity == BUY_SEVERITY


def test_minor_units_compare_and_other_currencies_are_reported() -> None:
    pence = ThesisPriceTarget(currency="GBP", method="PE", set_on=SET_ON, sell_target=5)
    # 520 GBX = 5.20 GBP, above a 5 GBP target.
    (f,) = evaluate_targets([_position("L", "520", "GBX")], _theses(("L", pence))).findings
    assert f.key == "target:sell:L" and f.observed == pytest.approx(5.2)

    check = evaluate_targets([_position("A", "200", "EUR")], _theses(("A", _target(sell_target=1))))
    assert check.findings == [] and "EUR" in check.unavailable["A"]


def test_unheld_or_targetless_theses_are_skipped() -> None:
    theses = _theses(("A", _target(sell_target=1)), ("B", None))
    positions = [_position("A", "5", quantity="0"), _position("B", "5")]
    check = evaluate_targets(positions, theses)
    assert check.findings == [] and check.unavailable == {}
