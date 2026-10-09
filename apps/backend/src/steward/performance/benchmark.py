"""Benchmark comparison (plan M4.5): a benchmark's return over the same window as the portfolio's,
blended benchmarks with a stated rebalance rule, and the active return with its missing share.

Pure: no I/O. The comparison is only drawn on a matched footing:
- currency: a benchmark quoted in another currency than the portfolio's base gives no active
  return (both returns stay visible), because a currency effect would be mixed into it;
- dividend treatment: a price index leaves dividends out, so against a total-return portfolio the
  active return is flagged as understated; an unknown basis is flagged the same way;
- coverage: the benchmark must have a close on or just before both endpoints, and the share of
  portfolio days with no benchmark close is reported.
"""

from __future__ import annotations

import bisect
import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from steward.analytics.contract import AnalyticsResult, Coverage, Status, result, unavailable
from steward.analytics.versions import version
from steward.performance.windows import WindowReturn

ZERO = Decimal(0)
ONE = Decimal(1)
Rebalance = Literal["none", "monthly", "quarterly", "annual"]
Basis = Literal["total", "price", "unknown"]
MAX_ENDPOINT_GAP_DAYS = 5
# Shared by the settings validator and `blended_index`, so a blend the settings accept always
# builds.
WEIGHT_TOLERANCE = Decimal("1e-6")
Series = Sequence[tuple[dt.date, Decimal]]


def _close_on_or_before(series: Series, day: dt.date) -> tuple[dt.date, Decimal] | None:
    dates = [d for d, _ in series]
    i = bisect.bisect_right(dates, day) - 1
    if i < 0 or (day - dates[i]).days > MAX_ENDPOINT_GAP_DAYS:
        return None
    return series[i]


def series_return(series: Series, start: dt.date, end: dt.date) -> Decimal | None:
    """`close(end) / close(start) - 1` on closes at or just before each endpoint."""
    ordered = sorted(series)
    first = _close_on_or_before(ordered, start)
    last = _close_on_or_before(ordered, end)
    if first is None or last is None or first[1] <= 0 or last[0] <= first[0]:
        return None
    return last[1] / first[1] - ONE


