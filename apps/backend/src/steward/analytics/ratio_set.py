"""The company ratio set (plan M2.15): every ratio defined once, over one period's figures.

A ratio whose denominator is zero, negative or meaningless for the industry is `n/m` with the
reason, never a number. Screens (M1.8), the ratios page and peer compare all read this module,
so a ratio has one value everywhere. Reuses the screen's own helpers where they overlap.
"""

# ruff: noqa: E501, RUF001
from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from steward.analytics import ratios as base
from steward.analytics.ratios import RatioValue

ZERO = Decimal(0)
ONE = Decimal(1)
Figures = Mapping[str, Decimal | None]


@dataclass(frozen=True)
class Period:
    """The figures behind one column. `f` is this period (TTM or fiscal year) from the
    standardized statements; `prior` the balance sheet about a year earlier (for averages);
    `back` annual figures n years earlier (for growth)."""

    end: dt.date
    f: Figures
    prior: Figures = field(default_factory=dict)
    back: Mapping[int, Figures] = field(default_factory=dict)
    market_cap: Decimal | None = None
    price_date: dt.date | None = None
    financial: bool = False  # bank or insurer: industry-specific ratios are n/m
    forward_eps: Decimal | None = None
    price: Decimal | None = None
    implied_growth: RatioValue | None = None


@dataclass(frozen=True)
class CompanyRatio:
    id: str
    label: str
    group: str
    unit: str  # fraction, multiple, days, years, money_per_share
    formula: str
    period_rule: str
    exclusions: str
    higher_is_better: bool | None
    inputs: tuple[str, ...]
    fn: Callable[[Period], RatioValue]


def _missing(*what: str) -> RatioValue:
    return RatioValue(None, "missing " + ", ".join(what))


def _nm(reason: str) -> RatioValue:
    return RatioValue(None, f"n/m: {reason}")


def _num(p: Period, key: str) -> Decimal | None:
    return p.f.get(key)


def _avg(p: Period, key: str) -> Decimal | None:
    now, before = p.f.get(key), p.prior.get(key)
    return None if now is None or before is None else (now + before) / 2


def debt_of(f: Figures) -> Decimal | None:
    """Financial debt: the filed total, else the filed parts (current + non-current + short-term
    borrowings + commercial paper). Leases are not included. None unless a long-term part exists."""
    for total in ("long_term_debt_total", "borrowings_total"):
        v = f.get(total)
        if v is not None:
            return v
    longer = next(
        (
            f.get(k)
            for k in ("long_term_debt_noncurrent", "borrowings_noncurrent")
            if f.get(k) is not None
        ),
        None,
    )
    if longer is None:
        return None
    parts = [
        next(
            (
                f.get(k)
                for k in ("long_term_debt_current", "borrowings_current")
                if f.get(k) is not None
            ),
            None,
        ),
        f.get("short_term_borrowings"),
        f.get("commercial_paper"),
    ]
    return longer + sum((p for p in parts if p is not None), ZERO)


def _ratio(
    num: Decimal | None, den: Decimal | None, what: str, *, den_name: str, positive: bool = True
) -> RatioValue:
    if num is None or den is None:
        return _missing(what)
    if positive and den <= 0:
        return _nm(f"non-positive {den_name}")
    if not positive and den == 0:
        return _nm(f"zero {den_name}")
    return RatioValue(num / den)


def _financial(p: Period, what: str) -> RatioValue | None:
    return _nm(f"{what} is not meaningful for banks and insurers") if p.financial else None


def _ebit(p: Period) -> Decimal | None:
    return _num(p, "operating_income")


def _ebitda(p: Period) -> Decimal | None:
    ebit, da = _ebit(p), _num(p, "depreciation_amortization")
    return None if ebit is None or da is None else ebit + abs(da)


def _fcf(p: Period) -> Decimal | None:
    cfo, capex = _num(p, "cash_from_operations"), _num(p, "capex")
    return None if cfo is None or capex is None else cfo - abs(capex)


def _per_share(p: Period, value: Decimal | None) -> Decimal | None:
    sh = _num(p, "weighted_shares_diluted") or _num(p, "weighted_shares_basic")
    return None if value is None or not sh or sh <= 0 else value / sh


