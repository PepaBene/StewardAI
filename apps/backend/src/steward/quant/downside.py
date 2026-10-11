"""Downside statistics of realised returns (plan Q2.3). Pure; periodic simple returns in.

Sortino ratio, Calmar ratio, Ulcer index, downside deviation, skew and excess kurtosis. Annual
figures scale by `periods_per_year` (52 for weekly returns). Nothing is returned for a series
shorter than 26 periods.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

METHOD_VERSION = "downside-1"
MIN_PERIODS = 26

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class DownsideStats:
    periods: int
    annual_return: float
    downside_deviation: float  # annualised, below the minimum acceptable return
    sortino: float | None
    max_drawdown: float
    calmar: float | None
    ulcer_index: float
    skew: float
    excess_kurtosis: float


def drawdown_path(returns: Array) -> Array:
    """The fall from the running peak after each period (positive fractions)."""
    value = np.cumprod(1.0 + np.asarray(returns, dtype=float))
    peak = np.maximum.accumulate(np.concatenate(([1.0], value)))[1:]
    return 1.0 - value / peak


def downside_stats(
    returns: Array, periods_per_year: int = 52, minimum_acceptable: float = 0.0
) -> DownsideStats | None:
    """`minimum_acceptable` is the yearly return below which a period counts as a shortfall."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < MIN_PERIODS:
        return None
    years = len(r) / periods_per_year
    growth = float(np.prod(1.0 + r))
    annual = growth ** (1.0 / years) - 1.0 if growth > 0 else -1.0
    target = (1.0 + minimum_acceptable) ** (1.0 / periods_per_year) - 1.0
    shortfall = np.minimum(r - target, 0.0)
    deviation = float(np.sqrt((shortfall**2).mean()) * np.sqrt(periods_per_year))
    falls = drawdown_path(r)
    worst = float(falls.max())
    sd = float(r.std(ddof=1))
    if sd > 0:
        centred = (r - r.mean()) / sd
        skew = float((centred**3).mean())
        kurt = float((centred**4).mean() - 3.0)
    else:
        skew = kurt = 0.0
    return DownsideStats(
        periods=len(r),
        annual_return=annual,
        downside_deviation=deviation,
        sortino=(annual - minimum_acceptable) / deviation if deviation > 0 else None,
        max_drawdown=worst,
        calmar=annual / worst if worst > 0 else None,
        ulcer_index=float(np.sqrt((falls**2).mean())),
        skew=skew,
        excess_kurtosis=kurt,
    )
