"""Response models of the X-Ray endpoints and the functions that assemble them."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from steward.funds.aggregate import FundAsOf, Unknown
from steward.lookthrough.read import is_fund_type
from steward.xray.intersection import XrayIntersection, intersection
from steward.xray.metrics import (
    XrayConcentration,
    XrayFees,
    XrayItem,
    build_fees,
    concentration_levels,
)
from steward.xray.service import Loaded, aggregate, benchmark_result
from steward.xray.views import XrayView, build_views

METHOD = (
    "Every direct holding and every published fund row is one weighted leaf (share of total "
    "portfolio value), merged by canonical issuer. Cash, a fund's own cash and derivatives, a fund "
    "that is not looked through and the part of a fund its rows do not account for are separate "
    "buckets in every view; unknown weight is never spread over known holdings. Fund holdings are "
    "as of their own dates, not the portfolio date."
)


class XrayHolding(BaseModel):
    ticker: str
    name: str | None
    weight: float = Field(description="Share of total portfolio value.")
    is_fund: bool
    looked_through: bool | None = Field(
        description="For a fund: whether its holdings are stored. None for a stock."
    )


class XrayResponse(BaseModel):
    as_of: str
    snapshot_date: dt.date | None
    base_currency: str | None
    total_value: float
    available: bool
    holdings: list[XrayHolding]
    cash_weight: float
    views: list[XrayView]
    funds: list[FundAsOf]
    stale_funds: list[str]
    unknown: Unknown | None
    fees: XrayFees | None
    scored_issuers: int
    issuers_total: int
    method: str
    notes: list[str]


class XrayConcentrationResponse(BaseModel):
    as_of: str
    levels: list[XrayConcentration]
    single_issuer: XrayItem | None
    benchmark_label: str | None
    benchmark_note: str | None
    notes: list[str]


def holdings(loaded: Loaded) -> list[XrayHolding]:
    pf = loaded.data.portfolio
    total = loaded.leafset.total
    if pf is None or total <= 0:
        return []
    settings = loaded.settings
    out = []
    for p in sorted(pf.positions, key=lambda p: -p.value_base):
        cfg = settings.fund(p.isin)
        is_fund = cfg is not None or is_fund_type(p.security_type)
        out.append(
            XrayHolding(
                ticker=p.ticker,
                name=p.name,
                weight=p.value_base / total,
                is_fund=is_fund,
                looked_through=(bool(cfg and p.isin in loaded.data.snapshots) if is_fund else None),
            )
        )
    return out


def xray_response(loaded: Loaded) -> XrayResponse:
    ctx = loaded.analysis.ctx
    leafset = ctx.leafset
    pf = ctx.data.portfolio
    agg = aggregate(loaded, top=1)
    return XrayResponse(
        as_of=loaded.day.isoformat(),
        snapshot_date=pf.snapshot_date if pf else None,
        base_currency=pf.base_currency if pf else None,
        total_value=leafset.total,
        available=bool(leafset.leaves),
        holdings=holdings(loaded),
        cash_weight=pf.cash_base / leafset.total if pf and leafset.total > 0 else 0.0,
        views=build_views(ctx) if leafset.leaves else [],
        funds=agg.funds,
        stale_funds=agg.stale_funds,
        unknown=agg.unknown,
        fees=build_fees(leafset),
        scored_issuers=loaded.scored,
        issuers_total=loaded.issuers_total,
        method=METHOD,
        notes=[*loaded.notes, *agg.notes],
    )


def concentration_response(loaded: Loaded) -> XrayConcentrationResponse:
    result, actives = benchmark_result(loaded)
    levels = concentration_levels(loaded.leafset, actives=actives)
    issuer = next((c for c in levels if c.level == "issuer"), None)
    return XrayConcentrationResponse(
        as_of=loaded.day.isoformat(),
        levels=levels,
        single_issuer=issuer.largest if issuer else None,
        benchmark_label=result.label if result.available else None,
        benchmark_note=None
        if result.available
        else (result.reason or "No benchmark comparison is available."),
        notes=[
            "HHI and effective holdings are measured on each level's identified weight; unknown "
            "fund weight, unlabelled equity and cash are outside them and shown beside them.",
            *loaded.notes,
        ],
    )


def intersection_response(loaded: Loaded) -> XrayIntersection:
    return intersection(aggregate(loaded), limit=100)
