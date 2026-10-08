import datetime as dt
import math

import pytest

from steward.decision import covariance
from steward.decision import pretrade as pt


def _days(n: int) -> list[dt.date]:
    start = dt.date(2025, 1, 1)
    return [start + dt.timedelta(days=i) for i in range(n)]


def _series(returns: list[float]) -> list[tuple[dt.date, float]]:
    price = 100.0
    out = [(_days(len(returns) + 1)[0], price)]
    for d, r in zip(_days(len(returns) + 1)[1:], returns, strict=True):
        price *= 1 + r
        out.append((d, price))
    return out


# A stock that moves up and down, one that moves with it, one that moves against it.
BASE = [0.01 if i % 2 else -0.01 for i in range(200)]
SERIES = {
    "A": _series(BASE),
    "B": _series(BASE),
    "C": _series([-r for r in BASE]),
}


def test_variance_of_perfect_hedge_is_zero() -> None:
    model = covariance.build(SERIES)
    assert model is not None
    assert covariance.volatility(model, {"A": 0.5, "C": 0.5}) == pytest.approx(0.0, abs=1e-9)


def test_volatility_of_one_stock_matches_its_annualised_deviation() -> None:
    model = covariance.build(SERIES)
    assert model is not None
    sd = math.sqrt(sum(r * r for r in BASE) / (len(BASE) - 1) * 252)
    assert covariance.volatility(model, {"A": 1.0}) == pytest.approx(sd, rel=0.02)


def test_contributions_sum_to_one() -> None:
    model = covariance.build(
        {**SERIES, "D": _series([0.02 * ((i * 7) % 5 - 2) for i in range(200)])}
    )
    assert model is not None
    w = {"A": 0.4, "B": 0.2, "C": 0.1, "D": 0.3}
    total = sum(covariance.contribution(model, w, s) or 0 for s in w)
    assert total == pytest.approx(1.0)


def test_short_history_is_left_out_and_named() -> None:
    model = covariance.build({**SERIES, "SHORT": _series(BASE[:30])})
    assert model is not None
    assert "SHORT" in model.left_out and model.index("SHORT") is None


def test_no_model_when_nothing_overlaps() -> None:
    assert covariance.build({"A": SERIES["A"]}) is None


def _facts(ticker: str, price: float = 100.0, target: float | None = 150.0) -> pt.TickerFacts:
    return pt.TickerFacts(
        ticker=ticker, held=True, price=price, sell_target=target, has_thesis=True,
        quote_currency="EUR", target_currency="EUR",
    )  # fmt: skip


def test_asymmetry_uses_the_bear_case_price() -> None:
    f = _facts("A")
    rules = pt.PretradeRules()
    leg = pt.TradeLeg(side="buy", ticker="A", value_base=100, bear_case_price=80)
    check = pt._asymmetry(leg, f, 0.5, rules)
    assert check.status == "pass" and check.values["ratio"] == pytest.approx(2.5)
    low = pt._asymmetry(leg.model_copy(update={"bear_case_price": 50}), f, 0.5, rules)
    assert low.status == "flag"
    none = pt._asymmetry(leg.model_copy(update={"bear_case_price": None}), f, 0.5, rules)
    assert none.status == "unavailable"
    f.bear_case = 80  # the thesis's own bear case is used when the buy gives none
    stored = pt._asymmetry(leg.model_copy(update={"bear_case_price": None}), f, 0.5, rules)
    assert stored.status == "pass" and stored.values["ratio"] == pytest.approx(2.5)
