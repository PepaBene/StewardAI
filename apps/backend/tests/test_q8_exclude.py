"""Q8.0: the single-position rule can exempt named holdings."""

from typing import Any

import pytest

from steward.config.strategy import StrategyConfig
from steward.portfolio.breaches import evaluate
from steward.portfolio.drift import (
    BucketDrift,
    CashDrift,
    DimensionAllocation,
    DriftReport,
    PositionDrift,
)


def _position(ticker: str, weight: float) -> PositionDrift:
    return PositionDrift(
        ticker=ticker, weight=weight, target_weight=None, band=None, lower=None, upper=None,
        hard_limit=None, drift=None, status="no_target", held=True,
    )  # fmt: skip


def _drift() -> DriftReport:
    return DriftReport(
        weight_basis="total",
        basis_value_base=1000,
        positions=[
            _position("CORE_ETF", 0.40),
            _position("STOCK_A", 0.15),
            _position("STOCK_B", 0.05),
        ],
        categories=[
            DimensionAllocation(dimension="sector", buckets=[
                BucketDrift(bucket="Mixed", value_base=950, weight=0.6, target_weight=None,
                            band=None, lower=None, upper=None, drift=None, status="no_target"),
            ]),
        ],
        cash=CashDrift(value_base=50, weight=0.05, target_weight=None, band=None,
                       lower=None, upper=None, drift=None, status="no_target"),
        out_of_band=0,
    )  # fmt: skip


def _strategy(rule: dict[str, Any]) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "base_currency": "USD",
            "categories": ["sector"],
            "rules": [{"id": "max-single", "type": "position_weight", "operator": "<=", **rule}],
        }
    )


def _breached(strategy: StrategyConfig) -> set[str | None]:
    return {f.ticker for f in evaluate(_drift(), strategy) if f.kind == "rule"}


def test_without_an_exempt_list_every_position_is_checked() -> None:
    assert _breached(_strategy({"value": 0.10})) == {"CORE_ETF", "STOCK_A"}


def test_an_excluded_ticker_above_the_cap_raises_nothing_and_others_still_breach() -> None:
    assert _breached(_strategy({"value": 0.10, "exclude": ["CORE_ETF"]})) == {"STOCK_A"}


def test_a_rule_for_one_ticker_cannot_carry_an_exempt_list() -> None:
    with pytest.raises(ValueError, match="no single ticker"):
        _strategy({"value": 0.10, "ticker": "STOCK_A", "exclude": ["CORE_ETF"]})


def test_the_exempt_list_rejects_duplicates() -> None:
    with pytest.raises(ValueError):
        _strategy({"value": 0.10, "exclude": ["CORE_ETF", "CORE_ETF"]})
