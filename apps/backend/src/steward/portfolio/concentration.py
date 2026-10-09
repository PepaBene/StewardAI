"""Positions above the single-stock cap, and what the strategy says to do (principle 4f; plan Q5.5).

Principle 4f judges a position above the cap on its outlook, not its size: while the thesis and
research still project growth, hold it, add nothing and let deposits and other positions dilute
it; once the projected growth is exhausted or the thesis is weakened, trim it back to 8 %; above
15 % it is trimmed to 10 % regardless of outlook. The outlook is the owner's call, so this shows
both paths for each position: how long deposits alone take to bring it under the cap, and how
much to sell to reach the trim weight, in money and whole shares.
"""

from __future__ import annotations

import math
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.store import load_active
from steward.config.strategy import PositionWeightRule, StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.portfolio import exante as ea
from steward.quant import sizing as sz

METHOD_VERSION = "concentration-1"
DEFAULT_CAP = 0.10
TRIM_TO = 0.08
BACKSTOP = 0.15
BACKSTOP_TO = 0.10
HORIZON_MONTHS = 36
DEPOSIT_LOOKBACK_DAYS = 365


class DilutionPoint(BaseModel):
    month: int
    weight: float


class OverCap(BaseModel):
    ticker: str
    name: str | None
    value: float
    weight: float
    cap: float
    excess_value: float = Field(description="Value above the cap.")
    zone: Literal["above_cap", "above_backstop"]
    months_to_cap: int | None = Field(
        description="Months of deposits (all going elsewhere, no price moves) until the weight "
        "is at or under the cap; null when not reached in three years."
    )
    path: list[DilutionPoint]
    trim_weight: float = Field(
        description="Where a trim takes it: 8 %, or 10 % above the backstop."
    )
    trim_value: float
    trim_shares: int | None
    price_base: float | None
    thesis_gaps: list[str] = Field(default_factory=list)
    advice: str


class ConcentrationReport(BaseModel):
    method_version: str = METHOD_VERSION
    base_currency: str | None
    cap: float
    trim_to: float
    backstop: float
    monthly_deposit: float
    deposit_source: str
    total_value: float
    positions: list[OverCap]
    exempt: list[str] = Field(
        default_factory=list, description="Over the cap but exempt (core ETFs)."
    )
    reason: str | None = None


def cap_of(strategy: StrategyConfig, default: float = DEFAULT_CAP) -> float:
    """The strictest single-position cap among the strategy's rules that apply to all stocks."""
    caps = [
        r.value
        for r in strategy.enabled_rules
        if isinstance(r, PositionWeightRule) and r.ticker is None and r.operator in ("<=", "<")
    ]
    return min(caps) if caps else default


def assess(
    ticker: str,
    name: str | None,
    value: float,
    total: float,
    cap: float,
    monthly_deposit: float,
    trim_to: float = TRIM_TO,
    backstop: float = BACKSTOP,
    price_base: float | None = None,
    thesis_gaps: list[str] | None = None,
) -> OverCap | None:
    """The view of one position; None when it is at or under the cap (pure)."""
    weight = value / total if total > 0 else 0.0
    if weight <= cap + 1e-12:
        return None
    zone: Literal["above_cap", "above_backstop"] = (
        "above_backstop" if weight > backstop + 1e-12 else "above_cap"
    )
    target = BACKSTOP_TO if zone == "above_backstop" else trim_to
    d = sz.dilution_path(value, total, monthly_deposit, cap, HORIZON_MONTHS)
    trim_value = sz.trim_to(value, total, target)
    shares = math.ceil(trim_value / price_base) if price_base and price_base > 0 else None
    if zone == "above_backstop":
        advice = (
            f"Above the {backstop:.0%} backstop: trim to {BACKSTOP_TO:.0%} whatever the outlook "
            f"(sell about {trim_value:,.0f})."
        )
    elif d.months_to_cap is not None:
        advice = (
            f"If the thesis still projects growth: hold, add nothing, and deposits alone bring it "
            f"under {cap:.0%} in {d.months_to_cap} months. If it is weakened or exhausted: trim "
            f"to {trim_to:.0%} (sell about {trim_value:,.0f})."
        )
    else:
        advice = (
            f"If the thesis still projects growth: hold and add nothing; at {monthly_deposit:,.0f} "
            f"a month deposits do not bring it under {cap:.0%} within {HORIZON_MONTHS // 12} "
            f"years. If it is weakened or exhausted: trim to {trim_to:.0%} (sell about "
            f"{trim_value:,.0f})."
        )
    return OverCap(
        ticker=ticker,
        name=name,
        value=value,
        weight=weight,
        cap=cap,
        excess_value=value - cap * total,
        zone=zone,
        months_to_cap=d.months_to_cap,
        path=[DilutionPoint(month=s.month, weight=s.weight) for s in d.path if s.month % 3 == 0],
        trim_weight=target,
        trim_value=trim_value,
        trim_shares=shares,
        price_base=price_base,
        thesis_gaps=thesis_gaps or [],
        advice=advice,
    )


