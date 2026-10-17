"""Assemble screen records from loaded observations (pure: no I/O). Stock records combine the
filed-statement periods (`peer_metrics`), stored daily closes, dividends, profile and insider
data into the catalogue's metrics; fund records combine the factsheet and holdings. Every
metric that cannot be computed is a `MetricValue` with the reason, never zero.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from steward.analytics import ratios
from steward.analytics.ratio_set import nopat_roic
from steward.analytics.returns import to_major_unit
from steward.company.ratios import is_financial_issuer
from steward.discovery.engine import Record
from steward.discovery.metrics import STOCK_CATALOGUE, MetricValue
from steward.discovery.universe import UniverseMember
from steward.peer_metrics import (
    FLOW_INPUTS,
    _is_annual,
    invested_capital,
    latest_period,
    prior_year,
)
from steward.reference.risk import volatility as _volatility

ZERO = Decimal(0)
ONE = Decimal(1)
Series = Sequence[tuple[dt.date, Decimal]]
NUMERIC_STOCK = [
    m.id for m in STOCK_CATALOGUE.values() if m.kind == "numeric" and m.id != "data_coverage"
]


@dataclass
class StockInputs:
    """Everything loaded for one ticker; every field may be empty."""

    periods: list[dict[str, Any]] = field(default_factory=list)  # peer_metrics periods
    closes: list[tuple[dt.date, Decimal]] = field(default_factory=list)  # adjusted, ascending
    shares: list[tuple[dt.date, Decimal]] = field(default_factory=list)  # annual diluted, ascending
    assets: list[tuple[dt.date, Decimal]] = field(default_factory=list)  # period-end total assets
    dividends_ttm: Decimal | None = None
    dividend_currency: str | None = None
    turnover: tuple[Decimal, str] | None = None
    sector: str | None = None
    industry: str | None = None
    first_trade: dt.date | None = None
    insider_buyers_90d: int | None = None
    forward_eps: Decimal | None = None
    forward_currency: str | None = None  # currency of the consensus EPS
    splits: list[tuple[dt.date, Decimal]] = field(default_factory=list)  # (ex_date, new per old)
    # The same annual share series as the latest filings state it, ignoring the cutoff: filings
    # restate earlier years for a split, so it shows a split no corporate action records.
    restated_shares: list[tuple[dt.date, Decimal]] = field(default_factory=list)
    statement_currency: str | None = None
    # Home-market shares one priced share represents (TSMC's ADS = 5 common shares).
    ads_ratio: Decimal = Decimal(1)
    # Latest ECB fixings: EUR per one unit of each currency.
    fx_rates: dict[str, Decimal] = field(default_factory=dict)
    # Latest cached company checks (axis -> 0-6 score or None) and failed risk checks; empty when
    # the issuer has never been checked.
    check_scores: dict[str, Decimal | None] = field(default_factory=dict)
    check_risk_flags: int | None = None
    # Insight I10.5. Annual filed flows by period end (cash-flow statement, absolute values).
    dividends_paid: list[tuple[dt.date, Decimal]] = field(default_factory=list)
    buybacks_paid: list[tuple[dt.date, Decimal]] = field(default_factory=list)
    depreciation: list[tuple[dt.date, Decimal]] = field(default_factory=list)
    # Split-adjusted dividends per share by trailing year: index 0 is the last 12 months, 1 the
    # 12 before, ... 5. A year with no confirmed dividend is absent, not zero.
    dividends_by_year: dict[int, Decimal] = field(default_factory=dict)
    insider_sellers_90d: int | None = None


TRACKING_UNSET = (
    "no benchmark recorded for this fund; record one on the fund's performance page "
    "(it is the only way to compare on the same basis)"
)


def _mv(
    value: Decimal | str | None,
    *,
    reason: str | None = None,
    period_end: dt.date | None = None,
    basis: str | None = None,
    source: str | None = None,
) -> MetricValue:
    return MetricValue(
        value=value,
        reason=None if value is not None and reason is None else reason,
        period_end=period_end,
        basis=basis,
        source=source,
    )


SPLIT_TOLERANCE = Decimal("0.02")


def _split_like(ratio: Decimal) -> Decimal | None:
    """The split ratio (new shares per old) a restated count implies, or None when the change is
    not within 2% of a whole split such as 2:1, 10:1 or 1:4 (a correction, not a split)."""
    for r, invert in ((ratio, False), (ONE / ratio, True)):
        whole = r.to_integral_value()
        if whole >= 2 and abs(r - whole) / whole <= SPLIT_TOLERANCE:
            return ONE / whole if invert else whole
    return None


def split_factor(
    filed: tuple[dt.date, Decimal],
    splits: Sequence[tuple[dt.date, Decimal]],
    restated: Sequence[tuple[dt.date, Decimal]],
) -> tuple[Decimal, str | None]:
    """What scales a filed share count onto the basis of today's split-adjusted closes, and how
    it was found. Stored splits after the period end come first; without one, a later filing that
    restates the same period by a whole split ratio stands in; else the count is used as filed."""
    period_end, count = filed
    stored = [r for ex, r in splits if ex > period_end and r > 0]
    if stored:
        factor = ONE
        for r in stored:
            factor *= r
        return factor, None
    later = dict(restated).get(period_end)
    if later is not None and count > 0 and later > 0 and later != count:
        implied = _split_like(later / count)
        if implied is not None:
            return implied, "split inferred from a later filing that restates the share count"
    return ONE, None


def _ratio_mv(r: ratios.RatioValue, **kw: Any) -> MetricValue:
    return _mv(r.value, reason=r.reason, **kw)


def nearest(
    series: Series, target: dt.date, tolerance_days: int = 45
) -> tuple[dt.date, Decimal] | None:
    best = min(series, key=lambda p: abs((p[0] - target).days), default=None)
    return best if best is not None and abs((best[0] - target).days) <= tolerance_days else None


def _return(closes: Series, as_of: dt.date, days: int) -> tuple[Decimal | None, str | None]:
    if not closes:
        return None, "no stored daily closes"
    end_d, end_c = closes[-1]
    if (as_of - end_d).days > 10:
        return None, f"latest close is from {end_d}, too old for the data date"
    start_target = end_d - dt.timedelta(days=days)
    earlier = [p for p in closes if p[0] <= start_target]
    if not earlier or (start_target - earlier[-1][0]).days > 10:
        return None, f"price history starts {closes[0][0]}, shorter than the window"
    start_c = earlier[-1][1]
    if start_c <= 0 or end_c <= 0:
        return None, "n/m: non-positive close"
    return end_c / start_c - ONE, None


def price_currency(member: UniverseMember) -> str | None:
    """The currency of the member's stored closes. They are read by the price symbol, so they are
    in that market's currency: AZN_US_EQ trades on NYSE (azn.us, in dollars) while its identity
    links the London listing (GBP)."""
    return "USD" if (member.price_symbol or "").lower().endswith(".us") else member.currency


def _price_major(member: UniverseMember, closes: Series) -> tuple[Decimal, str] | None:
    currency = price_currency(member)
    if not closes or not currency:
        return None
    return to_major_unit(closes[-1][1], currency)


def _fx(price: tuple[Decimal, str], target: str, data: StockInputs) -> Decimal | None:
    """`price` in `target` at the latest fixings (`data.fx_rates`: EUR per one unit)."""
    rates = {**data.fx_rates, "EUR": Decimal(1)}
    src, dst = rates.get(price[1]), rates.get(target)
    if src is None or not dst:
        return None
    return price[0] * src / dst


def _roic(
    period: dict[str, Any], periods: list[dict[str, Any]], financial: bool
) -> ratios.RatioValue:
    """ROIC by the shared definition (`ratio_set.nopat_roic` over `peer_metrics.
    invested_capital`, which counts non-controlling interest): average of invested capital now
    and a year earlier, tax rate capped to 0-50%, not meaningful for banks and insurers."""
    if financial:
        return ratios.RatioValue(None, "ROIC is not meaningful for banks and insurers")
    if not _is_annual(period["duration_days"]):
        return ratios.RatioValue(None, "n/m: ROIC is computed for a full-year period only")
    prior = prior_year(period, periods)
    got = {k: v["value"] for k, v in period["inputs"].items()}
    return nopat_roic(
        got.get("operating_income"),
        got.get("pretax"),
        got.get("tax"),
        invested_capital(period["inputs"]),
        invested_capital(prior["inputs"]) if prior else None,
    )


def trailing_inputs(period: dict[str, Any], periods: list[dict[str, Any]]) -> dict[str, Decimal]:
    """The period's inputs with every flow line over the twelve months to its end.

    Valuation multiples need a year of earnings and cash flow. When the newest period is a
    half-year or a quarter, a flow is the last full year plus this year to date less the same
    span a year earlier; failing that, the last full year's figure. Balance-sheet lines stay
    at the newest period. Production, 17 Oct 2026: KO's P/E read 48.5x because six months of
    net income ($8.3bn, H1 2026) were divided into a full market cap; on a trailing year it
    is about 28x."""
    current = {k: v["value"] for k, v in period["inputs"].items()}
    if _is_annual(period["duration_days"]):
        return current
    end = period["period_end"]
    # One period end is often stored several times (a quarter and a year to date, statements
    # with and without inputs): an empty copy picked as "the year" dropped every flow, so
    # GOOGL's and KO's P/E read unknown. Spans are merged by end and length.
    same_basis = [p for p in periods if p["basis"] == period["basis"] and p["inputs"]]

    def merged(at_end: dt.date, days: int) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for p in same_basis:
            if p["period_end"] == at_end and abs((p["duration_days"] or 0) - days) <= 10:
                for k, v in p["inputs"].items():
                    out.setdefault(k, v["value"])
        return out

    out = {k: v for k, v in current.items() if k not in FLOW_INPUTS}
    year_end = max(
        (
            p["period_end"]
            for p in same_basis
            if _is_annual(p["duration_days"]) and 0 < (end - p["period_end"]).days <= 370
        ),
        default=None,
    )
    if year_end is None:
        return out  # no year to build on: a multiple stays unknown rather than understated
    year = merged(year_end, 365)
    # The year to date since that year end, less the same span a year earlier. A quarter is
    # that span only in the first quarter: FY + Q2 - last year's Q2 is not a trailing year.
    span = (end - year_end).days
    ytd = merged(end, span)
    prior_ends = sorted(
        {p["period_end"] for p in same_basis if 350 <= (end - p["period_end"]).days <= 380}
    )
    before = next((b for b in (merged(e, span) for e in prior_ends) if b), {})
    for key in FLOW_INPUTS:
        if key in year and key in ytd and key in before:
            out[key] = year[key] + ytd[key] - before[key]
        elif key in year:
            out[key] = year[key]
    return out


def stock_record(member: UniverseMember, data: StockInputs, as_of: dt.date) -> Record:
    values: dict[str, MetricValue] = {}
    identity: dict[str, str | None] = {
        "country": member.country,
        "mic": member.mic or member.exchange,
        "currency": member.currency,
        "asset_type": member.asset_type,
        "isin": member.isin,
        "status": member.status,
    }
    for ident in ("country", "mic", "currency", "asset_type"):
        values[ident] = _mv(
            identity[ident], reason=None if identity[ident] else "not resolved", source="identity"
        )
    values["sector"] = _mv(
        data.sector,
        reason=None if data.sector else "no provider profile fetched",
        source="fmp_profile",
    )
    values["industry"] = _mv(
        data.industry,
        reason=None if data.industry else "no provider profile fetched",
        source="fmp_profile",
    )
    identity["sector"] = data.sector

    period = latest_period(data.periods) if data.periods else None
    inputs: dict[str, Decimal] = {}
    if period is not None:
        for metric in (
            "revenue_growth",
            "gross_margin",
            "operating_margin",
            "roic",
            "net_debt_to_equity",
            "free_cash_flow_conversion",
        ):
            values[metric] = _mv(
                period.get(metric),
                reason=period["not_meaningful"].get(metric),
                period_end=period["period_end"],
                basis=period["basis"],
                source="filed_statements",
            )
        inputs = trailing_inputs(period, data.periods)
        r = _roic(period, data.periods, is_financial_issuer(data.sector, data.industry))
        values["roic"] = _ratio_mv(
            r, period_end=period["period_end"], basis=period["basis"], source="filed_statements"
        )
    pe, basis = (period["period_end"], period["basis"]) if period else (None, None)
    statement_ccy = data.statement_currency or (period.get("unit") if period else None)
    if isinstance(statement_ccy, str) and not re.fullmatch(r"[A-Z]{3}", statement_ccy):
        statement_ccy = None

    # Market cap needs price and shares in one currency.
    price = _price_major(member, data.closes)
    shares = data.shares[-1] if data.shares else None
    market_cap: Decimal | None = None
    cap_reason: str | None = None
    cap_source = "price_bar x filed shares"
    if price is None:
        cap_reason = "no stored close or trading currency"
    elif data.closes and (as_of - data.closes[-1][0]).days > 10:
        cap_reason = f"last stored close is {data.closes[-1][0]}, too old to value the company"
    elif shares is None:
        cap_reason = "no stored share count"
    elif not statement_ccy:
        cap_reason = "statement currency unknown, so price and share count cannot be matched"
    elif price[1] != statement_ccy and _fx(price, statement_ccy, data) is None:
        cap_reason = f"price currency {price[1]} differs from statement currency {statement_ccy}"
    else:
        cap_price = price[0]
        if price[1] != statement_ccy:
            # ASML's dollar ADS against euro statements: the cap is put in the statement currency
            # at the latest ECB fixing so the multiples compare like with like.
            cap_price = _fx(price, statement_ccy, data) or price[0]
            cap_source += f" (price in {price[1]} converted to {statement_ccy} at the ECB fixing)"
        # Closes are split-adjusted to today's share basis; the filed share count is not, so it is
        # scaled by every split after its period end (see `split_factor`).
        factor, how = split_factor(shares, data.splits, data.restated_shares)
        # The price is per listed share and the filed count is of home-market shares: TSM's
        # $412 ADS times 25.9bn common shares put its P/E at 198x instead of about 40x.
        market_cap = cap_price * shares[1] * factor / data.ads_ratio
        if how:
            cap_source += f" ({how})"
        if data.ads_ratio != 1:
            cap_source += f" (1 listed share = {data.ads_ratio:g} home-market shares)"
    values["market_cap"] = _mv(
        market_cap,
        reason=cap_reason,
        period_end=data.closes[-1][0] if data.closes else None,
        source=cap_source,
    )

    ey = (
        ratios.earnings_yield(inputs.get("net_income"), market_cap)
        if period
        else ratios.RatioValue(None, "no filed statements")
    )
    values["earnings_yield"] = _ratio_mv(ey, period_end=pe, basis=basis, source="filed_statements")
    pe_val = ONE / ey.value if ey.value is not None and ey.value > 0 else None
    values["pe_ratio"] = _mv(
        pe_val,
        reason=None if pe_val else (ey.reason or "n/m: earnings not positive"),
        period_end=pe,
        basis=basis,
    )
    capex, cfo = inputs.get("capex"), inputs.get("cfo")
    values["fcf_yield"] = _ratio_mv(
        ratios.fcf_yield(cfo, capex, market_cap),
        period_end=pe,
        basis=basis,
        source="filed_statements",
    )
    debt, cash = inputs.get("debt"), inputs.get("cash")
    ev = ratios.enterprise_value(market_cap, debt, cash)
    values["ev_ebit"] = _ratio_mv(
        ratios.RatioValue(None, "enterprise value is not meaningful for banks and insurers")
        if is_financial_issuer(data.sector, data.industry)
        else ratios.ev_to_ebit(ev, inputs.get("operating_income")),
        period_end=pe,
        basis=basis,
        source="filed_statements",
    )
    equity = inputs.get("equity")
    if market_cap is None or equity is None:
        values["price_to_book"] = _mv(None, reason="missing market cap or equity", period_end=pe)
    elif equity <= 0:
        values["price_to_book"] = _mv(None, reason="n/m: non-positive equity", period_end=pe)
    else:
        values["price_to_book"] = _mv(
            market_cap / equity, period_end=pe, basis=basis, source="filed_statements"
        )
    revenue = inputs.get("revenue")
    net_income = inputs.get("net_income")
    if revenue is None or net_income is None:
        values["net_margin"] = _mv(None, reason="missing revenue or net income", period_end=pe)
    elif revenue <= 0:
        values["net_margin"] = _mv(None, reason="n/m: non-positive revenue", period_end=pe)
    else:
        values["net_margin"] = _mv(
            net_income / revenue, period_end=pe, basis=basis, source="filed_statements"
        )
    if market_cap is None or revenue is None:
        values["price_to_sales"] = _mv(None, reason="missing market cap or revenue", period_end=pe)
    elif revenue <= 0:
        values["price_to_sales"] = _mv(None, reason="n/m: non-positive revenue", period_end=pe)
    else:
        values["price_to_sales"] = _mv(
            market_cap / revenue, period_end=pe, basis=basis, source="filed_statements"
        )

    # Assets and shares over time.
    assets_now = None
    if period is not None and data.assets:
        assets_now = nearest(data.assets, period["period_end"], 7)
    values["gross_profit_to_assets"] = _ratio_mv(
        ratios.gross_profit_to_assets(
            inputs.get("gross_profit"), assets_now[1] if assets_now else None
        ),
        period_end=pe,
        basis=basis,
        source="filed_statements",
    )
    assets_prior = (
        nearest(data.assets, assets_now[0] - dt.timedelta(days=365), 45) if assets_now else None
    )
    values["asset_growth"] = _ratio_mv(
        ratios.change_over_period(
            assets_now[1] if assets_now else None,
            assets_prior[1] if assets_prior else None,
            "total assets",
        ),
        period_end=pe,
        source="filed_statements",
    )
    values["accruals_ratio"] = _ratio_mv(
        ratios.accruals_ratio(
            inputs.get("net_income"),
            cfo,
            assets_prior[1] if assets_prior else None,
            assets_now[1] if assets_now else None,
        ),
        period_end=pe,
        basis=basis,
        source="filed_statements",
    )
    s_now = shares
    s_1y = nearest(data.shares, s_now[0] - dt.timedelta(days=365), 45) if s_now else None
    s_3y = nearest(data.shares, s_now[0] - dt.timedelta(days=3 * 365), 60) if s_now else None
    values["share_change_1y"] = _ratio_mv(
        ratios.change_over_period(
            s_now[1] if s_now else None, s_1y[1] if s_1y else None, "share count"
        ),
        period_end=s_now[0] if s_now else None,
        source="filed_statements",
    )
    values["share_change_3y"] = _ratio_mv(
        ratios.annualised_change(
            ratios.change_over_period(
                s_now[1] if s_now else None, s_3y[1] if s_3y else None, "share count"
            ),
            3,
        ),
        period_end=s_now[0] if s_now else None,
        source="filed_statements (annualised)",
    )
    # Runway needs a full year of burn: the latest period when it is a full year, else the latest
    # annual one (stated in the basis); a quarter or year-to-date flow would overstate the runway.
    run_p = period
    if period is not None and not _is_annual(period["duration_days"]):
        run_p = next(
            (
                p
                for p in sorted(data.periods, key=lambda p: p["period_end"], reverse=True)
                if _is_annual(p["duration_days"])
                and p["inputs"].get("cfo")
                and p["inputs"].get("cash")
            ),
            None,
        )
    if run_p is None:
        runway = ratios.RatioValue(None, "no filed statements")
    else:
        got = {k: v["value"] for k, v in run_p["inputs"].items()}
        runway = ratios.cash_runway_years(got.get("cash"), got.get("cfo"))
    run_basis = basis
    if run_p is not None and run_p is not period:
        run_basis = f"{run_p['basis']}, latest annual operating cash flow (no TTM available)"
    values["cash_runway_years"] = _ratio_mv(
        runway,
        period_end=run_p["period_end"] if run_p else pe,
        basis=run_basis,
        source="filed_statements",
    )

    # Prices.
    for mid, days in (("return_1y", 365), ("return_3y", 3 * 365), ("return_5y", 5 * 365)):
        ret, why = _return(data.closes, as_of, days)
        values[mid] = _mv(
            ret,
            reason=why,
            period_end=data.closes[-1][0] if data.closes else None,
            source="price_bar (adjusted)",
        )
    tail = [(d, float(c)) for d, c in data.closes[-253:]]
    stale = bool(data.closes) and (as_of - data.closes[-1][0]).days > 10
    vol = _volatility(tail) if len(tail) >= 200 and not stale else None
    values["volatility_1y"] = _mv(
        Decimal(str(vol)) if vol is not None else None,
        reason=None
        if vol is not None
        else f"latest close is from {data.closes[-1][0]}, too old for the data date"
        if stale
        else "fewer than 200 sessions of stored closes",
        period_end=data.closes[-1][0] if data.closes else None,
        source="price_bar (adjusted)",
    )
    mom = (
        ratios.momentum_12_1(data.closes, as_of)
        if data.closes
        else ratios.RatioValue(None, "no stored daily closes")
    )
    values["momentum_12_1"] = _ratio_mv(mom, source="price_bar (adjusted)")

    # Income, liquidity, listing age, signals.
    if data.dividends_ttm is None:
        dy: Decimal | None = None
        dy_reason: str | None = "no dividend history stored"
    elif price is None:
        dy, dy_reason = None, "no stored close"
    else:
        # Pence amounts are put on the major unit the price is in.
        pence = data.dividend_currency == "GBX"
        div_ccy = "GBP" if pence else data.dividend_currency
        if div_ccy and div_ccy != price[1]:
            dy, dy_reason = (
                None,
                f"dividend currency {data.dividend_currency} differs from price "
                f"currency {price[1]}",
            )
        else:
            dy, dy_reason = data.dividends_ttm / (100 if pence else 1) / price[0], None
    values["dividend_yield"] = _mv(dy, reason=dy_reason, source="corporate_action")
    values["avg_daily_turnover"] = _mv(
        data.turnover[0] if data.turnover else None,
        reason=None if data.turnover else "no FITRS liquidity record",
        source="listing_liquidity",
    )
    listed = data.first_trade is not None and data.first_trade <= as_of
    values["years_since_listing"] = _mv(
        (Decimal((as_of - data.first_trade).days) / 365) if listed and data.first_trade else None,
        reason=None
        if listed
        else "not yet listed on this date"
        if data.first_trade
        else "first trade date not published",
        source="listing",
    )
    values["insider_buyers_90d"] = _mv(
        Decimal(data.insider_buyers_90d) if data.insider_buyers_90d is not None else None,
        reason=None
        if data.insider_buyers_90d is not None
        else "no insider filings stored (US issuers only)",
        source="insider_transaction",
    )
    fwd = None
    fwd_reason: str | None = "estimates source not available"
    if data.forward_eps is not None:
        if price is None:
            fwd_reason = "no stored close"
        elif data.forward_currency is not None and data.forward_currency != price[1]:
            fwd_reason = (
                f"consensus currency {data.forward_currency} differs from price currency {price[1]}"
            )
        elif data.forward_eps <= 0:
            fwd_reason = "n/m: consensus EPS not positive"
        else:
            fwd, fwd_reason = price[0] / data.forward_eps, None
    values["forward_pe"] = _mv(fwd, reason=fwd_reason, source="published_analyst_consensus")

    _parity(values, data, period, inputs, market_cap, price, pe, basis)

    checked = bool(data.check_scores)
    for axis in ("value", "future", "past", "health", "dividend"):
        score = data.check_scores.get(axis)
        values[f"checks_{axis}_score"] = _mv(
            score,
            reason=None
            if score is not None
            else "no applicable checks on this axis"
            if checked
            else "company checks not computed yet",
            source="company_check_snapshot",
        )
    values["checks_risk_flags"] = _mv(
        Decimal(data.check_risk_flags) if data.check_risk_flags is not None else None,
        reason=None if data.check_risk_flags is not None else "company checks not computed yet",
        source="company_check_snapshot",
    )
    values["qvm_score"] = _mv(None, reason="not computed until the universe is ranked")
    values["data_coverage"] = _mv(None)
    no_data = None
    if not any(
        v.value is not None
        for k, v in values.items()
        if k not in ("country", "mic", "currency", "asset_type")
    ):
        no_data = "No stored statements, prices or profile for this instrument"
    return Record(
        key=member.ticker,
        name=member.name,
        identity=identity,
        values=values,
        no_data_reason=no_data,
    )


def _at(series: Series, period_end: dt.date | None) -> Decimal | None:
    """The filed annual value for the period ending on `period_end` (within a week)."""
    if period_end is None:
        return None
    hit = nearest(series, period_end, 7)
    return abs(hit[1]) if hit else None


def _parity(
    values: dict[str, MetricValue],
    data: StockInputs,
    period: dict[str, Any] | None,
    inputs: dict[str, Decimal],
    market_cap: Decimal | None,
    price: tuple[Decimal, str] | None,
    pe: dt.date | None,
    basis: str | None,
) -> None:
    """Insight I10.5 metrics. The cash-flow lines are matched to the same annual period as the
    other statement metrics; a line that is not filed for that period leaves the metric unknown."""
    annual = period is not None and _is_annual(period["duration_days"])
    div = _at(data.dividends_paid, pe) if annual else None
    buy = _at(data.buybacks_paid, pe) if annual else None
    kw: dict[str, Any] = {"period_end": pe, "basis": basis, "source": "filed_statements"}
    no_year = (
        "latest filed period is not a full fiscal year"
        if period is not None
        else "no filed statements"
    )

    def over_cap(num: Decimal | None, what: str) -> MetricValue:
        if not annual:
            return _mv(None, reason=no_year, period_end=pe)
        if num is None:
            return _mv(None, reason=f"{what} not filed for the latest fiscal year", period_end=pe)
        if market_cap is None:
            return _mv(None, reason=values["market_cap"].reason or "no market cap", period_end=pe)
        if market_cap <= 0:
            return _mv(None, reason="n/m: non-positive market cap", period_end=pe)
        return _mv(num / market_cap, **kw)

    values["buyback_yield"] = over_cap(buy, "share repurchases paid")
    values["shareholder_yield"] = over_cap(
        div + buy if div is not None and buy is not None else None,
        "dividends paid" if div is None else "share repurchases paid",
    )
    ni = inputs.get("net_income")
    if not annual:
        values["payout_ratio"] = _mv(None, reason=no_year, period_end=pe)
    elif div is None:
        values["payout_ratio"] = _mv(
            None, reason="dividends paid not filed for the latest fiscal year", period_end=pe
        )
    elif ni is None or ni <= 0:
        values["payout_ratio"] = _mv(
            None, reason="n/m: net income missing or not positive", period_end=pe
        )
    else:
        values["payout_ratio"] = _mv(div / ni, **kw)
    cfo, capex = inputs.get("cfo"), inputs.get("capex")
    fcf = cfo - abs(capex) if cfo is not None and capex is not None else None
    if not annual:
        values["payout_ratio_fcf"] = _mv(None, reason=no_year, period_end=pe)
    elif div is None:
        values["payout_ratio_fcf"] = _mv(
            None, reason="dividends paid not filed for the latest fiscal year", period_end=pe
        )
    elif fcf is None or fcf <= 0:
        values["payout_ratio_fcf"] = _mv(
            None, reason="n/m: free cash flow missing or not positive", period_end=pe
        )
    else:
        values["payout_ratio_fcf"] = _mv(div / fcf, **kw)

    da = _at(data.depreciation, pe) if annual else None
    op, debt, cash = inputs.get("operating_income"), inputs.get("debt"), inputs.get("cash")
    if is_financial_issuer(data.sector, data.industry):
        values["net_debt_to_ebitda"] = _mv(
            None, reason="EBITDA is not meaningful for banks and insurers", period_end=pe
        )
    elif not annual:
        values["net_debt_to_ebitda"] = _mv(None, reason=no_year, period_end=pe)
    elif op is None or da is None or debt is None or cash is None:
        values["net_debt_to_ebitda"] = _mv(
            None,
            reason="missing operating income, depreciation and amortisation, debt or cash",
            period_end=pe,
        )
    elif op + da <= 0:
        values["net_debt_to_ebitda"] = _mv(None, reason="n/m: EBITDA not positive", period_end=pe)
    else:
        values["net_debt_to_ebitda"] = _mv((debt - cash) / (op + da), **kw)

    by_year = data.dividends_by_year
    src = "corporate_action (split-adjusted)"
    if not by_year:
        why = "no dividend history stored"
        values["dividend_growth_1y"] = _mv(None, reason=why, source=src)
        values["dividend_growth_5y"] = _mv(None, reason=why, source=src)
    else:
        now = by_year.get(0)
        for mid, years in (("dividend_growth_1y", 1), ("dividend_growth_5y", 5)):
            then = by_year.get(years)
            if now is None:
                values[mid] = _mv(None, reason="no dividend in the last 12 months", source=src)
            elif then is None or then <= 0:
                values[mid] = _mv(
                    None,
                    reason=f"no dividend in the 12 months {years} "
                    f"year{'s' if years > 1 else ''} earlier",
                    source=src,
                )
            elif years == 1:
                values[mid] = _mv(now / then - ONE, source=src)
            else:
                values[mid] = _mv(Decimal(str(float(now / then) ** (1 / years))) - ONE, source=src)

    buyers, sellers = data.insider_buyers_90d, data.insider_sellers_90d
    if buyers is None:
        values["insider_buy_ratio_90d"] = _mv(
            None, reason="no insider filings stored (US issuers only)", source="insider_transaction"
        )
    elif buyers + (sellers or 0) == 0:
        values["insider_buy_ratio_90d"] = _mv(
            None,
            reason="no open-market insider buys or sells in 90 days",
            source="insider_transaction",
        )
    else:
        values["insider_buy_ratio_90d"] = _mv(
            Decimal(buyers) / Decimal(buyers + (sellers or 0)), source="insider_transaction"
        )

    shares = data.shares[-1] if data.shares else None
    if data.forward_eps is None:
        values["forward_eps_growth"] = _mv(
            None, reason="estimates source not available", source="published_analyst_consensus"
        )
    elif ni is None or shares is None or shares[1] <= 0 or not annual:
        values["forward_eps_growth"] = _mv(
            None,
            reason="no latest annual net income and diluted share count",
            source="published_analyst_consensus",
        )
    elif price is None or (data.forward_currency is not None and data.forward_currency != price[1]):
        values["forward_eps_growth"] = _mv(
            None,
            reason="consensus currency differs from the listing currency",
            source="published_analyst_consensus",
        )
    elif market_cap is None:
        values["forward_eps_growth"] = _mv(
            None,
            reason="statement currency differs from the listing currency",
            source="published_analyst_consensus",
        )
    else:
        factor, _ = split_factor(shares, data.splits, data.restated_shares)
        trailing = ni / (shares[1] * factor)
        if trailing <= 0:
            values["forward_eps_growth"] = _mv(
                None, reason="n/m: trailing EPS not positive", source="published_analyst_consensus"
            )
        else:
            values["forward_eps_growth"] = _mv(
                data.forward_eps / trailing - ONE,
                period_end=pe,
                source="published_analyst_consensus",
            )


def finalize_coverage(records: Sequence[Record]) -> None:
    """Fill `data_coverage` once every other metric is set."""
    for r in records:
        known = sum(
            1 for m in NUMERIC_STOCK if (v := r.values.get(m)) is not None and v.value is not None
        )
        r.values["data_coverage"] = _mv(
            Decimal(known) / Decimal(len(NUMERIC_STOCK)), source="computed"
        )


def apply_scores(records: Sequence[Record], scores: Mapping[str, Any]) -> None:
    for r in records:
        s = scores.get(r.key)
        if s is None:
            continue
        if s.score is None:
            r.values["qvm_score"] = _mv(
                None,
                reason="abstains: " + ", ".join(s.abstained) + " lacks coverage",
                source="composite_qvm",
            )
        else:
            r.values["qvm_score"] = _mv(s.score, source="composite_qvm")


@dataclass
class FundInputs:
    isin: str
    name: str | None = None
    as_of: dt.date | None = None  # holdings date
    factsheet: dict[str, Any] = field(default_factory=dict)  # latest fund_factsheet row
    holdings: list[dict[str, Any]] = field(default_factory=list)  # etf_holding rows
    lookthrough_basis: str | None = None


def fund_record(data: FundInputs, as_of: dt.date) -> Record:
    f = data.factsheet
    values: dict[str, MetricValue] = {}
    src = "fund_factsheet"
    eq = f.get("equity_weight")
    asset_class = (
        None if eq is None else ("equity" if Decimal(eq) >= Decimal("0.85") else "mixed_or_other")
    )
    values["asset_class"] = _mv(
        asset_class,
        reason=None if asset_class else "holdings weights unknown",
        source="holdings (inferred)",
    )
    values["domicile"] = _mv(data.isin[:2], source="ISIN prefix")
    for key, col in (
        ("distribution_policy", "distribution_policy"),
        ("benchmark_name", "benchmark_name"),
        ("fund_currency", "fund_currency"),
    ):
        values[key] = _mv(
            f.get(col), reason=None if f.get(col) else "not in the factsheet", source=src
        )
    er = f.get("expense_ratio")
    values["expense_ratio"] = _mv(
        Decimal(er) if er is not None else None,
        reason=None if er is not None else "no expense ratio published",
        source=src,
    )
    ta = f.get("total_assets")
    values["total_assets"] = _mv(
        Decimal(ta) if ta is not None else None,
        reason=None if ta is not None else "fund size not published",
        source=src,
        period_end=f.get("total_assets_as_of"),
    )
    values["holdings_age_days"] = _mv(
        Decimal((as_of - data.as_of).days) if data.as_of else None,
        reason=None if data.as_of else "no holdings stored",
    )
    equity_rows = [h for h in data.holdings if h.get("asset_class") == "equity"]
    values["holdings_count"] = _mv(
        Decimal(len(data.holdings)) if data.holdings else None,
        reason=None if data.holdings else "no holdings stored",
        period_end=data.as_of,
    )
    cov = f.get("total_weight")
    values["lookthrough_coverage"] = _mv(
        Decimal(cov) if cov is not None else None,
        reason=None if cov is not None else "no weights reconciliation",
        source=src,
        period_end=data.as_of,
    )
    if equity_rows:

        def positions(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
            out: dict[str, Decimal] = {}
            for h in rows:
                if h.get("weight") is None:
                    continue
                # One issuer may hold several lines (share classes, ISINs): key by issuer id, then
                # a normalised name, before the security identifiers.
                name = " ".join(re.sub(r"[^\w ]", " ", str(h.get("name") or "")).casefold().split())
                key = (
                    (f"issuer:{h['issuer_id']}" if h.get("issuer_id") else None)
                    or (f"name:{name}" if name else None)
                    or h.get("isin")
                    or h.get("ticker")
                    or str(h.get("row_index"))
                )
                out[key] = out.get(key, ZERO) + Decimal(h["weight"])
            return out

        weights = positions(equity_rows)
        total = sum(weights.values(), ZERO)
        # Concentration is the whole fund's, so a 60/40 fund does not report its equity sleeve's.
        fund_weights = positions(list(data.holdings))
        fund_total = sum(fund_weights.values(), ZERO)
        fund_shares = (
            sorted((w / fund_total for w in fund_weights.values() if w > 0), reverse=True)
            if fund_total > 0
            else []
        )
        hhi = sum((s * s for s in fund_shares), ZERO)
        values["top10_weight"] = _mv(
            sum(fund_shares[:10], ZERO) if fund_shares else None,
            period_end=data.as_of,
            source="holdings",
        )
        values["effective_holdings"] = _mv(
            ONE / hhi if hhi else None, period_end=data.as_of, source="holdings"
        )

        def top(field_name: str) -> Decimal | None:
            acc: dict[str, Decimal] = {}
            for h in equity_rows:
                label = h.get(field_name)
                if label:
                    acc[label] = acc.get(label, ZERO) + Decimal(h["weight"])
            return max(acc.values()) / total if acc and total > 0 else None

        values["top_sector_weight"] = _mv(
            top("sector"),
            reason=None if top("sector") is not None else "holdings carry no sector",
            period_end=data.as_of,
        )
        values["top_country_weight"] = _mv(
            top("country"),
            reason=None if top("country") is not None else "holdings carry no country",
            period_end=data.as_of,
        )
    else:
        for k in ("top10_weight", "effective_holdings", "top_sector_weight", "top_country_weight"):
            values[k] = _mv(None, reason="no equity holdings stored")
    values["tracking_difference"] = _mv(None, reason=TRACKING_UNSET)
    return Record(
        key=data.isin,
        name=data.name or f.get("fund_name"),
        identity={
            "domicile": data.isin[:2],
            "currency": f.get("fund_currency"),
            "lookthrough_basis": data.lookthrough_basis,
        },
        values=values,
        no_data_reason=None
        if (f or data.holdings)
        else "No factsheet or holdings stored for this fund",
    )
