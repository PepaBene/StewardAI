"""Authenticated API routes for valuation: a holding's P/E, P/B and dividend yield, the
portfolio's against its benchmark, and a manual refresh of the underlying figures."""

from typing import Annotated

from fastapi import APIRouter, Path, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import _enqueue
from steward.config.common import Ticker
from steward.portfolio.read import JobSummary
from steward.valuation.read import (
    HoldingValuation,
    PortfolioValuation,
    holding_valuation,
    portfolio_valuation,
)

router = APIRouter()

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


@router.get("/holdings/{ticker}/valuation", tags=["holdings"])
async def get_holding_valuation(ticker: TickerPath, pool: Pool, _owner: Owner) -> HoldingValuation:
    """P/E, P/B and dividend yield from the latest stored close and the stored fundamentals
    (SEC XBRL, ESEF, or the holding profile's manual figures), each with its source, period,
    basis and staleness; an ETF's are its provider's fund figures. A ratio that cannot be
    computed is null with the reason (e.g. 'n/m: negative earnings')."""
    return await holding_valuation(pool, ticker)


@router.get("/portfolio/valuation", tags=["portfolio"])
async def get_portfolio_valuation(pool: Pool, _owner: Owner) -> PortfolioValuation:
    """The portfolio's P/E, P/B and dividend yield (value-weighted, iShares' capping and
    exclusion rules; see `method`) with the share of value covered, against the benchmark's
    fund-level figures (the Prices config's first benchmark) and every configured fund."""
    return await portfolio_valuation(pool)


class ValuationRefreshRequest(BaseModel):
    ticker: Ticker | None = Field(default=None, description="One holding, or all when null.")
    force: bool = Field(default=False, description="Re-read even when nothing new was filed.")


@router.post("/jobs/valuation-refresh", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_valuation_refresh(
    body: ValuationRefreshRequest, pool: Pool, owner: Owner
) -> JobSummary:
    """Refresh the fundamentals and fund figures behind the valuation now."""
    return await _enqueue(
        pool, "valuation_refresh", owner.user_id, body.model_dump(mode="json", exclude_none=True)
    )