async def average_monthly_deposit(pool: asyncpg.Pool) -> float | None:
    """Deposits over the last twelve months divided by twelve, from the stored cash movements
    (account currency, which is the base currency for this owner); None without any."""
    row = await pool.fetchrow(
        "select coalesce(sum(amount), 0) as total, count(*) as n from public.transaction"
        " where type = 'cash' and upper(subtype) = 'DEPOSIT'"
        " and occurred_at >= now() - make_interval(days => $1)",
        DEPOSIT_LOOKBACK_DAYS,
    )
    if row is None or not row["n"]:
        return None
    return float(row["total"]) / 12.0


async def concentration_report(
    pool: asyncpg.Pool,
    monthly_deposit: float | None = None,
    cap: float | None = None,
    trim_to: float = TRIM_TO,
) -> ConcentrationReport:
    book, strategy = await ea.book_from_snapshot(pool)
    if book is None or strategy is None:
        return ConcentrationReport(
            base_currency=None,
            cap=cap or DEFAULT_CAP,
            trim_to=trim_to,
            backstop=BACKSTOP,
            monthly_deposit=monthly_deposit or 0.0,
            deposit_source="none",
            total_value=0.0,
            positions=[],
            reason="no portfolio snapshot yet; run the portfolio sync",
        )
    used_cap = cap if cap is not None else cap_of(strategy)
    source = "request"
    deposit = monthly_deposit
    if deposit is None:
        deposit = await average_monthly_deposit(pool)
        source = "average of the last 12 months of deposits" if deposit is not None else "none"
        deposit = deposit or 0.0
    rows = await pool.fetch(
        "select p.ticker, p.quantity, p.value_base, i.instrument_type"
        " from public.position_snapshot p left join public.instrument i on i.t212_ticker = p.ticker"
        " where p.snapshot_date = (select max(snapshot_date) from public.position_snapshot)"
    )
    info = {r["ticker"]: r for r in rows}
    theses = await load_active(pool, "theses", ThesesConfig)
    gaps = {t.ticker: t.missing_requirements() for t in (theses.body.theses if theses else [])}
    out: list[OverCap] = []
    exempt: list[str] = []
    for p in sorted(book.positions, key=lambda x: -x.value_base):
        r = info.get(p.ticker)
        qty = float(r["quantity"] or 0) if r else 0.0
        found = assess(
            p.ticker,
            p.name,
            p.value_base,
            book.total_value,
            used_cap,
            deposit,
            trim_to,
            BACKSTOP,
            p.value_base / qty if qty > 0 else None,
            gaps.get(p.ticker),
        )
        if found is None:
            continue
        if r is not None and (r["instrument_type"] or "").upper() == "ETF":
            exempt.append(p.ticker)
            continue
        out.append(found)
    return ConcentrationReport(
        base_currency=book.base_currency,
        cap=used_cap,
        trim_to=trim_to,
        backstop=BACKSTOP,
        monthly_deposit=deposit,
        deposit_source=source,
        total_value=book.total_value,
        positions=out,
        exempt=exempt,
    )
