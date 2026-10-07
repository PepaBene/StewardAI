"""Strategy risk limits: schema, evaluation and beta."""

import datetime as dt

import pytest
from pydantic import ValidationError

from steward.config.strategy import RiskLimits, StrategyConfig
from steward.portfolio.risk import MIN_OBSERVATIONS, DailyReturn, beta
from steward.portfolio.risk_limits import (
    Exposure,
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
        RiskLimits.model_validate({"max_sharpe": 2})


def test_each_limit_breaches_only_past_its_threshold() -> None:
    inputs = RiskInputs(
        volatility=0.25,
        beta=1.5,
        positions=[PositionLoss("A", 100, 70), PositionLoss("B", 100, 80), PositionLoss("C", 0, 5)],
        underlyings=[Underlying("NVIDIA", 0.12, True), Underlying("Apple", 0.15, False)],
    )
    keys = sorted(f.key for f in evaluate_risk(inputs, LIMITS))
    assert keys == ["risk:beta", "risk:loss:A", "risk:overlap:NVIDIA", "risk:volatility"]


def test_sector_and_country_ceilings_breach_only_past_the_limit() -> None:
    limits = RiskLimits(max_sector_weight=0.30, max_country_weight=0.60)
    inputs = RiskInputs(
        exposures=[
            Exposure("sector", "Technology", 0.34),
            Exposure("sector", "Health Care", 0.30),
            Exposure("country", "US", 0.62),
            Exposure("country", "DE", 0.05),
        ]
    )
    findings = evaluate_risk(inputs, limits)
    assert sorted(f.key for f in findings) == ["risk:country:US", "risk:sector:Technology"]
    assert evaluate_risk(inputs, RiskLimits()) == []


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


def test_drawdown_is_measured_from_the_window_peak() -> None:
    from steward.portfolio.risk import current_drawdown

    d = dt.date(2026, 1, 1)
    pts = [(d + dt.timedelta(i), v) for i, v in enumerate([100.0, 120.0, 90.0])]
    assert current_drawdown(pts) == pytest.approx(0.25)
    assert current_drawdown(pts[:2]) == 0
    assert current_drawdown(pts[:1]) is None


def test_drawdown_limit_breaches_past_the_ceiling_only() -> None:
    limits = RiskLimits(max_drawdown=0.20)
    assert evaluate_risk(RiskInputs(drawdown=0.20), limits) == []
    (f,) = evaluate_risk(RiskInputs(drawdown=0.25), limits)
    assert f.key == "risk:drawdown" and "not a reason to sell" in f.detail


def test_correlation_needs_both_a_high_correlation_and_enough_weight() -> None:
    from steward.portfolio.risk_limits import PairCorrelation

    limits = RiskLimits(max_pair_correlation=0.85)
    inputs = RiskInputs(
        correlations=[
            PairCorrelation("A", "B", 0.90, 0.30),  # breach
            PairCorrelation("A", "C", 0.90, 0.05),  # too small to matter
            PairCorrelation("B", "C", 0.80, 0.40),  # not correlated enough
        ]
    )
    assert [f.key for f in evaluate_risk(inputs, limits)] == ["risk:correlation:A|B"]
