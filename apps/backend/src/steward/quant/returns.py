"""Aligned return matrices in the base currency (quant plan Q0.2).

Method:

1. Level: a series' close in its own currency times that day's base-currency rate, so a
   US listing is measured the way a CZK investor experiences it (stock move and currency move
   together).
2. Grid: every weekday (daily matrix) or every Wednesday (weekly matrix). A grid day without a
   close carries the last close forward (an exchange holiday is a zero local return), up to
   `MAX_CARRY_DAYS`; beyond that the series has no level, so a delisted or stalled series is
   not carried on silently. A day without a base-currency rate has no level either.
3. Return: r_t = ln(L_t / L_{t-1}), NaN where either level is missing. Weekly returns run
   Wednesday to Wednesday, which removes the closing-time asynchrony between US and European
   listings that biases daily cross-market correlation downwards.
4. Corporate actions are already in the stored adjusted closes; nothing is adjusted here.

Every column reports its coverage (returns present, first and last return date), so a caller
can tell real history from a short one.
"""

import bisect
import datetime as dt
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

Frequency = Literal["daily", "weekly"]
Closes = Sequence[tuple[dt.date, float]]
# Base-currency units for one unit of `currency` on a day; None without a rate.
FxLookup = Callable[[str, dt.date], float | None]

# A close older than this (calendar days) is not carried forward onto a grid day. Covers the
# longest regular exchange closures (Easter, Golden Week) without hiding a stalled series.
MAX_CARRY_DAYS = 7
WEDNESDAY = 2


@dataclass(frozen=True)
class SeriesInput:
    key: str
    closes: Closes
    currency: str


@dataclass(frozen=True)
class ColumnCoverage:
    key: str
    observations: int  # returns present
    expected: int  # returns in the matrix
    first_return: dt.date | None
    last_return: dt.date | None

    @property
    def share(self) -> float:
        return self.observations / self.expected if self.expected else 0.0


@dataclass(frozen=True)
class ReturnMatrix:
    """Log returns, one row per grid date (the return's end date), one column per series."""

    frequency: Frequency
    dates: list[dt.date]
    keys: list[str]
    values: npt.NDArray[np.float64]  # shape (len(dates), len(keys)); NaN = no return
    coverage: dict[str, ColumnCoverage]

    def column(self, key: str) -> npt.NDArray[np.float64]:
        return self.values[:, self.keys.index(key)]

    def last(self, rows: int) -> "ReturnMatrix":
        """The most recent `rows` returns."""
        return ReturnMatrix(
            self.frequency,
            self.dates[-rows:],
            self.keys,
            self.values[-rows:],
            coverage_of(self.keys, self.dates[-rows:], self.values[-rows:]),
        )


def grid(start: dt.date, end: dt.date, frequency: Frequency) -> list[dt.date]:
    """Weekdays (daily) or Wednesdays (weekly) from `start` to `end`, both inclusive."""
    days = (start + dt.timedelta(days=i) for i in range((end - start).days + 1))
    if frequency == "weekly":
        return [d for d in days if d.weekday() == WEDNESDAY]
    return [d for d in days if d.weekday() < 5]


def levels(
    series: SeriesInput, days: Sequence[dt.date], fx: FxLookup, max_carry: int = MAX_CARRY_DAYS
) -> npt.NDArray[np.float64]:
    """Base-currency level of the series on each grid day (NaN where there is none)."""
    ordered = sorted((d, c) for d, c in series.closes if c > 0)
    dates = [d for d, _ in ordered]
    out = np.full(len(days), np.nan)
    for i, day in enumerate(days):
        j = bisect.bisect_right(dates, day) - 1
        if j < 0 or (day - dates[j]).days > max_carry:
            continue
        rate = fx(series.currency, day)
        if rate is None or rate <= 0:
            continue
        out[i] = ordered[j][1] * rate
    return out


def coverage_of(
    keys: Sequence[str], dates: Sequence[dt.date], values: npt.NDArray[np.float64]
) -> dict[str, ColumnCoverage]:
    out: dict[str, ColumnCoverage] = {}
    for j, key in enumerate(keys):
        present = np.flatnonzero(~np.isnan(values[:, j]))
        out[key] = ColumnCoverage(
            key,
            int(present.size),
            len(dates),
            dates[int(present[0])] if present.size else None,
            dates[int(present[-1])] if present.size else None,
        )
    return out


def build_return_matrix(
    series: Sequence[SeriesInput],
    fx: FxLookup,
    frequency: Frequency,
    start: dt.date,
    end: dt.date,
    max_carry: int = MAX_CARRY_DAYS,
) -> ReturnMatrix:
    """Log returns of every series over start..end on the daily or weekly grid. The first grid
    day only supplies a level, so the matrix holds one fewer row than the grid."""
    days = grid(start, end, frequency)
    keys = [s.key for s in series]
    if len(set(keys)) != len(keys):
        raise ValueError("series keys must be unique")
    if len(days) < 2:
        return ReturnMatrix(
            frequency,
            [],
            keys,
            np.empty((0, len(keys))),
            coverage_of(keys, [], np.empty((0, len(keys)))),
        )
    lv = (
        np.column_stack([levels(s, days, fx, max_carry) for s in series])
        if series
        else np.empty((len(days), 0))
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        values = np.log(lv[1:] / lv[:-1])
    values[~np.isfinite(values)] = np.nan
    return ReturnMatrix(frequency, days[1:], keys, values, coverage_of(keys, days[1:], values))


def pairwise_correlation(
    a: npt.NDArray[np.float64], b: npt.NDArray[np.float64], min_observations: int
) -> tuple[float, int] | None:
    """Pearson correlation over the rows where both have a return, and how many that is;
    None below `min_observations` or when either does not vary."""
    both = ~np.isnan(a) & ~np.isnan(b)
    n = int(both.sum())
    if n < min_observations:
        return None
    x, y = a[both], b[both]
    sx, sy = x.std(ddof=1), y.std(ddof=1)
    if sx == 0 or sy == 0 or math.isnan(sx) or math.isnan(sy):
        return None
    return float(np.corrcoef(x, y)[0, 1]), n
