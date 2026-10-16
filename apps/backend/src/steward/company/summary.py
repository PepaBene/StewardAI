"""One read for the company page's header and sidebar: identity, last price and day change, the
default fair value, your position if held and the check axes. It aggregates what the page used to
fetch in pieces (overview, checks, holdings) and adds nothing new; a missing part is null with a
reason."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.checks.contract import AxisScore, score_text
from steward.checks.service import company_checks
from steward.company.fair_values import LABELS, CompanyFairValues, fair_values
from steward.company.quote import Quote, load_member, load_quote
from steward.portfolio.read import Holding, portfolio_holdings

_SHORT = re.compile(r"^([A-Z0-9.]+?)([a-z])?_(?:[A-Z]{2}_)?EQ$")


def short_symbol(ticker: str) -> str:
    """Trading212's 'AAPL_US_EQ' as 'AAPL' ('b' share classes on Nordic listings as 'VOLV B')."""
    m = _SHORT.match(ticker)
    if not m:
        return ticker
    return f"{m.group(1)} B" if m.group(2) == "b" else m.group(1)


class SummaryFairValue(BaseModel):
    value: Decimal
    currency: str | None
    source: str
    basis_label: str
    as_of: dt.date | None
    upside: Decimal | None


class SummaryPosition(BaseModel):
    quantity: float
    value_account: float
    account_currency: str
    value_base: float
    ppl: float = Field(description="Unrealised P/L in the account currency.")
    ppl_pct: float | None = Field(description="Unrealised P/L over cost, a fraction.")
    weight: float | None = Field(description="Share of the portfolio, a fraction.")


class SummaryAxis(BaseModel):
    axis: str
    label: str
    score: Decimal | None
    max: int
    text: str = Field(description="'4 of 5 applicable', or why there is no score.")


class CompanySummary(BaseModel):
    ticker: str
    symbol: str
    name: str | None
    exchange: str | None
    mic: str | None
    currency: str | None
    country: str | None
    held: bool
    price: Quote
    fair_value: SummaryFairValue | None
    position: SummaryPosition | None
    axes: list[SummaryAxis]
    checks_status: str | None
    reasons: list[str]


def summary_axes(axes: list[AxisScore]) -> list[SummaryAxis]:
    return [
        SummaryAxis(axis=a.axis, label=a.label, score=a.score, max=a.max, text=score_text(a))
        for a in axes
    ]


def default_fair_value(fv: CompanyFairValues) -> SummaryFairValue | None:
    item = next((i for i in fv.values if i.is_default and i.value is not None), None)
    if item is None or item.value is None:
        return None
    return SummaryFairValue(
        value=item.value, currency=item.currency, source=item.source,
        basis_label=LABELS.get(item.basis or item.source, item.label), as_of=item.as_of,
        upside=item.upside,
    )  # fmt: skip


def position_of(h: Holding | None) -> SummaryPosition | None:
    if h is None:
        return None
    return SummaryPosition(
        quantity=h.quantity, value_account=h.value_account, account_currency=h.account_currency,
        value_base=h.value_base, ppl=h.ppl, ppl_pct=h.ppl_pct, weight=h.weight,
    )  # fmt: skip


async def company_summary(
    pool: asyncpg.Pool, owner_id: Any, ticker: str
) -> CompanySummary | None:
    """None when the ticker is not in the universe."""
    member = await load_member(pool, ticker)
    if member is None:
        return None
    reasons: list[str] = []
    price = await load_quote(pool, member)
    if price.reason:
        reasons.append(f"price: {price.reason}")
    fv = await fair_values(pool, owner_id, ticker, price)
    fair = default_fair_value(fv) if fv else None
    if fair is None:
        reasons.append("no fair value recorded; open Valuation to compute one")
    checks = await company_checks(pool, owner_id, ticker)
    if checks is None:
        reasons.append("checks unavailable")
    elif checks.reason:
        reasons.append(f"checks: {checks.reason}")
    holdings = await portfolio_holdings(pool)
    held = next((h for h in holdings.holdings if h.ticker == ticker), None)
    return CompanySummary(
        ticker=ticker, symbol=short_symbol(ticker), name=member.name, exchange=member.exchange,
        mic=member.mic, currency=price.currency or member.currency, country=member.country,
        held=held is not None, price=price, fair_value=fair, position=position_of(held),
        axes=summary_axes(checks.axes) if checks else [],
        checks_status=checks.status if checks else None, reasons=reasons,
    )  # fmt: skip