def _cagr(
    key: str, years: int, *, derived: Callable[[Figures], Decimal | None] | None = None
) -> Callable[[Period], RatioValue]:
    def fn(p: Period) -> RatioValue:
        get = derived or (lambda f: f.get(key))
        now = get(p.f)
        then_f = p.back.get(years)
        then = get(then_f) if then_f is not None else None
        if now is None or then is None:
            return _missing(f"{key.replace('_', ' ')} now or {years} years earlier")
        if now <= 0 or then <= 0:
            return _nm("growth needs positive start and end values")
        return RatioValue((now / then) ** (ONE / Decimal(years)) - ONE)

    return fn


def _dilution(years: int) -> Callable[[Period], RatioValue]:
    def fn(p: Period) -> RatioValue:
        back = p.back.get(years)
        now = p.f.get("weighted_shares_diluted") or p.f.get("weighted_shares_basic")
        then = (
            (back.get("weighted_shares_diluted") or back.get("weighted_shares_basic"))
            if back
            else None
        )
        if not now or not then:
            return _missing("share count now or earlier")
        return RatioValue((now / then) ** (ONE / Decimal(years)) - ONE)

    return fn


def _nopat_roic(p: Period) -> RatioValue:
    if (v := _financial(p, "ROIC")) is not None:
        return v
    return nopat_roic(
        _ebit(p),
        _num(p, "pretax_income"),
        _num(p, "income_tax"),
        _invested_capital(p.f),
        _invested_capital(p.prior),
    )


def nopat_roic(
    ebit: Decimal | None,
    pretax: Decimal | None,
    tax: Decimal | None,
    ic_now: Decimal | None,
    ic_before: Decimal | None,
) -> RatioValue:
    """The one ROIC definition (ratios page, peer compare and screen): NOPAT at the tax rate
    capped to 0-50% over the average of invested capital now and a year earlier."""
    if ebit is None or pretax is None or tax is None:
        return _missing("operating income, pre-tax income or tax")
    if pretax <= 0:
        return _nm("negative pre-tax income: no meaningful tax rate")
    rate = min(max(tax / pretax, ZERO), Decimal("0.5"))
    if ic_now is None or ic_before is None:
        return _missing("invested capital now or a year earlier")
    avg = (ic_now + ic_before) / 2
    if avg <= 0:
        return _nm("non-positive invested capital")
    return RatioValue(ebit * (ONE - rate) / avg)


def _invested_capital(f: Figures) -> Decimal | None:
    equity, debt, cash = f.get("equity"), debt_of(f), f.get("cash")
    if equity is None or debt is None or cash is None:
        return None
    return equity + (f.get("noncontrolling_interest") or ZERO) + debt - cash


def _altman(p: Period) -> RatioValue:
    """Z = 1.2 WC/TA + 1.4 RE/TA + 3.3 EBIT/TA + 0.6 MVE/TL + 1.0 S/TA (original manufacturing
    model, applied to any non-financial issuer: read it as a distress screen, not a forecast)."""
    if (v := _financial(p, "Altman Z")) is not None:
        return v
    assets, liabilities = _num(p, "total_assets"), _num(p, "total_liabilities")
    current_a, current_l = _num(p, "current_assets"), _num(p, "current_liabilities")
    retained, ebit, sales = (
        _num(p, "retained_earnings"),
        _num(p, "operating_income"),
        _num(p, "revenue"),
    )
    needed = {
        "total assets": assets,
        "total liabilities": liabilities,
        "current assets": current_a,
        "current liabilities": current_l,
        "retained earnings": retained,
        "operating income": ebit,
        "revenue": sales,
        "market cap": p.market_cap,
    }
    gaps = [name for name, value in needed.items() if value is None]
    if gaps:
        return _missing(*gaps)
    assert assets is not None and liabilities is not None and current_a is not None
    assert current_l is not None and retained is not None and ebit is not None
    assert sales is not None and p.market_cap is not None
    if assets <= 0 or liabilities <= 0:
        return _nm("non-positive total assets or liabilities")
    z = (
        Decimal("1.2") * (current_a - current_l) / assets
        + Decimal("1.4") * retained / assets
        + Decimal("3.3") * ebit / assets
        + Decimal("0.6") * p.market_cap / liabilities
        + sales / assets
    )
    return RatioValue(z)


def _days(num_key: str, den_key: str, what: str) -> Callable[[Period], RatioValue]:
    def fn(p: Period) -> RatioValue:
        bal, flow = _avg(p, num_key), _num(p, den_key)
        if bal is None or flow is None:
            return _missing(f"{what} balance (average) or flow")
        if flow <= 0:
            return _nm(f"non-positive {den_key.replace('_', ' ')}")
        return RatioValue(bal / flow * Decimal(365))

    return fn


