"""Does the risk model forecast what happens? (quant plan Q6.1). Pure functions.

Method. For a weekly return series r_t and a volatility forecast sigma_t made BEFORE week t, a
parametric value-at-risk at confidence c is  VaR_t = z_c sigma_t  (z_c the standard normal
quantile, zero mean). A week is a hit when r_t < -VaR_t. If the model is right, hits are
independent and occur with probability p = 1 - c.

- Kupiec (1995) proportion-of-failures test: with n weeks and x hits,
      LR_pof = -2 ln[ (1-p)^(n-x) p^x ] + 2 ln[ (1-x/n)^(n-x) (x/n)^x ]   ~ chi2(1)
  rejects a hit rate that is too high or too low.
- Christoffersen (1998) independence test on the hit sequence: with n_ij the number of weeks in
  state j (hit or not) after state i,
      LR_ind = -2 ln[ (1-pi)^(n00+n10) pi^(n01+n11) ]
               + 2 ln[ (1-pi0)^n00 pi0^n01 (1-pi1)^n10 pi1^n11 ]
  ~ chi2(1) rejects hits that come in clusters. LR_cc = LR_pof + LR_ind ~ chi2(2) tests both.
- Calibration: the standardised return r_t / sigma_t has a standard deviation of 1 if sigma_t is
  right. The ratio realised / forecast is reported with the sampling error 1/sqrt(2n) of a
  standard deviation; a ratio further than two standard errors from 1 is flagged.

References: Kupiec (1995) "Techniques for verifying the accuracy of risk measurement models";
Christoffersen (1998) "Evaluating interval forecasts".
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from statistics import NormalDist

import numpy as np

WEEKS_PER_YEAR = 52
MIN_HISTORY_WEEKS = 52
SIGNIFICANCE = 0.05


def chi2_sf(x: float, df: int) -> float:
    """Survival function of a chi-square variable with 1 or 2 degrees of freedom."""
    if x <= 0:
        return 1.0
    if df == 1:
        return math.erfc(math.sqrt(x / 2.0))
    if df == 2:
        return math.exp(-x / 2.0)
    raise ValueError("only 1 or 2 degrees of freedom are needed")


def _xlogy(x: float, y: float) -> float:
    return 0.0 if x == 0 else x * math.log(y)


def kupiec_pof(n: int, hits: int, p: float) -> tuple[float, float]:
    """(statistic, p-value) of the proportion-of-failures test."""
    if n <= 0:
        return 0.0, 1.0
    rate = hits / n
    log_null = _xlogy(n - hits, 1 - p) + _xlogy(hits, p)
    log_alt = _xlogy(n - hits, 1 - rate) + _xlogy(hits, rate)
    lr = max(0.0, -2.0 * (log_null - log_alt))
    return lr, chi2_sf(lr, 1)


def christoffersen_independence(hit_sequence: Sequence[bool]) -> tuple[float, float]:
    """(statistic, p-value) of the independence test; (0, 1) when the sequence cannot say
    (fewer than two weeks, or no week in one of the two states to condition on)."""
    n00 = n01 = n10 = n11 = 0
    for prev, cur in pairwise(hit_sequence):
        if prev:
            n11 += cur
            n10 += not cur
        else:
            n01 += cur
            n00 += not cur
    total = n00 + n01 + n10 + n11
    if total == 0 or n01 + n11 == 0:
        return 0.0, 1.0
    pi = (n01 + n11) / total
    pi0 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi1 = n11 / (n10 + n11) if n10 + n11 else 0.0
    log_null = _xlogy(n00 + n10, 1 - pi) + _xlogy(n01 + n11, pi)
    log_alt = _xlogy(n00, 1 - pi0) + _xlogy(n01, pi0) + _xlogy(n10, 1 - pi1) + _xlogy(n11, pi1)
    lr = max(0.0, -2.0 * (log_null - log_alt))
    return lr, chi2_sf(lr, 1)


@dataclass(frozen=True)
class VarBacktest:
    confidence: float
    weeks: int
    hits: int
    expected_hits: float
    hit_rate: float | None
    kupiec_p: float
    independence_p: float
    conditional_p: float
    passed: bool
    reason: str | None  # why it was not run, or what failed


def rolling_ewma_sigma(
    returns: Sequence[float], half_life: float, min_history: int = MIN_HISTORY_WEEKS
) -> list[float | None]:
    """The weekly volatility forecast for each week from the weeks BEFORE it (zero-mean EWMA
    with the given half-life); None for the first `min_history` weeks."""
    lam = 0.5 ** (1.0 / half_life)
    out: list[float | None] = []
    var: float | None = None
    for i, r in enumerate(returns):
        out.append(math.sqrt(var) if var is not None and i >= min_history else None)
        # seed with the plain mean square of the first `min_history` weeks, then update
        if i + 1 == min_history:
            var = float(np.mean(np.square(returns[: i + 1])))
        elif var is not None:
            var = lam * var + (1 - lam) * r * r
    return out


def var_backtest(
    returns: Sequence[float], sigmas: Sequence[float | None], confidence: float
) -> VarBacktest:
    """Kupiec and Christoffersen tests of the parametric VaR at `confidence`."""
    pairs = [(r, s) for r, s in zip(returns, sigmas, strict=True) if s is not None and s > 0]
    p = 1.0 - confidence
    n = len(pairs)
    if n < 2:
        return VarBacktest(confidence, n, 0, n * p, None, 1.0, 1.0, 1.0, True,
                           "fewer than two forecast weeks")  # fmt: skip
    z = NormalDist().inv_cdf(confidence)
    sequence = [r < -z * s for r, s in pairs]
    hits = sum(sequence)
    pof, pof_p = kupiec_pof(n, hits, p)
    ind, ind_p = christoffersen_independence(sequence)
    cc_p = chi2_sf(pof + ind, 2)
    passed = cc_p >= SIGNIFICANCE and pof_p >= SIGNIFICANCE
    why = None
    if not passed:
        why = (
            f"{hits} hits in {n} weeks against {n * p:.1f} expected"
            if pof_p < SIGNIFICANCE
            else "hits come in clusters"
        )
    return VarBacktest(confidence, n, hits, n * p, hits / n, pof_p, ind_p, cc_p, passed, why)


@dataclass(frozen=True)
class Calibration:
    weeks: int
    realised_over_forecast: float | None  # std of r/sigma; 1 is perfect
    standard_error: float | None
    biased: bool
    direction: str | None  # "understates" when realised exceeds the forecast
    mean_forecast_vol: float | None  # annualised
    realised_vol: float | None  # annualised, same weeks


def calibration(returns: Sequence[float], sigmas: Sequence[float | None]) -> Calibration:
    pairs = [(r, s) for r, s in zip(returns, sigmas, strict=True) if s is not None and s > 0]
    n = len(pairs)
    if n < 20:
        return Calibration(n, None, None, False, None, None, None)
    r = np.array([a for a, _ in pairs])
    s = np.array([b for _, b in pairs])
    ratio = float(np.sqrt(np.mean((r / s) ** 2)))
    se = 1.0 / math.sqrt(2.0 * n)
    biased = abs(ratio - 1.0) > 2.0 * se
    direction = None
    if biased:
        direction = "understates" if ratio > 1.0 else "overstates"
    return Calibration(
        n, ratio, se, biased, direction,
        float(np.sqrt(np.mean(s**2)) * math.sqrt(WEEKS_PER_YEAR)),
        float(np.sqrt(np.mean(r**2)) * math.sqrt(WEEKS_PER_YEAR)),
    )  # fmt: skip
