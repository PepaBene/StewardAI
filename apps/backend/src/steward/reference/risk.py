"""Risk figures computed in code from stored daily closes (never taken from a third party).

- Returns over a period: the last close over the close on or before the period's start.
- Volatility: the sample standard deviation of daily log returns, annualised with sqrt(252),
  over the last `window` trading days.
- Maximum drawdown: the worst fall from a running peak over all stored closes.
- Beta: cov(holding, benchmark) / var(benchmark) of simple returns between consecutive days
  both traded, over the last `window` such days.
- Correlation: Pearson correlation of the same paired returns.
"""

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

TRADING_DAYS = 252
PERIODS: dict[str, int] = {"1m": 30, "3m": 91, "6m": 182, "1y": 365, "2y": 730}

Series = Sequence[tuple[dt.date, float]]


@dataclass(frozen=True)
class RiskFigures:
    returns: dict[str, float | None]
    volatility: float | None
    max_drawdown: float | None
    beta: float | None
    observations: int


def period_return(series: Series, days: int) -> float | None:
    if len(series) < 2:
        return None
    end_date, end_close = series[-1]
    start = end_date - dt.timedelta(days=days)
    base = [c for d, c in series if d <= start]
    if not base:
        return None
    return end_close / base[-1] - 1


def daily_returns(series: Series) -> dict[dt.date, float]:
    return {series[i][0]: series[i][1] / series[i - 1][1] - 1 for i in range(1, len(series))}


def volatility(series: Series, window: int = TRADING_DAYS) -> float | None:
    tail = list(series)[-(window + 1) :]
    logs = [math.log(tail[i][1] / tail[i - 1][1]) for i in range(1, len(tail))]
    if len(logs) < 20:
        return None
    mean = sum(logs) / len(logs)
    var = sum((x - mean) ** 2 for x in logs) / (len(logs) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def max_drawdown(series: Series) -> float | None:
    if len(series) < 2:
        return None
    peak = series[0][1]
    worst = 0.0
    for _, close in series:
        peak = max(peak, close)
        worst = min(worst, close / peak - 1)
    return worst


def _paired(a: Series, b: Series, window: int) -> tuple[list[float], list[float]]:
    # Align the closes on the days both traded first, so each pair of returns spans the
    # same interval (a day missing from one series would otherwise pair a two-day return
    # with a one-day return).
    ca, cb = dict(a), dict(b)
    days = sorted(set(ca) & set(cb))
    ra = daily_returns([(d, ca[d]) for d in days])
    rb = daily_returns([(d, cb[d]) for d in days])
    shared = sorted(ra)[-window:]
    return [ra[d] for d in shared], [rb[d] for d in shared]


def _cov(x: list[float], y: list[float]) -> float:
    mx, my = sum(x) / len(x), sum(y) / len(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (len(x) - 1)


def beta(series: Series, benchmark: Series, window: int = TRADING_DAYS) -> float | None:
    x, y = _paired(series, benchmark, window)
    if len(x) < 20:
        return None
    var = _cov(y, y)
    return _cov(x, y) / var if var > 0 else None


def paired_correlation(
    a: Series, b: Series, window: int = TRADING_DAYS, min_shared: int = 20
) -> tuple[float | None, int]:
    """Pearson correlation of the paired daily simple returns and the number of pairs used.
    The correlation is None (never 0) below `min_shared` pairs or when either side is flat."""
    x, y = _paired(a, b, window)
    if len(x) < min_shared or len(x) < 2:
        return None, len(x)
    vx, vy = _cov(x, x), _cov(y, y)
    if vx > 0 and vy > 0:
        return _cov(x, y) / math.sqrt(vx * vy), len(x)
    return None, len(x)


def correlation(a: Series, b: Series, window: int = TRADING_DAYS) -> float | None:
    return paired_correlation(a, b, window)[0]


def figures(series: Series, benchmark: Series | None) -> RiskFigures:
    return RiskFigures(
        returns={name: period_return(series, days) for name, days in PERIODS.items()},
        volatility=volatility(series),
        max_drawdown=max_drawdown(series),
        beta=beta(series, benchmark) if benchmark else None,
        observations=len(series),
    )
