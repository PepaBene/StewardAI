"""Back-test of the risk model on a synthetic book (quant plan Q6.1), no database."""

import numpy as np

from steward.config.strategy import StrategyConfig
from steward.portfolio import validation as pv
from tests.test_exante import _book


def _strategy() -> StrategyConfig:
    return StrategyConfig(base_currency="CZK")


def test_stable_returns_give_a_model_that_holds_up():
    rng = np.random.default_rng(21)
    cols = {"AAA": rng.normal(0, 0.02, 156), "BBB": rng.normal(0, 0.015, 156)}
    out = pv.validate_book(_book(cols, {"AAA": 600.0, "BBB": 400.0}), _strategy())
    assert out.available and out.weeks_tested == 104
    assert out.confidence == "ok" and out.review_item is None
    assert [b.confidence for b in out.backtests] == [0.95, 0.99]


def test_a_volatility_jump_the_model_cannot_see_lowers_confidence():
    rng = np.random.default_rng(22)
    calm = rng.normal(0, 0.005, 110)
    wild = rng.normal(0, 0.05, 46)  # volatility x10 for the last 46 weeks
    series = np.concatenate([calm, wild])
    out = pv.validate_book(_book({"AAA": series}, {"AAA": 1000.0}), _strategy())
    assert out.available
    assert out.confidence == "reduced"
    assert out.review_item is not None and "Model check failed" in out.review_item
    assert out.calibration is not None and out.calibration.direction == "understates"


def test_too_little_history_is_stated_not_guessed():
    rng = np.random.default_rng(23)
    out = pv.validate_book(_book({"AAA": rng.normal(0, 0.02, 60)}, {"AAA": 100.0}), _strategy())
    assert not out.available and out.reason and "weeks can be tested" in out.reason
    assert out.confidence == "untested"