def _ccc(p: Period) -> RatioValue:
    if (v := _financial(p, "the cash conversion cycle")) is not None:
        return v
    dso, dio, dpo = (
        _days("receivables", "revenue", "receivables")(p),
        _days("inventory", "cost_of_revenue", "inventory")(p),
        _days("payables", "cost_of_revenue", "payables")(p),
    )
    if not (dso.known and dio.known and dpo.known):
        return _missing("receivable, inventory or payable days")
    return RatioValue((dso.value or ZERO) + (dio.value or ZERO) - (dpo.value or ZERO))


def _shareholder_yield(p: Period) -> RatioValue:
    div, buy = _num(p, "dividends_paid"), _num(p, "share_repurchases_paid")
    if p.market_cap is None:
        return _missing("market cap")
    if p.market_cap <= 0:
        return _nm("non-positive market cap")
    if div is None or buy is None:
        return _missing("dividends and buybacks paid")
    return RatioValue((abs(div) + abs(buy)) / p.market_cap)


def _ev(p: Period) -> Decimal | None:
    return base.enterprise_value(p.market_cap, debt_of(p.f), _num(p, "cash"))


def _ev_ratio(num: Callable[[Period], Decimal | None], what: str) -> Callable[[Period], RatioValue]:
    def fn(p: Period) -> RatioValue:
        if (v := _financial(p, "enterprise value")) is not None:
            return v
        ev, den = _ev(p), num(p)
        if ev is None or den is None:
            return _missing(f"enterprise value (market cap, debt, cash) or {what}")
        if den <= 0:
            return _nm(f"non-positive {what}")
        if ev <= 0:
            return _nm("non-positive enterprise value")
        return RatioValue(ev / den)

    return fn


def _pe(p: Period) -> RatioValue:
    return _ratio(
        p.market_cap, _num(p, "net_income"), "market cap or net income", den_name="net income"
    )


def _peg(p: Period) -> RatioValue:
    pe, growth = _pe(p), _cagr("eps_diluted", 3)(p)
    if not pe.known or not growth.known:
        return RatioValue(None, pe.reason or growth.reason)
    assert pe.value is not None and growth.value is not None
    if growth.value <= 0:
        return _nm("non-positive three-year EPS growth")
    return RatioValue(pe.value / (growth.value * 100))


def _forward_pe(p: Period) -> RatioValue:
    if p.forward_eps is None or p.price is None:
        return RatioValue(
            None, "no tested forward estimate for this company (see the Estimates tab)"
        )
    if p.forward_eps <= 0:
        return _nm("non-positive forward EPS")
    return RatioValue(p.price / p.forward_eps)


def _reverse_dcf(p: Period) -> RatioValue:
    return p.implied_growth or RatioValue(
        None, "run the valuation tab: implied growth needs a discount-rate assumption"
    )


def _accruals(p: Period) -> RatioValue:
    return base.accruals_ratio(
        _num(p, "net_income"),
        _num(p, "cash_from_operations"),
        _num(p, "total_assets"),
        p.prior.get("total_assets"),
    )


def _quick(p: Period) -> RatioValue:
    if (v := _financial(p, "the quick ratio")) is not None:
        return v
    cash, rec, cl = _num(p, "cash"), _num(p, "receivables"), _num(p, "current_liabilities")
    if cash is None or rec is None:
        return _missing("cash or receivables")
    return _ratio(cash + rec, cl, "current liabilities", den_name="current liabilities")


def _ratio_of(
    num_key: str, den_key: str, name: str, *, avg_den: bool = False, fin: str | None = None
) -> Callable[[Period], RatioValue]:
    def fn(p: Period) -> RatioValue:
        if fin and (v := _financial(p, fin)) is not None:
            return v
        den = _avg(p, den_key) if avg_den else _num(p, den_key)
        return _ratio(
            _num(p, num_key),
            den,
            f"{num_key.replace('_', ' ')} or {den_key.replace('_', ' ')}",
            den_name=name,
        )

    return fn


def _net_debt_ebitda(p: Period) -> RatioValue:
    if (v := _financial(p, "net debt / EBITDA")) is not None:
        return v
    debt, cash, ebitda = debt_of(p.f), _num(p, "cash"), _ebitda(p)
    if debt is None or cash is None or ebitda is None:
        return _missing("debt, cash or EBITDA")
    if ebitda <= 0:
        return _nm("non-positive EBITDA")
    return RatioValue((debt - cash) / ebitda)


