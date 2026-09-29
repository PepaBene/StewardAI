"""Strategy risk limits: schema, evaluation and beta."""

import datetime as dt

import pytest
from pydantic import ValidationError

from steward.config.strategy import RiskLimits, StrategyConfig
from steward.portfolio.risk import MIN_OBSERVATIONS, DailyReturn, beta
from steward.portfolio.risk_limits import (
    PositionLoss,
    RiskInputs,
    Underlying,
    evaluate_risk,
)

LIMITS = RiskLimits(
    max_volatility=0.20, max_beta=1.2, position_loss_review=0.25, max_look_through_weight=0.10
)


def test_risk_limits_default_to_off() -> None:
    assert evaluate_risk(RiskInputs(volatility=5.0, beta=9.0), RiskLimits()) == []
    assert StrategyConfig(base_currency="CZK").risk == RiskLimits()


def test_unknown_risk_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RiskLimits.model_validate({"max_drawdown": 0.2})


def test_each_limit_breaches_only_past_its_threshold() -> None:
    inputs = RiskInputs(
        volatility=0.25,
        beta=1.5,
        positions=[PositionLoss("A", 100, 70), PositionLoss("B", 100, 80), PositionLoss("C", 0, 5)],
        underlyings=[Underlying("NVIDIA", 0.12, True), Underlying("Apple", 0.15, False)],
    )
    keys = sorted(f.key for f in evaluate_risk(inputs, LIMITS))
    assert keys == ["risk:beta", "risk:loss:A", "risk:overlap:NVIDIA", "risk:volatility"]


def test_at_the_limit_is_not_a_breach() -> None:
    inputs = RiskInputs(volatility=0.20, beta=1.2, positions=[PositionLoss("A", 100, 80)])
    assert all(f.key != "risk:volatility" for f in evaluate_risk(inputs, LIMITS))


def test_loss_finding_says_review_not_sell() -> None:
    (f,) = evaluate_risk(RiskInputs(positions=[PositionLoss("A", 100, 60)]), LIMITS)
    assert f.ticker == "A" and "not a reason to sell" in f.detail and f.severity == 2


def _returns(values: list[float]) -> list[DailyReturn]:
    d = dt.date(2026, 1, 1)
    return [
        DailyReturn(d + dt.timedelta(i), d + dt.timedelta(i + 1), v) for i, v in enumerate(values)
    ]


def test_beta_of_a_levered_benchmark_copy() -> None:
    bench = [((-1) ** i) * 0.01 * (1 + i % 3) for i in range(MIN_OBSERVATIONS + 10)]
    assert beta(_returns([1.5 * b for b in bench]), _returns(bench)) == pytest.approx(1.5)


def test_beta_needs_enough_days_and_a_moving_benchmark() -> None:
    assert beta(_returns([0.01] * 5), _returns([0.01] * 5)) is None
    n = MIN_OBSERVATIONS + 1
    assert beta(_returns([0.01] * n), _returns([0.0] * n)) is None
