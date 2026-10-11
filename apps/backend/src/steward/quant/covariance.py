"""Covariance estimators on weekly base-currency returns (quant plan Q1.1).

Both estimate the covariance of WEEKLY log returns and annualise by 52.

- EWMA (RiskMetrics, J.P. Morgan/Reuters 1996): Sigma = sum_t w_t r_t r_t', zero mean, with
  w_t proportional to lambda^(T-1-t) (newest row heaviest) normalised to sum to one, and
  lambda = 0.5^(1 / half_life). A half-life of 26 weeks gives lambda = 0.9737. Reacts to
  the current volatility regime.
- Ledoit-Wolf constant-correlation shrinkage (Ledoit & Wolf 2003, "Improved estimation of the
  covariance matrix of stock returns"; 2004, "Honey, I shrunk the sample covariance matrix"):
  the sample covariance is shrunk towards the matrix with the same variances and one common
  pairwise correlation (the average sample correlation). The shrinkage intensity
  delta = max(0, min(1, (pi - rho) / gamma / T)) follows the authors' published estimator
  (their covCor routine): pi is the sum of the asymptotic variances of the sample covariance
  entries, rho the part of it that the target explains, gamma the squared Frobenius distance
  between sample and target. Anchors the long-run view when N is large against T.

Portfolio volatility = sqrt(w' Sigma w) x sqrt(52) for weights w as shares of the TOTAL
portfolio value (cash has zero weight in w, so it counts at zero volatility).
"""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

WEEKS_PER_YEAR = 52
EWMA_HALF_LIFE_WEEKS = 26

Matrix = npt.NDArray[np.float64]


def ewma_lambda(half_life: float = EWMA_HALF_LIFE_WEEKS) -> float:
    return float(0.5 ** (1.0 / half_life))


def ewma_covariance(returns: Matrix, half_life: float = EWMA_HALF_LIFE_WEEKS) -> Matrix:
    """Weekly EWMA covariance of the rows of `returns` (oldest first, no NaN), zero mean."""
    t = returns.shape[0]
    lam = ewma_lambda(half_life)
    w = lam ** np.arange(t - 1, -1, -1, dtype=float)
    w /= w.sum()
    return (returns * w[:, None]).T @ returns


@dataclass(frozen=True)
class Shrunk:
    covariance: Matrix
    intensity: float  # weight on the constant-correlation target, 0..1
    mean_correlation: float


def ledoit_wolf(returns: Matrix) -> Shrunk:
    """Constant-correlation shrinkage of the sample covariance (see the module docstring)."""
    t, n = returns.shape
    x = returns - returns.mean(axis=0)
    sample = x.T @ x / (t - 1)
    var = np.diag(sample).copy()
    sd = np.sqrt(var)
    if n == 1 or np.any(sd == 0):
        return Shrunk(sample, 0.0, 1.0)
    corr_sum = float((sample / np.outer(sd, sd)).sum()) - n
    r_bar = corr_sum / (n * (n - 1))
    prior = r_bar * np.outer(sd, sd)
    np.fill_diagonal(prior, var)

    y = x**2
    cross = x.T @ x / t
    phi_mat = y.T @ y / t - 2 * cross * sample + sample**2
    phi = float(phi_mat.sum())

    term1 = (x**3).T @ x / t
    term2 = np.diag(cross)[:, None] * sample
    term3 = cross * var[:, None]
    term4 = var[:, None] * sample
    theta = term1 - term2 - term3 + term4
    np.fill_diagonal(theta, 0.0)
    rho = float(np.trace(phi_mat)) + r_bar * float((np.outer(1 / sd, sd) * theta).sum())

    gamma = float(((sample - prior) ** 2).sum())
    kappa = (phi - rho) / gamma if gamma > 0 else 1.0
    delta = max(0.0, min(1.0, kappa / t))
    return Shrunk(delta * prior + (1 - delta) * sample, delta, r_bar)


def annualise(weekly_covariance: Matrix) -> Matrix:
    return weekly_covariance * WEEKS_PER_YEAR


def portfolio_volatility(weights: npt.NDArray[np.float64], weekly_covariance: Matrix) -> float:
    """Annualised volatility of the portfolio, fraction."""
    variance = float(weights @ weekly_covariance @ weights)
    return math.sqrt(max(variance, 0.0) * WEEKS_PER_YEAR)