def _interest_cover(p: Period) -> RatioValue:
    if (v := _financial(p, "interest coverage")) is not None:
        return v
    ebit, interest = _ebit(p), _num(p, "interest_expense")
    if ebit is None or interest is None:
        return _missing("operating income or interest expense")
    if interest == 0:
        return _nm("no interest expense")
    return RatioValue(ebit / abs(interest))


def _debt_equity(p: Period) -> RatioValue:
    debt, eq = debt_of(p.f), _num(p, "equity")
    return _ratio(debt, eq, "debt or equity", den_name="equity")


def _debt_assets(p: Period) -> RatioValue:
    return _ratio(
        debt_of(p.f), _num(p, "total_assets"), "debt or total assets", den_name="total assets"
    )


def _gross_margin(p: Period) -> RatioValue:
    if (v := _financial(p, "gross margin")) is not None:
        return v
    return _ratio(
        _num(p, "gross_profit_resolved"),
        _num(p, "revenue"),
        "gross profit or revenue",
        den_name="revenue",
    )


def _capex_da(p: Period) -> RatioValue:
    capex, da = _num(p, "capex"), _num(p, "depreciation_amortization")
    if capex is None or da is None:
        return _missing("capital expenditure or depreciation")
    return _ratio(
        abs(capex), abs(da), "capital expenditure or depreciation", den_name="depreciation"
    )


def _fcf_conversion(p: Period) -> RatioValue:
    return _ratio(
        _fcf(p), _num(p, "net_income"), "free cash flow or net income", den_name="net income"
    )


def _cfo_ni(p: Period) -> RatioValue:
    return _ratio(
        _num(p, "cash_from_operations"),
        _num(p, "net_income"),
        "operating cash flow or net income",
        den_name="net income",
    )


def _roce(p: Period) -> RatioValue:
    if (v := _financial(p, "ROCE")) is not None:
        return v
    ta, cl = _num(p, "total_assets"), _num(p, "current_liabilities")
    if ta is None or cl is None:
        return _missing("total assets or current liabilities")
    return _ratio(_ebit(p), ta - cl, "operating income", den_name="capital employed")


def _payout(p: Period) -> RatioValue:
    div = _num(p, "dividends_paid")
    if div is None:
        return _missing("dividends paid")
    return _ratio(abs(div), _num(p, "net_income"), "net income", den_name="net income")


def _per_share_fn(key: str, *, absolute: bool = True) -> Callable[[Period], RatioValue]:
    def fn(p: Period) -> RatioValue:
        v = _num(p, key)
        if v is None:
            return _missing(key.replace("_", " "))
        out = _per_share(p, abs(v) if absolute else v)
        return RatioValue(out) if out is not None else _missing("share count")

    return fn


def _ey(p: Period) -> RatioValue:
    return base.earnings_yield(_num(p, "net_income"), p.market_cap)


def _fy(p: Period) -> RatioValue:
    return base.fcf_yield(_num(p, "cash_from_operations"), _num(p, "capex"), p.market_cap)


def _div_yield(p: Period) -> RatioValue:
    dps = _num(p, "dividends_per_share")
    if dps is None or p.price is None:
        return _missing("dividends per share or price")
    return _ratio(dps, p.price, "dividends per share or price", den_name="price")


def _pb(p: Period) -> RatioValue:
    return _ratio(p.market_cap, _num(p, "equity"), "market cap or equity", den_name="book equity")


def _pfcf(p: Period) -> RatioValue:
    return _ratio(p.market_cap, _fcf(p), "market cap or free cash flow", den_name="free cash flow")


def _fcf_of(f: Figures) -> Decimal | None:
    cfo, capex = f.get("cash_from_operations"), f.get("capex")
    return None if cfo is None or capex is None else cfo - abs(capex)


def _bvps_of(f: Figures) -> Decimal | None:
    equity = f.get("equity")
    shares = f.get("weighted_shares_diluted") or f.get("weighted_shares_basic")
    return None if equity is None or not shares else equity / shares


def _capex_revenue(p: Period) -> RatioValue:
    capex = _num(p, "capex")
    return _ratio(
        None if capex is None else abs(capex),
        _num(p, "revenue"),
        "capital expenditure or revenue",
        den_name="revenue",
    )


_R: list[CompanyRatio] = []


def _add(
    ratio_id: str,
    label: str,
    group: str,
    unit: str,
    formula: str,
    period: str,
    exclusions: str,
    better: bool | None,
    inputs: tuple[str, ...],
    fn: Callable[[Period], RatioValue],
) -> None:
    _R.append(
        CompanyRatio(ratio_id, label, group, unit, formula, period, exclusions, better, inputs, fn)
    )


