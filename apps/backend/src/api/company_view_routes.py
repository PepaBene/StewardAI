"""Owner-only aggregate reads for the company page: the header and sidebar summary, the Overview's
market context and every fair value on file. All read-only, from stored rows; a missing part is
null with a reason. Wiring: `app.include_router` of `router` in api/main.py."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from api.auth import Owner
from api.db import Pool
from steward.company import fair_values as fv
from steward.company import market_context as mc
from steward.company import summary as sm

router = APIRouter(prefix="/companies", tags=["companies"])
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


def _missing(ticker: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{ticker} is not in the covered universe")


@router.get("/{ticker}/summary", response_model=sm.CompanySummary)
async def get_company_summary(ticker: TickerPath, pool: Pool, owner: Owner) -> sm.CompanySummary:
    """Name, symbol, exchange, last price and day change, the default fair value, your position
    when held and the check axes: what the page header and sidebar need in one request."""
    out = await sm.company_summary(pool, owner.user_id, ticker)
    if out is None:
        raise _missing(ticker)
    return out


@router.get("/{ticker}/market-context", response_model=mc.CompanyMarketContext)
async def get_company_market_context(
    ticker: TickerPath, pool: Pool, owner: Owner
) -> mc.CompanyMarketContext:
    """7-day and 1-year shareholder returns against the industry and the market (the first
    Prices benchmark) with a pass sentence, the spread of weekly price moves, and up to four
    competitors with their check scores. Every figure is null when its history is missing, and
    `reasons` says which."""
    out = await mc.market_context(pool, owner.user_id, ticker)
    if out is None:
        raise _missing(ticker)
    return out


@router.get("/{ticker}/fair-values", response_model=fv.CompanyFairValues)
async def get_company_fair_values(
    ticker: TickerPath, pool: Pool, owner: Owner
) -> fv.CompanyFairValues:
    """Every fair value on file in the listing's currency beside the last price: your DCF and
    multiple-path scenarios, the default DCF, the thesis target, the peer-multiple value and the
    analyst mean, low and high, each with its date and a note on where it came from."""
    out = await fv.fair_values(pool, owner.user_id, ticker)
    if out is None:
        raise _missing(ticker)
    return out
