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
from steward.analytics.returns import to_major_unit
from steward.discovery.engine import Record
from steward.discovery.metrics import FUND_CATALOGUE, STOCK_CATALOGUE, MetricValue
from steward.discovery.universe import UniverseMember
from steward.peer_metrics import latest_period
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
    statement_currency: str | None = None


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


def _price_major(member: UniverseMember, closes: Series) -> tuple[Decimal, str] | None:
    if not closes or not member.currency:
        return None
    return to_major_unit(closes[-1][1], member.currency)


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
        inputs = {k: v["value"] for k, v in period["inputs"].items()}
    pe, basis = (period["period_end"], period["basis"]) if period else (None, None)
    statement_ccy = data.statement_currency or (period.get("unit") if period else None)
    if isinstance(statement_ccy, str) and not re.fullmatch(r"[A-Z]{3}", statement_ccy):
        statement_ccy = None

    # Market cap needs price and shares in one currency.
    price = _price_major(member, data.closes)
    shares = data.shares[-1] if data.shares else None
    market_cap: Decimal | None = None
    cap_reason: str | None = None
    if price is None:
        cap_reason = "no stored close or trading currency"
    elif shares is None:
        cap_reason = "no stored share count"
    elif statement_ccy and price[1] != statement_ccy:
        cap_reason = f"price currency {price[1]} differs from statement currency {statement_ccy}"
    else:
        market_cap = price[0] * shares[1]
    values["market_cap"] = _mv(
        market_cap,
        reason=cap_reason,
        period_end=data.closes[-1][0] if data.closes else None,
        source="price_bar x filed shares",
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
        ratios.ev_to_ebit(ev, inputs.get("operating_income")),
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
    values["cash_runway_years"] = _ratio_mv(
        ratios.cash_runway_years(cash, cfo), period_end=pe, basis=basis, source="filed_statements"
    )

    # Prices.
    for mid, days in (("return_1y", 365), ("return_3y", 3 * 365), ("return_5y", 5 * 365)):
        r, why = _return(data.closes, as_of, days)
        values[mid] = _mv(
            r,
            reason=why,
            period_end=data.closes[-1][0] if data.closes else None,
            source="price_bar (adjusted)",
        )
    tail = [(d, float(c)) for d, c in data.closes[-253:]]
    vol = _volatility(tail) if len(tail) >= 200 else None
    values["volatility_1y"] = _mv(
        Decimal(str(vol)) if vol is not None else None,
        reason=None if vol is not None else "fewer than 200 sessions of stored closes",
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
    elif data.dividend_currency and data.dividend_currency != price[1]:
        dy, dy_reason = (
            None,
            f"dividend currency {data.dividend_currency} differs from price currency {price[1]}",
        )
    else:
        dy, dy_reason = data.dividends_ttm / price[0], None
    values["dividend_yield"] = _mv(dy, reason=dy_reason, source="corporate_action")
    values["avg_daily_turnover"] = _mv(
        data.turnover[0] if data.turnover else None,
        reason=None if data.turnover else "no FITRS liquidity record",
        source="listing_liquidity",
    )
    values["years_since_listing"] = _mv(
        (Decimal((as_of - data.first_trade).days) / 365) if data.first_trade else None,
        reason=None if data.first_trade else "first trade date not published",
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
        elif data.forward_eps <= 0:
            fwd_reason = "n/m: consensus EPS not positive"
        else:
            fwd, fwd_reason = price[0] / data.forward_eps, None
    values["forward_pe"] = _mv(fwd, reason=fwd_reason, source="published_analyst_consensus")

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
        weights: dict[str, Decimal] = {}
        for h in equity_rows:
            key = h.get("isin") or h.get("name") or h.get("ticker") or str(h.get("row_index"))
            weights[key] = weights.get(key, ZERO) + Decimal(h["weight"])
        total = sum(weights.values(), ZERO)
        shares = (
            sorted((w / total for w in weights.values() if w > 0), reverse=True)
            if total > 0
            else []
        )
        hhi = sum((s * s for s in shares), ZERO)
        values["top10_weight"] = _mv(
            sum(shares[:10], ZERO) if shares else None, period_end=data.as_of, source="holdings"
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
    values["tracking_difference"] = _mv(
        None, reason=FUND_CATALOGUE["tracking_difference"].no_source_reason
    )
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
