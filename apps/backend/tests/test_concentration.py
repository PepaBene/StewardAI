"""The above-cap helper of principle 4f (plan Q5.5)."""

import pytest

from steward.config.strategy import PositionWeightRule, StrategyConfig
from steward.portfolio import concentration as cn


def test_a_position_under_the_cap_has_nothing_to_show() -> None:
    assert cn.assess("A", None, 90.0, 1000.0, 0.10, 0.0) is None


def test_above_the_cap_it_shows_the_dilution_path_and_the_trim_to_eight_percent() -> None:
    got = cn.assess("A", "Alpha", 120.0, 1000.0, 0.10, 50.0, price_base=7.0)
    assert got is not None and got.zone == "above_cap"
    assert got.weight == pytest.approx(0.12) and got.excess_value == pytest.approx(20.0)
    # 120 / (1000 + 50 m) <= 0.10 from m = 4.
    assert got.months_to_cap == 4
    assert got.trim_weight == 0.08
    assert got.trim_value == pytest.approx(40.0)  # 120 - 8 % of 1000
    assert got.trim_shares == 6  # 40 / 7, rounded up
    assert "4 months" in got.advice and "trim to 8%" in got.advice


def test_without_deposits_the_path_never_reaches_the_cap_and_says_so() -> None:
    got = cn.assess("A", None, 120.0, 1000.0, 0.10, 0.0)
    assert got is not None and got.months_to_cap is None
    assert "do not bring it under" in got.advice


def test_above_the_backstop_the_trim_goes_to_ten_percent_whatever_the_outlook() -> None:
    got = cn.assess("A", None, 180.0, 1000.0, 0.10, 50.0)
    assert got is not None and got.zone == "above_backstop"
    assert got.trim_weight == 0.10 and got.trim_value == pytest.approx(80.0)
    assert "whatever the outlook" in got.advice


def test_the_cap_is_the_strictest_all_stock_position_rule() -> None:
    s = StrategyConfig(
        base_currency="CZK",
        rules=[
            PositionWeightRule(id="a", type="position_weight", operator="<=", value=0.12),
            PositionWeightRule(id="b", type="position_weight", operator="<=", value=0.09),
            PositionWeightRule(
                id="c", type="position_weight", operator="<=", value=0.05, ticker="X"
            ),
        ],
    )
    assert cn.cap_of(s) == pytest.approx(0.09)
    assert cn.cap_of(StrategyConfig(base_currency="CZK")) == cn.DEFAULT_CAP
