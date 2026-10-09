"""Risk statistics on an aligned return series (methodology.md section 11).

Series are simple returns at a stated frequency. Every statistic carries its sample size and is
suppressed below the minimum in `thresholds`. Historical VaR and expected shortfall are
descriptive estimates, not forecasts.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from steward.analytics.contract import AnalyticsResult, result, unavailable
from steward.analytics.thresholds import THRESHOLDS
from steward.analytics.versions import version

ONE = Decimal(1)
ZERO = Decimal(0)

ANNUALISATION = {"daily": 252, "weekly": 52, "monthly": 12}


def mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, ZERO) / len(values)


def sample_stdev(values: Sequence[Decimal]) -> Decimal:
    m = mean(values)
    return (sum(((v - m) ** 2 for v in values), ZERO) / (len(values) - 1)).sqrt()


def _gate(n: int, minimum: int, unit: str, what: str) -> AnalyticsResult | None:
    if n < minimum:
        return unavailable(unit, version("risk"), f"{what}: {n} observations, minimum is {minimum}")
    return None


def _frequency_problem(unit: str, frequency: str) -> AnalyticsResult | None:
    """An unknown frequency is reported as unavailable, not raised as a KeyError."""
    if frequency not in ANNUALISATION:
        known = ", ".join(ANNUALISATION)
        return unavailable(unit, version("risk"), f"unknown frequency {frequency!r}; use {known}")
    return None


def volatility(returns: Sequence[Decimal], frequency: str = "daily") -> AnalyticsResult:
    """Sample standard deviation x sqrt(periods per year)."""
    blocked = _frequency_problem("fraction", frequency) or _gate(
        len(returns), THRESHOLDS.min_obs_volatility, "fraction", "volatility"
    )
    if blocked:
        return blocked
    factor = Decimal(ANNUALISATION[frequency]).sqrt()
    return result(sample_stdev(returns) * factor, "fraction", version("risk"), inputs=list(returns))


def downside_deviation(
    returns: Sequence[Decimal], frequency: str = "daily", mar: Decimal = ZERO
) -> AnalyticsResult:
    """`sqrt(sum(min(r - MAR, 0)^2) / n) x sqrt(periods per year)`, over all n observations."""
    blocked = _frequency_problem("fraction", frequency) or _gate(
        len(returns), THRESHOLDS.min_obs_volatility, "fraction", "downside deviation"
    )
    if blocked:
        return blocked
    shortfall = sum((min(r - mar, ZERO) ** 2 for r in returns), ZERO) / len(returns)
    factor = Decimal(ANNUALISATION[frequency]).sqrt()
    return result(shortfall.sqrt() * factor, "fraction", version("risk"), inputs=list(returns))


def max_drawdown(returns: Sequence[Decimal]) -> AnalyticsResult:
    """Worst peak-to-trough fall of the compounded wealth path starting at 1 (a negative number)."""
    blocked = _gate(len(returns), THRESHOLDS.min_obs_drawdown, "fraction", "drawdown")
    if blocked:
        return blocked
    wealth = peak = ONE
    worst = ZERO
    for r in returns:
        wealth *= ONE + r
        peak = max(peak, wealth)
        worst = min(worst, wealth / peak - ONE)
    return result(worst, "fraction", version("risk"), inputs=list(returns))


def var_es(
    returns: Sequence[Decimal], confidence: Decimal = Decimal("0.95")
) -> tuple[AnalyticsResult, AnalyticsResult]:
    """Historical VaR and expected shortfall as positive loss fractions.

    Sort ascending; `k = ceil(n x (1 - confidence))`; VaR is minus the k-th smallest return,
    ES minus the mean of the k smallest. A historical estimate: it describes the sample only.
    """
    method = version("risk")
    blocked = _gate(len(returns), THRESHOLDS.min_obs_var, "fraction", "value at risk")
    if blocked:
        return blocked, blocked
    ordered = sorted(returns)
    k = max(1, math.ceil(len(ordered) * (ONE - confidence)))
    tail = ordered[:k]
    note = ["historical estimate over the sample, not a forecast"]
    if tail[-1] > 0:
        note.append(
            "no losing period at this confidence level, so the figure is negative: "
            "the worst returns in the sample were gains"
        )
    return (
        result(-tail[-1], "fraction", method, warnings=note, inputs=list(returns)),
        result(-mean(tail), "fraction", method, warnings=note, inputs=list(returns)),
    )


def beta(asset: Sequence[Decimal], benchmark: Sequence[Decimal]) -> AnalyticsResult:
    """Sample cov(asset, benchmark) / var(benchmark) on aligned periods."""
    if len(asset) != len(benchmark):
        return unavailable("ratio", version("risk"), "series are not aligned")
    blocked = _gate(len(asset), THRESHOLDS.min_obs_beta, "ratio", "beta")
    if blocked:
        return blocked
    ma, mb = mean(asset), mean(benchmark)
    var_b = sum(((b - mb) ** 2 for b in benchmark), ZERO)
    if var_b == 0:
        return unavailable("ratio", version("risk"), "benchmark has no variance")
    cov = sum(((a - ma) * (b - mb) for a, b in zip(asset, benchmark, strict=True)), ZERO)
    return result(cov / var_b, "ratio", version("risk"), inputs=[list(asset), list(benchmark)])


def sharpe(
    returns: Sequence[Decimal], risk_free: Sequence[Decimal], frequency: str = "daily"
) -> AnalyticsResult:
    """Mean excess return / its sample stdev, annualised by sqrt(periods per year). The
    risk-free series must be aligned period by period."""
    if len(returns) != len(risk_free):
        return unavailable("ratio", version("risk"), "risk-free series is not aligned")
    blocked = _frequency_problem("ratio", frequency) or _gate(
        len(returns), THRESHOLDS.min_obs_volatility, "ratio", "Sharpe ratio"
    )
    if blocked:
        return blocked
    excess = [r - f for r, f in zip(returns, risk_free, strict=True)]
    deviation = sample_stdev(excess)
    if deviation == 0:
        return unavailable("ratio", version("risk"), "excess returns have no variance")
    factor = Decimal(ANNUALISATION[frequency]).sqrt()
    return result(
        mean(excess) / deviation * factor,
        "ratio",
        version("risk"),
        inputs=[list(returns), list(risk_free)],
    )


@dataclass(frozen=True)
class SampleInfo:
    n: int
    frequency: str
    annualisation: int


def sortino(
    returns: Sequence[Decimal], risk_free: Sequence[Decimal], frequency: str = "daily"
) -> AnalyticsResult:
    """Annualised mean excess return over the risk-free rate / annualised downside deviation of the
    same excess returns (`sqrt(mean(min(excess, 0)^2))`). Undefined with no losing period."""
    if len(returns) != len(risk_free):
        return unavailable("ratio", version("risk"), "risk-free series is not aligned")
    blocked = _frequency_problem("ratio", frequency) or _gate(
        len(returns), THRESHOLDS.min_obs_volatility, "ratio", "Sortino ratio"
    )
    if blocked:
        return blocked
    excess = [r - f for r, f in zip(returns, risk_free, strict=True)]
    shortfall = (sum((min(e, ZERO) ** 2 for e in excess), ZERO) / len(excess)).sqrt()
    if shortfall == 0:
        return unavailable("ratio", version("risk"), "no period fell short of the target")
    periods = Decimal(ANNUALISATION[frequency])
    value = mean(excess) * periods / (shortfall * periods.sqrt())
    return result(value, "ratio", version("risk"), inputs=[list(returns), list(risk_free)])


def correlation(a: Sequence[Decimal], b: Sequence[Decimal]) -> AnalyticsResult:
    """Pearson correlation of two aligned return series."""
    if len(a) != len(b):
        return unavailable("ratio", version("risk"), "series are not aligned")
    blocked = _gate(len(a), THRESHOLDS.min_obs_correlation, "ratio", "correlation")
    if blocked:
        return blocked
    ma, mb = mean(a), mean(b)
    va = sum(((x - ma) ** 2 for x in a), ZERO)
    vb = sum(((y - mb) ** 2 for y in b), ZERO)
    if va == 0 or vb == 0:
        return unavailable("ratio", version("risk"), "a series has no variance")
    cov = sum(((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)), ZERO)
    return result(cov / (va * vb).sqrt(), "ratio", version("risk"), inputs=[list(a), list(b)])


def rolling_correlation(
    a: Sequence[Decimal], b: Sequence[Decimal], window: int
) -> list[Decimal | None]:
    """Correlation over each trailing `window` of aligned observations; None until the window is
    full or where it has no variance. The window must reach the correlation minimum."""
    if len(a) != len(b):
        raise ValueError("series are not aligned")
    if window < THRESHOLDS.min_obs_correlation:
        raise ValueError(f"window must be at least {THRESHOLDS.min_obs_correlation} observations")
    out: list[Decimal | None] = []
    for end in range(len(a)):
        if end + 1 < window:
            out.append(None)
            continue
        out.append(correlation(a[end + 1 - window : end + 1], b[end + 1 - window : end + 1]).value)
    return out


def tracking_error(
    returns: Sequence[Decimal], benchmark: Sequence[Decimal], frequency: str = "daily"
) -> AnalyticsResult:
    """Annualised sample standard deviation of the active return (portfolio minus benchmark)."""
    if len(returns) != len(benchmark):
        return unavailable("fraction", version("risk"), "series are not aligned")
    blocked = _frequency_problem("fraction", frequency) or _gate(
        len(returns), THRESHOLDS.min_obs_tracking_error, "fraction", "tracking error"
    )
    if blocked:
        return blocked
    active = [r - b for r, b in zip(returns, benchmark, strict=True)]
    factor = Decimal(ANNUALISATION[frequency]).sqrt()
    return result(sample_stdev(active) * factor, "fraction", version("risk"), inputs=[active])


def diversification_benefit(
    weights: Sequence[Decimal], series: Sequence[Sequence[Decimal]]
) -> AnalyticsResult:
    """`1 - stdev(blend) / sum(w_i x stdev_i)` over aligned return series: the share of the
    weighted-average stand-alone risk that combining the holdings removes (0 = they move as one).
    Weights are renormalised to add to 1 and the blend is fixed-weight, so it describes today's
    mix over the sample, not the realised portfolio."""
    if len(weights) != len(series) or not series:
        return unavailable("fraction", version("risk"), "weights and series do not match")
    n = len(series[0])
    if any(len(s) != n for s in series):
        return unavailable("fraction", version("risk"), "series are not aligned")
    blocked = _gate(n, THRESHOLDS.min_obs_diversification, "fraction", "diversification benefit")
    if blocked:
        return blocked
    if any(x < 0 for x in weights):
        reason = "a weight is negative; short positions are not blended"
        return unavailable("fraction", version("risk"), reason)
    total = sum(weights, ZERO)
    if total <= 0:
        return unavailable("fraction", version("risk"), "no positive weight")
    w = [x / total for x in weights]
    blend = [sum((wi * s[t] for wi, s in zip(w, series, strict=True)), ZERO) for t in range(n)]
    standalone = sum((wi * sample_stdev(s) for wi, s in zip(w, series, strict=True)), ZERO)
    if standalone == 0:
        return unavailable("fraction", version("risk"), "the holdings have no variance")
    note = ["fixed-weight blend of today's holdings over the sample, not the realised portfolio"]
    return result(
        ONE - sample_stdev(blend) / standalone,
        "fraction",
        version("risk"),
        warnings=note,
        inputs=[w, series],
    )


def regression_sensitivity(
    asset: Sequence[Decimal], factor: Sequence[Decimal]
) -> tuple[AnalyticsResult, Decimal | None]:
    """Slope of `asset` on `factor` (a beta) with the R squared of the fit. The slope is a
    measured sensitivity only when the sample and the fit meet the thresholds; otherwise it is
    unavailable and the caller must use an owner assumption."""
    if len(asset) != len(factor):
        return unavailable("ratio", version("risk"), "series are not aligned"), None
    n = len(asset)
    if n < THRESHOLDS.min_obs_sensitivity:
        reason = f"sensitivity: {n} observations, minimum is {THRESHOLDS.min_obs_sensitivity}"
        return unavailable("ratio", version("risk"), reason), None
    ma, mf = mean(asset), mean(factor)
    vf = sum(((f - mf) ** 2 for f in factor), ZERO)
    va = sum(((a - ma) ** 2 for a in asset), ZERO)
    if vf == 0 or va == 0:
        return unavailable("ratio", version("risk"), "a series has no variance"), None
    cov = sum(((a - ma) * (f - mf) for a, f in zip(asset, factor, strict=True)), ZERO)
    r2 = cov * cov / (va * vf)
    if r2 < THRESHOLDS.min_sensitivity_r2:
        reason = f"sensitivity: R squared {r2:.3f} is below {THRESHOLDS.min_sensitivity_r2}"
        return unavailable("ratio", version("risk"), reason), r2
    return result(cov / vf, "ratio", version("risk"), inputs=[list(asset), list(factor)]), r2