ANNUAL = "Fiscal year, and trailing twelve months from the last four quarters"
BAL = "Fiscal year; balances are year-end, averages use the year-end and the one before"
PRICE = "Fiscal-year or TTM figures against the latest close on or before the price date"
NOFIN = "n/m for banks and insurers"
_f = "fraction"

for _id, _label, _num_key, _den_key, _better in (
    ("operating_margin", "Operating margin", "operating_income", "revenue", True),
    ("pretax_margin", "Pre-tax margin", "pretax_income", "revenue", True),
    ("net_margin", "Net margin", "net_income", "revenue", True),
):
    _add(
        _id,
        _label,
        "profitability",
        _f,
        f"{_num_key.replace('_', ' ')} / revenue",
        ANNUAL,
        "Non-positive revenue is n/m",
        _better,
        (_num_key, _den_key),
        _ratio_of(_num_key, _den_key, "revenue"),
    )
_add(
    "gross_margin",
    "Gross margin",
    "profitability",
    _f,
    "gross profit / revenue",
    ANNUAL,
    NOFIN,
    True,
    ("gross_profit_resolved", "revenue"),
    _gross_margin,
)
_add(
    "ebitda_margin",
    "EBITDA margin",
    "profitability",
    _f,
    "(operating income + depreciation and amortization) / revenue",
    ANNUAL,
    "Needs filed D&A; non-positive revenue is n/m",
    True,
    ("operating_income", "depreciation_amortization", "revenue"),
    lambda p: _ratio(_ebitda(p), _num(p, "revenue"), "EBITDA or revenue", den_name="revenue"),
)
_add(
    "fcf_margin",
    "FCF margin",
    "profitability",
    _f,
    "(operating cash flow − capital expenditure) / revenue",
    ANNUAL,
    "Non-positive revenue is n/m",
    True,
    ("cash_from_operations", "capex", "revenue"),
    lambda p: _ratio(_fcf(p), _num(p, "revenue"), "free cash flow or revenue", den_name="revenue"),
)

_add(
    "roe",
    "Return on equity",
    "returns",
    _f,
    "net income / average shareholders' equity",
    BAL,
    "n/m when average equity is not positive",
    True,
    ("net_income", "equity"),
    _ratio_of("net_income", "equity", "average equity", avg_den=True),
)
_add(
    "roa",
    "Return on assets",
    "returns",
    _f,
    "net income / average total assets",
    BAL,
    "Needs the prior year's balance sheet",
    True,
    ("net_income", "total_assets"),
    _ratio_of("net_income", "total_assets", "average total assets", avg_den=True),
)
_add(
    "roic",
    "Return on invested capital",
    "returns",
    _f,
    "operating income × (1 − tax rate) / average invested capital; invested capital = debt + equity + non-controlling interest − cash; tax rate = income tax / pre-tax income, limited to 0–50 %",
    BAL,
    NOFIN + "; n/m when pre-tax income is negative",
    True,
    ("operating_income", "pretax_income", "income_tax", "equity", "cash"),
    _nopat_roic,
)
_add(
    "roce",
    "Return on capital employed",
    "returns",
    _f,
    "operating income / (total assets − current liabilities)",
    BAL,
    NOFIN,
    True,
    ("operating_income", "total_assets", "current_liabilities"),
    _roce,
)
_add(
    "gross_profit_to_assets",
    "Gross profit / assets",
    "returns",
    _f,
    "gross profit / total assets",
    BAL,
    NOFIN,
    True,
    ("gross_profit_resolved", "total_assets"),
    lambda p: (
        _financial(p, "gross profit / assets")
        or base.gross_profit_to_assets(_num(p, "gross_profit_resolved"), _num(p, "total_assets"))
    ),
)

for _years in (1, 3, 5):
    for _key, _label2, _src in (
        ("revenue", "Revenue", ("revenue",)),
        ("operating_income", "Operating income", ("operating_income",)),
        ("eps_diluted", "EPS", ("eps_diluted",)),
    ):
        _add(
            f"{_key}_cagr_{_years}y",
            f"{_label2} growth, {_years}-year CAGR",
            "growth",
            _f,
            f"({_key.replace('_', ' ')} now / {_years} years earlier)^(1/{_years}) − 1",
            "Fiscal years",
            "n/m unless both ends are positive",
            True,
            _src,
            _cagr(_key, _years),
        )
    _add(
        f"fcf_cagr_{_years}y",
        f"FCF growth, {_years}-year CAGR",
        "growth",
        _f,
        f"(FCF now / {_years} years earlier)^(1/{_years}) − 1",
        "Fiscal years",
        "n/m unless both ends are positive",
        True,
        ("cash_from_operations", "capex"),
        _cagr(
            "fcf",
            _years,
            derived=_fcf_of,
        ),
    )
    _add(
        f"bvps_cagr_{_years}y",
        f"Book value per share growth, {_years}-year CAGR",
        "growth",
        _f,
        f"(equity per diluted share now / {_years} years earlier)^(1/{_years}) − 1",
        "Fiscal years",
        "n/m unless both ends are positive",
        True,
        ("equity", "weighted_shares_diluted"),
        _cagr(
            "bvps",
            _years,
            derived=_bvps_of,
        ),
    )

