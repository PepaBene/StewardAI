"""The ratio library: one definition per ratio, shared by screens, peer compare and the ratios
page (plan M2.15 extends it; M1.8 reads it). A ratio whose denominator is zero, negative or
meaningless is `n/m` with the reason, never a number.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from steward.analytics.versions import version

ONE = Decimal(1)
ZERO = Decimal(0)


@dataclass(frozen=True)
class RatioValue:
    value: Decimal | None
    reason: str | None = None  # why the value is missing or not meaningful

    @property
    def known(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class RatioDef:
    id: str
    label: str
    group: str  # value, momentum, quality, red_flag
    formula: str
    period_rule: str
    exclusions: str
    unit: str  # fraction, multiple, years
    higher_is_better: bool | None  # None: a flag, not ranked


def _nm(reason: str) -> RatioValue:
    return RatioValue(None, f"n/m: {reason}")


def _missing(what: str) -> RatioValue:
    return RatioValue(None, f"missing {what}")


def fcf_yield(cfo: Decimal | None, capex: Decimal | None, market_cap: Decimal | None) -> RatioValue:
    if cfo is None or capex is None or market_cap is None:
        return _missing("operating cash flow, capital expenditure or market cap")
    if market_cap <= 0:
        return _nm("non-positive market cap")
    return RatioValue((cfo - abs(capex)) / market_cap)


def earnings_yield(net_income: Decimal | None, market_cap: Decimal | None) -> RatioValue:
    if net_income is None or market_cap is None:
        return _missing("net income or market cap")
    if market_cap <= 0:
        return _nm("non-positive market cap")
    return RatioValue(net_income / market_cap)


def enterprise_value(
    market_cap: Decimal | None, debt: Decimal | None, cash: Decimal | None
) -> Decimal | None:
    if market_cap is None or debt is None or cash is None:
        return None
    return market_cap + debt - cash


def ev_to_ebit(ev: Decimal | None, ebit: Decimal | None) -> RatioValue:
    if ev is None or ebit is None:
        return _missing("enterprise value (market cap, debt, cash) or operating income")
    if ebit <= 0:
        return _nm("non-positive operating income")
    if ev <= 0:
        return _nm("non-positive enterprise value")
    return RatioValue(ev / ebit)


def gross_profit_to_assets(
    gross_profit: Decimal | None, total_assets: Decimal | None
) -> RatioValue:
    if gross_profit is None or total_assets is None:
        return _missing("gross profit or total assets")
    if total_assets <= 0:
        return _nm("non-positive total assets")
    return RatioValue(gross_profit / total_assets)


def accruals_ratio(
    net_income: Decimal | None,
    cfo: Decimal | None,
    assets_begin: Decimal | None,
    assets_end: Decimal | None,
) -> RatioValue:
    """(net income - operating cash flow) / average total assets. High = earnings not in cash."""
    if None in (net_income, cfo, assets_begin, assets_end):
        return _missing("net income, operating cash flow or opening/closing assets")
    assert net_income is not None and cfo is not None
    assert assets_begin is not None and assets_end is not None
    average = (assets_begin + assets_end) / 2
    if average <= 0:
        return _nm("non-positive average assets")
    return RatioValue((net_income - cfo) / average)


def change_over_period(now: Decimal | None, before: Decimal | None, what: str) -> RatioValue:
    """`now / before - 1` for share count or total assets; both must be positive."""
    if now is None or before is None:
        return _missing(f"{what} now or at the start of the window")
    if now <= 0 or before <= 0:
        return _nm(f"non-positive {what}")
    return RatioValue(now / before - ONE)


def annualised_change(total_change: RatioValue, years: int) -> RatioValue:
    if total_change.value is None:
        return total_change
    return RatioValue((ONE + total_change.value) ** (ONE / Decimal(years)) - ONE)


def cash_runway_years(cash: Decimal | None, cfo_trailing: Decimal | None) -> RatioValue:
    """Cash / trailing operating cash burn, for companies burning cash only. A company with
    non-negative operating cash flow has no burn: it is self-funding, not infinite."""
    if cash is None or cfo_trailing is None:
        return _missing("cash or trailing operating cash flow")
    if cfo_trailing >= 0:
        return RatioValue(None, "not burning cash (operating cash flow is not negative)")
    if cash < 0:
        return _nm("negative cash")
    return RatioValue(cash / -cfo_trailing)


def momentum_12_1(
    closes: Sequence[tuple[dt.date, Decimal]], as_of: dt.date, *, max_gap_days: int = 7
) -> RatioValue:
    """Return from 12 months before `as_of` to 1 month before it (skipping the latest month),
    on stored adjusted closes. Each endpoint is the last close on or before its date and must
    lie within `max_gap_days` of it; otherwise the series does not cover the window."""

    def close_on(day: dt.date) -> Decimal | None:
        eligible = [(d, c) for d, c in closes if d <= day]
        if not eligible:
            return None
        d, c = max(eligible, key=lambda x: x[0])
        return c if (day - d).days <= max_gap_days else None

    start = close_on(_months_back(as_of, 12))
    end = close_on(_months_back(as_of, 1))
    if start is None or end is None:
        return _missing("daily closes covering 12 months back to 1 month back")
    if start <= 0 or end <= 0:
        return _nm("non-positive close")
    return RatioValue(end / start - ONE)


def _months_back(day: dt.date, months: int) -> dt.date:
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    last = [
        31,
        29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ][month - 1]
    return dt.date(year, month, min(day.day, last))


_V = version("ratios")
RATIOS: dict[str, RatioDef] = {
    r.id: r
    for r in (
        RatioDef(
            "fcf_yield",
            "Free cash flow yield",
            "value",
            "(cash from operations - |capital expenditure|) / market cap",
            "latest full-year period; market cap at the price date",
            "market cap <= 0",
            "fraction",
            True,
        ),
        RatioDef(
            "earnings_yield",
            "Earnings yield",
            "value",
            "net income / market cap",
            "latest full-year period; market cap at the price date",
            "market cap <= 0",
            "fraction",
            True,
        ),
        RatioDef(
            "ev_ebit",
            "EV / EBIT",
            "value",
            "(market cap + debt - cash) / operating income",
            "latest full-year period",
            "operating income <= 0; debt or cash unknown",
            "multiple",
            False,
        ),
        RatioDef(
            "gross_profit_to_assets",
            "Gross profit / total assets",
            "quality",
            "gross profit / total assets at period end",
            "latest full-year period",
            "total assets <= 0; banks and insurers (no gross profit)",
            "fraction",
            True,
        ),
        RatioDef(
            "momentum_12_1",
            "12-1 month momentum",
            "momentum",
            "adjusted close 1 month ago / adjusted close 12 months ago - 1",
            "daily closes; each endpoint within 7 days of its date",
            "series shorter than 12 months",
            "fraction",
            True,
        ),
        RatioDef(
            "share_change_1y",
            "Share count change, 1 year",
            "red_flag",
            "diluted shares / diluted shares one year earlier - 1",
            "year over year",
            "non-positive shares",
            "fraction",
            None,
        ),
        RatioDef(
            "share_change_3y",
            "Share count change, 3 years",
            "red_flag",
            "diluted shares / diluted shares three years earlier - 1; per year = (1+x)^(1/3)-1",
            "three-year window",
            "non-positive shares",
            "fraction",
            None,
        ),
        RatioDef(
            "asset_growth",
            "Total asset growth",
            "red_flag",
            "total assets / total assets one year earlier - 1",
            "year over year",
            "non-positive assets",
            "fraction",
            None,
        ),
        RatioDef(
            "accruals_ratio",
            "Accruals ratio",
            "red_flag",
            "(net income - operating cash flow) / average total assets",
            "latest full-year period",
            "average assets <= 0",
            "fraction",
            None,
        ),
        RatioDef(
            "cash_runway_years",
            "Cash runway (years)",
            "red_flag",
            "cash / trailing operating cash burn, only when operating cash flow < 0",
            "trailing twelve months",
            "not burning cash: no runway is computed",
            "years",
            None,
        ),
    )
}
RATIO_LIBRARY_VERSION = _V
