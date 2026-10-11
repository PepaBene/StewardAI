"""Position sizing and concentration arithmetic (plan Q5.2, Q5.3, Q5.5). Pure functions.

Sizing is a ceiling, never a target: the owner's tier caps (principle 8b) stay authoritative,
the risk-aware size can only lower them, and the Kelly figure is shown next to them as a bound
with its inputs. Expected returns are the owner's thesis returns shrunk toward the return the
market would be expected to pay for the stock's beta (principle 8a: the market's expected return
is the yardstick), more or less so according to how far the thesis is trusted.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

KELLY_FRACTION = 0.25  # the strategy's "roughly expected return divided by variance" at a quarter
BISECTION_STEPS = 24


def equilibrium_return(risk_free: float, beta: float, premium: float) -> float:
    """What the market would be expected to pay for this stock's beta (CAPM)."""
    return risk_free + beta * premium


def shrink_expected_return(thesis: float, equilibrium: float, confidence: float) -> float:
    """Pull the thesis return toward the equilibrium return; confidence 1 keeps the thesis,
    0 returns the equilibrium (Black-Litterman style, one view)."""
    c = min(max(confidence, 0.0), 1.0)
    return c * thesis + (1.0 - c) * equilibrium


@dataclass(frozen=True)
class Kelly:
    full: float | None  # expected excess return / variance
    fraction: float
    bound: float | None  # fraction x full, floored at zero
    excess_return: float
    volatility: float


def kelly_bound(
    expected_return: float, risk_free: float, volatility: float, fraction: float = KELLY_FRACTION
) -> Kelly:
    """Fractional Kelly for one risky asset: f = (mu - r) / sigma^2, times `fraction`. Shown as
    an upper bound only; a negative excess return gives a bound of zero."""
    excess = expected_return - risk_free
    if volatility <= 0 or not math.isfinite(volatility):
        return Kelly(None, fraction, None, excess, volatility)
    full = excess / (volatility * volatility)
    return Kelly(full, fraction, max(0.0, full) * fraction, excess, volatility)


def largest_feasible(
    ok: Callable[[float], bool], upper: float, steps: int = BISECTION_STEPS
) -> float:
    """The largest weight in [0, upper] for which `ok` holds, assuming that more weight never
    makes a limit easier to meet (so the feasible weights form an interval starting at zero).
    Returns 0 when even a vanishing position fails, `upper` when the cap itself is fine."""
    if upper <= 0:
        return 0.0
    if ok(upper):
        return upper
    lo, hi = 0.0, upper
    if not ok(1e-6):
        return 0.0
    for _ in range(steps):
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


# --------------------------------------------------------------------------- rule 9 (principle 4f)


@dataclass(frozen=True)
class DilutionStep:
    month: int
    weight: float


@dataclass(frozen=True)
class Dilution:
    weight_now: float
    cap: float
    months_to_cap: int | None  # None: not reached within the horizon (or no deposit)
    path: list[DilutionStep]
    monthly_deposit: float


def dilution_path(
    position_value: float,
    total_value: float,
    monthly_deposit: float,
    cap: float,
    months: int = 36,
) -> Dilution:
    """The position's weight month by month if every deposit goes to other holdings and nothing
    else changes (no price moves). It shows how far deposits alone get a position under its
    cap."""
    path: list[DilutionStep] = []
    reached: int | None = None
    now = position_value / total_value if total_value > 0 else 0.0
    for m in range(months + 1):
        total = total_value + monthly_deposit * m
        w = position_value / total if total > 0 else 0.0
        path.append(DilutionStep(m, w))
        if reached is None and w <= cap + 1e-12:
            reached = m
    return Dilution(now, cap, reached, path, monthly_deposit)


def trim_to(position_value: float, total_value: float, target_weight: float) -> float:
    """Value to sell to bring the position down to `target_weight` of the total; the proceeds
    stay in the portfolio, so the total is unchanged. Zero when it is already at or below."""
    if total_value <= 0:
        return 0.0
    return max(0.0, position_value - target_weight * total_value)
