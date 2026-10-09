"""Factor regressions (quant plan Q4.2).

Method. For a return series y (weekly base-currency log returns minus the weekly risk-free
rate) and K factor series X (Fama-French market, size, value, profitability, investment and
momentum from the Ken French Data Library, plus the weekly log change of USD and EUR in the
base currency):

    y_t = alpha + sum_k beta_k X_kt + e_t        ordinary least squares, rows with a gap dropped

Standard errors are Newey-West (Bartlett kernel, `NW_LAGS` lags), which allows for serial
correlation and changing variance in e:

    V = (X'X)^-1 S (X'X)^-1
    S = G0 + sum_{l=1..L} (1 - l/(L+1)) (Gl + Gl')
    Gl = sum_t e_t e_{t-l} x_t x_{t-l}'

and the t-statistic is beta_k / sqrt(V_kk). The factors are in US dollars while y is in the
base currency, so the two currency factors absorb the exchange-rate part and the factor betas
are not distorted by it.

Daily factor returns are compounded to the weekly Wednesday grid of `returns` (a week needs at
least `MIN_DAYS_PER_WEEK` daily observations, otherwise it is missing).

References: Fama and French (2015) "A five-factor asset pricing model"; Carhart (1997);
Newey and West (1987).
"""

import bisect
import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

NW_LAGS = 4
MIN_DAYS_PER_WEEK = 3
MIN_OBSERVATIONS = 40
WEEKS_PER_YEAR = 52

Daily = Mapping[str, Sequence[tuple[dt.date, float]]]


def weekly_factor_returns(
    daily: Daily, dates: Sequence[dt.date]
) -> dict[str, npt.NDArray[np.float64]]:
    """Weekly log returns of each daily factor series over the weeks ending on `dates`
    (each week is the 7 days up to and including its date)."""
    out: dict[str, npt.NDArray[np.float64]] = {}
    for name, series in daily.items():
        days = [d for d, _ in series]
        logs = np.log1p(np.array([v for _, v in series], dtype=np.float64))
        col = np.full(len(dates), np.nan)
        for i, end in enumerate(dates):
            lo = bisect.bisect_right(days, end - dt.timedelta(days=7))
            hi = bisect.bisect_right(days, end)
            if hi - lo >= MIN_DAYS_PER_WEEK:
                col[i] = float(logs[lo:hi].sum())
        out[name] = col
    return out


@dataclass(frozen=True)
class FactorFit:
    alpha: float  # per week
    betas: dict[str, float]
    t_stats: dict[str, float]
    r_squared: float
    observations: int


def newey_west_covariance(
    x: npt.NDArray[np.float64], resid: npt.NDArray[np.float64], lags: int = NW_LAGS
) -> npt.NDArray[np.float64]:
    """HAC covariance of the OLS coefficients; `x` includes the intercept column."""
    n, _ = x.shape
    xe = x * resid[:, None]
    s = xe.T @ xe
    for lag in range(1, min(lags, n - 1) + 1):
        gamma = xe[lag:].T @ xe[:-lag]
        s += (1 - lag / (lags + 1)) * (gamma + gamma.T)
    bread = np.linalg.inv(x.T @ x)
    return np.asarray(bread @ s @ bread, dtype=np.float64)


def regress(
    y: npt.NDArray[np.float64],
    factors: Mapping[str, npt.NDArray[np.float64]],
    min_observations: int = MIN_OBSERVATIONS,
) -> FactorFit | None:
    """OLS of y on the factors with Newey-West t-statistics; None when too few complete rows
    or the factors are collinear over them."""
    names = list(factors)
    x = np.column_stack([factors[k] for k in names]) if names else np.empty((y.size, 0))
    keep = ~np.isnan(y) & ~np.isnan(x).any(axis=1)
    n = int(keep.sum())
    if n < max(min_observations, len(names) + 10):
        return None
    yy = y[keep]
    design = np.column_stack([np.ones(n), x[keep]])
    if np.linalg.matrix_rank(design) < design.shape[1]:
        return None
    coef, *_ = np.linalg.lstsq(design, yy, rcond=None)
    resid = yy - design @ coef
    sst = float(((yy - yy.mean()) ** 2).sum())
    r2 = 1 - float((resid**2).sum()) / sst if sst > 0 else 0.0
    cov = newey_west_covariance(design, resid)
    se = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    t = np.divide(coef, se, out=np.zeros_like(coef), where=se > 0)
    return FactorFit(
        float(coef[0]),
        {k: float(coef[i + 1]) for i, k in enumerate(names)},
        {k: float(t[i + 1]) for i, k in enumerate(names)},
        r2,
        n,
    )
