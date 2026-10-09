"""Portfolio return windows (plan M4.3): time-weighted return and money-weighted return (XIRR)
over 1M, YTD, 1Y, 3Y, 5Y and since inception, on one daily value series.

Pure: no I/O. The two figures answer different questions and are never blended. TWR removes
the effect of the owner's deposits and withdrawals (how the investments did); XIRR is the
owner's own return on the money actually put in, timing included. A window is annualized only
when it spans at least a year, and always says whether it is. A window the stored history does
not reach back to is `unavailable` with the date the history starts, never a shorter period
under the longer label.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from steward.analytics.contract import AnalyticsResult, Status, result, unavailable
from steward.analytics.returns import XIRR_UPPER, DayValue, twr, xirr
from steward.analytics.versions import version

ZERO = Decimal(0)
ONE = Decimal(1)
WindowLabel = Literal["1M", "YTD", "1Y", "3Y", "5Y", "inception"]
WINDOW_LABELS: tuple[WindowLabel, ...] = ("1M", "YTD", "1Y", "3Y", "5Y", "inception")
# An opening observation older than this before the window start is too stale to open it.
MAX_OPENING_GAP_DAYS = 7
CAP = Decimal("1e60")  # largest annual rate the short-window solver is asked to bracket


def window_start(label: WindowLabel, end: dt.date) -> dt.date | None:
    if label == "inception":
        return None
    if label == "YTD":
        return dt.date(end.year - 1, 12, 31)
    months = {"1M": 1, "1Y": 12, "3Y": 36, "5Y": 60}[label]
    year, month = divmod(end.year * 12 + end.month - 1 - months, 12)
    month += 1
    last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30,
            31, 31, 30, 31, 30, 31][month - 1]  # fmt: skip
    return dt.date(year, month, min(end.day, last))


def annualize(total_return: Decimal, days: int) -> Decimal | None:
    """`(1 + r) ** (365 / days) - 1` for a window of at least a year; None below that, and
    for a total loss (no real root)."""
    if days < 365 or total_return <= -ONE:
        return None
    return (ONE + total_return) ** (Decimal(365) / Decimal(days)) - ONE


@dataclass(frozen=True)
class WindowReturn:
    label: WindowLabel
    start: dt.date | None
    end: dt.date | None
    calendar_days: int | None
    observations: int
    start_value: Decimal | None
    end_value: Decimal | None
    net_flows: Decimal
    gain: Decimal | None
    twr: AnalyticsResult
    twr_annualized: Decimal | None
    xirr: AnalyticsResult
    reason: str | None
    xirr_annualized: bool = True  # False: `xirr` is the period return of a window under a year
    # Days between the window's nominal start and the stored day it opens on (0 = exact).
    opening_gap_days: int = 0


def _unavailable(label: WindowLabel, reason: str, end: dt.date | None) -> WindowReturn:
    twr_result = unavailable("fraction", version("twr"), reason, as_of=end)
    xirr_result = unavailable("fraction", version("xirr"), reason, as_of=end)
    return WindowReturn(
        label, None, end, None, 0, None, None, ZERO, None, twr_result, None, xirr_result, reason
    )  # fmt: skip


def window_return(
    days: Sequence[DayValue],
    label: WindowLabel,
    *,
    missing_weight: Decimal = ZERO,
    max_missing_weight: Decimal = ZERO,
) -> WindowReturn:
    ordered = sorted(days, key=lambda d: d.date)
    if len(ordered) < 2:
        return _unavailable(label, "fewer than two valuation days stored", None)
    end = ordered[-1].date
    wanted = window_start(label, end)
    first = ordered[0].date
    opening_gap = 0
    if wanted is None:
        opening = ordered[0]
    else:
        before = [d for d in ordered if d.date <= wanted]
        if not before:
            return _unavailable(
                label, f"stored history starts {first.isoformat()}, after the window start "
                f"{wanted.isoformat()}", end,
            )  # fmt: skip
        opening = before[-1]
        opening_gap = (wanted - opening.date).days
        if opening_gap > MAX_OPENING_GAP_DAYS:
            return _unavailable(
                label, f"no value stored within {MAX_OPENING_GAP_DAYS} days before "
                f"{wanted.isoformat()} (last {opening.date.isoformat()})", end,
            )  # fmt: skip
    window = [opening] + [d for d in ordered if d.date > opening.date]
    if len(window) < 2:
        return _unavailable(label, "no valuation day after the window opens", end)
    # The opening day's own flow is already inside its value.
    series = [DayValue(opening.date, opening.value, ZERO), *window[1:]]
    flows = sum((d.flow for d in series[1:]), ZERO)
    twr_result = twr(
        series, material_missing_weight=missing_weight, max_missing_weight=max_missing_weight
    )
    span = (series[-1].date - series[0].date).days
    cash_flows = [(opening.date, -opening.value)]
    cash_flows += [(d.date, -d.flow) for d in series[1:] if d.flow != 0]
    cash_flows.append((series[-1].date, series[-1].value))
    annual_xirr = span >= 365
    # The solver finds an annual rate; a short window can legitimately sit far above the default
    # bound (+50 % in a month is about +13,000 % a year), so widen it to a +1000 % period return.
    upper = XIRR_UPPER if annual_xirr else min(Decimal(11) ** (Decimal(365) / max(span, 1)), CAP)
    xirr_result = xirr(cash_flows, upper=upper)
    if not annual_xirr and xirr_result.value is not None:
        # Below a year the annual rate is a projection, so report the money-weighted return over
        # the window itself, and say so.
        period = (ONE + xirr_result.value) ** (Decimal(span) / 365) - ONE
        xirr_result = result(
            period, "fraction", version("xirr"), as_of=end, status=Status.COMPLETE,
            warnings=["period return over the window, not annualized (the window is under a year)"],
            inputs={"annual_rate": xirr_result.value, "days": span},
        )  # fmt: skip
    if missing_weight > max_missing_weight:
        xirr_result = result(
            None, "fraction", version("xirr"), as_of=end, status=Status.PARTIAL,
            warnings=[f"{missing_weight:.2%} of portfolio value is missing a price or flow"],
        )  # fmt: skip
    annual = (
        annualize(twr_result.value, span)
        if twr_result.value is not None and twr_result.status is not Status.UNAVAILABLE
        else None
    )
    return WindowReturn(
        label,
        series[0].date,
        series[-1].date,
        span,
        len(series),
        series[0].value,
        series[-1].value,
        flows,
        series[-1].value - series[0].value - flows,
        twr_result,
        annual,
        xirr_result,
        None,
        annual_xirr,
        opening_gap,
    )


def performance_windows(
    days: Sequence[DayValue],
    *,
    missing_weight: Decimal = ZERO,
    max_missing_weight: Decimal = ZERO,
    labels: Sequence[WindowLabel] = WINDOW_LABELS,
) -> list[WindowReturn]:
    return [
        window_return(
            days, label, missing_weight=missing_weight, max_missing_weight=max_missing_weight
        )
        for label in labels
    ]