def _period_key(day: dt.date, rule: Rebalance) -> tuple[int, ...]:
    if rule == "monthly":
        return (day.year, day.month)
    if rule == "quarterly":
        return (day.year, (day.month - 1) // 3)
    if rule == "annual":
        return (day.year,)
    return (0,)


def blended_index(
    series_by_symbol: Mapping[str, Series],
    weights: Mapping[str, Decimal],
    rebalance: Rebalance = "none",
) -> list[tuple[dt.date, Decimal]]:
    """A blend indexed to 100 on the first date every component has a close.

    Component values drift with their returns between rebalances and reset to the target weights
    on the first day of each month, quarter or year (`none`: set once at the start, never reset).
    Weights must be positive and sum to one; a component with no close on a date carries its last
    close, so a holiday in one market does not break the blend.
    """
    if not series_by_symbol or set(series_by_symbol) != set(weights):
        raise ValueError("every benchmark component needs a weight and a series")
    total = sum(weights.values(), ZERO)
    if any(w <= 0 for w in weights.values()) or abs(total - ONE) > WEIGHT_TOLERANCE:
        raise ValueError("benchmark weights must be positive and sum to 1")
    weights = {s: w / total for s, w in weights.items()}
    maps = {s: dict(sorted(v)) for s, v in series_by_symbol.items()}
    start = max(min(m) for m in maps.values())
    days = sorted({d for m in maps.values() for d in m if d >= start})
    last: dict[str, Decimal] = {}
    for symbol, m in maps.items():
        eligible = [d for d in m if d <= start]
        last[symbol] = m[max(eligible)]
    value = Decimal(100)
    holdings = {s: value * weights[s] for s in weights}
    out = [(days[0], value)]
    previous_close = dict(last)
    previous_key = _period_key(days[0], rebalance)
    for day in days[1:]:
        for symbol, m in maps.items():
            close = m.get(day, previous_close[symbol])
            if previous_close[symbol] > 0:
                holdings[symbol] *= close / previous_close[symbol]
            previous_close[symbol] = close
        value = sum(holdings.values(), ZERO)
        out.append((day, value))
        key = _period_key(day, rebalance)
        if rebalance != "none" and key != previous_key:
            holdings = {s: value * weights[s] for s in weights}
        previous_key = key
    return out


@dataclass(frozen=True)
class BenchmarkComparison:
    benchmark: str
    start: dt.date | None
    end: dt.date | None
    portfolio_return: Decimal | None
    benchmark_return: Decimal | None
    active_return: AnalyticsResult
    missing_share: Decimal | None
    basis: Basis
    currency: str | None
    portfolio_currency: str | None


def compare_window(
    window: WindowReturn,
    benchmark: str,
    series: Series,
    *,
    benchmark_currency: str | None,
    portfolio_currency: str | None,
    basis: Basis = "unknown",
    portfolio_days: Sequence[dt.date] = (),
) -> BenchmarkComparison:
    """Active return = portfolio TWR - benchmark return over the identical dates."""
    method = version("twr")
    portfolio_return = window.twr.value
    if window.start is None or window.end is None:
        reason = window.reason or "the portfolio window is unavailable"
        return BenchmarkComparison(
            benchmark, None, None, portfolio_return, None,
            unavailable("percentage_points", method, reason), None, basis,
            benchmark_currency, portfolio_currency,
        )  # fmt: skip
    ordered = sorted(series)
    benchmark_return = series_return(ordered, window.start, window.end)
    missing: Decimal | None = None
    if portfolio_days:
        have = {d for d, _ in ordered}
        in_window = [d for d in portfolio_days if window.start < d <= window.end]
        if in_window:
            missing = Decimal(sum(1 for d in in_window if d not in have)) / Decimal(len(in_window))
    warnings: list[str] = []
    if benchmark_return is None:
        active = unavailable(
            "percentage_points", method,
            f"{benchmark} has no close within {MAX_ENDPOINT_GAP_DAYS} days of "
            f"{window.start.isoformat()} and {window.end.isoformat()}",
            as_of=window.end,
        )  # fmt: skip
    elif portfolio_return is None:
        active = unavailable(
            "percentage_points", method, "no complete portfolio return in this window",
            as_of=window.end,
        )  # fmt: skip
    elif benchmark_currency is None or portfolio_currency is None:
        active = unavailable(
            "percentage_points", method,
            "benchmark or portfolio currency is unknown: no like-for-like active return",
            as_of=window.end,
        )  # fmt: skip
    elif benchmark_currency != portfolio_currency:
        active = unavailable(
            "percentage_points", method,
            f"benchmark is in {benchmark_currency}, portfolio in {portfolio_currency}: "
            "a currency effect would be mixed in, so no active return is drawn",
            as_of=window.end,
        )  # fmt: skip
    else:
        if basis != "total":
            warnings.append(
                f"benchmark basis is {basis}: dividends are not in the benchmark, so the active "
                "return is overstated by roughly the index's dividend yield"
            )
        if missing:
            warnings.append(f"{missing:.1%} of portfolio days have no benchmark close")
        active = result(
            (portfolio_return - benchmark_return) * 100,
            "percentage_points",
            method,
            as_of=window.end,
            coverage=Coverage(
                numerator=ONE - (missing or ZERO), denominator=ONE, basis="count"
            ),
            warnings=warnings,
            status=Status.PARTIAL if warnings else Status.COMPLETE,
            inputs={"portfolio": portfolio_return, "benchmark": benchmark_return,
                    "start": window.start, "end": window.end, "symbol": benchmark},
        )  # fmt: skip
    return BenchmarkComparison(
        benchmark, window.start, window.end, portfolio_return, benchmark_return, active,
        missing, basis, benchmark_currency, portfolio_currency,
    )  # fmt: skip
