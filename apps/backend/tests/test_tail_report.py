"""The tail report of a book and the Q2.4 limits that read it, on a synthetic book."""

import datetime as dt

import numpy as np
import pytest

from steward.config.strategy import RiskLimits, StrategyConfig
from steward.portfolio import exante as ex
from steward.portfolio import risk_limits as rl
from steward.portfolio import tail as tl
from steward.quant import returns as rt

START = dt.date(2023, 1, 4)


def _book(weeks: int = 156, vol: float = 0.03) -> ex.LoadedBook:
    rng = np.random.default_rng(31)
    market = rng.normal(0.001, 0.02, weeks)
    cols = {
        "WILD": 1.4 * market + rng.normal(0, vol, weeks),
        "CALM": 0.4 * market + rng.normal(0, 0.005, weeks),
        ex.BENCHMARK_KEY: market,
    }
    keys = list(cols)
    data = np.column_stack([cols[k] for k in keys])
    dates = [START + dt.timedelta(weeks=i) for i in range(weeks)]
    matrix = rt.ReturnMatrix("weekly", dates, keys, data, rt.coverage_of(keys, dates, data))
    held = [
        ex.BookPosition("WILD", "Wild", "CZK", 600.0),
        ex.BookPosition("CALM", "Calm", "CZK", 300.0),
    ]
    return ex.LoadedBook("CZK", 1000.0, held, matrix, {}, {}, {})


def _strategy(**risk: float) -> StrategyConfig:
    s = StrategyConfig(base_currency="CZK", categories=["risk"])
    s.risk.max_stock_risk_share = None
    for k, v in risk.items():
        setattr(s.risk, k, v)
    return s


def test_the_report_has_lines_contributions_odds_and_downside_statistics() -> None:
    out = tl.tail_report(_book(), _strategy(max_drawdown=0.2))
    assert out.available and out.weeks == 156
    horizons = {(x.horizon_weeks, x.level) for x in out.lines}
    assert {(1, 0.95), (1, 0.99), (4, 0.95), (4, 0.99)} <= horizons
    assert all(x.es >= x.var >= 0 for x in out.lines)
    month = next(x for x in out.lines if x.horizon_weeks == 4 and x.level == 0.95)
    week = next(x for x in out.lines if x.horizon_weeks == 1 and x.level == 0.95)
    assert month.es > week.es  # a longer horizon loses more
    assert out.es_95_month is not None and out.es_95_month >= month.es - 1e-12
    assert sum(c.part_of_es for c in out.contributions) == pytest.approx(1.0)
    assert out.contributions[0].ticker == "WILD"  # the volatile holding carries the tail
    assert [d.years for d in out.drawdown] == [1, 2, 3]
    assert out.drawdown[0].probability <= out.drawdown[2].probability  # longer, likelier
    assert out.downside is not None and out.downside.max_drawdown > 0
    assert out.covered_share == pytest.approx(1.0)
    assert out.lines[0].es_value == pytest.approx(out.lines[0].es * 1000.0)


def test_a_book_with_too_little_history_says_so_instead_of_guessing() -> None:
    out = tl.tail_report(_book(weeks=30), _strategy())
    assert not out.available and out.reason and "30 weeks" in out.reason
    empty = ex.LoadedBook("CZK", 0.0, [], _book().weekly, {}, {}, {})
    assert not tl.tail_report(empty, _strategy()).available


def test_the_expected_shortfall_and_drawdown_limits_open_findings_only_when_crossed() -> None:
    book = _book()
    strategy = _strategy(max_drawdown=0.2)
    report = tl.tail_report(book, strategy, full=False)
    assert report.es_95_month is not None and report.drawdown

    def findings(es_limit: float | None, dd_limit: float | None) -> list[str]:
        limits = RiskLimits(
            max_drawdown=0.2,
            max_es_95_month=es_limit,
            max_drawdown_probability=dd_limit,
            max_stock_risk_share=None,
        )
        inputs = rl.RiskInputs()
        inputs.es_95_month = report.es_95_month
        inputs.drawdown_probability = report.drawdown[0].probability
        return [f.key for f in rl.evaluate_risk(inputs, limits)]

    assert findings(None, None) == []
    assert "risk:es" in findings(report.es_95_month * 0.5, None)
    assert "risk:es" not in findings(report.es_95_month * 1.5, None)
    assert "risk:drawdown_probability" in findings(None, report.drawdown[0].probability * 0.5)
    assert "risk:drawdown_probability" not in findings(None, 0.999)
