"""The before and after ex-ante risk of a trade (plan Q5.1), on synthetic data."""

import datetime as dt

import numpy as np
import pytest

from steward.config.strategy import StrategyConfig
from steward.portfolio import exante as ex
from steward.portfolio import trade_risk as tr
from steward.quant import returns as rt

START = dt.date(2023, 1, 4)


def _book() -> ex.LoadedBook:
    rng = np.random.default_rng(31)
    weeks = 156
    market = rng.normal(0.001, 0.02, weeks)
    cols = {
        "WILD": 1.4 * market + rng.normal(0, 0.03, weeks),  # volatile
        "CALM": 0.4 * market + rng.normal(0, 0.005, weeks),  # quiet
        "MID": 0.9 * market + rng.normal(0, 0.012, weeks),
        ex.BENCHMARK_KEY: market,
    }
    keys = list(cols)
    data = np.column_stack([cols[k] for k in keys])
    dates = [START + dt.timedelta(weeks=i) for i in range(weeks)]
    matrix = rt.ReturnMatrix("weekly", dates, keys, data, rt.coverage_of(keys, dates, data))
    values = {"WILD": 500.0, "CALM": 300.0, "MID": 200.0}
    held = [ex.BookPosition(t, t.title(), "CZK", v) for t, v in values.items()]
    return ex.LoadedBook("CZK", 1000.0, held, matrix, {}, {}, {})


def _strategy(**risk: float) -> StrategyConfig:
    s = StrategyConfig(base_currency="CZK", categories=["risk"])
    # These tests isolate the volatility limit; the per-stock risk-share ceiling would add lines.
    s.risk.max_stock_risk_share = None
    for k, v in risk.items():
        setattr(s.risk, k, v)
    return s


def test_moving_weight_to_the_quiet_stock_lowers_forecast_and_the_wild_risk_share() -> None:
    book = _book()
    after_book = tr.book_after(book, {"WILD": -300.0, "CALM": 300.0})
    strategy = _strategy()
    before, fb = tr.findings_for(book, strategy)
    after, fa = tr.findings_for(after_book, strategy)
    out = tr.compare(before, after, fb, fa)
    assert out.available
    assert out.volatility_after < out.volatility_before
    wild = next(x for x in out.lines if x.ticker == "WILD")
    assert wild.weight_before == pytest.approx(0.5) and wild.weight_after == pytest.approx(0.2)
    assert wild.risk_share_after < wild.risk_share_before


def test_crossing_the_volatility_limit_is_flagged_new_and_fixing_it_clears() -> None:
    book = _book()
    base, _ = tr.findings_for(book, _strategy())
    limit = base.volatility_forecast
    assert limit is not None
    strategy = _strategy(max_volatility=limit * 1.05)
    riskier = tr.book_after(book, {"CALM": -300.0, "WILD": 300.0})
    before, fb = tr.findings_for(book, strategy)
    after, fa = tr.findings_for(riskier, strategy)
    out = tr.compare(before, after, fb, fa)
    assert any(line.change == "new" for line in out.limits)
    checks = tr.checks_from(out)
    assert any(c.status == "flag" and "Breached after" in c.title for c in checks)

    # Starting over the limit, a de-risking trade clears the breach.
    tight = _strategy(max_volatility=limit * 0.97)
    b2, fb2 = tr.findings_for(book, tight)
    a2, fa2 = tr.findings_for(tr.book_after(book, {"WILD": -400.0, "CALM": 400.0}), tight)
    cleared = tr.compare(b2, a2, fb2, fa2)
    assert [line.change for line in cleared.limits] == ["cleared"]
    assert any(c.status == "pass" and "cleared" in c.title for c in tr.checks_from(cleared))


def test_a_sale_cannot_take_a_position_below_zero_and_a_deposit_raises_the_total() -> None:
    book = _book()
    after = tr.book_after(book, {"MID": -5000.0}, deposit=250.0)
    assert [p.ticker for p in after.positions] == ["WILD", "CALM"]
    assert after.total_value == pytest.approx(1250.0)


def test_nothing_to_compare_is_reported_as_unavailable_not_as_a_pass() -> None:
    empty = tr.compare(
        ex.build_report(ex.LoadedBook("CZK", 0.0, [], _book().weekly, {}, {}, {}), _strategy()),
        ex.build_report(_book(), _strategy()),
        [],
        [],
    )
    assert not empty.available and empty.reason
    assert tr.checks_from(empty)[0].status == "unavailable"
    assert empty.not_modelled  # Expected Shortfall and drawdown probability are named as missing
