"""A covariance model from stored daily closes, for the pre-trade risk checks (plan M6.7).

The closes of every holding are cut to the days all of them traded, simple returns are taken
between consecutive such days, and the sample covariance is annualised with 252 days. Using only
common days keeps the matrix positive semi-definite and every return over the same interval. A
holding with too little history is left out of the model, not guessed; the caller says which.
Returns are in each holding's own currency: no currency effect is modelled.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

TRADING_DAYS = 252
MIN_COMMON_RETURNS = 120
WINDOW_DAYS = 400  # calendar days of closes read

Series = Sequence[tuple[dt.date, float]]


@dataclass(frozen=True)
class CovModel:
    symbols: tuple[str, ...]
    matrix: tuple[tuple[float, ...], ...]  # annualised
    observations: int
    left_out: tuple[str, ...]

    def index(self, symbol: str) -> int | None:
        try:
            return self.symbols.index(symbol)
        except ValueError:
            return None


def build(series_by: Mapping[str, Series]) -> CovModel | None:
    """The model over the holdings with enough shared history, or None when fewer than two."""
    names = sorted(s for s, series in series_by.items() if len(series) > MIN_COMMON_RETURNS)
    left_out = [s for s in series_by if s not in names]
    while len(names) >= 2:
        common = set.intersection(*(set(d for d, _ in series_by[n]) for n in names))
        days = sorted(common)
        if len(days) - 1 >= MIN_COMMON_RETURNS:
            break
        # Drop the shortest history and try again; a single short series should not sink the rest.
        worst = min(names, key=lambda n: len(series_by[n]))
        names.remove(worst)
        left_out.append(worst)
    else:
        return None
    days = days[-(TRADING_DAYS + 1) :]
    rets: list[list[float]] = []
    for n in names:
        close = dict(series_by[n])
        rets.append([close[days[i]] / close[days[i - 1]] - 1 for i in range(1, len(days))])
    count = len(days) - 1
    means = [sum(r) / count for r in rets]
    matrix = tuple(
        tuple(
            sum((a - means[i]) * (b - means[j]) for a, b in zip(rets[i], rets[j], strict=True))
            / (count - 1)
            * TRADING_DAYS
            for j in range(len(names))
        )
        for i in range(len(names))
    )
    return CovModel(tuple(names), matrix, count, tuple(sorted(left_out)))


def _known(model: CovModel, weights: Mapping[str, float]) -> list[tuple[int, float]]:
    """(row in the matrix, weight) for the holdings the model covers."""
    out: list[tuple[int, float]] = []
    for symbol, weight in weights.items():
        i = model.index(symbol)
        if i is not None:
            out.append((i, weight))
    return out


def variance(model: CovModel, weights: Mapping[str, float]) -> float:
    known = _known(model, weights)
    return float(sum(wi * wj * model.matrix[i][j] for i, wi in known for j, wj in known))


def volatility(model: CovModel, weights: Mapping[str, float]) -> float:
    return math.sqrt(max(variance(model, weights), 0.0))


def contribution(model: CovModel, weights: Mapping[str, float], symbol: str) -> float | None:
    """Share of the portfolio variance that `symbol` accounts for (weights sum to the invested
    share; the rest is cash and adds nothing). None when the symbol is not in the model."""
    k = model.index(symbol)
    var = variance(model, weights)
    if k is None or var <= 0:
        return None
    marginal = sum(w * model.matrix[k][j] for j, w in _known(model, weights))
    return float(weights.get(symbol, 0.0) * marginal / var)