_add(
    "fcf_conversion",
    "FCF conversion",
    "cash_quality",
    _f,
    "free cash flow / net income",
    ANNUAL,
    "n/m when net income is not positive",
    True,
    ("cash_from_operations", "capex", "net_income"),
    _fcf_conversion,
)
_add(
    "cfo_to_net_income",
    "Operating cash flow / net income",
    "cash_quality",
    "multiple",
    "operating cash flow / net income",
    ANNUAL,
    "n/m when net income is not positive",
    True,
    ("cash_from_operations", "net_income"),
    _cfo_ni,
)
_add(
    "accruals_ratio",
    "Accruals ratio",
    "cash_quality",
    _f,
    "(net income − operating cash flow) / average total assets",
    BAL,
    "Higher means earnings lean on accruals",
    False,
    ("net_income", "cash_from_operations", "total_assets"),
    _accruals,
)
_add(
    "capex_to_revenue",
    "Capex / revenue",
    "cash_quality",
    _f,
    "capital expenditure / revenue",
    ANNUAL,
    "Non-positive revenue is n/m",
    None,
    ("capex", "revenue"),
    _capex_revenue,
)
_add(
    "capex_to_depreciation",
    "Capex / depreciation",
    "cash_quality",
    "multiple",
    "capital expenditure / depreciation and amortization",
    ANNUAL,
    "Needs filed D&A",
    None,
    ("capex", "depreciation_amortization"),
    _capex_da,
)

_add(
    "current_ratio",
    "Current ratio",
    "liquidity",
    "multiple",
    "current assets / current liabilities",
    BAL,
    NOFIN,
    True,
    ("current_assets", "current_liabilities"),
    lambda p: (
        _financial(p, "the current ratio")
        or _ratio(
            _num(p, "current_assets"),
            _num(p, "current_liabilities"),
            "current assets or liabilities",
            den_name="current liabilities",
        )
    ),
)
_add(
    "quick_ratio",
    "Quick ratio",
    "liquidity",
    "multiple",
    "(cash + receivables) / current liabilities",
    BAL,
    NOFIN,
    True,
    ("cash", "receivables", "current_liabilities"),
    _quick,
)
_add(
    "cash_ratio",
    "Cash ratio",
    "liquidity",
    "multiple",
    "cash / current liabilities",
    BAL,
    NOFIN,
    True,
    ("cash", "current_liabilities"),
    lambda p: (
        _financial(p, "the cash ratio")
        or _ratio(
            _num(p, "cash"),
            _num(p, "current_liabilities"),
            "cash or current liabilities",
            den_name="current liabilities",
        )
    ),
)
_add(
    "cash_runway",
    "Cash runway",
    "liquidity",
    "years",
    "cash / annual operating cash burn",
    ANNUAL,
    "Only for companies with negative operating cash flow",
    None,
    ("cash", "cash_from_operations"),
    lambda p: base.cash_runway_years(_num(p, "cash"), _num(p, "cash_from_operations")),
)

