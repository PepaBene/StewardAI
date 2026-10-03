"""Authenticated API routes for recent quotes: every holding's recent closes in one call,
the market strip (held currencies' reference rates and the configured indices), and
intraday prices (Yahoo Finance, unofficial) for a holding and for the portfolio."""

import datetime as dt
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Path, Query

from api.auth import Owner
from api.db import Pool
from steward import __version__
from steward.portfolio.correlation import PortfolioCorrelation, portfolio_correlation
from steward.portfolio.quotes import HoldingsPrices, MarketStrip, holdings_prices, market_strip
from steward.reference.intraday import (
    HoldingIntraday,
    PortfolioIntraday,
    holding_intraday,
    portfolio_intraday,
)

router = APIRouter()

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


async def yahoo_http() -> AsyncIterator[httpx.AsyncClient]:
    """The client an on-demand intraday fetch uses (short timeout: a page is waiting)."""
    async with httpx.AsyncClient(
        timeout=8.0, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        yield http


YahooHttp = Annotated[httpx.AsyncClient, Depends(yahoo_http)]


@router.get("/portfolio/holdings/prices", tags=["portfolio"])
async def get_holdings_prices(
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=7, le=400)] = 30,
) -> HoldingsPrices:
    """Every current holding's daily closes over the last `days` (Stooq, else Yahoo, as
    for the holding page) with the latest close-to-close change, and the latest stored
    intraday quote when intraday prices are on. A holding with no stored prices is listed
    with no bars and null figures."""
    return await holdings_prices(pool, days)


@router.get("/portfolio/correlation", tags=["portfolio"])
async def get_portfolio_correlation(
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=30, le=3700, description="Calendar days of history.")] = 365,
    min_shared: Annotated[
        int, Query(ge=2, le=1000, description="Fewest shared daily returns for a pair.")
    ] = 20,
) -> PortfolioCorrelation:
    """The correlation matrix of the current holdings, computed server-side from stored
    adjusted closes (daily simple returns on the days both traded). A pair with fewer than
    `min_shared` shared returns is null, never 0; `counts` gives every pair's sample size and
    `notes` the reason for a null. Ordering by cluster is left to the client."""
    return await portfolio_correlation(pool, days, min_shared)


@router.get("/market/strip", tags=["market"])
async def get_market_strip(pool: Pool, _owner: Owner) -> MarketStrip:
    """The reference rate of each held currency against the base currency and the
    configured index quotes, each with its previous value, change, source, kind (intraday
    or close) and as-of time. Anything without stored data is null, never estimated."""
    return await market_strip(pool)


@router.get("/holdings/{ticker}/intraday", tags=["holdings"])
async def get_holding_intraday(
    ticker: TickerPath, pool: Pool, _owner: Owner, http: YahooHttp
) -> HoldingIntraday:
    """The holding's latest session in 5-minute bars with its last price, trade time
    (exchange time), previous close and delay (Yahoo Finance, unofficial). Fetched from
    Yahoo first when the stored quote is older than the configured cache during trading
    hours. When Yahoo fails or intraday prices are off, `note` says so and `daily` holds the
    latest daily close to show instead."""
    return await holding_intraday(pool, ticker, http=http)


@router.get("/portfolio/intraday", tags=["portfolio"])
async def get_portfolio_intraday(
    pool: Pool,
    _owner: Owner,
    until: Annotated[
        dt.datetime | None,
        Query(description="Latest bar time considered (default now); the path covers its UTC day."),
    ] = None,
) -> PortfolioIntraday:
    """The portfolio's value path over the latest day with intraday bars up to `until`: the
    latest snapshot's quantities times each 5-minute close, in the base currency at the
    stored reference rates, over the holdings that have bars (`coverage`)."""
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=dt.UTC)
    return await portfolio_intraday(pool, until)
