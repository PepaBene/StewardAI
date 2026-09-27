"""Authenticated API routes for recent quotes: every holding's recent closes in one call,
and the market strip (held currencies' reference rates and the configured indices)."""

from typing import Annotated

from fastapi import APIRouter, Query

from api.auth import Owner
from api.db import Pool
from steward.portfolio.quotes import HoldingsPrices, MarketStrip, holdings_prices, market_strip

router = APIRouter()


@router.get("/portfolio/holdings/prices", tags=["portfolio"])
async def get_holdings_prices(
    pool: Pool,
    _owner: Owner,
    days: Annotated[int, Query(ge=7, le=400)] = 30,
) -> HoldingsPrices:
    """Every current holding's daily closes over the last `days` (from Stooq's stored bars,
    resolved as for the holding page) with the latest close-to-close change. A holding with
    no stored prices is listed with no bars and null figures."""
    return await holdings_prices(pool, days)


@router.get("/market/strip", tags=["market"])
async def get_market_strip(pool: Pool, _owner: Owner) -> MarketStrip:
    """The reference rate of each held currency against the base currency and the
    configured index quotes, each with its previous value and change. Anything without
    stored data is null, never estimated."""
    return await market_strip(pool)
