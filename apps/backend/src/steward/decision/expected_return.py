"""Expected return from the owner's thesis scenarios (strategy principles 5 and 8a, plan Q8.2).

Pure functions, no I/O.

- Expected value is the probability-weighted value per share of the bear, base and bull cases;
  the expected return is that value over the current price, minus one.
- It is annualised to the catalyst date (two years when none is set, never less than three
  months, so a catalyst a week away cannot turn a small gain into a huge yearly figure).
- Asymmetry is the base-case upside divided by the bear-case loss. With a bear case at or above
  the price there is no loss, and the ratio is left unset rather than infinite.
- The hurdle (default 30 % over the horizon) and the asymmetry gate (default 2 to 1) are inputs,
  not constants: a threshold of None switches that flag off.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from steward.config.thesis import ThesisPriceTarget, ThesisScenarios

DEFAULT_HORIZON_YEARS = 2.0
MIN_HORIZON_YEARS = 0.25


@dataclass(frozen=True)
class ExpectedReturn:
    price: float
    expected_value: float
    total: float  # over the horizon, as a fraction
    annualized: float
    horizon_years: float
    horizon_source: str  # "catalyst date" or "default"
    upside: float  # base case over the price, minus one
    loss: float  # 1 - bear / price, floored at zero
    asymmetry: float | None  # upside / loss; None when there is no loss
    meets_hurdle: bool | None  # None when the hurdle is off
    meets_asymmetry: bool | None  # None when the gate is off or there is no loss to weigh


def horizon_years(catalyst: dt.date | None, today: dt.date) -> tuple[float, str]:
    """(years to the catalyst, source). Two years without one; at least three months."""
    if catalyst is None:
        return DEFAULT_HORIZON_YEARS, "default"
    years = (catalyst - today).days / 365.25
    return max(years, MIN_HORIZON_YEARS), "catalyst date"


def annualize(total: float, years: float) -> float:
    """The yearly rate that compounds to `total` over `years`; a total loss stays -100 %."""
    if total <= -1:
        return -1.0
    return float((1 + total) ** (1 / years) - 1)


def expected_return(
    scenarios: ThesisScenarios,
    price: float,
    *,
    catalyst: dt.date | None,
    today: dt.date,
    hurdle: float | None,
    asymmetry_gate: float | None,
) -> ExpectedReturn:
    if price <= 0:
        raise ValueError("the price must be positive")
    years, source = horizon_years(catalyst, today)
    total = scenarios.expected_value / price - 1
    upside = scenarios.base / price - 1
    loss = max(0.0, 1 - scenarios.bear / price)
    ratio = upside / loss if loss > 0 else None
    return ExpectedReturn(
        price=price,
        expected_value=scenarios.expected_value,
        total=total,
        annualized=annualize(total, years),
        horizon_years=years,
        horizon_source=source,
        upside=upside,
        loss=loss,
        asymmetry=ratio,
        meets_hurdle=None if hurdle is None else total >= hurdle,
        meets_asymmetry=None
        if asymmetry_gate is None or ratio is None
        else ratio >= asymmetry_gate,
    )


def scenarios_of(target: ThesisPriceTarget | None) -> ThesisScenarios | None:
    """The target's scenarios; none when the thesis has only price levels."""
    return target.scenarios if target is not None else None
