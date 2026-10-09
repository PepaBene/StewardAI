"""Return calculators (methodology.md sections 2-5): price and total return, CAGR, TWR, XIRR
and the attribution bridge. Pure `Decimal` code with no I/O; inputs come from stored, dated
observations. Each function returns an `AnalyticsResult`, with a reason when it cannot answer.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from pydantic import Field

from steward.analytics.contract import (
    AnalyticsResult,
    Coverage,
    Status,
    result,
    rounded,
    unavailable,
)
from steward.analytics.versions import version

ONE = Decimal(1)
ZERO = Decimal(0)
DAYS_PER_YEAR = Decimal(365)

# Quote units that are a fraction of the major currency (price / divisor = major currency).
MINOR_UNITS: dict[str, tuple[str, Decimal]] = {
    "GBX": ("GBP", Decimal(100)),
    "GBp": ("GBP", Decimal(100)),
    "ZAc": ("ZAR", Decimal(100)),
    "ILA": ("ILS", Decimal(100)),
}


def to_major_unit(price: Decimal, currency: str) -> tuple[Decimal, str]:
    """GBX 1234.5 -> (GBP 12.345). A major currency passes through unchanged."""
    if currency in MINOR_UNITS:
        major, divisor = MINOR_UNITS[currency]
        return price / divisor, major
    return price, currency


@dataclass(frozen=True)
class Split:
    ex_date: dt.date
    ratio: Decimal  # new shares per old share: 2 for a 2-for-1, 0.1 for a 1-for-10 reverse split


@dataclass(frozen=True)
class Dividend:
    ex_date: dt.date
    amount: Decimal  # per share, in the quote currency of the price series, as announced


def split_factor_after(day: dt.date, splits: Sequence[Split]) -> Decimal:
    """Product of the ratios of every split that takes effect after `day`: a price recorded on
    `day` divided by this factor is on the share basis of the latest date."""
    factor = ONE
    for split in splits:
        if split.ex_date > day:
            factor *= split.ratio
    return factor


def price_return(
    start: tuple[dt.date, Decimal],
    end: tuple[dt.date, Decimal],
    *,
    currency: str,
    end_currency: str | None = None,
    splits: Sequence[Split] = (),
    unresolved_split: bool = False,
) -> AnalyticsResult:
    """`(end / start) - 1` on one listing, one quote unit, one adjustment basis.

    Raw closes are put on the end-date share basis with the known splits first. A split the
    source flags but cannot give a ratio for (`unresolved_split`) blocks an exact return.
    """
    method = version("price_return")
    inputs = {"start": start, "end": end, "currency": currency, "splits": splits}
    if (end_currency or currency) != currency:
        return unavailable(
            "fraction", method, "endpoints are in different quote units", inputs=inputs
        )
    if unresolved_split:
        return unavailable(
            "fraction", method, "a split in the window has no ratio; no exact return", inputs=inputs
        )
    if start[0] >= end[0]:
        return unavailable("fraction", method, "start must be before end", inputs=inputs)
    if start[1] <= 0 or end[1] <= 0:
        return unavailable("fraction", method, "non-positive price", inputs=inputs)
    adjusted_start = start[1] / split_factor_after(start[0], splits)
    adjusted_end = end[1] / split_factor_after(end[0], splits)
    return result(
        adjusted_end / adjusted_start - ONE,
        "fraction",
        method,
        as_of=end[0],
        inputs=inputs,
    )


def total_return(
    closes: Sequence[tuple[dt.date, Decimal]],
    dividends: Sequence[Dividend],
    splits: Sequence[Split] = (),
    *,
    distributions_complete: bool,
) -> AnalyticsResult:
    """Chain daily factors `(close_t + dividend_t) / close_{t-1}` with each dividend reinvested
    at the close of its ex-date (convention `reinvest_at_ex_close`).

    Closes and dividends are first put on the final share basis (split factors). When the
    distribution history is incomplete no total return is published: the price return is
    returned instead, flagged, so the two are never confused.
    """
    method = version("total_return")
    inputs = {"closes": list(closes), "dividends": list(dividends), "splits": list(splits)}
    series = sorted(closes)
    if len(series) < 2:
        return unavailable("fraction", method, "fewer than two closes", inputs=inputs)
    if any(price <= 0 for _, price in series):
        return unavailable("fraction", method, "non-positive close", inputs=inputs)
    by_day = {d: p / split_factor_after(d, splits) for d, p in series}
    days = [d for d, _ in series]
    chain = ONE
    paid: dict[dt.date, Decimal] = {}
    for dividend in dividends:
        if days[0] < dividend.ex_date <= days[-1]:
            paid[dividend.ex_date] = paid.get(dividend.ex_date, ZERO) + (
                dividend.amount / split_factor_after(dividend.ex_date, splits)
            )
    for previous, current in pairwise(days):
        # A dividend whose ex-date falls on a non-trading day is paid on the next close.
        due = sum((a for d, a in paid.items() if previous < d <= current), ZERO)
        chain *= (by_day[current] + due) / by_day[previous]
    value = chain - ONE
    if not distributions_complete:
        price_only = by_day[days[-1]] / by_day[days[0]] - ONE
        return result(
            None,
            "fraction",
            method,
            as_of=days[-1],
            warnings=[
                "distribution history is incomplete; total return is not published "
                f"(price return over the same window: {price_only:.6f})"
            ],
            inputs=inputs,
            status=Status.PARTIAL,
        )
    return result(value, "fraction", method, as_of=days[-1], inputs=inputs)


def cagr(start_value: Decimal, end_value: Decimal, years: Decimal) -> AnalyticsResult:
    """`(end / start) ** (1 / years) - 1`; undefined for a non-positive endpoint or period."""
    method = version("cagr")
    inputs = {"start": start_value, "end": end_value, "years": years}
    if years <= 0:
        return unavailable("fraction", method, "period must be positive", inputs=inputs)
    if start_value <= 0 or end_value <= 0:
        return unavailable(
            "fraction", method, "n/m: CAGR needs positive start and end values", inputs=inputs
        )
    return result(
        (end_value / start_value) ** (ONE / years) - ONE, "fraction", method, inputs=inputs
    )


@dataclass(frozen=True)
class DayValue:
    """End-of-day portfolio value including that day's external flow, and the flow itself
    (positive deposit, negative withdrawal), in one base currency."""

    date: dt.date
    value: Decimal
    flow: Decimal = ZERO


def twr(
    days: Sequence[DayValue],
    *,
    material_missing_weight: Decimal = ZERO,
    max_missing_weight: Decimal = ZERO,
) -> AnalyticsResult:
    """Chain `r_i = (V_i - F_i) / V_{i-1} - 1` over daily subperiods: product(1 + r_i) - 1.

    Convention `end_of_day_flow`: a flow lands at the close of its day, so that day's return is
    measured on the value net of the flow. Dividends, fees and trades are internal, never flows.
    A subperiod whose opening value is not positive has no base to measure on and is skipped,
    counted in the coverage; a complete figure also needs `material_missing_weight` (the share
    of portfolio value with a missing price or flow) within `max_missing_weight`.
    """
    method = version("twr")
    inputs = {"days": list(days), "missing": material_missing_weight}
    ordered = sorted(days, key=lambda d: d.date)
    if len(ordered) < 2:
        return unavailable("fraction", method, "fewer than two valuation days", inputs=inputs)
    chain = ONE
    used = 0
    skipped: list[dt.date] = []
    for previous, current in pairwise(ordered):
        if previous.value <= 0:
            skipped.append(current.date)
            continue
        chain *= (current.value - current.flow) / previous.value
        used += 1
    total = len(ordered) - 1
    if used == 0:
        return unavailable(
            "fraction", method, "no subperiod has a positive opening value", inputs=inputs
        )
    warnings: list[str] = []
    if skipped:
        warnings.append(f"{len(skipped)} subperiod(s) skipped: opening value not positive")
    if material_missing_weight > max_missing_weight:
        return result(
            None,
            "fraction",
            method,
            as_of=ordered[-1].date,
            coverage=Coverage(numerator=Decimal(used), denominator=Decimal(total)),
            warnings=[
                *warnings,
                f"{material_missing_weight:.2%} of portfolio value has a missing price or flow; "
                "no complete return is published",
            ],
            inputs=inputs,
            status=Status.PARTIAL,
        )
    return result(
        chain - ONE,
        "fraction",
        method,
        as_of=ordered[-1].date,
        coverage=Coverage(numerator=Decimal(used), denominator=Decimal(total)),
        warnings=warnings,
        inputs=inputs,
    )


XIRR_LOWER = Decimal("-0.9999")
XIRR_UPPER = Decimal(100)
XIRR_TOLERANCE = Decimal("1e-12")


def _npv(rate: Decimal, flows: Sequence[tuple[dt.date, Decimal]]) -> Decimal:
    origin = flows[0][0]
    base = ONE + rate
    return sum(
        (amount / base ** (Decimal((day - origin).days) / DAYS_PER_YEAR) for day, amount in flows),
        ZERO,
    )


def _npv_sign_changes(
    flows: Sequence[tuple[dt.date, Decimal]], low: Decimal, high: Decimal, steps: int = 600
) -> int:
    """Sign changes of NPV on a grid spaced evenly in log(1 + rate); more than one means the
    flows have several rates and bisection would return an arbitrary one."""
    ratio = (ONE + high) / (ONE + low)
    signs: list[bool] = []
    for i in range(steps + 1):
        value = _npv((ONE + low) * ratio ** (Decimal(i) / steps) - ONE, flows)
        if value != 0:
            signs.append(value > 0)
    return sum(1 for a, b in pairwise(signs) if a != b)


def xirr(
    flows: Sequence[tuple[dt.date, Decimal]], *, upper: Decimal = XIRR_UPPER
) -> AnalyticsResult:
    """The annual rate making dated owner flows plus the ending value sum to zero.

    Sign convention: money the owner pays in is negative, money received (withdrawals and the
    ending value) positive. Actual/365 day count, solved by bisection on [-99.99 %, `upper`]
    (+10000 % by default; a caller with a short window passes a wider bound, since a +50 % month
    is a very large annual rate); a flow set with no sign change or no sign change of NPV across
    the bounds has no stable root and returns `unavailable`, never an invented percentage.
    """
    method = version("xirr")
    inputs = {"flows": list(flows), "bounds": (XIRR_LOWER, upper), "day_count": "act/365"}
    ordered = sorted(flows, key=lambda f: f[0])
    if len(ordered) < 2 or not any(a < 0 for _, a in ordered) or not any(a > 0 for _, a in ordered):
        return unavailable(
            "fraction", method, "needs at least one outflow and one inflow", inputs=inputs
        )
    if ordered[0][0] == ordered[-1][0]:
        return unavailable("fraction", method, "all flows on one date", inputs=inputs)
    low, high = XIRR_LOWER, upper
    f_low, f_high = _npv(low, ordered), _npv(high, ordered)
    if f_low == 0:
        root = low
    elif f_high == 0:
        root = high
    elif (f_low > 0) == (f_high > 0):
        return unavailable(
            "fraction", method, "no stable root within the solver bounds", inputs=inputs
        )
    elif _npv_sign_changes(ordered, low, high) > 1:
        return unavailable("fraction", method, "more than one rate fits these flows", inputs=inputs)
    else:
        for _ in range(400):
            mid = (low + high) / 2
            f_mid = _npv(mid, ordered)
            if f_mid == 0 or (high - low) < XIRR_TOLERANCE:
                break
            if (f_mid > 0) == (f_low > 0):
                low, f_low = mid, f_mid
            else:
                high = mid
        root = (low + high) / 2
    return result(root, "fraction", method, as_of=ordered[-1][0], inputs=inputs)


class Bridge(AnalyticsResult):
    """`value` is the total gain; `components` and `residual` reconcile to it exactly."""

    components: dict[str, Decimal] = Field(default_factory=dict)
    residual: Decimal = ZERO


def attribution_bridge(
    start_value: Decimal,
    end_value: Decimal,
    net_flows: Decimal,
    components: dict[str, Decimal],
    *,
    unattributed_value: Decimal = ZERO,
    unattributed_count: int = 0,
) -> Bridge:
    """Reconcile the value change to named effects with a disclosed residual.

    `gain = end - start - net external flows`; `residual = gain - sum(components)` is returned,
    never absorbed, so components + residual equal the gain. `unattributed_value` is the market
    value whose actions or FX were missing: it makes the bridge partial and sets the coverage.
    `unattributed_count` marks it partial too when such a holding has no value to show (it was
    sold before the end).
    """
    method = version("attribution")
    gain = end_value - start_value - net_flows
    residual = gain - sum(components.values(), ZERO)
    inputs = {
        "start": start_value,
        "end": end_value,
        "flows": net_flows,
        "components": components,
        "unattributed": unattributed_value,
    }
    denominator = end_value if end_value > 0 else ONE
    warnings = []
    if unattributed_value:
        warnings.append(f"{unattributed_value / denominator:.2%} of value lacks actions or FX")
    elif unattributed_count:
        warnings.append(
            f"{unattributed_count} holding(s) lacked actions or FX in some interval; their "
            "effect sits in the residual"
        )
    base = result(
        gain,
        "money",
        method,
        coverage=Coverage(
            numerator=denominator - unattributed_value,
            denominator=denominator,
            basis="market_value",
        ),
        warnings=warnings,
        inputs=inputs,
    )
    return Bridge(
        **base.model_dump(),
        components={k: rounded(v, "money") or ZERO for k, v in components.items()},
        residual=rounded(residual, "money") or ZERO,
    )
