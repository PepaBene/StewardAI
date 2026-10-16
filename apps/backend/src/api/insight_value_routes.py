# ruff: noqa: E501 - response text stays on one line each
"""Fair value against price for the portfolio and the watchlist (insight plan I5.1, I5.2, I5.5),
the insight coverage audit (I0.1) and the endpoint access verdicts (I0.2). Owner-only reads."""

import datetime as dt
import json
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from api.auth import Owner
from api.db import Pool
from steward.config.checks import load_checks
from steward.ops.clocks import ClockRetrospective, clock_retrospective
from steward.ops.insight_coverage import InsightCoverage, insight_coverage
from steward.sources.consensus import access_verdicts
from steward.sources.consensus_view import (
    ConsensusView,
    PortfolioConsensus,
    consensus_view,
    portfolio_consensus,
)
from steward.valuation.fair_value_view import (
    BASIS_LABELS,
    Basis,
    PortfolioFairValue,
    StepPoint,
    convert,
    latest_fx,
    portfolio_fair_value,
)

router = APIRouter()
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


@router.get("/portfolio/fair-value", response_model=PortfolioFairValue, tags=["portfolio"])
async def get_portfolio_fair_value(
    pool: Pool,
    owner: Owner,
    basis: Annotated[
        Basis, Query(description="Fair-value basis; switching it saves nothing.")
    ] = "dcf_default",
) -> PortfolioFairValue:
    """Per holding: price, fair value on the chosen basis, premium or discount, age of the inputs
    and the stale flag; portfolio totals over covered holdings, and the history from the stored
    fair values joined to portfolio value by date (it starts at the first recorded value)."""
    return await portfolio_fair_value(pool, owner.user_id, basis)


class BasisValue(BaseModel):
    basis: str
    label: str
    value: Decimal | None
    currency: str | None
    upside: Decimal | None
    as_of: dt.date
    detail: dict[str, Any]


class WatchFairValue(BaseModel):
    ticker: str
    list_name: str
    name: str | None
    price: Decimal | None
    price_currency: str | None
    price_date: dt.date | None
    default_basis: str | None
    bases: list[BasisValue]
    history: list[StepPoint]


ORDER = (
    "dcf_base",
    "multiple_base",
    "thesis_target",
    "dcf_default",
    "consensus_target",
    "peer_multiple",
)


@router.get("/watchlist/fair-value", response_model=list[WatchFairValue], tags=["watchlist"])
async def watchlist_fair_value(pool: Pool, owner: Owner) -> list[WatchFairValue]:
    """Every recorded basis per watchlist name with its upside against the latest close; the
    default is your latest saved scenario, then the thesis target, then the app's default DCF."""
    items = await pool.fetch(
        """select w.ticker, w.list_name, u.name, u.price_symbol, u.currency from public.watchlist_item w
             left join public.investment_universe u on u.ticker = w.ticker
            where w.owner_id = $1 order by w.list_name, w.ticker""",
        owner.user_id,
    )
    home, rates = await latest_fx(
        pool,
        await pool.fetchval(
            "select account_currency from public.account_snapshot order by snapshot_date desc limit 1"
        ),
    )
    out: list[WatchFairValue] = []
    for it in items:
        bar = await pool.fetchrow(
            "select date, close from public.price_bar where symbol = $1 order by date desc limit 1",
            it["price_symbol"] or it["ticker"],
        )
        price, pccy = (bar["close"], it["currency"]) if bar else (None, it["currency"])
        rows = await pool.fetch(
            """select distinct on (basis) basis, value, currency, as_of, detail from public.fair_value_point
                where owner_id = $1 and ticker = $2 order by basis, as_of desc""",
            owner.user_id, it["ticker"],
        )  # fmt: skip
        bases: list[BasisValue] = []
        for r in sorted(rows, key=lambda x: ORDER.index(x["basis"]) if x["basis"] in ORDER else 99):
            v = convert(r["value"], r["currency"] or pccy, pccy, home, rates) if pccy else None
            d = r["detail"]
            bases.append(BasisValue(
                basis=r["basis"], label=BASIS_LABELS.get(r["basis"], r["basis"].replace("_", " ")), value=v.quantize(Decimal("0.0001")) if v is not None else None,
                currency=pccy, upside=(v / price - 1).quantize(Decimal("0.0001")) if v is not None and price else None, as_of=r["as_of"],
                detail=json.loads(d) if isinstance(d, str) else dict(d or {}),
            ))  # fmt: skip
        default = next((b.basis for b in bases if b.basis in ORDER), None)
        hist: list[StepPoint] = []
        if default:
            for p in await pool.fetch(
                "select as_of, value, currency from public.fair_value_point where owner_id = $1 and ticker = $2 and basis = $3 order by as_of",
                owner.user_id, it["ticker"], default,
            ):  # fmt: skip
                v = convert(p["value"], p["currency"] or pccy, pccy, home, rates) if pccy else None
                if v is not None and (not hist or hist[-1].value != v.quantize(Decimal("0.0001"))):
                    hist.append(StepPoint(as_of=p["as_of"], value=v.quantize(Decimal("0.0001"))))
        out.append(WatchFairValue(
            ticker=it["ticker"], list_name=it["list_name"], name=it["name"], price=price, price_currency=pccy,
            price_date=bar["date"] if bar else None, default_basis=default, bases=bases, history=hist,
        ))  # fmt: skip
    return out


