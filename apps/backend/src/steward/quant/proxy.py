"""Proxy backfill of short return histories (quant plan Q0.4).

A recent listing has too few weeks for a covariance estimate, yet must still get a risk
number. Method, on the weekly base-currency return matrix:

1. A series with at least `MIN_FIT_WEEKS` (26) weekly returns of its own is eligible; with fewer
   it is excluded and its weight is reported as uncovered.
2. For every candidate proxy (a sector or region ETF, or the benchmark) with at least
   `MIN_FIT_WEEKS` returns overlapping the series, regress own = alpha + beta x proxy by ordinary
   least squares. The candidate with the highest R-squared that also covers some of the
   missing weeks is chosen.
3. Each missing week is filled with beta x proxy + e, where e is drawn with replacement from the
   fit residuals (mean removed). The generator is seeded from the series key, so the same
   inputs always give the same matrix. Alpha is left out: an excess return measured on a few
   months is not extrapolated backwards. Resampling the residuals (rather than filling with
   beta x proxy alone) keeps the series' idiosyncratic variance, so its risk is not understated.

A backfilled column is labelled `proxied`, with the proxy, fit window, beta and R-squared, so
every figure that rests on it can say so.
"""

import datetime as dt
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from steward.quant.returns import ReturnMatrix, coverage_of

MIN_FIT_WEEKS = 26
SEED = 20261005

Status = Literal["real", "proxied", "excluded"]


@dataclass(frozen=True)
class ProxyFit:
    proxy: str
    beta: float
    alpha: float
    r_squared: float
    fit_weeks: int
    fit_start: dt.date
    fit_end: dt.date
    residual_sd: float  # weekly
    backfilled_weeks: int


@dataclass(frozen=True)
class ColumnStatus:
    key: str
    status: Status
    real_weeks: int
    backfilled_weeks: int
    fit: ProxyFit | None
    reason: str | None  # why excluded (or why no proxy was used)


def ols(
    y: npt.NDArray[np.float64], x: npt.NDArray[np.float64]
) -> tuple[float, float, float, npt.NDArray[np.float64]] | None:
    """(alpha, beta, R-squared, residuals) of y on x; None when x does not vary."""
    mx, my = x.mean(), y.mean()
    sxx = float(((x - mx) ** 2).sum())
    if sxx <= 0:
        return None
    beta = float(((x - mx) * (y - my)).sum() / sxx)
    alpha = float(my - beta * mx)
    resid = y - (alpha + beta * x)
    sst = float(((y - my) ** 2).sum())
    r2 = 1 - float((resid**2).sum()) / sst if sst > 0 else 0.0
    return alpha, beta, r2, resid


def backfill(
    matrix: ReturnMatrix, candidates: Sequence[str], min_fit_weeks: int = MIN_FIT_WEEKS
) -> tuple[ReturnMatrix, dict[str, ColumnStatus]]:
    """Fill the NaN weeks of every non-candidate column from its best-fitting candidate.

    Candidate columns are never altered. Returns the filled matrix and one status per column.
    """
    values = matrix.values.copy()
    usable = [c for c in candidates if c in matrix.keys]
    statuses: dict[str, ColumnStatus] = {}
    for j, key in enumerate(matrix.keys):
        own = matrix.values[:, j]
        missing = np.isnan(own)
        real = int((~missing).sum())
        if key in usable or not missing.any():
            statuses[key] = ColumnStatus(key, "real", real, 0, None, None)
            continue
        if real < min_fit_weeks:
            statuses[key] = ColumnStatus(
                key,
                "excluded",
                real,
                0,
                None,
                f"{real} weekly returns of its own; at least {min_fit_weeks} are needed "
                "to fit a proxy",
            )
            continue
        best: (
            tuple[float, str, float, float, npt.NDArray[np.float64], npt.NDArray[np.bool_]] | None
        ) = None
        for cand in usable:
            if cand == key:
                continue
            x = matrix.values[:, matrix.keys.index(cand)]
            overlap = ~missing & ~np.isnan(x)
            fillable = missing & ~np.isnan(x)
            if int(overlap.sum()) < min_fit_weeks or not fillable.any():
                continue
            fit = ols(own[overlap], x[overlap])
            if fit is None:
                continue
            alpha, beta, r2, resid = fit
            if best is None or r2 > best[0]:
                best = (r2, cand, alpha, beta, resid, overlap)
        if best is None:
            statuses[key] = ColumnStatus(
                key, "excluded", real, 0, None, "no proxy with enough overlapping history"
            )
            continue
        r2, cand, alpha, beta, resid, overlap = best
        x = matrix.values[:, matrix.keys.index(cand)]
        fillable = missing & ~np.isnan(x)
        rng = np.random.default_rng([SEED, zlib.crc32(key.encode())])
        centred = resid - resid.mean()
        values[fillable, j] = beta * x[fillable] + rng.choice(centred, size=int(fillable.sum()))
        idx = np.flatnonzero(overlap)
        filled = int(fillable.sum())
        left = int(np.isnan(values[:, j]).sum())
        statuses[key] = ColumnStatus(
            key,
            "proxied" if left == 0 else "excluded",
            real,
            filled,
            ProxyFit(
                cand,
                beta,
                alpha,
                r2,
                int(overlap.sum()),
                matrix.dates[int(idx[0])],
                matrix.dates[int(idx[-1])],
                float(centred.std(ddof=1)) if centred.size > 1 else 0.0,
                filled,
            ),
            None if left == 0 else f"{left} weeks are still missing after the proxy backfill",
        )
    return (
        ReturnMatrix(
            matrix.frequency,
            matrix.dates,
            matrix.keys,
            values,
            coverage_of(matrix.keys, matrix.dates, values),
        ),
        statuses,
    )
