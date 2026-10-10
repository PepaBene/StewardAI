"""Value at Risk and Expected Shortfall of a return series (plan Q2.1). Pure.

Losses are positive fractions of the portfolio (0.05 = a 5 % loss). Three estimates, so the owner
can see how much the answer depends on the method:

- historical: the empirical quantile and the mean of the returns beyond it;
- filtered historical (Barone-Adesi, Giannopoulos and Vosper, 1999): returns are divided by the
  EWMA volatility of their day, then multiplied by today's, so a calm stretch is not read as
  proof that today's calm is safe, nor a storm as proof of the opposite;
- Cornish-Fisher: the normal quantile corrected for the series' skew and excess kurtosis, with the
  Expected Shortfall as the average of that quantile over the tail.

A k-period horizon uses compounded k-period returns (overlapping), never a square-root rule on
the historical estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import numpy.typing as npt

METHOD_VERSION = "tail-2"
# The same 26-week half-life as the covariance model, so the filtered estimates and the back-test
# of the risk model test one volatility model (0.94 is RiskMetrics' decay for daily data).
EWMA_HALF_LIFE_WEEKS = 26
EWMA_LAMBDA = float(0.5 ** (1.0 / EWMA_HALF_LIFE_WEEKS))
MIN_OBSERVATIONS = 52
TAIL_GRID = 400  # points averaged over the tail for the Cornish-Fisher Expected Shortfall

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class TailRisk:
    level: float
    observations: int
    var: float
    es: float


def simple_returns(log_returns: Array) -> Array:
    """The simple returns of log returns."""
    return np.expm1(np.asarray(log_returns, dtype=float))


def horizon_returns(returns: Array, periods: int) -> Array:
    """Compounded returns over `periods` consecutive observations, overlapping."""
    r = np.asarray(returns, dtype=float)
    if periods <= 1:
        return r
    if len(r) < periods:
        return np.empty(0)
    growth = np.cumprod(1.0 + r)
    lagged = np.concatenate(([1.0], growth[:-periods]))
    return growth[periods - 1 :] / lagged[: len(growth) - periods + 1] - 1.0


def historical(returns: Array, level: float) -> TailRisk | None:
    """Empirical VaR and ES at `level` (0.95: the loss exceeded on 5 % of periods)."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < MIN_OBSERVATIONS:
        return None
    cut = float(np.quantile(r, 1.0 - level))
    tail = r[r <= cut]
    return TailRisk(level, len(r), max(-cut, 0.0), max(-float(tail.mean()), 0.0))


def ewma_volatility(returns: Array, lam: float = EWMA_LAMBDA) -> Array:
    """The EWMA volatility known at the start of each period (zero mean, RiskMetrics): entry t
    uses returns before t only. The first is seeded with the sample standard deviation."""
    r = np.asarray(returns, dtype=float)
    out = np.empty(len(r))
    variance = float(np.var(r[: min(len(r), 20)])) or 1e-12
    for t in range(len(r)):
        out[t] = np.sqrt(variance)
        variance = lam * variance + (1 - lam) * r[t] ** 2
    return out


def filtered_historical(returns: Array, level: float, lam: float = EWMA_LAMBDA) -> TailRisk | None:
    """VaR and ES from returns rescaled to today's volatility."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < MIN_OBSERVATIONS:
        return None
    sigma = ewma_volatility(r, lam)
    # Volatility for the next period, after the last return.
    today = float(np.sqrt(lam * sigma[-1] ** 2 + (1 - lam) * r[-1] ** 2))
    standardised = r / np.where(sigma > 0, sigma, np.nan)
    standardised = standardised[~np.isnan(standardised)]
    return historical(standardised * today, level)


def _cornish_fisher_quantile(p: float, skew: float, excess_kurtosis: float) -> float:
    z = NormalDist().inv_cdf(p)
    return (
        z
        + (z**2 - 1) * skew / 6
        + (z**3 - 3 * z) * excess_kurtosis / 24
        - (2 * z**3 - 5 * z) * skew**2 / 36
    )


def cornish_fisher(returns: Array, level: float) -> TailRisk | None:
    """VaR and ES from the mean, standard deviation, skew and excess kurtosis of the series."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < MIN_OBSERVATIONS:
        return None
    mean, sd = float(r.mean()), float(r.std(ddof=1))
    if sd <= 0:
        return TailRisk(level, len(r), max(-mean, 0.0), max(-mean, 0.0))
    centred = (r - mean) / sd
    skew = float((centred**3).mean())
    kurt = float((centred**4).mean() - 3.0)
    alpha = 1.0 - level
    var = -(mean + sd * _cornish_fisher_quantile(alpha, skew, kurt))
    grid = (np.arange(TAIL_GRID) + 0.5) / TAIL_GRID * alpha
    quantiles = np.array([_cornish_fisher_quantile(float(u), skew, kurt) for u in grid])
    # The expansion is valid only for moderate skew and kurtosis; outside that domain the
    # quantile function folds over. A quantile that falls as the probability rises is no
    # quantile, so there is no estimate rather than a plausible-looking wrong one.
    if np.any(np.diff(quantiles) < -1e-12):
        return None
    es = -(mean + sd * float(quantiles.mean()))
    return TailRisk(level, len(r), max(var, 0.0), max(es, var, 0.0))


def es_contributions(
    weights: Array, asset_returns: Array, level: float
) -> tuple[Array, int] | None:
    """Each position's share of the portfolio's historical Expected Shortfall, as a fraction of
    the portfolio (they add up to the ES): the weight times the position's mean return on the
    periods when the portfolio was in its worst `1 - level`. Also the number of such periods."""
    data = np.asarray(asset_returns, dtype=float)
    if data.ndim != 2 or len(data) < MIN_OBSERVATIONS or data.shape[1] != len(weights):
        return None
    portfolio = data @ weights
    cut = np.quantile(portfolio, 1.0 - level)
    tail = portfolio <= cut
    if not tail.any():
        return None
    return -(weights * data[tail].mean(axis=0)), int(tail.sum())
