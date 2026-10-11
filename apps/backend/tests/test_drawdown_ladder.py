import numpy as np

from steward.config.strategy import RiskLimits, StrategyConfig
from steward.portfolio import drawdown_ladder as dl
from steward.portfolio.risk_limits import RiskInputs, evaluate_risk
from tests.test_exante import _book


def _keys(inputs: RiskInputs, limits: RiskLimits) -> list[str]:
    return sorted(f.key for f in evaluate_risk(inputs, limits) if "drawdown" in f.key)


def test_each_rung_reached_raises_its_own_note_and_the_freeze_stays_separate():
    limits = RiskLimits(max_drawdown=0.20)
    assert limits.drawdown_warnings == [0.10, 0.15]
    assert _keys(RiskInputs(drawdown=0.05), limits) == []
    assert _keys(RiskInputs(drawdown=0.12), limits) == ["risk:drawdown_warning:10"]
    assert _keys(RiskInputs(drawdown=0.16), limits) == [
        "risk:drawdown_warning:10",
        "risk:drawdown_warning:15",
    ]
    # past the freeze: the limit finding is raised on top of the notes, unchanged
    assert "risk:drawdown" in _keys(RiskInputs(drawdown=0.25), limits)


def test_no_notes_without_a_freeze_and_rungs_at_or_above_it_are_skipped():
    assert _keys(RiskInputs(drawdown=0.30), RiskLimits()) == []
    limits = RiskLimits(max_drawdown=0.12, drawdown_warnings=[0.10, 0.15])
    assert _keys(RiskInputs(drawdown=0.11), limits) == ["risk:drawdown_warning:10"]


def test_note_names_drivers_and_theses_due():
    limits = RiskLimits(max_drawdown=0.20)
    inputs = RiskInputs(
        drawdown=0.11, drawdown_drivers=[("AAA", -0.04), ("BBB", -0.02)], reviews_due=["AAA"]
    )
    (finding,) = [f for f in evaluate_risk(inputs, limits) if f.key.startswith("risk:drawdown_w")]
    assert finding.severity == 1
    assert "AAA (-4.0% of the portfolio)" in finding.detail
    assert "re-confirmation: AAA" in finding.detail
    assert "not a reason to sell" in finding.detail


def test_drivers_are_the_positions_that_lost_most_since_the_peak():
    rng = np.random.default_rng(4)
    n = 60
    up = np.full(n, 0.01)
    fall = np.concatenate([np.full(30, 0.01), np.full(30, -0.02)])  # peaks mid-window
    steady = rng.normal(0.0, 0.001, n)
    values = {"AAA": 500.0, "BBB": 300.0, "CCC": 200.0}
    book = _book({"AAA": fall, "BBB": steady, "CCC": up}, values)
    out = dl.drivers(book, StrategyConfig(base_currency="CZK"), weeks=52)
    assert out and out[0][0] == "AAA" and out[0][1] < -0.05
    assert all(t != "CCC" for t, _ in out)


def test_no_drivers_when_the_portfolio_is_at_its_peak():
    rising = np.full(60, 0.01)
    book = _book({"AAA": rising}, {"AAA": 100.0})
    assert dl.drivers(book, StrategyConfig(base_currency="CZK")) == []