@router.get("/quality/insight-coverage", response_model=InsightCoverage, tags=["quality"])
async def get_insight_coverage(pool: Pool, owner: Owner) -> InsightCoverage:
    """Per holding and planned feature (dividends, statement inputs, estimates, consensus, peers,
    fair value): ready, partial or unavailable with the reason, from the stored rows."""
    return await insight_coverage(pool, owner.user_id)


@router.get("/quality/insight-clocks", response_model=ClockRetrospective, tags=["quality"])
async def get_insight_clocks(pool: Pool, owner: Owner) -> ClockRetrospective:
    """The 3-, 6- and 12-month retrospective of the insight clocks: dividend forecast accuracy,
    consensus hit rate and the share of holdings with a usable fair-value history."""
    return await clock_retrospective(pool, owner.user_id)


class AccessVerdict(BaseModel):
    provider: str
    endpoint: str
    state: str
    tested_at: dt.datetime | None
    detail: str | None
    status_code: int | None


@router.get("/sources/insight-access", response_model=list[AccessVerdict], tags=["sources"])
async def insight_access(pool: Pool, _owner: Owner) -> list[AccessVerdict]:
    """The access verdict, with its date, of each endpoint the insight features depend on."""
    return [
        AccessVerdict(**{k: r.get(k) for k in AccessVerdict.model_fields})
        for r in await access_verdicts(pool)
    ]


@router.get("/companies/{ticker}/consensus", response_model=ConsensusView, tags=["companies"])
async def get_company_consensus(ticker: TickerPath, pool: Pool, _owner: Owner) -> ConsensusView:
    """Latest analyst target with spread and agreement, the stored history, the chart data once
    enough snapshots exist, and the track record once snapshots are 12 months old (insight I8).
    A holding with no reachable source says so; nothing is synthesised."""
    rules, _ = await load_checks(pool)
    fv = rules.fair_value
    return await consensus_view(
        pool,
        ticker,
        close=fv.agreement_close,
        wide=fv.agreement_wide,
        min_snapshots=fv.consensus_chart_min_snapshots,
    )


@router.get("/portfolio/consensus", response_model=PortfolioConsensus, tags=["portfolio"])
async def get_portfolio_consensus(pool: Pool, _owner: Owner) -> PortfolioConsensus:
    """Per holding: consensus coverage (complete, partial, unavailable) with the source and reason,
    and the latest mean target, upside and agreement where one exists."""
    rules, _ = await load_checks(pool)
    return await portfolio_consensus(
        pool, close=rules.fair_value.agreement_close, wide=rules.fair_value.agreement_wide
    )
