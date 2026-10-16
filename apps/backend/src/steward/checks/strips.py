# ruff: noqa: E501
"""Ranked dot strips per axis for the Allocation view (insight plan I4.5).

Each strip places every holding on one scale for one metric: price against fair value on a chosen
basis, forecast earnings growth, return on equity, debt to equity and the forward dividend yield.
Values are read from what is already computed (the cached check results, the fair-value read model
and the dividend forecast); nothing is recomputed here. The portfolio reference line is the
value-weighted mean over the holdings that have a value; no benchmark constituents are stored, so
the benchmark line is absent with the reason.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.checks import load_checks

Q4 = Decimal("0.0001")
BENCHMARK_NOTE = "No benchmark line: benchmark constituents and their fundamentals are not stored."


class StripDot(BaseModel):
    ticker: str
    name: str | None
    weight: Decimal
    value: Decimal | None
    reason: str | None = None


class StripZone(BaseModel):
    lo: Decimal | None
    hi: Decimal | None
    tone: Literal["gain", "loss", "neutral"]
    label: str


class Strip(BaseModel):
    metric: str
    label: str
    unit: Literal["fraction", "multiple"]
    basis: str
    dots: list[StripDot]
    portfolio: Decimal | None = Field(
        description="Value-weighted mean over the holdings with a value."
    )
    covered_weight: Decimal
    benchmark: Decimal | None = None
    benchmark_note: str = BENCHMARK_NOTE
    zones: list[StripZone]


class PortfolioStrips(BaseModel):
    as_of: Any
    valuation_basis: str
    bases: dict[str, str]
    strips: list[Strip]
    note: str = "Each dot is one holding; the line is the portfolio's value-weighted mean over the dots placed."


def weighted(dots: list[StripDot]) -> tuple[Decimal | None, Decimal]:
    have = [d for d in dots if d.value is not None]
    w = sum((d.weight for d in have), Decimal(0))
    if w <= 0:
        return None, Decimal(0)
    return (
        sum((d.weight * d.value for d in have if d.value is not None), Decimal(0)) / w
    ).quantize(Q4), w.quantize(Q4)


def strip(
    metric: str,
    label: str,
    unit: Literal["fraction", "multiple"],
    basis: str,
    dots: list[StripDot],
    zones: list[StripZone],
) -> Strip:
    mean, cov = weighted(dots)
    dots = sorted(
        dots, key=lambda d: (d.value is None, d.value if d.value is not None else Decimal(0))
    )
    return Strip(
        metric=metric,
        label=label,
        unit=unit,
        basis=basis,
        dots=dots,
        portfolio=mean,
        covered_weight=cov,
        zones=zones,
    )


def _check_value(result: dict[str, Any], check_id: str) -> tuple[Decimal | None, str | None]:
    for c in [*result.get("checks", []), *result.get("risks", [])]:
        if c.get("id") == check_id:
            v = c.get("value")
            if v is None:
                return None, c.get("sentence")
            return Decimal(str(v)), None
    return None, "not checked yet"


async def portfolio_strips(
    pool: asyncpg.Pool, owner_id: Any, basis: str = "headline"
) -> PortfolioStrips:
    from steward.dividends.service import portfolio_dividends
    from steward.valuation.fair_value_view import BASIS_LABELS, portfolio_fair_value

    rules, _ = await load_checks(pool)
    lim = rules.limits
    fv = await portfolio_fair_value(pool, owner_id, basis)
    total = fv.portfolio_value
    weight = {
        h.ticker: (
            h.value_account / total if h.value_account is not None and total > 0 else Decimal(0)
        )
        for h in fv.holdings
    }
    names = {h.ticker: h.name for h in fv.holdings}
    tickers = list(weight)
    rows = await pool.fetch(
        """select distinct on (ticker) ticker, result from public.company_check_snapshot
            where owner_id = $1 and ticker = any($2::text[]) order by ticker, computed_at desc""",
        owner_id, tickers,
    )  # fmt: skip
    results = {
        r["ticker"]: (json.loads(r["result"]) if isinstance(r["result"], str) else r["result"])
        for r in rows
    }

    def from_check(check_id: str) -> list[StripDot]:
        out: list[StripDot] = []
        for t in tickers:
            res = results.get(t)
            v, why = _check_value(res, check_id) if res else (None, "not checked yet")
            out.append(
                StripDot(
                    ticker=t,
                    name=names.get(t),
                    weight=weight[t].quantize(Q4),
                    value=v.quantize(Q4) if v is not None else None,
                    reason=why,
                )
            )
        return out

    valuation = [
        StripDot(
            ticker=h.ticker,
            name=h.name,
            weight=weight[h.ticker].quantize(Q4),
            value=h.upside,
            reason=h.reason,
        )
        for h in fv.holdings
    ]
    div = await portfolio_dividends(pool, owner_id)
    yields = {h.ticker: h for h in div.holdings}
    forward_yield = [
        StripDot(
            ticker=t, name=names.get(t), weight=weight[t].quantize(Q4),
            value=(yields[t].yield_on_price if t in yields and yields[t].yield_on_price is not None else (Decimal(0) if t in yields and not yields[t].pays else None)),
            reason=(yields[t].reason if t in yields else "no dividend data"),
        )
        for t in tickers
    ]  # fmt: skip
    strips = [
        strip("valuation", "Price against fair value", "fraction", BASIS_LABELS.get(basis, basis), valuation, [
            StripZone(lo=None, hi=Decimal(0), tone="loss", label="Price above fair value"),
            StripZone(lo=Decimal(0), hi=None, tone="gain", label="Price below fair value"),
        ]),
        strip("forecast_growth", "Forecast earnings growth a year", "fraction", "Published estimates, next years", from_check("future_earnings_vs_risk_free"), [
            StripZone(lo=None, hi=Decimal(0), tone="loss", label="Shrinking"),
        ]),
        strip("roe", "Return on equity", "fraction", f"Latest filed year; floor {lim.roe_floor:.0%}", from_check("past_roe"), [
            StripZone(lo=None, hi=Decimal(str(lim.roe_floor)), tone="loss", label=f"Below the {lim.roe_floor:.0%} floor"),
        ]),
        strip("debt_to_equity", "Debt to equity", "fraction", f"Latest balance sheet; limit {lim.debt_to_equity_max:.0%}", from_check("health_debt_to_equity"), [
            StripZone(lo=Decimal(str(lim.debt_to_equity_max)), hi=None, tone="loss", label=f"Above the {lim.debt_to_equity_max:.0%} limit"),
        ]),
        strip("forecast_yield", "Dividend yield, next 12 months", "fraction", "Declared plus estimated over today's price", forward_yield, []),
    ]  # fmt: skip
    return PortfolioStrips(as_of=fv.as_of, valuation_basis=basis, bases=fv.bases, strips=strips)