_add(
    "debt_to_equity",
    "Debt / equity",
    "solvency",
    "multiple",
    "financial debt / shareholders' equity; leases excluded",
    BAL,
    NOFIN + "; n/m when equity is not positive",
    False,
    ("long_term_debt_total", "equity"),
    lambda p: _financial(p, "debt / equity") or _debt_equity(p),
)
_add(
    "net_debt_to_ebitda",
    "Net debt / EBITDA",
    "solvency",
    "multiple",
    "(financial debt − cash) / EBITDA",
    ANNUAL,
    NOFIN + "; n/m when EBITDA is not positive",
    False,
    ("long_term_debt_total", "cash", "operating_income", "depreciation_amortization"),
    _net_debt_ebitda,
)
_add(
    "interest_coverage",
    "Interest coverage",
    "solvency",
    "multiple",
    "operating income / interest expense",
    ANNUAL,
    NOFIN,
    True,
    ("operating_income", "interest_expense"),
    _interest_cover,
)
_add(
    "debt_to_assets",
    "Debt / assets",
    "solvency",
    _f,
    "financial debt / total assets",
    BAL,
    NOFIN,
    False,
    ("long_term_debt_total", "total_assets"),
    lambda p: _financial(p, "debt / assets") or _debt_assets(p),
)
_add(
    "altman_z",
    "Altman Z-score",
    "solvency",
    "multiple",
    "1.2 WC/TA + 1.4 RE/TA + 3.3 EBIT/TA + 0.6 MVE/TL + 1.0 S/TA",
    BAL,
    "Non-financials only; periods collected before retained earnings were stored show as missing until re-collected",
    True,
    ("retained_earnings", "current_assets", "current_liabilities", "operating_income", "revenue"),
    _altman,
)

_add(
    "asset_turnover",
    "Asset turnover",
    "efficiency",
    "multiple",
    "revenue / average total assets",
    BAL,
    "Needs the prior year's balance sheet",
    True,
    ("revenue", "total_assets"),
    _ratio_of("revenue", "total_assets", "average total assets", avg_den=True),
)
_add(
    "inventory_turnover",
    "Inventory turnover",
    "efficiency",
    "multiple",
    "cost of revenue / average inventory",
    BAL,
    "n/m with no inventory",
    True,
    ("cost_of_revenue", "inventory"),
    _ratio_of(
        "cost_of_revenue", "inventory", "average inventory", avg_den=True, fin="inventory turnover"
    ),
)
_add(
    "receivables_turnover",
    "Receivables turnover",
    "efficiency",
    "multiple",
    "revenue / average receivables",
    BAL,
    NOFIN,
    True,
    ("revenue", "receivables"),
    _ratio_of(
        "revenue", "receivables", "average receivables", avg_den=True, fin="receivables turnover"
    ),
)
_add(
    "days_sales_outstanding",
    "Days sales outstanding",
    "efficiency",
    "days",
    "average receivables / revenue × 365",
    BAL,
    NOFIN,
    False,
    ("receivables", "revenue"),
    lambda p: (
        _financial(p, "days sales outstanding") or _days("receivables", "revenue", "receivables")(p)
    ),
)
_add(
    "days_inventory",
    "Days inventory",
    "efficiency",
    "days",
    "average inventory / cost of revenue × 365",
    BAL,
    NOFIN,
    False,
    ("inventory", "cost_of_revenue"),
    lambda p: (
        _financial(p, "days inventory") or _days("inventory", "cost_of_revenue", "inventory")(p)
    ),
)
_add(
    "days_payables",
    "Days payables",
    "efficiency",
    "days",
    "average payables / cost of revenue × 365",
    BAL,
    NOFIN,
    None,
    ("payables", "cost_of_revenue"),
    lambda p: _financial(p, "days payables") or _days("payables", "cost_of_revenue", "payables")(p),
)
_add(
    "cash_conversion_cycle",
    "Cash conversion cycle",
    "efficiency",
    "days",
    "days sales + days inventory − days payables",
    BAL,
    NOFIN,
    False,
    ("receivables", "inventory", "payables", "revenue", "cost_of_revenue"),
    _ccc,
)

_add(
    "dilution_1y",
    "Share-count change, 1 year",
    "per_share",
    _f,
    "diluted weighted shares now / one year earlier − 1",
    "Fiscal years",
    "Positive means dilution",
    False,
    ("weighted_shares_diluted",),
    _dilution(1),
)
_add(
    "dilution_3y",
    "Share-count change, 3-year CAGR",
    "per_share",
    _f,
    "(diluted weighted shares now / 3 years earlier)^(1/3) − 1",
    "Fiscal years",
    "Positive means dilution",
    False,
    ("weighted_shares_diluted",),
    _dilution(3),
)
_add(
    "dividends_per_share_paid",
    "Dividends paid per share",
    "per_share",
    "money_per_share",
    "dividends paid / diluted weighted shares",
    ANNUAL,
    "Cash paid, not declared",
    True,
    ("dividends_paid", "weighted_shares_diluted"),
    _per_share_fn("dividends_paid"),
)
_add(
    "buybacks_per_share",
    "Buybacks per share",
    "per_share",
    "money_per_share",
    "share repurchases paid / diluted weighted shares",
    ANNUAL,
    "Cash paid; net of issuance is not known",
    None,
    ("share_repurchases_paid", "weighted_shares_diluted"),
    _per_share_fn("share_repurchases_paid"),
)
_add(
    "payout_ratio",
    "Payout ratio",
    "per_share",
    _f,
    "dividends paid / net income",
    ANNUAL,
    "n/m when net income is not positive",
    None,
    ("dividends_paid", "net_income"),
    _payout,
)
_add(
    "total_shareholder_yield",
    "Total shareholder yield",
    "per_share",
    _f,
    "(dividends + buybacks paid) / market cap",
    PRICE,
    "Needs a price and a share count",
    True,
    ("dividends_paid", "share_repurchases_paid"),
    _shareholder_yield,
)

