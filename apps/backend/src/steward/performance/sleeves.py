"""Satellites versus core (plan M4.9, strategy principle 11a): the individual stocks against the
core ETFs, each as a time-weighted return after fees and FX costs, over a rolling window.

Pure: no I/O. A sleeve holds positions only (cash belongs to neither). Between two valuation
days the sleeve's return is

    r = (V1 - flow) / V0 - 1
    flow = net buys at fill value - dividends received + fill fees

so buying or selling inside the portfolio is a flow into or out of the sleeve (never a gain), a
dividend counts as return, and a fee, FX conversion cost included, counts against it. The
invested portfolio's interval return is exactly the sleeve-weighted sum of the two sleeve returns
(`reconciliation_error` proves it on the data it was given).

"Too early to judge" below 12 months of history: the gap is still returned, with the sample
length, but never advertised as a finding and never raises a review item.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from steward.analytics.returns import DayValue, twr
from steward.performance.attribution import Amount, PositionDay, Trade
from steward.performance.windows import annualize

ZERO = Decimal(0)
ONE = Decimal(1)
MIN_HISTORY_DAYS = 365
DEFAULT_LOOKBACK_YEARS = 2
DEFAULT_GAP_POINTS = Decimal(2)


def sleeve_days(
    tickers: Collection[str],
    valuation_days: Sequence[dt.date],
    positions: Iterable[PositionDay],
    trades: Iterable[Trade],
    income: Iterable[Amount],
    fees: Iterable[Amount],
    fx_for_trades: dict[tuple[dt.date, str], Decimal],
) -> list[DayValue]:
    """Daily value and flow of the sleeve made of `tickers`, in the base currency.

    `fx_for_trades` gives the base-currency rate for a fill's (day, ticker): the end-of-interval
    snapshot rate, the same convention the attribution uses.
    """
    value: dict[dt.date, Decimal] = defaultdict(Decimal)
    for p in positions:
        if p.ticker in tickers:
            value[p.day] += p.value_base
    days = sorted(valuation_days)
    flow: dict[dt.date, Decimal] = defaultdict(Decimal)
    for t in trades:
        if t.ticker in tickers:
            nearest = next((d for d in days if d >= t.day), None)
            rate = fx_for_trades.get((t.day, t.ticker))
            if nearest is not None and rate is not None:
                flow[nearest] += t.quantity * t.price * rate
    for amount in income:
        if amount.ticker in tickers:
            nearest = next((d for d in days if d >= amount.day), None)
            if nearest is not None:
                flow[nearest] -= amount.value
    for amount in fees:
        if amount.ticker in tickers:
            nearest = next((d for d in days if d >= amount.day), None)
            if nearest is not None:
                flow[nearest] += amount.value
    return [DayValue(d, value.get(d, ZERO), flow.get(d, ZERO)) for d in days]


@dataclass(frozen=True)
class SleeveReturn:
    name: str
    start: dt.date | None
    end: dt.date | None
    days: int
    total_return: Decimal | None
    annualized: Decimal | None
    reason: str | None


def sleeve_return(
    name: str, series: Sequence[DayValue], *, lookback_years: int = DEFAULT_LOOKBACK_YEARS
) -> SleeveReturn:
    ordered = sorted(series, key=lambda d: d.date)
    if len(ordered) < 2:
        return SleeveReturn(name, None, None, 0, None, None, "fewer than two valuation days")
    end = ordered[-1].date
    try:
        wanted = end.replace(year=end.year - lookback_years)
    except ValueError:
        wanted = end.replace(year=end.year - lookback_years, day=28)
    before = [d for d in ordered if d.date <= wanted]
    window = ([before[-1]] if before else [ordered[0]]) + [
        d for d in ordered if d.date > (before[-1].date if before else ordered[0].date)
    ]
    series_used = [DayValue(window[0].date, window[0].value, ZERO), *window[1:]]
    outcome = twr(series_used)
    span = (series_used[-1].date - series_used[0].date).days
    if outcome.value is None:
        return SleeveReturn(
            name, series_used[0].date, end, span, None, None,
            outcome.warnings[0] if outcome.warnings else "no return",
        )  # fmt: skip
    return SleeveReturn(
        name, series_used[0].date, end, span, outcome.value, annualize(outcome.value, span), None
    )


@dataclass(frozen=True)
class SleeveComparison:
    core: SleeveReturn
    stocks: SleeveReturn
    history_days: int
    annual_gap_points: Decimal | None  # core minus stocks, percentage points a year
    too_early: bool
    review_item: bool
    message: str
    reconciliation_error: Decimal | None


def reconciliation_error(core: Sequence[DayValue], stocks: Sequence[DayValue]) -> Decimal | None:
    """Largest difference, over the days both sleeves have a positive opening value, between the
    invested portfolio's interval return and the opening-value-weighted sleeve returns."""
    by_core = {d.date: d for d in core}
    by_stock = {d.date: d for d in stocks}
    dates = sorted(set(by_core) & set(by_stock))
    worst: Decimal | None = None
    for a, b in pairwise(dates):
        c0, c1, s0, s1 = by_core[a], by_core[b], by_stock[a], by_stock[b]
        v0 = c0.value + s0.value
        if v0 <= 0 or c0.value <= 0 or s0.value <= 0:
            continue
        combined = (c1.value + s1.value - c1.flow - s1.flow) / v0 - ONE
        weighted = (c0.value / v0) * ((c1.value - c1.flow) / c0.value - ONE) + (s0.value / v0) * (
            (s1.value - s1.flow) / s0.value - ONE
        )
        gap = abs(combined - weighted)
        worst = gap if worst is None or gap > worst else worst
    return worst


def compare_sleeves(
    core: Sequence[DayValue],
    stocks: Sequence[DayValue],
    *,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
    gap_points: Decimal = DEFAULT_GAP_POINTS,
) -> SleeveComparison:
    core_return = sleeve_return("core", core, lookback_years=lookback_years)
    stock_return = sleeve_return("stocks", stocks, lookback_years=lookback_years)
    starts = [r.start for r in (core_return, stock_return) if r.start]
    ends = [r.end for r in (core_return, stock_return) if r.end]
    history = (min(ends) - max(starts)).days if starts and ends else 0
    error = reconciliation_error(core, stocks)
    if core_return.total_return is None or stock_return.total_return is None:
        why = core_return.reason or stock_return.reason or "no return"
        return SleeveComparison(
            core_return, stock_return, history, None, True, False,
            f"No comparison yet: {why}", error,
        )  # fmt: skip
    # Annual figures when the window is long enough, otherwise the same-length total return.
    if core_return.annualized is not None and stock_return.annualized is not None:
        gap = (core_return.annualized - stock_return.annualized) * 100
    else:
        gap = (core_return.total_return - stock_return.total_return) * 100
    too_early = history < MIN_HISTORY_DAYS
    if too_early:
        message = (
            f"Too early to judge: {history} days of history, a fair read needs "
            f"{MIN_HISTORY_DAYS}. Gap so far {gap:+.1f} points."
        )
    elif gap > gap_points:
        message = (
            f"Stocks trail the core ETFs by {gap:.1f} points a year over {history} days, beyond "
            f"the {gap_points} point limit."
        )
    else:
        message = f"Stocks are within {gap_points} points a year of the core ETFs ({gap:+.1f})."
    return SleeveComparison(
        core_return, stock_return, history, gap, too_early,
        (not too_early) and gap > gap_points, message, error,
    )  # fmt: skip