_add(
    "pe",
    "P/E",
    "valuation",
    "multiple",
    "market cap / net income",
    PRICE,
    "n/m when net income is not positive",
    None,
    ("net_income",),
    _pe,
)
_add(
    "forward_pe",
    "Forward P/E",
    "valuation",
    "multiple",
    "price / forward EPS from a tested estimate",
    PRICE,
    "Needs a tested forward estimate; company guidance and event-calendar estimates do not count",
    None,
    (),
    _forward_pe,
)
_add(
    "ev_to_ebit",
    "EV / EBIT",
    "valuation",
    "multiple",
    "(market cap + debt − cash) / operating income",
    PRICE,
    NOFIN,
    None,
    ("operating_income",),
    _ev_ratio(_ebit, "operating income"),
)
_add(
    "ev_to_ebitda",
    "EV / EBITDA",
    "valuation",
    "multiple",
    "(market cap + debt − cash) / EBITDA",
    PRICE,
    NOFIN,
    None,
    ("operating_income", "depreciation_amortization"),
    _ev_ratio(_ebitda, "EBITDA"),
)
_add(
    "ev_to_sales",
    "EV / sales",
    "valuation",
    "multiple",
    "(market cap + debt − cash) / revenue",
    PRICE,
    NOFIN,
    None,
    ("revenue",),
    _ev_ratio(lambda p: _num(p, "revenue"), "revenue"),
)
_add(
    "pb",
    "P/B",
    "valuation",
    "multiple",
    "market cap / shareholders' equity",
    PRICE,
    "n/m when equity is not positive",
    None,
    ("equity",),
    _pb,
)
_add(
    "p_fcf",
    "P/FCF",
    "valuation",
    "multiple",
    "market cap / free cash flow",
    PRICE,
    "n/m when free cash flow is not positive",
    None,
    ("cash_from_operations", "capex"),
    _pfcf,
)
_add(
    "fcf_yield",
    "FCF yield",
    "valuation",
    _f,
    "(operating cash flow − capital expenditure) / market cap",
    PRICE,
    "Needs a price and a share count",
    True,
    ("cash_from_operations", "capex"),
    _fy,
)
_add(
    "earnings_yield",
    "Earnings yield",
    "valuation",
    _f,
    "net income / market cap",
    PRICE,
    "Needs a price and a share count",
    True,
    ("net_income",),
    _ey,
)
_add(
    "peg",
    "PEG",
    "valuation",
    "multiple",
    "P/E / (3-year EPS growth in percent)",
    PRICE,
    "n/m unless 3-year EPS growth is positive",
    None,
    ("net_income", "eps_diluted"),
    _peg,
)
_add(
    "dividend_yield",
    "Dividend yield",
    "valuation",
    _f,
    "dividends per share / price",
    PRICE,
    "Declared dividends per share, filed",
    True,
    ("dividends_per_share",),
    _div_yield,
)
_add(
    "reverse_dcf_growth",
    "Implied growth (reverse DCF)",
    "valuation",
    _f,
    "growth that makes the DCF equal the price at the stated discount rate",
    PRICE,
    "Depends on the valuation tab's discount-rate assumption",
    None,
    (),
    _reverse_dcf,
)

COMPANY_RATIOS: dict[str, CompanyRatio] = {r.id: r for r in _R}
GROUP_ORDER = (
    "profitability",
    "returns",
    "growth",
    "cash_quality",
    "liquidity",
    "solvency",
    "efficiency",
    "per_share",
    "valuation",
)
GROUP_LABEL = {
    "profitability": "Profitability",
    "returns": "Returns",
    "growth": "Growth",
    "cash_quality": "Cash-flow quality",
    "liquidity": "Liquidity",
    "solvency": "Solvency",
    "efficiency": "Efficiency",
    "per_share": "Per share and capital allocation",
    "valuation": "Valuation",
}
